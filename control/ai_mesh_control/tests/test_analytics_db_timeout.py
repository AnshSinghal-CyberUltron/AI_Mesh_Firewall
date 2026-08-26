"""Phase 0a C-7: analytics alias timeouts (T-C6 helpers)."""

from __future__ import annotations

from pathlib import Path

from django.test import SimpleTestCase

from main_app.db_url import postgres_database_from_url


class AnalyticsDbTimeoutTests(SimpleTestCase):
    def test_analytics_timeouts_are_aggressive_5s_and_30s(self):
        from main_app.analytics_db import IDLE_IN_TX_MS, STATEMENT_TIMEOUT_MS

        self.assertEqual(STATEMENT_TIMEOUT_MS, 5000)
        self.assertEqual(IDLE_IN_TX_MS, 30000)

    def test_analytics_alias_name(self):
        from main_app.analytics_db import ANALYTICS_DB_ALIAS

        self.assertEqual(ANALYTICS_DB_ALIAS, "analytics")

    def test_postgres_url_embeds_statement_timeout_options(self):
        db = postgres_database_from_url(
            "postgresql://u:p@postgres:5432/ai_mesh_firewall",
            conn_max_age=0,
            statement_timeout_ms=5000,
            idle_in_tx_ms=30000,
        )
        options = (db.get("OPTIONS") or {}).get("options", "")
        compact = options.replace(" ", "")
        self.assertIn("statement_timeout=5000", compact)
        self.assertIn("idle_in_transaction_session_timeout=30000", compact)

    def test_pgbouncer_url_still_disables_cursors_with_timeouts(self):
        db = postgres_database_from_url(
            "postgresql://u:p@pgbouncer:6432/ai_mesh_firewall",
            conn_max_age=0,
            statement_timeout_ms=5000,
            idle_in_tx_ms=30000,
        )
        self.assertIs(db["DISABLE_SERVER_SIDE_CURSORS"], True)

    def test_set_local_sql_is_transaction_scoped(self):
        from main_app.analytics_db import analytics_set_local_timeout_sql

        statements = analytics_set_local_timeout_sql()
        joined = " ".join(statements).upper()
        self.assertIn("SET LOCAL STATEMENT_TIMEOUT", joined)
        self.assertIn("SET LOCAL IDLE_IN_TRANSACTION_SESSION_TIMEOUT", joined)
        blob = " ".join(statements)
        self.assertIn("5000", blob)
        self.assertIn("30000", blob)

    def test_entrypoint_defaults_asgi_threads_to_two(self):
        text = (Path(__file__).resolve().parents[2] / "server-entrypoint.sh").read_text()
        self.assertIn("ASGI_THREADS=2", text)
        self.assertNotIn("ASGI_THREADS=8", text)

    def test_analytics_http_path_covers_security_and_dashboard(self):
        from main_app.analytics_db import is_analytics_http_path

        self.assertTrue(is_analytics_http_path("/api/security/soc-kpis/"))
        self.assertTrue(is_analytics_http_path("/api/dashboard/model-usage/"))
        self.assertFalse(is_analytics_http_path("/api/auth/token/"))
        self.assertFalse(is_analytics_http_path("/api/health/"))

    def test_analytics_middleware_is_registered(self):
        from django.conf import settings

        self.assertTrue(any("AnalyticsTimeoutMiddleware" in m for m in settings.MIDDLEWARE))

    def test_databases_has_analytics_alias(self):
        from django.conf import settings
        from main_app.analytics_db import ANALYTICS_DB_ALIAS

        self.assertIn(ANALYTICS_DB_ALIAS, settings.DATABASES)
        self.assertEqual(settings.DATABASES[ANALYTICS_DB_ALIAS].get("TEST", {}).get("MIRROR"), "default")

    def test_timeout_probe_route_is_mounted(self):
        from django.urls import reverse

        self.assertEqual(reverse("security-analytics-timeout-probe"), "/api/security/analytics-timeout-probe/")
