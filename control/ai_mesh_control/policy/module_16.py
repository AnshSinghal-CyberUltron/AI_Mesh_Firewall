"""
Module 1.6 (Inline Model Isolation & Kill-Switch) event classification.

Single source of truth for KPI/trend/chart bucketing and threat-feed filtering.
"""

from __future__ import annotations

from django.db.models import Q

MODULE_16_ID = "1.6"

_ISOLATION_EVENT_TYPES = frozenset(
    {"kill_switch", "model_isolation", "circuit_breaker"},
)
_ISOLATION_THREAT_TYPES = frozenset(
    {"kill_switch", "model_isolation", "model_state_unavailable", "model_isolated"},
)

# Never merge these from KillSwitchAuditLog.metadata into threat-feed payloads.
_AUDIT_METADATA_BLOCKLIST = frozenset(
    {
        "reason",
        "api_key_prefix",
        "prompt",
        "prompt_snippet",
        "raw_output",
        "response",
        "credentials",
        "secret",
        "password",
        "token",
        "is_audit_log",
        "module_id",
        "module",
        "is_isolation_event",
    },
)

_AUDIT_METADATA_ALLOWLIST = frozenset(
    {
        "fallback_model",
        "original_model",
        "isolation_scope",
        "kill_switch_action",
        "fallback_reason_code",
        "status",
        "scope",
    },
)


def is_module_16_enforcement(meta: dict | None) -> bool:
    """Return True when enforcement metadata belongs to Module 1.6."""
    if not meta:
        return False
    if meta.get("module_id") == MODULE_16_ID or meta.get("module") == MODULE_16_ID:
        return True
    if meta.get("is_isolation_event") or meta.get("is_audit_log"):
        return True
    event_type = (meta.get("event_type") or "").lower()
    threat_type = (meta.get("threat_type") or "").lower()
    if event_type in _ISOLATION_EVENT_TYPES:
        return True
    if threat_type in _ISOLATION_THREAT_TYPES:
        return True
    if (meta.get("trigger_source") or "").lower() in ("kill_switch", "model_state"):
        return True
    return False


def module_16_enforcement_q() -> Q:
    """Django Q filter approximating :func:`is_module_16_enforcement` for querysets."""
    return (
        Q(metadata__module_id=MODULE_16_ID)
        | Q(metadata__module=MODULE_16_ID)
        | Q(metadata__event_type__in=list(_ISOLATION_EVENT_TYPES))
        | Q(metadata__threat_type__in=list(_ISOLATION_THREAT_TYPES))
        | Q(metadata__is_isolation_event=True)
        | Q(metadata__is_audit_log=True)
        | Q(metadata__trigger_source__in=["kill_switch", "model_state"])
    )


def _safe_reason_snippet(reason: str, max_len: int = 200) -> str:
    text = (reason or "").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def _sanitize_audit_metadata(raw: dict | None) -> dict:
    if not raw or not isinstance(raw, dict):
        return {}
    safe = {}
    for key, value in raw.items():
        if key in _AUDIT_METADATA_BLOCKLIST:
            continue
        if key in _AUDIT_METADATA_ALLOWLIST:
            safe[key] = value
    return safe


def audit_log_to_threat_feed_item(log, *, organization_name: str | None = None) -> dict:
    """Map KillSwitchAuditLog row to threat-feed item shape."""
    risk_pct = int(float(log.risk_score or 0) * 100) if log.risk_score else 0
    action = (log.action or "").strip().lower() or "monitor"
    if action not in ("block", "redact", "monitor", "allow", "reroute"):
        action = "block" if "block" in (log.event or "").lower() or "isolat" in (log.event or "").lower() else "monitor"

    # Canonicalize the raw model id so a platform/guard/bedrock identifier
    # (e.g. global.anthropic claude-haiku, gpt-oss/120b) is never echoed
    # verbatim to a tenant — only the user-facing "ZeroShield Model" must
    # surface. Lazy-import to avoid the security_views <-> module_16 import
    # cycle; fall back to the raw value only if the helper cannot be loaded.
    try:
        from policy.security_views import _canonicalize_model_name

        safe_model = _canonicalize_model_name(log.model_name)
    except Exception:
        safe_model = log.model_name

    meta = {
        "event_type": log.event,
        "model": safe_model,
        "threat_type": "kill_switch",
        "threat_category": log.event,
        "source": "policy",
        "module_id": MODULE_16_ID,
        "module": MODULE_16_ID,
        "is_isolation_event": True,
        "is_audit_log": True,
        "security_risk_score": risk_pct,
        "triggered_by": log.triggered_by,
        "reason_snippet": _safe_reason_snippet(log.reason),
        "request_id": log.request_id or "",
        "organization_id": log.organization_id,
    }
    meta.update(_sanitize_audit_metadata(log.metadata))

    return {
        "id": f"audit-{log.pk}",
        "record_type": "control_plane_audit",
        "timestamp": log.timestamp.isoformat() if log.timestamp else None,
        "severity": risk_pct or "medium",
        "category": log.event or "Kill-Switch Audit",
        "subcategory": "LLM09",
        "user_id": None,
        "user_display": log.triggered_by or "control_plane",
        "endpoint_id": None,
        "endpoint_name": None,
        "endpoint_identifier": None,
        "organization_name": organization_name,
        "agent_id": None,
        "action": action,
        "source": "policy",
        "source_display": "control_plane",
        "metadata": meta,
        "incident_title": log.event or "Isolation control-plane action",
        "enforcement_action_text": action,
        "prompt_lineage": [],
        "tools_invoked": [],
        "data_accessed": [],
        "assignee": None,
        "incident_status": None,
        "escalated_at": None,
        "escalated_by_id": None,
        "resolved_at": None,
        "resolved_by_id": None,
    }


def merge_module_16_feed_items(enforcement_items: list, audit_items: list) -> list:
    """
    Merge enforcement and audit threat-feed rows; dedupe by request_id when both exist.
    Enforcement rows win over audit rows for the same request_id.
    """
    by_request: dict[str, dict] = {}
    no_request: list = []

    for item in enforcement_items:
        rid = (item.get("metadata") or {}).get("request_id") or ""
        if rid:
            by_request[rid] = item
        else:
            no_request.append(item)

    for item in audit_items:
        rid = (item.get("metadata") or {}).get("request_id") or ""
        if rid and rid in by_request:
            continue
        if rid:
            by_request[rid] = item
        else:
            no_request.append(item)

    merged = list(by_request.values()) + no_request
    merged.sort(key=lambda x: x.get("timestamp") or "", reverse=True)
    return merged
