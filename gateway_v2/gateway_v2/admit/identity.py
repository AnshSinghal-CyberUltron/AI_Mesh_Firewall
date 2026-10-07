"""Key to Principal: per-key invalidation, single-flight, bounded, with a negative cache.

RC2 made the key manifest's version the auth epoch, so ANY key write dropped every principal on
every worker within one 500 ms refresh (R2-02 / SP11). Measured in E2-01 phase B: store round
trips per request went 0.134 -> 0.986 and identity fetches 14/s -> 118/s, which produced 89 x 503
`shared_state_unavailable` at 10,000 tenants and 640 at 25,000.

Three changes, each closing one part of that:

* **Per-key eviction.** The key delta names the hashes that changed, so exactly those are
  evicted. The correctness bound is unchanged in strength — a revoked key stops being admitted
  within one feed round, which is what the global wipe delivered, because the wipe was itself
  carried by that same round.
* **Single-flight.** K concurrent requests for one cold key share ONE fetch. RC2 issued K.
  The fetch runs in its own task and every caller awaits it shielded, so a caller giving up does
  not cancel the fetch the others are waiting on.
* **Negative cache.** A never-issued key is remembered briefly, so key scanning costs one store
  read instead of one per attempt (GW06 / C26: 5,000 random keys caused 5,000 reads). It is
  invalidated by the key delta too, otherwise a `key_add` would not take effect for a key someone
  probed a moment earlier.

The epoch-checked fill from C36 is kept verbatim: a principal enters the cache only at the cursor
position this process has applied, so a lagging replica cannot re-admit a key revoked at a newer
position.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass

from gateway_v2.domain.identity import Principal
from gateway_v2.domain.state import SignedRecord, StateKind, StoreDataUnavailable
from gateway_v2.runtime.state_feed import FeedRound

DEFAULT_CAPACITY = 50_000
DEFAULT_NEGATIVE_TTL_S = 2.0

Fetch = Callable[[str], Awaitable[Principal | None]]


@dataclass(frozen=True, slots=True)
class CacheStats:
    """Counters a metric family can read without touching per-tenant labels."""

    held: int
    negative: int
    inflight: int
    evicted: int
    single_flight_joins: int
    feed_seq: int


def principal_of(record: SignedRecord) -> Principal | None:
    """Resolve a signed key record. None means the key is revoked."""
    if record.kind is not StateKind.KEY:
        raise StoreDataUnavailable(f"{record.kind.value} record is not a key")
    if record.deleted:
        return None  # explicit OFF record: revoked
    try:
        body: object = json.loads(record.body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise StoreDataUnavailable(f"key record body is not valid JSON: {exc}") from exc
    if not isinstance(body, dict):
        raise StoreDataUnavailable("key record body is not an object")
    return Principal(
        key_id=_text(body, "key_id"),
        org_id=_text(body, "org_id"),
        rate_per_s=_number(body, "rate_per_s"),
        burst=_number(body, "burst"),
        feed_seq=record.feed_seq,
    )


def _text(body: dict[str, object], name: str) -> str:
    value = body.get(name)
    if not isinstance(value, str) or not value.strip():
        raise StoreDataUnavailable(f"key record field {name!r} must be a non-empty string")
    return value


def _number(body: dict[str, object], name: str) -> float:
    value = body.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StoreDataUnavailable(f"key record field {name!r} must be a number")
    if value < 0:
        raise StoreDataUnavailable(f"key record field {name!r} must not be negative")
    return float(value)


class IdentityCache:
    """Per-process principal cache. Bounded, per-key invalidated, single-flighted."""

    def __init__(
        self,
        *,
        capacity: int = DEFAULT_CAPACITY,
        negative_ttl_s: float = DEFAULT_NEGATIVE_TTL_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if capacity <= 0:
            raise ValueError("identity cache capacity must be positive")
        self._capacity = capacity
        self._negative_ttl_s = negative_ttl_s
        self._clock = clock
        self._held: OrderedDict[str, Principal] = OrderedDict()
        self._negative: OrderedDict[str, float] = OrderedDict()
        self._inflight: dict[str, asyncio.Task[Principal | None]] = {}
        self._feed_seq = 0
        self._evicted = 0
        self._joins = 0

    # --- request path --------------------------------------------------------------------------

    def cached(self, key_hash: str) -> Principal | None:
        held = self._held.get(key_hash)
        if held is not None:
            self._held.move_to_end(key_hash)
        return held

    def denied(self, key_hash: str, now: float | None = None) -> bool:
        """True while a never-issued key is still negatively cached."""
        expires = self._negative.get(key_hash)
        if expires is None:
            return False
        moment = self._clock() if now is None else now
        if moment >= expires:
            del self._negative[key_hash]
            return False
        return True

    async def resolve(self, key_hash: str, fetch: Fetch) -> Principal | None:
        """Cache, negative cache, or ONE shared store read. Never K reads for K callers."""
        held = self.cached(key_hash)
        if held is not None:
            return held
        if self.denied(key_hash):
            return None
        task = self._inflight.get(key_hash)
        if task is not None:
            self._joins += 1
            return await asyncio.shield(task)
        task = asyncio.create_task(self._fetch_once(key_hash, fetch))
        self._inflight[key_hash] = task
        task.add_done_callback(self._retire)
        return await asyncio.shield(task)

    async def _fetch_once(self, key_hash: str, fetch: Fetch) -> Principal | None:
        principal = await fetch(key_hash)
        if principal is None:
            self._deny(key_hash)
            return None
        self._admit(key_hash, principal)
        return principal

    def _retire(self, task: asyncio.Task[Principal | None]) -> None:
        for key_hash, held in tuple(self._inflight.items()):
            if held is task:
                del self._inflight[key_hash]
        if not task.cancelled():
            # Mark any exception retrieved: with no awaiter left asyncio would log it as lost.
            task.exception()

    # --- refresh path --------------------------------------------------------------------------

    def on_delta(
        self,
        records: Iterable[SignedRecord] | Sequence[SignedRecord],
        feed_seq: int,
    ) -> tuple[str, ...]:
        """Evict exactly the keys that changed. Returns what was evicted, for metrics."""
        evicted: list[str] = []
        for record in records:
            if record.kind is not StateKind.KEY:
                raise StoreDataUnavailable(f"{record.kind.value} record is not a key")
            dropped = self._held.pop(record.key, None)
            had_negative = self._negative.pop(record.key, None)
            if dropped is not None or had_negative is not None:
                evicted.append(record.key)
        self._evicted += len(evicted)
        self._feed_seq = max(self._feed_seq, feed_seq)
        return tuple(evicted)

    def mark_applied(self, feed_seq: int) -> None:
        """A round with no key changes still advances the position cached fills are checked at."""
        self._feed_seq = max(self._feed_seq, feed_seq)

    def stats(self) -> CacheStats:
        return CacheStats(
            held=len(self._held),
            negative=len(self._negative),
            inflight=len(self._inflight),
            evicted=self._evicted,
            single_flight_joins=self._joins,
            feed_seq=self._feed_seq,
        )

    # --- fills ---------------------------------------------------------------------------------

    def _admit(self, key_hash: str, principal: Principal) -> None:
        # Epoch-checked fill (C36): a principal resolved from a position this process has not
        # applied is used for THIS request but not cached, so a lagging replica cannot re-admit
        # a key that a newer position revoked.
        if principal.feed_seq > self._feed_seq:
            return
        self._held[key_hash] = principal
        self._held.move_to_end(key_hash)
        while len(self._held) > self._capacity:
            self._held.popitem(last=False)
            self._evicted += 1

    def _deny(self, key_hash: str) -> None:
        if self._negative_ttl_s <= 0:
            return
        self._negative[key_hash] = self._clock() + self._negative_ttl_s
        self._negative.move_to_end(key_hash)
        while len(self._negative) > self._capacity:
            self._negative.popitem(last=False)


def identity_applier(cache: IdentityCache) -> Callable[[FeedRound], None]:
    """Bind a cache to the round shape the synchroniser drives.

    A round with no key changes still calls `mark_applied`, because that position is what a
    later cache fill is checked against (C36's epoch-checked fill).
    """

    def apply(round_: FeedRound) -> None:
        if round_.records:
            cache.on_delta(round_.records, round_.manifest.feed_seq)
        else:
            cache.mark_applied(round_.manifest.feed_seq)

    return apply
