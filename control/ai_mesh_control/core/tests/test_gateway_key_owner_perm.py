"""#43 regression: IsGatewayKeyOwner must query the roles M2M, not a nonexistent
`profile.role` (which raised AttributeError → HTTP 500 on the org-admin path).

UserProfile has `roles` (M2M), NOT `role`. The permission is checked with a mocked
profile so no DB is needed. Behavior also proven standalone this session via AST
extraction of has_object_permission.
"""

from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase

from core.gateway_key_views import IsGatewayKeyOwner


def _req(method, roles_exist, user_id=99, org=1):
    roles = mock.Mock()
    roles.filter.return_value.exists.return_value = roles_exist
    profile = SimpleNamespace(organization_id=org, roles=roles)
    user = SimpleNamespace(id=user_id, profile=profile)
    return SimpleNamespace(method=method, user=user), roles


class GatewayKeyOwnerPermTests(SimpleTestCase):
    def setUp(self):
        self.perm = IsGatewayKeyOwner()

    def test_org_admin_can_mutate_nonowned_same_org_key(self):
        req, roles = _req("DELETE", roles_exist=True)
        obj = SimpleNamespace(owner_id=5, organization_id=1)  # different owner, same org
        self.assertTrue(self.perm.has_object_permission(req, None, obj))
        roles.filter.assert_called_once_with(name__in=("admin", "superadmin", "org_admin"))

    def test_non_admin_same_org_denied_cleanly_not_500(self):
        req, _ = _req("PATCH", roles_exist=False)
        obj = SimpleNamespace(owner_id=5, organization_id=1)
        # must return False (deny), never raise AttributeError → 500
        self.assertFalse(self.perm.has_object_permission(req, None, obj))

    def test_owner_can_mutate_own_key(self):
        req, _ = _req("DELETE", roles_exist=False, user_id=5)
        obj = SimpleNamespace(owner_id=5, organization_id=1)
        self.assertTrue(self.perm.has_object_permission(req, None, obj))

    def test_safe_method_allowed(self):
        req, _ = _req("GET", roles_exist=False)
        obj = SimpleNamespace(owner_id=5, organization_id=1)
        self.assertTrue(self.perm.has_object_permission(req, None, obj))
