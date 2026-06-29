"""Playground project_id helper used by telemetry risk-score updates."""

from __future__ import annotations

from django.test import SimpleTestCase

from core.models import is_isolation_playground_project_id, is_live_test_gateway_project_id


class TelemetryPlaygroundExclusionTests(SimpleTestCase):
    def test_playground_project_id_detected(self):
        self.assertTrue(is_isolation_playground_project_id("isolation-playground-acme"))

    def test_simulator_project_id_not_playground(self):
        self.assertFalse(is_isolation_playground_project_id("simulator-acme"))

    def test_live_test_includes_simulator_and_playground(self):
        self.assertTrue(is_live_test_gateway_project_id("isolation-playground-acme"))
        self.assertTrue(is_live_test_gateway_project_id("simulator-acme"))
        self.assertFalse(is_live_test_gateway_project_id("mcp-default-acme"))

    def test_empty_project_id(self):
        self.assertFalse(is_isolation_playground_project_id(None))
        self.assertFalse(is_isolation_playground_project_id(""))
        self.assertFalse(is_live_test_gateway_project_id(None))
