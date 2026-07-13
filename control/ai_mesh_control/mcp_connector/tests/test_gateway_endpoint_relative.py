"""Regression test: MCPServerRegistration.gateway_endpoint must stay a relative
path, never host-qualified with settings.GATEWAY_PUBLIC_URL.

Root cause of the MCP gateway URL mismatch bug: this property used to return
f"{settings.GATEWAY_PUBLIC_URL}/gateway/{org.slug}/mcp/{slug}". GATEWAY_PUBLIC_URL
is one shared env var for the whole deployment and, on a local dev stack that
reuses the same .env as production, it holds the production gateway domain
(https://aimeshgateway.zeroshield.ai) — so every server's gateway_endpoint (read
straight off the API by MCPConnectorPanel's "Copy MCP Config" / server-card URL /
OAuth start) baked in the production host even in local dev, and the frontend's
toAbsoluteGatewayUrl() short-circuits on an already-absolute URL so it could never
correct it.

Returning a bare path lets the frontend resolve it itself based on where the
browser is actually running (resolveMcpGatewayBaseUrl() in environmentUrls.js).
"""

from django.test import TestCase, override_settings

from mcp_connector.models import MCPServerRegistration
from mcp_connector.serializers import MCPServerRegistrationSerializer


class GatewayEndpointRelativePathTests(TestCase):
    def setUp(self):
        from auth.models import Organization

        self.org = Organization.objects.create(name="Endpoint Org", slug="endpoint-org")
        self.server = MCPServerRegistration.objects.create(
            name="Files Server",
            server_slug="files-server",
            url="https://mcp.example.com/mcp",
            organization=self.org,
        )

    @override_settings(GATEWAY_PUBLIC_URL="https://aimeshgateway.zeroshield.ai")
    def test_gateway_endpoint_is_relative_even_with_prod_gateway_public_url_set(self):
        # The exact scenario that caused the bug: GATEWAY_PUBLIC_URL is the prod
        # domain (as it is on the shared .env), yet the emitted endpoint must
        # NOT be host-qualified with it.
        endpoint = self.server.gateway_endpoint
        self.assertEqual(endpoint, "/gateway/endpoint-org/mcp/files-server")
        self.assertFalse(endpoint.startswith("http"))
        self.assertNotIn("aimeshgateway.zeroshield.ai", endpoint)

    def test_gateway_endpoint_relative_regardless_of_gateway_public_url(self):
        with override_settings(GATEWAY_PUBLIC_URL="http://127.0.0.1:8300"):
            self.assertEqual(
                self.server.gateway_endpoint, "/gateway/endpoint-org/mcp/files-server"
            )

    def test_gateway_endpoint_empty_without_organization(self):
        orphan = MCPServerRegistration.objects.create(
            name="Orphan", server_slug="orphan-server", url="https://x.example/mcp"
        )
        self.assertEqual(orphan.gateway_endpoint, "")

    def test_serializer_exposes_the_same_relative_path(self):
        data = MCPServerRegistrationSerializer(self.server).data
        self.assertEqual(data["gateway_endpoint"], "/gateway/endpoint-org/mcp/files-server")
