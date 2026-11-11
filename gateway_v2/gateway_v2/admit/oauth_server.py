"""GW32: the OAuth 2.1 authorization server for MCP clients (Requirements 6 and 7).

A state machine over the narrow :class:`~gateway_v2.admit.oauth_store.KV`. It returns frozen values
and :class:`OAuthError` codes; ``edge/oauth_routes.py`` renders them as HTTP. No HTTP object here.

Everything secret is stored only as ``HMAC-SHA256(oauth_hash_key, value)``: codes, access and
refresh tokens, form tokens, client secrets and request sources. ``AMF_OAUTH_HASH_KEY`` is the
current key and an optional previous key is tried second on lookup, so one rotation keeps live
codes and tokens valid, a second invalidates them, and removing both is the emergency revoke.

A token never carries or caches a ``Principal`` (R7.2): it maps to the API key's hash, and the
identity cache is consulted on every request, so key revocation, deactivation and plan edits bind
a token exactly as they bind the key it was minted from.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

from gateway_v2.admit.oauth_store import KV
from gateway_v2.runtime.resources_mcp import McpTransportBounds
from gateway_v2.runtime.store_keys_mcp import McpStoreKeys

__all__ = (
    "AuthorizeRequest",
    "OAuthConfig",
    "OAuthError",
    "OAuthServer",
    "OAuthTokenResolver",
    "TokenGrant",
)

TOKEN_PREFIX = "zsmcp_"
_CHALLENGE = re.compile(r"^[A-Za-z0-9_\-.~]{43,128}$")
_VERIFIER = re.compile(r"^[A-Za-z0-9_\-.~]{43,128}$")
_LOOPBACK = frozenset({"127.0.0.1", "[::1]", "::1", "localhost"})
_SCHEMES = frozenset({"vscode", "vscode-insiders", "cursor"})
_DUMMY = "dummy-comparison-value"
_ONE_USE_SUFFIX = "used"


@dataclass(frozen=True, slots=True)
class OAuthError:
    """An RFC 6749 error. ``code`` is from a fixed table in the routes; never an input echo."""

    code: str
    status: int = 400
    retry_after_s: float = 0.0


@dataclass(frozen=True, slots=True)
class OAuthConfig:
    public_url: str
    hash_key: bytes
    previous_key: bytes | None
    bounds: McpTransportBounds
    trusted_proxy_hops: int = 0

    def __post_init__(self) -> None:
        if len(self.hash_key) < 32:
            raise ValueError("the OAuth hash key must be at least 32 bytes")
        if not _public_url_ok(self.public_url):
            raise ValueError("the public URL must be https (or loopback http)")


def _public_url_ok(url: str) -> bool:
    parts = urlsplit(url)
    host = parts.hostname or ""
    return bool(host) and (
        parts.scheme == "https" or (parts.scheme == "http" and host in _LOOPBACK)
    )


@dataclass(frozen=True, slots=True)
class AuthorizeRequest:
    client_id: str
    redirect_uri: str
    code_challenge: str
    state: str | None


@dataclass(frozen=True, slots=True)
class TokenGrant:
    access_token: str
    refresh_token: str
    expires_in: int


def redirect_uri_ok(uri: str) -> bool:
    """https, loopback http, or a known app scheme; no fragment, userinfo or wildcard."""
    if "*" in uri or any(ch.isspace() for ch in uri):
        return False
    try:
        parts = urlsplit(uri)
        host = parts.hostname or ""
        has_user = parts.username is not None or parts.password is not None
    except ValueError:
        return False
    if parts.fragment or has_user or not parts.scheme:
        return False
    if parts.scheme == "https":
        return bool(host)
    if parts.scheme == "http":
        return host in _LOOPBACK
    return parts.scheme in _SCHEMES


def s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


class OAuthServer:
    """One instance per worker; all shared state is in the injected KV."""

    def __init__(
        self,
        *,
        kv: KV,
        keys: McpStoreKeys,
        config: OAuthConfig,
        clock: Callable[[], float],
        audit: Callable[[str, Mapping[str, str]], None] | None = None,
    ) -> None:
        self._kv, self._keys, self._cfg, self._clock = kv, keys, config, clock
        self._audit = audit

    # -- hashing ----------------------------------------------------------------------------------
    def _mac(self, value: str, key: bytes | None = None) -> str:
        return hmac.new(key or self._cfg.hash_key, value.encode(), hashlib.sha256).hexdigest()

    def _candidates(self, value: str) -> list[str]:
        out = [self._mac(value)]
        if self._cfg.previous_key is not None:
            out.append(self._mac(value, self._cfg.previous_key))
        return out

    def source_of(self, scope: Mapping[str, object]) -> str:
        """HMAC of the peer address. ``X-Forwarded-For`` counts only with trusted hops > 0."""
        address = ""
        hops = self._cfg.trusted_proxy_hops
        headers = scope.get("headers")
        if hops > 0 and isinstance(headers, (list, tuple)):
            for item in headers:
                if (
                    isinstance(item, (list, tuple))
                    and len(item) == 2
                    and item[0] == b"x-forwarded-for"
                ):
                    parts = [p.strip() for p in bytes(item[1]).decode("latin-1").split(",")]
                    if len(parts) >= hops:
                        address = parts[-hops]
        client = scope.get("client")
        if not address and isinstance(client, (list, tuple)) and client:
            address = str(client[0])
        return self._mac(f"src:{address}")

    def _emit(self, event: str, **detail: str) -> None:
        if self._audit is not None:
            self._audit(event, detail)  # detail never carries a code, token, verifier or key

    # -- registration (R6.4) ----------------------------------------------------------------------
    async def register(
        self, body: Mapping[str, object], source: str
    ) -> dict[str, object] | OAuthError:
        bounds = self._cfg.bounds
        uris = body.get("redirect_uris")
        if (
            not isinstance(uris, list)
            or not 1 <= len(uris) <= bounds.oauth_redirects_max
            or not all(isinstance(u, str) and redirect_uri_ok(u) for u in uris)
        ):
            return OAuthError("invalid_request")
        method = body.get("token_endpoint_auth_method", "none")
        if method not in ("none", "client_secret_post"):
            return OAuthError("invalid_request")
        per_source = self._keys.oauth_clients_by_source(source)
        if await self._live_count(per_source) >= bounds.oauth_clients_per_source:
            return OAuthError("temporarily_unavailable", 429, bounds.oauth_fail_window_s)
        if await self._live_count(self._keys.oauth_clients_index()) >= bounds.oauth_clients_total:
            return OAuthError("temporarily_unavailable", 429, bounds.oauth_fail_window_s)
        client_id = secrets.token_urlsafe(16)
        record: dict[str, object] = {
            "redirect_uris": list(uris),
            "method": method,
            "used": False,
            "source": source,
        }
        secret: str | None = None
        if method == "client_secret_post":
            secret = secrets.token_urlsafe(32)
            record["secret_hash"] = self._mac(secret)
        ttl = bounds.oauth_client_unused_ttl_s
        await self._kv.put(self._keys.oauth_client(client_id), record, ttl)
        expires = self._clock() + ttl
        await self._kv.zadd(per_source, client_id, expires, bounds.oauth_client_ttl_s)
        index = self._keys.oauth_clients_index()
        await self._kv.zadd(index, client_id, expires, bounds.oauth_client_ttl_s)
        self._emit("oauth_register", client_id=client_id)
        reply: dict[str, object] = {
            "client_id": client_id,
            "redirect_uris": list(uris),
            "token_endpoint_auth_method": method,
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
        }
        if secret is not None:
            reply["client_secret"] = secret  # shown once
        return reply

    async def _live_count(self, set_key: str) -> int:
        """Count LIVE clients: expired members are pruned (by score) before counting."""
        return await self._kv.zlive(set_key, self._clock())

    # -- authorize (R6.5, R6.6) -------------------------------------------------------------------
    async def validate_authorize(self, params: Mapping[str, str]) -> AuthorizeRequest | OAuthError:
        """``OAuthError`` with ``status 400`` and code ``invalid_client`` means the redirect URI
        is untrusted: render a local error page, never redirect."""
        client = await self._kv.get(self._keys.oauth_client(params.get("client_id", "-")))
        uri = params.get("redirect_uri", "")
        registered = client.get("redirect_uris") if client else None
        if client is None or not isinstance(registered, list) or uri not in registered:
            return OAuthError("invalid_client")
        challenge = params.get("code_challenge", "")
        if params.get("response_type") != "code":
            return OAuthError("unsupported_response_type")
        if params.get("code_challenge_method") != "S256" or not _CHALLENGE.match(challenge):
            return OAuthError("invalid_request")
        return AuthorizeRequest(params["client_id"], uri, challenge, params.get("state"))

    async def issue_form_token(self, request: AuthorizeRequest) -> str:
        token = secrets.token_urlsafe(32)
        await self._kv.put(
            self._keys.oauth_form(self._mac(token)),
            {
                "c": request.client_id,
                "r": request.redirect_uri,
                "h": request.code_challenge,
                "s": request.state,
            },
            self._cfg.bounds.oauth_form_ttl_s,
        )
        return token

    async def authorize(
        self,
        form_token: str,
        resolve_key: Callable[[str], Awaitable[tuple[str, str] | None]],
        raw_key: str,
        source: str,
    ) -> tuple[AuthorizeRequest, str] | OAuthError:
        """Consume the form token, check the API key, mint a single-use code.

        ``resolve_key`` is an async callable ``raw_key -> (key_hash, key_id) | None``. A constant-
        cost dummy comparison runs for every attempt so the timing class does not reveal whether
        the key shape was acceptable.
        """
        bounds = self._cfg.bounds
        fail_key = self._keys.oauth_failures(source)
        if await self._kv.peek(fail_key) >= bounds.oauth_fail_max:
            return OAuthError("temporarily_unavailable", 429, bounds.oauth_fail_window_s)
        form = await self._take(self._keys.oauth_form, form_token)
        hmac.compare_digest(self._mac(raw_key), self._mac(_DUMMY))
        resolved = await resolve_key(raw_key) if form is not None else None
        if form is None or resolved is None:
            await self._kv.incr(fail_key, bounds.oauth_fail_window_s)
            self._emit("oauth_authorize_denied", source=source[:12])
            return OAuthError("access_denied", 403)
        key_hash, key_id = resolved
        code = secrets.token_urlsafe(32)
        family = secrets.token_urlsafe(16)
        await self._kv.put(
            self._keys.oauth_code(self._mac(code)),
            {
                "c": form["c"],
                "r": form["r"],
                "h": form["h"],
                "k": key_hash,
                "i": key_id,
                "f": family,
            },
            bounds.oauth_code_ttl_s,
        )
        self._emit("oauth_authorize_ok", client_id=str(form["c"]), key_id=str(key_id))
        state = form.get("s")
        request = AuthorizeRequest(
            str(form["c"]),
            str(form["r"]),
            str(form["h"]),
            state if isinstance(state, str) else None,
        )
        return request, code

    # -- token (R6.7, R6.8, R6.9) -----------------------------------------------------------------
    async def token(self, params: Mapping[str, str]) -> TokenGrant | OAuthError:
        grant = params.get("grant_type")
        client = await self._authenticate_client(params)
        if isinstance(client, OAuthError):
            return client
        if grant == "authorization_code":
            out = await self._exchange_code(params, client)
        elif grant == "refresh_token":
            out = await self._refresh(params, client)
        else:
            return OAuthError("unsupported_grant_type")
        self._emit(
            "oauth_token_issued" if isinstance(out, TokenGrant) else "oauth_token_refused",
            client_id=params.get("client_id", ""),
        )
        return out

    async def _authenticate_client(
        self, params: Mapping[str, str]
    ) -> dict[str, object] | OAuthError:
        client_id = params.get("client_id", "")
        client = await self._kv.get(self._keys.oauth_client(client_id)) if client_id else None
        if client is None:
            hmac.compare_digest(self._mac(params.get("client_secret", "")), self._mac(_DUMMY))
            return OAuthError("invalid_client", 401)
        if client.get("method") == "client_secret_post":
            presented = self._mac(params.get("client_secret", ""))
            if not hmac.compare_digest(presented, str(client.get("secret_hash", ""))):
                return OAuthError("invalid_client", 401)
        client["id"] = client_id
        return client

    async def _take(self, builder: Callable[[str], str], value: str) -> dict[str, object] | None:
        """Single-use consume under the current key, then the previous key (rotation)."""
        for mac in self._candidates(value):
            record = await self._kv.take(builder(mac))
            if record is not None:
                return record
        return None

    async def _touch_client(self, client: Mapping[str, object]) -> None:
        record = {k: v for k, v in client.items() if k != "id"}
        record["used"] = True
        bounds = self._cfg.bounds
        client_id = str(client["id"])
        await self._kv.put(self._keys.oauth_client(client_id), record, bounds.oauth_client_ttl_s)
        expires = self._clock() + bounds.oauth_client_ttl_s
        for index in (
            self._keys.oauth_clients_by_source(str(record["source"])),
            self._keys.oauth_clients_index(),
        ):
            await self._kv.zadd(index, client_id, expires, bounds.oauth_client_ttl_s)

    async def _exchange_code(
        self, params: Mapping[str, str], client: dict[str, object]
    ) -> TokenGrant | OAuthError:
        code = params.get("code", "")
        mac = self._mac(code)
        record = await self._take(self._keys.oauth_code, code)
        used_key = self._keys.oauth_code(f"{_ONE_USE_SUFFIX}:{mac}")
        if record is None:
            earlier = await self._kv.get(used_key)
            if earlier is not None:  # replay: revoke everything that code produced
                await self._revoke_family(str(earlier["f"]))
            return OAuthError("invalid_grant")
        await self._kv.put(used_key, {"f": record["f"]}, self._cfg.bounds.oauth_refresh_ttl_s)
        verifier = params.get("code_verifier", "")
        ok = (
            record["c"] == client["id"]
            and record["r"] == params.get("redirect_uri")
            and _VERIFIER.match(verifier) is not None
            and hmac.compare_digest(s256(verifier), str(record["h"]))
        )
        if not ok:
            return OAuthError("invalid_grant")
        await self._touch_client(client)
        return await self._mint(record["k"], record["i"], str(client["id"]), str(record["f"]))

    async def _refresh(
        self, params: Mapping[str, str], client: dict[str, object]
    ) -> TokenGrant | OAuthError:
        mac = self._mac(params.get("refresh_token", ""))
        record = await self._take(self._keys.oauth_refresh, params.get("refresh_token", ""))
        used_key = self._keys.oauth_refresh(f"{_ONE_USE_SUFFIX}:{mac}")
        if record is None:
            earlier = await self._kv.get(used_key)
            if earlier is not None:  # a rotated token presented again: the family is compromised
                await self._revoke_family(str(earlier["f"]))
            return OAuthError("invalid_grant")
        await self._kv.put(used_key, {"f": record["f"]}, self._cfg.bounds.oauth_refresh_ttl_s)
        if record["c"] != client["id"] or await self._family_revoked(record):
            return OAuthError("invalid_grant")
        await self._touch_client(client)
        return await self._mint(record["k"], record["i"], str(client["id"]), str(record["f"]))

    async def _mint(
        self, key_hash: object, key_id: object, client_id: str, family: str
    ) -> TokenGrant:
        bounds = self._cfg.bounds
        access = TOKEN_PREFIX + secrets.token_urlsafe(32)
        refresh = secrets.token_urlsafe(32)
        base = {"k": key_hash, "i": key_id, "c": client_id, "f": family}
        at_key = self._keys.oauth_access(self._mac(access))
        rt_key = self._keys.oauth_refresh(self._mac(refresh))
        await self._kv.put(at_key, base, bounds.oauth_access_ttl_s)
        await self._kv.put(rt_key, base, bounds.oauth_refresh_ttl_s)
        return TokenGrant(access, refresh, round(bounds.oauth_access_ttl_s))

    async def _revoke_family(self, family: str) -> None:
        """Revocation is a marker, not an enumeration: every token record names its family and is
        refused while the marker lives (O(1); it outlives the longest token, so nothing revives)."""
        await self._kv.put(
            self._keys.oauth_family(family), {"revoked": True}, self._cfg.bounds.oauth_refresh_ttl_s
        )

    async def _family_revoked(self, record: Mapping[str, object]) -> bool:
        return await self._kv.get(self._keys.oauth_family(str(record.get("f", "")))) is not None

    # -- resolution on MCP routes (R7) ------------------------------------------------------------
    async def lookup_access(self, token: str) -> str | None:
        """The API key hash a live access token maps to, or ``None``."""
        if not token.startswith(TOKEN_PREFIX):
            return None
        for mac in self._candidates(token):
            record = await self._kv.get(self._keys.oauth_access(mac))
            if record is not None and isinstance(record.get("k"), str):
                return None if await self._family_revoked(record) else str(record["k"])
        return None


class OAuthTokenResolver:
    """The ``TokenResolver`` for MCP routes: a bounded per-worker RAM table in front of the store.

    Family revocation lags at most ``token_cache_ttl_s`` (<= ``FRESH_MS``). Instance-owned.
    """

    def __init__(self, server: OAuthServer, bounds: McpTransportBounds, clock: Callable[[], float]):
        self._server, self._bounds, self._clock = server, bounds, clock
        self._ram: dict[str, tuple[float, str]] = {}

    async def resolve(self, token: str) -> str | None:
        now = self._clock()
        mac = self._server._mac(token)  # noqa: SLF001
        hit = self._ram.get(mac)
        if hit is not None and hit[0] > now:
            return hit[1]
        self._ram.pop(mac, None)
        key_hash = await self._server.lookup_access(token)
        if key_hash is not None:
            if len(self._ram) >= self._bounds.token_cache_slots:
                self._ram.pop(min(self._ram, key=lambda k: self._ram[k][0]))
            self._ram[mac] = (now + self._bounds.token_cache_ttl_s, key_hash)
        return key_hash
