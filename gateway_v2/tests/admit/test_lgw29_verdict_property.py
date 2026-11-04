# Feature: gw29-killswitch-isolation, Property 15: GateVerdict exactly-one invariant
# Validates: Requirements 7.1, 10 (one verdict shape; one counter per refusal)
"""P15 over random gate inputs, including garbage."""

from __future__ import annotations

import random

from gateway_v2.domain.incident import (
    INCIDENT_CAUSES,
    GateVerdict,
    ModelProfile,
    RequestConstraints,
)
from gateway_v2.domain.posture import MIN_RETRY_AFTER_S
from tests.admit._lgw29_support import GateRig, iso_body, ks

SEED = 29015
ITERATIONS = 5_000
_NAMES = ("m1", "m2", "M1 ", "auto", "", "  ", "bad name", "é", None, 7, "x" * 200, "m\x00")
_SCOPES = ("global", "org:a", "model:m1", "om:a:m2")
PROFILES = {n: ModelProfile(n, "p", frozenset({"t"}), 2) for n in ("m1", "m2")}


def test_property_15_every_gate_path_returns_a_well_formed_verdict_and_counts_refusals() -> None:
    rng = random.Random(SEED)
    for i in range(ITERATIONS):
        catalog = rng.random() < 0.5
        rig = GateRig(catalog=catalog, profiles=PROFILES,
                      constraints=RequestConstraints(rng.random() < 0.7, None, frozenset(), 0),
                      auto_target=rng.choice((None, "m1", "zz")), load_empty=rng.random() < 0.9)
        records = [ks(s, feed_seq=n + 1) for n, s in enumerate(_SCOPES) if rng.random() < 0.2]
        if rng.random() < 0.4:
            fb = rng.choice(("m1", "m2", None))
            records.append(ks("iso:m1", body=iso_body(expires_at=9e9, **({"fallback": fb}
                                                                          if fb else {})),
                              feed_seq=10))
        if rng.random() < 0.9:
            rig.engage(*records)
        verdicts: list[GateVerdict] = []
        for _ in range(3):
            name = rng.choice(_NAMES)
            verdicts.append(rig.decide(name, rng.choice("ab")))  # type: ignore[arg-type]
            verdicts.append(rig.gate.recheck(org_id="a", model=rng.choice(("m1", "m2"))))
            verdicts.append(rig.gate.decide_org(org_id="b", request_id="r"))
        refused = 0
        for v in verdicts:
            tag = f"seed={SEED} i={i}"
            assert (v.refuse_code is None) != (v.routed_model is None), f"{tag} not exactly one"
            if v.refuse_code is not None:
                refused += 1
                assert v.cause in INCIDENT_CAUSES and v.reroute is None, tag
                assert v.retry_after_s is not None and v.retry_after_s >= MIN_RETRY_AFTER_S, tag
                assert v.cause != "gate_error", f"{tag} the gate itself raised"
            else:
                assert v.cause is None and v.retry_after_s is None and v.routed_model, tag
        counted = sum(v for k, v in rig.metrics.series().items()
                      if "_refused_" in k and k.endswith("_total"))
        assert counted == refused, f"seed={SEED} i={i} counters {counted} != refusals {refused}"
