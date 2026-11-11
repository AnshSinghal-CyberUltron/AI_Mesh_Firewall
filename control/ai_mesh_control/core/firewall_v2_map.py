"""Which FirewallConfig fields the v2 plan compiler can represent.

A field is ``mapped`` when it becomes a rule, a mode, or an action
``compile_plan`` accepts. A field is ``unsupported`` when saving a new value
would otherwise be stored and then ignored. Identity fields are not selections.

An unchanged unsupported value is not an error: the console form posts the
whole row, and rejecting the stored value would block every other edit.
"""

from __future__ import annotations

import math
import os
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

_RETIRED = (
    "{field} is retired and not enforced (owner {owner}): {reason}. "
    "Change refused so the value is never stored and ignored."
)
_REWRITE = "{field}: 'rewrite' is not enforced (owner GW33)."

# off: values that turn a retired feature off. A change to one of these is allowed.
# reject: built/mapped values the compiler refuses.
# GW33: ``rewrite`` is no longer rejected on the four mapped action fields. It is a real, template-driven
# action on the v2 gateway, gated PER ORG by ``rewrite_served_by_v2`` (see ``REWRITE_FIELDS`` below).
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
    "output_pii_action": {"kind": "mapped"},
    "output_credential_enabled": {"kind": "mapped"},
    "output_credential_action": {"kind": "mapped"},
    "output_ip_leakage_enabled": {"kind": "mapped"},
    "output_ip_leakage_action": {"kind": "mapped"},
    "log_level": {
        "kind": "retired",
        "owner": "GW27",
        "reason": "v2 observability is the typed GW14d exposition with bounded cardinality; there is no per-org log-detail knob to build",
    },
    "rate_limit_enabled": {"kind": "built", "via": "controls.rate"},
    "requests_per_minute": {"kind": "built", "via": "controls.rate"},
    "burst_limit": {"kind": "built", "via": "controls.rate"},
    "org_tpm_limit": {"kind": "built", "via": "controls.org_tpm_limit"},
    # GW36 S4 (Req 8): the per-org token budget is NOT a firewall-plan field. The gateway
    # reads it as a signed StateKind.BUDGET record written by the projector and spends it
    # down through the per-org BudgetLease. It is the 46th former ``unsupported`` entry,
    # delegated to GW36 and outside the 45 that GW27 dispositions.
    "org_token_budget": {
        "kind": "retired",
        "owner": "GW36",
        "reason": "the per-org token budget is a signed StateKind.BUDGET record spent by the per-org BudgetLease",
    },
    "blocked_keywords": {"kind": "built", "via": "rules.keywords"},
    "model_isolation_enabled": {
        "kind": "retired",
        "owner": "GW29",
        "reason": "isolation is plan state built by GW29",
        "off": (False,),
    },
    "allowed_models": {
        "kind": "retired",
        "owner": "GW28",
        "reason": "per-org model catalogue",
    },
    "default_model": {
        "kind": "retired",
        "owner": "GW28",
        "reason": "per-org default model selection",
    },
    "semantic_analysis_enabled": {
        "kind": "retired",
        "owner": "GW10",
        "reason": "semantic detection is governed by the GW10 detector and policy set",
        "off": (False,),
    },
    "tier2_execution_mode": {
        "kind": "retired",
        "owner": "GW27",
        "reason": "there is no v2 plan primitive for sync or async Tier-2; v2 detection runs in the resolver",
    },
    "tier2_stream_hold_enabled": {
        "kind": "retired",
        "owner": "GW13",
        "reason": "stream hold is the output streaming-mode surface",
        "off": (False,),
    },
    "tier2_stream_hold_timeout_ms": {
        "kind": "retired",
        "owner": "GW13",
        "reason": "stream hold is the output streaming-mode surface",
    },
    "tier2_enabled": {
        "kind": "retired",
        "owner": "GW10",
        "reason": "semantic detection is governed by the GW10 detector and policy set",
        "off": (None, False),
    },
    "mcp_tier2_enabled": {
        "kind": "retired",
        "owner": "GW17",
        "reason": "MCP surface posture",
        "off": (None, False),
    },
    "mcp_ext_scan_action": {
        "kind": "retired",
        "owner": "GW17",
        "reason": "external MCP proxy posture (GW32 consumes it)",
        "off": ("tag",),
    },
    "tier2_strict": {
        "kind": "retired",
        "owner": "GW27",
        "reason": "subsumed by per-rule on_unavailable, which drafts_for already emits from tier2_fail_closed_enabled",
    },
    "mcp_policy_only_enforcement": {
        "kind": "retired",
        "owner": "GW17",
        "reason": "MCP surface posture",
        "off": (False,),
    },
    # GW33: grounding is built (lexical; model mode is capability-gated, see ``selection_errors``).
    "factuality_check_enabled": {"kind": "mapped"},
    "hallucination_grounding_mode": {"kind": "mapped"},
    "hallucination_grounding_threshold": {"kind": "mapped"},
    "hallucination_grounding_model": {"kind": "mapped"},
    "output_policy_enabled": {"kind": "built", "via": "rules.output_policy"},
    "output_policy_action": {"kind": "built", "via": "rules.output_policy", "reject": ("rewrite",)},
    "output_hallucination_action": {"kind": "mapped"},
    "output_incident_logging_enabled": {"kind": "built", "via": "controls.audit"},
    "max_response_tokens": {"kind": "built", "via": "controls.max_response_tokens"},
    "rag_enabled": {
        "kind": "retired",
        "owner": "GW31",
        "reason": "RAG settings are built by GW31",
        "off": (False,),
    },
    "vector_db_isolation": {
        "kind": "retired",
        "owner": "GW31",
        "reason": "RAG settings are built by GW31",
        "off": (False,),
    },
    "rag_max_documents": {
        "kind": "retired",
        "owner": "GW31",
        "reason": "RAG settings are built by GW31",
    },
    "rag_relevance_threshold": {
        "kind": "retired",
        "owner": "GW31",
        "reason": "RAG settings are built by GW31",
    },
    "rag_redaction_enabled": {
        "kind": "retired",
        "owner": "GW31",
        "reason": "RAG settings are built by GW31",
        "off": (False,),
    },
    "rag_tier2_enabled": {
        "kind": "retired",
        "owner": "GW31",
        "reason": "RAG settings are built by GW31",
        "off": (False,),
    },
    "threat_intel_enabled": {"kind": "built", "via": "rules.threat"},
    "auto_block_threats": {"kind": "built", "via": "rules.threat"},
    "threat_score_threshold": {"kind": "built", "via": "rules.threat"},
    "audit_logging_enabled": {"kind": "built", "via": "controls.audit"},
    "retention_days": {"kind": "built", "via": "controls.audit"},
    "compliance_frameworks": {"kind": "built", "via": "controls.compliance"},
    "routing_risk_weight": {
        "kind": "retired",
        "owner": "GW28",
        "reason": "weighted deterministic routing",
    },
    "routing_cost_weight": {
        "kind": "retired",
        "owner": "GW28",
        "reason": "weighted deterministic routing",
    },
    "routing_latency_weight": {
        "kind": "retired",
        "owner": "GW28",
        "reason": "weighted deterministic routing",
    },
    "routing_priority_weight": {
        "kind": "retired",
        "owner": "GW28",
        "reason": "weighted deterministic routing",
    },
    "default_data_sensitivity": {
        "kind": "retired",
        "owner": "GW28",
        "reason": "weighted deterministic routing",
    },
    "routing_enabled": {
        "kind": "retired",
        "owner": "GW28",
        "reason": "weighted deterministic routing",
        "off": (False,),
    },
}

#: The five action fields whose value may be ``rewrite`` (GW33). Mapped for four; the fifth,
#: ``output_policy_action``, stays unsupported and keeps rejecting any changed value.
REWRITE_FIELDS = (
    "output_pii_action",
    "output_credential_action",
    "output_ip_leakage_action",
    "output_policy_action",
    "output_hallucination_action",
)
#: Platform allow-set of local backends that advertise hallucination capability (D-27). Empty by
#: default: grounding ``model`` mode is refused at save until a backend names itself here.
GROUNDING_MODEL_BACKENDS: frozenset[str] = frozenset()
_GROUNDING_MODES = ("lexical", "model")
_REWRITE_MESSAGE = "{field}: rewrite is only enforced on the v2 gateway for orgs it is enabled for."
_MODEL_MESSAGE = (
    "{field}: grounding model mode unavailable: no backend advertises hallucination."
)


def _setting(name: str) -> str:
    """A deployment setting: ``AMF_<NAME>`` (the repo convention) or the bare name."""
    return os.environ.get(f"AMF_{name}") or os.environ.get(name) or ""


def org_listed(setting: str, org_slug: str | None) -> bool:
    """Comma-separated org slugs, or ``*``. Empty, unparseable or no slug: not listed (D-21)."""
    if not org_slug:
        return False
    names = {item.strip() for item in _setting(setting).split(",") if item.strip()}
    return "*" in names or org_slug in names


def rewrite_served_by_v2(org_slug: str | None) -> bool:
    """D-21: ``rewrite`` is saveable only for orgs the deployment lists in ``V2_REWRITE_ORGS``.

    The default (nothing listed) is ``False``, so ``rewrite`` stays refused for every org until an
    operator names them. It depends on no other card's function.
    """
    return org_listed("V2_REWRITE_ORGS", org_slug)


#: Platform-declared REWRITE templates, one per category (D-4). They travel in the signed plan, so
#: they are covered by ``content_hash``. Each is short, free of URL/markup syntax, and renders
#: clean under every GW33 detector (asserted by tests against the real compiler).
REWRITE_TEMPLATES: Mapping[str, str] = MappingProxyType(
    {
        "pii": "[personal data removed]",
        "credential": "[credential removed]",
        "secret": "[secret removed]",
        "ip_leak": "[internal address removed]",
        "exfil": "[external link removed]",
        "hallucination": "[unsupported claim removed]",
        "canary": "[reference removed]",
        "policy": "[{category} removed]",
    }
)
_INPUTS = (
    ("pii_detection_enabled", "pii", "redact"),
    ("jailbreak_detection_enabled", "jailbreak", "block"),
)
_OUTPUTS = (
    ("output_pii_enabled", "output_pii_action", "pii", "redact"),
    ("output_credential_enabled", "output_credential_action", "credential", "block"),
)
#: Priority of the platform's built-in detector rules (GW33 D-32). Identity-joined, so the number
#: only orders them among themselves; it is appended AFTER the publisher's customer-row stamping.
BUILTIN_PRIORITY = 200
_ACTIONS = ("allow", "flag", "redact", "rewrite", "block")
_BYTE_ALTERING = ("redact", "rewrite", "block")


def selection_errors(
    incoming: Mapping[str, Any],
    stored: Mapping[str, Any] | None,
    org_slug: str | None = None,
) -> dict[str, str]:
    """Field-keyed errors for a new value the gateway does not enforce.

    ``org_slug`` gates ``rewrite`` per org (``rewrite_served_by_v2``); ``None`` is "not served".
    """
    previous = stored or {}
    errors: dict[str, str] = {}
    for field, spec in FIELDS.items():
        if field not in incoming or _same(incoming[field], previous.get(field)):
            continue
        message = _refusal(field, spec, incoming[field])
        if message is not None:
            errors[field] = message
    for field, message in _gw33_errors(incoming, previous, org_slug).items():
        errors.setdefault(field, message)  # a spec-level refusal (owner message) wins
    return errors
DELEGATED = ("org_token_budget",)
"""Former ``unsupported`` entries outside the 45 GW27 dispositions: owned by another card (GW36)."""
def changed_fields(incoming: Mapping[str, Any], stored: Mapping[str, Any] | None) -> set[str]:
    """Fields whose incoming value differs from the stored one (the console posts the whole row)."""
    previous = stored or {}
    return {f for f, v in incoming.items() if f not in previous or not _same(v, previous[f])}
def retire_ledger() -> list[dict[str, str]]:
    """The 30 retired entries, sorted by field: what the committed ledger must equal."""
    return [
        {"field": f, "owner": spec["owner"], "reason": spec["reason"]}
        for f, spec in sorted(FIELDS.items())
        if spec["kind"] == "retired" and f not in DELEGATED
    ]
def delegated_ledger() -> list[str]:
    return sorted(DELEGATED)
def built_ledger() -> list[dict[str, str]]:
    """The built entries, sorted by field, with where each is published."""
    return [
        {"field": f, "via": spec["via"]}
        for f, spec in sorted(FIELDS.items())
        if spec["kind"] == "built"
    ]
def retired_unchanged(stored: Mapping[str, Any]) -> list[str]:
    """Retired fields whose stored value is on, i.e. values the operator holds that v2 ignores."""
    return sorted(
        f
        for f, spec in FIELDS.items()
        if spec["kind"] == "retired" and f in stored and stored[f] not in spec.get("off", ())
    )


def _gw33_errors(
    incoming: Mapping[str, Any],
    previous: Mapping[str, Any],
    org_slug: str | None,
) -> dict[str, str]:
    errors: dict[str, str] = {}
    for field in REWRITE_FIELDS:
        value = incoming.get(field)
        if _is_new(incoming, previous, field) and str(value or "").strip().lower() == "rewrite":
            if not rewrite_served_by_v2(org_slug):
                errors[field] = _REWRITE_MESSAGE.format(field=field)
    threshold = incoming.get("hallucination_grounding_threshold")
    if _is_new(incoming, previous, "hallucination_grounding_threshold"):
        valid = (
            isinstance(threshold, (int, float))
            and not isinstance(threshold, bool)
            and math.isfinite(float(threshold))
            and 0.0 <= float(threshold) <= 1.0
        )
        if not valid:
            errors["hallucination_grounding_threshold"] = (
                "hallucination_grounding_threshold must be a number between 0 and 1."
            )
    mode = incoming.get("hallucination_grounding_mode")
    if _is_new(incoming, previous, "hallucination_grounding_mode"):
        if mode not in _GROUNDING_MODES:
            errors["hallucination_grounding_mode"] = (
                "hallucination_grounding_mode must be lexical or model."
            )
        elif mode == "model" and not _model_allowed(incoming, previous):
            errors["hallucination_grounding_mode"] = _MODEL_MESSAGE.format(
                field="hallucination_grounding_mode",
            )
    return errors


def _is_new(incoming: Mapping[str, Any], previous: Mapping[str, Any], field: str) -> bool:
    return field in incoming and not _same(incoming[field], previous.get(field))


def _model_allowed(incoming: Mapping[str, Any], previous: Mapping[str, Any]) -> bool:
    model = incoming.get("hallucination_grounding_model", previous.get("hallucination_grounding_model"))
    return isinstance(model, str) and model in GROUNDING_MODEL_BACKENDS


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
        if action not in _ACTIONS:
            continue
        row = _draft(category, "output", action, mode, posture, None)
        if action == "rewrite":
            row["rewrite_template"] = REWRITE_TEMPLATES[category]
        rows.append(row)
    rows.extend(_builtin_output_drafts(payload, mode))
    return rows


def _builtin_output_drafts(payload: Mapping[str, Any], mode: str) -> list[dict[str, Any]]:
    """GW33: IP leakage and grounding are built-in detector rules (D-2, D-28), not category rows."""
    rows: list[dict[str, Any]] = []
    if payload.get("output_ip_leakage_enabled", True):
        action = _action(payload.get("output_ip_leakage_action"), "flag")
        rows.append(_builtin_draft("out_ip_leak", "ip_leak", "output.ip_leak", action, mode, None))
    if payload.get("factuality_check_enabled", False):
        action = _action(payload.get("output_hallucination_action"), "flag")
        threshold = _float(payload.get("hallucination_grounding_threshold", 0.5))
        rows.append(
            _builtin_draft(
                "out_hallucination", "hallucination", _grounding_class(payload), action, mode,
                threshold,
            ),
        )
    return rows


def _action(value: Any, default: str) -> str:
    action = str(value or default).strip().lower()
    return action if action in _ACTIONS else default


def _grounding_class(payload: Mapping[str, Any]) -> str:
    """``output.grounding.lexical`` or ``output.grounding.model:<name>``: the mode rides here."""
    if payload.get("hallucination_grounding_mode") == "model":
        model = str(payload.get("hallucination_grounding_model") or "").strip().lower()
        return f"output.grounding.model:{model}" if model else "output.grounding.model:unset"
    return "output.grounding.lexical"


def _builtin_draft(
    rule_id: str,
    category: str,
    detector_class: str,
    action: str,
    mode: str,
    threshold: float | None,
) -> dict[str, Any]:
    """A platform detector rule. FAIL_CLOSED only where bytes are altered or blocked (D-18)."""
    row: dict[str, Any] = {
        "rule_id": rule_id,
        "category": category,
        "scope": "output",
        "action": action,
        "mode": mode,
        "on_unavailable": "fail_closed" if action in _BYTE_ALTERING else "fail_open",
        "threshold": threshold,
        "priority": BUILTIN_PRIORITY,
        "surfaces": ["chat"],
        "condition": {
            "kind": "detector", "field": "", "direction": "output", "keywords": [],
            "pattern": "", "detector_class": detector_class, "pipeline_stage": "",
        },
    }
    if action == "rewrite":
        row["rewrite_template"] = REWRITE_TEMPLATES[category]
    return row


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


def _refusal(field: str, spec: Mapping[str, Any], value: Any) -> str | None:
    """The refusal message for a NEW value of ``field``, or None when it is acceptable."""
    if "reject" in spec and value in spec["reject"]:
        return _REWRITE.format(field=field)
    if spec["kind"] != "retired":
        return None
    off = spec.get("off")
    if off is not None and value in off:
        return None
    return _RETIRED.format(field=field, owner=spec["owner"], reason=spec["reason"])
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
