"""
Operator-facing pipeline stage trace for Module 1.1 Attack Simulator / dashboard.
Built server-side with full routing and prompt context (not subject to zeroshield redaction).
"""

from __future__ import annotations

from typing import Any

GUARD_MODEL_LABEL = "ZeroShield Guard Model"
PATTERN_ENGINE_LABEL = "ZeroShield Pattern Engine"
OUTPUT_GUARD_LABEL = "ZeroShield Output Guard"

REASON_CODE_LABELS: dict[str, str] = {
    "model_recommended_block": "The Guard Model classified this content as unsafe and recommended blocking the request.",
    "model_recommended_redact": "The Guard Model recommended redacting sensitive or policy-violating segments before forwarding.",
    "model_recommended_monitor": "The Guard Model flagged this content for monitoring — review recommended but not blocked.",
    "model_recommendation": "Decision follows the Guard Model's structured recommendation.",
    "model_refusal": "The Guard Model could not complete analysis and applied a conservative block.",
    "score_threshold_block": "Risk score exceeded the automatic block threshold.",
    "score_threshold_flag": "Risk score exceeded the advisory flag threshold.",
    "findings_with_allow": "Threat signals were detected, but policy escalated to flag instead of block.",
    "tier2_pass": "Guard Model (Tier-2) scan completed — content assessed as clean; no enforcement action required.",
    "bedrock_degraded": "Guard Model was degraded; request flagged for manual review.",
    "parse_failure_conservative": "Guard Model response could not be parsed; conservative enforcement applied.",
    "client_error": "Guard Model client error — conservative handling applied.",
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
) -> dict[str, Any]:
    """Operator-facing Guard Model explanation for pipeline stages."""
    zs = zs if isinstance(zs, dict) else {}
    sv = verdict
    reason_code = str(getattr(sv, "reason_code", None) or zs.get("reason_code") or "").strip()
    scan_meta = getattr(sv, "scan_meta", None) if sv is not None else None
    if not isinstance(scan_meta, dict):
        scan_meta = zs.get("scan_meta") if isinstance(zs.get("scan_meta"), dict) else {}

    threat_type = str(getattr(sv, "threat_type", None) or zs.get("threat_type") or "").strip()
    confidence = getattr(sv, "confidence", None)
    if confidence is None:
        confidence = zs.get("confidence", 0)
    try:
        confidence_f = float(confidence or 0)
    except (TypeError, ValueError):
        confidence_f = 0.0

    detail = (
        getattr(sv, "detail", None)
        or zs.get("detail")
        or zs.get("reason")
        or ""
    )
    matched_patterns = list(
        getattr(sv, "matched_patterns", None) or zs.get("matched_patterns") or []
    )
    recommended = str(
        scan_meta.get("recommended_action")
        or zs.get("recommended_action")
        or ""
    ).strip().lower()

    enforcement_action = stage_action
    if final_action in ("block", "redact", "rewrite", "flag") and stage_action == "allow":
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
    if detail:
        lines.append(str(detail).strip())

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
        lines.append("Evidence: " + "; ".join(findings[:3]))

    policy_note = ""
    if enforcement_action == "allow" and recommended in ("block", "redact"):
        policy_note = "Org policy allowed the request despite the Guard Model recommendation."
    elif enforcement_action in ("block", "redact", "rewrite") and recommended == "allow":
        policy_note = "Policy enforcement overrode the Guard Model allow recommendation."

    guard_reason = "\n".join(lines)
    if policy_note:
        guard_reason = guard_reason + "\n" + policy_note

    return {
        "guard_model": scanner_label,
        "guard_action": enforcement_action,
        "guard_reason": guard_reason,
        "reason_code": reason_code,
        "recommended_action": recommended,
        "guard_findings": findings[:8],
        "enforcement_source": "zeroshield_guard_model",
    }


def _metrics(stage_metrics: dict | None) -> dict[str, float]:
    sm = stage_metrics if isinstance(stage_metrics, dict) else {}
    tier1 = _round_ms(sm.get("tier1_ms"))
    tier2 = _round_ms(sm.get("tier2_ms"))
    return {
        "auth_ms": _round_ms(sm.get("auth_ms")),
        "policy_ms": _round_ms(sm.get("policy_ms")),
        "input_scan_ms": _round_ms(tier1 + tier2, tier1 or tier2),
        "upstream_ms": _round_ms(sm.get("upstream_ms")),
        "total_hint_ms": _round_ms(sm.get("total_ms")),
    }


def build_pipeline_trace(
    *,
    prompt: str = "",
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
) -> dict[str, Any]:
    """Return { stages: [...], total_latency_ms, prompt_preview } for the UI."""
    zs = zeroshield if isinstance(zeroshield, dict) else {}
    routing = route_metadata if isinstance(route_metadata, dict) else (zs.get("routing") or {})
    if not isinstance(routing, dict):
        routing = {}

    metrics = _metrics(stage_metrics)
    prompt_preview = _truncate(prompt)
    sv = scan_verdict
    threat_type = getattr(sv, "threat_type", None) or zs.get("threat_type") or ""
    confidence = getattr(sv, "confidence", None)
    if confidence is None:
        confidence = zs.get("confidence", 0)
    tier = getattr(sv, "tier", None) or zs.get("detection_tier") or ""
    matched_patterns = list(getattr(sv, "matched_patterns", None) or zs.get("matched_patterns") or [])
    scan_detail = getattr(sv, "detail", None) or zs.get("detail") or zs.get("reason") or blocked_detail or ""

    is_blocked = final_action == "block"
    is_skip_after = bool(blocked_stage) and is_blocked

    scan_action = getattr(sv, "action", None) or zs.get("action") or "allow"
    if scan_action not in ("allow", "flag", "block", "redact", "rewrite"):
        scan_action = "allow"
    if is_blocked and blocked_stage == "input_scan":
        scan_action = "block"
    elif not is_blocked and scan_action == "block":
        scan_action = "allow"

    def _latency(name: str, explicit: float | None = None) -> float:
        if explicit is not None:
            return _round_ms(explicit)
        mapping = {
            "auth": metrics["auth_ms"],
            "rate_limit": max(metrics["auth_ms"] * 0.05, 0.1) if metrics["auth_ms"] else 0.1,
            "policy": metrics["policy_ms"] or (0.2 if not is_blocked else 0.5),
            "input_scan": metrics["input_scan_ms"] or _round_ms(zs.get("processing_time_ms")),
            "kill_switch": 0.1,
            "model_routing": 0.5,
            "model_input": 0.1,
            "model_output": metrics["upstream_ms"] or _round_ms(zs.get("processing_time_ms")),
            "output_guardrail": 0.2,
        }
        return mapping.get(name, 0.1)

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
    routing_reason = routing.get("routing_reason") or zs.get("routing_reason") or ""
    decision_source = routing.get("decision_source") or zs.get("decision_source") or ""
    policy_summary = routing.get("policy_summary") or zs.get("policy_summary") or ""
    decision_factors = routing.get("decision_factors") or zs.get("decision_factors") or []
    weights = routing.get("weights") or zs.get("weights") or {}

    input_guard = build_guard_fields(
        verdict=sv,
        stage_action=scan_action,
        final_action=final_action,
        tier=tier,
        zs=zs,
        output=False,
    )
    output_guard_zs = {
        **zs,
        "detection_tier": zs.get("detection_tier") or "output_guard",
    }
    output_stage_action = _action("output_guardrail")
    if final_action in ("redact", "rewrite", "flag") and zs.get("detection_tier") == "output_guard":
        output_stage_action = final_action
    output_guard = build_guard_fields(
        verdict=output_scan_verdict,
        stage_action=output_stage_action,
        final_action=final_action,
        tier="output_guard",
        zs=output_guard_zs,
        output=True,
    )

    stages: list[dict[str, Any]] = [
        {
            "name": "auth",
            "action": _action("auth"),
            "latency_ms": _latency("auth"),
            "detail": "Gateway API key accepted" if _action("auth") != "block" else "Gateway API key invalid or missing",
        },
        {
            "name": "rate_limit",
            "action": _action("rate_limit"),
            "latency_ms": _latency("rate_limit"),
            "detail": blocked_detail if blocked_stage == "rate_limit" else "Within org TPM / RPM limits",
        },
        {
            "name": "policy",
            "action": _action("policy"),
            "latency_ms": _latency("policy"),
            "detail": scan_detail if blocked_stage == "policy" else "Policy Management evaluation complete",
            "matched_policies": matched_patterns,
        },
        {
            "name": "input_scan",
            "action": scan_action if blocked_stage != "input_scan" else _action("input_scan"),
            "latency_ms": _latency("input_scan"),
            "detail": (
                scan_detail
                or (
                    f"Input blocked — {threat_type}"
                    + (f" ({confidence:.0%} confidence)" if confidence else "")
                    if scan_action == "block" and threat_type
                    else (
                        f"Scan completed — {threat_type}"
                        + (f" ({confidence:.0%} confidence)" if confidence else "")
                        + "; allowed through"
                        if threat_type and threat_type not in ("none", "")
                        else "Input scan completed — no threats detected"
                    )
                )
            ),
            "threat_type": threat_type,
            "confidence": confidence,
            "risk_score": zs.get("risk_score"),
            "scan_outcome": zs.get("scan_outcome"),
            "tier": tier,
            "matched_patterns": matched_patterns,
            "prompt_submitted": prompt_preview,
            **input_guard,
        },
        {
            "name": "kill_switch",
            "action": _action("kill_switch"),
            "latency_ms": _latency("kill_switch"),
            "detail": blocked_detail if blocked_stage == "kill_switch" else "No active kill-switch for this model",
        },
        {
            "name": "model_routing",
            "action": _action("model_routing", "needs_model" if final_action == "needs_model" else "allow"),
            "latency_ms": _latency("model_routing"),
            "detail": routing_reason or (f"Routed to {selected}" if selected else "Model routing evaluated"),
            "requested_model": requested,
            "selected_model": selected,
            "routed_model": selected,
            "routing_reason": routing_reason,
            "decision_source": decision_source,
            "policy_summary": policy_summary,
            "decision_factors": decision_factors,
            "weights": weights,
        },
        {
            "name": "model_input",
            "action": "skip" if is_blocked else "allow",
            "latency_ms": _latency("model_input"),
            "detail": "Prompt delivered to LLM" if not is_blocked else "Prompt was not sent to the model (blocked upstream)",
            "content": "" if is_blocked else prompt_preview,
            "prompt_submitted": prompt_preview,
        },
        {
            "name": "model_output",
            "action": _action("model_output", "skip" if is_blocked else "allow"),
            "latency_ms": _latency("model_output"),
            "detail": "LLM inference complete" if response_text else ("Inference skipped or blocked upstream" if is_blocked else "No completion body"),
            "content": _truncate(response_text, 2000) if response_text else "",
        },
        {
            "name": "output_guardrail",
            "action": _action("output_guardrail"),
            "latency_ms": _latency("output_guardrail"),
            "detail": (
                output_guard.get("guard_reason")
                or zs.get("reason")
                or zs.get("detail")
                or "Output guard evaluation"
            ),
            "content": _truncate(zs.get("redacted_response") or zs.get("rewritten_response") or response_text, 2000),
            **output_guard,
        },
    ]

    total = sum(_round_ms(s.get("latency_ms")) for s in stages)
    if metrics["total_hint_ms"]:
        total = metrics["total_hint_ms"]
    elif zs.get("processing_time_ms"):
        total = max(total, _round_ms(zs.get("processing_time_ms")))

    guard_summary = input_guard
    if output_scan_verdict is not None or str(zs.get("detection_tier") or "") == "output_guard":
        guard_summary = output_guard if output_guard.get("guard_action") != "allow" else input_guard
    if is_blocked and blocked_stage == "input_scan":
        guard_summary = input_guard
    elif is_blocked and blocked_stage == "output_guardrail":
        guard_summary = output_guard

    return {
        "stages": stages,
        "total_latency_ms": total,
        "prompt_preview": prompt_preview,
        "guard_summary": guard_summary,
    }
