# Feature: gw29-killswitch-isolation, Property 1: an engaged applicable scope is never admitted
# Feature: gw29-killswitch-isolation, Property 2: other orgs and other models are unaffected
# Validates: Requirements 1.1, 1.2, 1.4 (kill precedence, scope isolation)
"""P1 and P2 over random engaged scope sets and random requests."""

from __future__ import annotations

import random

from gateway_v2.domain.posture import KILL_SWITCH_ENGAGED, MODEL_KILLED
from tests.admit._lgw29_support import GateRig, ks

SEED = 29001
ITERATIONS = 10_000
ORGS, MODELS = ("a", "b", "c"), ("m1", "m2", "m3")
SCOPES = (["global"] + [f"org:{o}" for o in ORGS] + [f"model:{m}" for m in MODELS]
          + [f"om:{o}:{m}" for o in ORGS for m in MODELS])


def _applicable(engaged: set[str], org: str, model: str) -> str | None:
    if "global" in engaged or f"org:{org}" in engaged or f"om:{org}:{model}" in engaged:
        return KILL_SWITCH_ENGAGED
    if f"model:{model}" in engaged:
        return MODEL_KILLED
    return None


def test_property_1_and_2_gate_matches_the_scope_oracle() -> None:
    rng = random.Random(SEED)
    rig = GateRig()
    admitted = refused = 0
    for i in range(ITERATIONS):
        engaged = {s for s in SCOPES if rng.random() < 0.12}
        feed = [ks(s, on=s in engaged, feed_seq=n + 1) for n, s in enumerate(SCOPES)]
        rig.engage(*feed)
        org, model = rng.choice(ORGS), rng.choice(MODELS)
        verdict = rig.decide(model, org)
        expected = _applicable(engaged, org, model)
        tag = f"seed={SEED} i={i} org={org} model={model} engaged={sorted(engaged)}"
        if expected is None:
            admitted += 1
            assert verdict.refuse_code is None and verdict.routed_model == model, (
                f"{tag} P2: an unaffected request was refused ({verdict.cause})")
        else:
            refused += 1
            assert verdict.refuse_code == expected, f"{tag} P1: engaged scope not enforced"
            assert verdict.routed_model is None, f"{tag} P1: admitted while engaged"
    assert admitted > 1_000 and refused > 1_000


def test_property_2_engaging_one_org_changes_nothing_for_another() -> None:
    rng = random.Random(SEED + 1)
    rig = GateRig()
    for i in range(1_000):
        victim, bystander = rng.sample(ORGS, 2)
        model = rng.choice(MODELS)
        rig.engage(*[ks(s, on=False, feed_seq=n + 1) for n, s in enumerate(SCOPES)])
        before = rig.decide(model, bystander)
        rig.engage(ks(f"org:{victim}", feed_seq=100), ks(f"om:{victim}:{model}", feed_seq=101))
        after = rig.decide(model, bystander)
        same = (before.refuse_code, before.routed_model) == (after.refuse_code, after.routed_model)
        assert same, f"seed={SEED + 1} i={i}"
