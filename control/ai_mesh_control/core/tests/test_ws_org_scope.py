"""#120 audit: the notifications WebSocket joins only the caller's own org group.

Before #120 `NotificationConsumer.connect` let ANY superuser pick a tenant with
?organization_id=<id> (ws/consumers.py), so an org B superuser could subscribe to org
A's live notification stream. R11 (auth/models.py): only a platform operator
(is_staff AND is_platform_operator) may target another org.

Lives under core/tests because CI runs the `core policy auth mcp_connector` labels.
"""

from __future__ import annotations

from asgiref.sync import async_to_sync
from channels.testing import WebsocketCommunicator
from django.contrib.auth import get_user_model
from django.test import TransactionTestCase, override_settings
from rest_framework_simplejwt.tokens import AccessToken

from ws.consumers import NotificationConsumer, notification_group_for_org

User = get_user_model()


class _ProbeConsumer(NotificationConsumer):
    """Records the group each accepted connection joined (the communicator hides the instance)."""

    joined: list = []

    async def connect(self):
        await super().connect()
        if getattr(self, "_connected", False):
            type(self).joined.append(self.group_name)


@override_settings(
    ASGI_ALLOWED_ORIGINS=[],
    CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}},
)
class NotificationSocketOrgScopeTests(TransactionTestCase):
    # TransactionTestCase: the consumer reads users from another thread.

    def setUp(self):
        from auth.models import Organization, UserProfile

        self.org_a = Organization.objects.create(name="WS A", slug="ws-scope-a")
        self.org_b = Organization.objects.create(name="WS B", slug="ws-scope-b")

        def user(name, org, *, superuser=False, staff=False, operator=False):
            u = User.objects.create_user(
                username=name, email=f"{name}@example.com", password="pw",
                is_superuser=superuser, is_staff=staff or superuser,
            )
            profile, _ = UserProfile.objects.get_or_create(user=u)
            profile.organization = org
            profile.is_platform_operator = operator
            profile.save(update_fields=["organization", "is_platform_operator"])
            return u

        self.super_b = user("ws_super_b", self.org_b, superuser=True)
        self.orgless_super = user("ws_super_none", None, superuser=True)
        self.operator = user("ws_operator", self.org_b, staff=True, operator=True)

    def _connect(self, user, query=""):
        async def run():
            token = str(AccessToken.for_user(user))
            _ProbeConsumer.joined = []
            communicator = WebsocketCommunicator(
                _ProbeConsumer.as_asgi(), f"/ws/notifications/?token={token}{query}"
            )
            connected, code = await communicator.connect()
            group = _ProbeConsumer.joined[-1] if connected and _ProbeConsumer.joined else None
            await communicator.disconnect()
            return connected, code, group

        return async_to_sync(run)()

    def test_superuser_naming_another_org_joins_own_org_only(self):
        connected, _code, group = self._connect(self.super_b, f"&organization_id={self.org_a.id}")
        self.assertTrue(connected)
        self.assertEqual(group, notification_group_for_org(self.org_b.id))

    def test_orgless_superuser_naming_an_org_is_refused(self):
        connected, code, _group = self._connect(
            self.orgless_super, f"&organization_id={self.org_a.id}"
        )
        self.assertFalse(connected)
        self.assertEqual(code, 4403)

    def test_platform_operator_may_name_another_org(self):
        connected, _code, group = self._connect(self.operator, f"&organization_id={self.org_a.id}")
        self.assertTrue(connected)
        self.assertEqual(group, notification_group_for_org(self.org_a.id))

    def test_superuser_without_naming_an_org_joins_own_org(self):
        connected, _code, group = self._connect(self.super_b)
        self.assertTrue(connected)
        self.assertEqual(group, notification_group_for_org(self.org_b.id))
