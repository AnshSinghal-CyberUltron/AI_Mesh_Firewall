"""
log_extras.py — Development logging utilities for the AISecShield backend.

Provides three components wired into settings.LOGGING when DJANGO_LOG_JSON=true
or when included explicitly:

  HealthEndpointFilter     — drop /health and /api/health lines from access logs
  StructuredJSONFormatter  — emit each log record as a single JSON line
  CorrelationIDMiddleware  — propagate X-Request-ID header into every log record
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from contextvars import ContextVar

# ---------------------------------------------------------------------------
# Shared ContextVars — set by CorrelationIDMiddleware, read by formatter
# ---------------------------------------------------------------------------

_request_id_var: ContextVar[str] = ContextVar("request_id", default="")
_session_id_var: ContextVar[str] = ContextVar("session_id", default="")
_org_id_var: ContextVar[str] = ContextVar("org_id", default="")


def get_request_id() -> str:
    """Return the correlation ID for the current request context."""
    return _request_id_var.get()


# ---------------------------------------------------------------------------
# HealthEndpointFilter
# ---------------------------------------------------------------------------

_SUPPRESSED_PATHS = frozenset(
    [
        "/health",
        "/api/health",
        "/readyz",
        "/livez",
        "/ping",
        "/_health",
        "/__health",
    ]
)


class HealthEndpointFilter(logging.Filter):
    """
    Drop Django server / request log records for health-check endpoints.

    Attach to the 'django.server' and 'django.request' handlers to stop
    readiness-probe polling from burying real log lines.
    """

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        message = record.getMessage()
        return not any(path in message for path in _SUPPRESSED_PATHS)


# ---------------------------------------------------------------------------
# StructuredJSONFormatter
# ---------------------------------------------------------------------------


class StructuredJSONFormatter(logging.Formatter):
    """
    Emit one compact JSON object per log record.

    Fixed fields (always present):
        timestamp  — ISO-8601 with microseconds (UTC)
        level      — DEBUG / INFO / WARNING / ERROR / CRITICAL
        service    — always "backend"
        component  — logger name (e.g. "django.request", "policy")
        module     — source module name
        lineno     — source line number
        request_id — correlation ID from CorrelationIDMiddleware (or "")
        session_id — Django session key (or "")
        org_id     — organisation ID set by auth layer (or "")
        message    — formatted log message

    Optional fields (only when present):
        exc_info   — formatted exception traceback string
    """

    SERVICE = "backend"

    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S.%f+00:00"),
            "level": record.levelname,
            "service": self.SERVICE,
            "component": record.name,
            "module": record.module,
            "lineno": record.lineno,
            "request_id": _request_id_var.get(),
            "session_id": _session_id_var.get(),
            "org_id": _org_id_var.get(),
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


# ---------------------------------------------------------------------------
# CorrelationIDMiddleware
# ---------------------------------------------------------------------------


class CorrelationIDMiddleware:
    """
    Django middleware (sync + async capable) that:

      1. Reads X-Request-ID from the incoming request headers, or generates a
         new UUID4 if absent.
      2. Stores the ID in ``_request_id_var`` so every logger call within the
         same async task / thread picks it up via StructuredJSONFormatter.
      3. Echoes the ID back in the X-Request-ID response header so API clients
         can correlate their requests with backend log lines.

    Supports both WSGI (sync) and ASGI (async) Django deployments.
    """

    async_capable = True
    sync_capable = True

    def __init__(self, get_response):
        self.get_response = get_response
        if asyncio.iscoroutinefunction(self.get_response):
            # Mark this instance as a coroutine function so Django's ASGI
            # adapter does not wrap it in sync_to_async.
            markcoroutinefunction(self)

    def __call__(self, request):
        if asyncio.iscoroutinefunction(self):
            return self._async_call(request)
        return self._sync_call(request)

    # -- sync path -----------------------------------------------------------

    def _sync_call(self, request):
        req_id = request.META.get("HTTP_X_REQUEST_ID") or str(uuid.uuid4())
        token = _request_id_var.set(req_id)
        try:
            response = self.get_response(request)
        finally:
            _request_id_var.reset(token)
        response["X-Request-ID"] = req_id
        return response

    # -- async path ----------------------------------------------------------

    async def _async_call(self, request):
        req_id = request.META.get("HTTP_X_REQUEST_ID") or str(uuid.uuid4())
        token = _request_id_var.set(req_id)
        try:
            response = await self.get_response(request)
        finally:
            _request_id_var.reset(token)
        response["X-Request-ID"] = req_id
        return response


# ---------------------------------------------------------------------------
# Helper — set org_id after authentication (call from auth middleware / views)
# ---------------------------------------------------------------------------


def set_org_id(org_id: str) -> None:
    """
    Inject the authenticated organisation ID into the log context for the
    current request.  Call this from your auth middleware after the user's
    organisation is resolved.
    """
    _org_id_var.set(str(org_id))


# ---------------------------------------------------------------------------
# markcoroutinefunction shim (Django 4.1+ / asgiref)
# ---------------------------------------------------------------------------

try:
    from asgiref.sync import markcoroutinefunction  # type: ignore[assignment]
except ImportError:  # pragma: no cover

    def markcoroutinefunction(func):  # type: ignore[misc]
        func._is_coroutine = asyncio.coroutines._is_coroutine
        return func
