"""#120 / #122: who may resolve or escalate an incident, and what is audited.

Owner decision (12 Nov 2026): "superuser = ZeroShield staff". Staff keep develop's reach
into any org's incident, and every use outside their own org writes a
``cross_org_access`` AuditLog row (actor, target org, action, object) BEFORE the change.
Customers (org admins, members) stay in their own org: another org's incident is a 404,
the same as a missing one, and the attempt writes a ``cross_org_denied`` row that holds
only the requested id. A platform operator (is_staff + profile flag, #139) still names
the org with ?organization_id. Escalation bells reach the event org's admins only.
"""

from __future__ import annotations

import json
from unittest import mock

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
        self.staff_b = user("inc_staff_b", self.org_b, superuser=True)
        self.padmin_b = user("inc_padmin_b", self.org_b, roles=(padmin,))
        self.member_b = user("inc_member_b", self.org_b)
        self.orgless_staff = user("inc_staff_none", None, superuser=True)
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

    def _post(self, user, action, query="", pk=None):
        client = APIClient()
        client.force_authenticate(user=user)
        return client.post(f"/api/security/incidents/{pk or self.incident.pk}/{action}/{query}")

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

    def _rows(self, action, **filters):
        from core.models import AuditLog

        return list(AuditLog.objects.filter(action=action, **filters).order_by("id"))

    def _assert_access_row(self, actor, action):
        rows = self._rows("cross_org_access", user=actor)
        self.assertEqual(len(rows), 1, [r.details for r in rows])
        row = rows[0]
        self.assertEqual(row.organization_id, self.org_a.id)
        self.assertEqual(row.resource, f"enforcement_event:{self.incident.pk}")
        details = json.loads(row.details)
        self.assertEqual(details["action"], action)
        self.assertEqual(details["target_org_id"], self.org_a.id)
        self.assertEqual(details["actor_id"], actor.id)
        self.assertEqual(details["object"], f"enforcement_event:{self.incident.pk}")
        return details

    # ── staff (superuser) keep cross-org reach, audited ────────────────────────────

    def test_staff_of_another_org_resolves_and_is_audited(self):
        resp = self._post(self.staff_b, "resolve")
        self.assertEqual(resp.status_code, 200, resp.content)
        self.incident.refresh_from_db()
        self.violation.refresh_from_db()
        self.assertEqual(self.incident.incident_status, "resolved")
        self.assertEqual(self.incident.resolved_by_id, self.staff_b.id)
        self.assertEqual(self.violation.status, "resolved")
        details = self._assert_access_row(self.staff_b, "incident.resolve")
        self.assertEqual(details["actor_org_id"], self.org_b.id)
        self.assertEqual(self._rows("cross_org_denied"), [])

    def test_orgless_staff_resolves_and_is_audited(self):
        resp = self._post(self.orgless_staff, "resolve")
        self.assertEqual(resp.status_code, 200, resp.content)
        details = self._assert_access_row(self.orgless_staff, "incident.resolve")
        self.assertIsNone(details["actor_org_id"])

    def test_staff_of_another_org_escalates_and_is_audited(self):
        resp = self._post(self.staff_b, "escalate")
        self.assertEqual(resp.status_code, 200, resp.content)
        self.incident.refresh_from_db()
        self.assertEqual(self.incident.escalated_by_id, self.staff_b.id)
        self._assert_access_row(self.staff_b, "incident.escalate")
        # The bell still goes to the event org's admins only, not to the staff member's org.
        new = Notification.objects.exclude(message="earlier escalation")
        self.assertEqual(set(new.values_list("recipient_id", flat=True)), {self.admin_a.id, self.super_a.id})

    def test_staff_action_is_not_taken_when_the_audit_row_cannot_be_written(self):
        with mock.patch("core.models.AuditLog.objects.create", side_effect=RuntimeError("audit down")):
            resp = self._post(self.staff_b, "resolve")
        self.assertEqual(resp.status_code, 500, resp.content)
        self._assert_untouched()

    def test_same_org_staff_writes_no_cross_org_row(self):
        self.assertEqual(self._post(self.super_a, "resolve").status_code, 200)
        self.assertEqual(self._rows("cross_org_access"), [])

    def test_staff_gets_404_for_a_missing_incident(self):
        self.assertEqual(self._post(self.staff_b, "resolve", pk=99999999).status_code, 404)
        self.assertEqual(self._rows("cross_org_access"), [])

    # ── customers stay in their org; denied attempts are audited ──────────────────

    def test_other_org_admin_cannot_resolve_and_is_audited_as_denied(self):
        resp = self._post(self.padmin_b, "resolve")
        self.assertEqual(resp.status_code, 404, resp.content)
        self._assert_untouched()
        rows = self._rows("cross_org_denied")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].user_id, self.padmin_b.id)
        self.assertEqual(rows[0].organization_id, self.org_b.id)
        details = json.loads(rows[0].details)
        self.assertEqual(details["requested"], f"enforcement_event:{self.incident.pk}")
        self.assertNotIn("target_org_id", details)

    def test_other_org_admin_naming_the_org_cannot_resolve(self):
        # ?organization_id is honoured for platform operators only.
        resp = self._post(self.padmin_b, "resolve", f"?organization_id={self.org_a.id}")
        self.assertEqual(resp.status_code, 404, resp.content)
        self._assert_untouched()

    def test_other_org_member_cannot_escalate(self):
        resp = self._post(self.member_b, "escalate")
        self.assertEqual(resp.status_code, 404, resp.content)
        self._assert_untouched()
        self.assertEqual(len(self._rows("cross_org_denied", user=self.member_b)), 1)

    def test_denied_row_is_the_same_for_another_orgs_incident_and_a_missing_one(self):
        other = self._post(self.padmin_b, "resolve")
        missing = self._post(self.padmin_b, "resolve", pk=99999999)
        self.assertEqual((other.status_code, other.content), (404, missing.content))
        rows = self._rows("cross_org_denied")
        self.assertEqual(len(rows), 2)

        def shape(row):
            d = json.loads(row.details)
            d.pop("requested")
            d.pop("path")
            return row.organization_id, row.user_id, d

        self.assertEqual(shape(rows[0]), shape(rows[1]))

    def test_same_org_admin_resolves(self):
        resp = self._post(self.admin_a, "resolve")
        self.assertEqual(resp.status_code, 200, resp.content)
        self.incident.refresh_from_db()
        self.violation.refresh_from_db()
        self.assertEqual(self.incident.incident_status, "resolved")
        self.assertEqual(self.incident.resolved_by_id, self.admin_a.id)
        self.assertEqual(self.violation.status, "resolved")
        self.assertFalse(Notification.objects.filter(read=False).exists())
        self.assertEqual(self._rows("cross_org_access") + self._rows("cross_org_denied"), [])

    # ── platform operators (#139 semantics unchanged) ─────────────────────────────

    def test_platform_operator_resolves_named_org(self):
        resp = self._post(self.operator, "resolve", f"?organization_id={self.org_a.id}")
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertTrue(
            self._rows("cross_org_access", user=self.operator, organization=self.org_a)
        )

    def test_platform_operator_without_naming_org_gets_404(self):
        resp = self._post(self.operator, "resolve")
        self.assertEqual(resp.status_code, 404, resp.content)
        self._assert_untouched()

    # ── bells ─────────────────────────────────────────────────────────────────────

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
        for outsider in (self.staff_b, self.orgless_staff, self.padmin_b, self.operator):
            client = APIClient()
            client.force_authenticate(user=outsider)
            listed = client.get("/api/notifications/")
            self.assertEqual(listed.status_code, 200)
            self.assertEqual(listed.json(), [], outsider.username)
