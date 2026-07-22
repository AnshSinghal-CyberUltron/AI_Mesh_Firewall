"""RAG pipeline view normalization for demo UI."""
from __future__ import annotations

from app.pipeline import build_rag_pipeline_view
from app.status_reason import REASON_RAG_VECTOR_UNAVAILABLE, attach_status_reason, derive_status_reason


def test_build_rag_pipeline_view_maps_audit_stages():
    audit = {
        "request_id": "rag-req-1",
        "final_action": "allow",
        "total_latency_ms": 42.5,
        "stages": [
            {"name": "query", "action": "allow", "latency_ms": 10.0, "threat_type": ""},
            {"name": "retriever", "action": "allow", "latency_ms": 32.5, "docs_out": 2},
        ],
    }
    view = build_rag_pipeline_view(zeroshield={"code": ""}, pipeline_audit=audit)
    assert view["action"] == "allow"
    assert view["request_id"] == "rag-req-1"
    assert view["pipeline_kind"] == "rag"
    assert len(view["stages"]) == 4
    assert view["stages"][0]["action"] == "allow"
    assert view["stages"][1]["action"] == "allow"
    assert view["stages"][2]["action"] == "skip"


def test_build_rag_pipeline_view_blocked_retriever():
    audit = {
        "request_id": "rag-req-2",
        "final_action": "block",
        "stages": [
            {"name": "query", "action": "allow", "latency_ms": 8.0},
            {"name": "retriever", "action": "block", "latency_ms": 3.0, "threat_type": "retrieval_error"},
        ],
    }
    zs = {
        "code": "rag_pipeline_blocked",
        "message": "RAG pipeline blocked at retriever: Vector retrieval failed.",
        "detail": "Vector retrieval failed.",
        "pipeline_stage": "retriever",
        "blocked_at_stage": "retriever",
        "action": "block",
    }
    view = build_rag_pipeline_view(zeroshield=zs, pipeline_audit=audit)
    assert view["action"] == "block"
    assert view["blocked_by"] == "retriever"
    assert view["stages"][1]["action"] == "block"


def test_rag_blocked_status_reason_is_vector_unavailable():
    audit = {
        "stages": [
            {"name": "query", "action": "allow", "latency_ms": 1.0},
            {"name": "retriever", "action": "block", "latency_ms": 2.0},
        ]
    }
    zs = {
        "code": "rag_pipeline_blocked",
        "message": "RAG pipeline blocked at retriever: Vector retrieval failed.",
        "detail": "Vector retrieval failed.",
        "pipeline_stage": "retriever",
        "action": "block",
    }
    pipeline = build_rag_pipeline_view(zeroshield=zs, pipeline_audit=audit)
    reason = derive_status_reason(
        zeroshield=zs,
        pipeline=pipeline,
        error=True,
        status=403,
        context="rag",
    )
    assert reason["code"] == REASON_RAG_VECTOR_UNAVAILABLE
    wrapped = attach_status_reason(
        {"zeroshield": zs, "pipeline": pipeline, "error": True, "status": 403},
        context="rag",
    )
    assert wrapped["status_reason"]["code"] == REASON_RAG_VECTOR_UNAVAILABLE
