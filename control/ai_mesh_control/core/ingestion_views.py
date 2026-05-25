"""
Ingestion API: accept pipeline events and push to Redis stream.
Uses same format as data_pipeline DataIngestionService for compatibility.
"""

import json
import uuid
from datetime import datetime

from django.conf import settings
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from core.agent_auth import AgentAPIKeyPermission

EVENT_QUEUE_KEY = "event_queue"


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
    if not isinstance(data.get("agent_id"), str) or not data.get("agent_id").strip():
        return Response(
            {"detail": "agent_id is required and must be a non-empty string."}, status=status.HTTP_400_BAD_REQUEST
        ), None
    if not isinstance(data.get("timestamp"), str) or not data.get("timestamp").strip():
        return Response(
            {"detail": "timestamp is required and must be a non-empty string (ISO 8601)."},
            status=status.HTTP_400_BAD_REQUEST,
        ), None
    if not isinstance(data.get("event_type"), str) or not data.get("event_type").strip():
        return Response(
            {"detail": "event_type is required and must be a non-empty string."}, status=status.HTTP_400_BAD_REQUEST
        ), None
    if "data" not in data or not isinstance(data.get("data"), dict):
        return Response({"detail": "data is required and must be an object."}, status=status.HTTP_400_BAD_REQUEST), None
    # Optional
    event = {
        "agent_id": data["agent_id"].strip(),
        "timestamp": data["timestamp"].strip(),
        "event_type": data["event_type"].strip(),
        "source_os": data.get("source_os") if isinstance(data.get("source_os"), str) else None,
        "user_id": str(data["user_id"]) if data.get("user_id") is not None else None,
        "data": data["data"],
    }
    return None, event


class IngestionEventView(APIView):
    """POST /api/ingestion/events/ — ingest a single event into the pipeline stream."""

    permission_classes = [AgentAPIKeyPermission]

    def post(self, request: Request):
        err, event = _validate_event_payload(request.data)
        if err is not None:
            return err
        event_id = f"evt_{uuid.uuid4().hex[:16]}"
        event["event_id"] = event_id
        event["ingested_at"] = datetime.utcnow().isoformat()
        try:
            redis_client = _get_redis_client()
            redis_client.xadd(EVENT_QUEUE_KEY, {"data": json.dumps(event)})
        except Exception as e:
            return Response(
                {"detail": f"Ingestion failed: {e!s}", "event_id": event_id},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response(
            {"event_id": event_id, "status": "queued", "message": "Event queued successfully."},
            status=status.HTTP_202_ACCEPTED,
        )


class IngestionBatchView(APIView):
    """POST /api/ingestion/events/batch/ — ingest multiple events. Body: { \"events\": [ ... ] }."""

    permission_classes = [AgentAPIKeyPermission]

    def post(self, request: Request):
        data = request.data or {}
        events_list = data.get("events") if isinstance(data.get("events"), list) else None
        if not events_list:
            return Response(
                {"detail": "Request body must contain an 'events' array."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        results = {"total": len(events_list), "successful": 0, "failed": 0, "event_ids": []}
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
                redis_client.xadd(EVENT_QUEUE_KEY, {"data": json.dumps(event)})
                results["successful"] += 1
                results["event_ids"].append(event_id)
            except Exception:
                results["failed"] += 1
        return Response(results, status=status.HTTP_202_ACCEPTED)
