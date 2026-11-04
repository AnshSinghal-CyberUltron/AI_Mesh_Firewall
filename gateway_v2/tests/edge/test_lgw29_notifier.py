"""GW29 notifier pump: a failing, slow or flooded notifier never changes a request outcome."""
from __future__ import annotations

import asyncio
import dataclasses
import random

from gateway_v2.admit.incident_gate import IncidentGate
from gateway_v2.admit.incident_metrics import IncidentMetrics
from gateway_v2.audit.record import AuditRecord
from gateway_v2.domain.incident import (
    IncidentEvent,
    IncidentEventKind,
    IsolationOrigin,
    RequestConstraints,
)
from gateway_v2.edge.notifier_pump import LoggingNotifier, NotifierPump, RerouteLimiter
from tests.admit._lgw29_support import Catalog, Clock, Constraints, Fallbacks, GateRig, contract, ks


def _event(kind: IncidentEventKind = IncidentEventKind.ISOLATE, model: str = "a",
           fallback: str | None = "b") -> IncidentEvent:
    return IncidentEvent(kind, "global", model, fallback, "error_rate", IsolationOrigin.AUTO,
                         1.0, f"{model}:auto")


class Recording:
    def __init__(self) -> None:
        self.events: list[IncidentEvent] = []

    async def notify(self, event: IncidentEvent) -> None:
        self.events.append(event)


class Raising:
    async def notify(self, event: IncidentEvent) -> None:
        raise RuntimeError("sink down")


class Blocking:
    async def notify(self, event: IncidentEvent) -> None:
        await asyncio.sleep(3600)


def _pump(notifier: object, *, slots: int = 4, timeout_s: float = 0.05,
          audit: list[AuditRecord] | None = None) -> tuple[NotifierPump, IncidentMetrics, Clock]:
    clock = Clock(0.0)
    metrics = IncidentMetrics()
    pump = NotifierPump(
        slots=slots, notifier=notifier,  # type: ignore[arg-type]
        timeout_s=timeout_s,
        limiter=RerouteLimiter(window_s=10.0, slots=8, clock=clock), metrics=metrics,
        wall=clock, audit=None if audit is None else (lambda r: audit.append(r) or True),
    )
    return pump, metrics, clock


def test_the_queue_is_bounded_and_drops_the_oldest() -> None:
    pump, metrics, _ = _pump(Recording(), slots=3)
    for index in range(7):
        pump.emit(_event(model=f"m{index}"))
    assert pump.pending() == 3
    assert metrics.series()["amf_incident_events_dropped_total"] == 4.0
    sink = Recording()
    pump._notifier = sink  # noqa: SLF001 - swap the sink to read what survived
    asyncio.run(pump.drain_once())
    assert [e.model for e in sink.events] == ["m4", "m5", "m6"], "the OLDEST were dropped"


def test_reroute_events_are_rate_limited_but_isolate_and_recover_never() -> None:
    pump, _, clock = _pump(Recording(), slots=64)
    for _ in range(5):
        pump.emit(_event(IncidentEventKind.REROUTE))
    assert pump.pending() == 1, "one REROUTE per (model, fallback) per window"
    for kind in (IncidentEventKind.ISOLATE, IncidentEventKind.RECOVER,
                 IncidentEventKind.AUTO_ISOLATE_SKIPPED):
        for _ in range(3):
            pump.emit(_event(kind))
    assert pump.pending() == 1 + 9, "isolation changes are never suppressed"
    clock.now += 11.0
    pump.emit(_event(IncidentEventKind.REROUTE))
    assert pump.pending() == 11, "the window passed, so the next REROUTE is let through"
    pump.emit(_event(IncidentEventKind.REROUTE, fallback="c"))
    assert pump.pending() == 12, "a different pair has its own window"


def test_a_raising_notifier_is_counted_and_never_raised() -> None:
    pump, metrics, _ = _pump(Raising())
    pump.emit(_event())
    assert asyncio.run(pump.drain_once()) == 1
    assert metrics.series()["amf_incident_notifier_failures_total"] == 1.0


def test_a_blocking_notifier_times_out_and_is_not_retried() -> None:
    pump, metrics, _ = _pump(Blocking(), timeout_s=0.01)
    pump.emit(_event())
    assert asyncio.run(asyncio.wait_for(pump.drain_once(), timeout=5)) == 1
    assert metrics.series()["amf_incident_notifier_failures_total"] == 1.0
    assert pump.pending() == 0, "a timed-out event is dropped, not requeued"


def test_emit_never_raises_even_when_the_limiter_is_broken() -> None:
    pump, metrics, _ = _pump(Recording())

    class Broken:
        def allow(self, event: IncidentEvent) -> bool:
            raise RuntimeError("limiter bug")

    pump._limiter = Broken()  # type: ignore[assignment]  # noqa: SLF001
    pump.emit(_event())
    assert metrics.series()["amf_incident_events_dropped_total"] == 1.0


def test_isolation_changes_write_one_audit_record_each_and_a_failed_audit_changes_nothing() -> None:
    records: list[AuditRecord] = []
    pump, _, _ = _pump(Recording(), audit=records)
    pump.emit(_event(IncidentEventKind.ISOLATE))
    pump.emit(_event(IncidentEventKind.RECOVER, fallback=None))
    pump.emit(_event(IncidentEventKind.REROUTE))
    asyncio.run(pump.drain_once())
    assert [r.detail["cause"] for r in records] == ["isolate", "recover"], "REROUTE has its own"
    assert all(r.request_id == "incident" and r.org_id == "global" for r in records)

    def explode(record: AuditRecord) -> bool:
        raise OSError("audit sink down")

    broken, _, _ = _pump(Recording())
    broken._audit = explode  # noqa: SLF001
    broken.emit(_event())
    assert asyncio.run(broken.drain_once()) == 1


def test_events_carry_no_content_key_or_request_field() -> None:
    names = {f.name for f in dataclasses.fields(IncidentEvent)}
    assert names == {"kind", "scope", "model", "fallback", "reason", "origin", "recorded_at",
                     "dedup_key"}, "AC30: nothing request-derived can be in a notification"


def test_the_logging_notifier_writes_one_line_per_event(caplog: object) -> None:
    import logging
    with caplog.at_level(logging.INFO, logger="amf.gateway.incident"):  # type: ignore[attr-defined]
        asyncio.run(LoggingNotifier().notify(_event()))
    lines = [r.getMessage() for r in caplog.records]  # type: ignore[attr-defined]
    assert len(lines) == 1 and "kind=isolate" in lines[0] and "model=a" in lines[0]


def test_a_failing_notifier_cannot_change_the_gate_verdict() -> None:
    """AC28: the same request gets the same verdict with a healthy, raising or blocking sink."""
    verdicts = []
    for sink in (Recording(), Raising(), Blocking()):
        pump, _, _ = _pump(sink, timeout_s=0.01)
        rig = GateRig(
            catalog=True,
            profiles={"a": _profile("a"), "b": _profile("b")},
            fallbacks={"a": "b"},
            constraints=RequestConstraints(True, None, frozenset(), 0),
        )
        gate = IncidentGate(
            switches=rig.snap, state=rig.state, catalog=Catalog(rig.profiles), aliases=rig.aliases,
            fallbacks=Fallbacks({"a": "b"}),
            constraints=Constraints(RequestConstraints(True, None, frozenset(), 0)),
            retry_jitter_s=contract().incident_retry_jitter_s(), metrics=IncidentMetrics(),
            emit=pump.emit, rng=random.Random(7), wall=rig.wall,
        )
        rig.engage(ks("iso:a", feed_seq=1, body={"on": True, "fallback": "b", "no_expiry": True}))
        verdict = gate.decide(org_id="o", requested_model="a", request_id="r")
        asyncio.run(asyncio.wait_for(pump.drain_once(), timeout=5))
        verdicts.append((verdict.routed_model, verdict.refuse_code))
    assert verdicts[0] == ("b", None) and len(set(verdicts)) == 1


def _profile(name: str):  # type: ignore[no-untyped-def]
    from gateway_v2.domain.incident import ModelProfile
    return ModelProfile(name, "p", frozenset({"t"}), 2)
