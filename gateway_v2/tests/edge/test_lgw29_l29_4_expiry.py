# Feature: gw29-killswitch-isolation, Property 6: expiry has one decision (L29-4 local)
# Validates: Requirements 4.4, AC11 (request path and read model agree within one refresh)
"""Local equivalent of L29-4: ``expires_at`` ends an isolation for gate and read model together."""

from __future__ import annotations

import asyncio

from gateway_v2.dispatch.openai_upstream import BODY_OPENAI_KEY
from gateway_v2.domain.incident import ModelProfile, RequestConstraints
from tests.admit._lgw29_support import iso_body, ks
from tests.edge._lgw29_app import Harness, chat

TAGS = frozenset({"t"})
PROFILES = {"a": ModelProfile("a", "p", TAGS, 2), "b": ModelProfile("b", "p", TAGS, 2)}
KNOWN = RequestConstraints(True, None, frozenset(), 0)


def _routed(h: Harness) -> object:
    n = h.provider.open_calls
    assert asyncio.run(h.call("/v1/chat/completions", chat("a"))).status == 200
    assert h.provider.open_calls == n + 1
    return h.provider.bodies[-1][BODY_OPENAI_KEY]["model"]


def test_the_gate_and_the_read_model_agree_at_every_step_around_expiry() -> None:
    h = Harness(rig={"catalog": True, "profiles": PROFILES, "constraints": KNOWN,
                     "fallbacks": {"a": "b"}})
    rig = h.rig
    expires = rig.wall.now + 60.0
    rig.engage(ks("iso:a", body=iso_body(fallback="b", expires_at=expires), feed_seq=1))
    steps = (0.0, 30.0, 59.0, 60.5, 61.0, 120.0)
    for step in steps:
        rig.wall.now = 1_000.0 + step
        rig.mono.now = 100.0 + step
        # keep the snapshot fresh: re-apply the same signed round (the refresh)
        rig.engage(ks("iso:a", body=iso_body(fallback="b", expires_at=expires), feed_seq=1))
        in_read_model = any(v.model == "a" for v in rig.state.snapshot_models())
        gate_isolated = _routed(h) == "b"
        assert in_read_model == gate_isolated, f"step={step}"
        assert gate_isolated == (rig.wall.now < expires), f"step={step}"


def test_an_isolation_with_no_expiry_field_is_malformed_and_refuses_that_model_only() -> None:
    h = Harness(rig={"catalog": True, "profiles": PROFILES, "constraints": KNOWN,
                     "fallbacks": {"a": "b"}})
    h.rig.engage(ks("iso:a", body=iso_body(fallback="b", origin="bogus"), feed_seq=1))
    assert asyncio.run(h.call("/v1/chat/completions", chat("a"))).status == 503
    assert asyncio.run(h.call("/v1/chat/completions", chat("b"))).status == 200


def test_a_signed_no_expiry_isolation_is_permanent() -> None:
    h = Harness(rig={"catalog": True, "profiles": PROFILES, "constraints": KNOWN,
                     "fallbacks": {"a": "b"}})
    h.rig.engage(ks("iso:a", body=iso_body(fallback="b", no_expiry=True), feed_seq=1))
    h.rig.wall.now += 86_400.0
    h.rig.engage(ks("iso:a", body=iso_body(fallback="b", no_expiry=True), feed_seq=1))
    assert _routed(h) == "b"
