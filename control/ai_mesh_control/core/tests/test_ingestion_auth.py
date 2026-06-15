"""Ingestion views: agent-key authentication wiring.

The ingestion views are called by agents with Authorization: Bearer <agent-key>
(or X-Agent-Key). They declared AgentAPIKeyPermission but no
authentication_classes, so the project default JWT authenticator ran first and
rejected the Bearer agent key as an invalid JWT (401) before the permission
could validate it. They now pin authentication_classes = [AgentKeyAuthentication],
matching gateway_instance_views and policy evaluation_views.
"""

from __future__ import annotations

from unittest import mock

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

AGENT_KEY = "test-agent-key-123"


def _valid_event():
    return {
        "agent_id": "agent-001",
        "timestamp": "2026-06-11T00:00:00Z",
        "event_type": "telemetry",
        "data": {"cpu": 10},
    }


@override_settings(AGENT_API_KEY=AGENT_KEY)
class IngestionAuthTests(TestCase):
    def _client(self, key: str | None = AGENT_KEY):
        client = APIClient()
        if key is not None:
            client.credentials(HTTP_AUTHORIZATION=f"Bearer {key}")
        return client

    def test_bearer_agent_key_accepted_on_single_event(self):
        # Regression: with the default JWT authenticator this Bearer agent key
        # was rejected as a malformed JWT before the permission ever ran.
        with mock.patch("core.ingestion_views._get_redis_client") as get_client:
            resp = self._client().post("/api/ingestion/events/", _valid_event(), format="json")
        self.assertEqual(resp.status_code, 202, resp.content)
        self.assertEqual(resp.json().get("status"), "queued")
        get_client.return_value.xadd.assert_called_once()

    def test_x_agent_key_header_accepted(self):
        client = APIClient()
        with mock.patch("core.ingestion_views._get_redis_client") as get_client:
            resp = client.post(
                "/api/ingestion/events/",
                _valid_event(),
                format="json",
                HTTP_X_AGENT_KEY=AGENT_KEY,
            )
        self.assertEqual(resp.status_code, 202, resp.content)
        get_client.return_value.xadd.assert_called_once()

    def test_invalid_key_rejected(self):
        with mock.patch("core.ingestion_views._get_redis_client") as get_client:
            resp = self._client("wrong-key").post(
                "/api/ingestion/events/", _valid_event(), format="json"
            )
        self.assertEqual(resp.status_code, 401)
        get_client.return_value.xadd.assert_not_called()

    def test_missing_key_rejected_when_key_configured(self):
        with mock.patch("core.ingestion_views._get_redis_client") as get_client:
            resp = self._client(key=None).post(
                "/api/ingestion/events/", _valid_event(), format="json"
            )
        self.assertEqual(resp.status_code, 401)
        get_client.return_value.xadd.assert_not_called()

    def test_bearer_agent_key_accepted_on_batch(self):
        with mock.patch("core.ingestion_views._get_redis_client") as get_client:
            resp = self._client().post(
                "/api/ingestion/events/batch/",
                {"events": [_valid_event(), _valid_event()]},
                format="json",
            )
        self.assertEqual(resp.status_code, 202, resp.content)
        body = resp.json()
        self.assertEqual(body["successful"], 2)
        self.assertEqual(get_client.return_value.xadd.call_count, 2)

    def test_batch_rejects_invalid_key(self):
        resp = self._client("wrong-key").post(
            "/api/ingestion/events/batch/",
            {"events": [_valid_event()]},
            format="json",
        )
        self.assertEqual(resp.status_code, 401)
