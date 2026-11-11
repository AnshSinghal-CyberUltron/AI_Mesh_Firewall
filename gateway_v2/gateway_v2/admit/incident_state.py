"""GW29 `IncidentState`: the one isolation read model and the one expiry decision (design 4.4).

Two sources feed it: signed `iso:<model>` records (from `KillSwitchSnapshot`) and process-local
auto-isolations created by `AutoIsolator` (a lock-guarded dict bounded by `local_slots`).
`active_isolation(model)` is the ONLY place expiry is decided; the request path (`IncidentGate`)
and the display read model (`snapshot_models`) both call it, so they agree by construction.

Expiry rules (wall time and monotonic time are injected, never read from a global):

* an explicit finite `expires_at` in the past: inactive, evaluated on every read, no grace;
* operator `no_expiry`: active until the signed OFF;
* auto origin with no usable `expires_at`: inactive after `created_at + auto_duration`, and when
  `created_at` is also missing, after `auto_duration` of monotonic time since this process first
  saw the record;
* auto origin is additionally capped by `max_hold` of monotonic time since first seen, so a skewed
  clock or an absurd `expires_at` cannot hold a model isolated indefinitely;
* a malformed record stays active for THAT model only (the gate refuses it) until the signed OFF.

Lock order (review finding 6): this module owns lock I, `KillSwitchSnapshot` owns lock S. They are
NEVER nested. Every method reads `switches.*` (which takes and releases S and returns immutable
copies) BEFORE taking I, and the snapshot never calls into this class. An AST test pins that no
`with self._lock` body here calls a `switches.*` method.

Who emits (review finding 3): gateways emit ISOLATE/RECOVER only for process-local auto isolations.
Transitions of SIGNED records belong to control (it wrote the record and the OFF); `sweep()` reports
them separately and emits nothing.
"""

from __future__ import annotations

import math
import threading
from collections.abc import Callable
from dataclasses import dataclass

from gateway_v2.admit.killswitch import KillSwitchSnapshot
from gateway_v2.domain.incident import (
    IsolationOrigin,
    IsolationRecord,
    KillEvaluation,
    ModelStatus,
    SweepResult,
)

__all__ = ("IncidentState", "IsolationView")

_SIGNED = "signed"
_LOCAL = "local"
type _FirstSeenToken = tuple[str, int, float | None]


@dataclass(frozen=True, slots=True)
class IsolationView:
    """One ACTIVE isolation as the display read model reports it."""

    model: str
    source: str
    record: IsolationRecord


def _valid_deadline(value: float | None) -> bool:
    return value is not None and math.isfinite(value) and value > 0


class IncidentState:
    """Signed isolations plus process-local auto isolations, with the one expiry function."""

    __slots__ = (
        "_switches", "_wall", "_clock", "_lock", "_local", "_first_seen", "_was_active",
        "_slots", "_auto_duration", "_max_hold", "_hook", "_signed_seen", "_evicted",
        "_suppressed",
    )

    def __init__(
        self,
        *,
        switches: KillSwitchSnapshot,
        wall: Callable[[], float],
        clock: Callable[[], float],
        local_slots: int,
        auto_duration_s: float,
        max_hold_s: float,
        probation_hook: Callable[[str], None] | None = None,
    ) -> None:
        if local_slots < 1 or auto_duration_s <= 0 or max_hold_s <= 0:
            raise ValueError("IncidentState needs positive slots, duration and hold cap")
        self._switches = switches
        self._wall = wall
        self._clock = clock
        self._lock = threading.Lock()
        self._local: dict[str, IsolationRecord] = {}
        self._first_seen: dict[str, tuple[_FirstSeenToken, float]] = {}
        self._was_active: dict[str, str] = {}
        self._slots = local_slots
        self._auto_duration = auto_duration_s
        self._max_hold = max_hold_s
        self._hook = probation_hook
        self._signed_seen: set[str] = set()
        self._evicted = 0
        self._suppressed = 0

    # --- the one expiry decision ---------------------------------------------------------------

    def _is_active_locked(self, rec: IsolationRecord, source: str) -> bool:
        if rec.malformed:
            return True  # refuses THAT model only, until the signed OFF
        wall = self._wall()
        if rec.origin is IsolationOrigin.OPERATOR:
            if rec.no_expiry:
                return True
            return rec.expires_at is not None and wall < rec.expires_at
        # auto origin. First-seen is recorded BEFORE any wall-clock verdict, so a record that was
        # already expired on its first read still starts its monotonic hold clock and a wall clock
        # stepping backwards cannot resurrect it beyond `max_hold`.
        first = self._first_seen_locked(rec, source)
        if _valid_deadline(rec.expires_at):
            if rec.expires_at is not None and wall >= rec.expires_at:
                return False
        elif _valid_deadline(rec.created_at):
            if rec.created_at is not None and wall >= rec.created_at + self._auto_duration:
                return False
        age = self._clock() - first
        if age >= self._max_hold:
            return False
        usable = _valid_deadline(rec.expires_at) or _valid_deadline(rec.created_at)
        return usable or age < self._auto_duration

    def _first_seen_locked(self, rec: IsolationRecord, source: str) -> float:
        token: _FirstSeenToken = (source, rec.feed_seq, rec.created_at)
        held = self._first_seen.get(rec.model)
        if held is not None and held[0] == token:
            return held[1]
        now = self._clock()
        self._first_seen[rec.model] = (token, now)
        return now

    def _note_signed_locked(self, model: str, signed: IsolationRecord | None) -> bool:
        """Track signed presence. True when a signed OFF just cleared a local auto entry."""
        if signed is not None:
            self._signed_seen.add(model)
            return False
        if model not in self._signed_seen:
            return False
        self._signed_seen.discard(model)
        self._first_seen.pop(model, None)
        return self._local.pop(model, None) is not None

    def _lookup(self, model: str) -> tuple[IsolationRecord, str] | None:
        signed = self._switches.isolation_record(model)  # takes and releases S before I
        cleared = False
        found: tuple[IsolationRecord, str] | None = None
        with self._lock:
            cleared = self._note_signed_locked(model, signed)
            if signed is not None and self._is_active_locked(signed, _SIGNED):
                found = (signed, _SIGNED)
            else:
                local = self._local.get(model)
                if local is not None and self._is_active_locked(local, _LOCAL):
                    found = (local, _LOCAL)
        if cleared and self._hook is not None:
            self._hook(model)
        return found

    def active_isolation(self, model: str) -> IsolationRecord | None:
        """The active isolation for a canonical model, signed preferred, expiry applied."""
        found = self._lookup(model)
        return None if found is None else found[0]

    def status_of(self, org_id: str, model: str, ev: KillEvaluation) -> ModelStatus:
        """Kill and isolation facts for one model: killed at ANY scope for this org."""
        return ModelStatus(model, ev.engaged, self.active_isolation(model))

    # --- display read model --------------------------------------------------------------------

    def snapshot_models(self) -> tuple[IsolationView, ...]:
        """Every active isolation, sorted by model. Uses the same expiry function as the gate."""
        signed = self._switches.isolation_models()
        with self._lock:
            names = sorted({*signed, *self._local})
        views: list[IsolationView] = []
        for name in names:
            found = self._lookup(name)
            if found is not None:
                views.append(IsolationView(name, found[1], found[0]))
        return tuple(views)

    def expired_signed_count(self) -> int:
        """Signed `on: true` isolation records that are no longer active (for the gauge)."""
        records = self._switches.isolation_records()
        with self._lock:
            return sum(1 for r in records if not self._is_active_locked(r, _SIGNED))

    @property
    def local_evicted_total(self) -> int:
        with self._lock:
            return self._evicted

    @property
    def local_suppressed_total(self) -> int:
        with self._lock:
            return self._suppressed

    # --- process-local auto isolations ---------------------------------------------------------

    def add_local(self, rec: IsolationRecord) -> bool:
        """Add a local auto isolation. Tightening only: False (and counted) when suppressed.

        Refused when an active signed isolation of any origin exists for the model (an operator
        isolation is never overridden or shortened; a signed auto one supersedes the local one) or
        when a `model:` kill is engaged. Never removes anything but this table's own expired
        entries, and at the bound evicts the entry with the earliest expiry.
        """
        if rec.origin is not IsolationOrigin.AUTO or rec.malformed:
            return False
        killed = self._switches.model_killed(rec.model)  # S before I, never nested
        signed = self._switches.isolation_record(rec.model)
        with self._lock:
            self._note_signed_locked(rec.model, signed)
            if killed or (signed is not None and self._is_active_locked(signed, _SIGNED)):
                self._suppressed += 1
                return False
            current = self._local.get(rec.model)
            if current is not None and self._is_active_locked(current, _LOCAL):
                self._suppressed += 1
                return False
            self._purge_expired_locked()
            if rec.model not in self._local and len(self._local) >= self._slots:
                self._evict_earliest_locked()
            self._local[rec.model] = rec
            self._first_seen.pop(rec.model, None)
        return True

    def _purge_expired_locked(self) -> None:
        for model in [m for m, r in self._local.items() if not self._is_active_locked(r, _LOCAL)]:
            del self._local[model]
            self._first_seen.pop(model, None)

    def _evict_earliest_locked(self) -> None:
        def expiry(item: tuple[str, IsolationRecord]) -> tuple[float, str]:
            deadline = item[1].expires_at
            return (math.inf if deadline is None else deadline, item[0])

        victim = min(self._local.items(), key=expiry)[0]
        del self._local[victim]
        self._first_seen.pop(victim, None)
        self._evicted += 1

    # --- transitions ---------------------------------------------------------------------------

    def sweep(self) -> SweepResult:
        """Models whose isolation ended since the previous sweep, each exactly once, disjoint.

        Request-path reads never touch `_was_active`, so a RECOVER never depends on a request for
        that model arriving. `_first_seen` and `_was_active` are bounded by the signed isolation
        set plus `local_slots`; neither grows with traffic.
        """
        signed = {r.model: r for r in self._switches.isolation_records()}  # S, released first
        cleared: list[str] = []
        local_expired: list[str] = []
        signed_expired: list[str] = []
        with self._lock:
            for model in signed:
                self._note_signed_locked(model, signed[model])
            for model in sorted(self._signed_seen.difference(signed)):
                if self._note_signed_locked(model, None):
                    cleared.append(model)
            active: dict[str, str] = {}
            for model, rec in signed.items():
                if self._is_active_locked(rec, _SIGNED):
                    active[model] = _SIGNED
            for model, rec in list(self._local.items()):
                if active.get(model) == _SIGNED:
                    del self._local[model]  # superseded by the signed record: silent convergence
                elif self._is_active_locked(rec, _LOCAL):
                    active[model] = _LOCAL
                else:
                    del self._local[model]
            for model, was in self._was_active.items():
                now = active.get(model)
                if now == was:
                    continue
                if was == _LOCAL and now is None:
                    local_expired.append(model)
                elif was == _SIGNED:
                    signed_expired.append(model)
            self._was_active = active
            keep = {*signed, *self._local}
            for model in [m for m in self._first_seen if m not in keep]:
                del self._first_seen[model]
        if self._hook is not None:
            for model in cleared:
                self._hook(model)
        return SweepResult(tuple(sorted(local_expired)), tuple(sorted(signed_expired)))
