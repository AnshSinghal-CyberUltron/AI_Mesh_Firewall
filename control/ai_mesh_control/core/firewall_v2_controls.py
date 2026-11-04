"""GW27: the BUILT FirewallConfig fields that are not plain rules (control-plane side).

``controls_for`` builds the signed plan body's ``controls`` object, ``threat_drafts`` builds the
three threat-intel rules, ``built_value_errors`` refuses values the gateway cannot represent, and
``controls_eligible`` / ``new_rules_mode`` read the publish gates. This module imports nothing from
``gateway_v2``: the JSON shape is the contract, pinned by a cross-tree contract test.

Control-plane policy constants (ranges, floors, limits) live here and never in ``gateway_v2``, so
no capacity literal enters the gateway.
"""
from __future__ import annotations

import os
from collections.abc import Collection, Mapping
from types import MappingProxyType
from typing import Any

CONTROLS_VERSION = 1
REQUIRES_CONTROLS = "controls/1"

RETENTION_FLOOR_DAYS: Mapping[str, int] = MappingProxyType({"PCI-DSS": 365, "HIPAA": 365})
"""Strictest declared framework wins. 365 is the serializer's existing ceiling (DECISION D-12: the
real HIPAA documentation period is longer, so this is the strongest floor expressible). Frameworks
with no entry impose none."""

KEYWORD_MAX_ENTRIES = 100
KEYWORD_MAX_CHARS = 128
FRAMEWORK_MAX_ENTRIES = 16
FRAMEWORK_MAX_CHARS = 32

CONTROLS_BACKED = frozenset(
    {
        "rate_limit_enabled",
        "requests_per_minute",
        "burst_limit",
        "org_tpm_limit",
        "max_response_tokens",
        "audit_logging_enabled",
        "output_incident_logging_enabled",
        "retention_days",
        "compliance_frameworks",
    },
)
"""BUILT fields carried by ``controls``; refused at save while the fleet gate is not open (D-10)."""

THREAT_CATEGORIES = ("prompt_injection", "jailbreak", "command")
NEW_RULE_MODES = ("off", "monitor", "enforce")

_GATE_REFUSAL = (
    "the gateway fleet is not yet at plan schema 1; change refused so the value is never "
    "stored and ignored"
)
_TRUE = {"1", "true", "yes", "on"}


def _setting(name: str, default: Any) -> Any:
    try:
        from django.conf import settings

        return getattr(settings, name, default)
    except Exception:  # noqa: BLE001 - Django unconfigured (pure-function use): use the environment.
        return os.environ.get(name, default)


def _flag(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in _TRUE
    return bool(value)


def controls_eligible(org_id: str) -> bool:
    """True when ``controls`` may be published for this org (D-10): flag on and org listed."""
    if not _flag(_setting("V2_PLAN_CONTROLS_ENABLED", False)):
        return False
    orgs = str(_setting("V2_PLAN_CONTROLS_ORGS", "") or "").strip()
    if orgs == "*":
        return True
    return org_id in {part.strip() for part in orgs.split(",") if part.strip()}


def new_rules_mode() -> str:
    """``off|monitor|enforce`` for the keyword and threat rows. Anything else is ``monitor``."""
    value = str(_setting("V2_PLAN_NEW_RULES_MODE", "monitor") or "").strip().lower()
    return value if value in NEW_RULE_MODES else "monitor"


def _whole(value: Any, default: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return value


def frameworks_of(payload: Mapping[str, Any]) -> list[str]:
    """Sorted unique framework labels (what is published and stamped)."""
    raw = payload.get("compliance_frameworks")
    if not isinstance(raw, list):
        return []
    return sorted({item for item in raw if isinstance(item, str) and item})


def controls_for(payload: Mapping[str, Any]) -> dict[str, Any]:
    """The ``controls`` body for a payload, or ``{}`` when nothing is set.

    A member is written only when the payload carries its fields, so a partial payload yields a
    partial object and the absent members mean "no org-specific control" on the gateway.
    """
    body: dict[str, Any] = {}
    if "rate_limit_enabled" in payload:
        body["rate"] = {
            "enabled": bool(payload["rate_limit_enabled"]),
            "requests_per_minute": _whole(payload.get("requests_per_minute")),
            "burst": _whole(payload.get("burst_limit")),
        }
    if _whole(payload.get("org_tpm_limit")) > 0:
        body["org_tpm_limit"] = _whole(payload.get("org_tpm_limit"))
    if _whole(payload.get("max_response_tokens")) > 0:
        body["max_response_tokens"] = _whole(payload.get("max_response_tokens"))
    if "audit_logging_enabled" in payload:
        body["audit"] = {
            "enabled": bool(payload["audit_logging_enabled"]),
            "incident_logging": bool(payload.get("output_incident_logging_enabled", True)),
            "retention_days": _whole(payload.get("retention_days"), 1),
        }
    frameworks = frameworks_of(payload)
    if frameworks:
        body["compliance"] = {"frameworks": frameworks}
    if not body:
        return {}
    return {"version": CONTROLS_VERSION, **body}


def threat_drafts(payload: Mapping[str, Any], *, mode: str = "enforce") -> list[dict[str, Any]]:
    """Three input rules (prompt_injection, jailbreak, command) from the threat-intel fields.

    v2 has no principal risk score (DECISION D-1): the value is an operator confidence floor over
    the semantic detector, ``threshold = value / 100``, ``block`` when auto-block is on else
    ``flag``. ``mode`` is the publish gate (``off`` omits, ``monitor`` publishes as monitor/flag).
    Turning ``threat_intel_enabled`` off removes only these ``fw-threat-*`` rules.
    """
    if mode == "off":
        return []
    if not (
        payload.get("firewall_enabled", True)
        and payload.get("content_filtering_enabled", True)
        and payload.get("threat_intel_enabled", False)
    ):
        return []
    value = _whole(payload.get("threat_score_threshold"), 75)
    threshold = min(max(value, 0), 100) / 100
    action = "block" if payload.get("auto_block_threats", True) else "flag"
    rule_mode = "monitor" if payload.get("enforcement_mode") == "monitor" else "enforce"
    if mode == "monitor":
        action, rule_mode = "flag", "monitor"
    posture = "fail_closed" if payload.get("tier2_fail_closed_enabled", True) else "fail_open"
    return [
        {
            "rule_id": f"fw-threat-{category}-input",
            "category": category,
            "scope": "input",
            "action": action,
            "mode": rule_mode,
            "on_unavailable": posture,
            "threshold": threshold,
            "surfaces": ["chat"],
        }
        for category in THREAT_CATEGORIES
    ]


def keywords_of(text: Any) -> list[str]:
    """Split comma text, strip, drop empties, de-duplicate case-insensitively (first spelling)."""
    if not isinstance(text, str):
        return []
    seen: set[str] = set()
    words: list[str] = []
    for part in text.split(","):
        word = part.strip()
        key = word.casefold()
        if word and key not in seen:
            seen.add(key)
            words.append(word)
    return words


def _active(field: str, changed: Collection[str] | None, *partners: str) -> bool:
    return changed is None or field in changed or any(p in changed for p in partners)


def built_value_errors(
    merged: Mapping[str, Any],
    changed: Collection[str] | None = None,
    *,
    controls_ok: bool = True,
) -> dict[str, str]:
    """Field-keyed refusals for BUILT values the gateway cannot represent.

    ``merged`` is stored plus incoming. A rule runs when its field, or a field it depends on,
    changed (``changed=None`` checks everything), so an unchanged stored value is always accepted.
    """
    errors: dict[str, str] = {}
    _rate_errors(merged, changed, errors)
    _range_errors(merged, changed, errors)
    _retention_errors(merged, changed, errors)
    _list_errors(merged, changed, errors)
    if not controls_ok:
        for field in sorted(CONTROLS_BACKED):
            if changed is None or field in changed:
                errors.setdefault(field, f"{field}: {_GATE_REFUSAL}.")
    return errors


def _rate_errors(
    merged: Mapping[str, Any], changed: Collection[str] | None, errors: dict[str, str]
) -> None:
    if not merged.get("rate_limit_enabled"):
        return
    pair = ("rate_limit_enabled", "requests_per_minute", "burst_limit")
    for field in ("requests_per_minute", "burst_limit"):
        if _active(field, changed, *pair) and _whole(merged.get(field)) < 1:
            errors[field] = f"{field} must be at least 1 when rate limiting is on."


def _range_errors(
    merged: Mapping[str, Any], changed: Collection[str] | None, errors: dict[str, str]
) -> None:
    if _active("org_tpm_limit", changed) and "org_tpm_limit" in merged:
        value = merged["org_tpm_limit"]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            errors["org_tpm_limit"] = "org_tpm_limit must be 0 (off) or a positive whole number."
    tokens_changed = _active("max_response_tokens", changed)
    if tokens_changed and "max_response_tokens" in merged and _whole(merged["max_response_tokens"]) < 1:
        errors["max_response_tokens"] = "max_response_tokens must be at least 1."
    if _active("threat_score_threshold", changed) and "threat_score_threshold" in merged:
        value = merged["threat_score_threshold"]
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100:
            errors["threat_score_threshold"] = "threat_score_threshold must be between 0 and 100."


def _retention_errors(
    merged: Mapping[str, Any], changed: Collection[str] | None, errors: dict[str, str]
) -> None:
    if not _active("retention_days", changed, "compliance_frameworks") or "retention_days" not in merged:
        return
    days = _whole(merged["retention_days"])
    if days < 1:
        errors["retention_days"] = "retention_days must be at least 1."
        return
    floors = [(RETENTION_FLOOR_DAYS[f], f) for f in frameworks_of(merged) if f in RETENTION_FLOOR_DAYS]
    if floors:
        floor, framework = max(floors)
        if days < floor:
            errors["retention_days"] = (
                f"retention_days must be at least {floor} while {framework} is declared."
            )


def _list_errors(
    merged: Mapping[str, Any], changed: Collection[str] | None, errors: dict[str, str]
) -> None:
    if _active("blocked_keywords", changed):
        words = keywords_of(merged.get("blocked_keywords"))
        if len(words) > KEYWORD_MAX_ENTRIES:
            errors["blocked_keywords"] = (
                f"blocked_keywords allows at most {KEYWORD_MAX_ENTRIES} entries (found {len(words)})."
            )
        elif any(len(word) > KEYWORD_MAX_CHARS for word in words):
            errors["blocked_keywords"] = (
                f"each blocked keyword must be at most {KEYWORD_MAX_CHARS} characters."
            )
    if _active("compliance_frameworks", changed):
        raw = merged.get("compliance_frameworks")
        if raw is not None and raw != "" and not _frameworks_valid(raw):
            errors["compliance_frameworks"] = (
                f"compliance_frameworks must be at most {FRAMEWORK_MAX_ENTRIES} labels of 1 to "
                f"{FRAMEWORK_MAX_CHARS} printable characters."
            )


def _frameworks_valid(raw: Any) -> bool:
    if not isinstance(raw, list) or len(raw) > FRAMEWORK_MAX_ENTRIES:
        return False
    return all(
        isinstance(item, str) and 1 <= len(item) <= FRAMEWORK_MAX_CHARS and item.isprintable()
        for item in raw
    )
