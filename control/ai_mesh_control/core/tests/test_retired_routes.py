"""GW35 D-22: with AMF_GATEWAY_BACKEND=v2 the retired proxies answer 410 and never call out."""
from __future__ import annotations

from unittest import mock

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings

from core import gateway_admin_proxy_views as proxy
from core import retired_routes


class RetiredRoutesTests(SimpleTestCase):
    @override_settings(AMF_GATEWAY_BACKEND="v2", GATEWAY_INTERNAL_API_KEY="secret")
    def test_v2_answers_410_and_makes_no_network_call(self):
        for path in retired_routes.RETIRED_ON_V2:
            with mock.patch.object(proxy.requests, "get") as get, mock.patch.object(
                proxy.requests, "request"
            ) as request:
                response = proxy._proxy("GET", path)
                self.assertEqual(response.status_code, 410, path)
                body = response.data
                self.assertEqual(body["status"], "error")
                self.assertEqual(body["data"]["code"], "retired_on_gateway_v2")
                self.assertEqual(body["data"]["path"], path)
                get.assert_not_called()
                request.assert_not_called()

    @override_settings(AMF_GATEWAY_BACKEND="v2", GATEWAY_INTERNAL_API_KEY="")
    def test_the_retired_check_precedes_the_secret_lookup(self):
        response = proxy._proxy("GET", "/v1/admin/circuit-breaker-state")
        self.assertEqual(response.status_code, 410)  # not the 503 proxy_unconfigured

    @override_settings(AMF_GATEWAY_BACKEND="v2", GATEWAY_INTERNAL_API_KEY="secret")
    def test_a_path_gateway_v2_does_serve_still_forwards(self):
        with mock.patch.object(proxy.requests, "request") as request:
            request.return_value = mock.Mock(status_code=200, json=lambda: {"ok": 1})
            response = proxy._proxy("POST", "/v1/admin/rag/collections", {})
        self.assertEqual(response.status_code, 200)

    @override_settings(AMF_GATEWAY_BACKEND="v1", GATEWAY_INTERNAL_API_KEY="secret")
    def test_v1_default_forwards_exactly_as_before(self):
        with mock.patch.object(proxy.requests, "get") as get:
            get.return_value = mock.Mock(status_code=200, json=lambda: {"state": "closed"})
            response = proxy._proxy("GET", "/v1/admin/circuit-breaker-state")
        self.assertEqual(response.status_code, 200)
        get.assert_called_once()

    @override_settings(AMF_GATEWAY_BACKEND="V2")
    def test_an_invalid_value_raises_and_does_not_fall_back(self):
        with self.assertRaises(ImproperlyConfigured):
            retired_routes.gateway_backend()
        with self.assertRaises(ImproperlyConfigured):
            retired_routes.is_retired("/v1/admin/db-test")

    def test_settings_refuse_to_start_on_a_bad_value(self):
        import importlib
        import os

        from main_app import settings

        with mock.patch.dict(os.environ, {"AMF_GATEWAY_BACKEND": "v3"}):
            with self.assertRaises(ImproperlyConfigured):
                importlib.reload(settings)
        importlib.reload(settings)

    @override_settings(AMF_GATEWAY_BACKEND="v2", GATEWAY_INTERNAL_API_KEY="secret")
    def test_the_mcp_forwarders_do_not_call_the_gateway(self):
        from mcp_connector import views

        with mock.patch.object(views.requests, "post") as post:
            tools, error = views._discover_tools_via_gateway(mock.Mock(), mock.Mock())
            self.assertEqual(tools, [])
            self.assertIn("retired_on_gateway_v2", error)
            with self.assertRaises(views._MCPToolCallError) as caught:
                views._call_tool_via_gateway(mock.Mock(), mock.Mock(), "t", {})
            self.assertEqual(caught.exception.http_status, 410)
            views._mirror_oauth_token_to_gateway(mock.Mock(), {"access_token": "x"})
            post.assert_not_called()
