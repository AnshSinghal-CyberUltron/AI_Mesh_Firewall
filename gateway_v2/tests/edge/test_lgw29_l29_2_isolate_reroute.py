# Feature: gw29-killswitch-isolation, Property 4: an isolated model is never called (L29-2 local)
# Validates: Requirements 4.1, 4.2, AC8, AC9 (zero provider calls to M after the isolate applies)
"""Local equivalent of L29-2: isolate model A with fallback B and watch the provider ledger."""

from __future__ import annotations

import asyncio

import pytest

from gateway_v2.dispatch.openai_upstream import BODY_OPENAI_KEY
from gateway_v2.domain.incident import ModelProfile, RequestConstraints
from tests.admit._lgw29_support import iso_body, ks
from tests.edge._lgw29_app import ENDPOINTS, Harness, chat

TAGS = frozenset({"t"})
PROFILES = {"a": ModelProfile("a", "p", TAGS, 2), "b": ModelProfile("b", "p", TAGS, 2)}
KNOWN = RequestConstraints(True, None, frozenset(), 0)


def _harness() -> Harness:
    return Harness(rig={"catalog": True, "profiles": PROFILES, "constraints": KNOWN,
                        "fallbacks": {"a": "b"}})


def _models(h: Harness) -> list[object]:
    return [b[BODY_OPENAI_KEY]["model"] for b in h.provider.bodies]


@pytest.mark.parametrize("stream", [False, True])
def test_zero_calls_reach_the_isolated_model_and_traffic_moves(stream: bool) -> None:
    h = _harness()
    before = asyncio.run(h.call("/v1/chat/completions", chat("a", stream=stream)))
    assert before.status == 200 and _models(h) == ["a"]
    h.rig.engage(ks("iso:a", body=iso_body(fallback="b", expires_at=9e9), feed_seq=1))
    for _ in range(25):
        assert asyncio.run(h.call("/v1/chat/completions", chat("A", stream=stream))).status == 200
    assert _models(h)[1:] == ["b"] * 25, "no call may name the isolated model after the apply"
    assert sum(1 for m in _models(h) if m == "a") == 1


def test_every_endpoint_reroutes_and_none_calls_the_isolated_model() -> None:
    for path, build in ENDPOINTS:
        h = _harness()
        h.rig.engage(ks("iso:a", body=iso_body(fallback="b", expires_at=9e9), feed_seq=1))
        result = asyncio.run(h.call(path, build("a")))
        assert result.status == 200, path
        assert "a" not in _models(h), path


def test_an_isolation_without_a_usable_fallback_refuses_instead_of_calling_the_model() -> None:
    h = Harness(rig={"catalog": True, "profiles": PROFILES, "constraints": KNOWN})
    h.rig.engage(ks("iso:a", body=iso_body(expires_at=9e9), feed_seq=1))
    result = asyncio.run(h.call("/v1/chat/completions", chat("a")))
    assert result.status == 503 and h.provider.open_calls == 0


def test_lifting_the_isolation_returns_traffic_to_the_model() -> None:
    h = _harness()
    h.rig.engage(ks("iso:a", body=iso_body(fallback="b", expires_at=9e9), feed_seq=1))
    assert asyncio.run(h.call("/v1/chat/completions", chat("a"))).status == 200
    h.rig.engage(ks("iso:a", on=False, feed_seq=2))
    assert asyncio.run(h.call("/v1/chat/completions", chat("a"))).status == 200
    assert _models(h) == ["b", "a"]
