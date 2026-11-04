"""GW32: the shared MCP session store (DD-2).

Requests of one session land on different workers, so a per-worker table would reject valid
sessions. Sessions are small records in the ``{rv2}`` keyspace with a TTL. The id is
``token_urlsafe(32)``; only ``sha256(id)`` is stored. Per-key, per-org and global live counts are
ZSETs of session hashes scored by expiry, all under the same hash tag so one WATCH/MULTI covers
them (the GW06 idiom, not Lua). A refused open never evicts another key's session (R3.8).

A store failure raises :class:`StoreDataUnavailable`; the edge maps it to
``shared_state_unavailable`` (503), never to "session valid". A touch whose transaction cannot
commit raises too: "valid without touch" would let a live session fall out of the counts.
"""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from gateway_v2.domain import posture
from gateway_v2.domain.state import StoreDataUnavailable
from gateway_v2.runtime.resources_mcp import McpTransportBounds
from gateway_v2.runtime.store_keys_mcp import McpStoreKeys

__all__ = (
    "MemorySessionStore",
    "SessionBinding",
    "SessionRefusal",
    "SessionStore",
    "ValkeySessionStore",
)


@dataclass(frozen=True, slots=True)
class SessionBinding:
    """What a session is bound to. A request whose binding differs is not the owner."""

    key_id: str
    org_id: str
    server_slug: str


@dataclass(frozen=True, slots=True)
class SessionRefusal:
    code: str


class SessionStore(Protocol):
    async def open(self, binding: SessionBinding) -> str | SessionRefusal: ...

    async def check(self, session_id: str, binding: SessionBinding) -> bool: ...

    async def close(self, session_id: str, binding: SessionBinding) -> bool: ...


def _digest(session_id: str) -> str:
    return hashlib.sha256(session_id.encode("ascii", "replace")).hexdigest()


def _limit_refusal() -> SessionRefusal:
    return SessionRefusal(posture.MCP_SESSION_LIMIT)


class MemorySessionStore:
    """The in-memory twin: same Protocol, same bounds, instance-owned state (tests and dev)."""

    def __init__(self, bounds: McpTransportBounds, clock: Callable[[], float]) -> None:
        self._bounds, self._clock = bounds, clock
        # digest -> (binding, created, expires)
        self._rows: dict[str, tuple[SessionBinding, float, float]] = {}

    def _live(self) -> dict[str, tuple[SessionBinding, float, float]]:
        now = self._clock()
        self._rows = {d: row for d, row in self._rows.items() if row[2] > now}
        return self._rows

    async def open(self, binding: SessionBinding) -> str | SessionRefusal:
        rows = self._live()
        counts = (
            sum(1 for b, _, _ in rows.values() if b.key_id == binding.key_id),
            sum(1 for b, _, _ in rows.values() if b.org_id == binding.org_id),
            len(rows),
        )
        limits = (
            self._bounds.sessions_per_key,
            self._bounds.sessions_per_org,
            self._bounds.sessions_global,
        )
        if any(count >= limit for count, limit in zip(counts, limits, strict=True)):
            return _limit_refusal()
        session_id = secrets.token_urlsafe(32)
        now = self._clock()
        self._rows[_digest(session_id)] = (binding, now, now + self._bounds.session_idle_ttl_s)
        return session_id

    async def check(self, session_id: str, binding: SessionBinding) -> bool:
        row = self._live().get(_digest(session_id))
        if row is None or row[0] != binding:
            return False
        now = self._clock()
        expiry = min(
            now + self._bounds.session_idle_ttl_s, row[1] + self._bounds.session_max_lifetime_s
        )
        self._rows[_digest(session_id)] = (row[0], row[1], expiry)
        return True

    async def close(self, session_id: str, binding: SessionBinding) -> bool:
        row = self._live().get(_digest(session_id))
        if row is None or row[0] != binding:
            return False
        del self._rows[_digest(session_id)]
        return True


class _Pipe(Protocol):
    async def __aenter__(self) -> _Pipe: ...
    async def __aexit__(self, *exc: object) -> None: ...
    async def watch(self, *keys: str) -> object: ...
    async def unwatch(self) -> object: ...
    def multi(self) -> None: ...
    def zremrangebyscore(self, key: str, lo: object, hi: object) -> object: ...
    async def zcount(self, key: str, lo: object, hi: object) -> int: ...
    async def hmget(self, key: str, fields: list[str]) -> list[Any]: ...
    def zadd(self, key: str, mapping: dict[str, float], **kw: Any) -> object: ...
    def zrem(self, key: str, *members: str) -> object: ...
    def hset(self, key: str, *, mapping: dict[str, str]) -> object: ...
    def pexpire(self, key: str, ms: int) -> object: ...
    def delete(self, *keys: str) -> object: ...
    async def execute(self) -> list[Any]: ...


class _Client(Protocol):
    def pipeline(self) -> _Pipe: ...


def _text(value: object) -> str:
    return value.decode() if isinstance(value, (bytes, bytearray)) else str(value)


class ValkeySessionStore:
    """The shared store over a redis-py style async client (WATCH/MULTI, ``{rv2}``-tagged)."""

    def __init__(
        self,
        client: _Client,
        keys: McpStoreKeys,
        bounds: McpTransportBounds,
        clock: Callable[[], float],
        max_tries: int,
    ) -> None:
        self._client, self._keys, self._bounds, self._clock = client, keys, bounds, clock
        self._tries = max_tries

    def _sets(self, binding: SessionBinding) -> tuple[str, str, str]:
        return (
            self._keys.sessions_by_key(binding.key_id),
            self._keys.sessions_by_org(binding.org_id),
            self._keys.sessions_global(),
        )

    @staticmethod
    async def _row(pipe: _Pipe, key: str) -> tuple[str, str, str, str] | None:
        """The four bound fields of one session hash (HMGET: O(1), not a whole-hash read)."""
        values = await pipe.hmget(key, ["key_id", "org_id", "server_slug", "created_ms"])
        if any(value is None for value in values):
            return None
        a, b, c, d = (_text(value) for value in values)
        return a, b, c, d

    async def open(self, binding: SessionBinding) -> str | SessionRefusal:
        session_id = secrets.token_urlsafe(32)
        digest = _digest(session_id)
        zsets = self._sets(binding)
        limits = (
            self._bounds.sessions_per_key,
            self._bounds.sessions_per_org,
            self._bounds.sessions_global,
        )
        ttl = self._bounds.session_idle_ttl_s
        for _ in range(self._tries):
            now = self._clock()
            async with self._client.pipeline() as pipe:
                try:
                    await pipe.watch(*zsets)
                    # Count LIVE members read-only: pruning here would dirty our own WATCH.
                    counts = [await pipe.zcount(zset, now, "+inf") for zset in zsets]
                    if any(c >= limit for c, limit in zip(counts, limits, strict=True)):
                        await pipe.unwatch()
                        return _limit_refusal()
                    pipe.multi()
                    for zset in zsets:
                        pipe.zremrangebyscore(zset, "-inf", now)
                        pipe.zadd(zset, {digest: now + ttl})
                    pipe.hset(
                        self._keys.session(digest),
                        mapping={
                            "key_id": binding.key_id,
                            "org_id": binding.org_id,
                            "server_slug": binding.server_slug,
                            "created_ms": str(int(now * 1000)),
                        },
                    )
                    pipe.pexpire(self._keys.session(digest), round(ttl * 1000))
                    await pipe.execute()
                    return session_id
                except Exception as exc:  # noqa: BLE001
                    if type(exc).__name__ == "WatchError":
                        continue
                    raise StoreDataUnavailable("session store unavailable") from exc
        raise StoreDataUnavailable("session open could not commit")

    async def check(self, session_id: str, binding: SessionBinding) -> bool:
        digest = _digest(session_id)
        key = self._keys.session(digest)
        zsets = self._sets(binding)
        for _ in range(self._tries):
            async with self._client.pipeline() as pipe:
                try:
                    await pipe.watch(key)
                    row = await self._row(pipe, key)
                    if row is None or (row[0], row[1], row[2]) != (
                        binding.key_id,
                        binding.org_id,
                        binding.server_slug,
                    ):
                        await pipe.unwatch()
                        return False
                    now = self._clock()
                    ceiling = int(row[3]) / 1000 + self._bounds.session_max_lifetime_s
                    expiry = min(now + self._bounds.session_idle_ttl_s, ceiling)
                    if expiry <= now:
                        await pipe.unwatch()
                        return False
                    pipe.multi()
                    pipe.pexpire(key, max(1, round((expiry - now) * 1000)))
                    for zset in zsets:
                        pipe.zadd(zset, {digest: expiry}, xx=True)
                    await pipe.execute()
                    return True
                except Exception as exc:  # noqa: BLE001
                    if type(exc).__name__ == "WatchError":
                        continue
                    raise StoreDataUnavailable("session store unavailable") from exc
        raise StoreDataUnavailable("session touch could not commit")

    async def close(self, session_id: str, binding: SessionBinding) -> bool:
        digest = _digest(session_id)
        key = self._keys.session(digest)
        for _ in range(self._tries):
            async with self._client.pipeline() as pipe:
                try:
                    await pipe.watch(key)
                    row = await self._row(pipe, key)
                    if row is None or (row[0], row[1], row[2]) != (
                        binding.key_id,
                        binding.org_id,
                        binding.server_slug,
                    ):
                        await pipe.unwatch()
                        return False
                    pipe.multi()
                    pipe.delete(key)
                    for zset in self._sets(binding):
                        pipe.zrem(zset, digest)
                    await pipe.execute()
                    return True
                except Exception as exc:  # noqa: BLE001
                    if type(exc).__name__ == "WatchError":
                        continue
                    raise StoreDataUnavailable("session store unavailable") from exc
        raise StoreDataUnavailable("session close could not commit")
