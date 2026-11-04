# Feature: gw29-killswitch-isolation, Property 4: never names a killed or isolated model
# Feature: gw29-killswitch-isolation, Property 5: a reroute never lowers compliance/sensitivity
# Validates: Requirements 4.1, 4.2, 4.3, 4.4
"""P4 and P5 over random inputs (seeded `random.Random`, no hypothesis)."""

from __future__ import annotations

import random

from gateway_v2.dispatch.incident_routing import decide_route
from gateway_v2.domain.incident import RouteOutcome
from tests.dispatch._lgw29_support import constraints, iso, profile, status

SEED = 29004
ITERATIONS = 10_000
_MODELS = ("m0", "m1", "m2", "m3")
_TAGS = ("hipaa", "pci", "gdpr", "sox")


def _rand_tags(rng: random.Random) -> frozenset[str]:
    return frozenset(t for t in _TAGS if rng.random() < 0.5)


def _case(rng: random.Random):
    req = rng.choice(_MODELS)
    fb = rng.choice((*_MODELS, None))
    req_iso = iso(req, fb, malformed=rng.random() < 0.1) if rng.random() < 0.8 else None
    req_status = status(req, killed=rng.random() < 0.15, isolation=req_iso)
    fb_status = None
    if fb is not None and rng.random() < 0.95:
        fb_iso = iso(fb, rng.choice(_MODELS)) if rng.random() < 0.2 else None
        fb_status = status(fb, killed=rng.random() < 0.2, isolation=fb_iso)
    p_req = profile(req, _rand_tags(rng), rng.randint(0, 4)) if rng.random() < 0.9 else None
    p_fb = profile(fb or "x", _rand_tags(rng), rng.randint(0, 4)) if rng.random() < 0.9 else None
    allowed = None if rng.random() < 0.6 else frozenset(m for m in _MODELS if rng.random() < 0.6)
    cons = constraints(known=rng.random() < 0.85, allowed=allowed,
                       required=_rand_tags(rng) if rng.random() < 0.3 else frozenset(),
                       rank=rng.randint(0, 4))
    return req, req_status, fb_status, p_req, p_fb, cons


def test_property_4_never_names_killed_or_isolated_and_is_deterministic() -> None:
    rng = random.Random(SEED)
    for i in range(ITERATIONS):
        req, req_status, fb_status, p_req, p_fb, cons = _case(rng)
        d = decide_route(req, req_status, fb_status, p_req, p_fb, cons)
        again = decide_route(req, req_status, fb_status, p_req, p_fb, cons)
        assert d == again, f"seed={SEED} i={i} not deterministic"
        if d.outcome is RouteOutcome.DIRECT:
            assert d.routed_model == req and not req_status.killed, f"seed={SEED} i={i}"
            assert req_status.isolation is None, f"seed={SEED} i={i} direct to an isolated model"
        elif d.outcome is RouteOutcome.REROUTE:
            assert fb_status is not None and d.routed_model == fb_status.model, f"seed={SEED} i={i}"
            assert not fb_status.killed and fb_status.isolation is None, f"seed={SEED} i={i}"
            assert not req_status.killed and d.routed_model != req, f"seed={SEED} i={i}"
        else:
            assert d.routed_model is None, f"seed={SEED} i={i} refusal named a model"


def test_property_5_reroute_never_lowers_compliance_or_sensitivity() -> None:
    rng = random.Random(SEED + 1)
    reroutes = 0
    for i in range(ITERATIONS):
        req, req_status, fb_status, p_req, p_fb, cons = _case(rng)
        d = decide_route(req, req_status, fb_status, p_req, p_fb, cons)
        if d.outcome is RouteOutcome.REROUTE:
            reroutes += 1
            assert cons.known and p_req is not None and p_fb is not None, f"seed={SEED+1} i={i}"
            assert p_fb.compliance_tags >= p_req.compliance_tags | cons.required_tags, f"i={i}"
            assert p_fb.sensitivity_ceiling >= max(p_req.sensitivity_ceiling,
                                                   cons.sensitivity_rank), f"i={i}"
            assert cons.allowed_models is None or d.routed_model in cons.allowed_models, f"i={i}"
        if not cons.known and req_status.isolation is not None and not req_status.killed:
            assert d.outcome is not RouteOutcome.REROUTE, f"seed={SEED+1} i={i} unknown constraints"
    assert reroutes > 100, "the generator must actually exercise the reroute path"
