"""
Operator-facing pipeline stage trace for Module 1.1 Attack Simulator / dashboard.
Built server-side with full routing and prompt context. Prompt/response text
fields are deterministically PII-redacted before they are written into the trace
so raw PII is never persisted or echoed back to the client (see `_truncate`).
"""

from __future__ import annotations

import time
from typing import Any

# Ordered pipeline stages (Module 1.1 trace contract).
PIPELINE_STAGE_NAMES: tuple[str, ...] = (
    "auth",
    "rate_limit",
    "policy",
    "input_scan",
    "kill_switch",
    "model_routing",
    "model_input",
    "model_output",
    "output_guardrail",
)

_STAGE_METRIC_KEYS: tuple[str, ...] = (
    "auth_ms",
    "rate_limit_ms",
    "policy_ms",
    "input_scan_ms",
    "kill_switch_ms",
    "model_routing_ms",
    "model_input_ms",
    "model_output_ms",
    "output_guardrail_ms",
)


class PipelineStageTimer:
    """Monotonic (perf_counter) per-stage latency tracker for proxy_chat."""

    def __init__(self, wall_start: float | None = None) -> None:
        self._wall_start = wall_start if wall_start is not None else time.perf_counter()
        self._segment_start = self._wall_start
        self._durations: dict[str, float] = {}

    def mark_segment_end(self, stage: str) -> float:
        """Attribute elapsed time since the last boundary to *stage*."""
        now = time.perf_counter()
        ms = round((now - self._segment_start) * 1000, 1)
        self._durations[stage] = round(self._durations.get(stage, 0.0) + ms, 1)
        self._segment_start = now
        return ms

    def add_ms(self, stage: str, ms: float) -> None:
        if ms <= 0:
            return
        self._durations[stage] = round(self._durations.get(stage, 0.0) + ms, 1)

    def wall_ms(self) -> float:
        return round((time.perf_counter() - self._wall_start) * 1000, 1)


def finalize_stage_metrics(
    metrics: dict | None,
    wall_start: float,
    *,
    ptimer: PipelineStageTimer | None = None,
) -> dict[str, float]:
    """Merge measured spans, compute input_scan + overhead; reconcile to wall clock."""
    m: dict[str, float] = {}
    if isinstance(metrics, dict):
        for k, v in metrics.items():
            try:
                m[str(k)] = _round_ms(v)
            except Exception:
                pass
    if ptimer is not None:
        for stage in PIPELINE_STAGE_NAMES:
            key = f"{stage}_ms"
            if not m.get(key) and ptimer._durations.get(stage):
                m[key] = ptimer._durations[stage]
    tier1 = m.get("tier1_ms") or 0.0
    tier2 = m.get("tier2_ms") or 0.0
    if not m.get("input_scan_ms") and (tier1 or tier2):
        m["input_scan_ms"] = _round_ms(tier1 + tier2)
    upstream = m.get("upstream_ms") or 0.0
    if not m.get("model_output_ms") and upstream:
        m["model_output_ms"] = upstream
    wall = round((time.perf_counter() - wall_start) * 1000, 1)
    m["total_ms"] = wall
    stage_sum = sum(m.get(k) or 0.0 for k in _STAGE_METRIC_KEYS)
    telemetry = m.get("telemetry_enqueue_ms") or 0.0
    m["overhead_ms"] = round(max(0.0, wall - stage_sum - telemetry), 1)
    m["stage_latency_sum_ms"] = round(stage_sum, 1)
    return m


# Minimum stage latency (ms) before a reduction hint is emitted.
_LATENCY_HINT_MIN_MS = 5.0
# Dominant stage must represent at least this share of total to emit a hint.
_LATENCY_HINT_MIN_SHARE_PCT = 10.0

_STAGE_REDUCTION_HINTS: dict[str, list[str]] = {
    "model_output": [
        "Switch to a smaller or faster model for this workload.",
        "Enable prompt/response caching for repeat traffic.",
        "Lower max_tokens or simplify tool schemas.",
    ],
    "model_input": [
        "Reduce prompt size or trim conversation history.",
        "Use a faster tokenizer/model pair for long contexts.",
    ],
    "input_scan": [
        "Run Tier-2 (Bedrock) scans asynchronously when policy allows.",
        "Disable Tier-2 for low-risk endpoints or monitor-only posture.",
        "Narrow scan scope (fewer PII/secret detectors) if compliance permits.",
    ],
    "output_guardrail": [
        "Run output Tier-2 scans asynchronously when policy allows.",
        "Reduce output length limits to shrink scan surface.",
    ],
    "policy": [
        "Reduce the number of active policy rules for this endpoint.",
        "Compile and cache policy bundles to avoid per-request re-evaluation.",
        "Scope rules to specific actors/models instead of org-wide.",
    ],
    "rate_limit": [
        "Raise org TPM/RPM ceilings if throttling is expected under load.",
        "Spread burst traffic across keys or stagger concurrent requests.",
    ],
    "auth": [
        "Cache API-key validation results when safe for your threat model.",
    ],
    "model_routing": [
        "Pre-select a model instead of dynamic routing when latency-sensitive.",
    ],
    "kill_switch": [
        "Keep kill-switch checks cached; they should stay sub-millisecond.",
    ],
}

_OVERHEAD_REDUCTION_HINTS: list[str] = [
    "Overhead dominates — check network RTT, serialization, and gateway load.",
    "Co-locate clients with the gateway or enable HTTP keep-alive.",
]


def build_latency_breakdown(
    stages: list[dict[str, Any]],
    *,
    total_latency_ms: float,
    overhead_ms: float,
) -> dict[str, Any]:
    """Dominant-stage latency breakdown + actionable reduction hints (PIPELINE-0017)."""
    total = max(float(total_latency_ms or 0), 0.0)
    overhead = max(float(overhead_ms or 0), 0.0)

    by_stage: list[dict[str, Any]] = []
    for s in stages or []:
        if not isinstance(s, dict):
            continue
        name = str(s.get("name") or "").strip()
        if not name:
            continue
        lat = max(float(s.get("latency_ms") or 0), 0.0)
        share = round((lat / total * 100.0), 1) if total > 0 else 0.0
        by_stage.append({"stage": name, "latency_ms": round(lat, 1), "share_pct": share})

    by_stage.sort(key=lambda x: x["latency_ms"], reverse=True)

    dominant_stage = ""
    dominant_ms = 0.0
    dominant_share = 0.0
    if by_stage and by_stage[0]["latency_ms"] > 0:
        dominant_stage = by_stage[0]["stage"]
        dominant_ms = by_stage[0]["latency_ms"]
        dominant_share = by_stage[0]["share_pct"]

    overhead_share = round((overhead / total * 100.0), 1) if total > 0 else 0.0

    hints: list[dict[str, Any]] = []

    def _append_hint(stage: str, lat_ms: float, share_pct: float, actions: list[str]) -> None:
        if lat_ms < _LATENCY_HINT_MIN_MS or share_pct < _LATENCY_HINT_MIN_SHARE_PCT:
            return
        label = stage.replace("_", " ")
        hints.append(
            {
                "stage": stage,
                "severity": "high" if share_pct >= 40 else "medium",
                "message": f"{label.title()} took {lat_ms:.1f}ms ({share_pct:.0f}% of total).",
                "actions": list(actions),
            }
        )

    if dominant_stage:
        actions = list(_STAGE_REDUCTION_HINTS.get(dominant_stage, []))
        if dominant_stage == "policy":
            for s in stages or []:
                if isinstance(s, dict) and s.get("name") == "policy":
                    detail = str(s.get("detail") or "")
                    if "rule" in detail.lower():
                        actions.insert(
                            0,
                            "Review matched policy rules — high rule count increases evaluation time.",
                        )
                    break
        if not actions:
            actions = [
                f"Investigate why {dominant_stage.replace('_', ' ')} is the slowest pipeline stage.",
            ]
        _append_hint(dominant_stage, dominant_ms, dominant_share, actions)

    if overhead >= _LATENCY_HINT_MIN_MS and overhead_share >= 25.0:
        if not hints or hints[0].get("stage") != "overhead":
            hints.append(
                {
                    "stage": "overhead",
                    "severity": "high" if overhead_share >= 40 else "medium",
                    "message": (
                        f"Unattributed overhead took {overhead:.1f}ms "
                        f"({overhead_share:.0f}% of total)."
                    ),
                    "actions": list(_OVERHEAD_REDUCTION_HINTS),
                }
            )

    return {
        "by_stage": by_stage,
        "dominant_stage": dominant_stage,
        "dominant_latency_ms": round(dominant_ms, 1),
        "dominant_share_pct": dominant_share,
        "overhead_ms": round(overhead, 1),
        "overhead_share_pct": overhead_share,
        "hints": hints,
    }


def attach_latency_breakdown(trace: dict[str, Any]) -> dict[str, Any]:
    """Recompute ``latency_breakdown`` on an existing pipeline_trace dict."""
    if not isinstance(trace, dict):
        return trace
    stages = trace.get("stages") if isinstance(trace.get("stages"), list) else []
    total = float(trace.get("total_latency_ms") or 0)
    overhead = float(trace.get("overhead_ms") or 0)
    if overhead <= 0 and total > 0:
        stage_sum = sum(
            float(s.get("latency_ms") or 0)
            for s in stages
            if isinstance(s, dict)
        )
        overhead = max(0.0, total - stage_sum)
    trace["latency_breakdown"] = build_latency_breakdown(
        stages,
        total_latency_ms=total,
        overhead_ms=overhead,
    )
    return trace


# Deterministic PII/secret redactor. Applied to every prompt/response text field
# written into the trace so raw PII never leaks into the operator UI / SSE / API
# (redact_all is a no-op on benign text, so the preview utility is preserved).
# Resolved once at import time via the same dual-import idiom used elsewhere.
try:  # pragma: no cover - import shim (script vs package execution)
    from patterns import redact_all as _redact_all  # type: ignore
    from patterns import contains_smart_redaction_markers as _has_smart_masks  # type: ignore
    from patterns import smart_mask_redaction_noop_is_expected as _smart_mask_noop_expected  # type: ignore
except ImportError:  # pragma: no cover
    try:
        from .patterns import redact_all as _redact_all  # type: ignore
        from .patterns import contains_smart_redaction_markers as _has_smart_masks  # type: ignore
        from .patterns import smart_mask_redaction_noop_is_expected as _smart_mask_noop_expected  # type: ignore
    except Exception:  # pragma: no cover
        _redact_all = None  # type: ignore
        _has_smart_masks = lambda _t: False  # type: ignore

        def _smart_mask_noop_expected(_p: str, _keys: list | None = None) -> bool:  # type: ignore
            return False

_INJECTION_THREAT_TYPES = frozenset({
    "prompt_injection",
    "jailbreak",
    "goal_hijacking",
    "tool_overreach",
    "indirect_injection",
    "injection",
})


def _is_injection_threat(threat_type: str) -> bool:
    t = str(threat_type or "").strip().lower()
    return t in _INJECTION_THREAT_TYPES or t.endswith("_injection")


def _normalize_verdict_action(action: str) -> str:
    a = str(action or "allow").strip().lower()
    if a not in ("allow", "flag", "block", "redact", "rewrite"):
        return "allow"
    return a


def _resolve_input_scan_action(
    *,
    policy_redacted: bool,
    scanner_redaction_applied: bool,
    verdict_action: str,
    threat_type: str,
    matched_patterns: list,
    final_action: str,
    is_blocked: bool,
    blocked_stage: str,
    is_output_only: bool,
    scan_input_prompt: str,
) -> tuple[str, str, bool]:
    """Honest input_scan stage action (action, scan_outcome, redact_noop)."""
    action = _normalize_verdict_action(verdict_action)
    threat_lc = str(threat_type or "").strip().lower()
    has_threat = bool(matched_patterns) or bool(
        threat_lc and threat_lc not in ("none", "", "clean")
    )

    if is_blocked and blocked_stage == "input_scan":
        return "block", "", False

    if not is_blocked and action == "block":
        action = "allow"

    if is_output_only:
        return (action if action in ("flag", "block") else "allow"), "", False

    if scanner_redaction_applied and has_threat:
        resolved = "rewrite" if final_action == "rewrite" else "redact"
        return resolved, "", False

    if policy_redacted and not scanner_redaction_applied:
        if has_threat and _is_injection_threat(threat_type):
            if action == "redact":
                action = "flag"
            return action, "", False
        if has_threat:
            return "allow", "analyzed", True
        return "allow", "clean", False

    if has_threat and action in ("redact", "rewrite") and not scanner_redaction_applied:
        return "allow", "analyzed", True

    if not has_threat:
        return "allow", "clean", False

    return action, "", False


def _input_scan_detail(
    *,
    scan_action: str,
    threat_type: str,
    confidence: float,
    scan_detail: str,
    policy_redacted: bool,
    scanner_redaction_applied: bool,
    scan_outcome: str,
) -> str:
    threat_label = str(threat_type or "").replace("_", " ")
    conf_suffix = f" ({confidence:.0%} confidence)" if confidence else ""

    if scan_action == "block" and threat_type:
        if policy_redacted and _is_injection_threat(threat_type):
            return scan_detail or f"Prompt injection detected in post-policy input{conf_suffix}"
        return scan_detail or f"Input blocked — {threat_label}{conf_suffix}"

    if scan_outcome == "analyzed" and policy_redacted and not scanner_redaction_applied:
        if threat_label:
            return (
                f"Input analyzed after policy redaction — {threat_label}{conf_suffix}; "
                "no additional masking"
            )
        return "Input analyzed after policy redaction; no additional masking"

    if scan_action == "block" and threat_type:
        return scan_detail or f"Input blocked — {threat_label}{conf_suffix}"

    threat_lc = threat_label.lower()
    if threat_type and threat_label and threat_lc not in ("none", "clean"):
        if scan_action in ("allow", "flag"):
            return (
                scan_detail
                or f"Scan completed — {threat_label}{conf_suffix}; allowed through"
            )

    if scan_detail:
        return scan_detail
    return "Input scan completed — no threats detected"

GUARD_MODEL_LABEL = "ZeroShield Model"
PATTERN_ENGINE_LABEL = "ZeroShield Pattern Engine"
OUTPUT_GUARD_LABEL = "ZeroShield Output Guard"
ZEROSHIELD_ADJUDICATOR_LABEL = "ZeroShield Policy Adjudicator"

_ROUTING_REASON_REPLACEMENTS = (
    ("Bedrock GPT OSS 120B adjudicator", ZEROSHIELD_ADJUDICATOR_LABEL),
    ("Bedrock GPT OSS 120B", ZEROSHIELD_ADJUDICATOR_LABEL),
    ("bedrock adjudicator", ZEROSHIELD_ADJUDICATOR_LABEL),
)

DECISION_SOURCE_LABELS: dict[str, str] = {
    "kill_switch": "Kill switch",
    "model_state": "Model state isolation",
    "policy_adjudicator": ZEROSHIELD_ADJUDICATOR_LABEL,
    "routing_disabled": "Routing disabled",
    "no_routing_models": "No routing models",
    "policy_engine": "Policy engine",
    "gateway_auth": "Gateway authentication",
    "org_rate_limit": "Org rate limit",
    "zeroshield_guard_model": GUARD_MODEL_LABEL,
    "pattern_engine": PATTERN_ENGINE_LABEL,
    "output_guard": OUTPUT_GUARD_LABEL,
    "llm_request": "LLM request",
    "llm_provider": "LLM provider",
}

# PIPELINE-0020: every stage object carries these keys (empty when N/A).
STAGE_TRANSPARENCY_KEYS: tuple[str, ...] = (
    "action",
    "latency_ms",
    "decision_source",
    "decision_source_label",
    "guard_reason",
    "tier",
    "confidence",
    "matched_policies",
    "matched_rules",
    "prompt_in",
    "prompt_out",
)

# PIPELINE-0021: model_routing stage exposes explicit destination + adjudicator WHY.
ROUTING_STAGE_KEYS: tuple[str, ...] = (
    "requested_model",
    "selected_model",
    "routed_model",
    "route_destination",
    "route_destination_label",
    "routing_reason",
    "policy_summary",
    "decision_factors",
    "weights",
    "routing_score",
    "candidate_count",
    "fallback_chain",
    "evaluator_model",
)

ROUTE_DESTINATION_LABELS: dict[str, str] = {
    "llm": "LLM inference",
    "rag": "RAG retrieval",
    "vector_db": "Vector DB",
    "mcp": "MCP tool",
}


def _empty_stage_why_fields() -> dict[str, Any]:
    return {
        "decision_source": "",
        "decision_source_label": "",
        "guard_reason": "",
        "tier": "",
        "confidence": 0,
        "matched_policies": [],
        "matched_rules": [],
        "prompt_in": "",
        "prompt_out": "",
    }


def _decision_source_fields(source: str) -> dict[str, str]:
    key = str(source or "").strip().lower()
    if not key:
        return {"decision_source": "", "decision_source_label": ""}
    return {
        "decision_source": key,
        "decision_source_label": _format_decision_source(key),
    }


def _guard_source_for_tier(tier: str, *, output: bool = False) -> str:
    if output:
        return "output_guard"
    t = str(tier or "").lower()
    if t in ("policy",):
        return "policy_engine"
    if t.startswith("tier_1") or t == "tier_1":
        return "pattern_engine"
    if t in ("tier_2", "tier2", "input_scan"):
        return "zeroshield_guard_model"
    return "zeroshield_guard_model"


def _policy_guard_reason(
    *,
    action: str,
    matched_policies: list[str],
    matched_rules: list[str],
    detail: str,
) -> str:
    lines: list[str] = [f"Policy engine — enforcement: {str(action or 'allow').upper()}"]
    if matched_policies:
        lines.append("Matched policies: " + ", ".join(matched_policies))
    if matched_rules:
        lines.append("Matched rules: " + ", ".join(matched_rules))
    if detail:
        lines.append(str(detail).strip())
    return "\n".join(lines)


def _resolve_route_destination(routing: dict[str, Any]) -> tuple[str, str]:
    """Where the request was routed (LLM / RAG / Vector DB / MCP). Chat → llm."""
    raw = str(routing.get("route_destination") or routing.get("routing_target") or "").strip().lower()
    if not raw:
        raw = "llm"
    label = ROUTE_DESTINATION_LABELS.get(raw, raw.replace("_", " ").title())
    return raw, label


def _build_routing_guard_reason(
    *,
    routing_reason: str,
    decision_factors: list[Any],
    weights: dict[str, Any],
    routing_score: Any = None,
    candidate_count: Any = None,
    route_destination_label: str = "",
) -> str:
    lines: list[str] = []
    if routing_reason:
        lines.append(str(routing_reason).strip())
    if route_destination_label:
        lines.append(f"Destination: {route_destination_label}")
    factors = [str(f).strip() for f in (decision_factors or []) if str(f).strip()]
    if factors:
        lines.append(f"Factors: {', '.join(factors)}")
    if weights:
        parts = []
        for key, val in weights.items():
            try:
                pct = f"{round(float(val) * 100)}%"
            except (TypeError, ValueError):
                pct = str(val)
            parts.append(f"{key}={pct}")
        if parts:
            lines.append(f"Weights: {', '.join(parts)}")
    try:
        score = float(routing_score)
        if score > 0:
            lines.append(f"Score: {round(score, 3)}")
    except (TypeError, ValueError):
        pass
    try:
        count = int(candidate_count)
        if count > 0:
            lines.append(f"Candidates: {count}")
    except (TypeError, ValueError):
        pass
    return "\n".join(lines) if lines else ""


def normalize_routing_stage_fields(stage: dict[str, Any]) -> dict[str, Any]:
    """Ensure model_routing exposes the PIPELINE-0021 routing contract."""
    if not isinstance(stage, dict) or stage.get("name") != "model_routing":
        return stage
    for key in ROUTING_STAGE_KEYS:
        if key not in stage:
            if key in ("decision_factors", "fallback_chain"):
                stage[key] = []
            elif key == "weights":
                stage[key] = {}
            elif key in ("routing_score", "candidate_count"):
                stage[key] = 0
            else:
                stage[key] = ""
    if not stage.get("route_destination"):
        stage["route_destination"] = "llm"
    if not stage.get("route_destination_label"):
        _, label = _resolve_route_destination(stage)
        stage["route_destination_label"] = label
    if not stage.get("guard_reason"):
        stage["guard_reason"] = _build_routing_guard_reason(
            routing_reason=str(stage.get("routing_reason") or ""),
            decision_factors=stage.get("decision_factors") or [],
            weights=stage.get("weights") or {},
            routing_score=stage.get("routing_score"),
            candidate_count=stage.get("candidate_count"),
            route_destination_label=str(stage.get("route_destination_label") or ""),
        )
    return stage


def normalize_stage_transparency(stage: dict[str, Any]) -> dict[str, Any]:
    """Ensure every stage dict exposes the PIPELINE-0020 field contract."""
    if not isinstance(stage, dict):
        return stage
    for key in STAGE_TRANSPARENCY_KEYS:
        if key not in stage:
            if key in ("matched_policies", "matched_rules"):
                stage[key] = []
            elif key == "confidence":
                stage[key] = 0
            elif key == "latency_ms":
                stage[key] = 0.0
            else:
                stage[key] = ""
    if stage.get("decision_source") and not stage.get("decision_source_label"):
        stage["decision_source_label"] = _format_decision_source(str(stage["decision_source"]))
    return normalize_routing_stage_fields(stage)


def _sanitize_routing_reason(text: str) -> str:
    if not text:
        return ""
    result = str(text)
    for old, new in _ROUTING_REASON_REPLACEMENTS:
        result = result.replace(old, new)
    return result


def _format_decision_source(source: str) -> str:
    key = str(source or "").strip().lower()
    if not key:
        return ""
    return DECISION_SOURCE_LABELS.get(key, key.replace("_", " ").title())


def _routing_stage_action(
    requested: str,
    selected: str,
    routing: dict,
    *,
    final_action: str,
    blocked_stage: str,
) -> str:
    if blocked_stage == "model_routing":
        return "block"
    if final_action == "needs_model":
        return "needs_model"
    if routing.get("rerouted"):
        return "reroute"
    req = str(requested or "").strip()
    sel = str(selected or "").strip()
    if req and sel and req.lower() != "auto" and req != sel:
        return "reroute"
    return "allow"

REASON_CODE_LABELS: dict[str, str] = {
    "model_recommended_block": "The ZeroShield Model classified this content as unsafe and recommended blocking the request.",
    "model_recommended_redact": "The ZeroShield Model recommended redacting sensitive or policy-violating segments before forwarding.",
    "model_recommended_monitor": "The ZeroShield Model flagged this content for monitoring — review recommended but not blocked.",
    "model_recommendation": "ZeroShield Model issued a structured recommendation; org policy may apply a different enforcement action.",
    "model_refusal": "The ZeroShield Model could not complete analysis and applied a conservative block.",
    "score_threshold_block": "Risk score exceeded the automatic block threshold.",
    "score_threshold_flag": "Risk score exceeded the advisory flag threshold.",
    "findings_with_allow": "Threat signals were detected, but policy escalated to flag instead of block.",
    "tier2_pass": "ZeroShield Model (Tier-2) scan completed — content assessed as clean; no enforcement action required.",
    "bedrock_degraded": "ZeroShield Model was degraded; request flagged for manual review.",
    "parse_failure_conservative": "ZeroShield Model response could not be parsed; conservative enforcement applied.",
    "client_error": "ZeroShield Model client error — conservative handling applied.",
}


def _round_ms(value: Any, default: float = 0.0) -> float:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return default
    if n < 0:
        return default
    return round(n, 1)


def _truncate(text: str, limit: int = 1200) -> str:
    raw = (text or "").strip()
    # Redact PII/secrets BEFORE truncating so raw PII is never persisted/echoed in
    # the trace (R17). No-op on benign text; fail-open if the redactor is missing.
    if raw and _redact_all is not None:
        try:
            raw = _redact_all(raw)
        except Exception:
            pass
    if len(raw) <= limit:
        return raw
    return raw[:limit] + "…"


def _scanner_label(tier: str, *, output: bool = False) -> str:
    if output:
        return OUTPUT_GUARD_LABEL
    t = str(tier or "").lower()
    if t in ("tier_2", "tier2", "input_scan"):
        return GUARD_MODEL_LABEL
    if t.startswith("tier_1") or t == "tier_1":
        return PATTERN_ENGINE_LABEL
    return GUARD_MODEL_LABEL if t else PATTERN_ENGINE_LABEL


def _reason_code_label(code: str) -> str:
    key = str(code or "").strip().lower()
    if not key:
        return ""
    return REASON_CODE_LABELS.get(key, key.replace("_", " ").capitalize() + ".")


def enrich_zeroshield_from_verdict(
    zeroshield: dict,
    *,
    scan_verdict=None,
    final_action: str = "allow",
) -> dict:
    """Merge operator-facing Guard Model fields into client zeroshield metadata."""
    if not isinstance(zeroshield, dict):
        return zeroshield

    tier = str(zeroshield.get("detection_tier") or "")
    stage_action = str(zeroshield.get("action") or "allow")
    guard = build_guard_fields(
        verdict=scan_verdict,
        stage_action=stage_action,
        final_action=final_action,
        tier=tier,
        zs=zeroshield,
        output=False,
    )
    enriched = {**zeroshield, **guard}

    risk = 0.0
    if scan_verdict is not None:
        meta = getattr(scan_verdict, "scan_meta", None) or {}
        if isinstance(meta, dict) and meta.get("llm_guard_score") is not None:
            risk = float(meta.get("llm_guard_score") or 0)
        else:
            risk = float(getattr(scan_verdict, "confidence", 0) or 0)
    enriched["risk_score"] = round(risk, 4)

    action = str(enriched.get("action") or "allow").lower()
    threat = str(enriched.get("threat_type") or "none").lower()
    reason_code = str(enriched.get("reason_code") or "").lower()
    if action == "allow" and (threat in ("none", "clean", "") or reason_code == "tier2_pass"):
        enriched["scan_outcome"] = "clean"
        if threat == "none":
            enriched["threat_type"] = "clean"
        enriched["confidence"] = round(max(0.0, 1.0 - risk), 4)
        if guard.get("guard_reason"):
            enriched["detail"] = guard["guard_reason"]
            enriched["reason"] = guard["guard_reason"].split("\n")[0]
    return enriched


def build_guard_fields(
    *,
    verdict: Any = None,
    stage_action: str = "allow",
    final_action: str = "allow",
    tier: str = "",
    zs: dict | None = None,
    output: bool = False,
    final_attributed: bool = True,
) -> dict[str, Any]:
    """Operator-facing Guard Model explanation for pipeline stages.

    ``final_attributed`` says whether the request-level ``final_action`` (and the
    request-level ``zs`` threat/pattern data) belongs to THIS guard. It is False
    for the output guard when the enforcement actually happened at input/policy
    (e.g. an INPUT PII redaction): in that case the output guard must report only
    its OWN verdict (``verdict``), never the input-side redaction — otherwise a
    clean model response gets mislabelled as an output-side PII redact (H1).
    """
    zs = zs if isinstance(zs, dict) else {}
    sv = verdict
    # When the final action is NOT attributed to this guard, the request-level
    # ``zs`` carries the OTHER stage's enforcement data — do not let it leak in.
    zs_src = zs if final_attributed else {}
    reason_code = str(getattr(sv, "reason_code", None) or zs_src.get("reason_code") or "").strip()
    scan_meta = getattr(sv, "scan_meta", None) if sv is not None else None
    if not isinstance(scan_meta, dict):
        scan_meta = zs_src.get("scan_meta") if isinstance(zs_src.get("scan_meta"), dict) else {}

    threat_type = str(getattr(sv, "threat_type", None) or zs_src.get("threat_type") or "").strip()
    confidence = getattr(sv, "confidence", None)
    if confidence is None:
        confidence = zs_src.get("confidence", 0)
    try:
        confidence_f = float(confidence or 0)
    except (TypeError, ValueError):
        confidence_f = 0.0

    detail = (
        getattr(sv, "detail", None)
        or zs_src.get("detail")
        or zs_src.get("reason")
        or ""
    )
    matched_patterns = list(
        getattr(sv, "matched_patterns", None) or zs_src.get("matched_patterns") or []
    )
    recommended = str(
        scan_meta.get("recommended_action")
        or zs_src.get("recommended_action")
        or ""
    ).strip().lower()

    enforcement_action = stage_action
    if final_attributed and final_action in ("block", "redact", "rewrite", "flag"):
        # R4: the ENFORCED, request-level outcome for the stage this guard is
        # attributed to IS final_action. Previously only a stage_action of
        # "allow" was corrected, so a stage still carrying the guard model's
        # *recommendation* (e.g. "block") survived even when the request was
        # actually redacted-and-served (final_action="redact") — a misleading
        # audit trail (operator sees "block" for a served response). Always
        # reflect the enforced outcome; preserve the stage's own recommendation
        # in recommended_action so it isn't lost.
        if stage_action not in ("allow", "", final_action) and not recommended:
            recommended = stage_action
        enforcement_action = final_action

    scanner_label = _scanner_label(
        getattr(sv, "tier", None) or tier or zs.get("detection_tier") or "",
        output=output,
    )

    lines: list[str] = [
        f"{scanner_label} — enforcement: {enforcement_action.upper()}",
    ]
    code_line = _reason_code_label(reason_code)
    if code_line:
        lines.append(code_line)
    if recommended and recommended != enforcement_action:
        lines.append(f"Model recommendation: {recommended.upper()}")
    if threat_type and threat_type not in ("none", ""):
        lines.append(f"Threat category: {threat_type.replace('_', ' ')}")
    if confidence_f > 0:
        lines.append(f"Confidence: {confidence_f:.0%}")
    # PII-LEAK FIX: the guard MODEL's advisory `detail` + `findings`/`evidence`
    # are LLM-generated free text that can echo the RAW PII it detected (e.g. a
    # full phone number) — even when the response itself was redacted. That raw
    # value must never reach the client-facing trace. Scrub every guard-derived
    # detail line through redact_all (a no-op on benign text) at this choke point.
    def _scrub(s: str) -> str:
        return _redact_all(s) if _redact_all else s

    if detail:
        lines.append(_scrub(str(detail).strip()))

    findings: list[str] = []
    for item in scan_meta.get("findings") or []:
        if isinstance(item, dict):
            ev = str(item.get("evidence") or "").strip()
            cat = str(item.get("category") or "").strip()
            if ev:
                findings.append(ev)
            elif cat:
                findings.append(cat)
    if not findings and matched_patterns:
        findings = [str(p) for p in matched_patterns[:5]]
    if findings:
        lines.append("Evidence: " + _scrub("; ".join(findings[:3])))

    policy_note = ""
    if enforcement_action == "allow" and recommended in ("block", "redact"):
        policy_note = "Org policy allowed the request despite the ZeroShield Model recommendation."
    elif enforcement_action == "redact" and recommended == "block":
        policy_note = (
            "Org PII policy redacted sensitive fields and continued the request "
            "instead of a hard block (model recommendation shown for audit only)."
        )
    elif enforcement_action in ("block", "redact", "rewrite") and recommended == "allow":
        policy_note = "Policy enforcement overrode the ZeroShield Model allow recommendation."

    guard_reason = "\n".join(lines)
    if policy_note:
        guard_reason = guard_reason + "\n" + policy_note

    ds = _guard_source_for_tier(tier, output=output)
    return {
        "guard_model": scanner_label,
        "guard_action": enforcement_action,
        "guard_reason": guard_reason,
        "reason_code": reason_code,
        "recommended_action": recommended,
        "guard_findings": findings[:8],
        "enforcement_source": ds,
        **_decision_source_fields(ds),
    }


def _metrics(stage_metrics: dict | None) -> dict[str, float]:
    sm = stage_metrics if isinstance(stage_metrics, dict) else {}
    tier1 = _round_ms(sm.get("tier1_ms"))
    tier2 = _round_ms(sm.get("tier2_ms"))
    input_scan = _round_ms(sm.get("input_scan_ms"))
    if not input_scan and (tier1 or tier2):
        input_scan = _round_ms(tier1 + tier2)
    upstream = _round_ms(sm.get("upstream_ms"))
    model_output = _round_ms(sm.get("model_output_ms"), upstream)
    return {
        "auth_ms": _round_ms(sm.get("auth_ms")),
        "rate_limit_ms": _round_ms(sm.get("rate_limit_ms")),
        "policy_ms": _round_ms(sm.get("policy_ms")),
        "input_scan_ms": input_scan,
        "kill_switch_ms": _round_ms(sm.get("kill_switch_ms")),
        "model_routing_ms": _round_ms(sm.get("model_routing_ms")),
        "model_input_ms": _round_ms(sm.get("model_input_ms")),
        "model_output_ms": model_output,
        "upstream_ms": upstream,
        "output_guardrail_ms": _round_ms(sm.get("output_guardrail_ms")),
        "telemetry_ms": _round_ms(sm.get("telemetry_enqueue_ms")),
        "overhead_ms": _round_ms(sm.get("overhead_ms")),
        "stage_latency_sum_ms": _round_ms(sm.get("stage_latency_sum_ms")),
        "total_ms": _round_ms(sm.get("total_ms") or sm.get("total_hint_ms")),
    }


def build_pipeline_trace(
    *,
    prompt: str = "",
    forwarded_prompt: str = "",
    policy_redacted_prompt: str = "",
    policy_redacted_flag: bool | None = None,
    scanner_redaction_applied: bool | None = None,
    stage_metrics: dict | None = None,
    final_action: str = "allow",
    blocked_stage: str = "",
    http_status: int = 200,
    scan_verdict: Any = None,
    route_metadata: dict | None = None,
    zeroshield: dict | None = None,
    response_text: str = "",
    blocked_detail: str = "",
    requested_model: str = "",
    output_scan_verdict: Any = None,
    prompt_in_operator_masked: bool = False,
) -> dict[str, Any]:
    """Return { stages: [...], total_latency_ms, prompt_preview } for the UI.

    ``prompt_in_operator_masked``: caller already ran operator-safe display masking
    (``_redact_trace_text``) on ``prompt`` before calling this. When True, the
    policy stage Before panel must not be read as the literal pre-policy bytes —
    raw PII was present and was display-masked for the operator UI.
    """
    zs = zeroshield if isinstance(zeroshield, dict) else {}
    routing = route_metadata if isinstance(route_metadata, dict) else (zs.get("routing") or {})
    if not isinstance(routing, dict):
        routing = {}

    metrics = _metrics(stage_metrics)
    prompt_preview = _truncate(prompt)
    # The prompt actually FORWARDED to the model — equals the redacted prompt
    # when PII/policy redaction fired, else the original. Shown at model_input so
    # the operator sees exactly what the LLM received (not the raw input).
    forwarded_preview = _truncate(forwarded_prompt) if forwarded_prompt else prompt_preview
    # Deterministic POLICY-stage redaction (applied before Tier-2). When the
    # policy engine masked matched patterns, `policy_redacted_prompt` is the text
    # AFTER policy redaction but BEFORE Tier-2 — i.e. exactly what the input
    # scanner received. The input_scan stage therefore scans this, not the raw
    # prompt, and any further change at model_input is attributable to Tier-2.
    # Whether the POLICY stage actually redacted. The caller MUST pass the
    # authoritative `policy_redacted_flag` (computed in main.py from the RAW
    # original prompt). Re-deriving it here via `policy_redacted_prompt != prompt`
    # is WRONG when the caller passes a trace-safe `prompt` (PII already masked by
    # _redact_trace_text): the policy-redacted prompt and the trace-redacted prompt
    # are then BOTH masked → comparison is False → the policy stage mislabels a real
    # redaction as "allow" (while top-level zeroshield.action correctly says
    # "redact"). The string compare is kept only as a fallback for callers that
    # don't pass the flag.
    policy_redacted = (
        policy_redacted_flag
        if policy_redacted_flag is not None
        else (bool(policy_redacted_prompt) and policy_redacted_prompt != prompt)
    )
    scan_input_prompt = policy_redacted_prompt or prompt
    scan_input_preview = _truncate(scan_input_prompt) if scan_input_prompt else prompt_preview
    policy_redacted_preview = _truncate(policy_redacted_prompt) if policy_redacted_prompt else ""
    sv = scan_verdict
    # When the zeroshield verdict is attributed to the POLICY stage (it performed
    # the redaction), its threat_type/matched_patterns/action/detail belong to the
    # policy stage — NOT input_scan. In that case the input_scan stage must reflect
    # ONLY the Tier-2 scan verdict (which scanned the already-redacted text and is
    # typically clean), so don't let it fall back to the policy-attributed zs.
    _zs_scan = {} if str(zs.get("detection_tier") or "") == "policy" else zs
    threat_type = getattr(sv, "threat_type", None) or _zs_scan.get("threat_type") or ""
    confidence = getattr(sv, "confidence", None)
    if confidence is None:
        confidence = _zs_scan.get("confidence", 0)
    tier = getattr(sv, "tier", None) or _zs_scan.get("detection_tier") or ""
    matched_patterns = list(getattr(sv, "matched_patterns", None) or _zs_scan.get("matched_patterns") or [])
    scan_detail = getattr(sv, "detail", None) or _zs_scan.get("detail") or _zs_scan.get("reason") or blocked_detail or ""
    # Real matched POLICY/RULE names from the deterministic policy engine — distinct
    # from the scanner's matched_patterns (which are regex substrings of the prompt).
    policy_matched = list(dict.fromkeys(zs.get("matched_policy_names") or zs.get("matched_policies") or []))
    policy_rules = list(dict.fromkeys(zs.get("matched_rule_names") or zs.get("matched_rules") or []))

    is_blocked = final_action == "block"
    is_skip_after = bool(blocked_stage) and is_blocked

    # The model stages (model_input / model_output) only fail to run when the
    # request was blocked at a stage UPSTREAM of the model. An OUTPUT-guard block
    # (or redact/flag) happens AFTER the model has already run + produced the
    # response, so those stages must still read "run"/"allow" — never "skip" —
    # otherwise the §1.7 audit trail is self-contradictory (output blocked while
    # the model that generated the blocked content is marked skipped).
    _UPSTREAM_OF_MODEL = {
        "auth", "rate_limit", "policy", "input_scan", "kill_switch", "model_routing",
    }
    _model_skipped = bool(is_blocked and blocked_stage in _UPSTREAM_OF_MODEL)

    if scanner_redaction_applied is None:
        scanner_redaction_applied = bool(
            forwarded_prompt
            and scan_input_prompt
            and forwarded_prompt != scan_input_prompt
        )

    verdict_action = getattr(sv, "action", None) or _zs_scan.get("action") or "allow"
    is_output_only = zs.get("detection_tier") == "output_guard"
    scan_action, scan_outcome, redact_noop = _resolve_input_scan_action(
        policy_redacted=policy_redacted,
        scanner_redaction_applied=scanner_redaction_applied,
        verdict_action=verdict_action,
        threat_type=threat_type,
        matched_patterns=matched_patterns,
        final_action=final_action,
        is_blocked=is_blocked,
        blocked_stage=blocked_stage,
        is_output_only=is_output_only,
        scan_input_prompt=scan_input_prompt,
    )
    input_scan_detail = _input_scan_detail(
        scan_action=scan_action,
        threat_type=threat_type,
        confidence=float(confidence or 0),
        scan_detail=scan_detail if isinstance(scan_detail, str) else str(scan_detail or ""),
        policy_redacted=policy_redacted,
        scanner_redaction_applied=scanner_redaction_applied,
        scan_outcome=scan_outcome,
    )

    def _latency(name: str, explicit: float | None = None, *, skipped: bool = False) -> float:
        if skipped:
            return 0.0
        if explicit is not None:
            return _round_ms(explicit)
        mapping = {
            "auth": metrics["auth_ms"],
            "rate_limit": metrics["rate_limit_ms"],
            "policy": metrics["policy_ms"],
            "input_scan": metrics["input_scan_ms"],
            "kill_switch": metrics["kill_switch_ms"],
            "model_routing": metrics["model_routing_ms"],
            "model_input": metrics["model_input_ms"],
            "model_output": metrics["model_output_ms"] or metrics["upstream_ms"],
            "output_guardrail": metrics["output_guardrail_ms"],
        }
        return mapping.get(name, 0.0)

    def _action(stage: str, default: str = "allow") -> str:
        if blocked_stage == stage:
            return "block"
        if is_blocked and blocked_stage and stage != blocked_stage:
            idx_order = [
                "auth", "rate_limit", "policy", "input_scan", "kill_switch",
                "model_routing", "model_input", "model_output", "output_guardrail",
            ]
            try:
                if idx_order.index(stage) > idx_order.index(blocked_stage):
                    return "skip"
            except ValueError:
                pass
        if final_action in ("redact", "rewrite", "flag") and stage == "input_scan":
            if policy_redacted and not scanner_redaction_applied:
                return default
            return final_action
        return default

    requested = (
        routing.get("original_model")
        or routing.get("requested_model")
        or requested_model
        or zs.get("original_model")
        or ""
    )
    selected = (
        routing.get("selected_model")
        or routing.get("routed_model")
        or zs.get("selected_model")
        or ""
    )
    routing_reason = _sanitize_routing_reason(
        routing.get("routing_reason") or zs.get("routing_reason") or ""
    )
    decision_source = routing.get("decision_source") or zs.get("decision_source") or ""
    decision_source_label = _format_decision_source(decision_source)
    policy_summary = routing.get("policy_summary") or zs.get("policy_summary") or ""
    decision_factors = routing.get("decision_factors") or zs.get("decision_factors") or []
    weights = routing.get("weights") or zs.get("weights") or {}
    route_destination, route_destination_label = _resolve_route_destination(routing)
    routing_score = routing.get("routing_score") or zs.get("routing_score") or 0
    candidate_count = routing.get("candidate_count") or zs.get("candidate_count") or 0
    fallback_chain = routing.get("fallback_chain") or zs.get("fallback_chain") or []
    evaluator_model = routing.get("evaluator_model") or zs.get("evaluator_model") or ""

    # Where did the request-level enforcement (block/redact/...) actually happen?
    # The output guard forces detection_tier="output_guard" on its zs copy below,
    # so capture the ORIGINAL tier first. Input/policy enforcement belongs to the
    # input guard; only detection_tier=="output_guard" belongs to the output guard.
    _orig_detection_tier = str(zs.get("detection_tier") or "")
    _enforced_at_output = _orig_detection_tier == "output_guard"
    _input_guard_final_attributed = (
        not _enforced_at_output
        and not (policy_redacted and not scanner_redaction_applied)
    )
    input_guard = build_guard_fields(
        verdict=sv,
        stage_action=scan_action,
        final_action=final_action,
        tier=tier,
        zs=_zs_scan,
        output=False,
        final_attributed=_input_guard_final_attributed,
    )
    output_guard_zs = {
        **zs,
        "detection_tier": zs.get("detection_tier") or "output_guard",
    }
    # OUTPUT-STAGE HONESTY (2026-07-16): the output guardrail stage must reflect the
    # OUTPUT guard's OWN enforced action (zs["action"] — e.g. "rewrite"), NOT the
    # request-global final_action. final_action is the highest-severity action across
    # ALL stages and is dominated by an INPUT-side policy redact (redact=3 > rewrite=2),
    # which made the OUTPUT stage display "REDACT" (badge + "enforcement: REDACT"
    # detail) for a response the output guard actually REWROTE — the operator-visible
    # mislabel behind "I set rewrite on output guardrails — why does it say redact?".
    # Attribute the output guard's own action to its own stage + enforcement label.
    _output_own_action = str(output_guard_zs.get("action") or "").lower()
    if _output_own_action not in ("block", "redact", "rewrite", "flag", "allow"):
        _output_own_action = final_action
    output_stage_action = _action("output_guardrail")
    if _output_own_action in ("block", "redact", "rewrite", "flag") and _enforced_at_output:
        output_stage_action = _output_own_action
    output_guard = build_guard_fields(
        verdict=output_scan_verdict,
        stage_action=output_stage_action,
        final_action=(_output_own_action if _enforced_at_output else final_action),
        tier="output_guard",
        zs=output_guard_zs,
        output=True,
        final_attributed=_enforced_at_output,
    )

    ks_trigger = str(routing.get("trigger_source") or "").lower()
    ks_rerouted = bool(routing.get("rerouted") and ks_trigger == "kill_switch")
    kill_switch_action = (
        "block" if blocked_stage == "kill_switch"
        else "reroute" if ks_rerouted
        else _action("kill_switch")
    )
    kill_switch_detail = (
        blocked_detail if blocked_stage == "kill_switch"
        else _sanitize_routing_reason(routing.get("routing_reason") or routing.get("reason") or "")
        if ks_rerouted
        else "No active kill-switch for this model"
    )
    routing_action = _routing_stage_action(
        requested,
        selected,
        routing,
        final_action=final_action,
        blocked_stage=blocked_stage,
    )
    routing_detail = routing_reason or (
        f"Routed to {selected}" if selected else "Model routing evaluated"
    )
    routing_guard_reason = _build_routing_guard_reason(
        routing_reason=routing_reason,
        decision_factors=decision_factors,
        weights=weights,
        routing_score=routing_score,
        candidate_count=candidate_count,
        route_destination_label=route_destination_label,
    ) or routing_detail

    policy_action = (
        "block" if blocked_stage == "policy"
        else "redact" if policy_redacted
        else _action("policy")
    )
    policy_detail = (
        scan_detail
        if blocked_stage == "policy"
        else (
            "Policy engine redacted %d matched rule(s) before scanning"
            % len(policy_rules)
            if policy_redacted
            else "Policy engine evaluated request against compiled rules"
            + (
                "; matched %d rule(s)" % len(policy_rules)
                if policy_rules
                else "; no matching policy rule"
                + (
                    " (regex/keyword rules target raw PII; pre-masked shapes are handled at input_scan)"
                    if not policy_rules and _has_smart_masks(scan_input_prompt or "")
                    else ""
                )
            )
        )
    )
    policy_confidence = 0.0
    if blocked_stage == "policy" or policy_redacted:
        try:
            policy_confidence = float(zs.get("confidence") or confidence or 0)
        except (TypeError, ValueError):
            policy_confidence = 0.0

    stages: list[dict[str, Any]] = [
        {
            "name": "auth",
            "action": _action("auth"),
            "latency_ms": _latency("auth"),
            "detail": "Gateway API key accepted" if _action("auth") != "block" else "Gateway API key invalid or missing",
            **_empty_stage_why_fields(),
            **_decision_source_fields("gateway_auth"),
            "guard_reason": (
                blocked_detail if blocked_stage == "auth" else "Gateway API key accepted"
            ),
            "prompt_in": prompt_preview,
            "prompt_out": prompt_preview if _action("auth") != "block" else "",
        },
        {
            "name": "rate_limit",
            "action": _action("rate_limit"),
            "latency_ms": _latency("rate_limit"),
            "detail": blocked_detail if blocked_stage == "rate_limit" else "Within org TPM / RPM limits",
            **_empty_stage_why_fields(),
            **_decision_source_fields("org_rate_limit"),
            "guard_reason": (
                blocked_detail if blocked_stage == "rate_limit" else "Within org TPM / RPM limits"
            ),
            "prompt_in": prompt_preview,
            "prompt_out": prompt_preview if _action("rate_limit") != "block" else "",
        },
        {
            "name": "policy",
            "action": policy_action,
            "latency_ms": _latency("policy"),
            "detail": policy_detail,
            "matched_policies": policy_matched,
            "matched_rules": policy_rules,
            "prompt_in": prompt_preview,
            "prompt_out": policy_redacted_preview if policy_redacted else prompt_preview,
            # Honesty: Before is often display-masked via _redact_trace_text so it
            # can look nearly identical to After even when policy redacted raw digits.
            "prompt_in_operator_masked": bool(prompt_in_operator_masked),
            "redaction_display_note": (
                "Before is display-masked for operator safety (raw PII is never shown "
                "in Scan Detail). The policy engine redacted the original request; "
                "Near-identical Before/After means both sides are masked views, not "
                "that redaction was a no-op."
                if prompt_in_operator_masked and policy_redacted
                else (
                    "Before is display-masked for operator safety (raw PII is never "
                    "shown in Scan Detail)."
                    if prompt_in_operator_masked
                    else ""
                )
            ),
            **_decision_source_fields("policy_engine"),
            "guard_reason": _policy_guard_reason(
                action=policy_action,
                matched_policies=policy_matched,
                matched_rules=policy_rules,
                detail=policy_detail if policy_action in ("block", "redact") else "",
            ),
            "tier": "policy",
            "confidence": policy_confidence,
        },
        {
            "name": "input_scan",
            "action": scan_action if blocked_stage != "input_scan" else _action("input_scan"),
            "latency_ms": _latency("input_scan"),
            "detail": input_scan_detail,
            "threat_type": threat_type,
            "confidence": confidence,
            "risk_score": zs.get("risk_score"),
            "scan_outcome": scan_outcome or zs.get("scan_outcome") or "",
            "redact_noop": redact_noop,
            "tier": tier,
            "matched_patterns": matched_patterns,
            # Tier-2 scans the POST-policy text. prompt_in is what it received
            # (policy-redacted when policy fired), prompt_out is what was forwarded
            # to the LLM — identical when Tier-2 found nothing further to redact.
            "prompt_submitted": scan_input_preview,
            "prompt_in": scan_input_preview,
            "prompt_out": forwarded_preview,
            **input_guard,
        },
        {
            "name": "kill_switch",
            "action": kill_switch_action,
            "latency_ms": _latency("kill_switch"),
            "detail": kill_switch_detail,
            **_empty_stage_why_fields(),
            **_decision_source_fields("kill_switch" if ks_rerouted or blocked_stage == "kill_switch" else ""),
            "guard_reason": kill_switch_detail,
            "prompt_in": scan_input_preview,
            "prompt_out": scan_input_preview if kill_switch_action not in ("block",) else "",
        },
        {
            "name": "model_routing",
            "action": routing_action,
            "latency_ms": _latency("model_routing"),
            "detail": routing_detail,
            "requested_model": requested,
            "selected_model": selected,
            "routed_model": selected,
            "route_destination": route_destination,
            "route_destination_label": route_destination_label,
            "routing_reason": routing_reason,
            **_decision_source_fields(decision_source),
            "policy_summary": policy_summary,
            "decision_factors": decision_factors,
            "weights": weights,
            "routing_score": routing_score,
            "candidate_count": candidate_count,
            "fallback_chain": fallback_chain,
            "evaluator_model": evaluator_model,
            "guard_reason": routing_guard_reason,
            "tier": "",
            "confidence": 0,
            "matched_policies": [],
            "matched_rules": [],
            "prompt_in": scan_input_preview,
            "prompt_out": scan_input_preview,
        },
        {
            "name": "model_input",
            "action": "skip" if _model_skipped else "allow",
            "latency_ms": _latency("model_input"),
            "detail": (
                "Prompt was not sent to the model (blocked upstream)" if _model_skipped
                else ("Sanitized (redacted) prompt delivered to the LLM"
                      if forwarded_prompt and forwarded_prompt != prompt
                      else "Prompt delivered to LLM")
            ),
            "content": "" if _model_skipped else forwarded_preview,
            "prompt_submitted": forwarded_preview,
            **_empty_stage_why_fields(),
            **_decision_source_fields("llm_request"),
            "guard_reason": (
                "Prompt was not sent to the model (blocked upstream)" if _model_skipped
                else (
                    "Sanitized prompt delivered to the LLM"
                    if forwarded_prompt and forwarded_prompt != prompt
                    else "Prompt delivered to LLM unchanged"
                )
            ),
            "prompt_in": scan_input_preview,
            "prompt_out": "" if _model_skipped else forwarded_preview,
        },
        {
            "name": "model_output",
            "action": _action("model_output", "skip" if _model_skipped else "allow"),
            "latency_ms": _latency("model_output"),
            "detail": "LLM inference complete" if response_text else ("Inference skipped or blocked upstream" if _model_skipped else "No completion body"),
            "content": _truncate(response_text, 2000) if response_text else "",
            **_empty_stage_why_fields(),
            **_decision_source_fields("llm_provider"),
            "guard_reason": (
                "Inference skipped or blocked upstream" if _model_skipped
                else ("LLM inference complete" if response_text else "No completion body")
            ),
            "prompt_in": "" if _model_skipped else forwarded_preview,
            "prompt_out": "" if _model_skipped else _truncate(response_text, 2000),
        },
        {
            "name": "output_guardrail",
            # Use the OUTPUT-enforcement-corrected action (already computed above:
            # = final_action when the output guard actually redact/rewrite/flag'd),
            # NOT the raw _action("output_guardrail") which only promotes input_scan
            # and so reported "allow" for a stage that genuinely redacted the output
            # — contradicting the stage's own "enforcement: REDACT" detail + masked
            # bytes (and mislabeling the Scan-Detail per-stage view green/allow).
            "action": output_stage_action,
            "latency_ms": _latency("output_guardrail"),
            "detail": (
                output_guard.get("guard_reason")
                or (
                    (zs.get("reason") or zs.get("detail"))
                    if _enforced_at_output
                    else (
                        "No model output to scan"
                        if not (response_text or "").strip()
                        else "Output guard evaluation — no findings"
                    )
                )
                or "Output guard evaluation"
            ),
            "content": _truncate(zs.get("redacted_response") or zs.get("rewritten_response") or response_text, 2000),
            # Operator input→output: raw model response in, guarded response out
            # (identical when the output guard made no change).
            "prompt_in": _truncate(response_text, 2000),
            "prompt_out": _truncate(zs.get("redacted_response") or zs.get("rewritten_response") or response_text, 2000),
            **output_guard,
        },
    ]

    # ── Server-side invariant: skip-after-block ──────────────────────────────
    # When the request was blocked at stage B, EVERY stage after B never ran. Force
    # those stages to action="skip" with a clean reason and strip any threat/guard/
    # pattern data, so a skipped stage can NEVER surface the upstream block reason.
    # This was the bug: input_scan (and model_routing, via _routing_stage_action)
    # bypassed the per-stage skip logic and showed action="allow" carrying the
    # policy block text ("policy scan block but tier-2 allow"). Enforcing it once,
    # centrally, makes the invariant hold for ALL stages regardless of each stage's
    # own action branch. No-op when not blocked → redact/flag/allow are unaffected.
    if is_blocked and blocked_stage:
        _stage_order = [
            "auth", "rate_limit", "policy", "input_scan", "kill_switch",
            "model_routing", "model_input", "model_output", "output_guardrail",
        ]
        _b_idx = _stage_order.index(blocked_stage) if blocked_stage in _stage_order else -1
        _blocked_label = blocked_stage.replace("_", " ")
        _clear_keys = (
            "threat_type", "guard_reason", "guard_action", "guard_model",
            "reason_code", "recommended_action", "matched_patterns",
            "matched_rules", "matched_policies", "guard_findings",
            "scan_outcome", "enforcement_source", "tier", "prompt_in", "prompt_out",
            "decision_source", "decision_source_label",
        )
        if _b_idx >= 0:
            for _s in stages:
                _s_idx = _stage_order.index(_s["name"]) if _s["name"] in _stage_order else -1
                if _s_idx > _b_idx:
                    _s["action"] = "skip"
                    _s["detail"] = f"Skipped — request was blocked upstream at {_blocked_label}"
                    _s["latency_ms"] = 0.0
                    for _k in _clear_keys:
                        if _k in _s:
                            _s[_k] = [] if isinstance(_s[_k], list) else ""
                    if "confidence" in _s:
                        _s["confidence"] = 0
                    if "risk_score" in _s:
                        _s["risk_score"] = None

    # Skipped stages never ran — zero latency (fixes model_output showing upstream
    # processing_time when the LLM was never called).
    for _s in stages:
        if _s.get("action") == "skip":
            _s["latency_ms"] = 0.0

    for _s in stages:
        normalize_stage_transparency(_s)

    stage_sum = sum(_round_ms(s.get("latency_ms")) for s in stages)
    overhead = metrics.get("overhead_ms") or 0.0
    telemetry = metrics.get("telemetry_ms") or 0.0
    wall = metrics.get("total_ms")
    if not wall and zs.get("processing_time_ms"):
        wall = _round_ms(zs.get("processing_time_ms"))
    if wall:
        overhead = _round_ms(max(0.0, wall - stage_sum - telemetry))
    total = _round_ms(stage_sum + overhead)

    guard_summary = input_guard
    if output_scan_verdict is not None or str(zs.get("detection_tier") or "") == "output_guard":
        guard_summary = output_guard if output_guard.get("guard_action") != "allow" else input_guard
    if is_blocked and blocked_stage == "input_scan":
        guard_summary = input_guard
    elif is_blocked and blocked_stage == "output_guardrail":
        guard_summary = output_guard

    fa = str(final_action or "allow").lower()
    out_raw = zs.get("redacted_response") or zs.get("rewritten_response") or response_text or ""
    output_withheld = False
    output_withheld_reason = ""
    if _model_skipped or (is_blocked and blocked_stage in _UPSTREAM_OF_MODEL):
        _bl = (blocked_stage or "policy").replace("_", " ")
        output_withheld = True
        output_withheld_reason = f"Response withheld — request blocked at {_bl}"
        output_text = ""
    elif is_blocked and blocked_stage == "output_guardrail":
        output_withheld = True
        output_withheld_reason = "Response withheld — output guard blocked delivery to client"
        # Operator forensics: show redacted-safe model bytes in trace (not client 403).
        # Display cap raised 2000 -> 20000 (2026-07-16) so the operator Output panel
        # shows the full response for all but the largest generations; the client
        # always receives the COMPLETE body regardless of this trace-display cap.
        output_text = _truncate(out_raw, 20000) if out_raw else ""
    else:
        # Display cap raised 2000 -> 20000 (2026-07-16) so the operator Output panel
        # shows the full response for all but the largest generations; the client
        # always receives the COMPLETE body regardless of this trace-display cap.
        output_text = _truncate(out_raw, 20000) if out_raw else ""

    input_was_redacted = bool(
        policy_redacted
        or (
            forwarded_preview
            and prompt_preview
            and forwarded_preview != prompt_preview
        )
    )

    trace_out: dict[str, Any] = {
        "stages": stages,
        "total_latency_ms": total,
        "stage_latency_sum_ms": stage_sum,
        "overhead_ms": overhead,
        "final_action": fa,
        "prompt_preview": prompt_preview,
        # PIPELINE-0022: trace-root redacted-safe I/O for operator Input/Output panels.
        "input_text": prompt_preview,
        "prompt_submitted": forwarded_preview,
        "output_text": output_text,
        "final_response": output_text,
        "output_withheld": output_withheld,
        "output_withheld_reason": output_withheld_reason if output_withheld else "",
        "input_was_redacted": input_was_redacted,
        "input_text_before": prompt_preview if input_was_redacted else "",
        "input_text_after": forwarded_preview if input_was_redacted else "",
        "guard_summary": guard_summary,
        "requested_model": requested,
        "routed_model": selected,
        "routing_reason": routing_reason,
        "routing": {
            "requested_model": requested,
            "selected_model": selected,
            "routed_model": selected,
            "route_destination": route_destination,
            "route_destination_label": route_destination_label,
            "routing_reason": routing_reason,
            "decision_source": decision_source,
            "decision_source_label": decision_source_label,
            "policy_summary": policy_summary,
            "decision_factors": decision_factors,
            "weights": weights,
            "routing_score": routing_score,
            "candidate_count": candidate_count,
            "fallback_chain": fallback_chain,
            "evaluator_model": evaluator_model,
        },
    }
    attach_latency_breakdown(trace_out)
    return trace_out
