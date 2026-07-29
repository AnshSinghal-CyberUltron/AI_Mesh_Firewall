"""Unit tests for GatewayAPIKeyViewSet.purge_revoked scoping helpers."""

from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase

from core.gateway_key_views import (
    GatewayAPIKeyViewSet,
    IsGatewayKeyOwner,
    _profile_is_org_admin,
)


class ProfileIsOrgAdminTests(SimpleTestCase):
    def test_none_profile(self):
        self.assertFalse(_profile_is_org_admin(None))

    def test_admin_role(self):
        roles = mock.Mock()
        roles.filter.return_value.exists.return_value = True
        self.assertTrue(_profile_is_org_admin(SimpleNamespace(roles=roles)))
        # platform_admin is now recognised alongside the org-scoped admin roles.
        roles.filter.assert_called_once_with(
            name__in=("admin", "superadmin", "org_admin", "platform_admin")
        )

    def test_non_admin(self):
        roles = mock.Mock()
        roles.filter.return_value.exists.return_value = False
        self.assertFalse(_profile_is_org_admin(SimpleNamespace(roles=roles)))


class IsGatewayKeyOwnerTests(SimpleTestCase):
    def _check(self, user, obj, method="DELETE"):
        return IsGatewayKeyOwner().has_object_permission(
            SimpleNamespace(user=user, method=method), None, obj
        )

    def test_superuser_can_mutate_any_key(self):
        # not owner, role is NOT org-admin — allowed purely because superuser.
        roles = mock.Mock()
        roles.filter.return_value.exists.return_value = False
        user = SimpleNamespace(
            id=2, is_superuser=True,
            profile=SimpleNamespace(roles=roles, organization_id=2),
        )
        obj = SimpleNamespace(owner_id=1, organization_id=2)
        self.assertTrue(self._check(user, obj))

    def test_non_owner_non_admin_denied(self):
        roles = mock.Mock()
        roles.filter.return_value.exists.return_value = False
        user = SimpleNamespace(
            id=2, is_superuser=False,
            profile=SimpleNamespace(roles=roles, organization_id=2),
        )
        obj = SimpleNamespace(owner_id=1, organization_id=2)
        self.assertFalse(self._check(user, obj))

    def test_owner_allowed(self):
        user = SimpleNamespace(id=1, is_superuser=False, profile=None)
        obj = SimpleNamespace(owner_id=1, organization_id=2)
        self.assertTrue(self._check(user, obj))

    def test_same_org_admin_role_allowed(self):
        roles = mock.Mock()
        roles.filter.return_value.exists.return_value = True
        user = SimpleNamespace(
            id=3, is_superuser=False,
            profile=SimpleNamespace(roles=roles, organization_id=2),
        )
        obj = SimpleNamespace(owner_id=1, organization_id=2)
        self.assertTrue(self._check(user, obj))

    def test_safe_method_always_allowed(self):
        user = SimpleNamespace(id=99, is_superuser=False, profile=None)
        obj = SimpleNamespace(owner_id=1, organization_id=2)
        self.assertTrue(self._check(user, obj, method="GET"))


class PurgeRevokedScopeTests(SimpleTestCase):
    def _view(self, user, base_qs):
        view = GatewayAPIKeyViewSet()
        view.request = SimpleNamespace(user=user)
        view.get_queryset = mock.Mock(return_value=base_qs)
        return view

    def test_org_admin_sees_all_revoked_in_queryset(self):
        roles = mock.Mock()
        roles.filter.return_value.exists.return_value = True
        user = SimpleNamespace(id=1, profile=SimpleNamespace(roles=roles))
        revoked = mock.Mock(name="revoked_qs")
        base = mock.Mock()
        base.filter.return_value = revoked
        view = self._view(user, base)
        result = view._revoked_keys_for_bulk_purge()
        base.filter.assert_called_once_with(is_active=False)
        self.assertIs(result, revoked)

    def test_non_admin_limited_to_own_revoked(self):
        roles = mock.Mock()
        roles.filter.return_value.exists.return_value = False
        user = SimpleNamespace(id=42, is_superuser=False, profile=SimpleNamespace(roles=roles))
        owned = mock.Mock(name="owned_revoked")
        revoked = mock.Mock()
        revoked.filter.return_value = owned
        base = mock.Mock()
        base.filter.return_value = revoked
        view = self._view(user, base)
        result = view._revoked_keys_for_bulk_purge()
        base.filter.assert_called_once_with(is_active=False)
        revoked.filter.assert_called_once_with(owner=user)
        self.assertIs(result, owned)

    def test_superuser_purges_all_revoked_even_if_not_org_admin(self):
        # platform_admin operator (superuser) with a non-org-admin role must purge
        # every revoked key in the org queryset, not just their own (which was 0).
        roles = mock.Mock()
        roles.filter.return_value.exists.return_value = False
        user = SimpleNamespace(id=2, is_superuser=True, profile=SimpleNamespace(roles=roles))
        revoked = mock.Mock(name="all_revoked")
        base = mock.Mock()
        base.filter.return_value = revoked
        view = self._view(user, base)
        result = view._revoked_keys_for_bulk_purge()
        base.filter.assert_called_once_with(is_active=False)
        revoked.filter.assert_not_called()  # NOT narrowed to owner
        self.assertIs(result, revoked)
