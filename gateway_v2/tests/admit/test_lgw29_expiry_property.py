# Feature: gw29-killswitch-isolation, Property 6: the gate and snapshot_models agree on expiry
# Validates: Requirements 3.3, 3.4, 6.1 (one expiry decision under clock jumps and bad deadlines)
"""P6: random clock jumps and missing/malformed `expires_at`; gate and read model never disagree."""

from __future__ import annotations

import random

from gateway_v2.domain.incident import IsolationOrigin
from tests.admit._lgw29_support import Clock, iso_body, ks, load, make_state, snapshot

SEED = 29006
ITERATIONS = 10_000
AUTO_S, HOLD_S = 300.0, 600.0
_BAD_EXPIRY = (None, "soon", 0, -1, True)  # NaN/inf cannot be signed: covered in the parser tests


def _body(rng: random.Random, wall: float) -> dict[str, object]:
    body: dict[str, object] = {"fallback": "fb"}
    if rng.random() < 0.5:
        body["origin"] = "auto"
        if rng.random() < 0.7:
            body["created_at"] = wall - rng.uniform(0, 400)
        if rng.random() < 0.6:
            body["expires_at"] = wall + rng.uniform(-100, 500)
        elif rng.random() < 0.7:
            body["expires_at"] = rng.choice(_BAD_EXPIRY)
    else:
        mode = rng.random()
        if mode < 0.6:
            body["expires_at"] = wall + rng.uniform(-100, 500)
        elif mode < 0.8:
            body.update(expires_at=None, no_expiry=True)
        # else: neither -> malformed
    return body


def test_property_6_gate_and_read_model_agree_and_expired_is_never_active() -> None:
    rng = random.Random(SEED)
    for i in range(ITERATIONS):
        wall, mono = Clock(rng.uniform(1e3, 1e6)), Clock(rng.uniform(0, 1e5))
        snap = snapshot(mono)
        state = make_state(snap, wall, mono, auto_s=AUTO_S, hold_s=HOLD_S)
        models = [f"m{j}" for j in range(rng.randint(1, 4))]
        records = [ks(f"iso:{m}", body=iso_body(**_body(rng, wall.now)), feed_seq=j + 1)
                   for j, m in enumerate(models)]
        load(snap, records)
        loaded_at = mono.now
        for m in models:
            state.active_isolation(m)  # first seen at load time
        for step in range(rng.randint(1, 6)):
            wall.now += rng.choice((-500.0, -1.0, 0.0, 1.0, 60.0, 400.0, 1e5))
            mono.now += rng.choice((0.0, 1.0, 60.0, 299.0, 301.0, 700.0))
            visible = {v.model: v for v in state.snapshot_models()}
            for m in models:
                rec = state.active_isolation(m)
                tag = f"seed={SEED} i={i} step={step} model={m}"
                assert (rec is not None) == (m in visible), f"{tag} gate/read model disagree"
                signed = snap.isolation_record(m)
                assert signed is not None
                if rec is None:
                    continue
                if signed.malformed:
                    continue
                exp = signed.expires_at
                deadline_ok = exp is not None and exp > 0 and exp == exp and exp != float("inf")
                if deadline_ok and not signed.no_expiry:
                    assert wall.now < exp, f"{tag} active past its deadline"  # type: ignore[operator]
                if signed.origin is IsolationOrigin.AUTO:
                    assert mono.now - loaded_at < HOLD_S, f"{tag} auto outlived the hold cap"
