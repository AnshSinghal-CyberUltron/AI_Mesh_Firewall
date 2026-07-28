"""Org-scoping guards for the live notifications WebSocket consumer."""

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from unittest.mock import AsyncMock, MagicMock, patch

from auth.models import Organization, UserProfile
from ws.consumers import NotificationConsumer, _get_org_id_from_scope


User = get_user_model()


class OrgIdFromScopeTests(SimpleTestCase):
    def test_parses_organization_id_query_param(self):
        scope = {"query_string": b"token=abc&organization_id=42"}
        self.assertEqual(_get_org_id_from_scope(scope), 42)

    def test_invalid_organization_id_returns_none(self):
        scope = {"query_string": b"organization_id=not-a-number"}
        self.assertIsNone(_get_org_id_from_scope(scope))


class NotificationConsumerOrgIsolationTests(TestCase):
    def setUp(self):
        self.org_a = Organization.objects.create(name="Org A", slug="org-a-ws")
        self.org_b = Organization.objects.create(name="Org B", slug="org-b-ws")
        self.user = User.objects.create_user(
            username="ws-tenant@example.com",
            email="ws-tenant@example.com",
            password="pass",
        )
        profile, _ = UserProfile.objects.get_or_create(user=self.user)
        profile.organization = self.org_a
        profile.save(update_fields=["organization"])

    def _make_consumer(self, query: bytes):
        consumer = NotificationConsumer()
        consumer.scope = {
            "headers": [(b"origin", b"http://localhost:8180")],
            "query_string": query,
            "type": "websocket",
        }
        consumer.accept = AsyncMock()
        consumer.close = AsyncMock()
        consumer.channel_layer = MagicMock()
        consumer.channel_layer.group_add = AsyncMock()
        consumer.channel_name = "test-channel"
        return consumer

    @patch("ws.consumers._get_user_from_token", new_callable=AsyncMock)
    def test_non_superuser_mismatch_org_claim_rejected(self, mock_user):
        mock_user.return_value = self.user
        consumer = self._make_consumer(
            f"token=abc&organization_id={self.org_b.id}".encode()
        )
        with patch("ws.consumers.settings") as settings_mock:
            settings_mock.ASGI_ALLOWED_ORIGINS = []
            async_to_sync(consumer.connect)()
        consumer.close.assert_awaited_with(code=4403)
        consumer.accept.assert_not_awaited()

    @patch("ws.consumers._get_user_from_token", new_callable=AsyncMock)
    def test_non_superuser_matching_org_claim_accepted(self, mock_user):
        mock_user.return_value = self.user
        consumer = self._make_consumer(
            f"token=abc&organization_id={self.org_a.id}".encode()
        )
        with patch("ws.consumers.settings") as settings_mock:
            settings_mock.ASGI_ALLOWED_ORIGINS = []
            async_to_sync(consumer.connect)()
        consumer.accept.assert_awaited()
        self.assertEqual(consumer.scope["organization_id"], self.org_a.id)
        self.assertEqual(consumer.group_name, f"notifications_{self.org_a.id}")
