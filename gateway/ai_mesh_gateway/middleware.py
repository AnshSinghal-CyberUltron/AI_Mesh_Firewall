"""
AuthMiddleware for the ZeroShield AI Mesh Gateway.

Intercepts incoming HTTP requests, validates GatewayAPIKey credentials
against Redis, and injects auth context into ``request.state``.

Architecture: Pure ASGI middleware (not BaseHTTPMiddleware) to avoid
response-body buffering that breaks streaming.

Security policy: Fail-closed. If Redis is unreachable, all requests
are rejected with 503 Service Unavailable.
"""

import asyncio
import hashlib
import hmac
import os
import json
import logging
import time
from datetime import datetime, timezone
from typing import Optional

import redis.asyncio as aioredis
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger("gateway.middleware")

REDIS_KEY_PREFIX = "auth:apikey:"

EXCLUDED_PATHS: frozenset[str] = frozenset({
    "/health",
    "/metrics",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/v1/mcp/health",
    # SEC-04 FIX: Admin routes now require authentication
    # "/v1/admin/logs",           # REMOVED - requires auth
    # "/v1/admin/bedrock-logs",    # REMOVED - requires auth
    # "/v1/admin/bedrock-logs/stream",  # REMOVED - requires auth
    # "/v1/admin/bedrock-logs/file",    # REMOVED - requires auth
    "/.well-known/oauth-authorization-server",
    "/.well-known/oauth-protected-resource",
    "/gateway/oauth/callback",
})

# Paths where auth is attempted but not required — auth_context is set if
# a valid key is present, otherwise the request proceeds unauthenticated.
SOFT_AUTH_PATHS: frozenset[str] = frozenset({
    "/v1/models",
})

EXCLUDED_PATH_PREFIXES: tuple[str, ...] = (
    "/oauth/",
    "/.well-known/oauth-protected-resource/",
    "/gateway/oauth/",
)

class AuthContext:
    """Structured auth context extracted from API key, injected into request.state.
    Uses __slots__ for memory efficiency on the hot path.
    """

    __slots__ = (
        "key_id",
        "key_hash",
        "prefix",
        "user_id",
        "project_id",
        "organization_id",
        "org_slug",
        "permissions",
        "allowed_models",
        "rate_limit_tpm",
        "risk_score",
        "max_context_tokens",
        "mcp_allowed_tools",
        "mcp_max_tool_calls",
        "roles",
        "is_active",
        "expires_at",
    )

    def __init__(self, key_hash: str, payload: dict) -> None:
        self.key_hash = key_hash
        self.key_id: str = payload.get("key_id", "")
        self.prefix: str = payload.get("prefix", key_hash[:8])
        self.user_id: int = payload.get("user_id", 0)
        self.project_id: str = payload.get("project_id", "")
        self.organization_id = payload.get("organization_id")
        self.org_slug: str = payload.get("org_slug", "")
        self.permissions: dict = payload.get("permissions", {})
        self.allowed_models: list = payload.get("allowed_models", [])
        self.rate_limit_tpm: int = payload.get("rate_limit_tpm", 100_000)
        self.risk_score: float = payload.get("risk_score", 0.0)
        self.max_context_tokens: int = payload.get("max_context_tokens", 0)
        # E12 least-privilege MCP controls (control GatewayAPIKey.build_redis_payload):
        #   mcp_allowed_tools — tool allowlist; EMPTY list = all tools allowed.
        #   mcp_max_tool_calls — per-request tool-call cap; 0 = unlimited.
        # These sync to Redis but were never extracted, so they were unenforced.
        self.mcp_allowed_tools: list = payload.get("mcp_allowed_tools", []) or []
        self.mcp_max_tool_calls: int = payload.get("mcp_max_tool_calls", 0) or 0
        # G8: role names (RBAC labels) used by mcp_connector policy filtering.
        # Populated by GatewayAPIKey.build_redis_payload from owner.profile.roles.
        self.roles: list = payload.get("roles", []) or []
        self.is_active: bool = payload.get("is_active", True)
        self.expires_at: str | None = payload.get("expires_at")


async def validate_api_key(
    raw_key: str,
    redis_client: aioredis.Redis,
) -> tuple[Optional[AuthContext], Optional[dict]]:
    """Validate a raw API key against Redis and return an AuthContext.

    Returns:
        (AuthContext, None) on success.
        (None, error_dict) on failure, where error_dict has 'status_code',
        'error', and 'message' keys.

    This function is shared by AuthMiddleware (HTTP) and the WebSocket
    agent proxy to avoid duplicating validation logic.
    """
    if not raw_key or len(raw_key) < 16:
        return None, {
            "status_code": 401,
            "error": "unauthorized",
            "message": "Invalid API key format.",
        }

    key_hash = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    redis_key = f"{REDIS_KEY_PREFIX}{key_hash}"

    try:
        raw_payload = await redis_client.get(redis_key)
    except (aioredis.RedisError, OSError, ConnectionError) as exc:
        logger.error("Redis unavailable during auth lookup: %s", exc)
        return None, {
            "status_code": 503,
            "error": "service_unavailable",
            "message": "Authentication service temporarily unavailable. Please retry.",
        }

    if not raw_payload:
        logger.warning("API key not found in Redis (prefix: %s...)", raw_key[:8])
        return None, {
            "status_code": 401,
            "error": "unauthorized",
            "message": "Invalid API key.",
        }

    try:
        payload: dict = json.loads(raw_payload)
    except (json.JSONDecodeError, TypeError):
        logger.error("Corrupt Redis payload for key hash %s", key_hash[:16])
        return None, {
            "status_code": 500,
            "error": "internal_error",
            "message": "Authentication data corrupted.",
        }

    required_fields = ("key_id", "user_id", "project_id")
    missing = [f for f in required_fields if f not in payload]
    if missing:
        logger.error(
            "Corrupt Redis payload for key hash %s: missing fields %s",
            key_hash[:16],
            missing,
        )
        return None, {
            "status_code": 500,
            "error": "internal_error",
            "message": "Authentication data corrupted. Re-sync API keys.",
        }

    if not payload.get("is_active", True):
        logger.info("Rejected disabled API key (prefix: %s)", raw_key[:8])
        return None, {
            "status_code": 403,
            "error": "forbidden",
            "message": "API key is disabled.",
        }

    expires_at_str = payload.get("expires_at")
    if expires_at_str:
        try:
            expires_at = datetime.fromisoformat(expires_at_str)
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at < datetime.now(timezone.utc):
                logger.info("Rejected expired API key (prefix: %s)", raw_key[:8])
                return None, {
                    "status_code": 403,
                    "error": "forbidden",
                    "message": "API key has expired.",
                }
        except (ValueError, TypeError):
            logger.warning("Invalid expires_at format for key hash %s", key_hash[:16])
            return None, {
                "status_code": 403,
                "error": "forbidden",
                "message": "API key expiry data is invalid.",
            }

    auth_context = AuthContext(key_hash=key_hash, payload=payload)
    return auth_context, None

class AuthMiddleware:
    """
    Pure ASGI middleware for GatewayAPIKey authentication.

    Flow:
    1. Extract Authorization: Bearer <key> header
    2. SHA-256 hash the key
    3. Look up auth:apikey:{hash} in Redis (async)
    4. Validate is_active and expires_at
    5. Inject AuthContext into scope["state"]["auth_context"]
    6. Fire-and-forget last_used_at update

    HTTP status codes:
    - 401 + WWW-Authenticate: Bearer -- missing/invalid/unknown key
    - 403 -- key disabled or expired
    - 503 + Retry-After -- Redis unavailable (fail-closed)
    - 500 -- corrupt Redis payload
    """
    def __init__(self, app: ASGIApp, redis_url: str) -> None:
        self.app = app
        self.redis_url = redis_url
        self._pool: aioredis.ConnectionPool | None = None
        # Debounce last_used_at writes: under load every request was issuing a
        # Redis GET+SET to stamp last_used_at, doubling auth-path Redis traffic.
        # We now stamp at most once per key per debounce window (in-process).
        self._last_used_stamp: dict[str, float] = {}
        self._last_used_debounce_seconds = float(
            os.environ.get("GATEWAY_LAST_USED_DEBOUNCE_SECONDS", "60")
        )

    def _get_pool(self) -> aioredis.ConnectionPool:
        """Lazy-init async connection pool."""
        if self._pool is None:
            max_connections = int(os.environ.get("GATEWAY_REDIS_MAX_CONNECTIONS", "300"))
            self._pool = aioredis.ConnectionPool.from_url(
                self.redis_url,
                decode_responses=True,
                max_connections=max_connections,
                socket_timeout=2.0,
                socket_connect_timeout=1.0,
                retry_on_timeout=True,
            )
        return self._pool

    async def _get_redis(self) -> aioredis.Redis:
        """Get a Redis client from the async pool."""
        return aioredis.Redis(connection_pool=self._get_pool())

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # CORS preflight — CORSMiddleware handles OPTIONS; auth must not 401 without ACAO.
        if scope.get("method") == "OPTIONS":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path in EXCLUDED_PATHS:
            await self.app(scope, receive, send)
            return

        if any(path.startswith(prefix) for prefix in EXCLUDED_PATH_PREFIXES):
            await self.app(scope, receive, send)
            return

        # Phase 1 Fx-2a (hardened): server-to-server bypass for the control
        # plane's admin proxy. Constant-time compare against the shared
        # secret in GATEWAY_INTERNAL_API_KEY. Scope is intentionally
        # **restricted to /v1/admin/*** so a leaked secret cannot be used
        # to impersonate tenants on the data path -- only operational
        # admin reads/mutations are reachable. Downstream RBAC is still
        # enforced by ``_require_admin_role`` (which also recognises this
        # header) so the bypass cannot widen privileges beyond admin.
        #
        # MCP-SYNC FIX: additionally allow read-only tool-discovery and
        # internal tool execution when the control plane presents the shared
        # secret. Both handlers re-validate the secret (defence in depth).
        _INTERNAL_MCP_PATHS = (
            "/v1/mcp/internal/discover-tools",
            "/v1/mcp/internal/tools-call",
        )
        if path.startswith("/v1/admin/") or path in _INTERNAL_MCP_PATHS:
            internal_secret = os.environ.get("GATEWAY_INTERNAL_API_KEY", "").strip()
            if internal_secret:
                header_secret: str | None = None
                for hn, hv in scope.get("headers", []):
                    if hn == b"x-gateway-internal-key":
                        header_secret = hv.decode("latin-1").strip()
                        break
                if header_secret and hmac.compare_digest(header_secret, internal_secret):
                    await self.app(scope, receive, send)
                    return

        # Allow upstream OAuth proxy routes without auth (user auth happens
        # at the external OAuth provider; callback is a redirect).
        if "/oauth/start" in path or "/oauth/status" in path:
            await self.app(scope, receive, send)
            return

        is_soft_auth = path in SOFT_AUTH_PATHS

        auth_value: str | None = None
        for header_name, header_value in scope.get("headers", []):
            if header_name == b"authorization":
                auth_value = header_value.decode("latin-1")
                break

        if not auth_value or not auth_value.startswith("Bearer "):
            if is_soft_auth:
                await self.app(scope, receive, send)
                return
            # Build resource_metadata URL per RFC 9728 Section 5.1
            host_header = None
            for hn, hv in scope.get("headers", []):
                if hn == b"host":
                    host_header = hv.decode("latin-1")
                    break
            scheme = "http"  # Inside Docker, always http
            for hn, hv in scope.get("headers", []):
                if hn == b"x-forwarded-proto":
                    scheme = hv.decode("latin-1")
                    break
            resource_base = f"{scheme}://{host_header}" if host_header else os.environ.get("GATEWAY_PUBLIC_URL", "http://127.0.0.1:8300").rstrip("/")
            resource_metadata_url = f"{resource_base}/.well-known/oauth-protected-resource"
            logger.debug(
                "401 Unauthorized for path=%s (no valid Bearer token). resource_metadata=%s",
                path, resource_metadata_url,
            )
            response = JSONResponse(
                status_code=401,
                content={
                    "error": "unauthorized",
                    "message": "Missing or malformed Authorization header. Expected: Bearer <api_key>",
                },
                headers={
                    "WWW-Authenticate": f'Bearer resource_metadata="{resource_metadata_url}"',
                },
            )
            await response(scope, receive, send)
            return

        raw_key = auth_value[7:].strip()

        try:
            client = await self._get_redis()
        except (aioredis.RedisError, OSError, ConnectionError) as exc:
            logger.error("Redis unavailable during auth lookup: %s", exc)
            if is_soft_auth:
                await self.app(scope, receive, send)
                return
            response = JSONResponse(
                status_code=503,
                content={
                    "error": "service_unavailable",
                    "message": "Authentication service temporarily unavailable. Please retry.",
                },
                headers={"Retry-After": "5"},
            )
            await response(scope, receive, send)
            return

        auth_context, error = await validate_api_key(raw_key, client)

        if error is not None:
            if is_soft_auth:
                await self.app(scope, receive, send)
                return
            headers = {}
            if error["status_code"] == 401:
                # Build resource_metadata URL per RFC 9728
                host_header = None
                for hn, hv in scope.get("headers", []):
                    if hn == b"host":
                        host_header = hv.decode("latin-1")
                        break
                scheme = "http"
                for hn, hv in scope.get("headers", []):
                    if hn == b"x-forwarded-proto":
                        scheme = hv.decode("latin-1")
                        break
                resource_base = f"{scheme}://{host_header}" if host_header else os.environ.get("GATEWAY_PUBLIC_URL", "http://127.0.0.1:8300").rstrip("/")
                resource_metadata_url = f"{resource_base}/.well-known/oauth-protected-resource"
                headers["WWW-Authenticate"] = f'Bearer resource_metadata="{resource_metadata_url}"'
                logger.warning(
                    "401 Unauthorized for path=%s — Bearer token present but invalid (prefix=%s)",
                    path, raw_key[:8] if raw_key else "?",
                )
            elif error["status_code"] == 503:
                headers["Retry-After"] = "5"
            response = JSONResponse(
                status_code=error["status_code"],
                content={
                    "error": error["error"],
                    "message": error["message"],
                },
                headers=headers if headers else None,
            )
            await response(scope, receive, send)
            return

        scope.setdefault("state", {})["auth_context"] = auth_context

        try:
            now = time.monotonic()
            last = self._last_used_stamp.get(auth_context.key_hash, 0.0)
            if now - last >= self._last_used_debounce_seconds:
                self._last_used_stamp[auth_context.key_hash] = now
                redis_key = f"{REDIS_KEY_PREFIX}{auth_context.key_hash}"
                asyncio.create_task(
                    self._update_last_used(client, redis_key)
                )
        except Exception:
            pass

        await self.app(scope, receive, send)

    async def _update_last_used(
            self,
            client: aioredis.Redis,
            redis_key: str,
    ) -> None:
        """Fire-and-forget: stamp last_used_at ATOMICALLY into the existing payload.

        F3: the previous blind GET -> modify -> SET(keepttl) raced control-plane
        key revocation. A SET landing AFTER a DELETE re-created the auth key
        (keepttl on an absent key => permanent live credential the resync
        reconciler never reaps, since it has no DB row); a SET after an
        is_active=False update reverted the revocation from a stale payload. The
        Lua script reads the CURRENT value (returns 0 if the key is gone, so it
        cannot resurrect a deleted key) and rewrites only last_used_at on the
        live payload (so it cannot overwrite a freshly-revoked is_active).
        """
        _LUA = (
            "local v = redis.call('GET', KEYS[1]) "
            "if not v then return 0 end "
            "local ok, t = pcall(cjson.decode, v) "
            "if not ok then return 0 end "
            "t['last_used_at'] = ARGV[1] "
            "redis.call('SET', KEYS[1], cjson.encode(t), 'KEEPTTL') "
            "return 1"
        )
        try:
            await client.eval(_LUA, 1, redis_key, datetime.now(timezone.utc).isoformat())
        except Exception as exc:
            logger.debug("Failed to update last_used_at: %s", exc)
