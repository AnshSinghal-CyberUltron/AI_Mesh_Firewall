"""#120: incident resolve/escalate and escalation bells are scoped to the event's org.

Product rule R11 (auth/models.py UserProfile.is_platform_operator): org admins and
superusers are ORG-scoped; only a platform operator (is_staff AND is_platform_operator)
may act on another org, and only by naming it with ?organization_id. Before #120:
- ResolveIncidentView / EscalateIncidentView skipped the org filter for any superuser,
  so an org B superuser could resolve (silence) or escalate org A's incident;
- the escalation bell went to every superuser of every org (`| Q(is_superuser=True)`).
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from policy.constants import ACTION_BLOCK
from policy.models import ComplianceViolation, EnforcementEvent, Notification
from policy.tests.analytics_api_testcase import AnalyticsAPITestCase

User = get_user_model()


class IncidentOrgScopeTests(AnalyticsAPITestCase):
    # /api/security/ runs inside the analytics-alias transaction (AnalyticsTimeoutMiddleware).

    def setUp(self):
        from auth.models import Organization, Role, UserProfile

        self.org_a = Organization.objects.create(name="Inc Org A", slug="inc-org-a")
        self.org_b = Organization.objects.create(name="Inc Org B", slug="inc-org-b")
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

        self.admin_a = user("inc_admin_a", self.org_a, roles=(padmin,))
        self.member_a = user("inc_member_a", self.org_a)
        self.super_a = user("inc_super_a", self.org_a, superuser=True)
        self.super_b = user("inc_super_b", self.org_b, superuser=True)
        self.padmin_b = user("inc_padmin_b", self.org_b, roles=(padmin,))
        self.orgless_super = user("inc_super_none", None, superuser=True)
        self.operator = user("inc_operator", self.org_b, staff=True, operator=True, roles=(padmin,))

        self.incident = EnforcementEvent.objects.create(
            organization=self.org_a,
            action=ACTION_BLOCK,
            metadata={"threat_category": "Prompt Injection", "model": "org-a-model"},
        )
        self.violation = ComplianceViolation.objects.create(
            enforcement_event=self.incident, framework="SOC2", status="open"
        )
        # Two unread escalation bells already delivered to org A's admin.
        for _ in range(2):
            Notification.objects.create(
                type="escalation", enforcement_event=self.incident,
                recipient_id=self.admin_a.id, message="earlier escalation",
            )

    def _post(self, user, action, query=""):
        client = APIClient()
        client.force_authenticate(user=user)
        return client.post(f"/api/security/incidents/{self.incident.pk}/{action}/{query}")

    def _assert_untouched(self):
        self.incident.refresh_from_db()
        self.violation.refresh_from_db()
        self.assertEqual(self.incident.incident_status, "investigating")
        self.assertIsNone(self.incident.resolved_by_id)
        self.assertIsNone(self.incident.escalated_by_id)
        self.assertEqual(self.violation.status, "open")
        self.assertEqual(
            Notification.objects.filter(recipient_id=self.admin_a.id, read=False).count(), 2
        )
        self.assertEqual(Notification.objects.count(), 2)

    # ── resolve ─────────────────────────────────────────────────────────

    def test_other_org_superuser_cannot_resolve(self):
        resp = self._post(self.super_b, "resolve")
        self.assertEqual(resp.status_code, 404, resp.content)
        self._assert_untouched()

    def test_other_org_superuser_cannot_resolve_by_naming_the_org(self):
        # ?organization_id is honoured for platform operators only.
        resp = self._post(self.super_b, "resolve", f"?organization_id={self.org_a.id}")
        self.assertEqual(resp.status_code, 404, resp.content)
        self._assert_untouched()

    def test_orgless_superuser_cannot_resolve(self):
        resp = self._post(self.orgless_super, "resolve")
        self.assertEqual(resp.status_code, 404, resp.content)
        self._assert_untouched()

    def test_other_org_platform_admin_cannot_resolve(self):
        resp = self._post(self.padmin_b, "resolve")
        self.assertEqual(resp.status_code, 404, resp.content)
        self._assert_untouched()

    def test_same_org_admin_resolves(self):
        resp = self._post(self.admin_a, "resolve")
        self.assertEqual(resp.status_code, 200, resp.content)
        self.incident.refresh_from_db()
        self.violation.refresh_from_db()
        self.assertEqual(self.incident.incident_status, "resolved")
        self.assertEqual(self.incident.resolved_by_id, self.admin_a.id)
        self.assertEqual(self.violation.status, "resolved")
        self.assertFalse(Notification.objects.filter(read=False).exists())

    def test_same_org_superuser_resolves(self):
        resp = self._post(self.super_a, "resolve")
        self.assertEqual(resp.status_code, 200, resp.content)

    def test_platform_operator_resolves_named_org(self):
        from core.models import AuditLog

        resp = self._post(self.operator, "resolve", f"?organization_id={self.org_a.id}")
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertTrue(
            AuditLog.objects.filter(
                user=self.operator, organization=self.org_a, action="cross_org_access"
            ).exists()
        )

    def test_platform_operator_without_naming_org_gets_404(self):
        resp = self._post(self.operator, "resolve")
        self.assertEqual(resp.status_code, 404, resp.content)
        self._assert_untouched()

    # ── escalate ────────────────────────────────────────────────────────

    def test_other_org_superuser_cannot_escalate(self):
        resp = self._post(self.super_b, "escalate")
        self.assertEqual(resp.status_code, 404, resp.content)
        self._assert_untouched()

    def test_orgless_superuser_cannot_escalate(self):
        resp = self._post(self.orgless_super, "escalate")
        self.assertEqual(resp.status_code, 404, resp.content)
        self._assert_untouched()

    def test_escalation_bells_stay_in_the_event_org(self):
        resp = self._post(self.member_a, "escalate")
        self.assertEqual(resp.status_code, 200, resp.content)
        self.incident.refresh_from_db()
        self.assertEqual(self.incident.incident_status, "escalated")
        new = Notification.objects.exclude(message="earlier escalation")
        recipients = set(new.values_list("recipient_id", flat=True))
        # Org A's admins are told ...
        self.assertEqual(recipients, {self.admin_a.id, self.super_a.id})
        # ... and nobody outside org A can read the bell.
        for outsider in (self.super_b, self.orgless_super, self.padmin_b, self.operator):
            client = APIClient()
            client.force_authenticate(user=outsider)
            listed = client.get("/api/notifications/")
            self.assertEqual(listed.status_code, 200)
            self.assertEqual(listed.json(), [], outsider.username)
