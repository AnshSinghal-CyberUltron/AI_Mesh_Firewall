# Feature: gw29-killswitch-isolation, Property 12/14: the live path feeds the risk tracker
# Validates: Requirements 5.1, 5.3, AC21..AC23, AC26 (real-route provider errors isolate+reroute)
"""L29-3 end to end: provider errors through the REAL route isolate model A and reroute to B."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from gateway_v2.admit.auto_isolation import AutoIsolator, IsolatorParams
from gateway_v2.admit.incident_metrics import IncidentMetrics
from gateway_v2.admit.model_risk import ModelRiskTracker, RiskParams
from gateway_v2.dispatch.openai_upstream import BODY_OPENAI_KEY
from gateway_v2.dispatch.provider import UpstreamEvent, UpstreamRequest
from gateway_v2.domain.incident import IncidentEventKind as K
from gateway_v2.domain.incident import ModelOutcome, ModelProfile, RequestConstraints
from gateway_v2.edge.incident_wiring import IncidentAuditTap
from tests.admit._lgw29_support import Catalog, Constraints, Fallbacks
from tests.edge._lgw29_app import Harness, chat, contract

TAGS = frozenset({"t"})
PROFILES = {"a": ModelProfile("a", "p", TAGS, 2), "b": ModelProfile("b", "p", TAGS, 2)}
KNOWN = RequestConstraints(True, None, frozenset(), 0)
C = contract()


class Live:
    """A harness whose audit sink is the production tap, bound to a tracker + isolator."""

    def __init__(self, failing: str, model: str = "a") -> None:
        tap = IncidentAuditTap(None, IncidentMetrics())
        self.h = Harness(
            rig={"catalog": True, "profiles": PROFILES, "constraints": KNOWN,
                 "fallbacks": {"a": "b"}},
            audit_sink=tap,
        )
        r = self.h.rig
        self.events: list = []
        self.tracker = ModelRiskTracker(
            clock=r.mono, params=RiskParams.from_contract(C), max_models=C.risk_max_models(),
            known=None,
        )
        self.isolator = AutoIsolator(
            state=r.state, risk=self.tracker, switches=r.snap, fallbacks=Fallbacks({"a": "b"}),
            catalog=Catalog(PROFILES), constraints=Constraints(KNOWN),
            params=IsolatorParams(C.risk_threshold(), C.risk_min_distinct_orgs(),
                                  C.auto_isolation_duration_s()),
            wall=r.wall, emit=self.events.append, metrics=IncidentMetrics(),
        )
        tap.bind_attempts(self._feed)
        self.failing = failing
        inner = self.h.provider.open

        def open_(request: UpstreamRequest) -> AsyncIterator[UpstreamEvent]:
            if request.body[BODY_OPENAI_KEY]["model"] == model:  # type: ignore[index]
                return self._fail()
            return inner(request)

        self.h.provider.open = open_  # type: ignore[method-assign]

    def _feed(self, *, org_id: str, model: str, outcome: ModelOutcome | None) -> None:
        if outcome is not None:
            self.tracker.observe(model, outcome, org_id)
            self.isolator.evaluate(model)

    async def _fail(self) -> AsyncIterator[UpstreamEvent]:
        yield UpstreamEvent(text_deltas=(), final=True, gateway_error=self.failing)

    def status(self, model: str = "a", *, stream: bool = False) -> int:
        return asyncio.run(self.h.call("/v1/chat/completions", chat(model, stream=stream))).status


def _drive(failing: str, *, stream: bool = False) -> Live:
    live = Live(failing)
    for _ in range(C.risk_min_samples() * 4):
        live.status(stream=stream)
        if live.h.rig.state.active_isolation("a") is not None:
            break
    return live


def test_provider_5xx_through_the_real_route_isolates_and_reroutes() -> None:
    live = _drive("upstream_error")
    iso = live.h.rig.state.active_isolation("a")
    assert iso is not None and iso.fallback == "b"
    assert [e.kind for e in live.events] == [K.ISOLATE]
    assert live.status() == 200
    assert live.h.provider.bodies[-1][BODY_OPENAI_KEY]["model"] == "b"


def test_provider_timeouts_through_the_real_route_isolate() -> None:
    assert _drive("upstream_timeout").h.rig.state.active_isolation("a") is not None


def test_client_attributable_provider_errors_never_isolate() -> None:
    live = Live("upstream_auth_error")
    for _ in range(C.risk_min_samples() * 4):
        live.status()
    assert live.h.rig.state.active_isolation("a") is None and live.events == []


def test_healthy_traffic_feeds_ok_samples_and_never_isolates() -> None:
    live = Live("upstream_error", model="never")
    for _ in range(C.risk_min_samples() * 2):
        assert live.status() == 200
    assert live.h.rig.state.active_isolation("a") is None and live.events == []


def test_streaming_provider_errors_through_the_real_route_isolate() -> None:
    live = _drive("upstream_error", stream=True)
    iso = live.h.rig.state.active_isolation("a")
    assert iso is not None and iso.fallback == "b"
