"""Regression: /v1/vector/query must pass a DICT policy to the RAG pipeline.

Root cause (fixed): the handler passed ``POLICY_SYNC.get_policies(org_slug)``
(a ``list[dict]`` of compiled entries) straight into
``RAGFirewallPipeline.execute(policy=...)``, which treats ``policy`` as a flat
dict. A non-empty list is truthy so ``policy or {}`` kept the list and the first
``.get`` raised ``AttributeError: 'list' object has no attribute 'get'`` (→ 500);
an empty list collapsed to ``{}`` and dropped the org slug. The fix builds a
dict via ``_build_rag_policy`` (sets ``_org_slug`` + merges per-org guardrail
config), mirroring /v1/rag/query.
"""
import types

import pytest

import vector_routes
from rag_pipeline.query_stage import QueryStage
from rag_pipeline.contracts import QueryStageInput


def test_build_rag_policy_is_always_a_dict_with_org_slug(monkeypatch):
    monkeypatch.setattr(vector_routes, "_gateway_main_module", lambda: None)
    pol = vector_routes._build_rag_policy("acme")
    assert isinstance(pol, dict)
    assert pol["_org_slug"] == "acme"


def test_build_rag_policy_merges_only_guardrail_keys(monkeypatch):
    class _CS:
        def get_config(self, slug):
            return {
                "prompt_injection_threshold": 0.6,
                "input_scan_enabled": False,
                "unrelated_key": 123,
            }

    fake_main = types.SimpleNamespace(CONFIG_SYNC=_CS())
    monkeypatch.setattr(vector_routes, "_gateway_main_module", lambda: fake_main)
    pol = vector_routes._build_rag_policy("acme")
    assert pol["_org_slug"] == "acme"
    assert pol["prompt_injection_threshold"] == 0.6
    assert pol["input_scan_enabled"] is False
    assert "unrelated_key" not in pol  # only the allowlisted guardrail keys flow


def test_build_rag_policy_failopen_when_config_sync_missing(monkeypatch):
    fake_main = types.SimpleNamespace()  # no CONFIG_SYNC attr
    monkeypatch.setattr(vector_routes, "_gateway_main_module", lambda: fake_main)
    pol = vector_routes._build_rag_policy("acme")
    assert pol == {"_org_slug": "acme"}


def test_build_rag_policy_empty_slug(monkeypatch):
    monkeypatch.setattr(vector_routes, "_gateway_main_module", lambda: None)
    assert vector_routes._build_rag_policy("") == {"_org_slug": ""}


async def test_built_policy_does_not_crash_pipeline_stage(monkeypatch):
    # The old list-shaped policy raised AttributeError here; the dict must not.
    monkeypatch.setattr(vector_routes, "_gateway_main_module", lambda: None)
    pol = vector_routes._build_rag_policy("acme")
    stage = QueryStage(None, config={})
    inp = QueryStageInput(
        query_text="hello world", collection_name="docs", project_id="org1",
        vector_db_type="pinecone", n_results=5, where_filter=None,
        namespace="", policy=pol, key_hash="",
    )
    out = await stage.execute(inp)  # must not raise
    assert out.verdict.action in ("allow", "flag")


async def test_list_policy_would_have_crashed_documents_contract():
    # Pins the pipeline contract that made the old code buggy: a list policy is
    # unusable. This is why _build_rag_policy must return a dict.
    stage = QueryStage(None, config={})
    inp = QueryStageInput(
        query_text="hello", collection_name="docs", project_id="org1",
        vector_db_type="pinecone", n_results=5, where_filter=None,
        namespace="", policy=[{"policy_id": "p1"}], key_hash="",
    )
    with pytest.raises(AttributeError):
        await stage.execute(inp)
