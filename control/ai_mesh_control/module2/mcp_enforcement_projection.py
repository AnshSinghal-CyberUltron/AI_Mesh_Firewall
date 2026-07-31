"""
Module-2-owned MCPEvent → EnforcementEvent projection.

On main, sync MCP `_record_event` already mirrors to EnforcementEvent.
Async gateway audit traffic (record_mcp_event_task) only writes MCPEvent.
This repair fills missing EnforcementEvent rows for Module 2 MCP Risk /
incidents without editing mcp_connector or sending WebSocket notifications.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

logger = logging.getLogger(__name__)

_ACTION_MAP = {
    "block": "block",
    "redact": "redact",
    "monitor": "monitor",
    "allow": "monitor",
    "scan_skipped": "monitor",
    "error": "monitor",
}
_RISK_MAP = {
    "block": 85,
    "redact": 55,
    "monitor": 25,
    "allow": 10,
    "scan_skipped": 5,
    "error": 40,
}
_STATUS_MAP = {
    "block": 403,
    "redact": 200,
    "monitor": 200,
    "allow": 200,
    "scan_skipped": 200,
    "error": 502,
}


def _classify(decision: str, reason: str, policy_ids) -> tuple[str, str]:
    r = (reason or "").lower()
    if decision == "block":
        if "schema" in r:
            return "MCP Schema Violation", "MCP08"
        if "tool_disabled" in r:
            return "MCP Tool Disabled", "MCP01"
        if "policy" in r or policy_ids:
            return "MCP Policy Violation", "MCP02"
        return "MCP Tool Block", "MCP01"
    if decision == "redact":
        return "MCP Data Redaction", "MCP06"
    if decision == "error":
        return "MCP Tool Error", "MCP01"
    return "MCP Tool Call", "MCP01"


def _build_metadata(ev) -> dict:
    decision = (ev.decision or "monitor").strip().lower()
    req_id = ev.request_id or ""
    policy_ids = list(ev.policy_ids or [])
    cat, owasp = _classify(decision, ev.policy_reason or "", policy_ids)
    existing_meta = dict(ev.metadata or {})
    risk = existing_meta.get("security_risk_score") or _RISK_MAP.get(decision, 10)
    status_code = existing_meta.get("status_code") or _STATUS_MAP.get(decision, 200)
    metadata = {
        **existing_meta,
        "source": "mcp_scan",
        "threat_category": cat,
        "owasp_code": owasp,
        "owasp_codes": [owasp] if owasp else [],
        "decision": decision,
        "reason": ev.policy_reason or "",
        "tool_name": ev.tool_name or "",
        "tools_invoked": [ev.tool_name] if ev.tool_name else [],
        "data_accessed": [ev.server_slug] if ev.server_slug else [],
        "server_slug": ev.server_slug or "",
        "server_name": ev.server_name or "",
        "request_id": req_id,
        "pipeline_request_id": req_id,
        "latency_ms": ev.latency_ms or 0,
        "policy_ids": policy_ids,
        "security_risk_score": risk,
        "actor_username": ev.username or "",
        "method": "POST",
        "endpoint": f"/api/mcp-connector/tools/call/ ({ev.tool_name})",
        "model": ev.tool_name or "",
        "status_code": status_code,
        "pipeline_stage": "mcp_tool_call",
        "intent": f"mcp:{ev.tool_name or 'unknown'}",
        "event_type": "mcp_tool_call",
        "module2_mcp_projection": True,
        "mcp_event_id": str(ev.id),
    }
    extra = dict(metadata.get("extra") or {})
    extra.setdefault("request_id", req_id)
    extra["module2_mcp_projection"] = True
    metadata["extra"] = extra
    return metadata


def _already_projected(ev, existing_req_ids: set[str]) -> bool:
    """Idempotent by request_id (when present) or mcp_event_id marker."""
    req_id = (ev.request_id or "").strip()
    if req_id and req_id in existing_req_ids:
        return True
    return False


def repair_mcp_enforcement_projection(
    *,
    lookback_hours: int = 24,
    batch_size: int = 250,
    org_id: int | None = None,
) -> dict:
    """
    Upsert missing EnforcementEvent rows for recent MCPEvent traffic.
    Never sends WebSocket notifications. Org-scoped when org_id is set.
    """
    from mcp_connector.models import MCPEvent
    from policy.models import EnforcementEvent

    since = timezone.now() - timedelta(hours=max(1, int(lookback_hours)))
    qs = (
        MCPEvent.objects.filter(timestamp__gte=since, organization_id__isnull=False)
        .order_by("timestamp")
    )
    if org_id is not None:
        qs = qs.filter(organization_id=org_id)

    # Recent mcp_scan EF request_ids (bounded lookback window).
    existing_req_ids = {
        rid
        for rid in EnforcementEvent.objects.filter(
            created_at__gte=since,
            metadata__source="mcp_scan",
        )
        .exclude(metadata__request_id="")
        .values_list("metadata__request_id", flat=True)
        if rid
    }
    # Also mark rows already tagged with mcp_event_id in this window.
    projected_mcp_ids = {
        str(mid)
        for mid in EnforcementEvent.objects.filter(
            created_at__gte=since,
            metadata__source="mcp_scan",
            metadata__module2_mcp_projection=True,
        ).values_list("metadata__mcp_event_id", flat=True)
        if mid
    }

    created = 0
    skipped = 0
    scanned = 0
    for ev in qs.iterator(chunk_size=min(batch_size, 100)):
        scanned += 1
        if scanned > batch_size:
            break
        if str(ev.id) in projected_mcp_ids:
            skipped += 1
            continue
        if _already_projected(ev, existing_req_ids):
            skipped += 1
            continue

        decision = (ev.decision or "monitor").strip().lower()
        action = _ACTION_MAP.get(decision, "monitor")
        metadata = _build_metadata(ev)

        # Secondary idempotency: same org + request_id + tool within window.
        req_id = (ev.request_id or "").strip()
        if req_id:
            exists = EnforcementEvent.objects.filter(
                organization_id=ev.organization_id,
                metadata__source="mcp_scan",
                metadata__request_id=req_id,
            ).filter(
                Q(metadata__tool_name=ev.tool_name or "") | Q(metadata__tool_name__isnull=True)
            ).exists()
            if exists:
                skipped += 1
                existing_req_ids.add(req_id)
                continue

        EnforcementEvent.objects.create(
            organization_id=ev.organization_id,
            policy=None,
            rule=None,
            action=action,
            user_id=ev.user_id,
            metadata=metadata,
            created_at=ev.timestamp,
            event_class="enforcement",
        )
        created += 1
        if req_id:
            existing_req_ids.add(req_id)
        projected_mcp_ids.add(str(ev.id))

    stats = {
        "scanned": scanned,
        "created": created,
        "skipped": skipped,
        "lookback_hours": lookback_hours,
        "batch_size": batch_size,
        "org_id": org_id,
    }
    logger.info("module2.repair_mcp_enforcement_projection %s", stats)
    return stats
