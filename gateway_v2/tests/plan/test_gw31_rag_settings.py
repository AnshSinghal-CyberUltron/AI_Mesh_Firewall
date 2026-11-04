"""GW31 plan-layer RAG settings: parse, compile, document round trip, request-time clamp."""
from __future__ import annotations

import json
import random

import pytest

from gateway_v2.domain.rag import RagSettings
from gateway_v2.plan.compiler import CompileError, compile_plan
from gateway_v2.plan.document import PlanDocument, decode_plan_body, encode_plan_body
from gateway_v2.plan.rag_settings import effective_settings, encode_rag_settings, parse_rag_settings
from gateway_v2.domain.plan import StreamingMode
from tests.runtime.test_gw17_mcp_bounds import _contract


def _plan(settings: dict[str, object] | None):  # type: ignore[no-untyped-def]
    return compile_plan("org", (), compiled_at=0.0, rag_settings=settings)


def test_default_hash_unchanged() -> None:
    assert _plan(None).content_hash == _plan({}).content_hash
    assert _plan(None).rag == RagSettings()


def test_non_default_changes_hash_and_sets_rag() -> None:
    plan = _plan({"rag_enabled": False})
    assert plan.rag.rag_enabled is False
    assert plan.content_hash != _plan(None).content_hash


@pytest.mark.parametrize(
    "bad",
    [
        {"typo": 1},
        {"rag_enabled": 1},
        {"vector_db_isolation": "tenant"},
        {"rag_max_documents": 0},
        {"rag_max_documents": True},
        {"rag_relevance_threshold": float("nan")},
        {"rag_relevance_threshold": 1.5},
        {"rag_relevance_threshold": True},
        {"rag_redaction_enabled": "yes"},
        {"rag_tier2_enabled": None},
    ],
)
def test_invalid_settings_raise(bad: dict[str, object]) -> None:
    with pytest.raises(CompileError):
        parse_rag_settings(bad)
    with pytest.raises(CompileError):
        _plan(bad)


def test_clamp_down_at_request_time() -> None:
    c = _contract()
    ceiling = c.rag_max_documents_ceiling()
    eff = effective_settings(_plan({"rag_max_documents": ceiling + 50}), c)
    assert eff.rag_max_documents == ceiling and eff.settings_clamped
    low = effective_settings(_plan({"rag_max_documents": 1}), c)
    assert low.rag_max_documents == 1 and not low.settings_clamped and low.top_k == 1
    dflt = effective_settings(_plan(None), c)
    assert dflt.rag_relevance_threshold == c.rag_default_relevance_threshold()


def test_plan_from_record_honours_disabled() -> None:
    from gateway_v2.plan.delta import plan_from_record

    doc = PlanDocument("org", StreamingMode.INCREMENTAL, (), {"rag_enabled": False})
    raw = json.dumps(encode_plan_body(doc)).encode()
    assert decode_plan_body(raw).rag_settings == {"rag_enabled": False}
    from tests.plan.test_lgw05c_delta import _plan_record

    record = _plan_record("org", seq=1, feed_seq=1, body=encode_plan_body(doc))
    assert plan_from_record(record, compiled_at=0.0).rag.rag_enabled is False


# Feature: gw31-rag-pipeline, Property 7: settings round trip is lossless
def test_property_round_trip() -> None:
    rng = random.Random(7)
    for _ in range(1000):
        raw: dict[str, object] = {}
        if rng.random() < 0.7:
            raw["rag_enabled"] = rng.random() < 0.5
        if rng.random() < 0.7:
            raw["vector_db_isolation"] = rng.choice(["project", "org"])
        if rng.random() < 0.7:
            raw["rag_max_documents"] = rng.randint(1, 500)
        if rng.random() < 0.7:
            raw["rag_relevance_threshold"] = rng.random()
        if rng.random() < 0.7:
            raw["rag_redaction_enabled"] = rng.random() < 0.5
        if rng.random() < 0.7:
            raw["rag_tier2_enabled"] = rng.random() < 0.5
        parsed = parse_rag_settings(raw)
        encoded = encode_rag_settings(parsed)
        assert parse_rag_settings(encoded) == parsed
        doc = PlanDocument("o", StreamingMode.INCREMENTAL, (), encoded)
        back = decode_plan_body(json.dumps(encode_plan_body(doc)).encode())
        assert parse_rag_settings(back.rag_settings) == parsed
