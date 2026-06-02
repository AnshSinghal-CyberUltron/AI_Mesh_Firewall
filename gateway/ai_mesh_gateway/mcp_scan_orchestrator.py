"""MCP two-tier scan orchestrator (Tier-1 policy/presets → conditional Tier-2 Bedrock).

Emits a structured scan trace (Tier-1 + Tier-2 stages) and compliance tags for
SOC workflows. PII/secret detection is regex-based (``patterns`` module) plus the
optional Bedrock Tier-2 judge — there is no Presidio dependency.

Enforcement (``tag`` < ``redact`` < ``block``) is treated as a FLOOR: a ``block``
posture blocks on ANY finding regardless of the matching rule's authored action.
See :func:`_enforce_blocks`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from ai_mesh_shared.mcp_compliance_tags import tags_for_preset_or_entity

from mcp_scan_targets import extract_and_bind
from patterns import detect_pii, detect_secrets, get_compliance_tags, redact_all
from policy_engine import apply_redaction, evaluate_mcp_policies

LOG = logging.getLogger("gateway.mcp_scan")

_INJECTION_KEYWORDS = (
    "ignore previous instructions",
    "ignore all prior",
    "disregard your instructions",
    "system prompt",
    "jailbreak",
    "do anything now",
)


@dataclass
class McpFinding:
    entity_type: str
    score: float
    start: int
    end: int
    direction: str
    tier: str = "tier1"
    threat_type: str = ""
    detail: str = ""

    def to_finding_dict(self) -> dict[str, Any]:
        return {
            "entity_type": self.entity_type,
            "score": round(self.score, 4),
            "start": self.start,
            "end": self.end,
            "direction": self.direction,
            "tier": self.tier,
            "threat_type": self.threat_type,
            "detail": self.detail,
        }


@dataclass
class McpScanResult:
    findings: list[McpFinding] = field(default_factory=list)
    compliance_tags: list[str] = field(default_factory=list)
    blocked: bool = False
    # True when a tier had findings under a 'monitor' action (observe-only):
    # the call is allowed + audited as decision='monitor'. block/redact win over
    # monitor (block > redact > monitor), so this only drives the final decision
    # when nothing blocked or redacted.
    monitored: bool = False
    scan_trace: list[dict[str, Any]] = field(default_factory=list)

    @property
    def has_findings(self) -> bool:
        return bool(self.findings)


def _get_input_scanner():
    try:
        import main as gateway_main

        return getattr(gateway_main, "INPUT_SCANNER", None)
    except Exception:
        return None


def _get_policy_sync():
    try:
        import main as gateway_main

        return getattr(gateway_main, "POLICY_SYNC", None)
    except Exception:
        return None


def _direction_label(scan_direction: str) -> str:
    return "inbound" if scan_direction == "input" else "outbound"


def _tags_for_finding(finding: McpFinding) -> list[str]:
    if finding.threat_type in ("pii", "secret"):
        return get_compliance_tags(
            finding.detail.replace("Matched: ", "").split(", ")
            if "Matched:" in finding.detail
            else [finding.entity_type]
        )
    preset_tags = tags_for_preset_or_entity(finding.entity_type)
    if preset_tags:
        return preset_tags
    return tags_for_preset_or_entity(finding.threat_type or "policy_match")


def _merge_tags(existing: list[str], new: list[str]) -> list[str]:
    out = list(existing)
    for t in new:
        if t not in out:
            out.append(t)
    return out


def _effective_control(effective: dict[str, Any], tier: str, scan_direction: str) -> dict[str, Any]:
    key = f"{tier}_{scan_direction}"
    return effective.get(key) or {}


def _org_tier2_allowed(enabled_info: dict[str, Any] | None) -> bool | None:
    if not enabled_info:
        return None
    return enabled_info.get("mcp_tier2_enabled")


def _build_mcp_context(
    text: str,
    *,
    scan_direction: str,
    full_payload: Any,
) -> dict[str, Any]:
    if scan_direction == "input":
        return {
            "prompt": text,
            "response": "",
            "input_args": full_payload if isinstance(full_payload, (dict, list)) else full_payload,
            "output_data": None,
        }
    return {
        "prompt": "",
        "response": text,
        "input_args": None,
        "output_data": full_payload if isinstance(full_payload, (dict, list)) else full_payload,
    }


def _findings_from_policy_eval(
    eval_result,
    *,
    scan_direction: str,
    text: str,
) -> list[McpFinding]:
    if not eval_result.matched_rule_ids:
        return []
    mcp_dir = _direction_label(scan_direction)
    entity = "policy_match"
    if eval_result.matched_rule_names:
        entity = eval_result.matched_rule_names[-1]
    preset = None
    if eval_result.redaction_hints:
        preset = eval_result.redaction_hints[-1].get("preset")
        if preset:
            entity = str(preset)
    return [
        McpFinding(
            entity_type=entity,
            score=0.95,
            start=0,
            end=len(text),
            direction=mcp_dir,
            tier="tier1",
            threat_type=eval_result.action or "policy",
            detail=eval_result.message or "Policy rule matched",
        )
    ]


def _injection_match(text: str) -> bool:
    lower = text.lower()
    return any(kw in lower for kw in _INJECTION_KEYWORDS)


def _enforce_blocks(enforcement: str) -> bool:
    """``block`` enforcement is a FLOOR, not a ceiling.

    When the resolved tool/server posture is ``block``, ANY matched finding
    blocks the call regardless of the matching rule's *authored* action — e.g.
    a PII policy rule authored as ``redact`` still BLOCKS under a ``block``
    posture. This is the single source of truth consulted by every Tier-1 and
    Tier-2 branch so the policy-rule path can never diverge from the
    injection/PII/secret paths. That divergence (block gated on the rule's own
    action) was the A4 outbound-block defect: a ``redact``-authored rule under a
    ``block`` posture silently redacted instead of blocking.
    """
    return enforcement == "block"


def _resolve_tier_action(ctrl: dict[str, Any] | None, fallback: str) -> str:
    """Resolve the enforcement action for a single tier from its control row.

    Each tier's MCPScanControl row owns its action INDEPENDENTLY (monitor /
    redact / block). ``inherit`` (or an unset action) defers to the resolved
    server/tool ``scan_action`` passed as ``fallback`` — preserving prior
    behaviour for rows that don't opt into a per-tier override.

    Semantics applied by the scan functions: ``block`` blocks on any finding
    (floor); ``redact`` masks; ``monitor`` (and the legacy ``tag`` fallback)
    detect + tag + allow without mutating. Precedence across a request is
    block > redact > monitor, and a Tier-1 block short-circuits Tier-2.
    """
    a = ((ctrl or {}).get("action") or "inherit").strip()
    if a in ("inherit", ""):
        return fallback or "monitor"
    return a


async def _scan_text_tier1(
    text: str,
    *,
    scan_direction: str,
    enforcement: str,
    full_payload: Any,
    org_slug: str,
    server_slug: str,
    tool_name: str,
) -> tuple[str, list[McpFinding], bool]:
    """Run Tier-1 policy + preset evaluation on a single text fragment."""
    if not text:
        return text, [], False

    findings: list[McpFinding] = []
    blocked = False
    mutated = text
    mcp_dir = _direction_label(scan_direction)
    context = _build_mcp_context(text, scan_direction=scan_direction, full_payload=full_payload)

    policy_sync = _get_policy_sync()
    policies: list[dict[str, Any]] = []
    if policy_sync is not None and org_slug and server_slug:
        try:
            policies = policy_sync.get_policies_for_server(org_slug, server_slug, domain="mcp")
        except Exception as exc:
            LOG.warning("MCP policy bundle lookup failed: %s", exc)

    if policies:
        eval_result = evaluate_mcp_policies(policies, context, tool_name=tool_name or None)
        if eval_result.matched_rule_ids:
            findings.extend(
                _findings_from_policy_eval(eval_result, scan_direction=scan_direction, text=text)
            )
            policy_redacts = eval_result.action == "redact"
            if _enforce_blocks(enforcement):
                # A4 FIX: block posture blocks on ANY matched policy rule, even
                # one authored as redact/tag. Floor, not ceiling.
                blocked = True
            elif enforcement == "redact" and (policy_redacts or eval_result.redaction_hints):
                mutated = apply_redaction(text, eval_result.redaction_hints)
            return mutated, findings, blocked

    if _injection_match(text):
        findings.append(
            McpFinding(
                entity_type="prompt_injection",
                score=0.9,
                start=0,
                end=len(text),
                direction=mcp_dir,
                tier="tier1",
                threat_type="prompt_injection",
                detail="Matched injection keyword pattern",
            )
        )
        if _enforce_blocks(enforcement):
            blocked = True
        elif enforcement == "redact":
            mutated = redact_all(text)
        return mutated, findings, blocked

    pii = detect_pii(text)
    secrets = detect_secrets(text)
    if pii or secrets:
        kinds = list(pii.keys()) + list(secrets.keys())
        findings.append(
            McpFinding(
                entity_type=kinds[0] if kinds else "PII",
                score=0.85,
                start=0,
                end=len(text),
                direction=mcp_dir,
                tier="tier1",
                threat_type="pii" if pii else "secret",
                detail=f"Matched: {', '.join(kinds)}",
            )
        )
        if _enforce_blocks(enforcement):
            blocked = True
        elif enforcement == "redact":
            mutated = redact_all(text)
    return mutated, findings, blocked


async def _scan_text_tier2(
    text: str,
    *,
    scan_direction: str,
    enforcement: str,
    scanner,
    org_slug: str,
    org_tier2_override: bool | None,
    org_tier2_strict: bool,
    strict_mode: str,
) -> tuple[str, list[McpFinding], bool, str | None]:
    """Tier-2 Bedrock scan. Returns (text, findings, blocked, fallback_reason)."""
    if scanner is None:
        return text, [], False, "scanner_unavailable"

    mcp_dir = _direction_label(scan_direction)
    try:
        verdict = await scanner.scan_prompt_with_tier2(
            text,
            org_tier2_override=org_tier2_override,
            org_slug=org_slug,
            org_tier2_strict=org_tier2_strict,
        )
    except Exception as exc:
        LOG.warning("MCP Tier-2 scan failed: %s", exc)
        if strict_mode == "strict":
            return text, [], True, "tier2_error_strict"
        return text, [], False, "tier2_error_fail_open"

    findings: list[McpFinding] = []
    if verdict.tier and "tier" in verdict.tier.lower() and verdict.tier != "tier_1":
        if verdict.action in ("block", "redact", "flag"):
            findings.append(
                McpFinding(
                    entity_type=verdict.threat_type or "bedrock",
                    score=verdict.confidence,
                    start=0,
                    end=len(text),
                    direction=mcp_dir,
                    tier="tier2",
                    threat_type=verdict.threat_type,
                    detail=verdict.detail,
                )
            )
    if _enforce_blocks(enforcement) and verdict.action in ("block", "redact", "flag"):
        # A4 FIX (Tier-2 parity): block posture blocks on any actionable Bedrock
        # verdict, not only verdict.action == "block".
        return text, findings, True, None
    if enforcement == "redact" and verdict.action in ("redact", "block"):
        return redact_all(text), findings, False, None
    return text, findings, False, None


async def scan_mcp_payload(
    payload: Any,
    *,
    scan_direction: str,
    enforcement: str,
    effective_controls: dict[str, Any],
    enabled_info: dict[str, Any] | None = None,
    org_slug: str = "",
    server_slug: str = "",
    tool_name: str = "",
) -> tuple[Any, McpScanResult]:
    """Run Tier-1 then conditional Tier-2 on ``payload`` for input or output."""
    result = McpScanResult()
    tier1_ctrl = _effective_control(effective_controls, "tier1", scan_direction)
    tier2_ctrl = _effective_control(effective_controls, "tier2", scan_direction)
    # Per-tier action: each tier's control row owns its action; 'inherit'/unset
    # defers to the server/tool action passed as ``enforcement``.
    tier1_action = _resolve_tier_action(tier1_ctrl, enforcement)
    result_redacted = False

    if not tier1_ctrl.get("enabled", True):
        result.scan_trace.append(
            {
                "scan_stage": "tier1_skipped",
                "tier": "tier1",
                "direction": scan_direction,
                "enabled": False,
            }
        )
        return payload, result

    target_mode = tier1_ctrl.get("target_mode") or "entire"
    key_path = tier1_ctrl.get("key_path") or ""
    state_ref, targets = extract_and_bind(
        payload,
        target_mode=target_mode,
        key_path=key_path,
    )

    scanner = _get_input_scanner()
    tier1_blocked = False

    for text, setter, path_label in targets:
        if not text:
            continue
        new_text, findings, blocked = await _scan_text_tier1(
            text,
            scan_direction=scan_direction,
            enforcement=tier1_action,
            full_payload=state_ref[0],
            org_slug=org_slug,
            server_slug=server_slug,
            tool_name=tool_name,
        )
        result.findings.extend(findings)
        if findings:
            for f in findings:
                result.compliance_tags = _merge_tags(
                    result.compliance_tags, _tags_for_finding(f)
                )
            if tier1_action == "monitor":
                result.monitored = True
        if blocked:
            tier1_blocked = True
        if new_text != text and tier1_action == "redact":
            setter(new_text)
            result_redacted = True
        result.scan_trace.append(
            {
                "scan_stage": "tier1",
                "tier": "tier1",
                "direction": scan_direction,
                "target_mode": target_mode,
                "key_path": path_label,
                "strict_mode": tier1_ctrl.get("strict_mode"),
                "action": tier1_action,
                "effective_control_id": tier1_ctrl.get("control_id"),
                "finding_count": len(findings),
                "policy_engine": True,
            }
        )

    if tier1_blocked:
        result.blocked = True
        return payload, result

    mutable = state_ref[0]

    if not tier2_ctrl.get("enabled", False):
        return (mutable if result_redacted else payload), result

    org_override = _org_tier2_allowed(enabled_info)
    if org_override is False:
        result.scan_trace.append(
            {"scan_stage": "tier2_skipped", "tier": "tier2", "reason": "org_mcp_tier2_disabled"}
        )
        return (mutable if result_redacted else payload), result

    strict_mode = tier2_ctrl.get("strict_mode") or "strict"
    tier2_action = _resolve_tier_action(tier2_ctrl, enforcement)
    org_strict = bool((enabled_info or {}).get("tier2_strict", True))

    for text, setter, path_label in targets:
        if not text:
            continue
        new_text, t2_findings, t2_blocked, fallback = await _scan_text_tier2(
            text,
            scan_direction=scan_direction,
            enforcement=tier2_action,
            scanner=scanner,
            org_slug=org_slug,
            org_tier2_override=org_override,
            org_tier2_strict=org_strict,
            strict_mode=strict_mode,
        )
        result.findings.extend(t2_findings)
        if t2_findings:
            for f in t2_findings:
                result.compliance_tags = _merge_tags(
                    result.compliance_tags, _tags_for_finding(f)
                )
            if tier2_action == "monitor":
                result.monitored = True
        result.scan_trace.append(
            {
                "scan_stage": "tier2",
                "tier": "tier2",
                "direction": scan_direction,
                "target_mode": target_mode,
                "key_path": path_label,
                "strict_mode": strict_mode,
                "action": tier2_action,
                "effective_control_id": tier2_ctrl.get("control_id"),
                "fallback_reason": fallback,
                "finding_count": len(t2_findings),
            }
        )
        if t2_blocked:
            result.blocked = True
            return payload, result
        if fallback and strict_mode == "strict" and "strict" in fallback:
            result.blocked = True
            return payload, result
        if new_text != text and tier2_action == "redact":
            setter(new_text)
            result_redacted = True

    out = state_ref[0] if result_redacted else payload
    return out, result
