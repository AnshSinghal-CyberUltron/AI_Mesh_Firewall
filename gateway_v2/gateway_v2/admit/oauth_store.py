"""GW32: the small key-value surface the OAuth 2.1 server stores everything in (R6.11).

One narrow Protocol, two implementations (an in-memory twin for tests and dev, and a Valkey one
under the ``{rv2}`` hash tag). Every record carries a TTL. The OAuth logic never sees a client
library, so the single-use guarantees below are the only thing it relies on:

* ``take`` is an atomic get-and-delete: of two concurrent callers exactly one gets the record.
* ``incr`` is a fixed-window counter used for per-source failure limits.

Values are plain JSON objects. The callers store only HMACs of codes, tokens and sources, never
the raw values.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, Protocol

from gateway_v2.domain.state import StoreDataUnavailable

__all__ = ("KV", "MemoryKV", "ValkeyKV")

Record = dict[str, Any]


class KV(Protocol):
    async def put(self, key: str, value: Record, ttl_s: float) -> None: ...

    async def get(self, key: str) -> Record | None: ...

    async def take(self, key: str) -> Record | None: ...

    async def delete(self, key: str) -> None: ...

    async def incr(self, key: str, ttl_s: float) -> int: ...

    async def peek(self, key: str) -> int: ...

    async def zadd(self, key: str, member: str, expires_at: float, ttl_s: float) -> None: ...

    async def zlive(self, key: str, now: float) -> int: ...


class MemoryKV:
    """Instance-owned twin. Expiry is evaluated on read against the injected clock."""

    def __init__(self, clock: Callable[[], float]) -> None:
        self._clock = clock
        self._rows: dict[str, tuple[float, Record]] = {}
        self._counters: dict[str, tuple[float, int]] = {}
        self._zsets: dict[str, dict[str, float]] = {}
        self.fail = False

    def _check(self) -> None:
        if self.fail:
            raise StoreDataUnavailable("injected store failure")

    async def put(self, key: str, value: Record, ttl_s: float) -> None:
        self._check()
        self._rows[key] = (self._clock() + ttl_s, json.loads(json.dumps(value)))

    async def get(self, key: str) -> Record | None:
        self._check()
        row = self._rows.get(key)
        if row is None or row[0] <= self._clock():
            self._rows.pop(key, None)
            return None
        copy: Record = json.loads(json.dumps(row[1]))
        return copy

    async def take(self, key: str) -> Record | None:
        found = await self.get(key)
        self._rows.pop(key, None)
        return found

    async def delete(self, key: str) -> None:
        self._check()
        self._rows.pop(key, None)

    async def incr(self, key: str, ttl_s: float) -> int:
        self._check()
        now = self._clock()
        expires, count = self._counters.get(key, (0.0, 0))
        if expires <= now:
            expires, count = now + ttl_s, 0
        self._counters[key] = (expires, count + 1)
        return count + 1

    async def peek(self, key: str) -> int:
        self._check()
        expires, count = self._counters.get(key, (0.0, 0))
        return count if expires > self._clock() else 0

    async def zadd(self, key: str, member: str, expires_at: float, ttl_s: float) -> None:
        self._check()
        self._zsets.setdefault(key, {})[member] = expires_at

    async def zlive(self, key: str, now: float) -> int:
        """Drop expired members, then count the rest (the live-client count)."""
        self._check()
        members = self._zsets.get(key, {})
        for member in [m for m, expires in members.items() if expires <= now]:
            del members[member]
        return len(members)


class ValkeyKV:
    """The shared store over a redis-py style async client (``decode_responses=True``)."""

    def __init__(self, client: Any, max_tries: int) -> None:
        self._client, self._tries = client, max_tries

    async def put(self, key: str, value: Record, ttl_s: float) -> None:
        try:
            await self._client.set(key, json.dumps(value), px=max(1, round(ttl_s * 1000)))
        except Exception as exc:  # noqa: BLE001
            raise StoreDataUnavailable("oauth store unavailable") from exc

    async def get(self, key: str) -> Record | None:
        try:
            raw = await self._client.get(key)
        except Exception as exc:  # noqa: BLE001
            raise StoreDataUnavailable("oauth store unavailable") from exc
        return None if raw is None else _record(raw)

    async def take(self, key: str) -> Record | None:
        for _ in range(self._tries):
            async with self._client.pipeline() as pipe:
                try:
                    await pipe.watch(key)
                    raw = await pipe.get(key)
                    if raw is None:
                        await pipe.unwatch()
                        return None
                    pipe.multi()
                    pipe.delete(key)
                    await pipe.execute()
                    return _record(raw)
                except Exception as exc:  # noqa: BLE001
                    if type(exc).__name__ == "WatchError":
                        continue
                    raise StoreDataUnavailable("oauth store unavailable") from exc
        raise StoreDataUnavailable("oauth take could not commit")

    async def delete(self, key: str) -> None:
        try:
            await self._client.delete(key)
        except Exception as exc:  # noqa: BLE001
            raise StoreDataUnavailable("oauth store unavailable") from exc

    async def incr(self, key: str, ttl_s: float) -> int:
        try:
            async with self._client.pipeline() as pipe:
                pipe.incr(key)
                pipe.pexpire(key, max(1, round(ttl_s * 1000)), nx=True)
                results = await pipe.execute()
            return int(results[0])
        except Exception as exc:  # noqa: BLE001
            raise StoreDataUnavailable("oauth store unavailable") from exc

    async def peek(self, key: str) -> int:
        try:
            raw = await self._client.get(key)
        except Exception as exc:  # noqa: BLE001
            raise StoreDataUnavailable("oauth store unavailable") from exc
        return 0 if raw is None else int(raw)

    async def zadd(self, key: str, member: str, expires_at: float, ttl_s: float) -> None:
        try:
            async with self._client.pipeline() as pipe:
                pipe.zadd(key, {member: expires_at})
                pipe.pexpire(key, max(1, round(ttl_s * 1000)))
                await pipe.execute()
        except Exception as exc:  # noqa: BLE001
            raise StoreDataUnavailable("oauth store unavailable") from exc

    async def zlive(self, key: str, now: float) -> int:
        try:
            async with self._client.pipeline() as pipe:
                pipe.zremrangebyscore(key, "-inf", now)
                pipe.zcard(key)
                results = await pipe.execute()
            return int(results[-1])
        except Exception as exc:  # noqa: BLE001
            raise StoreDataUnavailable("oauth store unavailable") from exc


def _record(raw: object) -> Record:
    try:
        text = raw if isinstance(raw, str) else bytes(raw).decode()  # type: ignore[call-overload]
        decoded = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise StoreDataUnavailable("oauth record is unreadable") from exc
    if not isinstance(decoded, dict):
        raise StoreDataUnavailable("oauth record is not an object")
    return decoded
