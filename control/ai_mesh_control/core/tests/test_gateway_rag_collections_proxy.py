"""Tests for RAG collections admin proxy organization_id stamping."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from core.gateway_admin_proxy_views import (
    GatewayRagCollectionsProxyView,
    _resolve_org_context,
)


class _FakeOrg:
    pk = 42
    slug = "zeroshield"


class ResolveOrgContextTests(SimpleTestCase):
    def test_resolve_org_context_uses_slug_and_pk(self):
        request = SimpleNamespace(
            user=SimpleNamespace(is_superuser=False),
            data={},
            query_params={},
        )
        with patch(
            "core.gateway_admin_proxy_views.get_request_organization",
            return_value=_FakeOrg(),
        ):
            project_id, org_id = _resolve_org_context(request)
        self.assertEqual(project_id, "zeroshield")
        self.assertEqual(org_id, 42)


class GatewayRagCollectionsProxyTests(SimpleTestCase):
    def setUp(self):
        self.view = GatewayRagCollectionsProxyView()
        self.request = SimpleNamespace(
            user=SimpleNamespace(is_superuser=False),
            data={},
            query_params={},
        )

    @patch("core.gateway_admin_proxy_views._proxy")
    @patch("core.gateway_admin_proxy_views.get_request_organization")
    def test_get_includes_organization_id(self, mock_org, mock_proxy):
        mock_org.return_value = _FakeOrg()
        mock_proxy.return_value = MagicMock()

        self.view.get(self.request)

        mock_proxy.assert_called_once()
        method, path = mock_proxy.call_args[0][:2]
        self.assertEqual(method, "GET")
        self.assertIn("project_id=zeroshield", path)
        self.assertIn("organization_id=42", path)

    @patch("core.gateway_admin_proxy_views._proxy")
    @patch("core.gateway_admin_proxy_views.get_request_organization")
    def test_post_stamps_organization_id(self, mock_org, mock_proxy):
        mock_org.return_value = _FakeOrg()
        mock_proxy.return_value = MagicMock()
        self.request.data = {"collection": "docs", "vector_db_type": "pinecone"}

        self.view.post(self.request)

        payload = mock_proxy.call_args[0][2]
        self.assertEqual(payload["project_id"], "zeroshield")
        self.assertEqual(payload["organization_id"], 42)
