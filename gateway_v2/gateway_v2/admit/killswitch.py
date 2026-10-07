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
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from gateway_v2.domain.locks import FRESH_MS
from gateway_v2.domain.state import SignedRecord, StateKind, StoreDataUnavailable
from gateway_v2.runtime.state_feed import FeedRound

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

    @property
    def count(self) -> int:
        return len(self.orgs) + len(self.models) + (1 if self.engaged else 0)


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
    ) -> None:
        self._stale_s = stale_ms / 1000
        self._clock = clock
        self._lock = threading.Lock()
        self._engaged = False
        self._orgs: set[str] = set()
        self._models: set[str] = set()
        self._feed_seq = 0
        self._verified_at: float | None = None

    # --- request path: RAM only ----------------------------------------------------------------

    def state(self, now: float | None = None) -> KillSwitchState:
        moment = self._clock() if now is None else now
        with self._lock:
            verified = self._verified_at
            if verified is None or moment - verified > self._stale_s:
                return KillSwitchState.STALE
            return KillSwitchState.ENGAGED if self._engaged else KillSwitchState.OK

    def org_killed(self, org_id: str) -> bool:
        with self._lock:
            return org_id in self._orgs

    def model_killed(self, model: str) -> bool:
        with self._lock:
            return model in self._models

    def view(self) -> KillSwitchView:
        with self._lock:
            return KillSwitchView(
                engaged=self._engaged,
                orgs=frozenset(self._orgs),
                models=frozenset(self._models),
                feed_seq=self._feed_seq,
            )

    def age_seconds(self, now: float | None = None) -> float | None:
        moment = self._clock() if now is None else now
        with self._lock:
            return None if self._verified_at is None else moment - self._verified_at

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
        for record in records:
            if record.feed_seq > attested_feed_seq:
                continue  # above the attested generation: a publish in flight
            self._set_scope(record.key, scope_is_on(record))
        if attested_engaged is not None:
            held = len(self._orgs) + len(self._models) + (1 if self._engaged else 0)
            if held != attested_engaged:
                raise StoreDataUnavailable(
                    f"kill-switch snapshot holds {held} engaged scopes, the manifest attests "
                    f"{attested_engaged}",
                )
        self._feed_seq = max(self._feed_seq, attested_feed_seq)
        self._verified_at = moment

    def _set_scope(self, scope: str, on: bool) -> None:
        if scope == GLOBAL_SCOPE:
            self._engaged = on
            return
        if scope.startswith(ORG_PREFIX):
            self._toggle(self._orgs, scope[len(ORG_PREFIX):], on, scope)
            return
        if scope.startswith(MODEL_PREFIX):
            self._toggle(self._models, scope[len(MODEL_PREFIX):], on, scope)
            return
        raise StoreDataUnavailable(
            f"kill-switch scope {scope!r} is not recognised; an unknown scope could be an "
            "engaged switch this process would ignore",
        )

    @staticmethod
    def _toggle(target: set[str], name: str, on: bool, scope: str) -> None:
        if not name:
            raise StoreDataUnavailable(f"kill-switch scope {scope!r} names nothing")
        if on:
            target.add(name)
        else:
            target.discard(name)


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
