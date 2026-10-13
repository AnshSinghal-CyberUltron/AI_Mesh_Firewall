"""The state_control push-coalescer (R2-23 / C43 push cost).

A per-owner, publish-side nudge coalescer that sits between the writer's committed, durable
write and the transport's `PUBLISH`, so a burst of writes to a single owner collapses to a
bounded number of nudges per owner per window instead of one `PUBLISH` per write. It coalesces
ONLY the nudge (a lossy latency hint); it never touches a Durable_Write.

Like the `StatePublisher` protocol it wraps and like `audit_control`, this module lives OUTSIDE
the import-linter `root_packages = ["gateway_v2"]`, so the layers contract does not govern it and
it may import `gateway_v2.domain` (its authority, `StateKnobs`) directly.

This file holds, across the push-coalescing tasks:

* `CoalescerCounters` — the frozen, label-free metric value a GW14d publisher reads (THIS task).
* `CoalesceConfig` — the authority-derived, start-validated bound/window (later task).
* `CoalescingPublisher` — the `StatePublisher` decorator that performs the coalescing, with its
  mutable per-`(owner, kind)` `_PendingNudge` accounting (later task).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from gateway_v2.domain.state import Manifest, SignedRecord, Stamp, StateKind
from gateway_v2.domain.state_knobs import StateKnobs
from state_control.publisher import StatePublisher, StoredHead

LOG = logging.getLogger("amf.state.coalescer")


@dataclass(frozen=True, slots=True)
class CoalescerCounters:
    """Label-free coalescer metric (Requirement 10).

    Mirrors `gateway_v2.runtime.state_nudge.ListenerCounters` exactly: a frozen slotted dataclass
    of int counters seeded 0, with a monotonic `.plus(**fields)` accumulator and a `.snapshot()`
    a GW14d publisher reads. It imports no metrics registry and self-registers nothing (Req 10.1):
    it is a plain value a publisher wires into a family.

    Exactly two series — the coalesced-push count and the pushes-saved count — and the series
    count stays two regardless of owner, tenant, or subscription count (Req 10.2); no per-owner,
    tenant-derived, or otherwise unbounded-cardinality label is ever attached (Req 10.3). Both
    series are present and zero from construction, so "no coalescing happened" (present, zero) is
    distinguishable from "series absent" (Req 10.4). Because `.plus` only ever adds non-negative
    deltas in practice, the two counts are monotonically non-decreasing (Req 10.5). `.snapshot()`
    is a plain dict read that neither raises nor mutates the value (Req 10.6).
    """

    coalesced_push: int = 0
    """Pushes actually emitted on the wire."""

    pushes_saved: int = 0
    """Nudges merged or suppressed as redundant (never placed on the wire)."""

    def plus(self, **fields: int) -> CoalescerCounters:
        """Return a NEW value with the given deltas added. Monotonic accumulator; never mutates.

        Same shape as `ListenerCounters.plus`: build the current dict of the two fields, add each
        delta, and construct a fresh `CoalescerCounters`. The frozen type is held by the coalescer
        and replaced via this method, exactly as `NudgeListener` replaces `self.counters`.
        """
        current = {
            "coalesced_push": self.coalesced_push,
            "pushes_saved": self.pushes_saved,
        }
        for name, delta in fields.items():
            current[name] += delta
        return CoalescerCounters(**current)

    def snapshot(self) -> Mapping[str, int]:
        """Return the two series as a plain dict. Neither raises nor mutates (Req 10.6)."""
        return {
            "coalesced_push": self.coalesced_push,
            "pushes_saved": self.pushes_saved,
        }


@dataclass(frozen=True, slots=True)
class CoalesceConfig:
    """The authority-derived, start-validated bound and window (Requirement 7).

    Resolved from the single declared authority, `StateKnobs` (`gateway_v2.domain.state_knobs`):
    there is no capacity-position literal in this module, so the capacity-literals discipline is
    respected and the bound/window have exactly ONE declared home (Req 7.1, 7.2). The three fields
    are frozen and slotted; `__post_init__` re-validates them, so a directly-constructed config
    (e.g. a test) is held to the same invariants `StateKnobs.__post_init__` enforces at the knob
    level. This re-validation is defense-in-depth: `from_knobs` reads knobs that are already
    validated, so it will not normally trip these guards, but a hand-built `CoalesceConfig` can.

    * `per_owner_push_bound` — the Per_Owner_Push_Bound, pushes per owner per window (Req 7).
    * `coalesce_window_s` — the Coalesce_Window in seconds, measured on the injected clock.
    * `freshness_window_s` — `StateKnobs.enforcement_bound_s`, the Freshness_Window backstop the
      Coalesce_Window must not outlive (Req 7.5).
    """

    per_owner_push_bound: int
    """Maximum nudge pushes emitted for one owner within one Coalesce_Window. Must be >= 1."""

    coalesce_window_s: float
    """The Coalesce_Window in seconds on the injected clock. Must not be negative."""

    freshness_window_s: float
    """The Freshness_Window (rehydrate + refresh period) the Coalesce_Window must not exceed."""

    def __post_init__(self) -> None:
        if self.per_owner_push_bound < 1:
            raise ValueError("per_owner_push_bound must be >= 1")
        if self.coalesce_window_s < 0:
            raise ValueError("coalesce_window_s must not be negative")
        # Freshness_Window check (Req 7.5): a window longer than the periodic-round backstop would
        # let a merged or dropped nudge outlive the window within which the round must deliver the
        # write. Equality is accepted; only a strictly longer window is refused.
        if self.coalesce_window_s > self.freshness_window_s:
            raise ValueError(
                f"coalesce_window_s={self.coalesce_window_s} exceeds the Freshness_Window "
                f"{self.freshness_window_s}: a merged nudge would outlive the backstop",
            )

    @classmethod
    def from_knobs(cls, knobs: StateKnobs) -> CoalesceConfig:
        """Resolve the bound and window from the single declared authority, `StateKnobs`.

        No capacity-position literal appears here: the authority is `StateKnobs`, whose
        `__post_init__` has already validated `coalesce_window_ms`, `per_owner_push_bound`, and
        the window <= Freshness_Window relationship fail-at-start. `enforcement_bound_s`
        (rehydrate + refresh period, in seconds) IS the Freshness_Window.
        """
        return cls(
            per_owner_push_bound=knobs.per_owner_push_bound,
            coalesce_window_s=knobs.coalesce_window_ms / 1000,
            freshness_window_s=knobs.enforcement_bound_s,
        )


@dataclass(slots=True)
class _PendingNudge:
    """Per-`(owner, kind)` window accounting. Slotted and MUTABLE: a per-key accumulator.

    One entry exists per owner that has written within a window — never one per tenant, and never
    on a serving path. Writes are a control-plane operation (plan edits, key rotations, kill-switch
    toggles), so this is neither tenant-count-proportional nor on a request path.

    * `window_start` — when the current Coalesce_Window opened, on the injected clock.
    * `pushes_this_window` — pushes already emitted for this owner/kind in the current window;
      bounded by `CoalesceConfig.per_owner_push_bound`.
    * `highest_pending` — the highest `feed_seq` seen for this owner/kind but possibly not yet on
      the wire (the position a held or window-roll flush will carry).
    * `highest_announced` — the highest `feed_seq` already PUBLISHed for this owner/kind; the
      monotonic-guard source that keeps a lower position from ever reaching the wire (Req 4.5).
    """

    window_start: float
    pushes_this_window: int
    highest_pending: int
    highest_announced: int


class CoalescingPublisher:
    """A `StatePublisher` decorator that bounds the per-owner nudge push rate (R2-23).

    Wraps an inner `StatePublisher` (`ValkeyPublisher` in prod, `MemoryStore` as the local twin).
    Reads (`stored_feed_seq`, `stored_head`, `stored_index`, `read_stamp`, `put_stamp`) and
    `publish_kind` (the re-hydrator's wholesale restore, which emits its own one nudge) pass
    straight through to `inner` with no coalescing. Only `publish_record` / `publish_batch`
    coalesce the nudge, always after delegating the Durable_Write to `inner`.

    This class satisfies the `StatePublisher` Protocol structurally (duck-typed); it is not a
    nominal subclass. The accounting map is keyed by `(owner, kind)`, where the owner is the
    write's per-tenant identity (`record.key` — the `org_id` / key owner / scope, Req 2.6).

    Batch owner key (a documented judgement call, task 5.3): `publish_record` keys the accounting
    map by `(record.key, record.kind)` exactly as the design's §3 states. `publish_batch` is ONE
    logical write operation that already carries ONE `manifest.feed_seq` and emits ONE nudge for
    the whole batch (manifest-last — Req 1.2), spanning many `record.key`s of a SINGLE kind. The
    design's per-`(owner, kind)` algorithm needs one owner key for that single nudge, so the batch
    is keyed by `(records[0].key, manifest.kind)` — its representative writing identity. This keeps
    every invariant intact: the Durable_Write is untouched (`nudge=False` delegation), the single
    emitted nudge carries `manifest.feed_seq` (the Highest_Feed_Seq), each batch's representative
    owner holds its own independent push budget and clock (Property 5, per-owner isolation), and
    the announced position for that `(owner, kind)` stays monotonic (Property 4). An empty batch
    carries no record and no owner, so it emits no nudge (it is a no-op durable write too).

    Task 5.3 fills `publish_record` / `publish_batch` with the durability-first coalesce algorithm
    (THIS task); task 5.4 fills `flush_due` / `flush_all`.
    """

    def __init__(
        self,
        inner: StatePublisher,
        config: CoalesceConfig,
        clock: Callable[[], float],
        metrics: CoalescerCounters = CoalescerCounters(),
    ) -> None:
        # `metrics=CoalescerCounters()` as a default is safe: CoalescerCounters is FROZEN, so the
        # usual mutable-default trap does not apply (the shared default can never be mutated).
        self._inner = inner
        self._config = config
        self._clock = clock
        self._metrics = metrics
        self._pending: dict[tuple[str, StateKind], _PendingNudge] = {}

    @property
    def counters(self) -> CoalescerCounters:
        """The current label-free `CoalescerCounters` value, for a GW14d publisher to read."""
        return self._metrics

    # --- reads: pass-through, no coalescing ----------------------------------------------------

    def stored_feed_seq(self, kind: StateKind) -> int | None:
        return self._inner.stored_feed_seq(kind)

    def stored_head(self, kind: StateKind) -> StoredHead:
        return self._inner.stored_head(kind)

    def stored_index(self, kind: StateKind) -> Mapping[str, int]:
        return self._inner.stored_index(kind)

    def read_stamp(self) -> bytes | None:
        return self._inner.read_stamp()

    def put_stamp(self, stamp: Stamp) -> bool:
        return self._inner.put_stamp(stamp)

    # --- re-hydrator wholesale restore: pass-through, NOT coalesced ----------------------------

    def publish_kind(
        self,
        kind: StateKind,
        records: Sequence[SignedRecord],
        manifest: Manifest,
        engaged: Sequence[str] = (),
        *,
        allow_regress: bool = False,
    ) -> bool:
        # A wholesale restore IS the change; it emits its own one nudge and must never be
        # intercepted by the coalescer. Straight pass-through.
        return self._inner.publish_kind(
            kind,
            records,
            manifest,
            engaged,
            allow_regress=allow_regress,
        )

    # --- the coalesced write path --------------------------------------------------------------

    def publish_record(
        self,
        record: SignedRecord,
        manifest: Manifest,
        *,
        engaged: bool | None = None,
        nudge: bool = True,
    ) -> bool:
        # Durability FIRST (Req 6.5): delegate the full Durable_Write + manifest to inner with the
        # inline push suppressed, and CAPTURE the bool before any coalescing can run. The writer's
        # outcome mapping (ok / ok_publish_pending / ...) is this inner bool, UNCHANGED. If this
        # call raises, it propagates to the writer exactly as the un-coalesced path does.
        result = self._inner.publish_record(record, manifest, engaged=engaged, nudge=False)
        # Then coalesce the (lossy) nudge inside a swallow-everything guard: because `result` is
        # already fixed, no coalescer error can alter the WriteOutcome (Req 3.6, 6.1, 6.4). A
        # swallowed failure simply leaves the owner to the next flush_due / periodic round, which
        # refreshes it within the Freshness_Window (Req 6.2).
        try:
            self._record_write(record.key, record.kind, manifest.feed_seq)
        except Exception as exc:  # noqa: BLE001 - the push is a lossy hint; durability already stands
            LOG.warning(
                "nudge coalesce/emit failed, durable write stands, owner left to next round: %s",
                exc,
            )
        return result

    def publish_batch(
        self,
        records: Sequence[SignedRecord],
        manifest: Manifest,
        *,
        engaged: Mapping[str, bool] | None = None,
        chunk: int = 500,
        nudge: bool = True,
    ) -> bool:
        # Durability FIRST (Req 6.5), same shape as publish_record: capture the inner bool before
        # coalescing, so a coalescer error can never change the batch's WriteOutcome.
        result = self._inner.publish_batch(
            records,
            manifest,
            engaged=engaged,
            chunk=chunk,
            nudge=False,
        )
        # A batch is ONE logical nudge for ONE kind at manifest.feed_seq. Key it by the batch's
        # representative owner (records[0].key) — see the class docstring. An empty batch carries
        # no owner, so there is nothing to coalesce (inner already treated it as a durable no-op).
        if records:
            try:
                self._record_write(records[0].key, manifest.kind, manifest.feed_seq)
            except Exception as exc:  # noqa: BLE001 - lossy hint; durability already stands
                LOG.warning(
                    "batch nudge coalesce/emit failed, durable write stands, owner left to "
                    "next round: %s",
                    exc,
                )
        return result

    def emit_nudge(self, kind: StateKind, feed_seq: int) -> None:
        # The protocol method stays a DIRECT delegate to inner — it is the raw emit the
        # coalescer's own `_emit` routes through, and the surface an external caller (or the
        # re-hydrator path) can still use to publish a nudge directly, un-coalesced.
        self._inner.emit_nudge(kind, feed_seq)

    # --- the coalesce algorithm (design §4) ----------------------------------------------------

    def _record_write(self, owner: str, kind: StateKind, feed_seq: int) -> None:
        """Run the per-`(owner, kind)` window algorithm for one committed write (design §4).

        Called by `publish_record` / `publish_batch` AFTER the Durable_Write has returned, inside
        their swallow-everything guard. Rolls the Coalesce_Window when it has elapsed (flushing the
        previous window's residue at Highest_Feed_Seq and resetting the push count), folds the new
        `feed_seq` into `highest_pending`, and emits now while under the Per_Owner_Push_Bound —
        otherwise holds the position for a window-roll / `flush_due` / shutdown flush.
        """
        key = (owner, kind)
        now = self._clock()
        entry = self._pending.get(key)
        if entry is None:
            entry = _PendingNudge(
                window_start=now,
                pushes_this_window=0,
                highest_pending=0,
                highest_announced=0,
            )
            self._pending[key] = entry
        # Window roll: a window that has fully elapsed flushes its residue (any pending position
        # not yet on the wire) at Highest_Feed_Seq, then opens a fresh window with a zeroed count.
        if now - entry.window_start >= self._config.coalesce_window_s:
            if entry.highest_pending > entry.highest_announced:
                self._emit(kind, entry.highest_pending, entry)
            entry.window_start = now
            entry.pushes_this_window = 0
        # Fold the new write into the pending Highest_Feed_Seq (Req 4.1): a coalesced push always
        # carries the maximum feed_seq among the writes it stands in for.
        entry.highest_pending = max(entry.highest_pending, feed_seq)
        # Emit now while under the per-owner bound; otherwise hold (flushed on the next window
        # roll, by flush_due, or on shutdown).
        if entry.pushes_this_window < self._config.per_owner_push_bound:
            self._emit(kind, entry.highest_pending, entry)
        # else: held — Req 2.1 bounds the window at per_owner_push_bound pushes.

    def _emit(self, kind: StateKind, pos: int, entry: _PendingNudge) -> None:
        """Put one position on the wire, honouring the monotonic guard (design §4, Req 4.5).

        Suppresses (counts `pushes_saved`) any position at or below the Highest_Feed_Seq already
        announced for this `(owner, kind)` — a lower position must NEVER reach the wire. Otherwise
        delegates to `inner.emit_nudge`, records the newly announced position, charges one push to
        the window budget, and counts one `coalesced_push`.
        """
        if pos <= entry.highest_announced:
            self._metrics = self._metrics.plus(pushes_saved=1)
            return
        self._inner.emit_nudge(kind, pos)
        entry.highest_announced = pos
        entry.pushes_this_window += 1
        self._metrics = self._metrics.plus(coalesced_push=1)

    # --- flush hooks (design §5 "The flush / timer model") -------------------------------------

    def flush_due(self, now: float) -> None:
        """Emit any held residue whose Coalesce_Window has elapsed, at Highest_Feed_Seq.

        Called by the service loop each round (design §5, layer 2): a quiesced owner's final held
        nudge goes out within one re-hydrator period, well inside the Freshness_Window. For each
        held `(owner, kind)` entry whose window has fully elapsed AND that still carries residue
        (`highest_pending > highest_announced`), emit at Highest_Feed_Seq and roll the window —
        mirroring the window-roll in `_record_write` so a flush and a subsequent write agree on
        window state. An entry with nothing pending emits nothing (Req 2.4 — a quiesced owner's
        flush is a no-op), and the monotonic guard inside `_emit` makes the residue check
        double-safe. This is a latency optimisation, never load-bearing: a never-flushed nudge is
        still delivered by the periodic O(1) round within the Freshness_Window.

        Per-entry emit failures are swallowed and logged at WARNING (fail-safe, Req 6.2) so one
        owner's transport fault cannot block another owner's flush; the swallowed owner is left to
        the next round. Iterates over a snapshot of the items so a concurrent mutation of the map
        cannot disturb the iteration.
        """
        for (owner, kind), entry in list(self._pending.items()):
            try:
                window_elapsed = now - entry.window_start >= self._config.coalesce_window_s
                has_residue = entry.highest_pending > entry.highest_announced
                if window_elapsed and has_residue:
                    self._emit(kind, entry.highest_pending, entry)
                    # Roll the window exactly as `_record_write` does, so the next write to this
                    # owner opens from the same window state rather than re-flushing stale residue.
                    entry.window_start = now
                    entry.pushes_this_window = 0
            except Exception as exc:  # noqa: BLE001 - lossy hint; owner left to the next round
                LOG.warning(
                    "flush_due emit failed for owner=%s kind=%s, left to next round: %s",
                    owner,
                    kind,
                    exc,
                )

    def flush_all(self) -> None:
        """Emit every held pending position regardless of window — the shutdown fail-safe (Req 6.3).

        Called on shutdown (design §5, layer 3). For each held `(owner, kind)` entry that still
        carries residue (`highest_pending > highest_announced`), emit at Highest_Feed_Seq
        REGARDLESS of whether the window has elapsed, so a pending Highest_Feed_Seq position is
        flushed rather than discarded. A quiesced owner (nothing pending) is a no-op (Req 2.4), and
        the monotonic guard inside `_emit` keeps the residue check double-safe.

        CRITICAL (Req 6.3): a shutdown flush must never raise. Each per-entry emit failure is
        swallowed and logged, and iteration continues to the next owner — a swallowed owner is left
        to the periodic backstop round, never discarded without that round covering it. Durability
        is not involved here at all: these methods only emit the lossy nudge, so there is nothing
        on the durable side to protect.
        """
        for (owner, kind), entry in list(self._pending.items()):
            try:
                if entry.highest_pending > entry.highest_announced:
                    self._emit(kind, entry.highest_pending, entry)
            except Exception as exc:  # noqa: BLE001 - shutdown flush must never raise (Req 6.3)
                LOG.warning(
                    "flush_all emit failed for owner=%s kind=%s, left to backstop round: %s",
                    owner,
                    kind,
                    exc,
                )
