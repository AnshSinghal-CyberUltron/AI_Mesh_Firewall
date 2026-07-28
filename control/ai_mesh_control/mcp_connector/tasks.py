import logging

from celery import shared_task

from auth.models import Organization
from mcp_connector.models import MCPEvent
from policy.models import EnforcementEvent
from ws.notify import send_enforcement_notification

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


def _mcp_decision_to_action(decision: str) -> str:
    action = (decision or "").strip().lower()
    if action == "block":
        return "block"
    if action == "redact":
        return "redact"
    return "monitor"


def _mirror_metadata(payload: dict, event: MCPEvent) -> dict:
    incoming_meta = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    decision = str(payload.get("decision") or "allow").strip().lower()
    policy_reason = str(payload.get("policy_reason") or "")
    risk_by_decision = {
        "block": 85,
        "redact": 55,
        "monitor": 25,
        "allow": 10,
        "scan_skipped": 5,
        "error": 40,
    }
    status_code_by_decision = {
        "block": 403,
        "redact": 200,
        "monitor": 200,
        "allow": 200,
        "scan_skipped": 200,
        "error": 502,
    }
    return {
        **incoming_meta,
        "source": incoming_meta.get("source") or "mcp_scan",
        "event_type": incoming_meta.get("event_type") or "mcp_tool_call",
        "request_id": payload.get("request_id") or "",
        "pipeline_request_id": payload.get("request_id") or "",
        "tool_name": payload.get("tool_name") or "",
        "server_slug": payload.get("server_slug") or "",
        "server_name": payload.get("server_name") or "",
        "decision": decision,
        "reason": policy_reason,
        "policy_ids": payload.get("policy_ids") or [],
        "security_risk_score": incoming_meta.get("security_risk_score") or risk_by_decision.get(decision, 10),
        "status_code": incoming_meta.get("status_code") or status_code_by_decision.get(decision, 200),
        "latency_ms": payload.get("latency_ms") or 0,
    }


def _build_notification_payload(ev: EnforcementEvent) -> dict:
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
    if organization_id:
        try:
            mirrored = EnforcementEvent.objects.create(
                organization_id=organization_id,
                action=_mcp_decision_to_action(payload.get("decision", "allow")),
                user_id=payload.get("user_id"),
                metadata=_mirror_metadata(payload, event),
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
