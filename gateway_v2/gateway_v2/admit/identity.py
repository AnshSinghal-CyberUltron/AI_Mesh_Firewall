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

**Per-key quarantine.** A KEY record that fails verification on the delta path (a `RecordFault`)
quarantines its key at the fault's index position. Evicting it alone was not enough: the next
request re-read the store, and a store holding the key's OLDER, genuinely signed record (the
pre-revocation ON record put back under the revocation's index entry) verifies on its own, so a
revoked key was admitted again. While quarantined, `_fetch_once` refuses (503) any answer below the
faulted position, including "absent". A verified record at or above it lifts the quarantine: a
later delta (`on_delta`), or the request path once the stored record verifies and pairs with its
index entry again (`StateRuntime.fetch_key`, `lift_quarantine`). The map is not capacity-bounded,
because dropping an entry would re-admit the key; it holds at most one entry per key the signed
KEY manifest counts, since every entry is a distinct index entry a verified round accounted for.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass

from gateway_v2.domain.identity import DENY_ALL_TOOLS, Principal
from gateway_v2.domain.state import SignedRecord, StateKind, StoreDataUnavailable
from gateway_v2.runtime.resources_mcp import mcp_policy_bounds
from gateway_v2.runtime.state_feed import FeedRound
from gateway_v2.runtime.state_stamp import FreshnessGate

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
    quarantined: int = 0
    """Keys refused because their record failed verification. A gauge: the alert reads it."""


class ProjectInvalid(StoreDataUnavailable):
    """The key record carries a malformed ``project`` (GW31).

    A subclass of :class:`StoreDataUnavailable` so any catch site that predates it still fails
    closed as a 503 and never rewrites the project. ``edge/identity_resolver`` catches it first
    and renders ``project_invalid``.
    """


_PROJECT_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789_-")
DEFAULT_PROJECT = "default"


def valid_project(value: object, max_len: int | None = None) -> bool:
    """Lowercase ``[a-z0-9_-]``, non-empty after strip, within ``max_len`` when given."""
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        return False
    if max_len is not None and len(value) > max_len:
        return False
    return all(ch in _PROJECT_CHARS for ch in value)


def principal_of(record: SignedRecord, *, max_project_len: int | None = None) -> Principal | None:
    """Resolve a signed key record. None means the key is revoked."""
    if record.kind is not StateKind.KEY:
        raise StoreDataUnavailable(f"{record.kind.value} record is not a key")
    if record.deleted:
        return None  # explicit OFF record: revoked
    try:
        body: object = json.loads(record.body)
    except RecursionError as exc:
        raise StoreDataUnavailable("key record body is nested too deeply") from exc
    except (ValueError, UnicodeDecodeError) as exc:
        raise StoreDataUnavailable(f"key record body is not valid JSON: {exc}") from exc
    if not isinstance(body, dict):
        raise StoreDataUnavailable("key record body is not an object")
    project = body.get("project")
    if project is None:
        project = DEFAULT_PROJECT
    elif not valid_project(project, max_project_len):
        raise ProjectInvalid("key record field 'project' is malformed")
    return Principal(
        key_id=_text(body, "key_id"),
        org_id=_text(body, "org_id"),
        rate_per_s=_number(body, "rate_per_s"),
        burst=_number(body, "burst"),
        feed_seq=record.feed_seq,
        mcp_allowed_tools=_allowed_tools(body),
        mcp_max_tool_calls=_max_tool_calls(body),
        project=str(project),
    )


def _allowed_tools(body: dict[str, object]) -> frozenset[str]:
    """GW17c strict parse. Absent = unrestricted; present but malformed raises, so the key does
    not resolve at all (never unrestricted)."""
    if "mcp_allowed_tools" not in body:
        return frozenset()
    raw = body["mcp_allowed_tools"]
    bounds = mcp_policy_bounds()
    if not isinstance(raw, list) or not 1 <= len(raw) <= bounds.tools_max:
        raise StoreDataUnavailable("key record field 'mcp_allowed_tools' must be a bounded list")
    names: set[str] = set()
    for name in raw:
        if not isinstance(name, str) or name in names:
            raise StoreDataUnavailable("key record 'mcp_allowed_tools' entries must be unique text")
        if name != DENY_ALL_TOOLS and not _valid_tool_name(name, bounds.tool_name_max):
            raise StoreDataUnavailable("key record 'mcp_allowed_tools' holds an invalid name")
        names.add(name)
    return frozenset(names)


def _valid_tool_name(name: str, limit: int) -> bool:
    return 1 <= len(name) <= limit and name.isprintable()


def _max_tool_calls(body: dict[str, object]) -> int:
    if "mcp_max_tool_calls" not in body:
        return 0
    value = body["mcp_max_tool_calls"]
    if isinstance(value, bool) or not isinstance(value, int):
        raise StoreDataUnavailable("key record field 'mcp_max_tool_calls' must be an integer")
    if not 0 <= value <= mcp_policy_bounds().cap_max:
        raise StoreDataUnavailable("key record field 'mcp_max_tool_calls' is out of bounds")
    return value


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
        stamp: FreshnessGate | None = None,
    ) -> None:
        if capacity <= 0:
            raise ValueError("identity cache capacity must be positive")
        self._capacity = capacity
        self._negative_ttl_s = negative_ttl_s
        self._clock = clock
        self._stamp = stamp
        self._held: OrderedDict[str, Principal] = OrderedDict()
        self._negative: OrderedDict[str, float] = OrderedDict()
        self._inflight: dict[str, asyncio.Task[Principal | None]] = {}
        self._touched: set[str] = set()
        """Keys a delta changed while their fill was in flight (N18). Bounded by `_inflight`."""
        self._quarantined: OrderedDict[str, int] = OrderedDict()
        """Key hash -> index position of its record that failed verification. See the module."""
        self._feed_seq = 0
        self._evicted = 0
        self._joins = 0

    # --- request path --------------------------------------------------------------------------

    def unverified(self) -> str | None:
        """Why identity must not be served from RAM, or None when it may be."""
        if self._stamp is None or self._stamp.fresh():
            return None
        return self._stamp.unverified()

    def cached(self, key_hash: str) -> Principal | None:
        """A held principal, or None -- including when state is unverified.

        **This suppression is the fix, not a refinement.** Fail-closed that exempts the fast path
        does nothing, because the fast path is where the traffic is: a warm worker would keep
        admitting a revoked key from RAM for as long as the revocation stayed unpublished, which
        is SP2's 60 s with no 503. The revocation may be precisely the part that was not
        verified, so while unverified nothing cached is trusted.

        The entry is SUPPRESSED, not evicted. The state is probably fine and will be verified
        again within a round or two; dropping 50,000 principals on a blip would turn a freshness
        lapse into a fetch stampede the moment it cleared (SP11's shape).
        """
        if self.unverified() is not None:
            return None
        held = self._held.get(key_hash)
        if held is not None:
            self._held.move_to_end(key_hash)
        return held

    def denied(self, key_hash: str, now: float | None = None) -> bool:
        """True while a never-issued key is still negatively cached.

        False while unverified, and the asymmetry is deliberate: a `key_add` may be the
        unpublished write, so answering from the negative cache would 401 a key that is actually
        valid. Failing closed here means 503, not 401 -- `resolve` refuses before anyone asks.
        """
        if self.unverified() is not None:
            return False
        expires = self._negative.get(key_hash)
        if expires is None:
            return False
        moment = self._clock() if now is None else now
        if moment >= expires:
            del self._negative[key_hash]
            return False
        return True

    async def resolve(self, key_hash: str, fetch: Fetch) -> Principal | None:
        """Cache, negative cache, or ONE shared store read. Never K reads for K callers.

        Raises StoreDataUnavailable while state is unverified, so the request fails closed with
        a 503 rather than resolving against data nobody has checked.
        """
        reason = self.unverified()
        if reason is not None:
            raise StoreDataUnavailable(reason)
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
        """One store read, discarded and redone if a delta touched the key meanwhile (N18).

        A read that saw the key BEFORE its revoke, completing AFTER the revoke delta was
        applied, would otherwise be cached with nothing left to evict it: the epoch check only
        refuses positions newer than applied, and this one is older. The same holds for a read
        that saw "absent" before the key's add -- it would deny a valid key. A retry starts
        after the delta was applied, so it sees at least that state; if it is touched again the
        request fails closed rather than serve either answer.
        """
        for _attempt in range(2):
            self._touched.discard(key_hash)
            principal = await fetch(key_hash)
            if key_hash in self._touched:
                continue
            floor = self._quarantined.get(key_hash)
            if floor is not None and (principal is None or principal.feed_seq < floor):
                # Below the record that failed verification: a replayed older record, or one
                # that is gone. Neither proves the key's current state, so refuse (503).
                raise StoreDataUnavailable(
                    "key record failed verification; refused until a verified record replaces it",
                )
            if principal is None:
                self._deny(key_hash)
                return None
            self._admit(key_hash, principal)
            return principal
        self._touched.discard(key_hash)
        raise StoreDataUnavailable("the key changed twice while it was being read; retry")

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
        """Evict exactly the keys that changed. Returns what was evicted, for metrics.

        A verified record at or above a key's quarantined position lifts that quarantine.
        """
        keys: list[str] = []
        for record in records:
            if record.kind is not StateKind.KEY:
                raise StoreDataUnavailable(f"{record.kind.value} record is not a key")
            keys.append(record.key)
            self.lift_quarantine(record.key, record.feed_seq)
        return self.evict(keys, feed_seq)

    def quarantine(self, faults: Iterable[tuple[str, int]], feed_seq: int) -> tuple[str, ...]:
        """Fail these keys CLOSED: `(key_hash, index position)` of records that failed verification.

        Nothing cached for them survives (`evict`), and until a verified record at or above the
        position replaces it every fill is refused (`_fetch_once`). Returns what was evicted.
        """
        keys: list[str] = []
        for key_hash, at in faults:
            self._quarantined[key_hash] = max(at, self._quarantined.get(key_hash, at))
            self._quarantined.move_to_end(key_hash)
            keys.append(key_hash)
        return self.evict(keys, feed_seq)

    def quarantined_at(self, key_hash: str) -> int | None:
        """The faulted index position this key is refused below, or None. O(1)."""
        return self._quarantined.get(key_hash)

    def lift_quarantine(self, key_hash: str, feed_seq: int) -> bool:
        """A VERIFIED record of this key at `feed_seq`: lift its quarantine if it is not older."""
        at = self._quarantined.get(key_hash)
        if at is None or feed_seq < at:
            return False
        del self._quarantined[key_hash]
        return True

    def quarantined_for_recheck(self, limit: int) -> tuple[str, ...]:
        """Up to `limit` quarantined keys, round robin, for a bounded re-check. O(limit)."""
        visit = list(itertools.islice(self._quarantined, max(limit, 0)))
        for key_hash in visit:
            self._quarantined.move_to_end(key_hash)
        return tuple(visit)

    def evict(self, key_hashes: Iterable[str], feed_seq: int) -> tuple[str, ...]:
        """Evict these key hashes, whatever their records say. Returns what was evicted.

        Part of the fail-closed answer for a KEY record that failed verification (`quarantine`):
        nothing cached for the key survives -- not a principal it may have revoked, not a
        negative entry -- and a fill already in flight for the key is not cached either.
        """
        evicted: list[str] = []
        for key_hash in key_hashes:
            if key_hash in self._inflight:
                self._touched.add(key_hash)
            dropped = self._held.pop(key_hash, None)
            had_negative = self._negative.pop(key_hash, None)
            if dropped is not None or had_negative is not None:
                evicted.append(key_hash)
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
            quarantined=len(self._quarantined),
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
        if round_.faults:
            # A KEY record that failed verification: fail that key closed until a verified record
            # at or above the fault's position replaces it (`quarantine`).
            cache.quarantine(
                ((fault.key, fault.feed_seq) for fault in round_.faults), round_.manifest.feed_seq,
            )
        if round_.records:
            cache.on_delta(round_.records, round_.manifest.feed_seq)
        else:
            cache.mark_applied(round_.manifest.feed_seq)

    return apply
