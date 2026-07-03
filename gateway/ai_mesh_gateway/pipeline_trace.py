"""
Operator-facing pipeline stage trace for Module 1.1 Attack Simulator / dashboard.
Built server-side with full routing and prompt context. Prompt/response text
fields are deterministically PII-redacted before they are written into the trace
so raw PII is never persisted or echoed back to the client (see `_truncate`).
"""

from __future__ import annotations

from typing import Any

# Deterministic PII/secret redactor. Applied to every prompt/response text field
# written into the trace so raw PII never leaks into the operator UI / SSE / API
# (redact_all is a no-op on benign text, so the preview utility is preserved).
# Resolved once at import time via the same dual-import idiom used elsewhere.
try:  # pragma: no cover - import shim (script vs package execution)
    from patterns import redact_all as _redact_all  # type: ignore
    from patterns import contains_smart_redaction_markers as _has_smart_masks  # type: ignore
except ImportError:  # pragma: no cover
    try:
        from .patterns import redact_all as _redact_all  # type: ignore
        from .patterns import contains_smart_redaction_markers as _has_smart_masks  # type: ignore
    except Exception:  # pragma: no cover
        _redact_all = None  # type: ignore
        _has_smart_masks = lambda _t: False  # type: ignore

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
}


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
    forwarded_prompt: str = "",
    policy_redacted_prompt: str = "",
    policy_redacted_flag: bool | None = None,
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

    scan_action = getattr(sv, "action", None) or _zs_scan.get("action") or "allow"
    if scan_action not in ("allow", "flag", "block", "redact", "rewrite"):
        scan_action = "allow"
    if is_blocked and blocked_stage == "input_scan":
        scan_action = "block"
    elif not is_blocked and scan_action == "block":
        scan_action = "allow"
    is_output_only = zs.get("detection_tier") == "output_guard"
    has_input_threat = bool(matched_patterns) or bool(threat_type)
    # Reflect INPUT-stage enforcement on the input_scan badge even when the request
    # still proceeds (redact/rewrite/flag ≠ block). Neither the scan verdict's own
    # .action nor the global final_action is reliable here: redaction is
    # non-blocking so .action often stays "allow", and final_action may carry an
    # OUTPUT-guard verdict (so it can be "redact" for an output-only redaction
    # where the input was clean). The authoritative signal that the INPUT was
    # sanitized is that the prompt FORWARDED to the LLM differs from the original
    # AND an input-tier threat was detected. Output-guard redactions
    # (detection_tier == output_guard) leave the input untouched → must NOT colour
    # the input stage.
    if not is_blocked and scan_action in ("allow", "flag"):
        # Tier-2 redacted only if the FORWARDED prompt differs from what Tier-2
        # actually received (the post-policy text) — NOT from the raw prompt. If
        # the policy stage already masked everything, forwarded == scan_input and
        # input_scan stays clean (no double-attribution of the same redaction).
        input_modified = bool(forwarded_prompt) and forwarded_prompt != scan_input_prompt
        if input_modified and has_input_threat and not is_output_only:
            scan_action = "rewrite" if final_action == "rewrite" else "redact"
        elif (
            final_action == "redact"
            and has_input_threat
            and not is_output_only
            and _has_smart_masks(scan_input_prompt or "")
            and forwarded_prompt == scan_input_prompt
        ):
            scan_action = "redact"
        elif final_action == "flag" and has_input_threat and not is_output_only:
            scan_action = "flag"
    elif (
        not is_blocked
        and scan_action in ("redact", "rewrite")
        and forwarded_prompt
        and forwarded_prompt == scan_input_prompt
    ):
        # Tier-2's nominal action was redact/rewrite but it did NOT change the
        # text — the policy stage had already masked everything. Show input_scan
        # as clean instead of claiming a redaction it didn't perform.
        # PIPELINE-0012: smart partial masks are unchanged by design — still show
        # input_scan as redact when a threat was detected on masked shapes.
        if not (
            has_input_threat
            and _has_smart_masks(scan_input_prompt or "")
        ):
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
    routing_reason = _sanitize_routing_reason(
        routing.get("routing_reason") or zs.get("routing_reason") or ""
    )
    decision_source = routing.get("decision_source") or zs.get("decision_source") or ""
    decision_source_label = _format_decision_source(decision_source)
    policy_summary = routing.get("policy_summary") or zs.get("policy_summary") or ""
    decision_factors = routing.get("decision_factors") or zs.get("decision_factors") or []
    weights = routing.get("weights") or zs.get("weights") or {}

    # Where did the request-level enforcement (block/redact/...) actually happen?
    # The output guard forces detection_tier="output_guard" on its zs copy below,
    # so capture the ORIGINAL tier first. Input/policy enforcement belongs to the
    # input guard; only detection_tier=="output_guard" belongs to the output guard.
    _orig_detection_tier = str(zs.get("detection_tier") or "")
    _enforced_at_output = _orig_detection_tier == "output_guard"
    input_guard = build_guard_fields(
        verdict=sv,
        stage_action=scan_action,
        final_action=final_action,
        tier=tier,
        zs=_zs_scan,
        output=False,
        final_attributed=not _enforced_at_output,
    )
    output_guard_zs = {
        **zs,
        "detection_tier": zs.get("detection_tier") or "output_guard",
    }
    output_stage_action = _action("output_guardrail")
    if final_action in ("redact", "rewrite", "flag") and _enforced_at_output:
        output_stage_action = final_action
    output_guard = build_guard_fields(
        verdict=output_scan_verdict,
        stage_action=output_stage_action,
        final_action=final_action,
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
            "action": (
                "block" if blocked_stage == "policy"
                else "redact" if policy_redacted
                else _action("policy")
            ),
            "latency_ms": _latency("policy"),
            "detail": (
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
                    )
                )
            ),
            "matched_policies": policy_matched,
            "matched_rules": policy_rules,
            # Operator input→output for the hover card: original prompt in,
            # policy-redacted prompt out (only when policy actually masked).
            "prompt_in": prompt_preview,
            "prompt_out": policy_redacted_preview if policy_redacted else "",
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
        },
        {
            "name": "model_routing",
            "action": routing_action,
            "latency_ms": _latency("model_routing"),
            "detail": routing_detail,
            "requested_model": requested,
            "selected_model": selected,
            "routed_model": selected,
            "routing_reason": routing_reason,
            "decision_source": decision_source,
            "decision_source_label": decision_source_label,
            "policy_summary": policy_summary,
            "decision_factors": decision_factors,
            "weights": weights,
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
        },
        {
            "name": "model_output",
            "action": _action("model_output", "skip" if _model_skipped else "allow"),
            "latency_ms": _latency("model_output"),
            "detail": "LLM inference complete" if response_text else ("Inference skipped or blocked upstream" if _model_skipped else "No completion body"),
            "content": _truncate(response_text, 2000) if response_text else "",
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
        )
        if _b_idx >= 0:
            for _s in stages:
                _s_idx = _stage_order.index(_s["name"]) if _s["name"] in _stage_order else -1
                if _s_idx > _b_idx:
                    _s["action"] = "skip"
                    _s["detail"] = f"Skipped — request was blocked upstream at {_blocked_label}"
                    for _k in _clear_keys:
                        if _k in _s:
                            _s[_k] = [] if isinstance(_s[_k], list) else ""
                    if "confidence" in _s:
                        _s["confidence"] = 0
                    if "risk_score" in _s:
                        _s["risk_score"] = None

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
