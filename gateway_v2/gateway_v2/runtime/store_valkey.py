"""The reader's Valkey/Redis adapter. The commands GW05c's cost claims are made of.

Every method is ONE round trip, and the command set per round is fixed:

    head()         GET meta  +  ZCARD idx  +  ZREVRANGE idx 0 0 WITHSCORES
    index_range()  ZCOUNT idx -inf <upto>  +  ZRANGEBYSCORE idx (<after> <upto> LIMIT 0 <n>
    records()      HMGET <hash> <keys>     or  MGET <per-record keys>
    engaged()      SMEMBERS on:<kind>

No `HGETALL`, no `HVALS`, no `SCAN`, no `KEYS`. Those are the shapes that made RC2's refresh
O(records) (R2-02), and their absence here is what the fixed command list in the guard tests is
asserting.

`ZRANGEBYSCORE` takes `(after` so the lower bound is EXCLUSIVE: a reader must not re-apply the
record it is already at. `upto` is the position the manifest attests, which is what keeps an
in-flight bulk publish from being read as a fault.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from gateway_v2.domain.state import StateKind
from gateway_v2.runtime.state_feed import Head, IndexPage
from gateway_v2.runtime.store_keys import HASH_KINDS, KEYS, StoreKeys


def _as_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return int(value)


def _as_text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)


def _as_bytes(value: object) -> bytes | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("utf-8")
    return None


class ValkeyStateStore:
    """Satisfies `gateway_v2.runtime.state_feed.StateStore` over redis.asyncio.

    The client is injected: pool sizing and operation timeouts come from the ResourceContract
    (GW03), not from a literal in here.
    """

    def __init__(self, client: Any, keys: StoreKeys = KEYS) -> None:
        self._client = client
        self._keys = keys

    async def head(self, kind: StateKind) -> Head:
        """One MULTI: the manifest, the index size and the index head."""
        index = self._keys.index(kind)
        async with self._client.pipeline(transaction=True) as pipe:
            pipe.get(self._keys.manifest(kind))
            pipe.zcard(index)
            pipe.zrevrange(index, 0, 0, withscores=True)
            manifest_raw, count, top = await pipe.execute()
        return Head(
            manifest_raw=_as_bytes(manifest_raw),
            index_count=_as_int(count),
            index_top=0 if not top else _as_int(top[0][1]),
        )

    async def index_range(
        self,
        kind: StateKind,
        *,
        after: int,
        upto: int,
        limit: int,
    ) -> IndexPage:
        """One MULTI: the exact count at or below the attested position, and the page above."""
        index = self._keys.index(kind)
        async with self._client.pipeline(transaction=True) as pipe:
            pipe.zcount(index, "-inf", upto)
            pipe.zrangebyscore(
                index,
                f"({after}",
                upto,
                start=0,
                num=limit,
                withscores=True,
            )
            total, page = await pipe.execute()
        return IndexPage(
            entries=tuple((_as_text(member), _as_int(score)) for member, score in page),
            total_at_or_below=_as_int(total),
        )

    async def records(self, kind: StateKind, keys: Sequence[str]) -> tuple[bytes | None, ...]:
        """One command. Hash-stored kinds use HMGET; plans have a key each, so MGET."""
        if not keys:
            return ()
        if kind in HASH_KINDS:
            raw = await self._client.hmget(self._keys.record_hash(kind), list(keys))
        else:
            raw = await self._client.mget([self._keys.record_key(kind, key) for key in keys])
        return tuple(_as_bytes(value) for value in raw)

    async def engaged(self, kind: StateKind) -> tuple[str, ...]:
        """O(engaged). The cold-start read that replaces reading the whole kind."""
        members = await self._client.smembers(self._keys.engaged(kind))
        return tuple(sorted(_as_text(member) for member in members))
