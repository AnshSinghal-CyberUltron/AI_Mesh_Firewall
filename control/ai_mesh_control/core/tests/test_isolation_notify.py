"""Module 1.6 — Header bell notifications for isolation / kill-switch events."""

from __future__ import annotations

from django.test import TestCase


class IsolationNotifyTests(TestCase):
    def test_notify_creates_escalation_notification(self):
        from django.contrib.auth import get_user_model

        from auth.models import Organization, UserProfile
        from core.isolation_notify import notify_isolation_from_control
        from policy.models import Notification

        User = get_user_model()
        org = Organization.objects.create(name="ZS", slug="zeroshield-test")
        admin = User.objects.create_user(
            username="iso-admin", email="iso-admin@example.com", password="x"
        )
        admin.is_superuser = True
        admin.save()
        UserProfile.objects.get_or_create(user=admin, defaults={"organization": org})
        profile = admin.profile
        profile.organization = org
        profile.save(update_fields=["organization"])

        created = notify_isolation_from_control(
            organization=org,
            event_type="kill_switch",
            model_name="gpt-canary",
            action="disable",
            reason="unit-test",
            triggered_by="test",
        )
        assert created >= 1
        rows = Notification.objects.filter(type="escalation", recipient=admin)
        assert rows.exists()
        assert "[Isolation]" in rows.first().message
        assert "gpt-canary" in rows.first().message
