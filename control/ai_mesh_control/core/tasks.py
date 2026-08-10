import json
import logging
import math
import os
import socket
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

from auth.models import Organization
from core.models import AuditLog

logger = logging.getLogger(__name__)

REDIS_TELEMETRY_KEY = "telemetry:events"
REDIS_GATEWAY_JOBS_KEY = "gateway:jobs"

# EnforcementEvent.action is a CharField(max_length=16); a longer ``action``
# from a malformed/poison telemetry event raises StringDataRightTruncation on
# the atomic bulk_create, dropping the whole batch. Truncate defensively.
_MAX_ENFORCEMENT_ACTION_LEN = 16


def _strip_nul(value):
    """Recursively sanitize values that Postgres jsonb/text columns reject.

    Two poison classes are scrubbed so a single malformed/malicious telemetry
    event can't abort the atomic ``bulk_create`` and silently drop a whole batch
    (and, on older builds, re-queue forever):

    * NUL (``\\x00``) in any string — psycopg raises ``UntranslatableCharacter``
      → ``DataError``.
    * Non-finite floats (``inf`` / ``-inf`` / ``nan``) anywhere — jsonb has no
      representation for them, so the insert fails (e.g. a client-supplied
      ``risk_score``/``latency_ms`` of ``Infinity`` stored raw in metadata).
      These coerce to ``0.0`` so the event persists (sanitized) instead.
    """
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, float):
        return value if math.isfinite(value) else 0.0
    if isinstance(value, dict):
        return {_strip_nul(k): _strip_nul(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_strip_nul(v) for v in value]
    return value


def _coerce_risk(value) -> float:
    """Coerce an untrusted ``risk_score`` into a clean, bounded float.

    The gateway-supplied ``risk_score`` is client-influenceable telemetry. A
    non-numeric value (e.g. ``"high"``, ``{"x": 1}``, ``None``) raises on the
    ``int()``/``float()`` coercion in ``_build_enforcement_metadata``, crashing
    the metadata build and silently DROPPING the security event. A ``NaN`` /
    ``inf`` poisons downstream ``int(... * 100)`` math (``ValueError`` /
    nonsense risk). Total + defensive: anything that isn't a finite number maps
    to ``0.0`` so the event always degrades gracefully instead of being lost.
    """
    try:
        risk = float(value)
    except (TypeError, ValueError):
        return 0.0
    if math.isinf(risk) or math.isnan(risk):
        return 0.0
    if risk < 0.0:
        return 0.0
    if risk > 1.0:
        return 1.0
    return risk


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

    # ``metadata`` is client-influenceable. A non-dict value (list/str/number
    # from a malformed or malicious event) would raise on every ``.get()``
    # below, crashing the build and silently DROPPING the security event.
    # Guard to an empty dict so the event always degrades gracefully.
    event_metadata = event.get("metadata")
    if not isinstance(event_metadata, dict):
        event_metadata = {}
    # Same for ``tokens_used`` — read once, isinstance-guarded.
    tokens_used = event.get("tokens_used")
    if not isinstance(tokens_used, dict):
        tokens_used = {}
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
    owasp_codes = resolve_owasp_codes(threat_type, extra_payload, event_type=event_type)
    owasp_code = primary_owasp_code(owasp_codes) or _THREAT_TYPE_TO_OWASP.get(threat_type, "")
    # ``raw_risk`` is client-influenceable telemetry. A non-numeric or NaN/inf
    # value would crash the build here (the original int()/float() coercion
    # raises) and silently DROP the security event. Coerce defensively while
    # preserving legacy semantics: a float is a 0..1 fraction (scaled to a
    # 0..100 score); an int is already a 0..100 score. Anything non-finite or
    # non-numeric degrades to 0 — the event must persist, never be lost.
    # (``bool`` is an int subclass and keeps its legacy int() path.)
    if isinstance(raw_risk, float):
        security_risk_score = int(_coerce_risk(raw_risk) * 100)
    elif isinstance(raw_risk, int):
        security_risk_score = int(raw_risk)
    else:
        try:
            coerced = float(raw_risk)
        except (TypeError, ValueError):
            coerced = 0.0
        if math.isinf(coerced) or math.isnan(coerced):
            coerced = 0.0
        # A fractional string ("0.85") is a 0..1 fraction; an integral string
        # ("85") is already a 0..100 score.
        security_risk_score = int(coerced * 100) if 0.0 <= coerced <= 1.0 else int(coerced)

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
        "tokens_used": tokens_used,
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
        # PER-STAGE HONESTY (2026-07-16): surface the input vs output actions distinctly
        # + a clear one-line reason at the top level (the request `action` is now the
        # OUTPUT delivery action; `input_action` records the separate prompt redaction).
        "input_action": event_metadata.get("input_action"),
        "output_action": event_metadata.get("output_action"),
        "reason": event_metadata.get("reason") or event_metadata.get("detail") or "",
        "extra": event_metadata,
        # Enriched request-level fields for LogDetailPage
        "method": event.get("method", "POST"),
        "endpoint": "/v1/rag/query" if event_type == "rag_pipeline" else "/v1/chat/completions",
        "source_ip": event.get("source_ip", ""),
        "user_agent": event.get("user_agent", ""),
        "status_code": event.get("status_code", 200),
        "input_tokens": tokens_used.get("prompt_tokens", 0),
        "output_tokens": tokens_used.get("completion_tokens", 0),
        "total_tokens": tokens_used.get("total_tokens", 0),
        "organization_id": event.get("organization_id"),
        # Policy linkage: promote gateway match fields for analytics + FK resolution
        "policy_violations": event_metadata.get("matched_policies") or [],
        "matched_policies": event_metadata.get("matched_policies") or [],
        "matched_policy_codes": event_metadata.get("matched_policy_codes")
        or event_metadata.get("matched_policies")
        or [],
        "matched_rules": event_metadata.get("matched_rules") or [],
        "matched_rule_names": event_metadata.get("matched_rule_names")
        or event_metadata.get("matched_rules")
        or [],
        "matched_policy_ids": event_metadata.get("matched_policy_ids") or [],
        "matched_rule_ids": event_metadata.get("matched_rule_ids") or [],
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

    # Scan Detail Report / Activity Preview: hoist gateway enrichments to metadata top-level
    # so LogDetailPage can render pipeline stages, I/O, and incident correlation without
    # digging only into metadata.extra (which threat-feed rows also mirror here).
    if event_metadata.get("pipeline_trace"):
        result["pipeline_trace"] = event_metadata["pipeline_trace"]
    result["incident_id"] = (
        event_metadata.get("incident_id")
        or event_metadata.get("request_id")
        or request_id
    )
    result["prompt_submitted"] = (
        event_metadata.get("prompt_submitted")
        or event_metadata.get("prompt_snippet")
        or prompt_snippet
        or ""
    )
    if not result["prompt_submitted"] and result.get("prompt_lineage"):
        first = result["prompt_lineage"][0] if result["prompt_lineage"] else {}
        if isinstance(first, dict):
            result["prompt_submitted"] = first.get("prompt") or ""
    for _resp_key in ("response_snippet", "sanitized_output", "raw_output"):
        if event_metadata.get(_resp_key):
            result[_resp_key] = event_metadata[_resp_key]

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

    # Scrub NUL bytes from every nested string before this metadata reaches the
    # jsonb EnforcementEvent.metadata column (Postgres rejects \x00). This is the
    # single funnel for ALL drained telemetry, so one scrub covers every event.
    return _strip_nul(result)


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


def _output_incident_logging_enabled(org_id, _cache: dict) -> bool:
    """
    Best-effort per-org check of FirewallConfig.output_incident_logging_enabled.

    Honors per-org suppression of incident/review logging. Results are cached
    per drain batch to avoid an extra query per event. Fail-open (default True)
    if the config can't be read so auditing is never silently disabled by an error.
    """
    if org_id in _cache:
        return _cache[org_id]
    enabled = True
    try:
        from core.models import FirewallConfig

        val = (
            FirewallConfig.objects.filter(organization_id=org_id)
            .values_list("output_incident_logging_enabled", flat=True)
            .first()
        )
        if val is not None:
            enabled = bool(val)
    except Exception:
        logger.warning(
            "Failed to read output_incident_logging_enabled for org=%s; defaulting to enabled",
            org_id,
            exc_info=True,
        )
    _cache[org_id] = enabled
    return enabled


def _auto_create_review_items_and_incidents(events: list) -> None:
    """
    After bulk-creating EnforcementEvents, auto-create:
    - HumanReviewItem for 'flag' actions (populates ReviewQueuePanel)
    - SecurityIncident for 'block' actions (populates SecurityIncidentPanel)
    - HumanReviewItem for 'redact'/'rewrite' actions so post-generation
      sanitization is auditable in the SOC/HITL surface (gated per-org by
      FirewallConfig.output_incident_logging_enabled).
    """
    from policy.models import HumanReviewItem, SecurityIncident

    review_items = []
    incidents = []
    _logging_cache: dict = {}

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

        elif event.action in ("redact", "rewrite"):
            # Post-generation sanitization: surface it for human review so the
            # action is auditable. Honor per-org suppression of incident logging.
            if _output_incident_logging_enabled(org_id, _logging_cache):
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


def _safe_bulk_create_enforcement_events(events_to_create: list) -> list:
    """
    Insert EnforcementEvents with per-row isolation so a single poison-pill row
    cannot drop the entire batch.

    A single malformed telemetry event (e.g. an ``action`` longer than the
    column, or any value the DB rejects) makes one atomic ``bulk_create`` raise
    and roll back EVERY row in the batch — including healthy events for other
    tenants. Those events are then permanently lost because their dedupe keys
    were already written. We first try the fast bulk path; only if it fails do
    we fall back to per-row inserts, logging and skipping the offender(s) and
    persisting the rest. Returns the list of rows that were actually persisted.
    """
    from policy.models import EnforcementEvent

    if not events_to_create:
        return []
    try:
        EnforcementEvent.objects.bulk_create(events_to_create)
        return events_to_create
    except Exception:
        logger.warning(
            "drain_telemetry_from_redis: bulk insert failed for %d events; "
            "falling back to per-row inserts to isolate poison pills",
            len(events_to_create),
            exc_info=True,
        )

    persisted: list = []
    for ev in events_to_create:
        try:
            ev.save()
            persisted.append(ev)
        except Exception:
            logger.warning(
                "drain_telemetry_from_redis: skipped poison-pill telemetry event "
                "(org=%s action=%r) — persisting the rest of the batch",
                getattr(ev, "organization_id", None),
                getattr(ev, "action", None),
                exc_info=True,
            )
    return persisted


def _post_drain_ueba_hooks(events: list) -> None:
    """Module 2: after drain, sample prompts / bump lifetime / queue UEBA reassess."""
    if not events:
        return

    from collections import defaultdict

    from module2.analytics import key_prefix_from_meta
    from module2.tasks import reassess_ueba_keys_for_prefixes
    from module2.ueba_behavior_profile import append_prompt_samples_for_events
    from module2.ueba_metrics import increment_lifetime_request_counts

    append_prompt_samples_for_events(events)
    increment_lifetime_request_counts(events)

    prefixes_by_org: dict[int, set[str]] = defaultdict(set)
    for ev in events:
        org_id = getattr(ev, "organization_id", None)
        meta = getattr(ev, "metadata", None) or {}
        prefix = key_prefix_from_meta(meta)
        if org_id and prefix:
            prefixes_by_org[int(org_id)].add(prefix)

    for org_id, prefixes in prefixes_by_org.items():
        reassess_ueba_keys_for_prefixes.delay(org_id, list(prefixes))


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
    # R9 FIX: in-batch idempotency guard. Even with the per-consumer processing
    # key and the Redis dedupe set, a single drain pass must never write two
    # EnforcementEvents for the same (organization_id, request_id) — e.g. when
    # the same request_id appears twice in one dequeued batch. Track the pairs
    # already accepted in THIS pass and skip duplicates before constructing the
    # row.
    seen_in_batch: set[tuple[int, str, str, str]] = set()

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

    # R9 FIX: the control-web background-thread drain and the Celery worker drain
    # both run drain_telemetry_from_redis concurrently. A single shared
    # PROCESSING_KEY let each drainer's recovery path "recover" the OTHER
    # drainer's in-flight batch and re-process it (2x-4x EnforcementEvent
    # duplication). Scope the processing queue PER-CONSUMER (stable per
    # container via gethostname) so each drainer only ever recovers its OWN
    # crashed batch and never steals another consumer's in-flight events.
    try:
        _consumer_id = socket.gethostname() or "unknown"
    except Exception:
        _consumer_id = "unknown"
    PROCESSING_KEY = f"{REDIS_TELEMETRY_KEY}:processing:{_consumer_id}"

    # M5: single-runner lock per consumer (see the workers copy). Prevents two
    # concurrent drains on the same hostname from each "recovering" the other's
    # in-flight batch (the "recovered N pending" churn). Fail-open; TTL releases a
    # crashed holder; released in the finally below.
    DRAIN_LOCK_KEY = f"{REDIS_TELEMETRY_KEY}:drain_lock:{_consumer_id}"
    try:
        _got_drain_lock = bool(client.set(DRAIN_LOCK_KEY, "1", nx=True, ex=60))
    except redis.RedisError:
        _got_drain_lock = True  # fail-open
    if not _got_drain_lock:
        return 0

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

            # ``metadata`` is client-influenceable and may be a non-dict poison
            # value; guard before any ``.get()`` so the dedupe/scope reads here
            # cannot crash the drain loop.
            _event_meta = event.get("metadata")
            if not isinstance(_event_meta, dict):
                _event_meta = {}

            request_id = (
                event.get("request_id")
                or _event_meta.get("request_id")
                or event.get("pipeline_request_id")
            )

            # Set organization from telemetry event (injected by gateway).
            # Resolved BEFORE the dedupe check so the dedupe key can be scoped
            # by org + event identity (FIX-B, below).
            org_id = event.get("organization_id") or _event_meta.get("organization_id")
            try:
                org_id = int(org_id) if org_id is not None else None
            except (TypeError, ValueError):
                org_id = None

            # B1 (regression fix): these two were previously assigned ONLY inside
            # the `len(request_id) >= 8` Redis-dedupe block, but the in-batch
            # guard below runs under `if request_id:` and references them. A
            # truthy-but-short request_id (1-7 chars, e.g. a malformed
            # X-Request-ID) skipped the assignment and then hit an
            # UnboundLocalError at the in-batch guard — which propagated to the
            # except handler and SKIPPED the processing-key ack-delete, wedging
            # the same batch into a ~1s "recovered N pending" crash-loop
            # (remotely triggerable, permanent security-event loss). Assign
            # unconditionally — matching the workers copy (telemetry.py) so the
            # two drain implementations stay at parity.
            _dedupe_event_type = str(event.get("event_type") or "request")
            _dedupe_action = str(event.get("action") or "allow")

            # FIX-B: the dedupe key was derived purely from the
            # client-influenceable ``request_id`` (X-Request-ID → telemetry
            # request_id). An attacker could pin one request_id so a later BLOCK
            # event collides with an earlier ALLOW and is dropped — a silent
            # security UNDER-COUNT. Scope the key by org + event identity
            # (event_type, action) so distinct security events for the same
            # request_id never collide cross-type, while genuine retries of the
            # SAME event still dedupe.
            # A DEGENERATE request_id (e.g. a single char "h" from a malformed
            # X-Request-ID) would build a dedupe key that collapses ALL future
            # events sharing it for the 24h TTL — a sticky sink that silently
            # under-counts security events. Require a minimum length so a
            # malformed id falls through to no-request_id dedupe (in-batch guard
            # still applies) instead of poisoning the keyspace.
            if request_id and len(str(request_id).strip()) >= 8:
                dedupe_key = (
                    f"{DEDUPE_KEY_PREFIX}{org_id}:{_dedupe_event_type}:"
                    f"{_dedupe_action}:{request_id}"
                )
                is_new = client.set(dedupe_key, "1", nx=True, ex=86400)
                if not is_new:
                    logger.debug(
                        "drain_telemetry_from_redis: skipped duplicate telemetry "
                        "(org=%s event_type=%s action=%s request_id=%s)",
                        org_id,
                        _dedupe_event_type,
                        _dedupe_action,
                        request_id,
                    )
                    continue

            if not org_id or org_id <= 0:
                logger.warning(
                    "Skipping unscoped telemetry event (invalid organization_id): event_type=%s",
                    event.get("event_type", "unknown"),
                )
                continue

            # R9 FIX: idempotency guard — never write two EnforcementEvents for
            # the same event identity within one drain pass.
            # FIX-B: scope by (org, event_type, action, request_id) — same as
            # the Redis dedupe key — so distinct security events (e.g. an ALLOW
            # and a later BLOCK that share a pinned request_id) never collide
            # cross-type and get under-counted, while genuine in-batch retries
            # of the SAME event still dedupe.
            if request_id:
                batch_key = (org_id, _dedupe_event_type, _dedupe_action, str(request_id))
                if batch_key in seen_in_batch:
                    logger.debug(
                        "drain_telemetry_from_redis: skipped in-batch duplicate "
                        "(org=%s event_type=%s action=%s request_id=%s)",
                        org_id,
                        _dedupe_event_type,
                        _dedupe_action,
                        request_id,
                    )
                    continue
                seen_in_batch.add(batch_key)

            # Build + construct the row inside a per-event guard so a single
            # malformed event (poison pill) is skipped + logged rather than
            # raising and aborting the whole batch loop.
            try:
                built_metadata = _build_enforcement_metadata(event)
                action = event.get("action", "allow")
                # ``action`` is a CharField(max_length=16). An oversized value
                # (poison pill) raises StringDataRightTruncation on the atomic
                # bulk_create and drops the whole batch — coerce + truncate.
                if not isinstance(action, str):
                    action = str(action)
                # Strip NUL bytes (poison pill) before this CharField is inserted.
                action = action.replace("\x00", "")
                if len(action) > _MAX_ENFORCEMENT_ACTION_LEN:
                    action = action[:_MAX_ENFORCEMENT_ACTION_LEN]
                from policy.telemetry_resolution import resolve_policy_rule_from_event

                policy, rule = resolve_policy_rule_from_event(
                    action=action,
                    organization_id=org_id,
                    raw_metadata=event.get("metadata"),
                    built_metadata=built_metadata,
                )
                # Coerce numeric fields defensively. ``user_id`` / ``endpoint_id``
                # are IntegerFields; a malformed telemetry event (e.g.
                # user_id={'x': 1} from a client sending a non-scalar "user") is a
                # poison pill — it fails the ENTIRE atomic bulk_create below,
                # dropping every event in the batch and wedging the telemetry
                # drain for ALL orgs until the bad event is manually purged from
                # Redis. Null any non-integer value.
                _raw_uid = event.get("user_id")
                _raw_eid = event.get("endpoint_id")
                try:
                    _uid = int(_raw_uid) if _raw_uid is not None else None
                except (TypeError, ValueError):
                    _uid = None
                try:
                    _eid = int(_raw_eid) if _raw_eid is not None else None
                except (TypeError, ValueError):
                    _eid = None
                enforcement_event = EnforcementEvent(
                    policy=policy,
                    rule=rule,
                    action=action,
                    user_id=_uid,
                    endpoint_id=_eid,
                    agent=None,
                    metadata=built_metadata,
                    organization_id=org_id,
                )
            except Exception:
                logger.warning(
                    "drain_telemetry_from_redis: skipped unbuildable telemetry "
                    "event (org=%s event_type=%s)",
                    org_id,
                    event.get("event_type", "unknown"),
                    exc_info=True,
                )
                continue
            events_to_create.append(enforcement_event)
            processed += 1

        if events_to_create:
            # Per-row isolated insert: a single poison-pill row that slips past
            # the build-time coercion above cannot drop healthy rows for other
            # tenants. Downstream notifications / incident creation / critical
            # alerts act only on the rows that were actually persisted.
            events_to_create = _safe_bulk_create_enforcement_events(events_to_create)
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
            # M5: wrap this POST-insert side-effect so a failure here cannot jump
            # to the except handlers and SKIP the processing-key ack (delete)
            # below. The events are already persisted (and dedupe-marked), so a
            # skipped ack would make the next run "recover" an already-committed
            # batch — spurious churn (the ~727 "recovered N pending" warnings) and
            # a re-run of review/incident creation. Side-effect failures must not
            # block the ack.
            try:
                _auto_create_review_items_and_incidents(events_to_create)
            except Exception:
                logger.warning(
                    "drain_telemetry_from_redis: review/incident creation failed for drained batch",
                    exc_info=True,
                )

            # Module 2 UEBA hooks (best-effort; never block drain ack)
            try:
                _post_drain_ueba_hooks(events_to_create)
            except Exception:
                logger.warning(
                    "drain_telemetry_from_redis: UEBA post-drain hooks failed",
                    exc_info=True,
                )

        # Clear the processing queue after a clean iteration — even when
        # events_to_create is empty. A batch of all-skipped events (unscoped /
        # duplicate) is intentionally discarded, not retried; leaving the delete
        # inside `if events_to_create` meant an all-skipped batch never cleared
        # the processing queue, so the recovery path re-appended the same events
        # to the main list on every run and an unscoped telemetry flood could
        # never drain (the queue grew without bound, starving real events behind
        # it). A bulk_create exception above jumps to the handlers below and skips
        # this delete, so genuine commit failures are still recovered and retried.
        client.delete(PROCESSING_KEY)

    except redis.RedisError:
        logger.exception("drain_telemetry_from_redis: Redis error during drain")
    except Exception:
        logger.exception("drain_telemetry_from_redis: unexpected error")
    finally:
        # M5: release the single-runner lock so the next scheduled drain can run.
        try:
            client.delete(DRAIN_LOCK_KEY)
        except redis.RedisError:
            pass  # TTL will expire it

    # Security Alerting & Notifications feature REMOVED: critical events are still
    # persisted and surfaced in the dashboard, but no WebSocket/email alert is
    # dispatched (the alerting config + email path were removed system-wide).
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


def _build_vector_client_for_org(org_id, vector_db_type: str):
    """Resolve the org's active VectorProviderConfig and build a vendored
    gateway vector client (PineconeClient / MilvusClient). Returns (client, used)
    or (None, "").

    Vector deps (pinecone/litellm/pymilvus) and the vendored gateway modules
    (vector_client, byok_embedder) are present in the WORKER image only, so the
    imports are LAZY — ``core.tasks`` must still import cleanly in the control
    web container, which does not run this task and does not ship those modules.
    """
    from policy.vector_provider_models import VectorProviderConfig

    # Match ONLY the requested provider type — NOT "any active provider". An
    # untyped fallback could upsert into a DIFFERENT provider/namespace than the
    # request targeted (the gateway sync path resolves by exact type).
    cfg = VectorProviderConfig.objects.filter(
        organization_id=org_id, provider_type=vector_db_type, is_active=True
    ).first()
    if cfg is None:
        return None, ""

    ptype = (cfg.provider_type or "").strip()
    if ptype != "pinecone":
        # MilvusClient (milvus/custom) has no add()/upsert() yet — building one
        # would raise AttributeError mid-ingest and burn the task's retries.
        # Reject upfront with a clear log until Milvus async ingest is built.
        logger.error(
            "vector_ingest_task: provider '%s' does not support async ingest yet (org=%s); "
            "documents NOT stored", ptype, org_id,
        )
        return None, ""

    from vector_client import PineconeClient  # vendored from gateway

    # api_key is an EncryptedCharField — attribute access auto-decrypts (Fernet).
    api_key = cfg.api_key or ""
    if not api_key:
        return None, ""
    try:
        return PineconeClient(
            api_key=api_key,
            environment=cfg.environment or "",
            embedding_model=cfg.embedding_model or "text-embedding-3-small",
        ), "pinecone"
    except Exception:  # noqa: BLE001 - never crash the task on client construction
        logger.exception("vector_ingest_task: failed to build pinecone client for org=%s", org_id)
    return None, ""


@shared_task(bind=True, max_retries=3, default_retry_delay=20)
def vector_ingest_task(self, payload: dict) -> dict:
    """Async RAG ingest: embed + upsert the ALREADY-scanned-and-redacted docs
    into the org's BYOK vector DB.

    The gateway runs the ingest-time ContextGuard / PII / Tier-2 scan AND the
    typed-placeholder redaction INLINE before enqueuing, so by the time this
    worker runs the documents are guardrail-clean; this task only performs the
    slow per-org embed + upsert (reusing the vendored gateway vector_client +
    byok_embedder so the fail-closed embedding contract is identical)."""
    import asyncio

    job_id = payload.get("job_id", "")
    collection = payload.get("collection", "")
    org_id = payload.get("organization_id")
    project_id = payload.get("project_id", "") or ""
    vector_db_type = (payload.get("vector_db_type") or "pinecone").strip() or "pinecone"
    documents = payload.get("documents") or []
    ids = payload.get("ids") or []
    metadatas = payload.get("metadatas") or []

    if not documents:
        return {"status": "empty", "job_id": job_id, "collection": collection}

    client, used = _build_vector_client_for_org(org_id, vector_db_type)
    if client is None:
        logger.error(
            "vector_ingest_task: no resolvable vector client (org=%s vdb=%s job=%s) — "
            "documents NOT stored", org_id, vector_db_type, job_id,
        )
        return {"status": "error", "reason": "no_provider_configured", "job_id": job_id}

    try:
        count = asyncio.run(
            client.add(
                collection_name=collection,
                documents=documents,
                ids=ids,
                metadatas=metadatas,
                project_id=project_id,
            )
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "vector_ingest_task: upsert failed (org=%s collection=%s provider=%s job=%s)",
            org_id, collection, used, job_id,
        )
        # Retry transient provider/embedding errors; after max_retries the job is
        # dropped (the gateway already returned 202 with the scan verdict).
        raise self.retry(exc=exc)

    logger.info(
        "vector_ingest_task upserted job=%s collection=%s docs=%d provider=%s org=%s",
        job_id, collection, count, used, org_id,
    )
    return {
        "status": "ingested",
        "job_id": job_id,
        "collection": collection,
        "doc_count": count,
        "provider": used,
    }


@shared_task
def resync_gateway_keys() -> int:
    """Reconcile all active gateway API keys into Redis (startup + periodic).

    Recovers the gateway auth keyspace after a Redis flush/eviction or container
    recycle, which otherwise 401s every /v1/* request until keys are re-saved.
    """
    from core.signals import resync_all_gateway_keys

    return resync_all_gateway_keys()


@shared_task
def reconcile_routing_state() -> dict:
    """Periodic full reconcile of routing state (models/allowlist/isolation) into
    Redis. B2 DEFENSE: a bulk ``QuerySet.update()`` bypasses the per-instance
    ``post_save`` signal and leaves Redis stale, so the gateway routes on old
    config (deactivated/isolated/re-prioritised models keep serving). This task
    re-pushes ground-truth on a short interval so any signal-bypass self-heals.
    """
    from core.signals import reconcile_all_routing_state

    return reconcile_all_routing_state()


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
