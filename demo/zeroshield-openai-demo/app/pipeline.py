"""Build customer-visible pipeline stages from ZeroShield metadata."""
from __future__ import annotations

from typing import Any


STAGE_ORDER = [
    ("auth", "Authentication"),
    ("rate_limit", "Rate Limit"),
    ("policy", "Policy Engine"),
    ("input_scan", "Input Analysis"),
    ("kill_switch", "Model Governance"),
    ("model_routing", "Routing Decision"),
    ("model_input", "Provider Request"),
    ("model_output", "Provider Response"),
    ("output_guardrail", "Output Validation"),
]

RAG_STAGE_ORDER = [
    ("query", "Query Scan"),
    ("retriever", "Vector Retrieval"),
    ("ranker", "Context Ranker"),
    ("generator", "Synthesis Guard"),
]


def _stage_action(trace_stages: dict[str, dict], name: str, default: str = "allow") -> str:
    st = trace_stages.get(name) or {}
    return st.get("action") or st.get("status") or default


def build_pipeline_view(
    *,
    zeroshield: dict | None = None,
    pipeline_trace: dict | None = None,
    requested_model: str = "",
) -> dict[str, Any]:
    """Normalize gateway metadata into a routing visualizer payload."""
    zs = zeroshield or {}
    trace = pipeline_trace or {}
    trace_stages = {}
    for s in trace.get("stages") or []:
        if isinstance(s, dict) and s.get("name"):
            trace_stages[s["name"]] = s

    stages = []
    has_trace = bool(trace_stages)
    for key, label in STAGE_ORDER:
        src = trace_stages.get(key, {})
        default_action = _stage_action(trace_stages, key) if has_trace else "n/a"
        stages.append({
            "id": key,
            "label": label,
            "action": default_action,
            "latency_ms": src.get("latency_ms"),
            "detail": src.get("guard_reason") or src.get("detail") or "",
        })

    routing = zs.get("routing") if isinstance(zs.get("routing"), dict) else {}
    trace_present = bool(trace_stages)
    return {
        "request_id": zs.get("request_id") or trace.get("request_id") or "",
        "action": zs.get("action") or ("unknown" if trace_present else "pending"),
        "requested_model": requested_model or routing.get("original_model") or routing.get("requested_model") or zs.get("model") or "",
        "routed_model": routing.get("selected_model") or zs.get("routed_model") or zs.get("model") or "",
        "fallback_model": routing.get("fallback_model") or "",
        "routing_reason": routing.get("routing_reason") or routing.get("reason") or zs.get("routing_reason") or "",
        "routing_status": routing.get("routing_status") or "",
        "decision_source": routing.get("decision_source") or zs.get("decision_source") or "",
        "candidate_count": routing.get("candidate_count"),
        "blocked_by": zs.get("blocked_by") or trace.get("blocked_by") or "",
        "category": zs.get("category") or "",
        "code": zs.get("code") or "",
        "processing_time_ms": zs.get("processing_time_ms"),
        "stages": stages,
        "trace_present": trace_present,
        "raw_zeroshield": zs,
        "raw_trace": trace,
    }


def _rag_stage_detail(stage: dict) -> str:
    parts: list[str] = []
    threat = str(stage.get("threat_type") or "").strip()
    if threat:
        parts.append(threat)
    rewritten = stage.get("rewritten_text")
    if isinstance(rewritten, str) and rewritten.strip():
        parts.append("rewritten")
    docs_out = stage.get("docs_out")
    if docs_out is not None:
        parts.append(f"docs={docs_out}")
    if not parts:
        return ""
    return ", ".join(parts)


def build_rag_pipeline_view(
    *,
    zeroshield: dict | None = None,
    pipeline_audit: dict | None = None,
    requested_model: str = "",
) -> dict[str, Any]:
    """Normalize gateway RAG pipeline_audit into a customer-visible payload."""
    zs = zeroshield or {}
    audit = pipeline_audit if isinstance(pipeline_audit, dict) else {}
    audit_stages = audit.get("stages") if isinstance(audit.get("stages"), list) else []
    stage_map: dict[str, dict] = {}
    for stage in audit_stages:
        if isinstance(stage, dict) and stage.get("name"):
            stage_map[str(stage["name"])] = stage

    stages = []
    for key, label in RAG_STAGE_ORDER:
        src = stage_map.get(key, {})
        action = src.get("action") or ("n/a" if not stage_map else "skip")
        stages.append({
            "id": key,
            "label": label,
            "action": action,
            "latency_ms": src.get("latency_ms"),
            "detail": _rag_stage_detail(src),
        })

    blocked_stage = str(
        zs.get("blocked_at_stage") or zs.get("pipeline_stage") or audit.get("blocked_at_stage") or ""
    ).strip()
    trace_present = bool(stage_map)
    action = str(zs.get("action") or audit.get("final_action") or "").lower()
    if not action:
        action = "block" if blocked_stage else ("unknown" if trace_present else "pending")

    return {
        "request_id": zs.get("request_id") or audit.get("request_id") or "",
        "action": action,
        "requested_model": requested_model or "",
        "routed_model": "",
        "fallback_model": "",
        "routing_reason": "",
        "routing_status": "",
        "decision_source": "rag_pipeline",
        "candidate_count": None,
        "blocked_by": blocked_stage or zs.get("blocked_by") or "",
        "category": zs.get("threat_type") or "",
        "code": zs.get("code") or "",
        "processing_time_ms": audit.get("total_latency_ms") or zs.get("processing_time_ms"),
        "stages": stages,
        "trace_present": trace_present,
        "pipeline_kind": "rag",
        "raw_zeroshield": zs,
        "raw_trace": audit,
    }
