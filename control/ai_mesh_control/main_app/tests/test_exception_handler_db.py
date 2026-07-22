"""CP26: the project exception handler maps a transient DB OperationalError
(e.g. Postgres 'too many clients already', raised during authentication before
the view body) to a retryable 503 — not a raw 500 — while preserving the existing
400 (malformed input) and 500 (generic) behaviour. This is what makes the MCP
Observability tab (and every endpoint) degrade gracefully under connection-pool
exhaustion instead of showing a hard 500.
"""
from types import SimpleNamespace

from django.db import InterfaceError, OperationalError
from django.test import SimpleTestCase

from main_app.exception_handlers import safe_exception_handler


def _ctx(path="/api/mcp-connector/events/summary/"):
    return {"request": SimpleNamespace(path=path)}


class ExceptionHandlerDBTests(SimpleTestCase):
    def test_operational_error_too_many_clients_is_503(self):
        exc = OperationalError(
            'connection failed: FATAL:  sorry, too many clients already'
        )
        resp = safe_exception_handler(exc, _ctx())
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.data["code"], "db_unavailable")
        self.assertEqual(resp["Retry-After"], "2")

    def test_interface_error_is_503(self):
        resp = safe_exception_handler(InterfaceError("connection already closed"), _ctx())
        self.assertEqual(resp.status_code, 503)

    def test_value_error_still_400(self):
        resp = safe_exception_handler(ValueError("invalid literal for int()"), _ctx())
        self.assertEqual(resp.status_code, 400)

    def test_generic_error_still_500(self):
        resp = safe_exception_handler(RuntimeError("boom"), _ctx())
        self.assertEqual(resp.status_code, 500)
