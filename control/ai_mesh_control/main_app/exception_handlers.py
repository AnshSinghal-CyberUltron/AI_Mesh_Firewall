"""Project-wide DRF exception handler.

Two goals:
  * Convert the common "malformed input" exceptions (unguarded ``int()`` of a
    bad ?days=/?limit=, ``.strip()``/iteration of a wrong-typed body field) into
    a clean ``400`` across EVERY endpoint, instead of an unhandled ``500`` —
    closing the systemic 500 surface without touching dozens of views.
  * Never leak internals: with ``DEBUG=False`` a generic 500 is returned for
    anything we don't explicitly translate (no settings/traceback in the body).
"""

import logging

from django.db import InterfaceError, OperationalError
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

logger = logging.getLogger(__name__)


def safe_exception_handler(exc, context):
    # Let DRF handle the exceptions it knows about (ValidationError, 404, throttle,
    # auth, etc.) — those already produce correct, sanitized responses.
    response = drf_exception_handler(exc, context)
    if response is not None:
        return response

    request = context.get("request") if context else None
    path = getattr(request, "path", "?")

    # Transient DB operational faults → 503 (retryable), not a raw 500. The classic
    # case is Postgres "FATAL: sorry, too many clients already" (connection-pool
    # exhaustion under load), raised in DRF ``perform_authentication`` BEFORE the
    # view body — so it surfaces here on ANY endpoint (the MCP Observability tab
    # 500 was this). A 503 + Retry-After lets the client back off and retry instead
    # of showing a hard error; the pool recovers in seconds. (CP26)
    if isinstance(exc, (OperationalError, InterfaceError)):
        logger.warning("Transient DB fault (%s) at %s: %s", type(exc).__name__, path, exc)
        resp = Response(
            {"detail": "Service temporarily unavailable, please retry.",
             "code": "db_unavailable"},
            status=503,
        )
        resp["Retry-After"] = "2"
        return resp

    # Malformed-input errors → 400 (not 500). Covers int('abc'), 'x'.strip() on a
    # non-str, iterating a non-iterable body field, and out-of-range numerics that
    # overflow C-int conversion (e.g. timedelta(hours=99999999999) raising
    # OverflowError, a subclass of ArithmeticError).
    if isinstance(exc, (ValueError, TypeError, ArithmeticError)):
        logger.warning("Bad request (%s) at %s: %s", type(exc).__name__, path, exc)
        return Response({"detail": "Invalid request parameter."}, status=400)

    # Anything else is a genuine server error: log it server-side and return a
    # generic 500. (DEBUG=False ensures Django itself also never leaks.)
    logger.exception("Unhandled server error at %s", path)
    return Response({"detail": "Internal server error."}, status=500)
