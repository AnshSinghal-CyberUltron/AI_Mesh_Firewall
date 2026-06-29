"""Policy compile view: org-membership assertion (defense-in-depth).

PolicyCompileView compiles and pushes the bundle for whatever org
get_request_organization resolves. The view now asserts that a non-superuser
actually belongs to that org before compiling, so a future change to org
resolution (header/body-derived org, agent-key context, ...) can never let an
admin of org A compile and push org B's bundle.
"""

from __future__ import annotations

from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

User = get_user_model()

COMPILED_BUNDLE = {
    "version": 7,
    "policy_count": 0,
    "rule_count": 0,
    "compiled_at": 1718000000.0,
    "policies": [],
}


class PolicyCompileOrgAssertionTests(TestCase):
    def setUp(self):
        from auth.models import Organization, UserProfile

        self.org_a = Organization.objects.create(name="Org A", slug="compile-org-a")
        self.org_b = Organization.objects.create(name="Org B", slug="compile-org-b")

        # Staff (admin) member of org A.
        self.staff_a = User.objects.create_user(
            username="compile_staff_a", password="pw", is_staff=True
        )
        profile, _ = UserProfile.objects.get_or_create(user=self.staff_a)
        profile.organization = self.org_a
        profile.save(update_fields=["organization"])

        self.superuser = User.objects.create_superuser(
            username="compile_super", password="pw", email="su@example.com"
        )

    def _mock_compiler(self):
        patcher = mock.patch("policy.compile_views.PolicyCompiler")
        mocked = patcher.start()
        self.addCleanup(patcher.stop)
        instance = mocked.return_value
        instance.compile_all.return_value = dict(COMPILED_BUNDLE)
        instance.push_to_redis.return_value = True
        return instance

    def test_member_can_compile_own_org(self):
        compiler = self._mock_compiler()
        client = APIClient()
        client.force_authenticate(user=self.staff_a)
        resp = client.post("/api/policies/compile/")
        self.assertEqual(resp.status_code, 200, resp.content)
        compiler.compile_all.assert_called_once_with(organization=self.org_a)

    def test_org_mismatch_is_rejected_for_non_superuser(self):
        compiler = self._mock_compiler()
        # Simulate a (future) org-resolution path handing back a foreign org.
        with mock.patch(
            "policy.compile_views.get_request_organization", return_value=self.org_b
        ):
            client = APIClient()
            client.force_authenticate(user=self.staff_a)
            resp = client.post("/api/policies/compile/")
        self.assertEqual(resp.status_code, 403)
        self.assertIn("own organization", resp.json()["detail"])
        compiler.compile_all.assert_not_called()
        compiler.push_to_redis.assert_not_called()

    def test_superuser_may_compile_explicit_other_org(self):
        from auth.models import UserProfile

        compiler = self._mock_compiler()
        profile, _ = UserProfile.objects.get_or_create(user=self.superuser)
        profile.is_platform_operator = True
        profile.save(update_fields=["is_platform_operator"])
        self.superuser.is_staff = True
        self.superuser.save(update_fields=["is_staff"])
        client = APIClient()
        client.force_authenticate(user=self.superuser)
        resp = client.post(f"/api/policies/compile/?organization_id={self.org_b.id}")
        self.assertEqual(resp.status_code, 200, resp.content)
        compiler.compile_all.assert_called_once_with(organization=self.org_b)
