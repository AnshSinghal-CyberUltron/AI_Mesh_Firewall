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


# -- risk / auto-isolation builders ----------------------------------------------------------


def contract():  # type: ignore[no-untyped-def]
    from gateway_v2.runtime.kinds import HardwareSignals
    from gateway_v2.runtime.resources import from_signals

    rss = 100 * 1024 * 1024
    return from_signals(
        HardwareSignals(cpu_quota=4.0, memory_limit=rss * 64, fd_limit=4096,
                        cpu_source="t", mem_source="t", fd_source="t"),
        target_p99_ms=20.0, utilization_cap=0.75, per_worker_rss=rss,
    )


def risk_tracker(clock: Clock, *, max_models: int = 16, known=None):  # type: ignore[no-untyped-def]
    from gateway_v2.admit.model_risk import ModelRiskTracker, RiskParams

    return ModelRiskTracker(
        clock=clock, params=RiskParams.from_contract(contract()), max_models=max_models,
        known=known,
    )


def observe_many(tracker, model: str, outcome, orgs: list[str], count: int) -> None:  # type: ignore[no-untyped-def]
    for i in range(count):
        tracker.observe(model, outcome, orgs[i % len(orgs)])


class Catalog:
    def __init__(self, profiles: dict[str, object]) -> None:
        self._profiles = profiles

    def profile(self, model: str):  # type: ignore[no-untyped-def]
        return self._profiles.get(model)


class Fallbacks:
    def __init__(self, mapping: dict[str, str]) -> None:
        self._mapping = mapping

    def fallback_for(self, model: str) -> str | None:
        return self._mapping.get(model)


class Constraints:
    def __init__(self, constraints: object) -> None:
        self._constraints = constraints

    def for_request(self, plan: object, org_id: str, requested_model: str):  # type: ignore[no-untyped-def]
        return self._constraints


class Rig:
    """One worker: snapshot + state + tracker + isolator + the events it emitted."""

    def __init__(self, *, fallbacks: dict[str, str] | None = None, profiles=None,  # type: ignore[no-untyped-def]
                 constraints=None, slots: int = 4) -> None:
        from gateway_v2.admit.auto_isolation import AutoIsolator, IsolatorParams
        from gateway_v2.admit.incident_metrics import IncidentMetrics
        from gateway_v2.domain.incident import ModelProfile, RequestConstraints

        c = contract()
        self.mono, self.wall = Clock(100.0), Clock(1_000.0)
        self.snap = snapshot(self.mono)
        load(self.snap, [], on_count=0)
        self.tracker = risk_tracker(self.mono)
        self.state = make_state(self.snap, self.wall, self.mono, slots=slots,
                                auto_s=c.auto_isolation_duration_s(),
                                hold_s=c.max_isolation_hold_s(),
                                hook=self.tracker.reset_for_recovery)
        tags = frozenset({"t"})
        self.profiles = profiles if profiles is not None else {
            "a": ModelProfile("a", "p", tags, 2), "b": ModelProfile("b", "p", tags, 2)}
        self.metrics = IncidentMetrics()
        self.events: list = []
        self.isolator = AutoIsolator(
            state=self.state, risk=self.tracker, switches=self.snap,
            fallbacks=Fallbacks({"a": "b"} if fallbacks is None else fallbacks),
            catalog=Catalog(self.profiles),
            constraints=Constraints(constraints if constraints is not None
                                    else RequestConstraints(True, None, frozenset(), 0)),
            params=IsolatorParams(c.risk_threshold(), c.risk_min_distinct_orgs(),
                                  c.auto_isolation_duration_s()),
            wall=self.wall, emit=self.events.append, metrics=self.metrics,
        )

    def trip(self, model: str = "a", orgs: tuple[str, ...] = ("o1", "o2", "o3")) -> None:
        from gateway_v2.domain.incident import ModelOutcome

        observe_many(self.tracker, model, ModelOutcome.SERVER_ERROR, list(orgs),
                     contract().risk_min_samples())
