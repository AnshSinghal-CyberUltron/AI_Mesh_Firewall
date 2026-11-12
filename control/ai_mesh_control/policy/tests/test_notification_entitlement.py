"""#120 / #122 (Reviewer B B1, Reviewer A S1): bells are readable only inside the event's org.

Develop fanned escalation and isolation bells out to every superuser of every org. The
fan-out fix stops new ones, but rows already written stay, and the list reads the event's
live incident_status, so they would keep reporting another org's incident. These tests
seed such rows directly (as develop left them) and check list, mark-read and mark-all-read,
then the purge command.
"""

from __future__ import annotations

from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from rest_framework.test import APIClient

from policy.constants import ACTION_BLOCK
from policy.models import EnforcementEvent, Notification

User = get_user_model()


class NotificationEntitlementTests(TestCase):
    def setUp(self):
        from auth.models import Organization, UserProfile

        self.org_a = Organization.objects.create(name="Bell A", slug="bell-a")
        self.org_b = Organization.objects.create(name="Bell B", slug="bell-b")

        def user(name, org, *, superuser=False):
            u = User.objects.create_user(
                username=name, email=f"{name}@example.com", password="pw",
                is_superuser=superuser, is_staff=superuser,
            )
            profile, _ = UserProfile.objects.get_or_create(user=u)
            profile.organization = org
            profile.save(update_fields=["organization"])
            return u

        self.admin_a = user("bell_admin_a", self.org_a)
        self.staff_b = user("bell_staff_b", self.org_b, superuser=True)
        self.orgless_staff = user("bell_staff_none", None, superuser=True)
        self.ev_a = EnforcementEvent.objects.create(
            organization=self.org_a, action=ACTION_BLOCK, metadata={"threat_category": "ORG-A-CAT"}
        )
        self.ev_b = EnforcementEvent.objects.create(
            organization=self.org_b, action=ACTION_BLOCK, metadata={"threat_category": "ORG-B-CAT"}
        )
        self.ev_none = EnforcementEvent.objects.create(organization=None, action=ACTION_BLOCK, metadata={})
        # Rows as develop left them: org A's bells delivered to staff of org B and to org-less staff.
        self.legacy_b = Notification.objects.create(
            type="escalation", enforcement_event=self.ev_a, recipient_id=self.staff_b.id, message="org A leak"
        )
        self.legacy_none = Notification.objects.create(
            type="escalation", enforcement_event=self.ev_a, recipient_id=self.orgless_staff.id, message="org A leak"
        )
        self.own_b = Notification.objects.create(
            type="escalation", enforcement_event=self.ev_b, recipient_id=self.staff_b.id, message="org B own"
        )
        self.own_a = Notification.objects.create(
            type="escalation", enforcement_event=self.ev_a, recipient_id=self.admin_a.id, message="org A own"
        )
        self.no_org_event = Notification.objects.create(
            type="escalation", enforcement_event=self.ev_none, recipient_id=self.staff_b.id, message="no org"
        )
        self.gone_user = Notification.objects.create(
            type="escalation", enforcement_event=self.ev_a, recipient_id=987654321, message="deleted user"
        )

    def _client(self, user):
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    def test_list_shows_only_own_org_bells(self):
        items = self._client(self.staff_b).get("/api/notifications/?limit=200").json()
        self.assertEqual([i["id"] for i in items], [self.own_b.id])
        self.assertEqual(self._client(self.orgless_staff).get("/api/notifications/").json(), [])
        items = self._client(self.admin_a).get("/api/notifications/").json()
        self.assertEqual([i["id"] for i in items], [self.own_a.id])

    def test_legacy_bell_no_longer_tracks_the_other_orgs_status(self):
        self.ev_a.incident_status = "resolved"
        self.ev_a.save(update_fields=["incident_status"])
        body = self._client(self.staff_b).get("/api/notifications/").content
        self.assertNotIn(b"ORG-A-CAT", body)
        self.assertNotIn(b"org A leak", body)

    def test_user_who_moved_org_loses_the_old_orgs_bells(self):
        from auth.models import UserProfile

        UserProfile.objects.filter(user=self.admin_a).update(organization=self.org_b)
        moved = User.objects.get(pk=self.admin_a.pk)  # a request loads the user afresh
        self.assertEqual(self._client(moved).get("/api/notifications/").json(), [])

    def test_mark_read_by_id_is_404_for_a_bell_outside_the_org(self):
        resp = self._client(self.staff_b).post(f"/api/notifications/{self.legacy_b.id}/read/")
        self.assertEqual(resp.status_code, 404)
        missing = self._client(self.staff_b).post("/api/notifications/99999999/read/")
        self.assertEqual((resp.status_code, resp.content), (missing.status_code, missing.content))
        self.legacy_b.refresh_from_db()
        self.assertFalse(self.legacy_b.read)
        self.assertEqual(self._client(self.staff_b).post(f"/api/notifications/{self.own_b.id}/read/").status_code, 200)

    def test_mark_all_read_touches_only_entitled_bells(self):
        self.assertEqual(self._client(self.staff_b).post("/api/notifications/mark-all-read/").status_code, 200)
        read = set(Notification.objects.filter(read=True).values_list("id", flat=True))
        self.assertEqual(read, {self.own_b.id})

    def test_limit_parameter_is_robust(self):
        client = self._client(self.staff_b)
        for value, expected in (("abc", 200), ("-1", 200), ("0", 200), ("100000", 200)):
            resp = client.get(f"/api/notifications/?limit={value}")
            self.assertEqual(resp.status_code, expected, (value, resp.content))
        self.assertEqual(len(client.get("/api/notifications/?limit=-5").json()), 1)

    def test_purge_command_dry_run_then_apply(self):
        out = StringIO()
        call_command("purge_cross_org_notifications", stdout=out)
        self.assertIn("cross-org notifications: 4", out.getvalue())
        self.assertIn("dry run", out.getvalue())
        self.assertEqual(Notification.objects.count(), 6)

        out = StringIO()
        call_command("purge_cross_org_notifications", "--apply", "--batch-size", "1", stdout=out)
        self.assertIn("deleted: 4", out.getvalue())
        self.assertEqual(
            set(Notification.objects.values_list("id", flat=True)), {self.own_a.id, self.own_b.id}
        )
        out = StringIO()
        call_command("purge_cross_org_notifications", "--apply", stdout=out)
        self.assertIn("deleted: 0", out.getvalue())
