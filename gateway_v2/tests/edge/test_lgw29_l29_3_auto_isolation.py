# Feature: gw29-killswitch-isolation, Property 12: auto-isolation tightens only (L29-3 local)
# Validates: Requirements 5.3, 5.5, 5.6, AC16..AC19 (injected 5xx/timeouts trip inside the window)
"""Local equivalent of L29-3: 50% injected 5xx/timeouts, risk tracker, isolator, gate, recovery."""

from __future__ import annotations

import asyncio

from gateway_v2.admit.auto_isolation import AutoIsolator, IsolatorParams
from gateway_v2.admit.model_risk import ModelRiskTracker, RiskParams
from gateway_v2.dispatch.openai_upstream import BODY_OPENAI_KEY
from gateway_v2.domain.incident import (
    IncidentEventKind as K,
)
from gateway_v2.domain.incident import (
    ModelOutcome,
    ModelProfile,
    RequestConstraints,
)
from tests.admit._lgw29_support import Catalog, Constraints, Fallbacks
from tests.edge._lgw29_app import Harness, chat, contract

TAGS = frozenset({"t"})
PROFILES = {"a": ModelProfile("a", "p", TAGS, 2), "b": ModelProfile("b", "p", TAGS, 2)}
KNOWN = RequestConstraints(True, None, frozenset(), 0)
C = contract()


class Scenario:
    def __init__(self) -> None:
        self.h = Harness(rig={"catalog": True, "profiles": PROFILES, "constraints": KNOWN,
                              "fallbacks": {"a": "b"}})
        r = self.h.rig
        self.events: list = []
        self.tracker = ModelRiskTracker(clock=r.mono, params=RiskParams.from_contract(C),
                                        max_models=C.risk_max_models(), known=None)
        self.isolator = AutoIsolator(
            state=r.state, risk=self.tracker, switches=r.snap, fallbacks=Fallbacks({"a": "b"}),
            catalog=Catalog(PROFILES), constraints=Constraints(KNOWN),
            params=IsolatorParams(C.risk_threshold(), C.risk_min_distinct_orgs(),
                                  C.auto_isolation_duration_s()),
            wall=r.wall, emit=self.events.append,
        )

    def attempts(self, orgs: tuple[str, ...], n: int, *, bad_orgs: tuple[str, ...] | None = None,
                 bad: ModelOutcome = ModelOutcome.SERVER_ERROR) -> None:
        """n attempts spread over ``orgs``; alternate ones fail (50%), from ``bad_orgs`` only."""
        for i in range(n):
            org = orgs[i % len(orgs)]
            failing = (i // len(orgs)) % 2 == 0 and (bad_orgs is None or org in bad_orgs)
            outcome = bad if failing else ModelOutcome.OK
            self.tracker.observe("a", outcome, org)
            self.isolator.evaluate("a")

    def model_sent(self) -> object:
        assert asyncio.run(self.h.call("/v1/chat/completions", chat("a"))).status == 200
        return self.h.provider.bodies[-1][BODY_OPENAI_KEY]["model"]


def _n() -> int:
    return C.risk_min_samples() * 4


def test_a_multi_org_run_trips_inside_the_window_notifies_and_moves_traffic() -> None:
    s = Scenario()
    assert s.model_sent() == "a"
    start = s.h.rig.mono.now
    orgs = tuple(f"o{i}" for i in range(max(C.risk_min_distinct_orgs(), 2) + 1))
    s.attempts(orgs, _n(), bad=ModelOutcome.TIMEOUT)
    assert s.h.rig.mono.now - start < C.risk_window_s()
    iso = s.h.rig.state.active_isolation("a")
    assert iso is not None and iso.fallback == "b"
    assert [e.kind for e in s.events] == [K.ISOLATE]
    assert s.model_sent() == "b"


def test_a_one_org_run_is_valid_and_trips() -> None:
    s = Scenario()
    s.attempts(("only",), _n())
    assert s.h.rig.state.active_isolation("a") is not None


def test_a_two_org_run_with_errors_from_one_org_does_not_trip_when_the_floor_needs_both() -> None:
    s = Scenario()
    floor = min(C.risk_min_distinct_orgs(), 2)
    s.attempts(("o1", "o2"), _n(), bad_orgs=("o1",))
    tripped = s.h.rig.state.active_isolation("a") is not None
    assert tripped == (floor <= 1)
    if not tripped:
        assert s.events == []
        assert s.model_sent() == "a"


def test_recovery_after_the_isolation_ends_and_there_is_no_flapping() -> None:
    s = Scenario()
    s.attempts(("o1", "o2", "o3"), _n())
    assert s.h.rig.state.active_isolation("a") is not None
    # while isolated nothing reaches `a`, so nothing re-trips: a second evaluate adds no event
    for _ in range(10):
        s.isolator.evaluate("a")
    assert [e.kind for e in s.events] == [K.ISOLATE]
    s.isolator.sweep()  # the ticker's pass while active: records what is active now
    jump = C.max_isolation_hold_s() + C.auto_isolation_duration_s() + 1.0
    s.h.rig.wall.now += jump
    s.h.rig.mono.now += jump
    from tests.admit._lgw29_support import load
    load(s.h.rig.snap, [])  # a fresh round keeps the snapshot live
    s.isolator.sweep()
    assert [e.kind for e in s.events] == [K.ISOLATE, K.RECOVER]
    assert s.h.rig.state.active_isolation("a") is None
    assert s.model_sent() == "a"
    # probation: the old failures are gone, a few new errors alone do not re-trip it
    s.attempts(("o1", "o2", "o3"), 4)
    assert s.h.rig.state.active_isolation("a") is None
    assert [e.kind for e in s.events] == [K.ISOLATE, K.RECOVER]


def test_client_errors_and_rate_limits_at_fifty_percent_do_not_trip() -> None:
    s = Scenario()
    s.attempts(("o1", "o2", "o3"), _n(), bad=ModelOutcome.RATE_LIMITED)
    assert s.h.rig.state.active_isolation("a") is None and s.events == []
