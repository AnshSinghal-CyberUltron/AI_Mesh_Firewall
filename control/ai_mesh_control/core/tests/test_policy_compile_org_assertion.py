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

    def test_org_mismatch_is_rejected_for_superuser(self):
        # R11: is_superuser is no exemption from the compile guard
        # (policy/compile_views.py, `if not is_platform_operator(...)`). Without
        # this test the guard's pre-R11 superuser exemption could come back
        # unnoticed, because get_request_organization already scopes a superuser
        # to its own org before the guard runs (#113 review, A1).
        from auth.models import UserProfile

        profile, _ = UserProfile.objects.get_or_create(user=self.superuser)
        profile.organization = self.org_a
        profile.save(update_fields=["organization"])

        compiler = self._mock_compiler()
        with mock.patch(
            "policy.compile_views.get_request_organization", return_value=self.org_b
        ):
            client = APIClient()
            client.force_authenticate(user=self.superuser)
            resp = client.post("/api/policies/compile/")
        self.assertEqual(resp.status_code, 403, resp.content)
        self.assertIn("own organization", resp.json()["detail"])
        compiler.compile_all.assert_not_called()
        compiler.push_to_redis.assert_not_called()

    # Cross-org compile is reserved for PLATFORM OPERATORS (is_staff AND
    # profile.is_platform_operator, auth/models.py is_platform_operator). Product rule
    # R11, 9f8c9489: "org admins and superusers are ORG-scoped only, NO global
    # cross-org"; see the UserProfile.is_platform_operator field comment,
    # auth/utils.py get_request_organization and policy/compile_views.py. The old
    # test_superuser_may_compile_explicit_other_org asserted the pre-R11 rule, so
    # it is replaced by the tests below (#106); the guard itself is pinned for
    # superusers by test_org_mismatch_is_rejected_for_superuser above.

    def test_platform_operator_may_compile_explicit_other_org(self):
        from auth.models import UserProfile
        from core.models import AuditLog

        operator = User.objects.create_user(
            username="compile_operator", password="pw", is_staff=True
        )
        profile, _ = UserProfile.objects.get_or_create(user=operator)
        profile.organization = self.org_a
        profile.is_platform_operator = True
        profile.save(update_fields=["organization", "is_platform_operator"])

        compiler = self._mock_compiler()
        client = APIClient()
        client.force_authenticate(user=operator)
        resp = client.post(f"/api/policies/compile/?organization_id={self.org_b.id}")
        self.assertEqual(resp.status_code, 200, resp.content)
        compiler.compile_all.assert_called_once_with(organization=self.org_b)
        self.assertEqual(compiler.push_to_redis.call_args.kwargs["organization"], self.org_b)
        # The cross-org access is audited against the target org.
        self.assertTrue(
            AuditLog.objects.filter(
                user=operator, organization=self.org_b, action="cross_org_access"
            ).exists()
        )

    def test_plain_superuser_explicit_other_org_compiles_own_org_only(self):
        from auth.models import UserProfile

        profile, _ = UserProfile.objects.get_or_create(user=self.superuser)
        profile.organization = self.org_a
        profile.save(update_fields=["organization"])

        compiler = self._mock_compiler()
        client = APIClient()
        client.force_authenticate(user=self.superuser)
        resp = client.post(f"/api/policies/compile/?organization_id={self.org_b.id}")
        # ?organization_id is not honoured for a plain superuser: the bundle
        # compiled and pushed is the superuser's own org, never org B.
        self.assertEqual(resp.status_code, 200, resp.content)
        compiler.compile_all.assert_called_once_with(organization=self.org_a)
        self.assertEqual(compiler.push_to_redis.call_args.kwargs["organization"], self.org_a)

    def test_operator_flag_without_staff_compiles_own_org_only(self):
        # A platform operator is is_staff AND profile.is_platform_operator
        # (auth/models.py is_platform_operator). The flag alone grants nothing:
        # ?organization_id is ignored, the user's own org is compiled and pushed,
        # and no cross_org_access audit row is written (#113 review, A4).
        # The user holds the platform_admin role so that, without is_staff, it
        # still passes the view's IsAdminOrSuperuser permission
        # (core/admin_views.py) and reaches org resolution.
        from auth.models import Role, UserProfile
        from core.models import AuditLog

        flagged = User.objects.create_user(
            username="compile_flag_no_staff", password="pw", is_staff=False
        )
        profile, _ = UserProfile.objects.get_or_create(user=flagged)
        profile.organization = self.org_a
        profile.is_platform_operator = True
        profile.save(update_fields=["organization", "is_platform_operator"])
        platform_admin, _ = Role.objects.get_or_create(name="platform_admin")
        profile.roles.add(platform_admin)

        compiler = self._mock_compiler()
        client = APIClient()
        client.force_authenticate(user=flagged)
        resp = client.post(f"/api/policies/compile/?organization_id={self.org_b.id}")
        self.assertEqual(resp.status_code, 200, resp.content)
        compiler.compile_all.assert_called_once_with(organization=self.org_a)
        self.assertEqual(compiler.push_to_redis.call_args.kwargs["organization"], self.org_a)
        self.assertFalse(
            AuditLog.objects.filter(user=flagged, action="cross_org_access").exists()
        )

    def test_plain_superuser_without_org_cannot_compile_other_org(self):
        compiler = self._mock_compiler()
        client = APIClient()
        client.force_authenticate(user=self.superuser)
        resp = client.post(f"/api/policies/compile/?organization_id={self.org_b.id}")
        self.assertEqual(resp.status_code, 403, resp.content)
        compiler.compile_all.assert_not_called()
        compiler.push_to_redis.assert_not_called()
