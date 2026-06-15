"""
Ingestion API: accept pipeline/agent events and enqueue them for persistence.

Events are mapped into the gateway telemetry-event schema and pushed onto the
``telemetry:events`` Redis list — the SAME queue the gateway data plane writes to
and the worker / control drain (``drain_telemetry_from_redis``) consumes. Earlier
this endpoint wrote a different shape to a separate ``event_queue`` Redis *stream*
that nothing in this deployment consumed, so ingested events were silently dropped
and never reached Postgres. Aligning the producer with the live consumer makes
ingested events persist as EnforcementEvents and surface in the dashboards.
"""

import json
import logging
import uuid
from datetime import datetime

from django.conf import settings
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from core.agent_auth import AgentAPIKeyPermission, AgentKeyAuthentication

# Drain queue shared with the gateway data plane (drain_telemetry_from_redis).
REDIS_TELEMETRY_KEY = "telemetry:events"
# Cap events accepted per batch request so a single call cannot enqueue an
# unbounded amplifier flood.
_MAX_BATCH_EVENTS = 1_000

# DRF's JSONParser uses json.load with no size guard and
# DATA_UPLOAD_MAX_MEMORY_SIZE only applies to multipart — so without these caps
# a single request can push a multi-MB / deeply-nested blob straight onto the
# shared telemetry drain queue. Bound the serialized body, per-field string
# length, and JSON nesting depth; reject anything larger with a 400.
_MAX_EVENT_BYTES = 256 * 1024  # serialized size of one event payload
_MAX_STR_FIELD_LEN = 4_096  # any individual string value
_MAX_AGENT_ID_LEN = 256
_MAX_EVENT_TYPE_LEN = 128
_MAX_JSON_DEPTH = 32
# EnforcementEvent.action is a CharField(max_length=16) downstream.
_MAX_ACTION_LEN = 16
# C-4: the known EnforcementEvent.action vocabulary. The ingestion boundary
# previously only TRUNCATED a caller-supplied action (no validation), so a
# redactor-mangled payload like 'X'*16 persisted verbatim as an action value
# (then mis-bucketed in analytics). Coerce anything outside this set to "allow"
# so the boundary is validated, not just length-capped.
_KNOWN_ACTIONS = frozenset({
    "block", "redact", "rewrite", "flag", "alert", "model_downgrade",
    "reroute", "monitor", "monitored", "allow", "allowed", "confirm", "pass",
})

logger = logging.getLogger(__name__)


def _json_depth(value, _depth: int = 0) -> int:
    """Return the maximum nesting depth of a JSON-decoded value.

    Bails out as soon as ``_MAX_JSON_DEPTH`` is exceeded so a pathological
    2000-deep payload is rejected without recursing the whole structure.
    """
    if _depth > _MAX_JSON_DEPTH:
        return _depth
    if isinstance(value, dict):
        if not value:
            return _depth
        return max(_json_depth(v, _depth + 1) for v in value.values())
    if isinstance(value, (list, tuple)):
        if not value:
            return _depth
        return max(_json_depth(v, _depth + 1) for v in value)
    return _depth


def _get_redis_client():
    """Return Redis client from settings.REDIS_URL."""
    import redis

    url = getattr(settings, "REDIS_URL", "redis://localhost:6379/0")
    return redis.from_url(url, decode_responses=True)


def _validate_event_payload(data):
    """
    Validate payload matches EventPayload shape: agent_id, timestamp, event_type, data;
    optional source_os, user_id. Returns (None, None) if valid, else (error_response, None).
    """
    if not isinstance(data, dict):
        return Response({"detail": "Request body must be a JSON object."}, status=status.HTTP_400_BAD_REQUEST), None
    # Bound the serialized size + nesting depth before doing anything else so an
    # oversized / deeply-nested payload cannot reach the shared drain queue.
    try:
        serialized_len = len(json.dumps(data))
    except (TypeError, ValueError):
        return Response({"detail": "Request body is not JSON-serializable."}, status=status.HTTP_400_BAD_REQUEST), None
    if serialized_len > _MAX_EVENT_BYTES:
        return Response(
            {"detail": f"Event payload too large (max {_MAX_EVENT_BYTES} bytes)."},
            status=status.HTTP_400_BAD_REQUEST,
        ), None
    if _json_depth(data) > _MAX_JSON_DEPTH:
        return Response(
            {"detail": f"Event payload nested too deeply (max depth {_MAX_JSON_DEPTH})."},
            status=status.HTTP_400_BAD_REQUEST,
        ), None
    if not isinstance(data.get("agent_id"), str) or not data.get("agent_id").strip():
        return Response(
            {"detail": "agent_id is required and must be a non-empty string."}, status=status.HTTP_400_BAD_REQUEST
        ), None
    if len(data["agent_id"]) > _MAX_AGENT_ID_LEN:
        return Response(
            {"detail": f"agent_id too long (max {_MAX_AGENT_ID_LEN} chars)."}, status=status.HTTP_400_BAD_REQUEST
        ), None
    if not isinstance(data.get("timestamp"), str) or not data.get("timestamp").strip():
        return Response(
            {"detail": "timestamp is required and must be a non-empty string (ISO 8601)."},
            status=status.HTTP_400_BAD_REQUEST,
        ), None
    if len(data["timestamp"]) > _MAX_STR_FIELD_LEN:
        return Response(
            {"detail": f"timestamp too long (max {_MAX_STR_FIELD_LEN} chars)."}, status=status.HTTP_400_BAD_REQUEST
        ), None
    if not isinstance(data.get("event_type"), str) or not data.get("event_type").strip():
        return Response(
            {"detail": "event_type is required and must be a non-empty string."}, status=status.HTTP_400_BAD_REQUEST
        ), None
    if len(data["event_type"]) > _MAX_EVENT_TYPE_LEN:
        return Response(
            {"detail": f"event_type too long (max {_MAX_EVENT_TYPE_LEN} chars)."}, status=status.HTTP_400_BAD_REQUEST
        ), None
    if "data" not in data or not isinstance(data.get("data"), dict):
        return Response({"detail": "data is required and must be an object."}, status=status.HTTP_400_BAD_REQUEST), None
    # ``user_id`` maps onto EnforcementEvent.user_id (IntegerField) downstream.
    # Coerce to int-or-None at the producer so a non-scalar / non-numeric value
    # is never enqueued as a poison pill that the drain's bulk_create chokes on.
    raw_user_id = data.get("user_id")
    if raw_user_id is None:
        coerced_user_id = None
    else:
        try:
            coerced_user_id = int(raw_user_id)
        except (TypeError, ValueError):
            coerced_user_id = None
    # Optional
    event = {
        "agent_id": data["agent_id"].strip(),
        "timestamp": data["timestamp"].strip(),
        "event_type": data["event_type"].strip(),
        "source_os": data.get("source_os") if isinstance(data.get("source_os"), str) else None,
        "user_id": coerced_user_id,
        "data": data["data"],
    }
    return None, event


def _to_telemetry_event(event: dict, organization_id) -> dict:
    """
    Map a validated ingestion EventPayload onto the gateway telemetry-event schema
    that ``drain_telemetry_from_redis`` understands. The drain requires a positive
    ``organization_id`` (it skips unscoped events), derives source/module/metadata
    from ``metadata``, and reads ``action``/``threat_type``/``risk_score`` off the
    top level — so promote any of those the caller supplied under ``data``.
    """
    data = event.get("data") or {}
    if not isinstance(data, dict):
        data = {}
    # ``action`` maps onto EnforcementEvent.action (CharField max_length=16).
    # Coerce + truncate so a caller-supplied oversized action never becomes a
    # poison pill that crashes the drain's bulk_create for the whole batch.
    action = data.get("action") or "allow"
    if not isinstance(action, str):
        action = str(action)
    if len(action) > _MAX_ACTION_LEN:
        action = action[:_MAX_ACTION_LEN]
    # C-4: validate against the known action vocabulary (not just length). An
    # unknown/corrupt value (e.g. a redaction-mask artifact) is coerced to
    # "allow" with a warning rather than persisted verbatim as a bogus action.
    if action.lower() not in _KNOWN_ACTIONS:
        logger.warning("ingestion: unknown action %r coerced to 'allow'", action[:32])
        action = "allow"
    return {
        "organization_id": organization_id,
        "event_type": event.get("event_type", "request"),
        "action": action,
        "timestamp": event.get("timestamp"),
        "request_id": event.get("event_id"),
        "agent_id": event.get("agent_id"),
        "user_id": event.get("user_id"),
        "threat_type": data.get("threat_type") or "",
        "risk_score": data.get("risk_score") or 0,
        "model": data.get("model") or "",
        "metadata": {
            "source": "ingestion_api",
            "request_id": event.get("event_id"),
            "agent_id": event.get("agent_id"),
            "source_os": event.get("source_os"),
            "ingested_at": event.get("ingested_at"),
            "event_type": event.get("event_type"),
            "organization_id": organization_id,
            "extra": data,
        },
    }


def _enqueue(redis_client, event: dict, organization_id) -> None:
    """Push one ingestion event onto the telemetry drain queue."""
    telemetry_event = _to_telemetry_event(event, organization_id)
    redis_client.rpush(REDIS_TELEMETRY_KEY, json.dumps(telemetry_event))


class IngestionEventView(APIView):
    """POST /api/ingestion/events/ — ingest a single event for persistence."""

    # Intended caller is an agent presenting Authorization: Bearer <agent-key>
    # (or X-Agent-Key), NOT a browser session/JWT. Without an explicit
    # authentication_classes the project default (JWTAuthenticationWithTermination)
    # ran first and rejected the Bearer agent key as an invalid JWT (401) before
    # AgentAPIKeyPermission could ever validate it. Same pattern as
    # gateway_instance_views and policy evaluation_views.
    authentication_classes = [AgentKeyAuthentication]
    permission_classes = [AgentAPIKeyPermission]

    def post(self, request: Request):
        err, event = _validate_event_payload(request.data)
        if err is not None:
            return err
        org_id = getattr(request, "agent_organization_id", None)
        event_id = f"evt_{uuid.uuid4().hex[:16]}"
        event["event_id"] = event_id
        event["ingested_at"] = datetime.utcnow().isoformat()
        try:
            redis_client = _get_redis_client()
            _enqueue(redis_client, event, org_id)
        except Exception as e:
            return Response(
                {"detail": f"Ingestion failed: {e!s}", "event_id": event_id},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        # Unscoped events (global agent key, no org) are accepted but the drain
        # will not persist them — flag that back to the caller so it is not silent.
        persisted = bool(org_id)
        return Response(
            {
                "event_id": event_id,
                "status": "queued" if persisted else "queued_unscoped",
                "persisted": persisted,
                "message": (
                    "Event queued successfully."
                    if persisted
                    else "Event queued but not persisted: use a per-organization agent key for it to be stored."
                ),
            },
            status=status.HTTP_202_ACCEPTED,
        )


class IngestionBatchView(APIView):
    """POST /api/ingestion/events/batch/ — ingest multiple events. Body: { \"events\": [ ... ] }."""

    # Agent-key authenticated, same as IngestionEventView (see note there).
    authentication_classes = [AgentKeyAuthentication]
    permission_classes = [AgentAPIKeyPermission]

    def post(self, request: Request):
        data = request.data
        if not isinstance(data, dict):
            return Response(
                {"detail": "Request body must be a JSON object."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        events_list = data.get("events") if isinstance(data.get("events"), list) else None
        if not events_list:
            return Response(
                {"detail": "Request body must contain an 'events' array."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(events_list) > _MAX_BATCH_EVENTS:
            return Response(
                {"detail": f"Too many events in one request (max {_MAX_BATCH_EVENTS})."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        org_id = getattr(request, "agent_organization_id", None)
        results = {
            "total": len(events_list),
            "successful": 0,
            "failed": 0,
            "persisted": bool(org_id),
            "event_ids": [],
        }
        redis_client = None
        try:
            redis_client = _get_redis_client()
        except Exception as e:
            return Response(
                {"detail": f"Redis unavailable: {e!s}"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        for item in events_list:
            err, event = _validate_event_payload(item)
            if err is not None:
                results["failed"] += 1
                continue
            event_id = f"evt_{uuid.uuid4().hex[:16]}"
            event["event_id"] = event_id
            event["ingested_at"] = datetime.utcnow().isoformat()
            try:
                _enqueue(redis_client, event, org_id)
                results["successful"] += 1
                results["event_ids"].append(event_id)
            except Exception:
                results["failed"] += 1
        return Response(results, status=status.HTTP_202_ACCEPTED)
