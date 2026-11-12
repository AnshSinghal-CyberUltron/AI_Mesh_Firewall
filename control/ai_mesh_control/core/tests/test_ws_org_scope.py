"""#120 / #122: which org's notification stream a socket joins, who may connect, what is audited.

Owner decision (12 Nov 2026): "superuser = ZeroShield staff". Staff (and platform
operators, #139) may name any EXISTING org with ?organization_id; the subscription is
audited (``cross_org_access``) before the socket joins. Anyone else naming another org
joins their own org, and the attempt is audited (``cross_org_denied``). No org: 4403.

Connect also applies the REST user checks (Reviewer A S2 on #122, #131 connect part):
inactive users, users with an active TerminatedSession and tokens minted before the
user's last password change are refused with 4401.

Lives under core/tests because CI runs the `core policy auth mcp_connector` labels.
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import ClassVar
from unittest import mock

from asgiref.sync import async_to_sync
from channels.testing import WebsocketCommunicator
from django.contrib.auth import get_user_model
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework_simplejwt.tokens import AccessToken

from ws.consumers import NotificationConsumer, notification_group_for_org

User = get_user_model()


class _ProbeConsumer(NotificationConsumer):
    """Records the group each accepted connection joined (the communicator hides the instance)."""

    joined: ClassVar[list] = []

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

        self.staff_b = user("ws_staff_b", self.org_b, superuser=True)
        self.orgless_staff = user("ws_staff_none", None, superuser=True)
        self.operator = user("ws_operator", self.org_b, staff=True, operator=True)
        self.member_b = user("ws_member_b", self.org_b)
        self.orgless_member = user("ws_member_none", None)

    def _connect(self, user, query="", token=None):
        async def run():
            tok = token or str(AccessToken.for_user(user))
            _ProbeConsumer.joined = []
            communicator = WebsocketCommunicator(
                _ProbeConsumer.as_asgi(), f"/ws/notifications/?token={tok}{query}"
            )
            connected, code = await communicator.connect()
            group = _ProbeConsumer.joined[-1] if connected and _ProbeConsumer.joined else None
            await communicator.disconnect()
            return connected, code, group

        return async_to_sync(run)()

    def _rows(self, action, **filters):
        from core.models import AuditLog

        return list(AuditLog.objects.filter(action=action, **filters).order_by("id"))

    # ── staff and operators may name an org, audited ─────────────────────────────

    def test_staff_naming_another_org_joins_it_and_is_audited(self):
        connected, _code, group = self._connect(self.staff_b, f"&organization_id={self.org_a.id}")
        self.assertTrue(connected)
        self.assertEqual(group, notification_group_for_org(self.org_a.id))
        rows = self._rows("cross_org_access", user=self.staff_b)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].organization_id, self.org_a.id)
        details = json.loads(rows[0].details)
        self.assertEqual((details["action"], details["via"]), ("ws.subscribe", "websocket"))
        self.assertEqual(details["object"], f"ws:{notification_group_for_org(self.org_a.id)}")

    def test_orgless_staff_naming_an_org_joins_it_and_is_audited(self):
        connected, _code, group = self._connect(self.orgless_staff, f"&organization_id={self.org_a.id}")
        self.assertTrue(connected)
        self.assertEqual(group, notification_group_for_org(self.org_a.id))
        self.assertEqual(len(self._rows("cross_org_access", user=self.orgless_staff)), 1)

    def test_staff_naming_an_unknown_org_is_refused(self):
        connected, code, _group = self._connect(self.staff_b, "&organization_id=99999999")
        self.assertFalse(connected)
        self.assertEqual(code, 4403)
        self.assertEqual(self._rows("cross_org_access"), [])

    def test_staff_subscription_is_refused_when_the_audit_row_cannot_be_written(self):
        with mock.patch("core.models.AuditLog.objects.create", side_effect=RuntimeError("audit down")):
            connected, code, _group = self._connect(self.staff_b, f"&organization_id={self.org_a.id}")
        self.assertFalse(connected)
        self.assertEqual(code, 4403)

    def test_platform_operator_may_name_another_org(self):
        connected, _code, group = self._connect(self.operator, f"&organization_id={self.org_a.id}")
        self.assertTrue(connected)
        self.assertEqual(group, notification_group_for_org(self.org_a.id))
        self.assertEqual(len(self._rows("cross_org_access", user=self.operator)), 1)

    def test_staff_without_naming_an_org_joins_own_org_unaudited(self):
        connected, _code, group = self._connect(self.staff_b)
        self.assertTrue(connected)
        self.assertEqual(group, notification_group_for_org(self.org_b.id))
        connected, _code, group = self._connect(self.staff_b, f"&organization_id={self.org_b.id}")
        self.assertEqual(group, notification_group_for_org(self.org_b.id))
        self.assertEqual(self._rows("cross_org_access"), [])

    def test_orgless_staff_without_naming_an_org_is_refused(self):
        # M-08: never default a socket to a tenant.
        connected, code, _group = self._connect(self.orgless_staff)
        self.assertFalse(connected)
        self.assertEqual(code, 4403)

    # ── customers: the parameter is ignored, the attempt audited ──────────────────

    def test_member_naming_another_org_joins_own_org_and_is_audited(self):
        connected, _code, group = self._connect(self.member_b, f"&organization_id={self.org_a.id}")
        self.assertTrue(connected)
        self.assertEqual(group, notification_group_for_org(self.org_b.id))
        self.assertEqual(self._rows("cross_org_access"), [])
        rows = self._rows("cross_org_denied", user=self.member_b)
        self.assertEqual(len(rows), 1)
        self.assertEqual(json.loads(rows[0].details)["requested"], f"organization:{self.org_a.id}")

    def test_member_denied_row_is_the_same_for_a_real_and_an_unknown_org(self):
        self._connect(self.member_b, f"&organization_id={self.org_a.id}")
        self._connect(self.member_b, "&organization_id=99999999")
        shapes = []
        for row in self._rows("cross_org_denied", user=self.member_b):
            d = json.loads(row.details)
            d.pop("requested")
            shapes.append((row.organization_id, d))
        self.assertEqual(len(shapes), 2)
        self.assertEqual(shapes[0], shapes[1])

    def test_orgless_member_naming_an_org_is_refused(self):
        connected, code, _group = self._connect(self.orgless_member, f"&organization_id={self.org_a.id}")
        self.assertFalse(connected)
        self.assertEqual(code, 4403)

    # ── connect-time user checks (A S2 / #131) ────────────────────────────────────

    def test_terminated_user_is_refused(self):
        from auth.models import TerminatedSession

        TerminatedSession.objects.create(user=self.member_b, reason="test")
        connected, code, _group = self._connect(self.member_b)
        self.assertFalse(connected)
        self.assertEqual(code, 4401)

    def test_reinstated_user_connects(self):
        from auth.models import TerminatedSession

        TerminatedSession.objects.create(user=self.member_b, reason="test", cleared_at=timezone.now())
        connected, _code, _group = self._connect(self.member_b)
        self.assertTrue(connected)

    def test_inactive_user_is_refused(self):
        token = str(AccessToken.for_user(self.member_b))
        self.member_b.is_active = False
        self.member_b.save(update_fields=["is_active"])
        connected, code, _group = self._connect(self.member_b, token=token)
        self.assertFalse(connected)
        self.assertEqual(code, 4401)

    def test_token_minted_before_password_change_is_refused(self):
        from auth.models import UserProfile

        token = str(AccessToken.for_user(self.member_b))
        UserProfile.objects.filter(user=self.member_b).update(
            password_changed_at=timezone.now() + timedelta(seconds=5)
        )
        connected, code, _group = self._connect(self.member_b, token=token)
        self.assertFalse(connected)
        self.assertEqual(code, 4401)

    def test_garbage_token_is_refused(self):
        connected, code, _group = self._connect(self.member_b, token="not-a-jwt")
        self.assertFalse(connected)
        self.assertEqual(code, 4401)
