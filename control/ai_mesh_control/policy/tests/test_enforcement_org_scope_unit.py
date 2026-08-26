"""T-C8 unit: fail-closed _enforcement_events_for_request (no DB)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from django.test import RequestFactory, SimpleTestCase


class EnforcementOrgScopeUnitTests(SimpleTestCase):
    def test_superuser_no_org_uses_none_not_unscoped(self):
        from policy.security_views import _enforcement_events_for_request

        req = RequestFactory().get("/api/security/soc-kpis/")
        req.user = MagicMock(is_authenticated=True, is_superuser=True)
        base = MagicMock()
        none_qs = MagicMock()
        base.none.return_value = none_qs
        none_qs.using.return_value = none_qs
        with patch("auth.utils.get_request_organization", return_value=None):
            result = _enforcement_events_for_request(req, base)
        base.none.assert_called_once()
        base.filter.assert_not_called()
        none_qs.using.assert_called()
        self.assertIs(result, none_qs)

    def test_unauthenticated_uses_none(self):
        from django.contrib.auth.models import AnonymousUser
        from policy.security_views import _enforcement_events_for_request

        req = RequestFactory().get("/api/security/soc-kpis/")
        req.user = AnonymousUser()
        base = MagicMock()
        none_qs = MagicMock()
        base.none.return_value = none_qs
        none_qs.using.return_value = none_qs
        with patch("auth.utils.get_request_organization", return_value=None):
            _enforcement_events_for_request(req, base)
        base.none.assert_called_once()
        base.filter.assert_not_called()

    def test_org_present_filters_and_uses_analytics_alias(self):
        from main_app.analytics_db import ANALYTICS_DB_ALIAS
        from policy.security_views import _enforcement_events_for_request

        req = RequestFactory().get("/api/security/soc-kpis/")
        org = MagicMock()
        base = MagicMock()
        filtered = MagicMock()
        base.filter.return_value = filtered
        filtered.using.return_value = filtered
        with patch("auth.utils.get_request_organization", return_value=org):
            result = _enforcement_events_for_request(req, base)
        base.filter.assert_called_once_with(organization=org)
        filtered.using.assert_called_once_with(ANALYTICS_DB_ALIAS)
        self.assertIs(result, filtered)
