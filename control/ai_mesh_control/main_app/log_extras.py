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
from datetime import datetime, timezone

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
            # logging.Formatter.formatTime uses time.strftime, which does NOT
            # support %f — it would emit a literal "%f". Build the UTC ISO-8601
            # timestamp from record.created via datetime, which DOES expand %f.
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%S.%f+00:00"
            ),
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
        # Establish a clean per-request baseline for org_id and always reset it
        # in finally so an org from a prior request never leaks onto a reused
        # thread / worker. The real org id is populated after the view runs
        # (request.user / agent identity is available by then).
        org_token = _org_id_var.set("")
        try:
            response = self.get_response(request)
            set_org_id(_resolve_org_id(request))
        finally:
            _org_id_var.reset(org_token)
            _request_id_var.reset(token)
        response["X-Request-ID"] = req_id
        return response

    # -- async path ----------------------------------------------------------

    async def _async_call(self, request):
        req_id = request.META.get("HTTP_X_REQUEST_ID") or str(uuid.uuid4())
        token = _request_id_var.set(req_id)
        # Establish a clean per-request baseline for org_id and always reset it
        # in finally so an org from a prior request never leaks onto a reused
        # async task / worker. The real org id is populated after the view runs.
        org_token = _org_id_var.set("")
        try:
            response = await self.get_response(request)
            set_org_id(_resolve_org_id(request))
        finally:
            _org_id_var.reset(org_token)
            _request_id_var.reset(token)
        response["X-Request-ID"] = req_id
        return response


# ---------------------------------------------------------------------------
# Helper — set org_id after authentication (call from auth middleware / views)
# ---------------------------------------------------------------------------


def set_org_id(org_id) -> None:
    """
    Inject the authenticated organisation ID into the log context for the
    current request.  Call this from your auth middleware / middleware response
    path after the user's organisation is resolved.

    Accepts an int / str org id or ``None``. When the org id cannot be resolved
    (anonymous request, unauthenticated, etc.) the call is a no-op so the
    current context value (the per-request "" baseline) is preserved instead of
    overwriting it with the literal string "None".
    """
    if org_id is None:
        return
    _org_id_var.set(str(org_id))


def _resolve_org_id(request):
    """
    Resolve the authenticated organisation id from the request principal without
    issuing new DB queries where possible.

    Order of resolution:
      1. ``request.agent_organization_id`` — set by agent-key auth.
      2. ``request.user.profile.organization_id`` — the FK id attribute, which
         does NOT trigger a related-object fetch (unlike ``.organization``).

    Returns the org id (int) or ``None`` when it cannot be determined. Any
    AttributeError / lazy-eval surprise is swallowed so logging concerns never
    break the request.
    """
    try:
        org = getattr(request, "agent_organization_id", None) or getattr(
            getattr(getattr(request, "user", None), "profile", None),
            "organization_id",
            None,
        )
        return org
    except Exception:  # pragma: no cover - logging must never break a request
        return None


# ---------------------------------------------------------------------------
# markcoroutinefunction shim (Django 4.1+ / asgiref)
# ---------------------------------------------------------------------------

try:
    from asgiref.sync import markcoroutinefunction  # type: ignore[assignment]
except ImportError:  # pragma: no cover

    def markcoroutinefunction(func):  # type: ignore[misc]
        func._is_coroutine = asyncio.coroutines._is_coroutine
        return func
