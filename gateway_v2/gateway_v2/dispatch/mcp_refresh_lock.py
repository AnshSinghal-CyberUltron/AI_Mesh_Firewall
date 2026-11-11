"""The cross-worker upstream-refresh lock (GW17b DD-3): `SET NX PX` with compare-and-delete.

Two implementations of the `RefreshLock` protocol. `ValkeyRefreshLock` is shared by every worker
and replica. `LocalRefreshLock` is instance-owned and used only when no store is configured: it
serialises refreshes inside one worker and nothing else, and the boot log says so.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from gateway_v2.runtime.store_keys_mcp import McpStoreKeys

__all__ = ("LocalRefreshLock", "ValkeyRefreshLock")


class ValkeyRefreshLock:
    """`SET NX PX` over a redis-py style async client. A store error is a failed acquire."""

    __slots__ = ("_client", "_keys")

    def __init__(self, client: Any, keys: McpStoreKeys) -> None:
        self._client, self._keys = client, keys

    async def acquire(self, org: str, slug: str, holder: str, ttl_s: float) -> bool:
        try:
            won = await self._client.set(
                self._keys.refresh_lock(org, slug), holder, nx=True, px=max(1, round(ttl_s * 1000))
            )
        except Exception:  # noqa: BLE001 - an unreachable store means "not the winner"
            return False
        return bool(won)

    async def release(self, org: str, slug: str, holder: str) -> None:
        key = self._keys.refresh_lock(org, slug)
        try:
            async with self._client.pipeline() as pipe:
                await pipe.watch(key)
                current = await pipe.get(key)
                text = current.decode() if isinstance(current, (bytes, bytearray)) else current
                if text != holder:
                    await pipe.unwatch()
                    return
                pipe.multi()
                pipe.delete(key)
                await pipe.execute()
        except Exception:  # noqa: BLE001 - the PX expiry is the backstop
            return


class LocalRefreshLock:
    """Single-worker lock with an injected clock; expiry is evaluated on acquire."""

    __slots__ = ("_clock", "_held")

    def __init__(self, clock: Callable[[], float]) -> None:
        self._clock = clock
        self._held: dict[tuple[str, str], tuple[str, float]] = {}

    async def acquire(self, org: str, slug: str, holder: str, ttl_s: float) -> bool:
        now = self._clock()
        current = self._held.get((org, slug))
        if current is not None and current[1] > now:
            return False
        self._held[(org, slug)] = (holder, now + ttl_s)
        return True

    async def release(self, org: str, slug: str, holder: str) -> None:
        current = self._held.get((org, slug))
        if current is not None and current[0] == holder:
            self._held.pop((org, slug), None)
