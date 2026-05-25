import logging

from celery import shared_task

from auth.models import Organization
from mcp_connector.models import MCPEvent

logger = logging.getLogger(__name__)


@shared_task
def record_mcp_event_task(payload: dict) -> str:
    """Persist an MCP audit event asynchronously from a gateway envelope."""
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
    logger.info("record_mcp_event_task created MCPEvent id=%s", event.id)
    return str(event.id)
