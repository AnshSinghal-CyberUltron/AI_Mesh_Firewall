"""Builders shared by the GW29 admit tests (signed KS records and a controllable snapshot)."""

from __future__ import annotations

from gateway_v2.admit.incident_state import IncidentState
from gateway_v2.admit.killswitch import KillSwitchSnapshot
from gateway_v2.domain.incident import IsolationOrigin, IsolationRecord
from gateway_v2.domain.state import SignedRecord, StateKind, StateOp, Version
from gateway_v2.runtime.state_sig import make_record

SECRET = b"gw29-ks-test-secret"


class Clock:
    """A settable monotonic clock."""

    def __init__(self, now: float = 100.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def ks(
    scope: str,
    *,
    on: bool = True,
    feed_seq: int,
    seq: int | None = None,
    deleted: bool = False,
    body: dict[str, object] | None = None,
) -> SignedRecord:
    return make_record(
        SECRET,
        StateKind.KS,
        scope,
        {"on": on} if body is None else body,
        Version(1, feed_seq if seq is None else seq),
        feed_seq,
        deleted=deleted,
        op=StateOp.REVOKE if deleted else StateOp.PUT,
    )


def iso_body(**extra: object) -> dict[str, object]:
    return {"on": True, **extra}


def snapshot(clock: Clock | None = None, **kwargs: object) -> KillSwitchSnapshot:
    return KillSwitchSnapshot(
        stale_ms=5_000, clock=clock if clock is not None else Clock(), **kwargs,  # type: ignore[arg-type]
    )


def load(
    snap: KillSwitchSnapshot, records: list[SignedRecord], *, on_count: int | None = None,
) -> None:
    """One clean complete round: attested position = the highest feed_seq, count = engaged keys."""
    top = max((r.feed_seq for r in records), default=0)
    snap.apply(records, attested_feed_seq=top, attested_engaged=on_count)


def make_state(
    snap: KillSwitchSnapshot,
    wall: Clock,
    mono: Clock,
    *,
    slots: int = 4,
    auto_s: float = 300.0,
    hold_s: float = 600.0,
    hook: object = None,
) -> IncidentState:
    return IncidentState(
        switches=snap, wall=wall, clock=mono, local_slots=slots, auto_duration_s=auto_s,
        max_hold_s=hold_s, probation_hook=hook,  # type: ignore[arg-type]
    )


def local_auto(model: str, wall_now: float, *, dur: float = 300.0, fallback: str | None = "fb",
               ) -> IsolationRecord:
    return IsolationRecord(
        model, fallback, IsolationOrigin.AUTO, "error_rate", wall_now, wall_now + dur,
        False, 0, False,
    )
