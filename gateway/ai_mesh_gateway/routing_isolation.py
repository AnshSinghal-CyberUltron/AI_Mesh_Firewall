"""
Model isolation primitives: compliant fallback resolution and audit envelopes.

Architecture (locked): per-request Redis kill-switch truth; disable-first reroute.
"""
from __future__ import annotations

from typing import Any

# Documented decision boundary for operators and tests.
KILL_SWITCH_TRUTH_MODE = "per_request_redis"
DISABLE_FIRST = True

_SENSITIVITY_ORDER = {
    "public": 0,
    "internal": 1,
    "confidential": 2,
    "restricted": 3,
}


def _req_sensitivity_level(value: object) -> int:
    """Resolve a CALLER-supplied data_sensitivity to a numeric level, FAIL-CLOSED.

    An unknown, non-empty value ('topsecret', a typo) is treated as the MOST
    restrictive level rather than defaulting to 0 (public). Otherwise a caller
    could downgrade restricted data onto a public model simply by sending an
    unrecognized-but-clearly-sensitive label — the gate already counts it as a
    compliance demand, so the filter must agree.
    """
    key = str(value or "").strip().lower()
    if key in _SENSITIVITY_ORDER:
        return _SENSITIVITY_ORDER[key]
    if key in ("", "public"):
        return 0
    return max(_SENSITIVITY_ORDER.values())


SCOPE_CREDENTIAL = "credential"
SCOPE_ORG_MODEL = "org_model"
SCOPE_ORG_GLOBAL = "org_global"
SCOPE_NONE = "none"


def fallback_profile_key(
    data_sensitivity: str = "public",
    compliance_tags: list[str] | None = None,
) -> str:
    tags = ",".join(sorted(t for t in (compliance_tags or []) if t))
    return f"{data_sensitivity}|{tags}"


def model_passes_hard_filters(
    model: dict[str, Any],
    *,
    data_sensitivity: str = "public",
    required_compliance: list[str] | None = None,
    allowed_models: set[str] | None = None,
) -> bool:
    """Same hard filters as LLMRouter._score_routing_models (without soft scoring)."""
    model_name = str(model.get("model_name") or "")
    model_id = str(model.get("model_id") or model_name)
    if not model.get("is_active", True):
        return False
    if allowed_models:
        lower = {m.lower() for m in allowed_models}
        if model_name.lower() not in lower and model_id.lower() not in lower:
            return False
    required_tags = [t for t in (required_compliance or []) if t]
    if required_tags:
        model_tags = model.get("compliance_tags") or []
        if not all(tag in model_tags for tag in required_tags):
            return False
    # Caller sensitivity fails CLOSED on unknown/mis-cased values; model level
    # defaults to public (low clearance) so an unknown model level can't qualify
    # for a high request.
    req_level = _req_sensitivity_level(data_sensitivity)
    model_level = _SENSITIVITY_ORDER.get(str(model.get("data_sensitivity_level", "public")).strip().lower(), 0)
    return model_level >= req_level


def resolve_compliant_fallback(
    *,
    primary_model: str,
    requested_fallback: str,
    routing_models: list[dict[str, Any]],
    fallback_chains: dict[str, Any] | None,
    data_sensitivity: str = "public",
    required_compliance: list[str] | None = None,
    allowed_models: set[str] | None = None,
) -> tuple[str | None, str]:
    """
    Pick a compliant fallback model.

    Returns (model_name_or_none, reason_code).
    reason_code: ``explicit_ok`` | ``chain_ok`` | ``chain_next`` | ``ineligible`` | ``empty_chain``
    """
    if not requested_fallback and not fallback_chains:
        return None, "empty_chain"

    models_by_name = {
        str(m.get("model_name") or ""): m
        for m in routing_models
        if m.get("model_name")
    }

    try:
        from platform_models import is_platform_model_name as _is_platform_model
    except ImportError:  # pragma: no cover - package-relative import
        from .platform_models import is_platform_model_name as _is_platform_model

    def _eligible(name: str) -> bool:
        if not name or name == primary_model:
            return False
        # A reserved platform/guard model (zeroshield-guard-120b, Bedrock
        # foundation IDs) must never be selected as a reroute target — it is
        # internal ML, not an org inference model.
        if _is_platform_model(name):
            return False
        entry = models_by_name.get(name)
        if not entry:
            return False
        return model_passes_hard_filters(
            entry,
            data_sensitivity=data_sensitivity,
            required_compliance=required_compliance,
            allowed_models=allowed_models,
        )

    if requested_fallback and _eligible(requested_fallback):
        return requested_fallback, "explicit_ok"

    if not fallback_chains:
        return None, "ineligible"

    profile = fallback_profile_key(data_sensitivity, required_compliance)
    per_primary = fallback_chains.get("per_primary") or {}
    chain = per_primary.get(primary_model)
    if not chain:
        chain = (fallback_chains.get("chains") or {}).get(profile) or []
        chain = [m for m in chain if m != primary_model]

    for candidate in chain:
        if _eligible(candidate):
            return candidate, "chain_ok" if not requested_fallback else "chain_next"

    # Defense-in-depth: profile key may omit single-tag subsets (e.g. restricted|HIPAA
    # vs precomputed restricted|HIPAA,SOC2). Scan routing catalog directly.
    ranked = sorted(
        routing_models,
        key=lambda m: (-(m.get("routing_priority") or 0), str(m.get("model_name") or "")),
    )
    for entry in ranked:
        name = str(entry.get("model_name") or "")
        if _eligible(name):
            return name, "catalog_scan"

    return None, "ineligible"


def build_isolation_audit_metadata(
    *,
    event_kind: str,
    scope: str,
    trigger_source: str,
    action: str,
    reason: str,
    original_model: str = "",
    selected_model: str = "",
    data_sensitivity: str = "public",
    compliance_tags: list[str] | None = None,
    fallback_chain_version: int | None = None,
    fallback_reason_code: str = "",
    correlation_id: str = "",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Structured audit envelope for kill-switch / isolation / reroute events."""
    meta: dict[str, Any] = {
        "isolation_event_kind": event_kind,
        "isolation_scope": scope,
        "trigger_source": trigger_source,
        "action": action,
        "reason": reason,
        "original_model": original_model,
        "selected_model": selected_model,
        "data_sensitivity": data_sensitivity,
        "compliance_tags": list(compliance_tags or []),
        "fallback_profile": fallback_profile_key(data_sensitivity, compliance_tags),
        "kill_switch_truth_mode": KILL_SWITCH_TRUTH_MODE,
    }
    if fallback_chain_version is not None:
        meta["fallback_chain_version"] = fallback_chain_version
    if fallback_reason_code:
        meta["fallback_reason_code"] = fallback_reason_code
    if correlation_id:
        meta["correlation_id"] = correlation_id
    if extra:
        meta.update(extra)
    return meta
