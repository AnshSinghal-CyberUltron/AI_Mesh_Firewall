"""
Precompute OPA/policy-aligned fallback chains per org routing catalog.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

# Must match gateway llm_router._SENSITIVITY_ORDER
_SENSITIVITY_ORDER = {
    "public": 0,
    "internal": 1,
    "confidential": 2,
    "restricted": 3,
}

FALLBACK_CHAIN_SCHEMA_VERSION = 1


def fallback_profile_key(
    data_sensitivity: str = "public",
    compliance_tags: list[str] | None = None,
) -> str:
    """Stable profile id for chain lookup (data-class + compliance set)."""
    tags = ",".join(sorted(t for t in (compliance_tags or []) if t))
    return f"{data_sensitivity}|{tags}"


def _model_passes_hard_filters(
    model: dict[str, Any],
    *,
    data_sensitivity: str,
    required_tags: list[str],
) -> bool:
    if not model.get("is_active", True):
        return False
    if required_tags:
        model_tags = model.get("compliance_tags") or []
        if not all(tag in model_tags for tag in required_tags):
            return False
    req_level = _SENSITIVITY_ORDER.get(data_sensitivity, 0)
    model_level = _SENSITIVITY_ORDER.get(model.get("data_sensitivity_level", "public"), 0)
    return model_level >= req_level


def _ordered_chain_for_profile(
    routing_entries: list[dict[str, Any]],
    *,
    data_sensitivity: str,
    compliance_tags: list[str],
) -> list[str]:
    """Rank eligible models by routing_priority (desc), then model_name."""
    required = [t for t in compliance_tags if t]
    eligible = [
        m
        for m in routing_entries
        if _model_passes_hard_filters(
            m,
            data_sensitivity=data_sensitivity,
            required_tags=required,
        )
    ]
    eligible.sort(
        key=lambda m: (
            -(m.get("routing_priority") or 0),
            str(m.get("model_name") or ""),
        ),
    )
    return [str(m["model_name"]) for m in eligible if m.get("model_name")]


def build_compliant_fallback_chains(
    routing_entries: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Build versioned fallback chains for all sensitivity levels and observed tag sets.

    Returns payload stored under ``fallback_chains`` in ``llm:model_configs:{org}``.
    """
    profiles: set[tuple[str, tuple[str, ...]]] = {("public", ())}
    for entry in routing_entries:
        sens = entry.get("data_sensitivity_level") or "public"
        tag_list = sorted(t for t in (entry.get("compliance_tags") or []) if t)
        tags = tuple(tag_list)
        profiles.add((sens, tags))
        # Single-tag profiles so requests with one compliance tag resolve chains.
        for single in tag_list:
            profiles.add((sens, (single,)))
        for level in _SENSITIVITY_ORDER:
            if _SENSITIVITY_ORDER.get(sens, 0) <= _SENSITIVITY_ORDER.get(level, 0):
                profiles.add((level, tags))
                for single in tag_list:
                    profiles.add((level, (single,)))

    chains: dict[str, list[str]] = {}
    for sens, tag_tuple in sorted(profiles):
        key = fallback_profile_key(sens, list(tag_tuple))
        chains[key] = _ordered_chain_for_profile(
            routing_entries,
            data_sensitivity=sens,
            compliance_tags=list(tag_tuple),
        )

    per_primary: dict[str, list[str]] = {}
    for entry in routing_entries:
        primary = str(entry.get("model_name") or "")
        if not primary:
            continue
        sens = entry.get("data_sensitivity_level") or "public"
        tags = entry.get("compliance_tags") or []
        profile_chain = chains.get(fallback_profile_key(sens, tags), [])
        per_primary[primary] = [m for m in profile_chain if m != primary]

    raw = json.dumps(chains, sort_keys=True)
    content_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    return {
        "version": FALLBACK_CHAIN_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "content_hash": content_hash,
        "chains": chains,
        "per_primary": per_primary,
    }
