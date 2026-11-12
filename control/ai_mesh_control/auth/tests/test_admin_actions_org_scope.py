"""#120 / #122: session terminate/reinstate and user management, by caller.

Owner decision (12 Nov 2026): "superuser = ZeroShield staff".
- Staff terminate or reinstate a session of a user in any org (the documented staff-only
  endpoint), and each use outside their own org writes ``cross_org_access`` first.
- Any other caller of these IsAdminUser endpoints (is_staff without superuser) reaches only
  users in get_request_organization's org; with no org, nobody (develop let an org-less
  staff user act on every tenant). Misses write ``cross_org_denied``.
- User management: an org-less staff member keeps develop's fleet READ (list and detail),
  audited; writes never fall through to another tenant, for staff too. A staff member
  WITH an org sees their own org, as on develop.
"""

from __future__ import annotations

import json

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

User = get_user_model()


class AdminActionsOrgScopeTests(TestCase):
    def setUp(self):
        from auth.models import Organization, Role, UserProfile

        self.org_a = Organization.objects.create(name="Adm A", slug="adm-scope-a")
        self.org_b = Organization.objects.create(name="Adm B", slug="adm-scope-b")
        self.padmin, _ = Role.objects.get_or_create(name="platform_admin")
        platform_user, _ = Role.objects.get_or_create(name="platform_user")

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

        self.member_a = user("adm_member_a", self.org_a, roles=(platform_user,))
        self.padmin_a = user("adm_padmin_a", self.org_a, roles=(self.padmin,))
        self.staff_a = user("adm_staff_a", self.org_a, staff=True, roles=(self.padmin,))
        self.super_a = user("adm_super_a", self.org_a, superuser=True)
        self.super_b = user("adm_super_b", self.org_b, superuser=True)
        self.padmin_b = user("adm_padmin_b", self.org_b, roles=(self.padmin,))
        self.orgless_super = user("adm_super_none", None, superuser=True)
        self.orgless_staff = user("adm_staff_none", None, staff=True, roles=(self.padmin,))
        self.staff_b = user("adm_staff_b", self.org_b, staff=True, roles=(self.padmin,))
        self.operator = user("adm_operator", self.org_b, staff=True, operator=True)

    def _client(self, user):
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    def _rows(self, action, **filters):
        from core.models import AuditLog

        return list(AuditLog.objects.filter(action=action, **filters).order_by("id"))

    def _active_terminations(self, user):
        from auth.models import TerminatedSession

        return TerminatedSession.objects.filter(user=user, cleared_at__isnull=True).count()

    # ── sessions: staff keep cross-org reach, audited ─────────────────────────────

    def _terminate(self, actor, query="", user_id=None):
        return self._client(actor).post(
            f"/api/auth/sessions/terminate/{query}",
            {"user_id": self.member_a.id if user_id is None else user_id}, format="json",
        )

    def test_staff_of_another_org_terminates_and_is_audited(self):
        resp = self._terminate(self.super_b)
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(self._active_terminations(self.member_a), 1)
        rows = self._rows("cross_org_access", user=self.super_b)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].organization_id, self.org_a.id)
        self.assertEqual(rows[0].resource, f"user:{self.member_a.id}")
        details = json.loads(rows[0].details)
        self.assertEqual(
            (details["action"], details["target_org_id"], details["actor_org_id"]),
            ("session.terminate", self.org_a.id, self.org_b.id),
        )

    def test_orgless_staff_terminates_and_reinstates_audited(self):
        self.assertEqual(self._terminate(self.orgless_super).status_code, 200)
        resp = self._client(self.orgless_super).post(
            "/api/auth/sessions/reinstate/", {"user_id": self.member_a.id}, format="json"
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(self._active_terminations(self.member_a), 0)
        actions = [json.loads(r.details)["action"] for r in self._rows("cross_org_access", user=self.orgless_super)]
        self.assertEqual(actions, ["session.terminate", "session.reinstate"])

    def test_staff_of_another_org_reinstates_and_is_audited(self):
        from auth.models import TerminatedSession

        TerminatedSession.objects.create(user=self.member_a, reason="r", terminated_by=self.staff_a)
        resp = self._client(self.super_b).post(
            "/api/auth/sessions/reinstate/", {"user_id": self.member_a.id}, format="json"
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(self._active_terminations(self.member_a), 0)
        self.assertEqual(len(self._rows("cross_org_access", user=self.super_b)), 1)

    def test_same_org_staff_writes_no_cross_org_row(self):
        self.assertEqual(self._terminate(self.super_a).status_code, 200)
        self.assertEqual(self._rows("cross_org_access"), [])

    # ── sessions: non-superuser staff stay in their org ───────────────────────────

    def test_other_org_non_superuser_staff_cannot_terminate_and_is_audited(self):
        resp = self._terminate(self.staff_b)
        self.assertEqual(resp.status_code, 404, resp.content)
        self.assertEqual(self._active_terminations(self.member_a), 0)
        rows = self._rows("cross_org_denied", user=self.staff_b)
        self.assertEqual(len(rows), 1)
        self.assertEqual(json.loads(rows[0].details)["requested"], f"user:{self.member_a.id}")

    def test_orgless_non_superuser_staff_cannot_terminate(self):
        resp = self._terminate(self.orgless_staff)
        self.assertEqual(resp.status_code, 404, resp.content)
        self.assertEqual(self._active_terminations(self.member_a), 0)

    def test_other_org_non_superuser_staff_cannot_reinstate(self):
        from auth.models import TerminatedSession

        TerminatedSession.objects.create(user=self.member_a, reason="r", terminated_by=self.staff_a)
        for actor in (self.staff_b, self.orgless_staff):
            resp = self._client(actor).post(
                "/api/auth/sessions/reinstate/", {"user_id": self.member_a.id}, format="json"
            )
            self.assertEqual(resp.status_code, 404, (actor.username, resp.content))
        self.assertEqual(self._active_terminations(self.member_a), 1)

    def test_denied_response_and_row_are_the_same_for_another_orgs_user_and_a_missing_one(self):
        other = self._terminate(self.staff_b)
        missing = self._terminate(self.staff_b, user_id=99999999)
        self.assertEqual((other.status_code, other.content), (missing.status_code, missing.content))
        rows = self._rows("cross_org_denied", user=self.staff_b)
        shapes = []
        for row in rows:
            d = json.loads(row.details)
            d.pop("requested")
            shapes.append((row.organization_id, d))
        self.assertEqual(len(shapes), 2)
        self.assertEqual(shapes[0], shapes[1])

    def test_customer_org_admin_cannot_use_session_endpoints(self):
        # IsAdminUser: customers are never is_staff, so they get 403 before any lookup.
        self.assertEqual(self._terminate(self.padmin_b).status_code, 403)
        self.assertEqual(self._terminate(self.padmin_a).status_code, 403)
        self.assertEqual(self._active_terminations(self.member_a), 0)

    def test_same_org_staff_terminates(self):
        self.assertEqual(self._terminate(self.staff_a).status_code, 200)

    def test_platform_operator_terminates_in_named_org(self):
        resp = self._terminate(self.operator, f"?organization_id={self.org_a.id}")
        self.assertEqual(resp.status_code, 200, resp.content)

    def test_malformed_user_id_is_400(self):
        for bad in ("abc", "1.5", "", None):
            body = {} if bad is None else {"user_id": bad}
            resp = self._client(self.super_a).post("/api/auth/sessions/terminate/", body, format="json")
            self.assertEqual(resp.status_code, 400, (bad, resp.content))

    # ── user management ─────────────────────────────────────────────────────────

    def test_orgless_staff_lists_every_tenants_users_audited(self):
        resp = self._client(self.orgless_super).get("/api/auth/users/?offering=platform")
        self.assertEqual(resp.status_code, 200, resp.content)
        emails = {row["email"] for row in resp.json()}
        self.assertIn(self.member_a.email, emails)
        self.assertIn(self.padmin_b.email, emails)
        rows = self._rows("cross_org_access", user=self.orgless_super)
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0].organization_id)
        details = json.loads(rows[0].details)
        self.assertEqual((details["scope"], details["action"]), ("all", "fleet_read.auth.users"))

    def test_orgless_staff_reads_any_user_but_cannot_update_or_delete(self):
        url = f"/api/auth/users/{self.member_a.id}/?offering=platform"
        client = self._client(self.orgless_super)
        self.assertEqual(client.get(url).status_code, 200)
        self.assertEqual(len(self._rows("cross_org_access", user=self.orgless_super)), 1)
        resp = client.patch(url, {"offering": "platform", "is_active": False}, format="json")
        self.assertEqual(resp.status_code, 404, resp.content)
        self.assertEqual(client.delete(url).status_code, 404)
        self.member_a.refresh_from_db()
        self.assertTrue(self.member_a.is_active)
        # The refused writes did not add fleet rows.
        self.assertEqual(len(self._rows("cross_org_access", user=self.orgless_super)), 1)

    def test_orgless_non_superuser_admin_cannot_read_update_or_delete(self):
        url = f"/api/auth/users/{self.member_a.id}/?offering=platform"
        client = self._client(self.orgless_staff)
        self.assertEqual(client.get(url).status_code, 404)
        resp = client.patch(url, {"offering": "platform", "is_active": False}, format="json")
        self.assertEqual(resp.status_code, 404, resp.content)
        self.assertEqual(client.delete(url).status_code, 404)
        self.member_a.refresh_from_db()
        self.assertTrue(self.member_a.is_active)
        resp = client.get("/api/auth/users/?offering=platform")
        self.assertEqual(resp.json(), [])

    def test_other_org_admin_cannot_read_or_delete_user(self):
        url = f"/api/auth/users/{self.member_a.id}/?offering=platform"
        for actor in (self.padmin_b, self.super_b):
            client = self._client(actor)
            self.assertEqual(client.get(url).status_code, 404, actor.username)
            self.assertEqual(client.delete(url).status_code, 404, actor.username)
        self.assertTrue(User.objects.filter(pk=self.member_a.pk).exists())

    def test_same_org_staff_reads_user(self):
        url = f"/api/auth/users/{self.member_a.id}/?offering=platform"
        self.assertEqual(self._client(self.super_a).get(url).status_code, 200)
        self.assertEqual(self._rows("cross_org_access"), [])

    def test_staff_with_an_org_lists_only_own_org(self):
        resp = self._client(self.super_a).get("/api/auth/users/?offering=platform")
        self.assertEqual(resp.status_code, 200, resp.content)
        emails = {row["email"] for row in resp.json()}
        self.assertIn(self.member_a.email, emails)
        self.assertNotIn(self.super_b.email, emails)
        self.assertNotIn(self.orgless_super.email, emails)
