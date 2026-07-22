import logging

from celery import shared_task

from auth.models import Organization
from mcp_connector.models import MCPEvent

logger = logging.getLogger(__name__)


def _normalize_compliance_tags(tags) -> list:
    """CHG-0059: normalize gateway-granular compliance tags (GDPR/PII/HIPAA/PHI/
    PCI-DSS/SECRET/INFRA/SOC2) onto the ComplianceTag catalog vocabulary
    (GDPR-PII/HIPAA-PHI/PCI-CARD/SOC2-CONF/...), so ``MCPEvent.compliance_tags``
    holds catalog codes regardless of which plane recorded the event (its
    documented contract). Idempotent + never drops a tag; never raises (audit
    recording must not break on a tagging fault)."""
    try:
        from ai_mesh_shared.mcp_compliance_tags import to_catalog_codes

        return to_catalog_codes(tags or [])
    except Exception as exc:  # pragma: no cover - defensive, never break ingestion
        logger.warning("record_mcp_event_task compliance_tag_normalize_failed err=%s", exc)
        return list(tags or [])


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
        compliance_tags=_normalize_compliance_tags(payload.get("compliance_tags")),
        # Accept the new ``scan_findings`` key, falling back to the legacy
        # ``presidio_findings`` for in-flight envelopes during the rename window.
        scan_findings=payload.get("scan_findings") or payload.get("presidio_findings") or [],
    )
    logger.info("record_mcp_event_task created MCPEvent id=%s", event.id)
    return str(event.id)
