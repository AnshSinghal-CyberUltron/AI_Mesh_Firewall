# Validates: AC9, 4.6 (the canonical routed model reaches the provider; request id unchanged)
"""What phase 4b checked is exactly what the provider-open body carries."""

from __future__ import annotations

import asyncio

import pytest

from gateway_v2.dispatch.openai_upstream import BODY_OPENAI_KEY
from gateway_v2.domain.incident import ModelProfile, RequestConstraints, RerouteNote
from tests.admit._lgw29_support import iso_body, ks
from tests.edge._lgw29_app import Harness, chat

TAGS = frozenset({"t"})
PROFILES = {"a": ModelProfile("a", "p", TAGS, 2), "b": ModelProfile("b", "p", TAGS, 2)}
KNOWN = RequestConstraints(True, None, frozenset(), 0)


def _sent_model(h: Harness) -> object:
    body = h.provider.bodies[-1][BODY_OPENAI_KEY]
    return body["model"]


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("raw", ["GPT-Test", "gpt-test-2024-01"])
def test_case_and_alias_variants_send_the_canonical_name(raw: str, stream: bool) -> None:
    h = Harness(aliases={"gpt-test-2024-01": "gpt-test"})
    result = asyncio.run(h.call("/v1/chat/completions", chat(raw, stream=stream)))
    assert result.status == 200
    assert _sent_model(h) == "gpt-test"


def test_the_request_id_is_still_derived_from_the_client_string() -> None:
    a, b = Harness(), Harness()
    asyncio.run(a.call("/v1/chat/completions", chat("gpt-test")))
    asyncio.run(b.call("/v1/chat/completions", chat("GPT-TEST")))
    ids_a = {r.request_id for r in a.records}
    ids_b = {r.request_id for r in b.records}
    assert ids_a and ids_b and ids_a != ids_b  # two case variants, two opaque correlation ids


@pytest.mark.parametrize("stream", [False, True])
def test_a_rerouted_request_carries_the_fallback_and_is_recorded(stream: bool) -> None:
    h = Harness(rig={"catalog": True, "profiles": PROFILES, "constraints": KNOWN})
    h.rig.engage(ks("iso:a", body=iso_body(fallback="b", expires_at=9e9), feed_seq=1))
    result = asyncio.run(h.call("/v1/chat/completions", chat("A", stream=stream)))
    assert result.status == 200 and h.provider.open_calls == 1
    assert _sent_model(h) == "b"
    if not stream:
        assert result.headers["x-amf-routing-reason"] == "isolation:a->b"
    routed = [r for r in h.records if r.outcome == "rerouted"]
    assert len(routed) == 1 and routed[0].detail["from"] == "a" and routed[0].detail["to"] == "b"
    others = [r for r in h.records if r.outcome != "rerouted"]
    assert others and all(r.detail["routing"]["fallback"] == "b" for r in others)
    assert all(r.detail["routing"]["isolated_model"] == "a" for r in others)


def test_a_request_that_was_not_rerouted_has_no_routing_provenance() -> None:
    h = Harness()
    result = asyncio.run(h.call("/v1/chat/completions", chat()))
    assert "x-amf-routing-reason" not in result.headers
    assert all("routing" not in r.detail and r.outcome != "rerouted" for r in h.records)
    assert RerouteNote("a", "b", "a", "isolation", "isolated_fallback").routed == "b"
