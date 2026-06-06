import json
import logging
import time
from datetime import timedelta

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

from core.models import AuditLog

logger = logging.getLogger(__name__)

# Backward-compatible alias for tests and internal references.
_THREAT_TYPE_TO_OWASP = THREAT_TYPE_TO_OWASP

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
}

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
    request_id = (
        event_metadata.get("request_id")
        or event.get("request_id")
        or event.get("prompt_hash")
        or f"evt-{int(time.time() * 1000)}"
    )
    extra_payload = dict(event_metadata)
    if event.get("metadata"):
        extra_payload.setdefault("raw_findings", (event.get("metadata") or {}).get("raw_findings"))
    owasp_codes = resolve_owasp_codes(
        threat_type,
        extra_payload,
        event_type=event_type,
    )
    owasp_code = primary_owasp_code(owasp_codes) or _THREAT_TYPE_TO_OWASP.get(threat_type, "")
    security_risk_score = int(raw_risk * 100) if isinstance(raw_risk, float) else int(raw_risk)

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
        # Policy linkage: promote gateway match fields for analytics + FK resolution
        "policy_violations": (event.get("metadata") or {}).get("matched_policies") or [],
        "matched_policies": (event.get("metadata") or {}).get("matched_policies") or [],
        "matched_policy_codes": (event.get("metadata") or {}).get("matched_policy_codes")
        or (event.get("metadata") or {}).get("matched_policies")
        or [],
        "matched_rules": (event.get("metadata") or {}).get("matched_rules") or [],
        "matched_rule_names": (event.get("metadata") or {}).get("matched_rule_names")
        or (event.get("metadata") or {}).get("matched_rules")
        or [],
        "matched_policy_ids": (event.get("metadata") or {}).get("matched_policy_ids") or [],
        "matched_rule_ids": (event.get("metadata") or {}).get("matched_rule_ids") or [],
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
    prompt_snippet = event.get("prompt_snippet", "")
    if prompt_snippet:
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

    return result


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
def log_audit(user_id, action, resource="", details="", ip=None):
    """Create an AuditLog entry asynchronously. No-op if user_id is None."""
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
    AuditLog.objects.create(
        user=user,
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


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def send_critical_alert_email(
    self,
    recipients_str: str,
    threat_type: str,
    risk_score: float,
    detail: str,
    event_type: str,
) -> bool:
    """
    Send critical security alert email to configured recipients.
    Runs as a separate task so email failures do not block telemetry processing.
    Retries up to 3 times with 30-second delay on failure.
    """
    if not recipients_str:
        logger.debug("No alert recipients configured, skipping email")
        return False

    recipients = [r.strip() for r in recipients_str.split(",") if r.strip() and "@" in r]
    if not recipients:
        logger.warning("No valid email addresses in alert_recipients: %s", recipients_str)
        return False

    from django.core.mail import send_mail

    risk_pct = int(risk_score * 100) if isinstance(risk_score, float) else risk_score
    subject = f"[ZeroShield CRITICAL] {threat_type} detected (score: {risk_pct}%)"
    body = (
        f"ZeroShield Critical Security Alert\n"
        f"{'=' * 40}\n\n"
        f"Threat Type: {threat_type}\n"
        f"Risk Score: {risk_pct}%\n"
        f"Event Type: {event_type}\n"
        f"Detail: {detail}\n"
        f"Timestamp: {timezone.now().isoformat()}\n\n"
        f"This alert was generated by the ZeroShield AI Mesh Firewall.\n"
        f"Review the SOC dashboard for full details and forensic data."
    )

    try:
        send_mail(
            subject=subject,
            message=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=recipients,
            fail_silently=False,
        )
        logger.info(
            "Critical alert email sent to %d recipients for %s",
            len(recipients),
            threat_type,
        )
        return True
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
        
        for raw in raw_events:
            try:
                event = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                logger.warning("Malformed telemetry event: %s", raw[:200] if raw else "None")
                continue

            request_id = (
                event.get("request_id")
                or (event.get("metadata") or {}).get("request_id")
                or event.get("pipeline_request_id")
            )
            if request_id:
                dedupe_key = f"{DEDUPE_KEY_PREFIX}{request_id}"
                is_new = client.set(dedupe_key, "1", nx=True, ex=86400)
                if not is_new:
                    logger.debug(
                        "drain_telemetry_from_redis: skipped duplicate telemetry request_id=%s",
                        request_id,
                    )
                    continue

            # Set organization from telemetry event (injected by gateway)
            org_id = event.get("organization_id") or (event.get("metadata") or {}).get("organization_id")
            try:
                org_id = int(org_id) if org_id is not None else None
            except (TypeError, ValueError):
                org_id = None

            if not org_id or org_id <= 0:
                logger.warning(
                    "Skipping unscoped telemetry event (invalid organization_id): event_type=%s",
                    event.get("event_type", "unknown"),
                )
                continue

            built_metadata = _build_enforcement_metadata(event)
            action = event.get("action", "allow")
            from policy.telemetry_resolution import resolve_policy_rule_from_event

            policy, rule = resolve_policy_rule_from_event(
                action=action,
                organization_id=org_id,
                raw_metadata=event.get("metadata"),
                built_metadata=built_metadata,
            )
            enforcement_event = EnforcementEvent(
                policy=policy,
                rule=rule,
                action=action,
                user_id=event.get("user_id"),
                endpoint_id=event.get("endpoint_id"),
                agent=None,
                metadata=built_metadata,
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
                from ws.notify import send_enforcement_notification

                for ev in events_to_create:
                    send_enforcement_notification(_build_notification_payload(ev))
            except Exception:
                logger.warning("Failed to dispatch enforcement notifications for drained telemetry", exc_info=True)

            # Auto-create HumanReviewItems for flagged events and
            # SecurityIncidents for blocked events so the frontend
            # ReviewQueuePanel and SecurityIncidentPanel have data.
            _auto_create_review_items_and_incidents(events_to_create)

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

            send_critical_alert_email.delay(
                recipients_str=extra.get("alert_recipients", ""),
                threat_type=meta.get("threat_type", "unknown"),
                risk_score=meta.get("risk_score", 0),
                detail=extra.get("detail", ""),
                event_type=meta.get("event_type", ""),
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
        deleted_audit, _ = AuditLog.objects.filter(
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
