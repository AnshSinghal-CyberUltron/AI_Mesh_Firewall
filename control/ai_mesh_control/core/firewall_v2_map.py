"""Which FirewallConfig fields the v2 plan compiler can represent.

A field is ``mapped`` when it becomes a rule, a mode, or an action
``compile_plan`` accepts. A field is ``unsupported`` when saving a new value
would otherwise be stored and then ignored. Identity fields are not selections.

An unchanged unsupported value is not an error: the console form posts the
whole row, and rejecting the stored value would block every other edit.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

_MESSAGE = "{field} is unsupported. The plan compiler has no rule, mode, or action for this field."

# off: values that turn the feature off. A change to one of these is allowed.
# reject: mapped values the compiler refuses.
FIELDS: dict[str, dict[str, Any]] = {
    "organization": {"kind": "identity"},
    "updated_at": {"kind": "identity"},
    "connected_models": {"kind": "identity"},
    "allowed_models_list": {"kind": "identity"},
    "governance_stale_models": {"kind": "identity"},
    "firewall_enabled": {"kind": "mapped"},
    "enforcement_mode": {"kind": "mapped", "reject": ("audit",)},
    "content_filtering_enabled": {"kind": "mapped"},
    "pii_detection_enabled": {"kind": "mapped"},
    "toxicity_threshold": {"kind": "mapped"},
    "jailbreak_detection_enabled": {"kind": "mapped"},
    "prompt_injection_threshold": {"kind": "mapped"},
    "tier2_fail_closed_enabled": {"kind": "mapped"},
    "response_filtering_enabled": {"kind": "mapped"},
    "output_pii_enabled": {"kind": "mapped"},
    "output_pii_action": {"kind": "mapped", "reject": ("rewrite",)},
    "output_credential_enabled": {"kind": "mapped"},
    "output_credential_action": {"kind": "mapped", "reject": ("rewrite",)},
    "output_ip_leakage_enabled": {"kind": "mapped"},
    "output_ip_leakage_action": {"kind": "mapped", "reject": ("rewrite",)},
    "log_level": {"kind": "unsupported"},
    "rate_limit_enabled": {"kind": "unsupported", "off": (False,)},
    "requests_per_minute": {"kind": "unsupported"},
    "burst_limit": {"kind": "unsupported"},
    "org_tpm_limit": {"kind": "unsupported"},
    "blocked_keywords": {"kind": "unsupported"},
    "model_isolation_enabled": {"kind": "unsupported", "off": (False,)},
    "allowed_models": {"kind": "unsupported"},
    "default_model": {"kind": "unsupported"},
    "semantic_analysis_enabled": {"kind": "unsupported", "off": (False,)},
    "tier2_execution_mode": {"kind": "unsupported"},
    "tier2_stream_hold_enabled": {"kind": "unsupported", "off": (False,)},
    "tier2_stream_hold_timeout_ms": {"kind": "unsupported"},
    "tier2_enabled": {"kind": "unsupported", "off": (None, False)},
    "mcp_tier2_enabled": {"kind": "unsupported", "off": (None, False)},
    "mcp_ext_scan_action": {"kind": "unsupported", "off": ("tag",)},
    "tier2_strict": {"kind": "unsupported"},
    "mcp_policy_only_enforcement": {"kind": "unsupported", "off": (False,)},
    "factuality_check_enabled": {"kind": "unsupported", "off": (False,)},
    "hallucination_grounding_mode": {"kind": "unsupported"},
    "hallucination_grounding_threshold": {"kind": "unsupported"},
    "hallucination_grounding_model": {"kind": "unsupported"},
    "output_policy_enabled": {"kind": "unsupported", "off": (False,)},
    "output_policy_action": {"kind": "unsupported", "reject": ("rewrite",)},
    "output_hallucination_action": {"kind": "unsupported", "reject": ("rewrite",)},
    "output_incident_logging_enabled": {"kind": "unsupported", "off": (False,)},
    "max_response_tokens": {"kind": "unsupported"},
    "rag_enabled": {"kind": "unsupported", "off": (False,)},
    "vector_db_isolation": {"kind": "unsupported", "off": (False,)},
    "rag_max_documents": {"kind": "unsupported"},
    "rag_relevance_threshold": {"kind": "unsupported"},
    "rag_redaction_enabled": {"kind": "unsupported", "off": (False,)},
    "rag_tier2_enabled": {"kind": "unsupported", "off": (False,)},
    "threat_intel_enabled": {"kind": "unsupported", "off": (False,)},
    "auto_block_threats": {"kind": "unsupported", "off": (False,)},
    "threat_score_threshold": {"kind": "unsupported"},
    "audit_logging_enabled": {"kind": "unsupported", "off": (False,)},
    "retention_days": {"kind": "unsupported"},
    "compliance_frameworks": {"kind": "unsupported"},
    "routing_risk_weight": {"kind": "unsupported"},
    "routing_cost_weight": {"kind": "unsupported"},
    "routing_latency_weight": {"kind": "unsupported"},
    "routing_priority_weight": {"kind": "unsupported"},
    "default_data_sensitivity": {"kind": "unsupported"},
    "routing_enabled": {"kind": "unsupported", "off": (False,)},
}

_INPUTS = (
    ("pii_detection_enabled", "pii", "redact"),
    ("jailbreak_detection_enabled", "jailbreak", "block"),
)
_OUTPUTS = (
    ("output_pii_enabled", "output_pii_action", "pii", "redact"),
    ("output_credential_enabled", "output_credential_action", "credential", "block"),
    ("output_ip_leakage_enabled", "output_ip_leakage_action", "exfil", "flag"),
)


def selection_errors(
    incoming: Mapping[str, Any],
    stored: Mapping[str, Any] | None,
) -> dict[str, str]:
    """Field-keyed errors for a new value the compiler cannot represent."""
    previous = stored or {}
    errors: dict[str, str] = {}
    for field, spec in FIELDS.items():
        if field not in incoming or _same(incoming[field], previous.get(field)):
            continue
        if _rejected(spec, incoming[field]):
            errors[field] = _MESSAGE.format(field=field)
    return errors


def drafts_for(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Rules a supported payload compiles to. Unsupported fields are absent here."""
    if payload.get("firewall_enabled") is False:
        return []
    mode = "monitor" if payload.get("enforcement_mode") == "monitor" else "enforce"
    posture = "fail_closed" if payload.get("tier2_fail_closed_enabled", True) else "fail_open"
    drafts: list[dict[str, Any]] = []
    if payload.get("content_filtering_enabled", True):
        drafts.extend(_input_drafts(payload, mode, posture))
    if payload.get("response_filtering_enabled", True):
        drafts.extend(_output_drafts(payload, mode, posture))
    return drafts


def _input_drafts(payload: Mapping[str, Any], mode: str, posture: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for flag, category, action in _INPUTS:
        if payload.get(flag, True):
            rows.append(_draft(category, "input", action, mode, posture, None))
    if payload.get("content_filtering_enabled", True):
        rows.append(
            _draft(
                "prompt_injection",
                "input",
                "block",
                mode,
                posture,
                _float(payload.get("prompt_injection_threshold", 0.8)),
            ),
        )
        rows.append(
            _draft(
                "toxicity",
                "input",
                "flag",
                mode,
                posture,
                _float(payload.get("toxicity_threshold", 0.7)),
            ),
        )
    return rows


def _output_drafts(payload: Mapping[str, Any], mode: str, posture: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for flag, action_field, category, default in _OUTPUTS:
        if not payload.get(flag, True):
            continue
        action = str(payload.get(action_field) or default).strip().lower()
        if action not in {"allow", "flag", "redact", "block"}:
            continue
        rows.append(_draft(category, "output", action, mode, posture, None))
    return rows


def _draft(
    category: str,
    scope: str,
    action: str,
    mode: str,
    posture: str,
    threshold: float | None,
) -> dict[str, Any]:
    return {
        "rule_id": f"fw-{category}-{scope}",
        "category": category,
        "scope": scope,
        "action": action,
        "mode": mode,
        "on_unavailable": posture,
        "threshold": threshold,
        "surfaces": ["chat"],
    }


def _rejected(spec: Mapping[str, Any], value: Any) -> bool:
    if "reject" in spec and value in spec["reject"]:
        return True
    if spec["kind"] != "unsupported":
        return False
    off = spec.get("off")
    return off is None or value not in off


def _same(left: Any, right: Any) -> bool:
    if isinstance(left, float) or isinstance(right, float):
        if left is None or right is None:
            return left is right
        return math.isclose(float(left), float(right), rel_tol=0.0, abs_tol=1e-9)
    return left == right


def _float(value: Any) -> float | None:
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    return None
