import json
import logging
import os
import time
from base64 import b64encode
from datetime import timedelta
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo

import redis
from celery import shared_task
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import F, Value
from django.db.models.functions import Least
from django.utils import timezone

from ai_mesh_shared.owasp_telemetry import (
    primary_owasp_code,
    resolve_owasp_codes,
    THREAT_TYPE_TO_OWASP,
)

from auth.models import Organization
from core.models import AuditLog

logger = logging.getLogger(__name__)

REDIS_TELEMETRY_KEY = "telemetry:events"
REDIS_GATEWAY_JOBS_KEY = "gateway:jobs"

_EVENT_TYPE_TO_SOURCE: dict[str, str] = {
    "input_blocked": "security_scan",
    "output_guard": "security_scan",
    "output_scan": "security_scan",
    "agentic_violation": "agentic_scan",
    "critical_alert": "security_scan",
    "kill_switch": "policy",
    "request": "security_scan",
    "model_routed": "routing",
}

_SOURCE_TO_MODULE: dict[str, str] = {
    "security_scan": "1.2",
    "agentic_scan": "1.5",
    "routing": "1.5",
    "policy": "1.6",
}

_EVENT_TYPE_TO_MODULE: dict[str, str] = {
    "rag_pipeline": "1.3",
    "output_guard": "1.7",
    "output_scan": "1.7",
    "request": "1.1",
    "kill_switch": "1.6",
    "model_isolation": "1.6",
}

_THREAT_TYPE_TO_OWASP = THREAT_TYPE_TO_OWASP

RISK_INCREMENT_MAP: dict[str, float] = {
    "prompt_injection": 0.10,
    "jailbreak": 0.15,
    "data_leakage": 0.10,
    "goal_hijacking": 0.12,
    "tool_overreach": 0.10,
    "sql_injection": 0.15,
    "command_injection": 0.15,
    "rag_poisoning": 0.10,
    "toxicity": 0.05,
}
RISK_SCORE_CAP: float = 1.0
RISK_SCORE_DECAY_PER_DAY: float = 0.01
_EMAIL_LOGO_PATH = Path(__file__).resolve().parent / "email_assets" / "zeroshield-logo.png"

_TELEMETRY_CANONICAL_PRIORITY: dict[str, int] = {
    "request": 100,
    "stream_complete": 95,
    "rag_pipeline": 90,
    "input_blocked": 90,
    "output_scan": 85,
    "output_guard": 85,
    "kill_switch": 80,
    "model_routed": 20,
    "critical_alert": 10,
}


def _telemetry_request_id(event: dict) -> str:
    """Stable per-request id used for drain dedupe (matches gateway emit)."""
    event_metadata = event.get("metadata") or {}
    return (
        str(event.get("request_id") or "").strip()
        or str(event_metadata.get("request_id") or "").strip()
        or str(event.get("pipeline_request_id") or "").strip()
        or str(event.get("prompt_hash") or "").strip()
        or f"evt-{int(time.time() * 1000)}"
    )


def _merge_telemetry_group(group: list[dict]) -> dict:
    """Keep one canonical event per request_id, preserving prompt and key attribution."""
    best = max(
        group,
        key=lambda e: _TELEMETRY_CANONICAL_PRIORITY.get(str(e.get("event_type") or ""), 50),
    )
    merged = dict(best)
    if not str(merged.get("prompt_snippet") or "").strip():
        for event in group:
            snippet = str(event.get("prompt_snippet") or "").strip()
            if snippet:
                merged["prompt_snippet"] = snippet
                break
    if not str(merged.get("key_prefix") or "").strip():
        for event in group:
            prefix = str(event.get("key_prefix") or "").strip()
            if prefix:
                merged["key_prefix"] = prefix
                break
    return merged


def _collapse_telemetry_batch(events: list[dict]) -> list[dict]:
    """Keep one canonical EnforcementEvent candidate per shared request_id."""
    groups: dict[str, list[dict]] = {}
    orphans: list[dict] = []
    for event in events:
        rid = _telemetry_request_id(event)
        if rid.startswith("evt-"):
            orphans.append(event)
            continue
        groups.setdefault(rid, []).append(event)

    collapsed = [_merge_telemetry_group(group) for group in groups.values() if group]
    collapsed.extend(orphans)
    return collapsed


def _resolve_logo_src() -> str:
    explicit_url = os.environ.get("ZEROSHIELD_LOGO_URL", "").strip()
    if explicit_url:
        return explicit_url
    try:
        encoded = b64encode(_EMAIL_LOGO_PATH.read_bytes()).decode("ascii")
        return f"data:image/png;base64,{encoded}"
    except Exception:
        return "https://zeroshield.ai/assets/zeroshield-logo.png"


def _build_enforcement_metadata(event: dict) -> dict:
    """
    Transform a raw gateway telemetry event into a metadata dict
    with correctly derived source, owasp_code, threat_category,
    and security_risk_score fields that the security dashboard
    views expect.
    """
    event_type = event.get("event_type", "request")
    threat_type = event.get("threat_type", "")
    raw_risk = event.get("risk_score", 0)

    event_metadata = event.get("metadata") or {}
    source = event_metadata.get("source") or _EVENT_TYPE_TO_SOURCE.get(event_type, "security_scan")
    module_id = (
        event_metadata.get("module")
        or event_metadata.get("module_id")
        or _SOURCE_TO_MODULE.get(source)
        or _EVENT_TYPE_TO_MODULE.get(event_type)
        or "1.1"
    )
    request_id = _telemetry_request_id(event)
    extra_payload = dict(event_metadata)
    owasp_codes = resolve_owasp_codes(threat_type, extra_payload, event_type=event_type)
    owasp_code = primary_owasp_code(owasp_codes) or _THREAT_TYPE_TO_OWASP.get(threat_type, "")
    security_risk_score = int(raw_risk * 100) if isinstance(raw_risk, float) else int(raw_risk)

    is_isolation = event_type in ("kill_switch", "model_isolation", "circuit_breaker")
    if is_isolation:
        module_id = "1.6"

    result = {
        "event_type": event_type,
        "model": event.get("model", ""),
        "project_id": event.get("project_id", ""),
        "key_prefix": event.get("key_prefix", ""),
        "prompt_hash": event.get("prompt_hash", ""),
        "latency_ms": event.get("latency_ms", 0),
        "risk_score": raw_risk,
        "threat_type": threat_type,
        "tokens_used": event.get("tokens_used", {}),
        "compliance_tags": event.get("compliance_tags", []),
        "source": source,
        "owasp_code": owasp_code,
        "owasp_codes": owasp_codes,
        "threat_category": threat_type,
        "security_risk_score": security_risk_score,
        "pipeline_stage": event.get("pipeline_stage", ""),
        "pipeline_request_id": request_id,
        "request_id": request_id,
        "module": module_id,
        "module_id": module_id,
        "is_isolation_event": is_isolation,
        "event_timestamp": event.get("timestamp"),
        "intent": event.get("intent", ""),
        "extra": event.get("metadata", {}),
        # Enriched request-level fields for LogDetailPage
        "method": event.get("method", "POST"),
        "endpoint": "/v1/rag/query" if event_type == "rag_pipeline" else "/v1/chat/completions",
        "source_ip": event.get("source_ip", ""),
        "user_agent": event.get("user_agent", ""),
        "status_code": event.get("status_code", 200),
        "input_tokens": (event.get("tokens_used") or {}).get("prompt_tokens", 0),
        "output_tokens": (event.get("tokens_used") or {}).get("completion_tokens", 0),
        "total_tokens": (event.get("tokens_used") or {}).get("total_tokens", 0),
        "organization_id": event.get("organization_id"),
        # Policy violations: derived from matched policies in extra metadata
        "policy_violations": (event.get("metadata") or {}).get("matched_policies") or [],
        # Derived security analysis flags for LogDetailPage
        "pii_detected": threat_type in ("pii", "secret", "data_leakage", "credential"),
        "prompt_injection_detected": threat_type in ("prompt_injection", "injection"),
        "jailbreak_detected": threat_type == "jailbreak",
        "rate_limit_status": "exceeded" if "rate_limit" in threat_type else "ok",
        "auth_status": "blocked" if threat_type == "high_risk_actor" else "passed",
        "input_validation": "failed" if event_type == "input_blocked" else "passed",
        "content_safety": "flagged" if threat_type in ("toxicity", "hallucination") else "passed",
        "response_time_ms": event.get("latency_ms", 0),
    }

    # Build prompt_lineage from gateway prompt_snippet so forensics page shows the prompt
    prompt_snippet = str(event.get("prompt_snippet") or "").strip()
    if prompt_snippet:
        result["prompt_snippet"] = prompt_snippet[:500]
        result["prompt_lineage"] = [{"prompt": prompt_snippet, "risk_score": security_risk_score}]

    if event_type == "model_routed":
        extra = result.get("extra") or {}
        if isinstance(extra, dict):
            requested = extra.get("original_model") or extra.get("requested_model") or ""
            routed = extra.get("routed_model") or extra.get("selected_model") or result.get("model") or ""
            result["requested_model"] = requested
            result["original_model"] = extra.get("original_model") or requested
            result["routed_model"] = routed
            result["selected_model"] = extra.get("selected_model") or routed
            for key in (
                "routing_reason",
                "decision_source",
                "policy_summary",
                "routing_score",
                "rerouted",
                "decision_factors",
            ):
                if key in extra:
                    result[key] = extra[key]
        result["module"] = "1.5"
        result["module_id"] = "1.5"

    from module2.telemetry_health import normalize_enforcement_metadata

    normalized, _ = normalize_enforcement_metadata(result)
    return normalized


def _build_notification_payload(ev) -> dict:
    meta = ev.metadata or {}
    return {
        "type": "enforcement_event",
        "id": str(ev.id),
        "action": ev.action,
        "timestamp": ev.created_at.isoformat() if ev.created_at else None,
        "severity": meta.get("security_risk_score") or meta.get("severity") or "medium",
        "category": meta.get("threat_category") or "Policy",
        "subcategory": meta.get("owasp_code") or meta.get("threat_subcategory") or "",
        "source": meta.get("source", "policy"),
        "user_id": ev.user_id,
        "endpoint_id": ev.endpoint_id,
        "agent_id": str(ev.agent_id) if ev.agent_id else None,
        "organization_id": ev.organization_id,
        "metadata": meta,
    }


@shared_task
def log_audit(user_id, action, resource="", details="", ip=None, organization_id=None):
    """Create an AuditLog entry asynchronously. No-op if user_id is None.

    ``organization_id`` scopes the record to a tenant so retention cleanup and
    isolation can filter by org. When omitted it is derived from the user's
    profile organization where available.
    """
    if user_id is None:
        return
    user = None
    if user_id:
        User = get_user_model()
        try:
            user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            logger.warning("log_audit: user_id=%s not found", user_id)
            pass
    if organization_id is None and user is not None:
        # Best-effort: derive org from the user's profile (UserProfile.organization).
        organization_id = getattr(getattr(user, "profile", None), "organization_id", None)
    AuditLog.objects.create(
        user=user,
        organization_id=organization_id,
        action=action,
        resource=resource or "",
        details=details or "",
        ip_address=ip,
    )
    logger.debug(
        "Audit log created: action=%s resource=%s user_id=%s",
        action,
        resource or "-",
        user_id,
    )


def deliver_critical_alert_email(
    recipients_str: str,
    threat_type: str,
    risk_score: float,
    detail: str,
    event_type: str,
    organization_id: int | None = None,
    user_id: int | None = None,
    key_prefix: str = "",
    source: str = "",
    request_id: str = "",
    endpoint: str = "",
    model: str = "",
    pipeline_stage: str = "",
) -> bool:
    """
    Send critical security alert email to configured recipients.

    Prefers Microsoft Graph (application permissions) when TENANT_ID/CLIENT_ID/
    CLIENT_SECRET are set; otherwise uses Django SMTP (EMAIL_* settings).
    """
    if not recipients_str:
        logger.debug("No alert recipients configured, skipping email")
        return False

    recipients = [r.strip() for r in recipients_str.split(",") if r.strip() and "@" in r]
    if not recipients:
        logger.warning("No valid email addresses in alert_recipients: %s", recipients_str)
        return False

    risk_pct = int(risk_score * 100) if isinstance(risk_score, float) else risk_score
    org_name = "Unknown Organization"
    if organization_id:
        org_name = (
            Organization.objects.filter(id=organization_id)
            .values_list("name", flat=True)
            .first()
            or org_name
        )
    subject = f"AIMesh-Firewall Security Alert - {org_name}"
    ist_now = timezone.now().astimezone(ZoneInfo("Asia/Kolkata"))
    formatted_ts = ist_now.strftime("%d %b %Y, %I:%M:%S %p IST")
    threat_label = (threat_type or "unknown").replace("_", " ").title()
    event_type_label = (event_type or "unknown").replace("_", " ").title()
    source_label = (source or "gateway").replace("_", " ").title()
    pipeline_label = (pipeline_stage or "query").replace("_", " ").title()
    endpoint_label = endpoint or "/v1/chat/completions"
    request_id_label = request_id or "N/A"
    user_label = str(user_id) if user_id is not None else "N/A"
    key_prefix_label = key_prefix or "N/A"
    model_label = model or "N/A"
    detail_safe = escape(detail or "No additional threat description provided.")
    threat_link = (
        f"{os.environ.get('BACKEND_PUBLIC_URL', '').strip().rstrip('/')}/security-events"
        if os.environ.get("BACKEND_PUBLIC_URL", "").strip()
        else "https://app.zeroshield.ai/security-events"
    )
    logo_path = _resolve_logo_src()
    body = (
        f"ZeroShield Critical Security Alert\n"
        f"{'=' * 40}\n\n"
        f"Organization: {org_name}\n"
        f"Threat Type: {threat_type}\n"
        f"Risk Score: {risk_pct}%\n"
        f"Event Type: {event_type}\n"
        f"Detail: {detail}\n"
        f"Timestamp: {formatted_ts}\n\n"
        f"This alert was generated by the ZeroShield AI Mesh Firewall.\n"
        f"Threat Link: {threat_link}\n"
        f"Review the SOC dashboard for full details and forensic data."
    )
    body_html = f"""
<html>
  <body style="margin:0;background:#f2f6ff;font-family:Arial,sans-serif;color:#112147;">
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="padding:24px 0;">
      <tr>
        <td align="center">
          <table role="presentation" width="680" cellspacing="0" cellpadding="0" style="background:#ffffff;border:1px solid #dbe4ff;border-radius:12px;overflow:hidden;">
            <tr>
              <td style="background:#0d3f9e;padding:18px 24px;">
                <div style="display:inline-block;background:#ffffff;border-radius:6px;padding:8px;margin-bottom:10px;">
                  <img src="{escape(logo_path)}" alt="ZeroShield Logo" style="height:44px;display:block;" />
                </div>
                <div style="font-size:20px;font-weight:700;color:#ffffff;">AIMesh-Firewall Security Alert</div>
                <div style="font-size:13px;color:#dce8ff;margin-top:4px;">Organization - {escape(org_name)}</div>
              </td>
            </tr>
            <tr>
              <td style="padding:24px;">
                <p style="margin:0 0 14px 0;font-size:15px;line-height:1.5;">
                  A high risk security event was detected and requires immediate investigation.
                </p>
                <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse;border:1px solid #e6ecff;">
                  <tr><td style="padding:10px 12px;background:#f7f9ff;font-weight:700;width:180px;">Threat Type</td><td style="padding:10px 12px;">{escape(threat_label)}</td></tr>
                  <tr><td style="padding:10px 12px;background:#f7f9ff;font-weight:700;">Risk Score</td><td style="padding:10px 12px;">{risk_pct}%</td></tr>
                  <tr><td style="padding:10px 12px;background:#f7f9ff;font-weight:700;">Event Type</td><td style="padding:10px 12px;">{escape(event_type_label)}</td></tr>
                  <tr><td style="padding:10px 12px;background:#f7f9ff;font-weight:700;">Service</td><td style="padding:10px 12px;">{escape(source_label)}</td></tr>
                  <tr><td style="padding:10px 12px;background:#f7f9ff;font-weight:700;">Pipeline Stage</td><td style="padding:10px 12px;">{escape(pipeline_label)}</td></tr>
                  <tr><td style="padding:10px 12px;background:#f7f9ff;font-weight:700;">Endpoint</td><td style="padding:10px 12px;">{escape(endpoint_label)}</td></tr>
                  <tr><td style="padding:10px 12px;background:#f7f9ff;font-weight:700;">User ID</td><td style="padding:10px 12px;">{escape(user_label)}</td></tr>
                  <tr><td style="padding:10px 12px;background:#f7f9ff;font-weight:700;">Gateway API Key Prefix</td><td style="padding:10px 12px;">{escape(key_prefix_label)}</td></tr>
                  <tr><td style="padding:10px 12px;background:#f7f9ff;font-weight:700;">Model</td><td style="padding:10px 12px;">{escape(model_label)}</td></tr>
                  <tr><td style="padding:10px 12px;background:#f7f9ff;font-weight:700;">Request ID</td><td style="padding:10px 12px;">{escape(request_id_label)}</td></tr>
                  <tr><td style="padding:10px 12px;background:#f7f9ff;font-weight:700;">Timestamp</td><td style="padding:10px 12px;">{escape(formatted_ts)}</td></tr>
                </table>
                <h3 style="margin:18px 0 8px 0;font-size:16px;">Threat Description</h3>
                <p style="margin:0 0 16px 0;font-size:14px;line-height:1.6;color:#243a6b;">{detail_safe}</p>
                <a href="{escape(threat_link)}" style="display:inline-block;padding:10px 16px;background:#0d3f9e;color:#ffffff;text-decoration:none;border-radius:6px;font-weight:700;">
                  Open Security Investigation
                </a>
                <p style="margin:20px 0 0 0;font-size:12px;color:#5f6f95;">
                  This is an automated message from ZeroShield AIMesh-Firewall.
                </p>
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>
""".strip()

    from core.graph_mail import graph_mail_configured, send_graph_mail

    if graph_mail_configured():
        sent = send_graph_mail(
            recipients=recipients,
            subject=subject,
            body_text=body,
            body_html=body_html,
        )
        logger.info(
            "Critical alert email sent via Graph - org=%s user=%s key=%s source=%s event=%s request_id=%s recipients=%d",
            organization_id,
            user_label,
            key_prefix_label,
            source_label,
            event_type_label,
            request_id_label,
            len(recipients),
        )
        return sent

    from django.core.mail import send_mail

    send_mail(
        subject=subject,
        message=body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=recipients,
        fail_silently=False,
    )
    logger.info(
        "Critical alert email (SMTP) sent to %d recipients for %s",
        len(recipients),
        threat_type,
    )
    return True


def _dispatch_critical_alert_email(
    *,
    recipients_str: str,
    threat_type: str,
    risk_score: float,
    detail: str,
    event_type: str,
    organization_id: int | None = None,
    user_id: int | None = None,
    key_prefix: str = "",
    source: str = "",
    request_id: str = "",
    endpoint: str = "",
    model: str = "",
    pipeline_stage: str = "",
) -> None:
    """
    Deliver alert email without blocking telemetry drain.

    Default: synchronous delivery (standalone compose has no Celery worker).
    Set ALERT_EMAIL_USE_CELERY=true when workers consume platform.batch.
    """
    kwargs = {
        "recipients_str": recipients_str,
        "threat_type": threat_type,
        "risk_score": risk_score,
        "detail": detail,
        "event_type": event_type,
        "organization_id": organization_id,
        "user_id": user_id,
        "key_prefix": key_prefix,
        "source": source,
        "request_id": request_id,
        "endpoint": endpoint,
        "model": model,
        "pipeline_stage": pipeline_stage,
    }
    use_celery = str(getattr(settings, "ALERT_EMAIL_USE_CELERY", "false")).lower() in (
        "true",
        "1",
        "yes",
    )
    if use_celery:
        send_critical_alert_email.delay(**kwargs)
        return
    try:
        deliver_critical_alert_email(**kwargs)
    except Exception:
        logger.warning("Critical alert email delivery failed", exc_info=True)


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def send_critical_alert_email(
    self,
    recipients_str: str,
    threat_type: str,
    risk_score: float,
    detail: str,
    event_type: str,
    organization_id: int | None = None,
    user_id: int | None = None,
    key_prefix: str = "",
    source: str = "",
    request_id: str = "",
    endpoint: str = "",
    model: str = "",
    pipeline_stage: str = "",
) -> bool:
    """
    Celery wrapper for deliver_critical_alert_email (retries on failure).
    """
    try:
        return deliver_critical_alert_email(
            recipients_str=recipients_str,
            threat_type=threat_type,
            risk_score=risk_score,
            detail=detail,
            event_type=event_type,
            organization_id=organization_id,
            user_id=user_id,
            key_prefix=key_prefix,
            source=source,
            request_id=request_id,
            endpoint=endpoint,
            model=model,
            pipeline_stage=pipeline_stage,
        )
    except Exception as exc:
        logger.error("Failed to send critical alert email: %s", exc)
        raise self.retry(exc=exc) from exc


_GATEWAY_THREAT_TO_INCIDENT_TITLE: dict[str, str] = {
    "prompt_injection": "Prompt Injection Attack Blocked",
    "injection": "Prompt Injection Attack Blocked",
    "jailbreak": "Jailbreak Attempt Blocked",
    "pii": "PII Exposure Redacted",
    "secret": "Credential Exposure Redacted",
    "credential": "Credential Exposure in Output",
    "ip_leakage": "Infrastructure Information Leakage",
    "hallucination": "Hallucination Detected",
    "toxicity": "Toxic Content Blocked",
    "rag_poisoning": "RAG Poisoning Attempt Blocked",
    "data_leakage": "Data Leakage Attempt Blocked",
    "kill_switch": "Kill-Switch Activation",
    "model_not_allowed": "Unauthorized Model Access Blocked",
    "high_risk_actor": "High-Risk Actor Blocked",
    "blocked_keyword": "Blocked Keyword Detected",
    "policy_violation": "Policy Violation",
    "mcp_injection": "MCP Tool Injection Blocked",
    "tool_overreach": "MCP Tool Overreach Blocked",
    "goal_hijacking": "Agent Goal Hijacking Blocked",
    "rate_limit_burst": "Burst Rate Limit Exceeded",
    "rate_limit_rpm": "RPM Rate Limit Exceeded",
    "rate_limit_tpm": "TPM Rate Limit Exceeded",
}


def _auto_create_review_items_and_incidents(events: list) -> None:
    """
    After bulk-creating EnforcementEvents, auto-create:
    - HumanReviewItem for 'flag' actions (populates ReviewQueuePanel)
    - SecurityIncident for 'block' actions (populates SecurityIncidentPanel)
    """
    from policy.models import HumanReviewItem, SecurityIncident

    review_items = []
    incidents = []

    for event in events:
        org_id = event.organization_id
        if not org_id:
            continue

        meta = event.metadata or {}
        threat_type = meta.get("threat_type", "")
        risk_score = meta.get("security_risk_score", 0)

        if event.action == "flag":
            review_items.append(HumanReviewItem(
                enforcement_event=event,
                organization_id=org_id,
                status="pending",
            ))

        elif event.action == "block":
            if risk_score >= 80:
                severity = "critical"
            elif risk_score >= 60:
                severity = "high"
            elif risk_score >= 40:
                severity = "medium"
            else:
                severity = "low"

            title = _GATEWAY_THREAT_TO_INCIDENT_TITLE.get(
                threat_type, f"Security Event: {threat_type}"
            )
            incidents.append(SecurityIncident(
                organization_id=org_id,
                enforcement_event=event,
                title=title,
                severity=severity,
                status="open",
            ))

    if review_items:
        try:
            HumanReviewItem.objects.bulk_create(review_items)
            logger.info("Auto-created %d HumanReviewItems", len(review_items))
        except Exception:
            logger.warning("Failed to bulk-create HumanReviewItems", exc_info=True)

    if incidents:
        try:
            SecurityIncident.objects.bulk_create(incidents)
            logger.info("Auto-created %d SecurityIncidents", len(incidents))
        except Exception:
            logger.warning("Failed to bulk-create SecurityIncidents", exc_info=True)


def drain_telemetry_from_redis(batch_size: int = 50) -> int:
    """
    Drain telemetry events from Redis list and batch-insert
    EnforcementEvent records into Postgres.

    This is the core drain logic used by both the Celery Beat task
    and the background thread fallback in core/apps.py.

    Returns the number of events processed.
    """
    from policy.models import EnforcementEvent

    redis_url = getattr(settings, "REDIS_URL", "redis://localhost:6379/0")
    try:
        client = redis.Redis.from_url(redis_url, decode_responses=True)
    except redis.RedisError:
        logger.exception("drain_telemetry_from_redis: Redis connection failed")
        return 0

    events_to_create: list[EnforcementEvent] = []
    processed = 0
    key_org_by_prefix = None

    # DATA-01 FIX: Atomic dequeue using Lua script to prevent event loss
    ATOMIC_DEQUEUE_LUA = """
    local events = redis.call('LRANGE', KEYS[1], 0, tonumber(ARGV[1]) - 1)
    if #events > 0 then
        redis.call('LTRIM', KEYS[1], #events, -1)
        for i, event in ipairs(events) do
            redis.call('RPUSH', KEYS[2], event)
        end
    end
    return events
    """
    
    PROCESSING_KEY = f"{REDIS_TELEMETRY_KEY}:processing"
    
    DEDUPE_KEY_PREFIX = "telemetry:dedupe:"
    try:
        # Recovery path: if a previous run crashed after moving events to
        # the processing queue, requeue them before draining fresh events.
        processing_items = client.lrange(PROCESSING_KEY, 0, -1)
        if processing_items:
            client.rpush(REDIS_TELEMETRY_KEY, *processing_items)
            client.delete(PROCESSING_KEY)
            logger.warning(
                "drain_telemetry_from_redis: recovered %d pending telemetry events from processing queue",
                len(processing_items),
            )

        # Use Lua script for atomic move to processing queue
        raw_events = client.eval(ATOMIC_DEQUEUE_LUA, 2, REDIS_TELEMETRY_KEY, PROCESSING_KEY, batch_size)

        parsed_events: list[dict] = []
        for raw in raw_events:
            try:
                parsed_events.append(json.loads(raw))
            except (json.JSONDecodeError, TypeError):
                logger.warning("Malformed telemetry event: %s", raw[:200] if raw else "None")

        for event in _collapse_telemetry_batch(parsed_events):
            request_id = _telemetry_request_id(event)
            if request_id and not request_id.startswith("evt-"):
                dedupe_key = f"{DEDUPE_KEY_PREFIX}{request_id}"
                is_new = client.set(dedupe_key, "1", nx=True, ex=86400)
                if not is_new:
                    logger.debug(
                        "drain_telemetry_from_redis: skipped duplicate telemetry request_id=%s",
                        request_id,
                    )
                    continue

            metadata = _build_enforcement_metadata(event)
            if key_org_by_prefix is None:
                from module2.telemetry_health import build_key_org_map

                key_org_by_prefix = build_key_org_map()

            from module2.telemetry_health import resolve_organization_id

            org_id = resolve_organization_id(event, metadata, key_org_by_prefix)

            if not org_id or org_id <= 0:
                logger.warning(
                    "Skipping unscoped telemetry event (invalid organization_id): event_type=%s",
                    event.get("event_type", "unknown"),
                )
                continue

            metadata["organization_id"] = org_id
            enforcement_event = EnforcementEvent(
                policy=None,
                rule=None,
                action=event.get("action", "allow"),
                user_id=event.get("user_id"),
                endpoint_id=event.get("endpoint_id"),
                agent=None,
                metadata=metadata,
                organization_id=org_id,
            )
            events_to_create.append(enforcement_event)
            processed += 1

        if events_to_create:
            EnforcementEvent.objects.bulk_create(events_to_create)
            # Clear processing queue after successful commit
            client.delete(PROCESSING_KEY)
            logger.info(
                "drain_telemetry_from_redis: inserted %d events",
                len(events_to_create),
            )

            try:
                from module2.ueba_metrics import increment_lifetime_request_counts

                increment_lifetime_request_counts(events_to_create)
            except Exception:
                logger.warning("Failed to increment UEBA lifetime request counts", exc_info=True)

            try:
                from ws.notify import send_enforcement_notification

                for ev in events_to_create:
                    send_enforcement_notification(_build_notification_payload(ev))
            except Exception:
                logger.warning("Failed to dispatch enforcement notifications for drained telemetry", exc_info=True)

            # Auto-create HumanReviewItems for flagged events and
            # SecurityIncidents for blocked events so the frontend
            # ReviewQueuePanel and SecurityIncidentPanel have data.
            _auto_create_review_items_and_incidents(events_to_create)

        try:
            from module2.telemetry_health import maybe_repair_stale_telemetry

            maybe_repair_stale_telemetry()
        except Exception:
            logger.warning("Background Module 2 telemetry repair failed", exc_info=True)

    except redis.RedisError:
        logger.exception("drain_telemetry_from_redis: Redis error during drain")
    except Exception:
        logger.exception("drain_telemetry_from_redis: unexpected error")

    for event_data in events_to_create:
        meta = event_data.metadata or {}
        extra = meta.get("extra", {})
        if extra.get("alert_level") == "critical":
            try:
                from ws.notify import send_enforcement_notification

                send_enforcement_notification(
                    {
                        "type": "critical_alert",
                        "event_type": meta.get("event_type", ""),
                        "threat_type": meta.get("threat_type", ""),
                        "risk_score": meta.get("risk_score", 0),
                        "alert_level": "critical",
                        "detail": extra.get("detail", ""),
                        "recipients": extra.get("alert_recipients", ""),
                        "organization_id": event_data.organization_id,
                    }
                )
                logger.info(
                    "Critical alert dispatched via WebSocket: threat_type=%s, risk_score=%s",
                    meta.get("threat_type"),
                    meta.get("risk_score"),
                )
            except Exception:
                logger.warning("Failed to dispatch critical alert via WebSocket", exc_info=True)

            _dispatch_critical_alert_email(
                recipients_str=extra.get("alert_recipients", ""),
                threat_type=meta.get("threat_type", "unknown"),
                risk_score=meta.get("risk_score", 0),
                detail=extra.get("detail", ""),
                event_type=meta.get("event_type", ""),
                organization_id=event_data.organization_id,
                user_id=event_data.user_id,
                key_prefix=meta.get("key_prefix", ""),
                source=meta.get("source", ""),
                request_id=meta.get("request_id", ""),
                endpoint=meta.get("endpoint", ""),
                model=meta.get("model", ""),
                pipeline_stage=meta.get("pipeline_stage", ""),
            )

    return processed


@shared_task
def process_telemetry_batch(batch_size: int = 50) -> int:
    """
    Celery Beat wrapper for drain_telemetry_from_redis.
    Scheduled by Beat using TELEMETRY_DRAIN_INTERVAL_SEC.
    """
    return drain_telemetry_from_redis(batch_size)


def _handle_gateway_job(job: dict) -> bool:
    """Dispatch a gateway envelope payload to its domain task."""
    job_type = str(job.get("job_type", "")).strip().lower()
    payload = job.get("payload") or {}

    if job_type == "mcp_audit":
        from mcp_connector.tasks import record_mcp_event_task

        record_mcp_event_task.delay(payload)
        return True

    if job_type == "tier2_post_scan":
        from security_engines.tasks import tier2_post_scan_task

        tier2_post_scan_task.delay(payload)
        return True

    if job_type == "chat_postprocess":
        from security_engines.tasks import chat_postprocess_task

        chat_postprocess_task.delay(payload)
        return True

    if job_type == "vector_ingest":
        vector_ingest_task.delay(payload)
        return True

    logger.warning("Unknown gateway job_type=%s", job_type)
    return False


def drain_gateway_jobs_from_redis(batch_size: int = 200) -> int:
    """Drain gateway jobs from Redis and fan out to domain tasks."""
    redis_url = getattr(settings, "REDIS_URL", "redis://localhost:6379/0")
    try:
        client = redis.Redis.from_url(redis_url, decode_responses=True)
    except redis.RedisError:
        logger.exception("drain_gateway_jobs_from_redis: Redis connection failed")
        return 0

    processed = 0
    raw_jobs = []
    try:
        raw_jobs = client.lrange(REDIS_GATEWAY_JOBS_KEY, 0, batch_size - 1)
        if raw_jobs:
            client.ltrim(REDIS_GATEWAY_JOBS_KEY, len(raw_jobs), -1)
    except redis.RedisError:
        logger.exception("drain_gateway_jobs_from_redis: Redis read failed")
        return 0

    for raw_job in raw_jobs:
        try:
            job = json.loads(raw_job)
        except (json.JSONDecodeError, TypeError):
            logger.warning("Malformed gateway job payload")
            continue

        if _handle_gateway_job(job):
            processed += 1

    return processed


@shared_task
def process_gateway_jobs_batch(batch_size: int = 200) -> int:
    """Beat wrapper for gateway async envelopes."""
    return drain_gateway_jobs_from_redis(batch_size=batch_size)


@shared_task
def vector_ingest_task(payload: dict) -> dict:
    """
    Placeholder async ingest task.
    A follow-up worker can extend this to perform full embedding and upsert.
    """
    logger.info(
        "vector_ingest_task accepted job_id=%s collection=%s docs=%s",
        payload.get("job_id", ""),
        payload.get("collection", ""),
        len(payload.get("documents") or []),
    )
    return {
        "status": "accepted",
        "job_id": payload.get("job_id", ""),
        "collection": payload.get("collection", ""),
    }


@shared_task
def cleanup_old_audit_logs() -> dict:
    """
    Purge AuditLog and EnforcementEvent records older than the configured
    retention_days from each organisation's FirewallConfig. Runs daily via Celery Beat.
    Respects per-org retention settings; falls back to the global default config.
    """
    from auth.models import Organization
    from core.models import FirewallConfig
    from policy.models import EnforcementEvent

    total_deleted_events = 0
    total_deleted_audit = 0

    # Process each organisation with its own retention setting
    for org in Organization.objects.filter(is_active=True):
        config = FirewallConfig.load(organization=org)
        retention_days = config.retention_days
        cutoff = timezone.now() - timedelta(days=retention_days)
        deleted_events, _ = EnforcementEvent.objects.filter(
            organization_id=org.id,
            created_at__lt=cutoff,
        ).delete()
        # Tenant-scoped: only delete THIS org's audit logs. Previously this
        # filtered by created_at alone, so the org with the shortest retention
        # wiped every tenant's audit trail (cross-tenant data destruction).
        deleted_audit, _ = AuditLog.objects.filter(
            organization_id=org.id,
            created_at__lt=cutoff,
        ).delete()
        total_deleted_events += deleted_events
        total_deleted_audit += deleted_audit
        if deleted_events or deleted_audit:
            logger.info(
                "Retention cleanup for org=%s: deleted %d events, %d audit logs (retention=%dd, cutoff=%s)",
                org.slug, deleted_events, deleted_audit, retention_days, cutoff.isoformat(),
            )

    # Also clean orphan records with no org using the global default
    default_config = FirewallConfig.load(organization=None)
    default_cutoff = timezone.now() - timedelta(days=default_config.retention_days)
    orphan_events, _ = EnforcementEvent.objects.filter(
        organization_id__isnull=True,
        created_at__lt=default_cutoff,
    ).delete()
    orphan_audit, _ = AuditLog.objects.filter(
        organization_id__isnull=True,
        created_at__lt=default_cutoff,
    ).delete()
    total_deleted_events += orphan_events
    total_deleted_audit += orphan_audit

    logger.info(
        "Retention cleanup complete: total deleted %d enforcement events and %d audit logs",
        total_deleted_events,
        total_deleted_audit,
    )

    return {
        "deleted_enforcement_events": total_deleted_events,
        "deleted_audit_logs": total_deleted_audit,
    }


@shared_task
def update_risk_scores_from_telemetry() -> dict:
    """
    Scan recent EnforcementEvents for blocked threats and increment the
    risk_score on the associated GatewayAPIKey. Runs periodically via
    Celery Beat (every 5 minutes).

    Also applies natural decay to keys that have not triggered violations
    recently, preventing permanent blacklisting.
    """
    from core.models import GatewayAPIKey
    from policy.models import EnforcementEvent

    lookback = timezone.now() - timedelta(minutes=5)
    updated_keys: dict[str, float] = {}

    blocked_events = EnforcementEvent.objects.filter(
        action="block",
        created_at__gte=lookback,
    ).exclude(
        metadata__threat_type="",
    )

    key_increments: dict[str, float] = {}
    for event in blocked_events:
        meta = event.metadata or {}
        key_prefix = meta.get("key_prefix", "")
        threat_type = meta.get("threat_type", "")
        if not key_prefix or not threat_type:
            continue

        increment = RISK_INCREMENT_MAP.get(threat_type, 0.05)
        key_increments[key_prefix] = key_increments.get(key_prefix, 0.0) + increment

    for prefix, total_increment in key_increments.items():
        try:
            api_key = GatewayAPIKey.objects.filter(prefix=prefix, is_active=True).first()
            if api_key is None:
                logger.debug("No active GatewayAPIKey found for prefix=%s", prefix)
                continue

            old_score = api_key.risk_score

            GatewayAPIKey.objects.filter(pk=api_key.pk).update(
                risk_score=Least(F("risk_score") + total_increment, Value(RISK_SCORE_CAP))
            )
            api_key.refresh_from_db()
            api_key.save(update_fields=["risk_score"])

            updated_keys[prefix] = api_key.risk_score
            logger.info(
                "Risk score updated for key %s: %.2f -> %.2f (increment=%.2f)",
                prefix,
                old_score,
                api_key.risk_score,
                total_increment,
            )
        except Exception:
            logger.exception("Failed to update risk score for key prefix=%s", prefix)

    _apply_risk_decay()

    return {
        "keys_updated": len(updated_keys),
        "updates": updated_keys,
    }


def _apply_risk_decay() -> int:
    """
    Apply natural decay to risk scores for keys that have not triggered
    violations in the last 24 hours. Prevents permanent blacklisting.
    Returns the number of keys decayed.
    """
    from core.models import GatewayAPIKey
    from policy.models import EnforcementEvent

    decay_lookback = timezone.now() - timedelta(hours=24)

    active_keys = GatewayAPIKey.objects.filter(is_active=True, risk_score__gt=0.0)

    decayed_count = 0
    for api_key in active_keys:
        recent_violations = EnforcementEvent.objects.filter(
            action="block",
            created_at__gte=decay_lookback,
            metadata__key_prefix=api_key.prefix,
        ).exists()

        if not recent_violations:
            old_score = api_key.risk_score
            new_score = max(0.0, old_score - RISK_SCORE_DECAY_PER_DAY)
            if new_score != old_score:
                GatewayAPIKey.objects.filter(pk=api_key.pk).update(risk_score=new_score)
                api_key.refresh_from_db()
                api_key.save(update_fields=["risk_score"])
                decayed_count += 1
                logger.debug(
                    "Risk decay applied to key %s: %.2f -> %.2f",
                    api_key.prefix,
                    old_score,
                    new_score,
                )

    return decayed_count


@shared_task
def generate_compliance_report() -> dict:
    """
    Generate per-organisation compliance status reports based on active frameworks.
    Checks config alignment and recent enforcement event coverage.
    Runs daily via Celery Beat.
    """
    from auth.models import Organization
    from core.compliance import validate_compliance_requirements
    from core.models import FirewallConfig
    from policy.models import EnforcementEvent

    org_reports = []
    for org in Organization.objects.filter(is_active=True):
        config = FirewallConfig.load(organization=org)
        frameworks = config.compliance_frameworks or []

        if not frameworks:
            continue

        violations = validate_compliance_requirements(config)

        report_period_start = timezone.now() - timedelta(days=1)
        events_qs = EnforcementEvent.objects.filter(
            created_at__gte=report_period_start,
            organization_id=org.id,
        )
        total_events = events_qs.count()
        blocked_events = events_qs.filter(action="block").count()
        flagged_events = events_qs.filter(action="flag").count()

        report = {
            "generated_at": timezone.now().isoformat(),
            "organization_id": org.id,
            "organization_slug": org.slug,
            "active_frameworks": frameworks,
            "config_violations": violations,
            "compliant": len(violations) == 0,
            "period_start": report_period_start.isoformat(),
            "enforcement_summary": {
                "total_events": total_events,
                "blocked": blocked_events,
                "flagged": flagged_events,
                "allowed": total_events - blocked_events - flagged_events,
            },
            "retention_days": config.retention_days,
            "pii_detection_active": config.pii_detection_enabled,
            "audit_logging_active": config.audit_logging_enabled,
        }

        AuditLog.objects.create(
            user=None,
            organization=org,
            action="compliance_report",
            resource="firewall_config",
            details=json.dumps(report, default=str),
        )

        logger.info(
            "Compliance report generated for org=%s: frameworks=%s, violations=%d, compliant=%s",
            org.slug,
            frameworks,
            len(violations),
            report["compliant"],
        )
        org_reports.append(report)

    if not org_reports:
        logger.info("No active compliance frameworks across organisations, skipping report")
        return {"org_reports": [], "status": "no_frameworks"}

    return {"org_reports": org_reports, "status": "completed"}
