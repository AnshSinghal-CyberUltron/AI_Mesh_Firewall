"""#120 audit: session terminate/reinstate and user management stay inside the caller's org.

Before #120:
- SessionTerminateView / SessionReinstateView skipped the org check for any superuser
  (`... and not request.user.is_superuser`) and for any staff user with no org
  (`req_org is not None and ...`), so an org B superuser could lock org A's users out.
- UserManagementDetailView only compared orgs when the caller had one, and the list view
  returned every tenant's users to a superuser without an org.
R11 (auth/models.py): superusers and org admins are ORG-scoped; only a platform
operator crosses orgs, by naming the org with ?organization_id.
"""

from __future__ import annotations

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
        self.staff_a = user("adm_staff_a", self.org_a, staff=True, roles=(self.padmin,))
        self.super_a = user("adm_super_a", self.org_a, superuser=True)
        self.super_b = user("adm_super_b", self.org_b, superuser=True)
        self.orgless_super = user("adm_super_none", None, superuser=True)
        self.orgless_staff = user("adm_staff_none", None, staff=True, roles=(self.padmin,))
        self.operator = user("adm_operator", self.org_b, staff=True, operator=True)

    def _client(self, user):
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    # ── sessions ────────────────────────────────────────────────────────

    def _terminate(self, actor, query=""):
        return self._client(actor).post(
            f"/api/auth/sessions/terminate/{query}", {"user_id": self.member_a.id}, format="json"
        )

    def test_other_org_superuser_cannot_terminate(self):
        from auth.models import TerminatedSession

        resp = self._terminate(self.super_b)
        self.assertEqual(resp.status_code, 404, resp.content)
        self.assertFalse(TerminatedSession.objects.filter(user=self.member_a).exists())

    def test_orgless_admin_cannot_terminate(self):
        from auth.models import TerminatedSession

        for actor in (self.orgless_super, self.orgless_staff):
            resp = self._terminate(actor)
            self.assertEqual(resp.status_code, 404, (actor.username, resp.content))
        self.assertFalse(TerminatedSession.objects.filter(user=self.member_a).exists())

    def test_same_org_staff_terminates(self):
        resp = self._terminate(self.staff_a)
        self.assertEqual(resp.status_code, 200, resp.content)

    def test_platform_operator_terminates_in_named_org(self):
        resp = self._terminate(self.operator, f"?organization_id={self.org_a.id}")
        self.assertEqual(resp.status_code, 200, resp.content)

    def test_other_org_superuser_cannot_reinstate(self):
        from auth.models import TerminatedSession

        TerminatedSession.objects.create(user=self.member_a, reason="r", terminated_by=self.staff_a)
        for actor in (self.super_b, self.orgless_super):
            resp = self._client(actor).post(
                "/api/auth/sessions/reinstate/", {"user_id": self.member_a.id}, format="json"
            )
            self.assertEqual(resp.status_code, 404, (actor.username, resp.content))
        self.assertTrue(
            TerminatedSession.objects.filter(user=self.member_a, cleared_at__isnull=True).exists()
        )

    # ── user management ─────────────────────────────────────────────────

    def test_orgless_admin_cannot_read_update_or_delete_other_org_user(self):
        url = f"/api/auth/users/{self.member_a.id}/?offering=platform"
        for actor in (self.orgless_super, self.orgless_staff):
            client = self._client(actor)
            self.assertEqual(client.get(url).status_code, 404, actor.username)
            resp = client.patch(url, {"offering": "platform", "is_active": False}, format="json")
            self.assertEqual(resp.status_code, 404, (actor.username, resp.content))
            self.assertEqual(client.delete(url).status_code, 404, actor.username)
        self.member_a.refresh_from_db()
        self.assertTrue(self.member_a.is_active)

    def test_other_org_superuser_cannot_read_or_delete_user(self):
        url = f"/api/auth/users/{self.member_a.id}/?offering=platform"
        client = self._client(self.super_b)
        self.assertEqual(client.get(url).status_code, 404)
        self.assertEqual(client.delete(url).status_code, 404)
        self.assertTrue(User.objects.filter(pk=self.member_a.pk).exists())

    def test_same_org_superuser_reads_user(self):
        url = f"/api/auth/users/{self.member_a.id}/?offering=platform"
        self.assertEqual(self._client(self.super_a).get(url).status_code, 200)

    def test_orgless_superuser_lists_no_users(self):
        resp = self._client(self.orgless_super).get("/api/auth/users/?offering=platform")
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json(), [])

    def test_same_org_superuser_lists_only_own_org(self):
        resp = self._client(self.super_a).get("/api/auth/users/?offering=platform")
        self.assertEqual(resp.status_code, 200, resp.content)
        emails = {row["email"] for row in resp.json()}
        self.assertIn(self.member_a.email, emails)
        self.assertNotIn(self.super_b.email, emails)
        self.assertNotIn(self.orgless_super.email, emails)
