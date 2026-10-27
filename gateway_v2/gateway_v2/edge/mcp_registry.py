"""MCP server registry from control, and the org map (GW17, tasks 5.1 and 5.2).

Control is the source of truth (``MCPServerRegistration``). The registry reads
``GET /api/mcp-connector/servers/`` and ``GET /api/mcp-connector/internal/enabled-tools/``
with the gateway internal-key headers v1 sends (``mcp_proxy.py:77-92``), and caches each
result in RAM for a bounded TTL. Any control failure is a declared refusal; it is never
cached and never read as an empty registry (v1 failed open here).

Changed ``mcp:scan_ver`` values drop cache entries, polled by a lifespan task off the
request path, so a request does no extra shared-state read.

Fail closed on missing fields: a server is usable only when control says it is active
and exposed, and a missing ``auth_type`` counts as needing credentials.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass

from gateway_v2.dispatch.mcp_broker import BROKER_SLUG

__all__ = (
    "ControlTransport",
    "McpRegistry",
    "OrgMap",
    "ServerEntry",
    "ToolSet",
)

_UNAVAILABLE = "mcp_registry_unavailable"
_NOT_FOUND = "mcp_server_not_found"
_NO_AUTH = "none"
_OK = 200
_SCAN_VER = "mcp:scan_ver"

#: ``(url, headers, query, timeout_s) -> (status, body)``. Raises on transport failure.
ControlTransport = Callable[
    [str, Mapping[str, str], Mapping[str, str], float], Awaitable[tuple[int, bytes]]
]
#: Read several Redis keys at once, ``None`` for an absent key.
Mget = Callable[[Sequence[str]], Awaitable[Sequence[object]]]


@dataclass(frozen=True, slots=True)
class ServerEntry:
    slug: str
    transport: str
    url: str
    command: str
    args: tuple[str, ...]
    env: Mapping[str, str]
    usable: bool
    needs_auth: bool


@dataclass(frozen=True, slots=True)
class ToolSet:
    known: frozenset[str]
    enabled: frozenset[str]
    disabled: frozenset[str]

    def allows(self, tool: str) -> bool:
        """Disabled or not in a non-empty known set is refused."""
        if tool in self.disabled:
            return False
        return not self.known or tool in self.known


class OrgMap:
    """Maps a v2 org id to control's org slug, which is also the broker slug."""

    __slots__ = ("_map",)

    def __init__(self, entries: Mapping[str, str]) -> None:
        for org, slug in entries.items():
            if not org or not BROKER_SLUG.match(slug):
                raise ValueError("invalid MCP org map entry")
        self._map = dict(entries)

    @classmethod
    def parse(cls, raw: str) -> OrgMap:
        """``v2org=controlslug,...``. A malformed entry refuses the whole map."""
        entries: dict[str, str] = {}
        for item in raw.split(","):
            if not item.strip():
                continue
            org, sep, slug = item.partition("=")
            if not sep:
                raise ValueError("invalid MCP org map entry")
            entries[org.strip()] = slug.strip()
        return cls(entries)

    def control_slug(self, org_id: str) -> str | None:
        return self._map.get(org_id)


class McpRegistry:
    """Per-(org, server) RAM cache over control. One instance per worker."""

    __slots__ = ("_base", "_key", "_servers", "_timeout", "_tools", "_transport", "_ttl",
                 "_clock", "_versions", "_mget", "_poll_s", "_poll")

    def __init__(
        self,
        *,
        base_url: str,
        internal_key: str,
        transport: ControlTransport,
        ttl_s: float,
        timeout_s: float,
        clock: Callable[[], float],
        mget: Mget | None = None,
        poll_s: float = 0.0,
    ) -> None:
        if not base_url or not internal_key:
            raise ValueError("the MCP registry needs a control URL and an internal key")
        self._base = base_url.rstrip("/")
        self._key = internal_key
        self._transport = transport
        self._ttl = ttl_s
        self._timeout = timeout_s
        self._clock = clock
        self._servers: dict[tuple[str, str], tuple[float, ServerEntry]] = {}
        self._tools: dict[tuple[str, str], tuple[float, ToolSet]] = {}
        self._versions: dict[str, object] = {}
        self._mget = mget
        self._poll_s = poll_s
        self._poll: asyncio.Task[None] | None = None

    def _ensure_poll(self) -> None:
        """Start the version poll on first use, inside the running loop, off the request path."""
        if self._mget is None or self._poll_s <= 0 or self._poll is not None:
            return
        self._poll = asyncio.get_running_loop().create_task(self.run_poll(self._mget, self._poll_s))

    async def server(self, org: str, server_slug: str) -> ServerEntry | str:
        self._ensure_poll()
        cached = self._fresh(self._servers.get((org, server_slug)))
        if cached is not None:
            return cached
        reply = await self._get("/api/mcp-connector/servers/", org, {}, {})
        if isinstance(reply, str):
            return reply
        rows = reply.get("results") if isinstance(reply, dict) else reply
        if not isinstance(rows, list):
            return _UNAVAILABLE
        for row in rows:
            if isinstance(row, dict) and row.get("server_slug") == server_slug:
                entry = _server_entry(server_slug, row)
                self._servers[(org, server_slug)] = (self._clock(), entry)
                return entry
        return _NOT_FOUND

    async def tools(self, org: str, server_slug: str) -> ToolSet | str:
        self._ensure_poll()
        cached = self._fresh(self._tools.get((org, server_slug)))
        if cached is not None:
            return cached
        reply = await self._get(
            "/api/mcp-connector/internal/enabled-tools/",
            org,
            {"X-Server-Slug": server_slug},
            {"server_slug": server_slug},
        )
        if not isinstance(reply, dict):
            return reply if isinstance(reply, str) else _UNAVAILABLE
        tools = ToolSet(
            known=_names(reply.get("known_tools")),
            enabled=_names(reply.get("enabled_tools")),
            disabled=_names(reply.get("disabled_tools")),
        )
        self._tools[(org, server_slug)] = (self._clock(), tools)
        return tools

    def _fresh[T](self, cached: tuple[float, T] | None) -> T | None:
        if cached is None or self._clock() - cached[0] >= self._ttl:
            return None
        return cached[1]

    async def _get(
        self, path: str, org: str, extra: Mapping[str, str], query: Mapping[str, str]
    ) -> object | str:
        headers = {
            "X-Gateway-Auth": "true",
            "X-Gateway-Internal-Key": self._key,
            "X-Org-Slug": org,
            **extra,
        }
        try:
            status, body = await self._transport(self._base + path, headers, query, self._timeout)
        except Exception:  # noqa: BLE001 - control unreachable is a declared refusal
            return _UNAVAILABLE
        if status != _OK:
            return _UNAVAILABLE
        try:
            decoded: object = json.loads(body)
        except (UnicodeDecodeError, ValueError):
            return _UNAVAILABLE
        return decoded

    async def poll_once(self, mget: Mget) -> None:
        """Drop entries whose ``mcp:scan_ver`` changed since the last poll. Never raises."""
        keys = sorted({f"{_SCAN_VER}:{org}" for org, _ in self._cached()}
                      | {f"{_SCAN_VER}:{org}:{srv}" for org, srv in self._cached()})
        if not keys:
            return
        try:
            values = await mget(keys)
        except Exception:  # noqa: BLE001 - a failed poll leaves the TTL as the bound
            return
        for key, value in zip(keys, values, strict=False):
            previous = self._versions.get(key, value)
            self._versions[key] = value
            if previous != value:
                self._drop(key[len(_SCAN_VER) + 1:])

    async def run_poll(self, mget: Mget, interval_s: float) -> None:
        """The lifespan task: poll until cancelled."""
        while True:
            await self.poll_once(mget)
            await asyncio.sleep(interval_s)

    def _cached(self) -> set[tuple[str, str]]:
        return set(self._servers) | set(self._tools)

    def _drop(self, scope: str) -> None:
        org, _, server = scope.partition(":")
        for table in (self._servers, self._tools):
            for cached in [key for key in table if key[0] == org and server in ("", key[1])]:
                table.pop(cached, None)


def _server_entry(slug: str, row: Mapping[str, object]) -> ServerEntry:
    args = row.get("args")
    env = row.get("env_vars")
    auth_type = row.get("auth_type")
    return ServerEntry(
        slug=slug,
        transport=str(row.get("transport") or "streamable-http").strip().lower(),
        url=str(row.get("url") or ""),
        command=str(row.get("command") or ""),
        args=tuple(str(item) for item in args) if isinstance(args, list) else (),
        env={str(k): str(v) for k, v in env.items()} if isinstance(env, dict) else {},
        usable=row.get("is_active") is True and row.get("is_exposed_to_agents") is True,
        needs_auth=auth_type != _NO_AUTH or row.get("oauth_authorized") is True,
    )


def _names(value: object) -> frozenset[str]:
    if not isinstance(value, list):
        return frozenset()
    return frozenset(str(item) for item in value)
