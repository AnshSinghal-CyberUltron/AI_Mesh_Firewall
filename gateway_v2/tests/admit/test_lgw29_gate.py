# Validates: Requirements 1, 2, 3, 4 (AC1-AC5, AC7, AC13; order, identity, isolation, fail closed)
"""`IncidentGate`: refusal order, canonical identity, `auto`, reroute, recheck, org gate."""

from __future__ import annotations

import inspect
import random
from types import MappingProxyType

import pytest

from gateway_v2.admit.incident_gate import IncidentGate
from gateway_v2.domain.incident import (
    INCIDENT_CAUSES,
    IncidentEventKind,
    ModelProfile,
    RequestConstraints,
    RouteOutcome,
)
from gateway_v2.domain.posture import (
    KILL_SWITCH_ENGAGED,
    KILL_SWITCH_UNAVAILABLE,
    MIN_RETRY_AFTER_S,
    MODEL_ISOLATED_NO_FALLBACK,
    MODEL_KILLED,
    REROUTE_REFUSED,
)
from tests.admit._lgw29_support import GateRig, iso_body, ks

TAGS = frozenset({"t"})
PROFILES = {"a": ModelProfile("a", "p", TAGS, 2), "b": ModelProfile("b", "p", TAGS, 2)}
KNOWN = RequestConstraints(True, None, frozenset(), 0)


def _refused(v, code: str, cause: str) -> None:  # type: ignore[no-untyped-def]
    assert (v.refuse_code, v.cause) == (code, cause) and v.routed_model is None
    assert v.retry_after_s is not None and v.retry_after_s >= MIN_RETRY_AFTER_S


def test_an_unaffected_request_proceeds_with_the_canonical_name() -> None:
    rig = GateRig(aliases={"gpt-4o-2024": "gpt-4o"})
    for raw in ("gpt-4o", " GPT-4O ", "gpt-4o-2024"):
        v = rig.decide(raw)
        assert v.refuse_code is None and v.routed_model == "gpt-4o" and v.reroute is None


def test_never_loaded_snapshot_refuses_stale_first() -> None:  # AC4
    rig = GateRig(load_empty=False)
    _refused(rig.decide("m"), KILL_SWITCH_UNAVAILABLE, "stale")


def test_precedence_is_stale_then_global_org_org_model_then_model() -> None:  # AC1-AC3
    scopes = [ks("model:m", feed_seq=1), ks("om:a:m", feed_seq=2), ks("org:a", feed_seq=3),
              ks("global", feed_seq=4)]
    rig = GateRig()
    rig.engage(scopes[0])
    _refused(rig.decide("m"), MODEL_KILLED, "model")
    rig.engage(scopes[1])
    _refused(rig.decide("m"), KILL_SWITCH_ENGAGED, "org_model")
    rig.engage(scopes[2])
    _refused(rig.decide("m"), KILL_SWITCH_ENGAGED, "org")
    rig.engage(scopes[3])
    _refused(rig.decide("m"), KILL_SWITCH_ENGAGED, "global")
    rig.mono.now += 60.0  # stale outranks everything
    _refused(rig.decide("m"), KILL_SWITCH_UNAVAILABLE, "stale")


def test_an_org_kill_does_not_touch_other_orgs_and_a_model_kill_not_other_models() -> None:
    rig = GateRig()
    rig.engage(ks("org:a", feed_seq=1), ks("model:m", feed_seq=2), ks("om:b:n", feed_seq=3))
    assert rig.decide("x", "b").refuse_code is None
    assert rig.decide("x", "a").refuse_code == KILL_SWITCH_ENGAGED
    assert rig.decide("m", "b").refuse_code == MODEL_KILLED
    assert rig.decide("n", "b").refuse_code == KILL_SWITCH_ENGAGED  # AC13: that org + that model
    assert rig.decide("n", "c").refuse_code is None  # AC13: the same model for another org
    assert rig.decide("o", "b").refuse_code is None  # AC13: the same org, another model


def test_the_org_is_a_plain_argument_never_a_header_or_body_field() -> None:  # AC7
    params = set(inspect.signature(IncidentGate.decide).parameters) - {"self"}
    assert params == {"org_id", "requested_model", "request_id"}


def test_retry_after_is_floored_jittered_and_deterministic_under_a_seed() -> None:
    values = []
    for _ in range(2):
        rig = GateRig(load_empty=False)
        values.append([rig.decide("m").retry_after_s for _ in range(20)])
    assert values[0] == values[1]
    base = max(MIN_RETRY_AFTER_S, 1.5)  # rehydrate 1000 ms + refresh 500 ms defaults
    assert all(base <= v <= base + 0.5 + 1e-9 for v in values[0]) and len(set(values[0])) > 1


# -- model identity --------------------------------------------------------------------------


@pytest.mark.parametrize("raw", ["", "   ", "a b", "é", "m" * 129, "gp\x00t", None, 5])
def test_unresolvable_names_are_refused_with_the_model_killed_code(raw: object) -> None:
    _refused(GateRig().decide(raw), MODEL_KILLED, "unresolvable_model")  # type: ignore[arg-type]


def test_a_configured_catalog_refuses_models_it_does_not_know() -> None:
    rig = GateRig(catalog=True, profiles=PROFILES)
    assert rig.decide("a").routed_model == "a"
    _refused(rig.decide("zzz"), MODEL_KILLED, "unknown_model")


def test_without_a_catalog_any_canonical_name_passes_through() -> None:
    assert GateRig().decide("whatever-model").routed_model == "whatever-model"


def test_auto_is_resolved_through_the_target_or_refused() -> None:
    _refused(GateRig().decide("auto"), MODEL_KILLED, "unknown_model")  # no catalog
    _refused(GateRig(catalog=True, profiles=PROFILES).decide("AUTO"), MODEL_KILLED, "unknown_model")
    ok = GateRig(catalog=True, profiles=PROFILES, auto_target="B")
    assert ok.decide("auto").routed_model == "b"
    bad = GateRig(catalog=True, profiles=PROFILES, auto_target="nope")
    _refused(bad.decide("auto"), MODEL_KILLED, "unknown_model")
    ok.engage(ks("model:b", feed_seq=1))  # kills match the substituted concrete model
    _refused(ok.decide("auto"), MODEL_KILLED, "model")


def test_a_dated_alias_kill_is_a_family_kill() -> None:
    rig = GateRig(aliases={"gpt-4o-2024": "gpt-4o"})
    rig.engage(ks("model:gpt-4o-2024", feed_seq=1))
    for raw in ("gpt-4o", "GPT-4o-2024", " gpt-4o "):
        _refused(rig.decide(raw), MODEL_KILLED, "model")


# -- isolation and reroute -------------------------------------------------------------------


def _isolated(rig: GateRig, **extra: object) -> None:
    rig.engage(ks("iso:a", body=iso_body(fallback="b", expires_at=9e9, **extra), feed_seq=1))


def test_an_eligible_fallback_reroutes_and_emits_one_event() -> None:
    rig = GateRig(catalog=True, profiles=PROFILES, constraints=KNOWN)
    _isolated(rig)
    v = rig.decide("A")
    assert (v.refuse_code, v.routed_model) == (None, "b")
    assert v.reroute is not None and v.reroute.outcome is RouteOutcome.REROUTE
    assert v.reroute.isolated_model == "a" and v.reroute.decision_source == "isolation"
    assert [e.kind for e in rig.events] == [IncidentEventKind.REROUTE]
    assert (rig.events[0].model, rig.events[0].fallback, rig.events[0].scope) == ("a", "b", "a")
    assert rig.metrics.series()["amf_incident_reroute_total"] == 1.0


def test_isolated_without_a_fallback_or_malformed_is_refused() -> None:
    rig = GateRig(catalog=True, profiles=PROFILES, constraints=KNOWN)
    rig.engage(ks("iso:a", body=iso_body(expires_at=9e9), feed_seq=1))
    _refused(rig.decide("a"), MODEL_ISOLATED_NO_FALLBACK, "isolated_no_fallback")
    rig.engage(ks("iso:a", body=iso_body(origin="bogus"), feed_seq=2))
    _refused(rig.decide("a"), MODEL_ISOLATED_NO_FALLBACK, "isolated_no_fallback")
    assert rig.decide("b").routed_model == "b"  # malformed refuses THAT model only
    assert rig.events == []


def test_unknown_constraints_refuse_the_reroute() -> None:
    rig = GateRig(catalog=True, profiles=PROFILES)  # interim default: constraints unknown
    _isolated(rig)
    _refused(rig.decide("a"), REROUTE_REFUSED, "reroute_refused")
    no_catalog = GateRig(constraints=KNOWN)
    _isolated(no_catalog)
    _refused(no_catalog.decide("a"), REROUTE_REFUSED, "reroute_refused")  # profiles missing


def test_a_fallback_that_is_killed_for_this_org_or_isolated_is_never_used() -> None:
    for killer in (ks("om:a:b", feed_seq=2), ks("model:b", feed_seq=2), ks("org:z", feed_seq=2)):
        rig = GateRig(catalog=True, profiles=PROFILES, constraints=KNOWN)
        _isolated(rig)
        rig.engage(killer)
        v = rig.decide("a")
        if killer.key == "org:z":
            assert v.routed_model == "b"  # another org's kill is irrelevant
        else:
            _refused(v, REROUTE_REFUSED, "reroute_refused")
    chain = GateRig(catalog=True, profiles=PROFILES, constraints=KNOWN)
    _isolated(chain)
    chain.engage(ks("iso:b", body=iso_body(fallback="a", expires_at=9e9), feed_seq=2))
    _refused(chain.decide("a"), REROUTE_REFUSED, "reroute_refused")  # single hop, no ping-pong


def test_an_expired_isolation_is_not_applied() -> None:
    rig = GateRig(catalog=True, profiles=PROFILES, constraints=KNOWN)
    rig.engage(ks("iso:a", body=iso_body(fallback="b", expires_at=rig.wall.now + 5), feed_seq=1))
    assert rig.decide("a").routed_model == "b"
    rig.wall.now += 6
    v = rig.decide("a")
    assert v.routed_model == "a" and v.reroute is None


def test_a_local_auto_isolation_reroutes_like_a_signed_one() -> None:
    from tests.admit._lgw29_support import local_auto

    rig = GateRig(catalog=True, profiles=PROFILES, constraints=KNOWN)
    assert rig.state.add_local(local_auto("a", rig.wall.now, fallback="b"))
    assert rig.decide("a").routed_model == "b"


# -- fail closed -----------------------------------------------------------------------------


def test_an_exception_inside_the_gate_refuses_with_gate_error_and_never_raises() -> None:
    rig = GateRig()

    class Boom:
        def __getattr__(self, name: str) -> object:
            raise RuntimeError("secret detail that must not be logged")

    rig.gate._state = Boom()  # type: ignore[assignment]
    v = rig.decide("m")
    _refused(v, KILL_SWITCH_UNAVAILABLE, "gate_error")
    assert rig.metrics.series()["amf_incident_refused_gate_error_total"] == 1.0
    rig.gate._switches = Boom()  # type: ignore[assignment]
    _refused(rig.decide("m"), KILL_SWITCH_UNAVAILABLE, "gate_error")
    _refused(rig.gate.recheck(org_id="a", model="m"), KILL_SWITCH_UNAVAILABLE, "gate_error")
    _refused(rig.gate.decide_org(org_id="a", request_id="r"), KILL_SWITCH_UNAVAILABLE, "gate_error")


def test_a_failing_emitter_never_changes_the_outcome() -> None:
    rig = GateRig(catalog=True, profiles=PROFILES, constraints=KNOWN)

    def broken(_event: object) -> None:
        raise RuntimeError("pump down")

    rig.gate._emit = broken  # type: ignore[method-assign, assignment]
    _isolated(rig)
    assert rig.decide("a").routed_model == "b"


# -- recheck ---------------------------------------------------------------------------------


def test_recheck_passes_unchanged_and_refuses_with_the_underlying_code() -> None:
    rig = GateRig()
    assert rig.gate.recheck(org_id="a", model="m").routed_model == "m"
    rig.engage(ks("model:m", feed_seq=1))
    _refused(rig.gate.recheck(org_id="a", model="m"), MODEL_KILLED, "recheck")
    rig.engage(ks("org:a", feed_seq=2))
    _refused(rig.gate.recheck(org_id="a", model="m"), KILL_SWITCH_ENGAGED, "recheck")
    rig.mono.now += 60.0
    _refused(rig.gate.recheck(org_id="a", model="m"), KILL_SWITCH_UNAVAILABLE, "recheck")
    assert rig.metrics.series()["amf_incident_refused_recheck_total"] == 3.0


def test_recheck_refuses_a_model_isolated_since_routing_and_never_reroutes() -> None:
    rig = GateRig(catalog=True, profiles=PROFILES, constraints=KNOWN)
    _isolated(rig)
    v = rig.gate.recheck(org_id="a", model="a")
    _refused(v, MODEL_ISOLATED_NO_FALLBACK, "recheck")
    assert rig.gate.recheck(org_id="a", model="b").routed_model == "b"


# -- org-only gate ---------------------------------------------------------------------------


def test_decide_org_checks_freshness_global_and_org_only() -> None:
    rig = GateRig()
    assert rig.gate.decide_org(org_id="a", request_id="r").refuse_code is None
    rig.engage(ks("model:m", feed_seq=1), ks("om:a:m", feed_seq=2))
    assert rig.gate.decide_org(org_id="a", request_id="r").refuse_code is None
    rig.engage(ks("org:a", feed_seq=3))
    _refused(rig.gate.decide_org(org_id="a", request_id="r"), KILL_SWITCH_ENGAGED, "org")
    assert rig.gate.decide_org(org_id="b", request_id="r").refuse_code is None
    rig.engage(ks("global", feed_seq=4))
    _refused(rig.gate.decide_org(org_id="b", request_id="r"), KILL_SWITCH_ENGAGED, "global")
    rig.mono.now += 60.0
    _refused(rig.gate.decide_org(org_id="b", request_id="r"), KILL_SWITCH_UNAVAILABLE, "stale")


def test_every_cause_the_gate_can_produce_is_in_the_closed_vocabulary() -> None:
    rig = GateRig(catalog=True, profiles=PROFILES)
    rng = random.Random(3)
    causes = set()
    for _ in range(50):
        rig.engage(*[ks(rng.choice(["global", "org:a", "model:a", "om:a:b"]), on=rng.random() < 0.5,
                        feed_seq=n + 1) for n in range(2)])
        v = rig.decide(rng.choice(["a", "b", "zz", ""]))
        causes.add(v.cause)
    assert causes - {None} <= set(INCIDENT_CAUSES)
    assert isinstance(MappingProxyType({}), MappingProxyType)
