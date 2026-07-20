"""Unit tests for GatewayAPIKeyViewSet.purge_revoked scoping helpers."""

from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase

from core.gateway_key_views import GatewayAPIKeyViewSet, _profile_is_org_admin


class ProfileIsOrgAdminTests(SimpleTestCase):
    def test_none_profile(self):
        self.assertFalse(_profile_is_org_admin(None))

    def test_admin_role(self):
        roles = mock.Mock()
        roles.filter.return_value.exists.return_value = True
        self.assertTrue(_profile_is_org_admin(SimpleNamespace(roles=roles)))
        roles.filter.assert_called_once_with(name__in=("admin", "superadmin", "org_admin"))

    def test_non_admin(self):
        roles = mock.Mock()
        roles.filter.return_value.exists.return_value = False
        self.assertFalse(_profile_is_org_admin(SimpleNamespace(roles=roles)))


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
        user = SimpleNamespace(id=42, profile=SimpleNamespace(roles=roles))
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
