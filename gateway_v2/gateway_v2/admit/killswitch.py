"""Kill-switch snapshot maintained from deltas, with an O(engaged) cold start.

RC2 rebuilt this from the complete record set every 500 ms on the serving loop: 203-223 ms of
loop block twice a second at 25,000 tenants, and `ks_cpu / ks_ms` ~= 0.9, so it was CPU, not the
store (R2-02 / H6). Phase A of E2-01 — steady state, zero writes — already failed at 10,000
tenants on this alone.

Records are O(tenants), because C36 requires an explicit OFF record for every scope so that
absence can never read as "switch off". The ENGAGED subset is normally empty. This snapshot keeps
only the engaged subset in RAM and moves it with the delta, so a round costs O(changes) and a
cold start costs O(engaged) — not O(tenants).

Two fail-closed rules carry the correctness that the whole-set refresh used to provide:

* `attested_engaged` (the manifest's signed `on_count`) is checked against the engaged set this
  snapshot holds after every COMPLETE round. A missed engage, a double-counted disengage or any
  drift makes the snapshot UNAVAILABLE instead of quietly serving a disengaged switch. It is an
  O(1) comparison of two integers, so it is affordable on every round.
* A record above the attested position is never applied, and a scope spelling this module does
  not recognise is UNAVAILABLE rather than ignored — an unrecognised scope could be an engaged
  switch we would otherwise skip.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from gateway_v2.domain.incident import (
    IsolationRecord,
    KillEvaluation,
    ParsedScope,
    ScopeKind,
    parse_isolation_body,
    parse_scope,
)
from gateway_v2.domain.locks import FRESH_MS
from gateway_v2.domain.state import SignedRecord, StateKind, StoreDataUnavailable
from gateway_v2.runtime.state_feed import FeedRound
from gateway_v2.runtime.state_stamp import FreshnessGate

GLOBAL_SCOPE = "global"
ORG_PREFIX = "org:"
MODEL_PREFIX = "model:"


class KillSwitchState(StrEnum):
    """Evaluated from RAM only. No store call on the request path."""

    OK = "ok"
    ENGAGED = "engaged"
    STALE = "stale"


@dataclass(frozen=True, slots=True)
class KillSwitchView:
    """An immutable read of the engaged set. O(engaged), safe to log."""

    engaged: bool
    orgs: frozenset[str]
    models: frozenset[str]
    feed_seq: int
    org_models: int = 0
    """GW29: engaged `om:<org>:<model>` scopes. A count: names stay out of metrics."""

    isolations: int = 0
    """GW29: `iso:<model>` records with `on: true`, expired or not (expiry is applied at READ)."""

    quarantined: int = 0
    """GW29: ON records of a known shape whose model could not be canonicalised (C19)."""

    @property
    def count(self) -> int:
        return (
            len(self.orgs) + len(self.models) + (1 if self.engaged else 0)
            + self.org_models + self.isolations + self.quarantined
        )


def scope_is_on(record: SignedRecord) -> bool:
    """A scope counts as engaged only on an explicit, signed `on: true`."""
    if record.kind is not StateKind.KS:
        raise StoreDataUnavailable(f"{record.kind.value} record is not a kill switch")
    if record.deleted:
        return False
    try:
        body: object = json.loads(record.body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise StoreDataUnavailable(
            f"kill-switch record {record.key!r} body is not valid JSON: {exc}",
        ) from exc
    if not isinstance(body, dict):
        raise StoreDataUnavailable(f"kill-switch record {record.key!r} body is not an object")
    state = body.get("on")
    if not isinstance(state, bool):
        # Fail CLOSED: an ambiguous switch must never read as "off". That is the entire reason
        # C36 stores an explicit OFF record.
        raise StoreDataUnavailable(
            f"kill-switch record {record.key!r} has no boolean 'on' field",
        )
    return state


class KillSwitchSnapshot:
    """Per-process engaged set. Moves with the delta; never rebuilt from the whole kind."""

    def __init__(
        self,
        *,
        stale_ms: int = FRESH_MS,
        clock: Callable[[], float] = time.monotonic,
        stamp: FreshnessGate | None = None,
        aliases: Mapping[str, str] = MappingProxyType({}),
    ) -> None:
        self._stale_s = stale_ms / 1000
        self._clock = clock
        self._stamp = stamp
        self._aliases = aliases
        self._lock = threading.Lock()
        self._engaged = False
        self._orgs: set[str] = set()
        self._models: set[str] = set()
        self._org_models: set[tuple[str, str]] = set()
        self._isolations: dict[str, IsolationRecord] = {}
        self._quarantined: set[str] = set()
        # Raw record key -> the engaged entry it holds, and how many keys hold each entry. Two keys
        # can canonicalise to ONE entry (`model:GPT-4o`, `model:gpt-4o`); the manifest's `on_count`
        # counts RECORDS, so the drift check counts keys, not entries.
        self._on_keys: dict[str, tuple[ScopeKind, str | None, str | None]] = {}
        self._refs: dict[tuple[ScopeKind, str | None, str | None], int] = {}
        self._feed_seq = 0
        # NOT `_verified_at`. This is a MONOTONIC reading of when this process last read the
        # store successfully -- nothing was verified against anything. The stamp's `verified_at`
        # is the wall-clock moment a re-hydrator compared the store with Postgres, and conflating
        # the two names is how SP2 stayed invisible: a refresh kept "verifying" a store that had
        # been wrong for a minute.
        self._refreshed_at: float | None = None

    # --- request path: RAM only ----------------------------------------------------------------

    def state(self, now: float | None = None) -> KillSwitchState:
        """`ok` | `engaged` | `stale`, from RAM. Two independent ways to be stale.

        The local one asks "did I read the store recently". The stamp asks "was that store
        checked against Postgres recently". Both are needed, and they fail in different
        scenarios: a wedged refresh trips the first and not the second, while an unpublished
        committed write (SP2) or a lagging replica (SP1) trips the second and not the first.
        """
        moment = self._clock() if now is None else now
        with self._lock:
            refreshed = self._refreshed_at
            if refreshed is None or moment - refreshed > self._stale_s:
                return KillSwitchState.STALE
            engaged = self._engaged
        if self._stamp is not None and not self._stamp.fresh():
            return KillSwitchState.STALE
        return KillSwitchState.ENGAGED if engaged else KillSwitchState.OK

    def unavailable_reason(self) -> str | None:
        """Why `state()` is STALE, or None when it is not. For the 503 detail and the log."""
        if self.state() is not KillSwitchState.STALE:
            return None
        if self._stamp is not None and not self._stamp.fresh():
            return self._stamp.unverified()
        age = self.refresh_age_seconds()
        if age is None:
            return "the kill-switch snapshot has never been loaded"
        return f"the kill-switch snapshot is {age:.1f} s old, over its {self._stale_s:g} s ceiling"

    def org_killed(self, org_id: str) -> bool:
        with self._lock:
            return org_id in self._orgs

    def model_killed(self, model: str) -> bool:
        with self._lock:
            return model in self._models

    def evaluate(self, org_id: str, model: str, now: float | None = None) -> KillEvaluation:
        """Every fact the gate needs under ONE lock acquisition (design 4.3).

        `model` must already be canonical (`domain.incident.canonical_model`): matching is exact
        set membership, so the gate canonicalises once, before any kill matching. The freshness
        stamp is checked outside the lock, exactly as `state()` does.
        """
        moment = self._clock() if now is None else now
        with self._lock:
            refreshed = self._refreshed_at
            is_global = self._engaged
            org_engaged = org_id in self._orgs
            org_model = (org_id, model) in self._org_models
            model_engaged = model in self._models
        stale = refreshed is None or moment - refreshed > self._stale_s
        if not stale and self._stamp is not None and not self._stamp.fresh():
            stale = True
        if stale:
            state = KillSwitchState.STALE
        else:
            state = KillSwitchState.ENGAGED if is_global else KillSwitchState.OK
        age = None if refreshed is None else moment - refreshed
        return KillEvaluation(
            state.value, is_global, org_engaged, org_model, model_engaged, age,
        )

    def isolation_record(self, model: str) -> IsolationRecord | None:
        """The raw, UN-expired isolation record for a canonical model name, or None."""
        with self._lock:
            return self._isolations.get(model)

    def isolation_models(self) -> tuple[str, ...]:
        """Canonical names with an `on: true` isolation record. A sorted copy."""
        with self._lock:
            return tuple(sorted(self._isolations))

    def isolation_records(self) -> tuple[IsolationRecord, ...]:
        """Every `on: true` isolation record, un-expired, as a copy taken under the lock."""
        with self._lock:
            return tuple(self._isolations[m] for m in sorted(self._isolations))

    def view(self) -> KillSwitchView:
        with self._lock:
            return KillSwitchView(
                engaged=self._engaged,
                orgs=frozenset(self._orgs),
                models=frozenset(self._models),
                feed_seq=self._feed_seq,
                org_models=len(self._org_models),
                isolations=len(self._isolations),
                quarantined=len(self._quarantined),
            )

    def refresh_age_seconds(self, now: float | None = None) -> float | None:
        """Since this process last read the store. NOT since the store was verified."""
        moment = self._clock() if now is None else now
        with self._lock:
            return None if self._refreshed_at is None else moment - self._refreshed_at

    # --- refresh path: O(changes), or O(engaged) once ------------------------------------------

    def adopt(
        self,
        records: Sequence[SignedRecord],
        *,
        attested_feed_seq: int,
        attested_engaged: int,
        now: float | None = None,
    ) -> None:
        """Cold start from the published engaged set. O(engaged), never O(tenants)."""
        moment = self._clock() if now is None else now
        with self._lock:
            self._engaged = False
            self._orgs = set()
            self._models = set()
            self._org_models = set()
            self._isolations = {}
            self._quarantined = set()
            self._on_keys = {}
            self._refs = {}
            self._feed_seq = 0
            self._absorb_locked(records, attested_feed_seq, attested_engaged, moment)

    def apply(
        self,
        records: Sequence[SignedRecord],
        *,
        attested_feed_seq: int,
        attested_engaged: int | None = None,
        now: float | None = None,
    ) -> None:
        """Move the engaged set by a delta.

        Call this on EVERY successful round, empty records included: a round that found nothing
        changed is still proof of freshness, and it is what keeps `state()` out of STALE.
        `attested_engaged` must be None when the round was truncated, because the snapshot does
        not yet hold the attested generation and the count would legitimately disagree.
        """
        moment = self._clock() if now is None else now
        with self._lock:
            self._absorb_locked(records, attested_feed_seq, attested_engaged, moment)

    def _absorb_locked(
        self,
        records: Sequence[SignedRecord],
        attested_feed_seq: int,
        attested_engaged: int | None,
        moment: float,
    ) -> None:
        try:
            self._absorb_checked(records, attested_feed_seq, attested_engaged)
        except StoreDataUnavailable:
            # Drift latch (C3): whatever was half-applied must not be served as fresh. Clearing the
            # stamp makes the snapshot STALE immediately, not after `stale_ms`; a later clean round
            # sets it again.
            self._refreshed_at = None
            raise
        self._feed_seq = max(self._feed_seq, attested_feed_seq)
        self._refreshed_at = moment

    def _absorb_checked(
        self,
        records: Sequence[SignedRecord],
        attested_feed_seq: int,
        attested_engaged: int | None,
    ) -> None:
        for record in records:
            if record.feed_seq > attested_feed_seq:
                continue  # above the attested generation: a publish in flight
            self._apply_record_locked(record)
        if attested_engaged is not None:
            held = len(self._on_keys)
            if held != attested_engaged:
                raise StoreDataUnavailable(
                    f"kill-switch snapshot holds {held} engaged scopes, the manifest attests "
                    f"{attested_engaged}",
                )

    def _apply_record_locked(self, record: SignedRecord) -> None:
        """Apply one record: parse the scope, read `on`, move the engaged entry it names."""
        on = scope_is_on(record)
        parsed = parse_scope(record.key, self._aliases)
        entry = _entry(record.key, parsed)
        previous = self._on_keys.get(record.key)
        if not on:
            if previous is not None:
                del self._on_keys[record.key]
                self._release(previous)
            return
        if previous is not None and previous != entry:
            self._release(previous)
        self._on_keys[record.key] = entry
        if previous != entry:
            self._acquire(entry)
        if parsed.kind is ScopeKind.ISOLATION and parsed.model is not None:
            self._isolations[parsed.model] = parse_isolation_body(
                record, parsed.model, self._aliases,
            )

    def _acquire(self, entry: tuple[ScopeKind, str | None, str | None]) -> None:
        count = self._refs.get(entry, 0)
        self._refs[entry] = count + 1
        if count == 0:
            self._toggle_entry(entry, True)

    def _release(self, entry: tuple[ScopeKind, str | None, str | None]) -> None:
        count = self._refs.get(entry, 0) - 1
        if count > 0:
            self._refs[entry] = count
            return
        self._refs.pop(entry, None)
        self._toggle_entry(entry, False)

    def _toggle_entry(self, entry: tuple[ScopeKind, str | None, str | None], on: bool) -> None:
        kind, org, model = entry
        if kind is ScopeKind.GLOBAL:
            self._engaged = on
        elif kind is ScopeKind.ORG and org is not None:
            _flip(self._orgs, org, on)
        elif kind is ScopeKind.MODEL and model is not None:
            _flip(self._models, model, on)
        elif kind is ScopeKind.ORG_MODEL and org is not None and model is not None:
            _flip(self._org_models, (org, model), on)
        elif kind is ScopeKind.ISOLATION and model is not None:
            if not on:
                self._isolations.pop(model, None)
        elif org is not None:  # a quarantined key: org slot carries the raw key
            _flip(self._quarantined, org, on)


def _entry(key: str, parsed: ParsedScope) -> tuple[ScopeKind, str | None, str | None]:
    """The engaged entry a record names. A quarantined record is keyed by its raw key."""
    if parsed.quarantined:
        return (parsed.kind, key, None)
    return (parsed.kind, parsed.org, parsed.model)


def _flip[T](target: set[T], item: T, on: bool) -> None:
    if on:
        target.add(item)
    else:
        target.discard(item)


def engaged_scopes(records: Iterable[SignedRecord], attested_feed_seq: int) -> tuple[str, ...]:
    """Scopes that are engaged at or below the attested position. Cold-start helper."""
    return tuple(
        record.key
        for record in records
        if record.feed_seq <= attested_feed_seq and scope_is_on(record)
    )


def killswitch_applier(snapshot: KillSwitchSnapshot) -> Callable[[FeedRound], None]:
    """Bind a snapshot to the round shape the synchroniser drives.

    `attested_engaged` is passed only on a COMPLETE round. A truncated round holds a partial
    view, which legitimately disagrees with the manifest, and checking it there would fail the
    fleet closed in the middle of a bulk publish.
    """

    def apply(round_: FeedRound) -> None:
        snapshot.apply(
            round_.records,
            attested_feed_seq=round_.manifest.feed_seq,
            attested_engaged=None if round_.truncated else round_.manifest.on_count,
        )

    return apply


def killswitch_adopter(snapshot: KillSwitchSnapshot) -> Callable[[FeedRound], None]:
    """Bind a snapshot to the O(engaged) cold start."""

    def adopt(round_: FeedRound) -> None:
        snapshot.adopt(
            round_.records,
            attested_feed_seq=round_.manifest.feed_seq,
            attested_engaged=round_.manifest.on_count,
        )

    return adopt
