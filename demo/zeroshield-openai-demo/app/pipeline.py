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
    for key, label in STAGE_ORDER:
        src = trace_stages.get(key, {})
        stages.append({
            "id": key,
            "label": label,
            "action": _stage_action(trace_stages, key),
            "latency_ms": src.get("latency_ms"),
            "detail": src.get("guard_reason") or src.get("detail") or "",
        })

    routing = zs.get("routing") if isinstance(zs.get("routing"), dict) else {}
    return {
        "request_id": zs.get("request_id") or trace.get("request_id") or "",
        "action": zs.get("action") or "unknown",
        "requested_model": requested_model or zs.get("model") or routing.get("requested_model") or "",
        "routed_model": routing.get("selected_model") or zs.get("routed_model") or zs.get("model") or "",
        "fallback_model": routing.get("fallback_model") or "",
        "routing_reason": routing.get("reason") or zs.get("routing_reason") or "",
        "blocked_by": zs.get("blocked_by") or trace.get("blocked_by") or "",
        "category": zs.get("category") or "",
        "code": zs.get("code") or "",
        "processing_time_ms": zs.get("processing_time_ms"),
        "stages": stages,
        "raw_zeroshield": zs,
        "raw_trace": trace,
    }
