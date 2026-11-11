"""#120: Module 1.6 isolation bells reach only the event org's admins.

Before #120 the recipient query added `| Q(is_superuser=True)`, so every superuser of
every org (and one with no org) received "[Isolation] ... model=<other org's model>".
R11 (auth/models.py): superusers are ORG-scoped.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase

User = get_user_model()


class IsolationNotifyOrgScopeTests(TestCase):
    def setUp(self):
        from auth.models import Organization, Role, UserProfile

        self.org_a = Organization.objects.create(name="Iso A", slug="iso-scope-a")
        self.org_b = Organization.objects.create(name="Iso B", slug="iso-scope-b")
        padmin, _ = Role.objects.get_or_create(name="platform_admin")

        def user(name, org, *, superuser=False, staff=False, operator=False, roles=()):
            u = User.objects.create_user(
                username=name, email=f"{name}@example.com", password="pw",
                is_superuser=superuser, is_staff=staff or superuser,
            )
            profile, _ = UserProfile.objects.get_or_create(user=u)
            profile.organization = org
            profile.is_platform_operator = operator
            profile.save(update_fields=["organization", "is_platform_operator"])
            for role in roles:
                profile.roles.add(role)
            return u

        self.super_a = user("iso_super_a", self.org_a, superuser=True)
        self.padmin_a = user("iso_padmin_a", self.org_a, roles=(padmin,))
        self.member_a = user("iso_member_a", self.org_a)
        self.super_b = user("iso_super_b", self.org_b, superuser=True)
        self.padmin_b = user("iso_padmin_b", self.org_b, roles=(padmin,))
        self.orgless_super = user("iso_super_none", None, superuser=True)
        self.operator = user("iso_operator", self.org_b, staff=True, operator=True)

    def test_isolation_bell_recipients_are_the_event_org_admins(self):
        from core.isolation_notify import notify_isolation_from_control
        from policy.models import Notification

        created = notify_isolation_from_control(
            organization=self.org_a,
            event_type="kill_switch",
            model_name="org-a-secret-model",
            action="disable",
            reason="unit-test",
            triggered_by="test",
        )
        recipients = set(Notification.objects.values_list("recipient_id", flat=True))
        self.assertEqual(recipients, {self.super_a.id, self.padmin_a.id})
        self.assertEqual(created, 2)

    def test_drained_event_without_org_notifies_nobody(self):
        from core.isolation_notify import create_isolation_bell_notifications_for_events
        from policy.constants import ACTION_BLOCK
        from policy.models import EnforcementEvent, Notification

        ev = EnforcementEvent.objects.create(
            organization=None,
            action=ACTION_BLOCK,
            metadata={"event_type": "kill_switch", "is_isolation_event": True, "model": "m"},
        )
        self.assertEqual(create_isolation_bell_notifications_for_events([ev]), 0)
        self.assertFalse(Notification.objects.exists())
