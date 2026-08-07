"""REST API views for MCP Connector.

Provides endpoints for managing MCP servers (registered locally), tool
discovery, tool controls, MCP-Firewall pre/post flight enforcement, and
structured observability. Guardrails are unified into the policy engine —
the legacy Enkrypt Secure-MCP-Gateway profile layer has been removed.
"""

import json
import logging
import os
import re
import secrets
import time
import uuid as uuid_mod
from datetime import timedelta
from functools import lru_cache

import jsonschema
import requests
from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError
from django.db.models import Count, Q
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny, BasePermission, IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from . import mcp_firewall_client
from .models import MCPEvent, MCPServerRegistration, MCPScanControl, MCPToolRegistration
from .scan_controls import resolve_effective_controls, serialize_control
from .serializers import (
    MCPEventSerializer,
    MCPServerCreateSerializer,
    MCPServerRegistrationSerializer,
    MCPScanControlSerializer,
    MCPToolRegistrationSerializer,
)

logger = logging.getLogger(__name__)


def _mcp_compliance_tags(*hint_sources) -> list[str]:
    """Best-effort: union compliance tags from matched preset/entity keys.

    Mirrors the gateway scan path's compliance tagging on the control-plane
    MCP tool-call enforcement events. Each redaction hint carries a ``preset``
    (and/or ``key``) identifier; we map those through the shared
    ``tags_for_preset_or_entity`` table and union the result. Never raises —
    a failure to tag must never break enforcement, so we default to [].
    """
    try:
        from ai_mesh_shared.mcp_compliance_tags import tags_for_preset_or_entity

        keys: set[str] = set()
        for hints in hint_sources:
            for hint in hints or []:
                if not isinstance(hint, dict):
                    continue
                for field_name in ("preset", "key"):
                    val = hint.get(field_name)
                    if isinstance(val, str) and val.strip():
                        keys.add(val.strip())
        tags: set[str] = set()
        for key in keys:
            tags.update(tags_for_preset_or_entity(key))
        return sorted(tags)
    except Exception as exc:  # pragma: no cover - defensive, never break enforcement
        logger.warning("mcp_connector.tool.compliance_tag_compute_failed err=%s", exc)
        return []


# HTTP read timeout (seconds) for the control -> gateway discover-tools call.
# This MUST be >= the gateway's stdio init timeout (MCP_STDIO_INIT_TIMEOUT,
# default 120s) plus margin, otherwise a legitimate first-connect that fetches
# a server package on-demand (npx/uvx cold download) is aborted control-side
# before the gateway finishes initializing — surfacing as a misleading
# "Read timed out" even though the server would have come up. Configurable so
# operators can match it to their gateway init budget.
_DISCOVER_HTTP_TIMEOUT = float(os.environ.get("MCP_DISCOVER_HTTP_TIMEOUT", "150"))


def _gateway_internal_secret() -> str:
    """Return the configured internal gateway-to-backend shared secret."""
    return (
        (getattr(settings, "GATEWAY_INTERNAL_API_KEY", "") or "").strip()
        or (getattr(settings, "AGENT_API_KEY", "") or "").strip()
    )


def _is_gateway_internal_request(request) -> bool:
    """Validate trusted gateway->backend requests using shared secret + marker header."""
    if (request.headers.get("X-Gateway-Auth", "") or "").lower() != "true":
        return False

    configured_secret = _gateway_internal_secret()
    provided_secret = (request.headers.get("X-Gateway-Internal-Key", "") or "").strip()
    if not configured_secret or not provided_secret:
        return False
    return secrets.compare_digest(provided_secret, configured_secret)


def _gateway_request_org(request):
    """Resolve org from trusted gateway headers when request is gateway-authenticated."""
    if not _is_gateway_internal_request(request):
        return None
    org_slug = (request.headers.get("X-Org-Slug", "") or "").strip().lower()
    if not org_slug:
        return None

    from auth.models import Organization

    return Organization.objects.filter(slug=org_slug, is_active=True).first()


class IsAuthenticatedOrGatewayInternal(BasePermission):
    """Allow either regular JWT auth or trusted internal gateway authentication."""

    def has_permission(self, request, view):
        user = getattr(request, "user", None)
        if user and user.is_authenticated:
            return True
        return _is_gateway_internal_request(request)


class IsGatewayInternalOnly(BasePermission):
    """Allow ONLY trusted gateway->backend requests (shared secret).

    Used for internal endpoints (e.g. audit record-event) that must never be
    callable with an end-user JWT, to prevent audit forgery / event injection.
    """

    def has_permission(self, request, view):
        return _is_gateway_internal_request(request)


# Free-text fields written to MCPEvent are rendered in the operator dashboard;
# sanitise them to prevent stored XSS and unbounded growth.
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _sanitize_event_text(value, max_len: int = 255) -> str:
    """Strip control chars + angle brackets (kills stored <script>) and cap length."""
    text = "" if value is None else str(value)
    text = _CONTROL_CHARS_RE.sub("", text).replace("<", "").replace(">", "")
    return text[:max_len]


def _sanitize_event_structure(obj, _depth: int = 0, max_str_len: int = 2000):
    """Recursively sanitise caller-supplied JSON (metadata / compliance_tags /
    scan_findings) before it is persisted to MCPEvent and mirrored to
    EnforcementEvent. Scalar-field sanitisation alone was bypassable by
    smuggling raw <script>/control-chars through the metadata dict.

    Strips control chars + angle brackets from every string value AND key,
    bounds string length, and bounds nesting depth (defends against deep-nest
    DoS in stored audit payloads).
    """
    if _depth > 24:
        return "[truncated:max-depth]"
    if isinstance(obj, str):
        return _sanitize_event_text(obj, max_str_len)
    if isinstance(obj, dict):
        return {
            _sanitize_event_text(str(k), 256): _sanitize_event_structure(v, _depth + 1, max_str_len)
            for k, v in list(obj.items())[:200]
        }
    if isinstance(obj, (list, tuple)):
        return [_sanitize_event_structure(v, _depth + 1, max_str_len) for v in list(obj)[:500]]
    # int/float/bool/None pass through unchanged
    return obj


# Heuristic classification of mutating / destructive MCP tools. This is FLAG-ONLY:
# the tool-call path attaches the label to the audit event and logs it, but does
# NOT block (operators decide whether to disable a flagged tool).
_DESTRUCTIVE_TOOL_RE = re.compile(
    r"(?:^|[_\-/.:])(?:delete|remove|destroy|drop|purge|revoke|wipe|truncate|"
    r"merge|force[_-]?push|reset|terminate|kill|uninstall|deprovision)(?:$|[_\-/.:s])",
    re.IGNORECASE,
)
_WRITE_TOOL_RE = re.compile(
    r"(?:^|[_\-/.:])(?:create|update|write|save|set|modify|edit|add|insert|post|"
    r"put|patch|push|comment|send|upload|execute|run|approve|close|publish|assign|"
    r"move|rename|disable|enable|grant|provision|invite|trigger|deploy|cancel)(?:$|[_\-/.:s])",
    re.IGNORECASE,
)


def _classify_tool_write_risk(tool_name: str, description: str = "", annotations: dict | None = None) -> str:
    """Classify a tool as 'destructive' | 'write' | 'read'.

    Prefers explicit MCP tool annotations (destructiveHint / readOnlyHint) when
    present, else uses a name keyword heuristic.
    """
    ann = annotations or {}
    if isinstance(ann, dict):
        if ann.get("destructiveHint") is True:
            return "destructive"
        if ann.get("readOnlyHint") is True:
            return "read"
        if ann.get("writeHint") is True or ann.get("idempotentHint") is False:
            return "write"
    name = tool_name or ""
    if _DESTRUCTIVE_TOOL_RE.search(name):
        return "destructive"
    if _WRITE_TOOL_RE.search(name):
        return "write"
    return "read"


def _request_actor(request) -> tuple[int | None, str]:
    """Resolve actor identity for observability (JWT user or trusted gateway identity)."""
    user = getattr(request, "user", None)
    if user and user.is_authenticated:
        return user.id, user.get_username()

    if _is_gateway_internal_request(request):
        gateway_user_id = (request.headers.get("X-Gateway-User-Id", "") or "").strip()
        actor_user_id = int(gateway_user_id) if gateway_user_id.isdigit() else None
        key_prefix = (request.headers.get("X-Gateway-Key-Prefix", "") or "").strip()
        actor_username = f"gateway:{key_prefix}" if key_prefix else ""
        return actor_user_id, actor_username

    return None, ""


def _upstream_error_detail(exc: Exception) -> str:
    """Extract actionable details from upstream HTTP failures."""
    response = getattr(exc, "response", None)
    if response is not None:
        text = (response.text or "").strip()
        if text:
            return f"Upstream HTTP {response.status_code}: {text[:1200]}"
        return f"Upstream HTTP {response.status_code}"
    return str(exc)


# SEC-03 FIX: JSON Schema validation for MCP tool arguments
@lru_cache(maxsize=256)
def _compile_schema(schema_json: str):
    """Compile and cache JSON schema validators for performance."""
    schema = json.loads(schema_json)
    return jsonschema.Draft7Validator(schema)


def _validate_tool_arguments(tool_reg, arguments: dict) -> list[dict] | None:
    """Validate tool arguments against input_schema. Returns list of errors or None."""
    if not tool_reg or not tool_reg.input_schema:
        return None  # No schema = no validation (allow)
    
    try:
        # Serialize schema for cache lookup
        schema_key = json.dumps(tool_reg.input_schema, sort_keys=True)
        validator = _compile_schema(schema_key)
        errors = list(validator.iter_errors(arguments))
        if errors:
            return [
                {
                    "field": "/".join(str(p) for p in e.path) if e.path else "(root)",
                    "message": e.message,
                    "validator": e.validator,
                }
                for e in errors
            ]
    except Exception as exc:
        logger.warning("Schema validation failed: %s", exc)
        # Schema parsing error - don't block, just log
        return None
    
    return None  # No errors


def _request_org(request):
    """Resolve request organization for STRICT tenant scoping.

    Tenant is derived ONLY from the authenticated principal's own profile
    (for JWT users) or a trusted gateway-internal header (for the data plane).
    A client-supplied ``organization_id`` is intentionally NOT honored here —
    even for superusers — so no MCP request (server/tool/event/scan-control)
    can read or write another organization's data.
    """
    user = getattr(request, "user", None)
    if user is not None and getattr(user, "is_authenticated", False):
        return getattr(getattr(user, "profile", None), "organization", None)
    return _gateway_request_org(request)


def _org_scoped_servers_queryset(request):
    org = _request_org(request)
    if org is None:
        return MCPServerRegistration.objects.none(), None
    return MCPServerRegistration.objects.filter(organization=org), org


def _store_oauth_tokens(server, tok: dict) -> None:
    """Persist an OAuth token response onto the server (encrypted fields).

    The access token is stored in ``auth_token`` (the existing encrypted
    bearer field) so the gateway forwards it as a normal ``Authorization:
    Bearer`` with no OAuth awareness. Refresh token + expiry are stored for
    silent renewal.
    """
    access = (tok.get("access_token") or "").strip()
    update_fields = ["updated_at"]
    if access:
        server.auth_token = access
        update_fields.append("auth_token")
    new_refresh = tok.get("refresh_token")
    if new_refresh:
        server.oauth_refresh_token = new_refresh
        update_fields.append("oauth_refresh_token")
    expires_in = tok.get("expires_in")
    server.oauth_token_expires_at = None
    if expires_in:
        try:
            server.oauth_token_expires_at = timezone.now() + timedelta(seconds=int(expires_in))
        except (TypeError, ValueError):
            server.oauth_token_expires_at = None
    update_fields.append("oauth_token_expires_at")
    if tok.get("scope"):
        server.oauth_scope = tok["scope"]
        update_fields.append("oauth_scope")
    server.auth_type = "oauth"
    update_fields.append("auth_type")
    server.save(update_fields=update_fields)
    _mirror_oauth_token_to_gateway(server, tok)


def _mirror_oauth_token_to_gateway(server, tok: dict) -> None:
    """Best-effort mirror of control-plane OAuth tokens into gateway Redis.

    HTTP OAuth servers route through the sandbox; ``get_stored_token(org, url)``
    must find the token after control ``_store_oauth_tokens`` or refresh.
    Failures are logged and swallowed — the control DB row remains authoritative.
    """
    org = getattr(server, "organization", None)
    org_slug = getattr(org, "slug", "") if org else ""
    server_url = (getattr(server, "url", "") or "").strip()
    access = (tok.get("access_token") or getattr(server, "auth_token", "") or "").strip()
    if not org_slug or not server_url or not access:
        return
    gateway_url = (
        (getattr(settings, "GATEWAY_URL", "") or "").strip().rstrip("/")
        or os.environ.get("GATEWAY_URL", "").strip().rstrip("/")
        or "http://gateway:8300"
    )
    internal_key = (
        (getattr(settings, "GATEWAY_INTERNAL_API_KEY", "") or "").strip()
        or os.environ.get("GATEWAY_INTERNAL_API_KEY", "").strip()
    )
    if not internal_key:
        logger.warning(
            "OAuth token mirror skipped for %s: GATEWAY_INTERNAL_API_KEY not configured",
            getattr(server, "server_slug", "?"),
        )
        return
    payload = {
        "org_slug": org_slug,
        "server_url": server_url,
        "access_token": access,
        "refresh_token": tok.get("refresh_token") or getattr(server, "oauth_refresh_token", "") or "",
        "expires_in": tok.get("expires_in"),
        "token_type": tok.get("token_type") or "bearer",
        "token_endpoint": getattr(server, "oauth_token_endpoint", "") or "",
        "client_id": getattr(server, "oauth_client_id", "") or "",
        "client_secret": getattr(server, "oauth_client_secret", "") or "",
        "resource": getattr(server, "oauth_resource", "") or "",
        "scope": tok.get("scope") or getattr(server, "oauth_scope", "") or "",
    }
    try:
        resp = requests.post(
            f"{gateway_url}/v1/mcp/internal/oauth-token-mirror",
            json=payload,
            headers={
                "Content-Type": "application/json",
                "X-Gateway-Internal-Key": internal_key,
            },
            timeout=5,
        )
        if resp.status_code >= 400:
            logger.warning(
                "OAuth token mirror HTTP %s for %s/%s: %s",
                resp.status_code,
                org_slug,
                getattr(server, "server_slug", "?"),
                resp.text[:300],
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "OAuth token mirror failed for %s/%s: %s",
            org_slug,
            getattr(server, "server_slug", "?"),
            exc,
        )


def _oauth_token_present_and_unexpired(server) -> bool:
    """True when the server currently holds a usable OAuth access token that has
    not expired — so a fresh re-authorization would just re-mint the same kind of
    token the upstream may already be rejecting."""
    if getattr(server, "auth_type", "") != "oauth" or not getattr(server, "auth_token", ""):
        return False
    exp = getattr(server, "oauth_token_expires_at", None)
    return exp is None or exp > timezone.now()


def _is_auth_rejection_message(msg: str) -> bool:
    """True when a sanitized sync error indicates the UPSTREAM rejected auth
    (401/403), as opposed to a missing / expired local token."""
    low = (msg or "").lower()
    return any(
        k in low
        for k in (
            "rejected authentication", "rejected the oauth", "re-authorize",
            "re-authenticate", "needs re-authentication", " 401", " 403",
            "unauthorized", "forbidden",
        )
    )


def _oauth_pending_initial_authorization(server) -> bool:
    """True when OAuth is configured but the operator has never completed authorize."""
    return (
        getattr(server, "auth_type", "") == "oauth"
        and not getattr(server, "auth_token", None)
    )


def _ensure_oauth_token_fresh(server) -> bool:
    """Refresh the OAuth access token in place when missing or near expiry.

    Returns ``True`` when the server has a *usable* (fresh or just-refreshed)
    token that is safe to forward upstream, ``False`` when re-authentication is
    required. No-op-True for non-OAuth servers. On refresh failure we do NOT
    raise (a hard failure here would 500 the hot path for every tool call);
    instead we surface the cause per-org via ``last_sync_error`` +
    ``needs_reauth`` so the operator sees an actionable "re-authenticate
    <server>" signal while other orgs keep working. Critically we now also
    return ``False`` so callers skip forwarding the expired token (which the
    upstream rejects with an opaque ``invalid_token``).
    """
    if getattr(server, "auth_type", "") != "oauth":
        return True
    if _oauth_pending_initial_authorization(server):
        # Never completed OAuth — pending authorization, not a re-auth failure.
        return False
    expires_at = getattr(server, "oauth_token_expires_at", None)
    has_token = bool(server.auth_token)
    near_expiry = bool(expires_at) and expires_at <= timezone.now() + timedelta(seconds=60)
    if has_token and not near_expiry:
        return True
    refresh = getattr(server, "oauth_refresh_token", "") or ""
    token_endpoint = getattr(server, "oauth_token_endpoint", "") or ""
    if not refresh or not token_endpoint:
        # Configured for OAuth but no usable refresh material — needs re-auth.
        _mark_needs_reauth(
            server,
            "OAuth credentials missing or incomplete — re-authenticate this server.",
        )
        return False
    try:
        from . import oauth as oauth_mod

        tok = oauth_mod.refresh_access_token(
            token_endpoint,
            refresh,
            server.oauth_client_id,
            server.oauth_client_secret,
            server.oauth_resource,
            server.oauth_scope,
        )
        _store_oauth_tokens(server, tok)
        _clear_needs_reauth(server)
        logger.info("Refreshed OAuth token for %s", server.server_slug)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.error("OAuth token refresh failed for %s: %s", server.server_slug, exc)
        # Do NOT interpolate the raw exception into the client-facing message
        # (it can leak token-endpoint URLs / provider error bodies). Route it
        # through the sanitizer for a clean auth message + correlation ref. (CP17)
        _mark_needs_reauth(
            server,
            _sanitize_sync_error(
                f"oauth token refresh failed: {exc}",
                org_slug=getattr(getattr(server, "organization", None), "slug", ""),
                server_slug=getattr(server, "server_slug", ""),
            ),
        )
        return False


def _mark_needs_reauth(server, message: str) -> None:
    """Persist an actionable per-org auth error without raising."""
    try:
        server.needs_reauth = True
        server.last_sync_error = message
        server.last_sync_attempt_at = timezone.now()
        server.save(update_fields=["needs_reauth", "last_sync_error", "last_sync_attempt_at"])
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to persist needs_reauth for %s: %s", server.server_slug, exc)


def _clear_needs_reauth(server) -> None:
    """Clear a previously-set auth error after a successful refresh."""
    if not getattr(server, "needs_reauth", False) and not getattr(server, "last_sync_error", ""):
        return
    try:
        server.needs_reauth = False
        server.last_sync_error = ""
        server.save(update_fields=["needs_reauth", "last_sync_error"])
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to clear needs_reauth for %s: %s", server.server_slug, exc)


# Stable, client-facing MCP sync error codes. These are part of the public
# contract (documented, referenced by support): the wording of a message may
# change, the CODE does not. Keep in sync with docs/mcp/HARDENING_CHANGELOG.md
# and the frontend inline-error renderer.
MCP_ERR_AUTH = "MCP_AUTH_FAILED"
MCP_ERR_EGRESS = "MCP_EGRESS_DENIED"
MCP_ERR_OOM = "MCP_OUT_OF_MEMORY"
MCP_ERR_STORAGE = "MCP_INSUFFICIENT_STORAGE"
MCP_ERR_CRASH = "MCP_SERVER_CRASHED"
MCP_ERR_IMAGE = "MCP_IMAGE_UNAVAILABLE"
MCP_ERR_TIMEOUT = "MCP_TIMEOUT"
MCP_ERR_START = "MCP_START_FAILED"
MCP_ERR_HOST_TOOL = "MCP_HOST_TOOL_FAILED"
MCP_ERR_PROTOCOL = "MCP_PROTOCOL_FAILED"
MCP_ERR_UNAVAILABLE = "MCP_UNAVAILABLE"


class SyncError(str):
    """A sanitized, client-facing sync-error string that also carries a stable
    error ``code`` and a correlation ``ref``.

    It subclasses ``str`` so every existing consumer keeps working unchanged —
    the ``last_sync_error`` CharField stores the human message, the
    ``needs_reauth`` ``.lower()`` heuristics scan the message, ``sync_error or ""``
    is truthy, and DRF/``json`` serialize it as the plain message. New consumers
    (the sync API response, the dev debug view) read ``.code`` / ``.ref``.
    """

    code: str
    ref: str

    def __new__(cls, message: str, code: str, ref: str):
        obj = super().__new__(cls, message)
        obj.code = code
        obj.ref = ref
        return obj


def _classify_sync_error(low: str) -> tuple[str, str]:
    """Map a lower-cased raw error to a (stable code, branded summary).

    Order matters: the specific internal-failure fingerprints (OOM, crash,
    image-missing) are checked BEFORE the generic "exited with code" branch,
    because those raw messages also contain "exited with code".
    """
    if any(k in low for k in ("re-authenticate", "re-authentication",
                              "unauthorized", "invalid token", "invalid_token",
                              "forbidden", " 401", " 403", "auth")):
        return MCP_ERR_AUTH, ("The MCP server rejected authentication. Check the "
                              "credentials or re-authorize the connection, then retry.")
    if "egress denied" in low or "allowlist" in low or "not permitted" in low:
        return MCP_ERR_EGRESS, ("The MCP server host is not permitted by your "
                                "organization's egress policy.")
    # Disk/storage: the server's install overflowed the sandbox tmpfs (ENOSPC).
    if any(k in low for k in ("no space left", "enospc", "exceeded the sandbox storage",
                              "storage limit")):
        return MCP_ERR_STORAGE, ("The MCP server is too large to install within the "
                                 "current sandbox storage limit. Increase the limit for "
                                 "heavy servers or use a lighter server, then retry.")
    # OOM: SIGKILL (exit -9 / signal 9) or container OOM-kill (exit 137 = 128+9).
    if any(k in low for k in ("code -9", "signal 9", "sigkill", "out of memory",
                              "oom", "code 137", "exit 137", "exitcode 137")):
        return MCP_ERR_OOM, ("The MCP server ran out of memory while starting. It "
                             "may be too resource-intensive for the current limits; "
                             "try a lighter configuration or contact support.")
    # Hard crash: SIGABRT (exit -6 / signal 6) or container abort (exit 134 = 128+6).
    if any(k in low for k in ("code -6", "signal 6", "sigabrt", "aborted",
                              "code 134", "exit 134", "core dumped")):
        return MCP_ERR_CRASH, ("The MCP server crashed while starting. Verify the "
                               "command and package are compatible, then retry.")
    # Container image not available (pull failure / missing image / manifest).
    if any(k in low for k in ("no such image", "image not found", "not found: image",
                              "manifest unknown", "pull access denied",
                              "imagepullbackoff", "no such file or directory: image",
                              "unable to find image")):
        return MCP_ERR_IMAGE, ("The MCP server's runtime image is not available. "
                               "Please retry shortly or contact support.")
    if any(k in low for k in ("timeout", "timed out", "did not respond",
                              "not ready", "provisioning", "starting up")):
        return MCP_ERR_TIMEOUT, ("The MCP server did not respond in time. Please "
                                 "retry in a moment.")
    if any(k in low for k in ("host tool install failed", "mcp_host_tools",
                              "host tool not in allowlist", "host tool manager")):
        return MCP_ERR_HOST_TOOL, ("A required CLI tool could not be installed in the "
                                   "sandbox. Verify the Host CLI tools declaration and "
                                   "retry, or contact support.")
    if "mcp_protocol_handshake_failed" in low or "host_cli_tools_installed" in low:
        return MCP_ERR_PROTOCOL, (
            "The command exited without speaking MCP JSON-RPC. Verify it launches an MCP "
            "server (not a one-off script). Host CLI tools were installed successfully."
        )
    if any(k in low for k in ("exited with code", "failed to start", "process exited",
                              "missing host dependency", "wrong package",
                              "stdout stream closed", "did not start")):
        return MCP_ERR_START, ("The MCP server could not be started — it stopped "
                               "immediately during startup. Verify the command and package "
                               "name; if the server shells out to a required CLI binary, "
                               "declare it in the server's Host CLI tools / MCP_HOST_TOOLS.")
    return MCP_ERR_UNAVAILABLE, ("The MCP server could not be reached or returned an "
                                 "error. Verify the configuration and retry.")


# Developer diagnostic channel (CP18). The raw cause behind a sanitized client
# error is written here keyed by the correlation ref, so a developer/staff user
# can retrieve it via MCPDiagnosticDetailView WITHOUT it ever reaching a client.
MCP_DIAG_CACHE_PREFIX = "mcp:diag:"
MCP_DIAG_TTL_SECONDS = 7 * 24 * 3600  # 7 days


def _diag_cache_key(ref: str) -> str:
    return f"{MCP_DIAG_CACHE_PREFIX}{ref}"


def _read_gateway_diagnostic(ref: str) -> dict | None:
    """CLEANUP-05: read a GATEWAY-originated diagnostic by correlation ref.

    The gateway's ``mcp_error_classifier.sanitize_mcp_error`` records the REAL cause
    (exit code, upstream body, internal host, raw exc) to Redis under the PLAIN key
    ``mcp:diag:<ref>`` as JSON — which is NOT the django_redis cache key
    (``<KEY_PREFIX>:<version>:mcp:diag:<ref>``, pickled). Read it directly off the
    shared Redis so this ONE staff-only endpoint surfaces BOTH control- and
    gateway-originated diagnostics. Best-effort: any failure → None (falls through
    to a 404), never an error.
    """
    try:
        import redis as _redis
        url = getattr(settings, "REDIS_URL", "redis://localhost:6379/0")
        client = _redis.Redis.from_url(url, decode_responses=True)
        raw = client.get(_diag_cache_key(ref))
        try:
            client.close()
        except Exception:  # noqa: BLE001
            pass
        return json.loads(raw) if raw else None
    except Exception as exc:  # noqa: BLE001
        logger.debug("gateway diagnostic read failed [ref=%s]: %s", ref, exc)
        return None


def _store_sync_diagnostic(ref, code, raw, *, org_slug="", server_slug="") -> None:
    """Persist the REAL cause behind a sanitized client error, keyed by ``ref``.

    Best-effort (a cache outage must never break the sync path): the client has
    already been given the sanitized message + code + ref; this only powers the
    staff-only debug view. Raw is truncated to a safe bound.
    """
    try:
        cache.set(
            _diag_cache_key(ref),
            {
                "ref": ref,
                "code": code,
                "kind": "mcp_sync_error",
                "org_slug": org_slug,
                "server_slug": server_slug,
                "raw_cause": str(raw)[:4000],
                "created_at": timezone.now().isoformat(),
            },
            timeout=MCP_DIAG_TTL_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to persist MCP diagnostic [ref=%s]: %s", ref, exc)


def _sanitize_sync_error(raw, *, org_slug: str = "", server_slug: str = "") -> "SyncError":
    """Map a raw internal MCP error to a clean, branded, NON-revealing client error.

    The raw detail (exit codes, upstream response bodies, sandbox-agent internals,
    host-dependency hints, server keys) is kept ONLY server-side under a short
    correlation ``ref`` — in the structured logs AND the staff-only diagnostic
    channel (:func:`_store_sync_diagnostic`); the client sees an actionable,
    brand-safe summary, a stable error ``code``, and the ``ref``. Returns a
    :class:`SyncError` (a ``str`` carrying ``.code`` + ``.ref``). (CP16 sanitize +
    CP17 code/mapping + CP18 dev diagnostic channel keyed by ref.)
    """
    ref = uuid_mod.uuid4().hex[:12]
    code, summary = _classify_sync_error(str(raw).lower())
    logger.warning(
        "MCP sync error [ref=%s code=%s] org=%s server=%s: %s",
        ref, code, org_slug, server_slug, str(raw)[:1000],
    )
    _store_sync_diagnostic(ref, code, raw, org_slug=org_slug, server_slug=server_slug)
    return SyncError(f"{summary} (Ref: {ref})", code, ref)


def _discover_tools_via_gateway(server, org) -> tuple[list[dict], str | None]:
    """Discover tools from an MCP server via the gateway's internal endpoint.

    Works for stdio, websocket, and streamable-http servers. The gateway
    routes the JSON-RPC tools/list call to the appropriate adapter or
    directly to the upstream MCP server.

    Returns ``(tools, error)``. ``error`` is ``None`` on success or a short
    human-readable string on failure. Previously this swallowed every
    failure and returned ``[]``, which surfaced to the operator as a
    misleading ``synced: 0`` with no cause -- the single biggest reason
    the MCP sync feature appeared silently broken.
    """
    gateway_url = (
        (getattr(settings, "GATEWAY_URL", "") or "").strip().rstrip("/")
        or os.environ.get("GATEWAY_URL", "").strip().rstrip("/")
        or "http://gateway:8300"
    )
    internal_key = _gateway_internal_secret()
    if not internal_key:
        logger.warning("Cannot discover tools via gateway: no GATEWAY_INTERNAL_API_KEY configured")
        return [], "Gateway internal key not configured (GATEWAY_INTERNAL_API_KEY)."

    payload = {
        "org_slug": org.slug,
        "server_slug": server.server_slug,
        # Send the authoritative url/transport so the gateway overrides any
        # stale TTL-cached config (fixes edit-then-resync using the old URL).
        "url": server.url or "",
        "transport": server.transport or "streamable-http",
    }

    # Pass auth credentials for upstream HTTP servers
    if server.transport in ("streamable-http", "sse") and hasattr(server, "auth_type"):
        auth_type = getattr(server, "auth_type", "none") or "none"
        if auth_type == "oauth":
            # Defer discovery until the operator completes OAuth — background
            # sync on a freshly registered HTTP OAuth server must NOT mark
            # needs_reauth or surface "token expired" before first authorize.
            if _oauth_pending_initial_authorization(server):
                return [], None
            # Refresh if needed, then forward the OAuth access token as a
            # normal bearer so the gateway needs no OAuth awareness. Only
            # forward when the token is usable; an expired/unrefreshable token
            # is rejected upstream as opaque ``invalid_token``, so instead we
            # short-circuit with an actionable re-auth error.
            if _ensure_oauth_token_fresh(server) and server.auth_token:
                payload["auth_type"] = "bearer"
                payload["auth_token"] = server.auth_token
            else:
                return [], (
                    f"{server.server_slug} needs re-authentication — "
                    "the OAuth token expired and could not be refreshed."
                )
        elif auth_type != "none":
            payload["auth_type"] = auth_type
            for field in ("auth_token", "auth_username", "auth_password",
                          "auth_header_key", "auth_header_value"):
                val = getattr(server, field, None)
                if val:
                    payload[field] = val

    try:
        resp = requests.post(
            f"{gateway_url}/v1/mcp/internal/discover-tools",
            json=payload,
            headers={
                "Content-Type": "application/json",
                "X-Gateway-Internal-Key": internal_key,
            },
            timeout=_DISCOVER_HTTP_TIMEOUT,
        )
        if resp.status_code != 200:
            logger.warning(
                "Gateway discover-tools returned HTTP %s for %s/%s: %s",
                resp.status_code, org.slug, server.server_slug,
                resp.text[:500],
            )
            return [], _sanitize_sync_error(f"gateway HTTP {resp.status_code}: {resp.text[:500]}", org_slug=org.slug, server_slug=server.server_slug)

        data = resp.json()
        # JSON-RPC response: {"jsonrpc":"2.0","id":1,"result":{"tools":[...]}}
        # A JSON-RPC error object means the upstream MCP rejected the call
        # (e.g. auth failure) -- surface it rather than reporting 0 tools.
        if isinstance(data, dict) and data.get("error"):
            err = data["error"]
            msg = err.get("message") if isinstance(err, dict) else str(err)
            return [], _sanitize_sync_error(msg, org_slug=org.slug, server_slug=server.server_slug)
        result = data.get("result", {})
        tools = result.get("tools", [])
        if isinstance(tools, list):
            logger.info(
                "Gateway discover-tools found %d tools for %s/%s",
                len(tools), org.slug, server.server_slug,
            )
            return tools, None
        return [], _sanitize_sync_error("malformed tools/list response from gateway", org_slug=org.slug, server_slug=server.server_slug)
    except Exception as exc:
        logger.warning(
            "Gateway discover-tools failed for %s/%s: %s",
            org.slug, server.server_slug, exc,
        )
        return [], _sanitize_sync_error(f"discovery request failed: {exc}", org_slug=org.slug, server_slug=server.server_slug)


def _resync_server_tools(server, org) -> dict:
    """Discover tools via the gateway and persist them for *server*.

    Shared by the API re-sync view (:class:`MCPServerToolListView`) and the
    ``resync_mcp_servers`` management command so both paths handle pruning,
    connection status, and ``last_sync_error`` identically. Returns a summary
    dict ``{synced, pruned, error, connection_status}``.
    """
    server_tool_names: set[str] = set()
    gateway_tools, sync_error = _discover_tools_via_gateway(server, org)
    for tool in gateway_tools:
        tname = tool.get("name", "")
        if tname:
            server_tool_names.add(tname)
            MCPToolRegistration.objects.update_or_create(
                server=server,
                tool_name=tname,
                defaults={
                    "description": tool.get("description", ""),
                    "input_schema": tool.get("inputSchema", {}),
                    "organization": org,
                    "last_seen_at": timezone.now(),
                },
            )
    # Prune tools the upstream no longer advertises -- ONLY on success, so a
    # transient upstream/auth failure does not wipe the operator's view.
    pruned = 0
    if sync_error is None:
        stale = MCPToolRegistration.objects.filter(server=server).exclude(
            tool_name__in=server_tool_names
        )
        pruned = stale.count()
        stale.delete()
    server.tools_count = MCPToolRegistration.objects.filter(server=server).count()
    server.last_sync_at = timezone.now()
    oauth_pending = _oauth_pending_initial_authorization(server) and sync_error is None
    if oauth_pending:
        server.connection_status = "unknown"
    else:
        server.connection_status = "connected" if (sync_error is None) else "failed"
    update_fields = ["tools_count", "last_sync_at", "connection_status", "updated_at"]
    # OAuth token PRESENT + UNEXPIRED but the upstream STILL rejected it (401/403):
    # re-authorizing just re-mints a token of the same kind the server already
    # refused, so the generic "re-authorize the connection" message sends the operator
    # in a loop ("even though I clicked re-authorize"). Replace it with an honest,
    # non-looping message and leave needs_reauth False — re-auth cannot fix an OAuth
    # config / audience / account-access mismatch on an otherwise-valid token.
    if (
        sync_error is not None
        and _oauth_token_present_and_unexpired(server)
        and _is_auth_rejection_message(sync_error)
    ):
        _ref = re.search(r"\(Ref: [0-9a-fA-F]{6,}\)", sync_error or "")
        sync_error = (
            "The MCP server rejected your OAuth token even though it is valid and "
            "unexpired — re-authorizing will not help. Check this server's OAuth "
            "configuration (scopes, resource/audience) and that your account has "
            "access to it." + (f" {_ref.group(0)}" if _ref else "")
        )
    if hasattr(server, "last_sync_error"):
        server.last_sync_error = "" if oauth_pending else (sync_error or "")
        update_fields.append("last_sync_error")
    # Map an interactive-auth / BYOK-OAuth failure (e.g. a stdio `mcp-remote`
    # server whose headless OAuth login the gateway detected and short-circuited)
    # to an actionable ``needs_reauth`` badge instead of a generic "failed", so
    # the operator sees "re-authenticate" rather than an opaque error. Cleared
    # on success or any non-auth failure.
    if hasattr(server, "needs_reauth"):
        if oauth_pending:
            server.needs_reauth = False
        else:
            _err_l = (sync_error or "").lower()
            server.needs_reauth = sync_error is not None and any(
                h in _err_l
                for h in (
                    "interactive authentication",
                    "requires re-authentication",
                    "re-authentication",
                    "re-authenticate",
                    "re-authorize",
                    "byok / oauth",
                    "needs_reauth",
                    # HTTP OAuth via sandbox (-32001 / upstream 401)
                    "upstream returned 401",
                    "401;",
                    " 401",
                    "invalid_token",
                    "invalid token",
                    "unauthorized",
                )
            )
        update_fields.append("needs_reauth")
    if hasattr(server, "last_sync_attempt_at"):
        server.last_sync_attempt_at = timezone.now()
        update_fields.append("last_sync_attempt_at")
    server.save(update_fields=update_fields)
    return {
        "synced": len(server_tool_names),
        "pruned": pruned,
        "error": sync_error,
        # Stable, client-facing error code + correlation id (present only on a
        # sanitized failure; a raw string or None carries neither). (CP17)
        "error_code": getattr(sync_error, "code", None),
        "correlation_id": getattr(sync_error, "ref", None),
        "connection_status": server.connection_status,
    }


class _MCPToolCallError(requests.RequestException):
    """A tool-call failure carrying the intended client-facing HTTP status, so the
    view returns 404 (unknown tool) / 400 (bad request) instead of a blanket 502.
    Subclasses RequestException so existing ``except requests.RequestException``
    handlers still catch it; the view reads ``http_status`` to pick the code. 502
    stays reserved for genuine gateway/upstream OUTAGES (connection/timeout). (M6)
    """

    def __init__(self, *args, http_status: int = 502, **kwargs):
        super().__init__(*args, **kwargs)
        self.http_status = http_status


def _call_tool_via_gateway(
    server,
    org,
    tool_name: str,
    arguments: dict,
    *,
    actor: dict | None = None,
) -> dict:
    """Execute a tool through the gateway's internal MCP route.

    This is used for all MCP server transports (stdio, websocket,
    streamable-http, sse). The gateway proxies directly to the upstream
    URL (or spawns the stdio process) and enforces policy in-band.

    ``actor`` is optional ``{user_id, agent_id, roles}`` so the gateway
    adapter scan path can honor actor-scoped MCP policies / field RBAC
    (parity with org_mcp_jsonrpc). Control already evaluated policies
    with the same actor; the gateway re-uses it for field projection.
    """
    gateway_url = (
        (getattr(settings, "GATEWAY_URL", "") or "").strip().rstrip("/")
        or os.environ.get("GATEWAY_URL", "").strip().rstrip("/")
        or "http://gateway:8300"
    )
    internal_key = _gateway_internal_secret()
    if not internal_key:
        raise requests.RequestException(
            "Gateway tool execution unavailable: missing GATEWAY_INTERNAL_API_KEY"
        )

    payload = {
        "org_slug": org.slug,
        "server_slug": server.server_slug,
        "tool_name": tool_name,
        "arguments": arguments or {},
        # Authoritative url/transport so the gateway overrides stale cached config.
        "url": server.url or "",
        "transport": server.transport or "streamable-http",
    }
    if actor:
        # Only forward non-empty identity fields (gateway treats missing as None).
        _actor_out: dict = {}
        if actor.get("user_id") is not None:
            _actor_out["user_id"] = actor["user_id"]
        if actor.get("agent_id"):
            _actor_out["agent_id"] = str(actor["agent_id"])[:64]
        if actor.get("roles"):
            _actor_out["roles"] = [str(r)[:64] for r in list(actor["roles"])[:16]]
        if _actor_out:
            payload["actor"] = _actor_out

    if server.transport in ("streamable-http", "sse") and hasattr(server, "auth_type"):
        auth_type = getattr(server, "auth_type", "none") or "none"
        if auth_type == "oauth":
            if _ensure_oauth_token_fresh(server) and server.auth_token:
                payload["auth_type"] = "bearer"
                payload["auth_token"] = server.auth_token
            else:
                raise requests.RequestException(
                    f"{server.server_slug} needs re-authentication — "
                    "the OAuth token expired and could not be refreshed."
                )
        elif auth_type != "none":
            payload["auth_type"] = auth_type
            for field in (
                "auth_token",
                "auth_username",
                "auth_password",
                "auth_header_key",
                "auth_header_value",
            ):
                val = getattr(server, field, None)
                if val:
                    payload[field] = val

    try:
        resp = requests.post(
            f"{gateway_url}/v1/mcp/internal/tools-call",
            json=payload,
            headers={
                "Content-Type": "application/json",
                "X-Gateway-Internal-Key": internal_key,
            },
            timeout=90,
        )
        if resp.status_code != 200:
            # Propagate a 4xx from the gateway as a client error; collapse 5xx to
            # 502 (genuine upstream failure). (M6)
            _st = 502 if resp.status_code >= 500 else resp.status_code
            raise _MCPToolCallError(
                f"Gateway tool execution returned HTTP {resp.status_code}: {resp.text[:500]}",
                http_status=_st,
            )

        data = resp.json() if resp.content else {}
        if not isinstance(data, dict):
            raise requests.RequestException("Gateway tool execution returned non-JSON payload")

        rpc_error = data.get("error")
        if isinstance(rpc_error, dict):
            # The gateway+upstream RESPONDED with a JSON-RPC error → not an outage.
            # -32601 (method not found) / "unknown tool" → 404; -32602 (invalid
            # params) → 400; any other application-level error → 400. (M6)
            _code = rpc_error.get("code")
            _msg = rpc_error.get("message") or "Gateway tool execution failed"
            _ml = _msg.lower()
            if _code == -32601 or "not found" in _ml or "unknown tool" in _ml:
                raise _MCPToolCallError(_msg, http_status=404)
            if _code == -32602:
                raise _MCPToolCallError(_msg, http_status=400)
            raise _MCPToolCallError(_msg, http_status=400)
        if rpc_error:
            raise _MCPToolCallError(str(rpc_error), http_status=400)

        # C1: a tool-execution FAILURE is most commonly reported NOT via the
        # top-level JSON-RPC ``error`` key but via the MCP result convention
        # ``result: {isError: true, content: [{type:"text", text:"..."}]}``
        # (stdio servers, streamable-http servers, etc.). Without inspecting it,
        # a failed / unknown-tool call fell through to a success return → the
        # caller recorded ``decision=allow`` and returned HTTP 200, defeating the
        # M6 error classification and corrupting the audit trail (SOC blind
        # spot). Surface it as the same typed error so the -32601→404 /
        # -32602→400 mapping applies and the MCPEvent records a failure.
        _result = data.get("result")
        if isinstance(_result, dict) and _result.get("isError"):
            _err_text = ""
            _content = _result.get("content")
            if isinstance(_content, list):
                _err_text = " ".join(
                    str(p.get("text", "")) for p in _content if isinstance(p, dict)
                ).strip()
            _err_text = _err_text or "MCP tool execution reported an error"
            _etl = _err_text.lower()
            if "-32601" in _etl or "not found" in _etl or "unknown tool" in _etl:
                raise _MCPToolCallError(_err_text, http_status=404)
            if "-32602" in _etl or "invalid" in _etl:
                raise _MCPToolCallError(_err_text, http_status=400)
            raise _MCPToolCallError(_err_text, http_status=400)

        return _result if isinstance(_result, dict) else data
    except requests.RequestException:
        raise
    except Exception as exc:
        raise requests.RequestException(f"Gateway tool execution failed: {exc}") from exc


# ── Health ────────────────────────────────────────────────────────────


class MCPServicesHealthView(APIView):
    """Health status of Secure-MCP-Gateway and MCP-Firewall.

    ContextForge has been removed (DECISION-D Phase 0); the gateway proxies
    streamable-http/sse directly to upstream URLs and spawns stdio processes
    via the local adapter, so there is no upstream registry to probe.
    """

    permission_classes = [IsAuthenticatedOrGatewayInternal]

    # Detectors enforced in-band by the policy engine on every tool call.
    # Enkrypt-only detectors (hallucination/toxicity) were never enforced on
    # the hot path and have been removed to end the policy/guardrail duality.
    _BUILTIN_DETECTORS = [
        "pii_redaction", "injection_attack", "sensitive_data",
        "profanity", "topic_restriction",
        "policy_violation", "pii_leakage", "data_exfiltration",
    ]

    def get(self, request):
        return Response(
            {
                "mcp_firewall": mcp_firewall_client.health(),
                "builtin_detectors": self._BUILTIN_DETECTORS,
            }
        )


# ── MCP Servers ───────────────────────────────────────────────────


def _trigger_background_sync(server, org) -> None:
    """CLEANUP-07: kick off discovery/sync for a freshly-registered server in a
    daemon thread so registration returns immediately while the state resolves
    (unknown → syncing → connected/failed) — it must NEVER linger at "unknown".

    ``_resync_server_tools`` does the gateway ``discover-tools`` round-trip + the
    status/tool update; running it off-thread keeps the POST /servers/ response
    fast (and covers non-UI registrations that never call POST /servers/<id>/tools/).
    The thread gets its own DB connection, so close old connections at both ends to
    avoid leaking one. Any failure is swallowed + logged — a background sync hiccup
    must never surface as a registration error.
    """
    import threading

    from django.db import close_old_connections

    def _run():
        close_old_connections()
        try:
            _resync_server_tools(server, org)
        except Exception:  # noqa: BLE001
            logger.exception("background sync failed for %s/%s",
                             getattr(org, "slug", "?"), getattr(server, "server_slug", "?"))
        finally:
            close_old_connections()

    threading.Thread(target=_run, name=f"mcp-sync-{server.pk}", daemon=True).start()


class MCPServerListCreateView(APIView):
    """List all registered MCP servers or register a new one."""

    permission_classes = [IsAuthenticatedOrGatewayInternal]

    def get(self, request):
        registrations, _org = _org_scoped_servers_queryset(request)
        serializer = MCPServerRegistrationSerializer(registrations, many=True)
        return Response(serializer.data)

    def post(self, request):
        org = _request_org(request)
        if org is None:
            return Response(
                {"error": "No organization context for this user."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = MCPServerCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        transport = (serializer.validated_data.get("transport") or "").strip().lower()
        local_data = MCPServerCreateSerializer.local_model_data(serializer.validated_data)

        # DECISION-D Phase 0: ContextForge removed. All transports register
        # locally; the gateway proxies streamable-http/sse directly to the
        # upstream URL and spawns stdio processes via mcp_stdio_adapter.
        # Policy enforcement (G7 redaction, G8 scope, tool toggles, compliance
        # tagging) runs in-band on every tool call regardless of transport.
        try:
            registration, created = MCPServerRegistration.objects.get_or_create(
                organization=org,
                name=local_data["name"],
                defaults={k: v for k, v in local_data.items() if k != "name"},
            )
        except IntegrityError:
            return Response(
                {"error": "An MCP server with this name already exists in your organization."},
                status=status.HTTP_409_CONFLICT,
            )
        if not created:
            return Response(
                {
                    "error": "An MCP server with this name already exists in your organization.",
                    "existing_server": MCPServerRegistrationSerializer(registration).data,
                },
                status=status.HTTP_409_CONFLICT,
            )
        logger.info(
            "mcp_connector.server.registered org_id=%s user_id=%s server=%s transport=%s",
            org.id,
            request.user.id,
            registration.name,
            transport,
        )

        # ── Auto-provision a default MCP gateway key for this org ──
        gw_key_info = {}
        try:
            from core.models import GatewayAPIKey

            actor = request.user if getattr(request.user, "is_authenticated", False) else None
            if actor is None:
                from django.contrib.auth import get_user_model
                User = get_user_model()
                actor = User.objects.filter(is_superuser=True).order_by("id").first()
            if actor:
                key_instance, raw_key = GatewayAPIKey.ensure_default_for_org(org, actor)
                gw_key_info["default_gateway_key_prefix"] = key_instance.prefix
                gw_key_info["has_gateway_key"] = True
                if raw_key:
                    gw_key_info["default_gateway_key"] = raw_key
                    gw_key_info["gateway_key_warning"] = (
                        "A default MCP gateway API key was auto-created for your organization. "
                        "Store it securely — it will not be shown again."
                    )
                    logger.info(
                        "mcp_connector.gateway_key.auto_provisioned org_id=%s prefix=%s",
                        org.id,
                        key_instance.prefix,
                    )
        except Exception:
            logger.exception("Failed to auto-provision MCP gateway key for org %s", org.id)

        # CLEANUP-07: registration triggers discovery/sync so the state resolves
        # (unknown → syncing → connected/failed) and NEVER lingers at "unknown".
        # Mark "syncing" now (the response reflects it) and resolve off-thread; the
        # UI's own inline sync (POST /tools/) still runs and is idempotent.
        registration.connection_status = "syncing"
        registration.save(update_fields=["connection_status", "updated_at"])
        _trigger_background_sync(registration, org)

        response_data = MCPServerRegistrationSerializer(registration).data
        response_data.update(gw_key_info)

        return Response(
            response_data,
            status=status.HTTP_201_CREATED,
        )


class MCPServerDetailView(APIView):
    """Retrieve, update, or delete a single MCP server registration."""

    permission_classes = [IsAuthenticatedOrGatewayInternal]

    def get(self, request, pk):
        registrations, _org = _org_scoped_servers_queryset(request)
        try:
            reg = registrations.get(pk=pk)
        except MCPServerRegistration.DoesNotExist:
            return Response({"error": "Not found"}, status=status.HTTP_404_NOT_FOUND)
        return Response(MCPServerRegistrationSerializer(reg).data)

    def patch(self, request, pk):
        registrations, _org = _org_scoped_servers_queryset(request)
        try:
            reg = registrations.get(pk=pk)
        except MCPServerRegistration.DoesNotExist:
            return Response({"error": "Not found"}, status=status.HTTP_404_NOT_FOUND)

        serializer = MCPServerCreateSerializer(reg, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)

        # DECISION-D Phase 0: no upstream registry to update; server config is
        # the single source of truth and the gateway re-reads it via the
        # `_get_server_config` Redis cache (TTL 120s) on next tool call.
        local_data = MCPServerCreateSerializer.local_model_data(serializer.validated_data)
        for field, value in local_data.items():
            setattr(reg, field, value)
        reg.save(update_fields=[*local_data.keys(), "updated_at"])
        return Response(MCPServerRegistrationSerializer(reg).data)

    def delete(self, request, pk):
        registrations, org = _org_scoped_servers_queryset(request)
        try:
            reg = registrations.get(pk=pk)
        except MCPServerRegistration.DoesNotExist:
            return Response({"error": "Not found"}, status=status.HTTP_404_NOT_FOUND)

        logger.info(
            "mcp_connector.server.deleted org_id=%s user_id=%s server=%s",
            getattr(org, "id", None),
            request.user.id,
            reg.name,
        )
        reg.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# ── Tool Discovery ──────────────────────────────────────────────────────


class MCPToolListView(APIView):
    """List all tools available across all federated MCP servers."""

    permission_classes = [IsAuthenticatedOrGatewayInternal]

    def get(self, request):
        registrations, org = _org_scoped_servers_queryset(request)
        if org is None:
            return Response([])

        server_slug_hint = (request.headers.get("X-Server-Slug", "") or "").strip().lower()
        if server_slug_hint:
            target_server = registrations.filter(server_slug=server_slug_hint).first()
            if target_server is None:
                return Response(
                    {"error": "Server not found for this organization."},
                    status=status.HTTP_404_NOT_FOUND,
                )
            registrations = registrations.filter(server_slug=server_slug_hint)

        # Source tools from local inventory. Inventory is updated via the
        # per-server sync endpoint which calls the gateway's
        # /v1/mcp/internal/discover-tools route for every transport.       
        tools = []
        tool_qs = MCPToolRegistration.objects.filter(
            organization=org,
            server__in=registrations,
        ).select_related("server")
        for tr in tool_qs:
            tools.append(
                {
                    "name": tr.tool_name,
                    "description": tr.description or "",
                    "inputSchema": tr.input_schema or {},
                    "server_name": tr.server.name,
                    "server_id": str(tr.server.id),
                    "server_slug": tr.server.server_slug,
                    "transport": tr.server.transport,
                    "enabled": tr.enabled,
                    "source": "registration",
                }
            )

        logger.info(
            "mcp_connector.tools.listed org_id=%s user_id=%s tool_count=%s",
            org.id,
            request.user.id,
            len(tools) if isinstance(tools, list) else 0,
        )
        return Response(tools)


class MCPToolCallView(APIView):
    """Invoke an MCP tool with pre-flight policy, tool controls, and post-flight audit."""

    permission_classes = [IsAuthenticatedOrGatewayInternal]

    def post(self, request):
        _registrations, org = _org_scoped_servers_queryset(request)
        if org is None:
            return Response(
                {"error": "No organization context for this user."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        actor_user_id, _actor_username = _request_actor(request)

        if not isinstance(request.data, dict):
            return Response(
                {"error": "Request body must be a JSON object."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        tool_name = request.data.get("name")
        arguments = request.data.get("arguments", {})
        if not tool_name:
            return Response({"error": "Missing 'name' field"}, status=status.HTTP_400_BAD_REQUEST)

        server_slug_hint = (
            request.headers.get("X-Server-Slug", "")
            or request.data.get("server_slug", "")
            or ""
        ).strip().lower()
        requested_server = None
        if server_slug_hint:
            requested_server = MCPServerRegistration.objects.filter(
                organization=org,
                server_slug=server_slug_hint,
            ).first()
            if requested_server is None:
                return Response(
                    {"error": "Server not found for this organization.", "server_slug": server_slug_hint},
                    status=status.HTTP_404_NOT_FOUND,
                )

        # Prefer the gateway-forwarded stable id so gateway + control rows for the
        # same call correlate; fall back to a fresh id for direct API callers.
        request_id = (
            (request.headers.get("X-Request-Id", "") or "").strip()
            or str(uuid_mod.uuid4())[:16]
        )[:64]
        t0 = time.time()

        # De-dup: a gateway-originated tool call is recorded once by the gateway
        # data plane (with the full two-tier scan_trace) via /internal/record-event/.
        # Shadow _record_event locally so this view SKIPS the duplicate control-plane
        # MCPEvent for those calls; direct (non-gateway) API callers still record.
        _gateway_originated = _is_gateway_internal_request(request)
        _record_event_impl = globals()["_record_event"]

        def _record_event(*_a, **_kw):  # noqa: A001 — intentional local shadow
            if _gateway_originated:
                return None
            return _record_event_impl(*_a, **_kw)

        # ── Tool enable/disable check (hardened against X-Server-Slug evasion) ──
        # Load EVERY registration of this tool across the org's servers, not just
        # the one matching the slug hint, so a caller cannot route a disabled tool
        # through a different server slug where it happens to be unregistered.
        org_tool_regs = list(
            MCPToolRegistration.objects.filter(
                organization=org, tool_name=tool_name
            ).select_related("server")
        )

        # mcp #4 / mcp #32 (flag-only): classify mutating/destructive tools ONCE,
        # up-front — BEFORE any block/error early-return — so the OWASP-MCP01
        # write-risk signal is present on EVERY audit event (blocked + errored),
        # not only on the success path. Primary signal is the tool NAME; the
        # description (when a registration row exists) is a secondary hint. Does
        # NOT block — operators decide whether to disable a flagged tool.
        _classify_desc = next(
            (tr.description for tr in org_tool_regs if tr.description), ""
        )
        _write_risk = _classify_tool_write_risk(tool_name, _classify_desc)
        _write_risk_meta = {
            "write_risk": _write_risk,
            "is_write_tool": _write_risk != "read",
        }

        # (a) Evasion guard: if this tool is disabled on ANY of the org's servers,
        #     block regardless of which server slug was requested.
        disabled_reg = next((tr for tr in org_tool_regs if not tr.enabled), None)
        if disabled_reg is not None:
            _record_event(
                org=org, request=request, tool_name=tool_name,
                decision="block", reason="tool_disabled",
                request_id=request_id, latency_ms=int((time.time() - t0) * 1000),
                server_name=disabled_reg.server.name if disabled_reg.server else "",
                server_slug=disabled_reg.server.server_slug if disabled_reg.server else "",
                metadata=dict(_write_risk_meta),
            )
            return Response(
                {"error": "Tool is disabled", "reason": "tool_disabled", "request_id": request_id},
                status=status.HTTP_403_FORBIDDEN,
            )

        # (b) Require a registered+enabled tool for the RESOLVED server. An unknown
        #     tool (no registration row) is denied rather than blindly forwarded.
        if requested_server is not None:
            tool_reg = next(
                (tr for tr in org_tool_regs if tr.server_id == requested_server.id), None
            )
        else:
            tool_reg = org_tool_regs[0] if org_tool_regs else None

        if tool_reg is None:
            _record_event(
                org=org, request=request, tool_name=tool_name,
                decision="block", reason="tool_not_registered",
                request_id=request_id, latency_ms=int((time.time() - t0) * 1000),
                server_name=requested_server.name if requested_server else "",
                server_slug=requested_server.server_slug if requested_server else "",
                metadata=dict(_write_risk_meta),
            )
            return Response(
                {
                    "error": "Tool is not registered for this server",
                    "reason": "tool_not_registered",
                    "request_id": request_id,
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        resolved_server = requested_server or tool_reg.server

        # ── SEC-03 FIX: JSON Schema validation ──
        validation_errors = _validate_tool_arguments(tool_reg, arguments)
        if validation_errors:
            _record_event(
                org=org, request=request, tool_name=tool_name,
                decision="block", reason="schema_validation_failed",
                request_id=request_id, latency_ms=int((time.time() - t0) * 1000),
                server_name=resolved_server.name if resolved_server else "",
                server_slug=resolved_server.server_slug if resolved_server else "",
                metadata=dict(_write_risk_meta),
            )
            logger.warning(
                "mcp_connector.tool.schema_validation_failed org=%s tool=%s errors=%s",
                org.id, tool_name, validation_errors,
            )
            return Response(
                {
                    "error": "Schema validation failed",
                    "reason": "invalid_arguments",
                    "validation_errors": validation_errors,
                    "request_id": request_id,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ── PRE-FLIGHT: Built-in policy engine check (org+server scoped) ──
        from policy.engine import evaluate as policy_evaluate
        from policy.models import Policy as PolicyModel
        from policy.redaction import apply_field_redaction, redact_structured
        from django.db.models import Q
        from urllib.parse import unquote as _unquote

        # ── G8: extract actor identifiers for per-user/agent/role policy
        # scoping. user_id is JWT-authenticated (or trusted gateway
        # header); agent_id is the API key prefix (8 chars, identity of
        # the calling key); roles come from the gateway's signed Redis
        # auth payload, forwarded as a comma-separated URL-quoted header.
        # All three feed BOTH the policy queryset filter AND the engine
        # context so rule conditions can also reference them.
        agent_id = (request.headers.get("X-Gateway-Key-Prefix", "") or "").strip()
        raw_roles_header = request.headers.get("X-Gateway-Roles", "") or ""
        actor_roles: list[str] = []
        if raw_roles_header:
            for chunk in raw_roles_header.split(","):
                chunk = chunk.strip()
                if not chunk:
                    continue
                try:
                    actor_roles.append(_unquote(chunk))
                except Exception:
                    actor_roles.append(chunk)

        arg_text = " ".join(str(v) for v in arguments.values()) if arguments else ""
        policy_context = {
            "prompt": f"tool:{tool_name} {arg_text}",
            "response": "",
            # Structured tool arguments so scope='key' input rules can target
            # a single argument by name (engine reads context["input_args"]).
            "input_args": arguments or {},
            "user_id": actor_user_id,
            "agent_id": agent_id,
            "roles": actor_roles,
        }
        # Get policies: org-specific + system-wide (org=None), MCP domain only.
        policy_qs = PolicyModel.objects.filter(
            enabled=True,
            policy_domain="mcp",
        ).filter(
            Q(organization=org) | Q(organization__isnull=True)
        ).prefetch_related("rules")
        if resolved_server:
            policy_qs = policy_qs.filter(
                Q(mcp_server__isnull=True) | Q(mcp_server=resolved_server)
            )
        # ── G8: actor allowlist filters. Each dimension uses
        # "empty list = wildcard" semantics (existing rows after
        # migration 0029 default to []). ``__isnull=True`` is included
        # defensively in case any row predates the migration and was
        # not backfilled. ``__contains=[v]`` is the correct ArrayField
        # containment operator (Django 6 + Postgres array @>); we use
        # ``__overlap`` for roles since a user may have multiple roles
        # and we want match if ANY user role intersects the allowlist.
        if actor_user_id is not None:
            policy_qs = policy_qs.filter(
                Q(allowed_user_ids__isnull=True)
                | Q(allowed_user_ids=[])
                | Q(allowed_user_ids__contains=[actor_user_id])
            )
        else:
            # Anonymous/unauthenticated: only policies with empty
            # allowlist (wildcard) may apply.
            policy_qs = policy_qs.filter(
                Q(allowed_user_ids__isnull=True) | Q(allowed_user_ids=[])
            )
        if agent_id:
            policy_qs = policy_qs.filter(
                Q(allowed_agent_ids__isnull=True)
                | Q(allowed_agent_ids=[])
                | Q(allowed_agent_ids__contains=[agent_id])
            )
        else:
            policy_qs = policy_qs.filter(
                Q(allowed_agent_ids__isnull=True) | Q(allowed_agent_ids=[])
            )
        if actor_roles:
            policy_qs = policy_qs.filter(
                Q(allowed_roles__isnull=True)
                | Q(allowed_roles=[])
                | Q(allowed_roles__overlap=actor_roles)
            )
        else:
            policy_qs = policy_qs.filter(
                Q(allowed_roles__isnull=True) | Q(allowed_roles=[])
            )
        # Materialization is deferred until AFTER ``policy_evaluate``
        # because the engine calls ``.filter(policy_domain=...)`` on the
        # queryset and would crash on a list. We re-query by id below
        # to collect each matched policy's ``redaction_fields`` for
        # post-call response scrubbing (G7).
        eval_result = policy_evaluate(policy_context, policies_qs=policy_qs, domain="mcp", tool_name=tool_name)
        if eval_result.action == "block":
            _record_event(
                org=org, request=request, tool_name=tool_name,
                decision="block",
                reason=eval_result.message or "blocked_by_builtin_policy",
                policy_ids=eval_result.matched_policy_ids,
                request_id=request_id, latency_ms=int((time.time() - t0) * 1000),
                server_name=resolved_server.name if resolved_server else "",
                server_slug=resolved_server.server_slug if resolved_server else "",
                metadata={
                    "matched_policy_codes": list(eval_result.matched_policy_codes or []),
                    "matched_rule_names": list(eval_result.matched_rule_names or []),
                    **_write_risk_meta,
                },
            )
            logger.warning(
                "mcp_connector.tool.blocked_by_policy org=%s user=%s tool=%s policies=%s",
                org.id, actor_user_id, tool_name, eval_result.matched_policy_codes,
            )
            return Response(
                {
                    "error": "Tool call blocked by policy",
                    "reason": eval_result.message,
                    "matched_policies": eval_result.matched_policy_codes,
                    "matched_rules": eval_result.matched_rule_names,
                    "request_id": request_id,
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        # Resolve the per-tier INPUT action from the MCPScanControl matrix (the
        # SAME source the gateway uses) so the control-plane input path honors
        # the operator's per-tier posture. 'monitor' = observe-only: the gateway
        # has already enforced the matrix on input, so the control plane must NOT
        # additionally mutate the arguments under a monitor posture.
        _input_action = "inherit"
        try:
            from mcp_connector.scan_controls import (
                resolve_effective_controls as _resolve_ctrls,
                serialize_control as _ser_ctrl,
            )
            _in_rows = [
                _ser_ctrl(c) for c in MCPScanControl.objects.filter(organization=org)
            ]
            _in_eff = _resolve_ctrls(
                _in_rows,
                server_id=str(resolved_server.id) if resolved_server else None,
                tool_name=tool_name,
            )
            _input_action = ((_in_eff.get("tier1_input") or {}).get("action") or "inherit")
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "mcp_connector.tool.input_action_resolve_failed org=%s tool=%s err=%s",
                org.id, tool_name, exc,
            )

        # ── INPUT redaction: scrub sensitive data OUT of the tool arguments
        # BEFORE they are forwarded to the gateway/tool. Driven by redact
        # rules whose direction includes the input side (input|both). Skipped
        # under a 'monitor' posture (observe-only — the matrix said detect, not
        # mutate). ``arguments`` is replaced with a redacted copy used for the
        # actual call; the original is not mutated.
        if _input_action != "monitor" and eval_result.action != "block" and eval_result.redaction_hints:
            try:
                redacted_args = redact_structured(
                    arguments or {}, eval_result.redaction_hints, "input"
                )
                if isinstance(redacted_args, dict):
                    arguments = redacted_args
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning(
                    "mcp_connector.tool.input_redaction_failed org=%s tool=%s err=%s",
                    org.id, tool_name, exc,
                )

        # ── PRE-FLIGHT: MCP-Firewall policy check (external, best-effort) ──
        policy_result = mcp_firewall_client.preflight_check(
            tool_name, arguments,
            org_id=str(org.id), user_id=str(actor_user_id or ""),
            server_name=resolved_server.name if resolved_server else "",
        )
        if not policy_result.get("allowed", True):
            _record_event(
                org=org, request=request, tool_name=tool_name,
                decision="block", reason=policy_result.get("reason", "blocked_by_policy"),
                policy_ids=policy_result.get("policy_ids", []),
                request_id=request_id, latency_ms=int((time.time() - t0) * 1000),
                server_name=resolved_server.name if resolved_server else "",
                server_slug=resolved_server.server_slug if resolved_server else "",
                metadata={
                    "matched_policy_codes": list(policy_result.get("violations", []) or []),
                    "matched_rule_names": [],
                    **_write_risk_meta,
                },
            )
            logger.warning(
                "mcp_connector.tool.blocked org=%s user=%s tool=%s reason=%s",
                org.id, actor_user_id, tool_name, policy_result.get("reason"),
            )
            return Response(
                {
                    "error": "Tool call blocked by policy",
                    "reason": policy_result.get("reason", ""),
                    "violations": policy_result.get("violations", []),
                    "request_id": request_id,
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        # ── EXECUTE: always via gateway (DECISION-D Phase 0) ──
        try:
            if not resolved_server:
                return Response(
                    {"error": "Tool call failed", "detail": "No server resolved for tool", "request_id": request_id},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            logger.info(
                "mcp_connector.tool.gateway_execution org_id=%s user_id=%s tool=%s server=%s transport=%s",
                org.id,
                actor_user_id,
                tool_name,
                resolved_server.server_slug,
                (resolved_server.transport or "").strip().lower(),
            )
            result = _call_tool_via_gateway(
                resolved_server,
                org,
                tool_name,
                arguments,
                actor={
                    "user_id": actor_user_id,
                    "agent_id": agent_id,
                    "roles": list(actor_roles),
                },
            )
        except requests.RequestException as exc:
            latency_ms = int((time.time() - t0) * 1000)
            mcp_firewall_client.postflight_audit(
                tool_name, org_id=str(org.id), user_id=str(actor_user_id or ""),
                decision="error", success=False, latency_ms=latency_ms,
                server_name=resolved_server.name if resolved_server else "",
            )
            _record_event(
                org=org, request=request, tool_name=tool_name,
                decision="error", reason=str(exc),
                request_id=request_id, latency_ms=latency_ms,
                server_name=resolved_server.name if resolved_server else "",
                server_slug=resolved_server.server_slug if resolved_server else "",
                metadata=dict(_write_risk_meta),
            )
            logger.warning(
                "mcp_connector.tool.failed org_id=%s user_id=%s tool=%s detail=%s",
                org.id, actor_user_id, tool_name, str(exc),
            )
            # M6: honor a typed http_status (404 unknown-tool / 400 bad-request);
            # default 502 only for genuine outages (plain RequestException).
            _http_status = getattr(exc, "http_status", status.HTTP_502_BAD_GATEWAY)
            _err_label = (
                "Tool not found" if _http_status == 404
                else "Invalid tool request" if _http_status == 400
                else "Tool call failed"
            )
            return Response(
                {"error": _err_label, "detail": _upstream_error_detail(exc), "request_id": request_id},
                status=_http_status,
            )

        latency_ms = int((time.time() - t0) * 1000)

        # ── OUTPUT evaluation: re-run the SAME policy set against the tool
        # RESPONSE so rules whose direction targets the output side can act
        # on returned data. Pre-call evaluation saw an empty response, so
        # output rules could not have matched yet. We populate both a
        # serialized ``response`` string (entire-scope rules) and the raw
        # ``output_data`` dict (scope='key' rules).
        try:
            output_context = {
                "prompt": "",
                "response": json.dumps(result, ensure_ascii=False, default=str)
                if not isinstance(result, str)
                else result,
                "output_data": result,
                "user_id": actor_user_id,
                "agent_id": agent_id,
                "roles": actor_roles,
            }
            eval_out = policy_evaluate(
                output_context, policies_qs=policy_qs, domain="mcp", tool_name=tool_name
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "mcp_connector.tool.output_eval_failed org=%s tool=%s err=%s",
                org.id, tool_name, exc,
            )
            eval_out = None

        # Output BLOCK: a rule decided the RESPONSE must not leave. The tool
        # already executed, but we refuse to return its data to the caller.
        if eval_out is not None and eval_out.action == "block":
            _record_event(
                org=org, request=request, tool_name=tool_name,
                decision="block",
                reason=eval_out.message or "blocked_by_output_policy",
                policy_ids=eval_out.matched_policy_ids,
                request_id=request_id, latency_ms=latency_ms,
                server_name=resolved_server.name if resolved_server else "",
                server_slug=resolved_server.server_slug if resolved_server else "",
                metadata={
                    "matched_policy_codes": list(eval_out.matched_policy_codes or []),
                    "matched_rule_names": list(eval_out.matched_rule_names or []),
                    "stage": "output",
                    **_write_risk_meta,
                },
            )
            logger.warning(
                "mcp_connector.tool.output_blocked org=%s user=%s tool=%s policies=%s",
                org.id, actor_user_id, tool_name, eval_out.matched_policy_codes,
            )
            return Response(
                {
                    "error": "Tool response blocked by policy",
                    "reason": eval_out.message,
                    "matched_policies": eval_out.matched_policy_codes,
                    "matched_rules": eval_out.matched_rule_names,
                    "request_id": request_id,
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        # Merge output-stage matches into the aggregate result used for
        # event logging / G7 field-union and the block decision below.
        if eval_out is not None:
            eval_result.matched_policy_ids = list(
                dict.fromkeys(eval_result.matched_policy_ids + eval_out.matched_policy_ids)
            )
            eval_result.matched_policy_codes = list(
                dict.fromkeys(eval_result.matched_policy_codes + eval_out.matched_policy_codes)
            )
            eval_result.matched_rule_names = list(
                dict.fromkeys(eval_result.matched_rule_names + eval_out.matched_rule_names)
            )

        # ── Resolve effective scan enforcement (per-tool override → server
        #    default → 'tag'). 'block' is a FLOOR: detected OUTPUT PII BLOCKS the
        #    call instead of being silently redacted-and-allowed. This is the A4
        #    fix for the streamable-http path: the gateway's outbound scan only
        #    ever sees the POST-redaction response, so the block decision must be
        #    made here, where the raw tool output and the policy match coexist.
        _tool_action = getattr(tool_reg, "scan_action", "inherit") if tool_reg else "inherit"
        if _tool_action and _tool_action != "inherit":
            _scan_action = _tool_action
        elif resolved_server is not None:
            _scan_action = getattr(resolved_server, "default_scan_action", "tag") or "tag"
        else:
            _scan_action = "tag"
        # Source the action from the MCPScanControl matrix (tier1_output) — the
        # SAME source the gateway uses — so control-plane and gateway never
        # split-brain on the output decision. 'inherit' (or no row) keeps the
        # per-tool/server fallback resolved just above.
        try:
            from mcp_connector.scan_controls import (
                resolve_effective_controls,
                serialize_control,
            )
            _scan_rows = [
                serialize_control(c)
                for c in MCPScanControl.objects.filter(organization=org)
            ]
            _eff_ctrls = resolve_effective_controls(
                _scan_rows,
                server_id=str(resolved_server.id) if resolved_server else None,
                tool_name=tool_name,
            )
            _row_action = ((_eff_ctrls.get("tier1_output") or {}).get("action") or "inherit")
            if _row_action and _row_action != "inherit":
                _scan_action = _row_action
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "mcp_connector.tool.scan_control_action_resolve_failed org=%s tool=%s err=%s",
                org.id, tool_name, exc,
            )

        # ── G7: compute the response-field redaction union (recursively over the
        # tool result). Trigger (per decision D6) is "policy matched AND has
        # non-empty redaction_fields" — NOT gated on action == 'redact', because
        # field redaction is an output-shaping concern orthogonal to the verdict.
        # We compute the field set FIRST (without mutating ``result``) so the
        # block-vs-redact decision below can see whether output PII was found.
        redacted_field_names: list[str] = []
        if eval_result.matched_policy_ids:
            try:
                from policy.models import Policy as _PolicyModel
                fields_union: set[str] = set()
                for fields in _PolicyModel.objects.filter(
                    id__in=list(eval_result.matched_policy_ids),
                ).values_list("redaction_fields", flat=True):
                    if fields:
                        fields_union.update(f for f in fields if isinstance(f, str) and f)
                if fields_union:
                    redacted_field_names = sorted(fields_union)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning(
                    "mcp_connector.tool.field_redaction_failed org=%s tool=%s err=%s",
                    org.id, tool_name, exc,
                )

        _output_pii_detected = bool(
            (eval_out is not None and (eval_out.matched_rule_ids or eval_out.redaction_hints))
            or redacted_field_names
        )

        # Compliance tagging (best-effort): union the frameworks implied by the
        # matched presets/entity types across BOTH the input-stage and
        # output-stage redaction hints, so control-plane enforcement events
        # carry the same compliance tags the gateway scan path emits.
        _enforcement_compliance_tags = _mcp_compliance_tags(
            getattr(eval_result, "redaction_hints", None),
            getattr(eval_out, "redaction_hints", None) if eval_out is not None else None,
        )

        if _scan_action == "block" and _output_pii_detected:
            latency_ms = int((time.time() - t0) * 1000)
            mcp_firewall_client.postflight_audit(
                tool_name, org_id=str(org.id), user_id=str(actor_user_id or ""),
                decision="block", success=False, latency_ms=latency_ms,
                server_name=resolved_server.name if resolved_server else "",
            )
            _record_event(
                org=org, request=request, tool_name=tool_name,
                decision="block", reason="pii_blocked_outbound", request_id=request_id,
                latency_ms=latency_ms,
                server_name=resolved_server.name if resolved_server else "",
                server_slug=resolved_server.server_slug if resolved_server else "",
                policy_ids=eval_result.matched_policy_ids,
                compliance_tags=_enforcement_compliance_tags,
                metadata={
                    "matched_policy_codes": list(eval_result.matched_policy_codes or []),
                    "matched_rule_names": list(eval_result.matched_rule_names or []),
                    "redacted_field_names": redacted_field_names,
                    "scan_action": _scan_action,
                    "stage": "output",
                    **_write_risk_meta,
                },
            )
            return Response(
                {
                    "blocked": True,
                    "decision": "block",
                    "detail": (
                        f"Response from '{tool_name}' withheld: output matched a "
                        f"compliance policy under a 'block' enforcement posture."
                    ),
                    "matched_policies": list(eval_result.matched_policy_codes or []),
                    "request_id": request_id,
                },
                status=status.HTTP_200_OK,
            )

        # Observe-only: monitor (and tag without a policy redact verdict) detect + audit,
        # no output mutation. Policy-authored redact still applies under tag.
        _policy_output_redact = (
            eval_out is not None
            and eval_out.action == "redact"
            and bool(eval_out.redaction_hints)
        )
        _output_monitor = (
            _scan_action in ("monitor", "tag")
            and _output_pii_detected
            and not _policy_output_redact
        )
        if eval_out is not None and eval_out.redaction_hints and not _output_monitor:
            try:
                result = redact_structured(result, eval_out.redaction_hints, "output")
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning(
                    "mcp_connector.tool.output_redaction_failed org=%s tool=%s err=%s",
                    org.id, tool_name, exc,
                )
        if redacted_field_names and not _output_monitor:
            try:
                result = apply_field_redaction(result, redacted_field_names)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning(
                    "mcp_connector.tool.field_redaction_failed org=%s tool=%s err=%s",
                    org.id, tool_name, exc,
                )

        # ── POST-FLIGHT: audit record ──
        # decision='monitor' when a monitor-posture control detected output PII
        # (observe-only: allowed, unmutated, audited); else 'allow'.
        _final_decision = "monitor" if _output_monitor else "allow"
        # mcp #4 (flag-only): the mutating/destructive classification was computed
        # once up-front (so blocked/errored events carry it too — mcp #32); reuse
        # ``_write_risk`` here for the success-path label + log. Does NOT block —
        # operators disable a flagged tool if needed.
        mcp_firewall_client.postflight_audit(
            tool_name, org_id=str(org.id), user_id=str(actor_user_id or ""),
            decision=_final_decision, success=True, latency_ms=latency_ms,
            server_name=resolved_server.name if resolved_server else "",
        )
        _record_event(
            org=org, request=request, tool_name=tool_name,
            decision=_final_decision, request_id=request_id, latency_ms=latency_ms,
            server_name=resolved_server.name if resolved_server else "",
            server_slug=resolved_server.server_slug if resolved_server else "",
            policy_ids=eval_result.matched_policy_ids,
            compliance_tags=_enforcement_compliance_tags,
            metadata={
                "matched_policy_codes": list(eval_result.matched_policy_codes or []),
                "matched_rule_names": list(eval_result.matched_rule_names or []),
                "redacted_field_names": redacted_field_names,
                "scan_action": _scan_action,
                "monitored": _output_monitor,
                "actor_agent_id": agent_id,
                "actor_roles": actor_roles,
                "write_risk": _write_risk,
                "is_write_tool": _write_risk != "read",
            },
        )

        if _write_risk != "read":
            logger.warning(
                "mcp_connector.tool.write_flagged org_id=%s tool=%s write_risk=%s decision=%s "
                "(flag-only, not blocked)",
                org.id, tool_name, _write_risk, _final_decision,
            )
        logger.info(
            "mcp_connector.tool.called org_id=%s user_id=%s agent=%s tool=%s success=true latency_ms=%s decision=%s redacted_fields=%s",
            org.id, actor_user_id, agent_id, tool_name, latency_ms, _final_decision, redacted_field_names,
        )
        return Response({"result": result, "request_id": request_id, "decision": _final_decision})


# ── Helper: Record structured MCP event ──────────────────────────────


def _record_event(
    org,
    request,
    tool_name: str,
    decision: str,
    reason: str = "",
    policy_ids: list | None = None,
    request_id: str = "",
    latency_ms: int = 0,
    server_name: str = "",
    server_slug: str = "",
    metadata: dict | None = None,
    compliance_tags: list | None = None,
    scan_findings: list | None = None,
):
    """Write a structured MCPEvent record.

    INVARIANT 3 (Tenant Isolation): Events without org context are
    recorded with decision='error' so observability is never silently
    un-scoped.
    INVARIANT 5 (Observability Completeness): Missing actor or server
    context is flagged in metadata so partial events are visible.
    """
    try:
        actor_user_id, actor_username = _request_actor(request)
        ev_metadata = dict(metadata or {})

        # ── Sanitise client/free-text fields before persistence (stored-XSS +
        # unbounded-growth guard); these are rendered in the operator dashboard. ──
        tool_name = _sanitize_event_text(tool_name, 255)
        server_name = _sanitize_event_text(server_name, 255)
        server_slug = _sanitize_event_text(server_slug, 255)
        reason = _sanitize_event_text(reason, 500)

        # ── Invariant 3: reject org-less events ──
        if org is None:
            logger.error(
                "INVARIANT-3 VIOLATION: Attempted to record MCP event "
                "without organization context (tool=%s, decision=%s)",
                tool_name, decision,
            )
            ev_metadata["invariant_violation"] = "missing_org"
            decision = "error"

        # ── Invariant 5: flag incomplete observability fields ──
        missing = []
        if not actor_user_id:
            missing.append("user_id")
        if not actor_username:
            missing.append("username")
        if not server_slug:
            missing.append("server_slug")
        if not tool_name:
            missing.append("tool_name")
        if missing:
            ev_metadata["incomplete_fields"] = missing
            logger.warning(
                "INVARIANT-5: MCP event has missing observability fields: %s "
                "(tool=%s, server=%s)", missing, tool_name, server_slug,
            )

        # Recursively sanitise caller-supplied JSON before persistence so the
        # scalar-field sanitisation above cannot be bypassed via the metadata /
        # compliance_tags / scan_findings channels (stored-XSS + NUL + deep-nest).
        ev_metadata = _sanitize_event_structure(ev_metadata)
        # CHG-0059: normalize onto the ComplianceTag catalog vocabulary so every
        # MCPEvent write site upholds the documented ``compliance_tags = list of
        # ComplianceTag.code`` contract. Idempotent (this path already emits catalog
        # codes via _mcp_compliance_tags); never raises (audit must not break).
        try:
            from ai_mesh_shared.mcp_compliance_tags import to_catalog_codes

            safe_compliance_tags = to_catalog_codes(
                _sanitize_event_structure(compliance_tags or [])
            )
        except Exception:  # pragma: no cover - defensive, never break recording
            safe_compliance_tags = _sanitize_event_structure(compliance_tags or [])
        safe_scan_findings = _sanitize_event_structure(scan_findings or [])

        MCPEvent.objects.create(
            organization=org,
            user_id=actor_user_id,
            username=actor_username,
            server_slug=server_slug,
            server_name=server_name,
            tool_name=tool_name,
            decision=decision,
            policy_ids=policy_ids or [],
            policy_reason=reason,
            latency_ms=latency_ms,
            request_id=request_id,
            metadata=ev_metadata,
            compliance_tags=safe_compliance_tags,
            scan_findings=safe_scan_findings,
        )

        # Module 2 + §1.4: mirror MCP → EnforcementEvent (source=mcp_scan); async path = tasks.py
        if org is not None:
            try:
                from policy.models import EnforcementEvent as _EnforcementEvent
                from policy.models import Policy as _Policy, Rule as _Rule

                # Capture request transport/client info for LogDetail panel.
                _src_ip = ""
                _ua = ""
                if request is not None and getattr(request, "META", None):
                    _src_ip = (
                        request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip()
                        or request.META.get("REMOTE_ADDR", "")
                        or ""
                    )
                    _ua = request.META.get("HTTP_USER_AGENT", "") or ""

                _status_code_map = {
                    "block": 403,
                    "redact": 200,
                    "monitor": 200,
                    "allow": 200,
                    "scan_skipped": 200,
                    "error": 502,
                }
                _status_code = _status_code_map.get(decision, 200)

                # Map MCPEvent decision → EnforcementEvent action.
                # 'allow' is recorded as 'monitor' so success traffic still
                # appears on dashboard timelines without being mis-tagged
                # as a block. 'error' is also recorded as 'monitor'.
                # 'scan_skipped' = zero scan controls (no Tier-1/Tier-2) —
                # observably distinct in MCPEvent, mapped to monitor for SOC.
                _action_map = {
                    "block": "block",
                    "redact": "redact",
                    "monitor": "monitor",
                    "allow": "monitor",
                    "scan_skipped": "monitor",
                    "error": "monitor",
                }
                _action = _action_map.get(decision, "monitor")

                _risk_map = {
                    "block": 85,
                    "redact": 55,
                    "monitor": 25,
                    "allow": 10,
                    "scan_skipped": 5,
                    "error": 40,
                }
                _risk = _risk_map.get(decision, 10)

                # Best-effort threat category from reason / decision.
                _reason = (reason or "").lower()
                if decision == "block":
                    if "schema" in _reason:
                        _threat_category = "MCP Schema Violation"
                        _owasp = "MCP08"
                    elif "tool_disabled" in _reason:
                        _threat_category = "MCP Tool Disabled"
                        _owasp = "MCP01"
                    elif "policy" in _reason or policy_ids:
                        _threat_category = "MCP Policy Violation"
                        _owasp = "MCP02"
                    else:
                        _threat_category = "MCP Tool Block"
                        _owasp = "MCP01"
                elif decision == "redact":
                    _threat_category = "MCP Data Redaction"
                    _owasp = "MCP06"
                elif decision == "error":
                    _threat_category = "MCP Tool Error"
                    _owasp = "MCP01"
                else:
                    _threat_category = "MCP Tool Call"
                    _owasp = "MCP01"

                # Resolve a policy + rule FK for richer dashboard rows.
                _policy_obj = None
                _rule_obj = None
                if policy_ids:
                    try:
                        _policy_obj = _Policy.objects.filter(pk=policy_ids[0]).first()
                    except Exception:
                        _policy_obj = None

                _ef_metadata = {
                    "source": "mcp_scan",
                    "threat_category": _threat_category,
                    "owasp_code": _owasp,
                    "owasp_codes": [_owasp] if _owasp else [],
                    "decision": decision,
                    "reason": reason or "",
                    "tool_name": tool_name,
                    "tools_invoked": [tool_name] if tool_name else [],
                    "data_accessed": [server_slug] if server_slug else [],
                    "server_slug": server_slug,
                    "server_name": server_name,
                    "request_id": request_id,
                    "pipeline_request_id": request_id,
                    "latency_ms": latency_ms,
                    "policy_ids": policy_ids or [],
                    "security_risk_score": _risk,
                    "actor_username": actor_username or "",
                    "incomplete_fields": ev_metadata.get("incomplete_fields", []),
                    # ── Detail-panel fields (Request / Response / Security / Metadata)
                    "method": "POST",
                    "endpoint": f"/api/mcp-connector/tools/call/ ({tool_name})",
                    "source_ip": _src_ip,
                    "user_agent": _ua,
                    "model": tool_name,
                    "status_code": _status_code,
                    "pipeline_stage": "mcp_tool_call",
                    "intent": f"mcp:{tool_name}",
                    "event_type": "mcp_tool_call",
                    "compliance_tags": ["OWASP-MCP", _owasp],
                    "rate_limit_status": "n/a",
                    "auth_status": "authenticated" if actor_user_id else "anonymous",
                    "input_validation": "schema_ok" if decision != "block" or "schema" not in (reason or "").lower() else "schema_failed",
                    "content_safety": "violation" if decision == "block" else "ok",
                    "pii_detected": decision == "redact",
                    "prompt_injection_detected": "MCP_TOOL_INJECT" in str(ev_metadata.get("matched_policy_codes") or []),
                    "jailbreak_detected": "MCP_EVASION" in str(ev_metadata.get("matched_policy_codes") or []),
                    "policy_violations": list(ev_metadata.get("matched_policy_codes") or []),
                    "extra": {
                        "matched_patterns": list(ev_metadata.get("matched_rule_names") or []),
                        "request_id": request_id,
                    },
                }
                # Carry through any extra context the caller passed
                # (e.g. matched_policy_codes, matched_rule_names). Use the
                # already-sanitised ``ev_metadata`` (NOT the raw ``metadata``
                # arg) so caller-controlled keys/values cannot smuggle raw
                # <script>/control-chars/NUL bytes into the dashboard mirror.
                for _k, _v in (ev_metadata or {}).items():
                    _ef_metadata.setdefault(_k, _v)

                # Defence-in-depth: recursively sanitise the full mirror payload
                # before persistence. A stray NUL byte makes the Postgres jsonb
                # INSERT fail (DataError), which the broad except below would
                # otherwise swallow — silently dropping MCP traffic from the
                # operator dashboard.
                _ef_metadata = _sanitize_event_structure(_ef_metadata)

                _ef_ev = _EnforcementEvent.objects.create(
                    organization=org,
                    policy=_policy_obj,
                    rule=_rule_obj,
                    action=_action,
                    user_id=actor_user_id,
                    metadata=_ef_metadata,
                )
                # Module 2: live WS notify after mirror (must not undo MCPEvent/EF)
                try:
                    from ws.notify import send_enforcement_notification

                    send_enforcement_notification(
                        {
                            "type": "enforcement_event",
                            "id": str(_ef_ev.id),
                            "action": _ef_ev.action,
                            "timestamp": _ef_ev.created_at.isoformat() if _ef_ev.created_at else None,
                            "severity": _ef_metadata.get("security_risk_score") or "medium",
                            "category": _ef_metadata.get("threat_category") or "Policy",
                            "subcategory": _ef_metadata.get("owasp_code") or "",
                            "source": _ef_metadata.get("source", "mcp_scan"),
                            "user_id": _ef_ev.user_id,
                            "endpoint_id": _ef_ev.endpoint_id,
                            "agent_id": str(_ef_ev.agent_id) if _ef_ev.agent_id else None,
                            "organization_id": _ef_ev.organization_id,
                            "metadata": _ef_metadata,
                        },
                        organization_id=_ef_ev.organization_id,
                    )
                except Exception as _notify_exc:
                    logger.warning("Failed to notify MCP mirrored enforcement event: %s", _notify_exc)
            except Exception as _ef_exc:
                logger.warning(
                    "Failed to mirror MCP event to EnforcementEvent: %s",
                    _ef_exc,
                )
    except Exception as exc:
        logger.warning("Failed to record MCP event: %s", exc)


# ── Tool Controls (per-server per-tool enable/disable) ───────────────


class MCPServerToolListView(APIView):
    """List or sync tool registrations for a specific MCP server."""

    permission_classes = [IsAuthenticatedOrGatewayInternal]

    def get(self, request, pk):
        """List tool registrations with their enable/disable state."""
        registrations, org = _org_scoped_servers_queryset(request)
        if org is None:
            return Response([], status=status.HTTP_200_OK)
        try:
            server = registrations.get(pk=pk)
        except MCPServerRegistration.DoesNotExist:
            return Response({"error": "Not found"}, status=status.HTTP_404_NOT_FOUND)

        tools = MCPToolRegistration.objects.filter(server=server)
        return Response(MCPToolRegistrationSerializer(tools, many=True).data)

    def post(self, request, pk):
        """Sync tools by asking the gateway to discover them on the upstream server.

        DECISION-D Phase 0: a single discovery path — the gateway's
        /v1/mcp/internal/discover-tools route handles every transport
        (stdio, websocket, streamable-http, sse) uniformly.
        """
        registrations, org = _org_scoped_servers_queryset(request)
        if org is None:
            return Response({"error": "No org context"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            server = registrations.get(pk=pk)
        except MCPServerRegistration.DoesNotExist:
            return Response({"error": "Not found"}, status=status.HTTP_404_NOT_FOUND)

        server_tool_names = set()

        result = _resync_server_tools(server, org)
        sync_error = result["error"]
        pruned = result["pruned"]

        tools = MCPToolRegistration.objects.filter(server=server)
        return Response({
            "synced": result["synced"],
            "pruned": pruned,
            "error": str(sync_error) if sync_error else sync_error,
            # Stable client-facing error code + correlation id for support/dev
            # cross-reference (CP17). None on success. The message wording may
            # change; the code is the stable contract.
            "error_code": result.get("error_code"),
            "correlation_id": result.get("correlation_id"),
            "connection_status": server.connection_status,
            "tools": MCPToolRegistrationSerializer(tools, many=True).data,
        })


class MCPDiagnosticDetailView(APIView):
    """DEV-ONLY diagnostic lookup by correlation ref (CP18).

    Staff/superuser ONLY (``IsAdminUser`` → ``request.user.is_staff``); an org
    client — even an org *admin* — is never staff, so this is never exposed to
    clients. Returns the REAL cause (stable code, raw error text, org/server)
    that the sanitized client message + ``(Ref: …)`` deliberately withholds, so
    a developer can debug the exact failure (exit code, upstream body, sandbox
    reason) using only the correlation id the client reported.
    """

    permission_classes = [IsAdminUser]

    def get(self, request, ref):
        if not re.fullmatch(r"[0-9a-f]{6,32}", ref or ""):
            return Response(
                {"error": "Invalid correlation ref."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # Control-originated diagnostics (django_redis cache), then GATEWAY-originated
        # (raw Redis key written by the gateway classifier — CLEANUP-05) so this one
        # staff-only endpoint resolves any correlation ref regardless of which service
        # sanitized the error.
        record = cache.get(_diag_cache_key(ref)) or _read_gateway_diagnostic(ref)
        if not record:
            return Response(
                {"error": "No diagnostic for this correlation ref (expired or unknown).",
                 "ref": ref},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(record)


class MCPToolControlView(APIView):
    """Enable/disable a tool or update its sensitivity."""

    permission_classes = [IsAuthenticatedOrGatewayInternal]

    def patch(self, request, pk, tool_name):
        registrations, org = _org_scoped_servers_queryset(request)
        if org is None:
            return Response({"error": "No org context"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            server = registrations.get(pk=pk)
        except MCPServerRegistration.DoesNotExist:
            return Response({"error": "Server not found"}, status=status.HTTP_404_NOT_FOUND)

        try:
            tool = MCPToolRegistration.objects.get(server=server, tool_name=tool_name)
        except MCPToolRegistration.DoesNotExist:
            return Response({"error": "Tool not found"}, status=status.HTTP_404_NOT_FOUND)

        if "enabled" in request.data:
            tool.enabled = bool(request.data["enabled"])
        if "sensitivity" in request.data:
            allowed = [c[0] for c in MCPToolRegistration.SENSITIVITY_CHOICES]
            if request.data["sensitivity"] in allowed:
                tool.sensitivity = request.data["sensitivity"]
        # Accept the new ``scan_action`` key (and the legacy ``presidio_action``
        # during the rename window) for the per-tool enforcement override.
        _scan_action = request.data.get("scan_action", request.data.get("presidio_action"))
        if _scan_action is not None:
            allowed_actions = {"inherit", "tag", "redact", "block"}
            if _scan_action in allowed_actions:
                tool.scan_action = _scan_action
        tool.save()
        return Response(MCPToolRegistrationSerializer(tool).data)


# ── Gateway-internal: enable/disable enforcement helpers ─────────────


class MCPScanControlListCreateView(APIView):
    """List or create MCP scan controls for the current organization."""

    permission_classes = [IsAuthenticatedOrGatewayInternal]

    def get(self, request):
        org = _request_org(request)
        if org is None:
            return Response({"error": "No organization context."}, status=status.HTTP_400_BAD_REQUEST)
        qs = MCPScanControl.objects.filter(organization=org).select_related("server")
        server_id = request.query_params.get("server_id")
        if server_id:
            qs = qs.filter(server_id=server_id)
        return Response(MCPScanControlSerializer(qs, many=True).data)

    def post(self, request):
        org = _request_org(request)
        if org is None:
            return Response({"error": "No organization context."}, status=status.HTTP_400_BAD_REQUEST)
        serializer = MCPScanControlSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        # Strict tenant isolation: a scan-control may only reference a server
        # that belongs to the caller's own organization.
        target_server = serializer.validated_data.get("server")
        if target_server is not None and target_server.organization_id != org.id:
            return Response(
                {"server": "Server does not belong to your organization."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        instance = serializer.save(organization=org)
        return Response(MCPScanControlSerializer(instance).data, status=status.HTTP_201_CREATED)


class MCPScanControlDetailView(APIView):
    """Retrieve, update, or delete a single MCP scan control."""

    permission_classes = [IsAuthenticatedOrGatewayInternal]

    def _get_object(self, request, pk):
        org = _request_org(request)
        if org is None:
            return None, Response({"error": "No organization context."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            return MCPScanControl.objects.select_related("server").get(pk=pk, organization=org), None
        except MCPScanControl.DoesNotExist:
            return None, Response({"error": "Not found"}, status=status.HTTP_404_NOT_FOUND)

    def get(self, request, pk):
        obj, err = self._get_object(request, pk)
        if err:
            return err
        return Response(MCPScanControlSerializer(obj).data)

    def patch(self, request, pk):
        obj, err = self._get_object(request, pk)
        if err:
            return err
        serializer = MCPScanControlSerializer(obj, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        # F6: the `server` FK is a free writable field over an all-tenants
        # queryset, so a PATCH could re-point this org's scan-control at ANOTHER
        # org's MCP server (the POST create path was org-guarded; PATCH was not).
        # Reject a target server not owned by the request org (mirrors POST).
        _org = _request_org(request)
        _tgt = serializer.validated_data.get("server")
        if _tgt is not None and getattr(_tgt, "organization_id", None) != getattr(_org, "id", None):
            return Response(
                {"server": "Server does not belong to your organization."},
                status=400,
            )
        serializer.save()
        return Response(MCPScanControlSerializer(obj).data)

    def delete(self, request, pk):
        obj, err = self._get_object(request, pk)
        if err:
            return err
        obj.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class MCPGatewayEnabledToolsView(APIView):
    """Gateway-internal lookup of enabled/disabled tools for a server.

    Used by the gateway proxy to enforce per-tool enable/disable for
    stdio and websocket transports (which bypass the normal HTTP
    backend tool-call path).

    GET /api/mcp-connector/internal/enabled-tools/?server_slug=<slug>
        Returns: {
            "server_slug": "<slug>",
            "known_tools": ["a","b","c"],   # all registered tool names
            "enabled_tools": ["a","b"],     # subset where enabled=True
            "disabled_tools": ["c"]
        }
    """

    permission_classes = [IsAuthenticatedOrGatewayInternal]

    def get(self, request):
        org = _request_org(request)
        if org is None:
            return Response(
                {"error": "No organization context."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        server_slug = (
            request.query_params.get("server_slug")
            or request.headers.get("X-Server-Slug")
            or ""
        ).strip().lower()
        if not server_slug:
            return Response(
                {"error": "server_slug required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            server = MCPServerRegistration.objects.get(
                organization=org, server_slug=server_slug,
            )
        except MCPServerRegistration.DoesNotExist:
            return Response(
                {"error": "Server not found", "server_slug": server_slug},
                status=status.HTTP_404_NOT_FOUND,
            )

        regs = list(MCPToolRegistration.objects.filter(server=server).values(
            "tool_name", "enabled", "scan_action",
        ))
        known = [r["tool_name"] for r in regs]
        enabled = [r["tool_name"] for r in regs if r["enabled"]]
        disabled = [r["tool_name"] for r in regs if not r["enabled"]]
        # Surface per-tool scan-action overrides plus the server-level fallback so
        # the gateway can decide per call without an extra round-trip. Tools with
        # "inherit" are omitted from tool_actions.
        tool_actions = {
            r["tool_name"]: r["scan_action"]
            for r in regs
            if r.get("scan_action") and r["scan_action"] != "inherit"
        }

        scan_rows = [
            serialize_control(c)
            for c in MCPScanControl.objects.filter(organization=org)
        ]
        effective_by_tool: dict[str, dict] = {}
        for tool in known:
            effective_by_tool[tool] = resolve_effective_controls(
                scan_rows,
                server_id=str(server.id),
                tool_name=tool,
            )
        org_effective = resolve_effective_controls(
            scan_rows,
            server_id=str(server.id),
            tool_name="",
        )

        mcp_tier2_enabled = None
        tier2_strict = True
        mcp_policy_only_enforcement = False
        try:
            from core.models import FirewallConfig

            fw = FirewallConfig.objects.filter(organization=org).first()
            if fw is not None:
                mcp_tier2_enabled = fw.mcp_tier2_enabled
                tier2_strict = fw.tier2_strict
                # Per-org Phase-3 cutover flag — surfaced into the gateway enabled_info so the
                # gateway's policy-only observe-only gate is reachable per-org (not only via the
                # gateway-wide env kill-switch). Without this the operator toggle is inoperative.
                mcp_policy_only_enforcement = bool(fw.mcp_policy_only_enforcement)
        except Exception:
            pass

        return Response({
            "server_slug": server_slug,
            "server_name": server.name,
            "server_id": str(server.id),
            "known_tools": known,
            "enabled_tools": enabled,
            "disabled_tools": disabled,
            # New engine-agnostic keys; legacy presidio_* aliases retained so a
            # gateway running mid-rename (reading from its <=30s cache) never
            # fails open to "tag". Remove the aliases once all gateways are updated.
            "default_scan_action": server.default_scan_action,
            "tool_scan_actions": tool_actions,
            "default_presidio_action": server.default_scan_action,
            "tool_presidio_actions": tool_actions,
            "scan_controls": scan_rows,
            "scan_controls_configured": bool(scan_rows),
            "effective_scan_controls": org_effective,
            "effective_scan_controls_by_tool": effective_by_tool,
            "mcp_tier2_enabled": mcp_tier2_enabled,
            "tier2_strict": tier2_strict,
            "mcp_policy_only_enforcement": mcp_policy_only_enforcement,
        })


class MCPGatewayRecordEventView(APIView):
    """Gateway-internal endpoint to record an MCPEvent.

    Used by the gateway when it short-circuits a tool call (e.g. blocks
    a disabled tool for stdio/websocket transports) so audit/observability
    stays consistent across transports.

    POST /api/mcp-connector/internal/record-event/
        Body: {
            "server_slug": "<slug>",
            "tool_name": "<name>",
            "decision": "allow|block|redact|monitor|scan_skipped|error",
            "reason": "<text>",
            "request_id": "<id>",
            "latency_ms": <int>,
            "metadata": {...}
        }
    """

    # Internal-only: an end-user JWT must never be able to inject/forge audit
    # events. Requires the gateway shared secret (X-Gateway-Internal-Key).
    permission_classes = [IsGatewayInternalOnly]

    def post(self, request):
        org = _gateway_request_org(request)
        if org is None:
            return Response(
                {"error": "No organization context."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        data = request.data or {}
        decision = (data.get("decision") or "").strip().lower()
        # Validate against the model's own decision choices so this allow-list
        # never drifts again when a new decision (e.g. 'monitor') is added to
        # MCPEvent.DECISION_CHOICES.
        _valid_decisions = {c[0] for c in MCPEvent._meta.get_field("decision").choices}
        if decision not in _valid_decisions:
            return Response(
                {"error": "invalid decision"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        server_slug = (data.get("server_slug") or "").strip().lower()
        server_name = data.get("server_name", "")
        if server_slug and not server_name:
            srv = MCPServerRegistration.objects.filter(
                organization=org, server_slug=server_slug,
            ).first()
            if srv:
                server_name = srv.name

        tool_name = data.get("tool_name", "")

        # R18: gateway-originated events must carry the same OWASP-MCP01
        # write/destructive-tool classification the direct MCPToolCallView path
        # attaches up-front, so the write-risk signal is present on EVERY MCP
        # audit event (including blocked/errored) regardless of which transport
        # short-circuited the call. Primary signal is the tool NAME; the
        # registered tool description (when present) is a secondary hint. The
        # gateway may already supply ``write_risk`` in metadata, so only compute
        # when absent (caller value wins — never clobber an explicit annotation).
        ev_metadata = data.get("metadata") or {}
        if not (isinstance(ev_metadata, dict) and "write_risk" in ev_metadata):
            _classify_desc = ""
            if tool_name:
                tool_reg = (
                    MCPToolRegistration.objects.filter(
                        organization=org, tool_name=tool_name,
                    )
                    .exclude(description="")
                    .values_list("description", flat=True)
                    .first()
                )
                _classify_desc = tool_reg or ""
            _write_risk = _classify_tool_write_risk(tool_name, _classify_desc)
            if not isinstance(ev_metadata, dict):
                ev_metadata = {}
            else:
                ev_metadata = dict(ev_metadata)
            ev_metadata["write_risk"] = _write_risk
            ev_metadata["is_write_tool"] = _write_risk != "read"

        _record_event(
            org=org,
            request=request,
            tool_name=tool_name,
            decision=decision,
            reason=data.get("reason", ""),
            policy_ids=data.get("policy_ids") or [],
            request_id=data.get("request_id", ""),
            latency_ms=int(data.get("latency_ms") or 0),
            server_name=server_name,
            server_slug=server_slug,
            metadata=ev_metadata,
            compliance_tags=data.get("compliance_tags") or [],
            scan_findings=data.get("scan_findings") or data.get("presidio_findings") or [],
        )
        return Response({"recorded": True}, status=status.HTTP_201_CREATED)


class MCPGatewayNeedsReauthView(APIView):
    """Gateway-internal endpoint to flag an org's MCP server as needing re-auth.

    Closes the Flow-2 backprop gap: for mcp-remote (stdio) servers the gateway
    holds the OAuth token and the control plane never learned about refresh
    failures, so an expired token surfaced only as an opaque upstream
    ``invalid_token``. The gateway now calls this when it cannot inject a
    usable token, letting control set ``needs_reauth`` and surface an
    actionable per-org "re-authenticate <server>" signal.

    POST /api/mcp-connector/internal/needs-reauth/
        Body: {"org_slug": "<slug>", "server_slug": "<slug>", "reason": "<text>"}
    """

    permission_classes = [IsAuthenticatedOrGatewayInternal]

    def post(self, request):
        org = _request_org(request)
        if org is None:
            return Response(
                {"error": "No organization context."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        data = request.data or {}
        server_slug = (data.get("server_slug") or "").strip().lower()
        if not server_slug:
            return Response(
                {"error": "server_slug required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        server = MCPServerRegistration.objects.filter(
            organization=org, server_slug=server_slug,
        ).first()
        if server is None:
            return Response(
                {"error": "server not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        reason = (data.get("reason") or "").strip() or (
            "OAuth token expired and refresh failed — re-authenticate this server."
        )
        _mark_needs_reauth(server, reason)
        return Response({"needs_reauth": True}, status=status.HTTP_200_OK)


# ── Org Gateway Key Provisioning ─────────────────────────────────────


class OrgGatewayKeyView(APIView):
    """Ensure the organization has a default MCP gateway API key.

    GET  — returns key metadata (prefix, name, active status).
    POST — creates one if none exists; returns plaintext key on first creation.
    """

    permission_classes = [IsAuthenticatedOrGatewayInternal]

    def _resolve(self, request):
        org = _request_org(request)
        if org is None:
            return None, None, Response(
                {"error": "No organization context for this user."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        actor = request.user if getattr(request.user, "is_authenticated", False) else None
        if actor is None:
            return None, None, Response(
                {"error": "Authentication required."},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        return org, actor, None

    def get(self, request):
        org, actor, err = self._resolve(request)
        if err:
            return err

        from core.models import GatewayAPIKey

        project_id = f"mcp-default-{org.slug}"
        key = GatewayAPIKey.objects.filter(
            organization=org,
            project_id=project_id,
            is_active=True,
        ).first()
        if key is None:
            # Also check for ANY active key in the org
            key = GatewayAPIKey.objects.filter(
                organization=org,
                is_active=True,
            ).first()

        if key:
            return Response({
                "has_gateway_key": True,
                "prefix": key.prefix,
                "name": key.name,
                "is_active": key.is_active,
                "project_id": key.project_id,
            })
        return Response({"has_gateway_key": False})

    def post(self, request):
        org, actor, err = self._resolve(request)
        if err:
            return err

        from core.models import GatewayAPIKey

        key_instance, raw_key = GatewayAPIKey.ensure_default_for_org(org, actor)
        data = {
            "has_gateway_key": True,
            "prefix": key_instance.prefix,
            "name": key_instance.name,
            "is_active": key_instance.is_active,
            "project_id": key_instance.project_id,
            "created": raw_key is not None,
        }
        if raw_key:
            data["key"] = raw_key
            data["warning"] = (
                "Store this key securely. It will not be shown again."
            )
            logger.info(
                "mcp_connector.gateway_key.provisioned org_id=%s prefix=%s",
                org.id,
                key_instance.prefix,
            )
        return Response(data, status=status.HTTP_201_CREATED if raw_key else status.HTTP_200_OK)


# ── Observability Events ─────────────────────────────────────────────


class MCPEventListView(APIView):
    """List structured MCP events with optional filters."""

    permission_classes = [IsAuthenticatedOrGatewayInternal]

    def get(self, request):
        _regs, org = _org_scoped_servers_queryset(request)
        if org is None:
            return Response([])

        events = MCPEvent.objects.filter(organization=org)

        # Time-window filter (hours). Omit or 0 = all-time.
        try:
            hours = int(request.query_params.get("hours", 0))
        except (TypeError, ValueError):
            hours = 0
        if hours > 0:
            from datetime import timedelta
            from django.utils import timezone as _tz
            events = events.filter(timestamp__gte=_tz.now() - timedelta(hours=hours))

        # Filters
        decision = request.query_params.get("decision")
        if decision:
            events = events.filter(decision=decision)
        tool = request.query_params.get("tool")
        if tool:
            events = events.filter(tool_name=tool)
        server = request.query_params.get("server")
        if server:
            events = events.filter(server_slug=server)
        user_id = request.query_params.get("user_id")
        if user_id:
            events = events.filter(user_id=user_id)

        # Limit
        limit = min(int(request.query_params.get("limit", 100)), 500)
        events = events[:limit]

        return Response(MCPEventSerializer(events, many=True).data)


class MCPEventSummaryView(APIView):
    """Aggregated summary of MCP events for dashboard cards."""

    permission_classes = [IsAuthenticatedOrGatewayInternal]

    def get(self, request):
        _regs, org = _org_scoped_servers_queryset(request)
        if org is None:
            return Response({})

        events = MCPEvent.objects.filter(organization=org)

        # Time-window filter (hours). Omit or 0 = all-time.
        try:
            hours = int(request.query_params.get("hours", 0))
        except (TypeError, ValueError):
            hours = 0
        if hours > 0:
            from datetime import timedelta
            from django.utils import timezone as _tz
            events = events.filter(timestamp__gte=_tz.now() - timedelta(hours=hours))

        # Decision counts
        decision_counts = dict(
            events.values_list("decision").annotate(count=Count("id")).values_list("decision", "count")
        )

        # Top tools
        top_tools = list(
            events.values("tool_name")
            .annotate(count=Count("id"))
            .order_by("-count")[:10]
        )

        # Top users
        top_users = list(
            events.filter(username__gt="")
            .values("username")
            .annotate(count=Count("id"))
            .order_by("-count")[:10]
        )

        # Recent events
        recent = MCPEventSerializer(events[:5], many=True).data

        return Response({
            "total": events.count(),
            "decisions": decision_counts,
            "top_tools": top_tools,
            "top_users": top_users,
            "recent": recent,
        })


# ── OAuth 2.1 authorization (Phase C) ────────────────────────────────


def _oauth_redirect_uri() -> str:
    """Browser-reachable callback URI registered with the authorization server.

    Must be a localhost or HTTPS URL per the MCP/OAuth spec. Overridable via
    env so a production deployment can point at its public control host.
    """
    return os.environ.get(
        "MCP_OAUTH_REDIRECT_URI",
        "http://localhost:8100/api/mcp-connector/oauth/callback",
    )


def _oauth_frontend_return_url(ok: bool, server_name: str = "", error: str = "") -> str:
    """Where the callback HTML bounces the browser back to after token exchange."""
    base = os.environ.get("MCP_OAUTH_FRONTEND_URL", "http://localhost:8180/?tab=firewall-1-4")
    return base


# OAuth 2.1 authorization-code (PKCE) is HTTP-only: the control-plane flow runs
# RFC 9728/8414 discovery + token exchange against an HTTP MCP endpoint URL.
_OAUTH_HTTP_TRANSPORTS = ("streamable-http", "sse")


def oauth_http_transport_error(server) -> str | None:
    """Return an error string iff ``server`` is INELIGIBLE for the control-plane
    HTTP OAuth flow, else ``None`` (B1 / MCP OAuth bug #1/#2 root fix).

    The registration serializer already rejects ``auth_type="oauth"`` on non-HTTP
    transports (``serializers.py`` transport guard). This is the matching
    server-side invariant for the *authorize* endpoint — enforcing it here makes
    the dup/broken control authorize path structurally unreachable:
      * a stdio / websocket row can never reach discovery (it authorizes upstream
        inside the gateway sandbox, not via this endpoint), so the misleading
        "Server has no URL" 400 is unreachable for the transport that actually
        triggered it; and
      * because the transport is validated BEFORE ``auth_type`` is persisted, the
        endpoint can no longer flip a stdio/websocket row to ``auth_type="oauth"``
        (the old guard-bypass that created an invalid oauth+stdio row).
    Order matters: check transport first so stdio gets the clear transport error,
    not the confusing "no URL" one.
    """
    if getattr(server, "transport", None) not in _OAUTH_HTTP_TRANSPORTS:
        return (
            "OAuth 2.1 (authorize via provider) requires an HTTP MCP transport "
            "(streamable-http or sse). stdio servers such as Linear via mcp-remote "
            "authorize upstream inside the gateway sandbox — this endpoint does not "
            "apply; leave the auth type as 'none'."
        )
    if not server.url:
        return "Server has no URL; OAuth is only for HTTP transports."
    return None


class MCPServerOAuthStartView(APIView):
    """Begin the OAuth 2.1 authorization-code (PKCE) flow for a server.

    POST /servers/<pk>/oauth/authorize/ → run discovery + dynamic client
    registration, generate PKCE + state, persist the transient flow state, and
    return the authorization URL for the operator to open in their browser.
    """

    permission_classes = [IsAuthenticatedOrGatewayInternal]

    def post(self, request, pk):
        from . import oauth as oauth_mod

        registrations, org = _org_scoped_servers_queryset(request)
        if org is None:
            return Response({"error": "No org context"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            server = registrations.get(pk=pk)
        except MCPServerRegistration.DoesNotExist:
            return Response({"error": "Not found"}, status=status.HTTP_404_NOT_FOUND)

        # B1 root fix: enforce the HTTP-transport invariant BEFORE any discovery
        # or auth_type mutation, so oauth+stdio is unreachable here (matches the
        # registration serializer's transport guard) and the auth_type="oauth"
        # persisted below can never land on a non-HTTP row.
        oauth_transport_err = oauth_http_transport_error(server)
        if oauth_transport_err:
            return Response(
                {"error": oauth_transport_err},
                status=status.HTTP_400_BAD_REQUEST,
            )

        redirect_uri = _oauth_redirect_uri()
        try:
            meta = oauth_mod.discover(server.url)
        except oauth_mod.OAuthDiscoveryError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        except Exception as exc:  # noqa: BLE001
            logger.warning("OAuth discovery error for %s: %s", server.server_slug, exc)
            return Response({"error": f"OAuth discovery failed: {exc}"}, status=status.HTTP_502_BAD_GATEWAY)

        # Reuse an existing registered client_id when present (idempotent
        # re-auth); otherwise register a fresh public client via DCR.
        client_id = server.oauth_client_id
        client_secret = server.oauth_client_secret
        if not client_id:
            if not meta.get("registration_endpoint"):
                return Response(
                    {
                        "error": (
                            "Server does not advertise dynamic client registration. "
                            "Provide a client_id/secret manually."
                        )
                    },
                    status=status.HTTP_502_BAD_GATEWAY,
                )
            try:
                reg = oauth_mod.register_client(
                    meta["registration_endpoint"],
                    redirect_uri,
                    client_name=f"AI Mesh Firewall ({server.name})",
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("DCR failed for %s: %s", server.server_slug, exc)
                return Response({"error": f"Client registration failed: {exc}"}, status=status.HTTP_502_BAD_GATEWAY)
            client_id = reg.get("client_id", "")
            client_secret = reg.get("client_secret", "") or ""
            if not client_id:
                return Response({"error": "Registration returned no client_id."}, status=status.HTTP_502_BAD_GATEWAY)

        verifier, challenge = oauth_mod.generate_pkce()
        state = oauth_mod.generate_state()
        # Prefer the server's configured scope; else the AS-advertised scopes.
        scope = server.oauth_scope or " ".join(meta.get("scopes_supported") or [])

        server.oauth_authorization_endpoint = meta["authorization_endpoint"]
        server.oauth_token_endpoint = meta["token_endpoint"]
        server.oauth_registration_endpoint = meta.get("registration_endpoint", "")
        server.oauth_client_id = client_id
        server.oauth_client_secret = client_secret
        server.oauth_scope = scope
        server.oauth_resource = meta["resource"]
        server.oauth_code_verifier = verifier
        server.oauth_state = state
        server.auth_type = "oauth"
        server.save(update_fields=[
            "oauth_authorization_endpoint",
            "oauth_token_endpoint",
            "oauth_registration_endpoint",
            "oauth_client_id",
            "oauth_client_secret",
            "oauth_scope",
            "oauth_resource",
            "oauth_code_verifier",
            "oauth_state",
            "auth_type",
            "updated_at",
        ])

        authorize_url = oauth_mod.build_authorize_url(
            meta["authorization_endpoint"],
            client_id,
            redirect_uri,
            challenge,
            state,
            scope,
            meta["resource"],
        )
        return Response({"authorize_url": authorize_url, "resource": meta["resource"]})


class MCPOAuthCallbackView(APIView):
    """OAuth redirect target. The browser lands here after user consent.

    GET /oauth/callback/?code=...&state=... → match the server by ``state``,
    exchange the code for tokens, store them (encrypted), clear the transient
    PKCE/state, and bounce the browser back to the frontend.

    Public endpoint (no JWT): the authorization server redirects the browser
    here with no Authorization header. CSRF protection is provided by the
    high-entropy, single-use ``state`` value.
    """

    permission_classes = [AllowAny]
    authentication_classes: list = []

    def _html(self, ok: bool, message: str, server_name: str = "") -> HttpResponse:
        # B3: this hand-built HTML bypasses Django template auto-escaping, and
        # `message` (and server_name via return_url) are attacker-controlled on an
        # unauthenticated callback (request.GET error/error_description), so a raw
        # f-string interpolation is a reflected/stored XSS that — with JWT in
        # localStorage on the single prod origin — exfiltrates the session. Escape
        # every dynamic value; only allow http(s) return URLs (no javascript:).
        from django.utils.html import escape

        return_url = _oauth_frontend_return_url(ok, server_name, message)
        if not isinstance(return_url, str) or not return_url.lower().startswith(("http://", "https://", "/")):
            return_url = "/"
        status_word = "succeeded" if ok else "failed"
        color = "#16a34a" if ok else "#dc2626"
        _msg = escape(str(message or ""))
        _url = escape(return_url)
        body = f"""<!doctype html><html><head><meta charset="utf-8">
<title>MCP OAuth {status_word}</title>
<meta http-equiv="refresh" content="3;url={_url}">
<style>body{{font-family:system-ui,sans-serif;background:#0b1020;color:#e5e7eb;
display:flex;align-items:center;justify-content:center;height:100vh;margin:0}}
.card{{background:#111827;padding:32px 40px;border-radius:12px;max-width:520px;
box-shadow:0 10px 40px rgba(0,0,0,.4);border:1px solid #1f2937}}
h1{{color:{color};margin:0 0 12px;font-size:20px}}
p{{margin:6px 0;line-height:1.5}} a{{color:#60a5fa}}</style></head>
<body><div class="card"><h1>Authorization {status_word}</h1>
<p>{_msg}</p>
<p>Returning to the dashboard… <a href="{_url}">click here</a> if you are not redirected.</p>
</div></body></html>"""
        resp = HttpResponse(body, content_type="text/html")
        resp["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'"
        resp["X-Content-Type-Options"] = "nosniff"
        return resp

    def get(self, request):
        from . import oauth as oauth_mod

        error = request.GET.get("error")
        error_desc = request.GET.get("error_description", "")
        code = request.GET.get("code", "")
        state = request.GET.get("state", "")

        if error:
            return self._html(False, f"Authorization server returned: {error} {error_desc}".strip())
        if not code or not state:
            return self._html(False, "Missing authorization code or state in callback.")

        try:
            server = MCPServerRegistration.objects.get(oauth_state=state)
        except MCPServerRegistration.DoesNotExist:
            return self._html(False, "Unknown or expired authorization state (possible CSRF).")
        except MCPServerRegistration.MultipleObjectsReturned:
            return self._html(False, "Ambiguous authorization state.")

        redirect_uri = _oauth_redirect_uri()
        try:
            tok = oauth_mod.exchange_code(
                server.oauth_token_endpoint,
                code,
                redirect_uri,
                server.oauth_client_id,
                server.oauth_client_secret,
                server.oauth_code_verifier,
                server.oauth_resource,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("OAuth code exchange failed for %s: %s", server.server_slug, exc)
            # Clear single-use state even on failure so it can't be replayed.
            server.oauth_state = ""
            server.oauth_code_verifier = ""
            server.save(update_fields=["oauth_state", "oauth_code_verifier", "updated_at"])
            return self._html(False, f"Token exchange failed: {exc}", server.name)

        _store_oauth_tokens(server, tok)
        # Clear transient single-use flow state.
        server.oauth_state = ""
        server.oauth_code_verifier = ""
        server.save(update_fields=["oauth_state", "oauth_code_verifier", "updated_at"])
        _clear_needs_reauth(server)
        if hasattr(server, "last_sync_error"):
            server.last_sync_error = ""
            server.save(update_fields=["last_sync_error", "updated_at"])
        org = getattr(server, "organization", None)
        if org is not None:
            _trigger_background_sync(server, org)

        return self._html(
            True,
            f"“{server.name}” is now authorized. You can sync its tools.",
            server.name,
        )

