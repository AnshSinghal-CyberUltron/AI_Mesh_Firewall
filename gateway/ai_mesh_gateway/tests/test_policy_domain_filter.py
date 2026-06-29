"""Gateway compiled-policy domain filtering."""

from __future__ import annotations

from ai_mesh_gateway.policy_sync import filter_policies_by_domain


def _entry(code: str, domain: str) -> dict:
    return {"policy": {"code": code, "policy_domain": domain}, "rules": []}


def test_filter_policies_by_domain_excludes_wrong_domain():
    bundle = [
        _entry("PIPE_A", "pipeline"),
        _entry("RAG_A", "rag"),
        _entry("MCP_A", "mcp"),
    ]
    pipeline_only = filter_policies_by_domain(bundle, "pipeline")
    assert [e["policy"]["code"] for e in pipeline_only] == ["PIPE_A"]


def test_filter_policies_by_domain_maps_legacy_global_to_pipeline():
    bundle = [_entry("LEGACY", "global"), _entry("RAG_A", "rag")]
    pipeline_only = filter_policies_by_domain(bundle, "pipeline")
    assert [e["policy"]["code"] for e in pipeline_only] == ["LEGACY"]
