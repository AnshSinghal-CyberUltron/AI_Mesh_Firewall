"""Ingestion views: agent-key authentication wiring.

The ingestion views are called by agents with Authorization: Bearer <agent-key>
(or X-Agent-Key). They declared AgentAPIKeyPermission but no
authentication_classes, so the project default JWT authenticator ran first and
rejected the Bearer agent key as an invalid JWT (401) before the permission
could validate it. They now pin authentication_classes = [AgentKeyAuthentication],
matching gateway_instance_views and policy evaluation_views.

Producer contract (core/ingestion_views.py module docstring, since 9f8c9489): an
accepted event is pushed with RPUSH onto the ``telemetry:events`` list that
``drain_telemetry_from_redis`` consumes, not XADD onto the old ``event_queue``
stream that nothing read. The drain only persists events with an organization, so
the organization comes from the authenticated per-org agent key; the global
AGENT_API_KEY has no org and is answered with ``queued_unscoped`` (#106).
"""

from __future__ import annotations

import json
from unittest import mock

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

# The CONSUMER's key: drain_telemetry_from_redis reads this list. Comparing pushes
# with the drain's constant, not the producer's own (core.ingestion_views), makes
# a producer/drain key mismatch (the dead `event_queue` defect fixed in 9f8c9489)
# fail these tests (#113 review, A2).
from core.tasks import REDIS_TELEMETRY_KEY as DRAIN_QUEUE_KEY

AGENT_KEY = "test-agent-key-123"


def _valid_event():
    return {
        "agent_id": "agent-001",
        "timestamp": "2026-06-11T00:00:00Z",
        "event_type": "telemetry",
        "data": {"cpu": 10},
    }


def _enqueued(redis_mock) -> list[dict]:
    """Every telemetry event pushed onto the queue the drain reads, decoded."""
    out = []
    for call in redis_mock.rpush.call_args_list:
        key, payload = call.args
        assert key == DRAIN_QUEUE_KEY, f"pushed to {key!r}, the drain reads {DRAIN_QUEUE_KEY!r}"
        out.append(json.loads(payload))
    return out


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
        # The global key carries no organization: accepted, but flagged as not persisted.
        self.assertEqual(resp.json().get("status"), "queued_unscoped")
        self.assertIs(resp.json().get("persisted"), False)
        get_client.return_value.rpush.assert_called_once()
        get_client.return_value.xadd.assert_not_called()
        (event,) = _enqueued(get_client.return_value)
        self.assertIsNone(event["organization_id"])
        self.assertEqual(event["agent_id"], "agent-001")

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
        get_client.return_value.rpush.assert_called_once()
        self.assertEqual(len(_enqueued(get_client.return_value)), 1)

    def test_invalid_key_rejected(self):
        with mock.patch("core.ingestion_views._get_redis_client") as get_client:
            resp = self._client("wrong-key").post(
                "/api/ingestion/events/", _valid_event(), format="json"
            )
        self.assertEqual(resp.status_code, 401)
        get_client.return_value.rpush.assert_not_called()
        get_client.return_value.xadd.assert_not_called()

    def test_missing_key_rejected_when_key_configured(self):
        with mock.patch("core.ingestion_views._get_redis_client") as get_client:
            resp = self._client(key=None).post(
                "/api/ingestion/events/", _valid_event(), format="json"
            )
        self.assertEqual(resp.status_code, 401)
        get_client.return_value.rpush.assert_not_called()
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
        self.assertIs(body["persisted"], False)
        self.assertEqual(get_client.return_value.rpush.call_count, 2)
        self.assertEqual(len(_enqueued(get_client.return_value)), 2)

    def test_batch_rejects_invalid_key(self):
        with mock.patch("core.ingestion_views._get_redis_client") as get_client:
            resp = self._client("wrong-key").post(
                "/api/ingestion/events/batch/",
                {"events": [_valid_event()]},
                format="json",
            )
        self.assertEqual(resp.status_code, 401)
        get_client.return_value.rpush.assert_not_called()


@override_settings(AGENT_API_KEY=AGENT_KEY)
class IngestionOrgKeyScopeTests(TestCase):
    """A per-org agent key scopes the event to that org; the body cannot re-target it."""

    def setUp(self):
        from auth.models import Organization
        from core.models import OrganizationAgentKey

        self.org = Organization.objects.create(name="Ingest Org", slug="ingest-org")
        self.other_org = Organization.objects.create(name="Other Org", slug="ingest-other")
        _, self.org_key = OrganizationAgentKey.generate_key(self.org)

    def _hostile_event(self):
        event = _valid_event()
        # A caller trying to land its event in another tenant.
        event["organization_id"] = self.other_org.id
        event["data"] = {"cpu": 10, "organization_id": self.other_org.id}
        return event

    def test_org_key_event_is_scoped_to_key_org(self):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.org_key}")
        with mock.patch("core.ingestion_views._get_redis_client") as get_client:
            resp = client.post("/api/ingestion/events/", self._hostile_event(), format="json")
        self.assertEqual(resp.status_code, 202, resp.content)
        self.assertEqual(resp.json().get("status"), "queued")
        self.assertIs(resp.json().get("persisted"), True)
        (event,) = _enqueued(get_client.return_value)
        # The drain reads organization_id, then metadata.organization_id
        # (core/tasks.py drain_telemetry_from_redis): both must be the key's org.
        self.assertEqual(event["organization_id"], self.org.id)
        self.assertEqual(event["metadata"]["organization_id"], self.org.id)

    def test_org_key_batch_is_scoped_to_key_org(self):
        client = APIClient()
        client.credentials(HTTP_X_AGENT_KEY=self.org_key)
        with mock.patch("core.ingestion_views._get_redis_client") as get_client:
            resp = client.post(
                "/api/ingestion/events/batch/",
                {"events": [self._hostile_event(), self._hostile_event()]},
                format="json",
            )
        self.assertEqual(resp.status_code, 202, resp.content)
        self.assertEqual(resp.json()["successful"], 2)
        self.assertIs(resp.json()["persisted"], True)
        events = _enqueued(get_client.return_value)
        self.assertEqual(len(events), 2)
        for event in events:
            self.assertEqual(event["organization_id"], self.org.id)
            self.assertEqual(event["metadata"]["organization_id"], self.org.id)

    def test_inactive_org_key_rejected(self):
        from core.models import OrganizationAgentKey

        OrganizationAgentKey.objects.filter(organization=self.org).update(is_active=False)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.org_key}")
        with mock.patch("core.ingestion_views._get_redis_client") as get_client:
            resp = client.post("/api/ingestion/events/", _valid_event(), format="json")
        self.assertEqual(resp.status_code, 401)
        get_client.return_value.rpush.assert_not_called()
