import logging

from django.conf import settings
from django.db import connection
from django.http import JsonResponse
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny

logger = logging.getLogger(__name__)


@extend_schema(
    tags=["Health"],
    summary="Health check",
    description='Simple liveness probe. Returns `{"status": "ok"}` when the backend is running.',
    responses={200: {"type": "object", "properties": {"status": {"type": "string", "example": "ok"}}}},
)
@api_view(["GET"])
@permission_classes([AllowAny])
def health_view(request):
    """GET /api/health/ — simple liveness check for monitoring."""
    return JsonResponse({"status": "ok"})


@api_view(["GET"])
@permission_classes([AllowAny])
def services_health_view(request):
    """GET /api/health/services/ — probe all backend dependencies and return per-service status."""
    result = {}

    # ── Database ──
    try:
        connection.ensure_connection()
        result["database"] = {"status": "ok", "error": None}
    except Exception as exc:
        logger.warning("Health: database probe failed: %s", exc)
        result["database"] = {"status": "error", "error": str(exc)}

    # ── Redis ──
    try:
        import redis as _redis
        _redis_url = getattr(settings, "REDIS_URL", "redis://localhost:6379/0")
        _r = _redis.from_url(_redis_url, socket_timeout=2, socket_connect_timeout=2)
        _r.ping()
        result["redis"] = {"status": "ok", "error": None}
    except Exception as exc:
        logger.warning("Health: Redis probe failed: %s", exc)
        result["redis"] = {"status": "error", "error": str(exc)}

    # ── Celery (implicit: proves worker + broker reachable) ──
    try:
        from main_app.celery_app import app as _celery_app
        _inspector = _celery_app.control.inspect(timeout=2.0)
        _pings = _inspector.ping() or {}
        _workers = list(_pings.keys())
        if _workers:
            result["celery"] = {"status": "ok", "workers": _workers, "error": None}
        else:
            result["celery"] = {"status": "error", "workers": [], "error": "No Celery workers responded"}
    except Exception as exc:
        logger.warning("Health: Celery probe failed: %s", exc)
        result["celery"] = {"status": "error", "workers": [], "error": str(exc)}

    # ── RabbitMQ (direct AMQP connection probe via kombu) ──
    try:
        from kombu import Connection as _KombuConnection
        _broker_url = getattr(settings, "CELERY_BROKER_URL", "amqp://guest:guest@localhost:5672//")
        _conn = _KombuConnection(_broker_url)
        _conn.ensure_connection(max_retries=1, interval_start=0, interval_step=0, timeout=2)
        _conn.close()
        result["rabbitmq"] = {"status": "ok", "error": None}
    except Exception as exc:
        logger.warning("Health: RabbitMQ probe failed: %s", exc)
        result["rabbitmq"] = {"status": "error", "error": str(exc)}

    return JsonResponse(result)
