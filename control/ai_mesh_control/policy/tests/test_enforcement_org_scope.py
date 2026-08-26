"""T-C8: fail-closed org scope on _enforcement_events_for_request (Phase 0a / X-1)."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from rest_framework.test import APIClient

from policy.constants import ACTION_BLOCK
from policy.models import EnforcementEvent
from policy.security_views import _enforcement_events_for_request

User = get_user_model()


class EnforcementOrgScopeTests(TestCase):
    def setUp(self):
        from auth.models import Organization, UserProfile

        self.org_a = Organization.objects.create(name="Scope A", slug="scope-a")
        self.org_b = Organization.objects.create(name="Scope B", slug="scope-b")
        self.factory = RequestFactory()

        self.member = User.objects.create_user(username="scope_member", password="pw")
        profile, _ = UserProfile.objects.get_or_create(user=self.member)
        profile.organization = self.org_a
        profile.save(update_fields=["organization"])

        self.super_no_org = User.objects.create_superuser(
            username="scope_super", password="pw", email="scope-super@example.com"
        )
        su_profile, _ = UserProfile.objects.get_or_create(user=self.super_no_org)
        su_profile.organization = None
        su_profile.save(update_fields=["organization"])

        self.canary = EnforcementEvent.objects.create(
            organization=self.org_b,
            action=ACTION_BLOCK,
            metadata={"canary": "foreign-tenant-secret"},
        )
        self.own = EnforcementEvent.objects.create(
            organization=self.org_a,
            action=ACTION_BLOCK,
            metadata={"canary": "own-tenant"},
        )

    def _request(self, user):
        req = self.factory.get("/api/security/soc-kpis/")
        req.user = user
        return req

    def test_member_sees_only_own_org(self):
        qs = _enforcement_events_for_request(self._request(self.member))
        ids = set(qs.values_list("id", flat=True))
        self.assertIn(self.own.id, ids)
        self.assertNotIn(self.canary.id, ids)

    def test_superuser_with_no_org_sees_zero_rows(self):
        qs = _enforcement_events_for_request(self._request(self.super_no_org))
        self.assertEqual(list(qs), [])
        self.assertNotIn(self.canary.id, qs.values_list("id", flat=True))

    def test_unauthenticated_sees_zero_rows(self):
        from django.contrib.auth.models import AnonymousUser

        req = self.factory.get("/api/security/soc-kpis/")
        req.user = AnonymousUser()
        qs = _enforcement_events_for_request(req)
        self.assertEqual(list(qs), [])

    def test_soc_kpis_api_hides_foreign_canary_for_superuser_no_org(self):
        client = APIClient()
        client.force_authenticate(user=self.super_no_org)
        resp = client.get("/api/security/soc-kpis/?period=24h")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        blob = str(body)
        self.assertNotIn("foreign-tenant-secret", blob)
        self.assertEqual(body.get("total_threats", 0), 0)
