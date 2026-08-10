import logging

from celery import shared_task

from auth.models import Organization
from mcp_connector.models import MCPEvent
from policy.models import EnforcementEvent  # Module 2 bridge
from ws.notify import send_enforcement_notification  # Module 2 live toast

logger = logging.getLogger(__name__)


def _mcp_decision_to_action(decision: str) -> str:
    """Module 2: MCP decision → EnforcementEvent.action."""
    action = (decision or "").strip().lower()
    if action == "block":
        return "block"
    if action == "redact":
        return "redact"
    return "monitor"


def _mirror_metadata(payload: dict) -> dict:
    """Module 2: tag mirrored row source=mcp_scan."""
    incoming_meta = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    return {
        **incoming_meta,
        "source": incoming_meta.get("source") or "mcp_scan",
        "event_type": incoming_meta.get("event_type") or "mcp_tool_call",
        "request_id": payload.get("request_id") or "",
        "pipeline_request_id": payload.get("request_id") or "",
        "tool_name": payload.get("tool_name") or "",
        "server_slug": payload.get("server_slug") or "",
        "server_name": payload.get("server_name") or "",
        "decision": payload.get("decision") or "allow",
        "reason": payload.get("policy_reason") or "",
        "policy_ids": payload.get("policy_ids") or [],
        "latency_ms": payload.get("latency_ms") or 0,
    }


def _build_notification_payload(ev: EnforcementEvent) -> dict:
    """Module 2: WS notify payload."""
    meta = ev.metadata or {}
    return {
        "type": "enforcement_event",
        "id": str(ev.id),
        "action": ev.action,
        "timestamp": ev.created_at.isoformat() if ev.created_at else None,
        "severity": meta.get("security_risk_score") or meta.get("severity") or "medium",
        "category": meta.get("threat_category") or "Policy",
        "subcategory": meta.get("owasp_code") or meta.get("threat_subcategory") or "",
        "source": meta.get("source", "mcp_scan"),
        "user_id": ev.user_id,
        "endpoint_id": ev.endpoint_id,
        "agent_id": str(ev.agent_id) if ev.agent_id else None,
        "organization_id": ev.organization_id,
        "metadata": meta,
    }


@shared_task
def record_mcp_event_task(payload: dict) -> str:
    """Persist MCPEvent; Module 2 best-effort EF mirror + notify."""
    if not payload:
        return ""

    organization_id = payload.get("organization_id")
    if not organization_id and payload.get("org_slug"):
        organization = Organization.objects.filter(slug=payload.get("org_slug"), is_active=True).first()
        organization_id = organization.id if organization else None

    event = MCPEvent.objects.create(
        organization_id=organization_id,
        user_id=payload.get("user_id"),
        username=payload.get("username", ""),
        server_slug=payload.get("server_slug", ""),
        server_name=payload.get("server_name", ""),
        tool_name=payload.get("tool_name", ""),
        decision=payload.get("decision", "allow"),
        policy_ids=payload.get("policy_ids") or [],
        policy_reason=payload.get("policy_reason", ""),
        latency_ms=int(payload.get("latency_ms") or 0),
        request_id=payload.get("request_id", ""),
        metadata=payload.get("metadata") or {},
    )
    # Module 2: mirror for UEBA / threat-feed (do not fail MCPEvent)
    if organization_id:
        try:
            mirrored = EnforcementEvent.objects.create(
                organization_id=organization_id,
                action=_mcp_decision_to_action(payload.get("decision", "allow")),
                user_id=payload.get("user_id"),
                metadata=_mirror_metadata(payload),
                event_class="enforcement",
            )
            send_enforcement_notification(
                _build_notification_payload(mirrored),
                organization_id=organization_id,
            )
        except Exception as exc:
            logger.warning("record_mcp_event_task mirror/notify failed err=%s", exc)
    logger.info("record_mcp_event_task created MCPEvent id=%s", event.id)
    return str(event.id)
