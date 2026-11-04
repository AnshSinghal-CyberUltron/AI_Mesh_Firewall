"""GW17b: rendering, fetching, caching and refreshing an upstream MCP credential.

Credentials stay in control. The gateway fetches ONE server's credential per use through an
internal endpoint, keeps it in a per-worker RAM cache keyed ``(org, slug)``, renders it into the
broker body's ``upstream.headers`` only, and never hands it to a type that logs, audits, scans or
serialises it. Every failure is a declared value-code; no message is ever built from a secret, a
response body or an exception text.

`render` is pure. The control client, the lock and the token endpoint are injected, so every path
here runs offline.
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
import secrets
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urlparse

from gateway_v2.domain import posture
from gateway_v2.domain.mcp_credential import (
    KINDS,
    CredentialRefusal,
    RenderedHeaders,
    UpstreamCredential,
)
from gateway_v2.runtime.resources_mcp import McpCredentialBounds

__all__ = (
    "ControlCredentialClient",
    "CredentialCache",
    "CredentialFetch",
    "CredentialService",
    "RefreshLock",
    "RefreshMaterial",
    "render",
)

_TOKEN_CHARS = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_DENY_HEADERS = frozenset(
    {
        "host", "content-length", "content-type", "transfer-encoding", "connection", "upgrade",
        "x-request-id", "x-mcp-broker-key", "cookie", "keep-alive", "proxy-authorization", "te",
        "trailer",
    }
)
_OK = 200
_NOT_FOUND = 404
_CONFLICT = 409
_REDACTED = "<redacted>"
_PERMANENT_OAUTH_ERRORS = frozenset({"invalid_grant", "invalid_client", "unauthorized_client"})
_UNAVAILABLE = CredentialRefusal(posture.MCP_UPSTREAM_CREDENTIAL_UNAVAILABLE)
_INVALID = CredentialRefusal(posture.MCP_UPSTREAM_CREDENTIAL_INVALID)
_REAUTH = CredentialRefusal(posture.MCP_UPSTREAM_NEEDS_REAUTH)


def _printable_ascii(value: str) -> bool:
    return all(0x20 <= ord(ch) <= 0x7E for ch in value)


def render(
    cred: UpstreamCredential, bounds: McpCredentialBounds
) -> RenderedHeaders | CredentialRefusal:
    """Credential -> upstream headers, or a refusal. Pure."""
    if cred.kind not in KINDS or len(cred.token.strip()) < bounds.credential_min_len:
        return _INVALID
    token = cred.token.strip()
    if cred.kind in ("bearer", "oauth"):
        pair = ("Authorization", f"Bearer {token}")
    elif cred.kind == "basic":
        if not cred.username or ":" in cred.username:
            return _INVALID
        raw = f"{cred.username}:{token}".encode()
        pair = ("Authorization", "Basic " + base64.b64encode(raw).decode("ascii"))
    else:
        name = cred.header_name.strip()
        if not _valid_header_name(name, bounds):
            return _INVALID
        pair = (name, token)
    if len(pair[1]) > bounds.header_value_max or not _printable_ascii(pair[1]):
        return _INVALID
    return RenderedHeaders((pair,))


def _valid_header_name(name: str, bounds: McpCredentialBounds) -> bool:
    return (
        0 < len(name) <= bounds.header_name_max
        and _TOKEN_CHARS.fullmatch(name) is not None
        and name.lower() not in _DENY_HEADERS
    )


@dataclass(frozen=True, slots=True)
class RefreshMaterial:
    """What a refresh needs. Returned by control only when the gateway asks (`need_refresh=1`)."""

    refresh_token: str = field(repr=False)
    token_endpoint: str
    client_id: str = field(default="", repr=False)
    client_secret: str = field(default="", repr=False)

    def __repr__(self) -> str:
        return _REDACTED

    __str__ = __repr__


@dataclass(frozen=True, slots=True)
class CredentialFetch:
    """One control answer: the credential, and refresh material when it was asked for."""

    credential: UpstreamCredential
    refresh: RefreshMaterial | None = None

    def __repr__(self) -> str:
        return _REDACTED

    __str__ = __repr__


class ControlCall(Protocol):
    """``(method, url, headers, query, json_body, timeout_s, max_bytes) -> (status, body)``."""

    def __call__(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        query: Mapping[str, str],
        body: Mapping[str, object] | None,
        timeout_s: float,
        max_bytes: int,
    ) -> Awaitable[tuple[int, bytes]]: ...


class ControlCredentialClient:
    """Talks to control's credential endpoints. Value-codes only, never an exception text."""

    __slots__ = ("_base", "_bounds", "_call", "_key")

    def __init__(
        self, *, base_url: str, internal_key: str, call: ControlCall, bounds: McpCredentialBounds
    ) -> None:
        if not base_url or not internal_key:
            raise ValueError("the credential client needs a control URL and an internal key")
        _require_safe_base(base_url)
        self._base = base_url.rstrip("/")
        self._key = internal_key
        self._call = call
        self._bounds = bounds

    def _headers(self, org: str) -> dict[str, str]:
        return {"X-Gateway-Auth": "true", "X-Gateway-Internal-Key": self._key, "X-Org-Slug": org}

    async def fetch(
        self, org: str, slug: str, *, need_refresh: bool = False
    ) -> CredentialFetch | CredentialRefusal | None:
        """``None`` = control has no such server for this org (indistinguishable from unowned)."""
        query = {"server_slug": slug, **({"need_refresh": "1"} if need_refresh else {})}
        reply = await self._request("GET", "/api/mcp-connector/internal/credential/", org, query)
        if reply is None:
            return None
        if isinstance(reply, CredentialRefusal):
            return reply
        return _parse_fetch(reply)

    async def write_back(
        self, org: str, slug: str, *, version: int, access_token: str, expires_in: int,
        refresh_token: str | None,
    ) -> bool:
        """Persist refreshed tokens. ``False`` on a version conflict or any failure."""
        body: dict[str, object] = {
            "server_slug": slug, "version": version, "access_token": access_token,
            "expires_in": expires_in,
        }
        if refresh_token is not None:
            body["refresh_token"] = refresh_token
        reply = await self._request(
            "POST", "/api/mcp-connector/internal/credential/refresh-result/", org, {}, body
        )
        return isinstance(reply, dict)

    async def mark_needs_reauth(self, org: str, slug: str) -> None:
        """Flag the server. The reason is fixed text, never the upstream body."""
        await self._request(
            "POST", "/api/mcp-connector/internal/needs-reauth/", org, {},
            {"org_slug": org, "server_slug": slug, "reason": "OAuth refresh failed"},
        )

    async def _request(
        self, method: str, path: str, org: str, query: Mapping[str, str],
        body: Mapping[str, object] | None = None,
    ) -> dict[str, object] | CredentialRefusal | None:
        try:
            status, raw = await self._call(
                method, self._base + path, self._headers(org), query, body,
                self._bounds.refresh_timeout_s, self._bounds.credential_response_bytes,
            )
        except Exception:  # noqa: BLE001 - control unreachable is a declared refusal
            return _UNAVAILABLE
        if status == _NOT_FOUND:
            return None
        if status != _OK:
            return _UNAVAILABLE
        if len(raw) > self._bounds.credential_response_bytes:
            return _INVALID
        try:
            decoded = json.loads(raw)
        except (UnicodeDecodeError, ValueError):
            return _INVALID
        return decoded if isinstance(decoded, dict) else _INVALID


def _require_safe_base(base_url: str) -> None:
    parsed = urlparse(base_url)
    host = parsed.hostname or ""
    if parsed.scheme == "https" or host in ("localhost", "127.0.0.1", "::1"):
        return
    raise ValueError("the control credential URL must be https or loopback")


def _parse_fetch(body: Mapping[str, object]) -> CredentialFetch | CredentialRefusal:
    kind = body.get("auth_type")
    token = body.get("token")
    if kind not in KINDS or not isinstance(token, str):
        return _INVALID
    expires = body.get("expires_at")
    version = body.get("version", 0)
    if isinstance(version, bool) or not isinstance(version, int):
        return _INVALID
    if expires is not None and (isinstance(expires, bool) or not isinstance(expires, (int, float))):
        return _INVALID
    cred = UpstreamCredential(
        kind=str(kind),
        token=token,
        username=str(body.get("username") or ""),
        header_name=str(body.get("header_name") or ""),
        expires_at=None if expires is None else float(expires),
        refreshable=body.get("refreshable") is True,
        version=version,
    )
    refresh_token = body.get("refresh_token")
    endpoint = body.get("token_endpoint")
    if isinstance(refresh_token, str) and isinstance(endpoint, str) and refresh_token and endpoint:
        material = RefreshMaterial(
            refresh_token, endpoint, str(body.get("client_id") or ""),
            str(body.get("client_secret") or ""),
        )
        return CredentialFetch(cred, material)
    return CredentialFetch(cred)


class CredentialCache:
    """Per-worker RAM cache, instance-owned. Bounded; TTL = the registry TTL; no ``lru_cache``.

    An entry records the ``auth_type`` it was fetched under; a different type bypasses and
    drops it (R14.9). ``drop`` is what the registry's version poll calls.
    """

    __slots__ = ("_clock", "_entries", "_slots", "_ttl")

    def __init__(self, *, slots: int, ttl_s: float, clock: Callable[[], float]) -> None:
        if slots < 1 or ttl_s <= 0:
            raise ValueError("the credential cache needs positive bounds")
        self._slots, self._ttl, self._clock = slots, ttl_s, clock
        self._entries: dict[tuple[str, str], tuple[float, str, UpstreamCredential]] = {}

    def get(self, org: str, slug: str, auth_type: str) -> UpstreamCredential | None:
        entry = self._entries.get((org, slug))
        if entry is None:
            return None
        fetched_at, cached_type, cred = entry
        if cached_type != auth_type or self._clock() - fetched_at >= self._ttl:
            self._entries.pop((org, slug), None)
            return None
        return cred

    def put(self, org: str, slug: str, auth_type: str, cred: UpstreamCredential) -> None:
        if (org, slug) not in self._entries and len(self._entries) >= self._slots:
            oldest = min(self._entries, key=lambda key: self._entries[key][0])
            self._entries.pop(oldest, None)
        self._entries[(org, slug)] = (self._clock(), auth_type, cred)

    def drop(self, org: str, slug: str) -> None:
        self._entries.pop((org, slug), None)

    def drop_scope(self, org: str, server: str) -> None:
        """Drop every entry of ``org`` (``server == ""``) or the one ``(org, server)`` entry. The
        walk is over this worker's bounded RAM table (``slots`` entries), never the store."""
        for cached in [key for key in self._entries if key[0] == org and server in ("", key[1])]:
            self._entries.pop(cached, None)

    def scopes(self) -> frozenset[tuple[str, str]]:
        """The ``(org, server)`` pairs held (at most ``slots``), for the registry version poll."""
        return frozenset(self._entries)


class RefreshLock(Protocol):
    """Cross-worker lock (``SET NX PX``). ``release`` is compare-and-delete on the holder token."""

    async def acquire(self, org: str, slug: str, holder: str, ttl_s: float) -> bool: ...

    async def release(self, org: str, slug: str, holder: str) -> None: ...


class TokenEndpoint(Protocol):
    """One token-endpoint call (pinned dial, no redirects). Returns ``(status, body)``."""

    def __call__(
        self, url: str, form: Mapping[str, str], timeout_s: float, max_bytes: int
    ) -> Awaitable[tuple[int, bytes]]: ...


Sleep = Callable[[float], Awaitable[None]]


class CredentialService:
    """``acquire`` = cache, fetch, and (for OAuth) refresh once under a cross-worker lock."""

    __slots__ = (
        "_bounds", "_cache", "_client", "_clock", "_failures", "_flights", "_lock", "_sleep",
        "_token_endpoint",
    )

    def __init__(
        self,
        *,
        client: ControlCredentialClient,
        cache: CredentialCache,
        lock: RefreshLock,
        token_endpoint: TokenEndpoint,
        bounds: McpCredentialBounds,
        clock: Callable[[], float],
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._client, self._cache, self._lock = client, cache, lock
        self._token_endpoint, self._bounds, self._clock, self._sleep = (
            token_endpoint, bounds, clock, sleep,
        )
        self._failures: dict[tuple[str, str], int] = {}
        self._flights: dict[
            tuple[str, str], asyncio.Future[UpstreamCredential | CredentialRefusal]
        ] = {}

    async def acquire(
        self, org: str, slug: str, auth_type: str
    ) -> UpstreamCredential | CredentialRefusal:
        """The usable credential for one call. Never raises; one refresh attempt at most."""
        cached = self._cache.get(org, slug, auth_type)
        if cached is not None and not self._expiring(cached):
            return cached
        key = (org, slug)
        flight = self._flights.get(key)
        if flight is not None:
            return await asyncio.shield(flight)  # in-worker single flight
        flight = asyncio.get_running_loop().create_future()
        self._flights[key] = flight
        try:
            result = await self._acquire_once(org, slug, auth_type)
        except BaseException:
            flight.cancel()
            raise
        finally:
            self._flights.pop(key, None)
        flight.set_result(result)
        return result

    def _expiring(self, cred: UpstreamCredential) -> bool:
        if cred.expires_at is None:
            return False
        return cred.expires_at - self._clock() <= self._bounds.refresh_skew_s

    async def _acquire_once(
        self, org: str, slug: str, auth_type: str
    ) -> UpstreamCredential | CredentialRefusal:
        try:
            async with asyncio.timeout(self._bounds.refresh_total_budget_s):
                return await self._fetch_and_refresh(org, slug, auth_type)
        except TimeoutError:
            return _UNAVAILABLE

    async def _fetch_and_refresh(
        self, org: str, slug: str, auth_type: str
    ) -> UpstreamCredential | CredentialRefusal:
        fetched = await self._client.fetch(org, slug)
        if fetched is None:
            return CredentialRefusal(posture.MCP_UPSTREAM_CREDENTIAL_UNAVAILABLE)
        if isinstance(fetched, CredentialRefusal):
            return fetched
        cred = fetched.credential
        if _kind_of(auth_type) != cred.kind:
            return _INVALID  # control and registry disagree: never attach under a changed type
        if cred.kind == "oauth" and self._expiring(cred):
            return await self._refresh(org, slug, auth_type, cred)
        self._cache.put(org, slug, auth_type, cred)
        return cred

    async def _refresh(
        self, org: str, slug: str, auth_type: str, stale: UpstreamCredential
    ) -> UpstreamCredential | CredentialRefusal:
        if not stale.refreshable:
            return await self._permanent(org, slug, stale.version)
        holder = secrets.token_urlsafe(16)
        budget = self._bounds.refresh_total_budget_s
        if not await self._lock.acquire(org, slug, holder, budget):
            return await self._wait_for_winner(org, slug, auth_type, stale.version)
        try:
            return await self._refresh_locked(org, slug, auth_type, stale.version)
        finally:
            await self._lock.release(org, slug, holder)

    async def _wait_for_winner(
        self, org: str, slug: str, auth_type: str, version: int
    ) -> UpstreamCredential | CredentialRefusal:
        waited = 0.0
        while waited < self._bounds.refresh_total_budget_s:
            await self._sleep(self._bounds.refresh_poll_s)
            waited += self._bounds.refresh_poll_s
            again = await self._client.fetch(org, slug)
            if isinstance(again, CredentialFetch) and again.credential.version > version:
                self._cache.put(org, slug, auth_type, again.credential)
                return again.credential
        return _UNAVAILABLE

    async def _refresh_locked(
        self, org: str, slug: str, auth_type: str, version: int
    ) -> UpstreamCredential | CredentialRefusal:
        full = await self._client.fetch(org, slug, need_refresh=True)
        if not isinstance(full, CredentialFetch):
            return full if isinstance(full, CredentialRefusal) else _UNAVAILABLE
        if full.credential.version > version and not self._expiring(full.credential):
            self._cache.put(org, slug, auth_type, full.credential)
            return full.credential  # another replica already refreshed
        if full.refresh is None:
            return await self._permanent(org, slug, full.credential.version)
        outcome = await self._call_endpoint(full.refresh)
        if outcome == "transient":
            return await self._transient(org, slug)
        if outcome == "permanent":
            return await self._permanent(org, slug, full.credential.version)
        assert isinstance(outcome, _Refreshed)
        return await self._store(org, slug, auth_type, full, outcome)

    async def _call_endpoint(self, material: RefreshMaterial) -> _Refreshed | str:
        form = {"grant_type": "refresh_token", "refresh_token": material.refresh_token}
        if material.client_id:
            form["client_id"] = material.client_id
        if material.client_secret:
            form["client_secret"] = material.client_secret
        try:
            status, raw = await self._token_endpoint(
                material.token_endpoint, form, self._bounds.refresh_timeout_s,
                self._bounds.refresh_response_bytes,
            )
        except Exception:  # noqa: BLE001 - timeout / connection error / refused destination
            return "transient"
        return _classify_token_reply(status, raw, self._bounds.refresh_response_bytes)

    async def _store(
        self, org: str, slug: str, auth_type: str, full: CredentialFetch, new: _Refreshed
    ) -> UpstreamCredential | CredentialRefusal:
        self._failures.pop((org, slug), None)
        ok = await self._client.write_back(
            org, slug, version=full.credential.version, access_token=new.access_token,
            expires_in=new.expires_in, refresh_token=new.refresh_token,
        )
        cred = UpstreamCredential(
            kind="oauth", token=new.access_token,
            expires_at=self._clock() + new.expires_in, refreshable=True,
            version=full.credential.version + (1 if ok else 0),
        )
        if ok:
            self._cache.put(org, slug, auth_type, cred)
            return cred
        if new.refresh_token is not None:
            await self._client.mark_needs_reauth(org, slug)  # the old refresh token may be dead
            self._cache.drop(org, slug)
        return cred  # usable once; not cached, so the next request refreshes again

    async def _permanent(
        self, org: str, slug: str, version: int
    ) -> UpstreamCredential | CredentialRefusal:
        del version
        self._cache.drop(org, slug)
        await self._client.mark_needs_reauth(org, slug)
        return _REAUTH

    async def _transient(self, org: str, slug: str) -> CredentialRefusal:
        count = self._failures.get((org, slug), 0) + 1
        self._failures[(org, slug)] = count
        if count >= self._bounds.needs_reauth_after_failures:
            self._failures.pop((org, slug), None)
            self._cache.drop(org, slug)
            await self._client.mark_needs_reauth(org, slug)
        return _UNAVAILABLE


@dataclass(frozen=True, slots=True)
class _Refreshed:
    access_token: str = field(repr=False)
    expires_in: int
    refresh_token: str | None = field(default=None, repr=False)


def _kind_of(auth_type: str) -> str:
    return "header" if auth_type == "authheaders" else auth_type


def _classify_token_reply(status: int, raw: bytes, max_bytes: int) -> _Refreshed | str:
    """A token-endpoint reply. 3xx and anything unparsable is a failure (never followed)."""
    if len(raw) > max_bytes:
        return "permanent"
    try:
        body = json.loads(raw)
    except (UnicodeDecodeError, ValueError):
        return "permanent" if status == _OK else "transient"
    if status != _OK:
        error = body.get("error") if isinstance(body, dict) else None
        if status < 500 and status != 429 and error in _PERMANENT_OAUTH_ERRORS:
            return "permanent"
        return "transient"
    if not isinstance(body, dict):
        return "permanent"
    access, expires = body.get("access_token"), body.get("expires_in")
    token_type = body.get("token_type")
    rotated = body.get("refresh_token")
    if (
        not isinstance(access, str) or not access
        or not isinstance(token_type, str) or token_type.lower() != "bearer"
        or isinstance(expires, bool) or not isinstance(expires, int) or expires <= 0
        or (rotated is not None and not isinstance(rotated, str))
    ):
        return "permanent"
    return _Refreshed(access, expires, rotated)
