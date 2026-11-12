"""Module 1.6 — Header bell notifications for isolation / kill-switch events.

Notification.recipient_id is a plain IntegerField (policy migration 0006), not a
ForeignKey, so rows are looked up by ``recipient_id``. The original lookup
``recipient=admin`` raised FieldError and the test never ran its assertions (#106).
"""

from __future__ import annotations

from django.test import TestCase


class IsolationNotifyTests(TestCase):
    def test_notify_creates_escalation_notification(self):
        from django.contrib.auth import get_user_model

        from auth.models import Organization, Role, UserProfile
        from core.isolation_notify import notify_isolation_from_control
        from policy.models import Notification

        User = get_user_model()
        org = Organization.objects.create(name="ZS", slug="zeroshield-test")
        other_org = Organization.objects.create(name="Other", slug="zeroshield-other")
        platform_admin, _ = Role.objects.get_or_create(name="platform_admin")

        def _user(username, organization, *, superuser=False, roles=()):
            user = User.objects.create_user(
                username=username, email=f"{username}@example.com", password="x"
            )
            if superuser:
                user.is_superuser = True
                user.save(update_fields=["is_superuser"])
            profile, _ = UserProfile.objects.get_or_create(user=user)
            profile.organization = organization
            profile.save(update_fields=["organization"])
            for role in roles:
                profile.roles.add(role)
            return user

        admin = _user("iso-admin", org, superuser=True)
        org_platform_admin = _user("iso-org-padmin", org, roles=(platform_admin,))
        org_member = _user("iso-member", org)
        other_platform_admin = _user("iso-other-padmin", other_org, roles=(platform_admin,))

        created = notify_isolation_from_control(
            organization=org,
            event_type="kill_switch",
            model_name="gpt-canary",
            action="disable",
            reason="unit-test",
            triggered_by="test",
        )
        assert created >= 1
        rows = Notification.objects.filter(type="escalation", recipient_id=admin.id)
        assert rows.exists()
        assert "[Isolation]" in rows.first().message
        assert "gpt-canary" in rows.first().message
        # The event's org admins are notified ...
        assert Notification.objects.filter(recipient_id=org_platform_admin.id).count() == 1
        # ... a plain member of the org and an admin of ANOTHER org are not.
        assert not Notification.objects.filter(recipient_id=org_member.id).exists()
        assert not Notification.objects.filter(recipient_id=other_platform_admin.id).exists()
        # Every notification points at an event of this org.
        assert not Notification.objects.exclude(enforcement_event__organization=org).exists()
