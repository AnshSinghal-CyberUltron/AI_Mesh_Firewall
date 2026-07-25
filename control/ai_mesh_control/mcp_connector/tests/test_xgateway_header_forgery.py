"""AU3-01 (lifecycle red-team): the agent_id (X-Gateway-Key-Prefix) and roles
(X-Gateway-Roles) that scope per-agent/per-role MCP policies must be trusted ONLY from
the internal gateway->backend path. MCPToolCallView also accepts a direct JWT org user,
who could otherwise forge these headers to dodge a role/agent-scoped policy.
"""
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from mcp_connector import views


def _req(headers):
    return SimpleNamespace(headers=headers)


_FORGED = {
    "X-Gateway-Key-Prefix": "abcd1234",
    "X-Gateway-Roles": "admin,superuser",
}


class GatewayForwardedIdentityTests(SimpleTestCase):
    def test_direct_jwt_user_cannot_forge_identity(self):
        # No gateway shared-secret headers -> a plain JWT/API caller. Forged
        # X-Gateway-* headers MUST be ignored (empty identity).
        with patch.object(views, "_is_gateway_internal_request", return_value=False):
            agent_id, roles = views._gateway_forwarded_actor_identity(_req(_FORGED))
        self.assertEqual(agent_id, "")
        self.assertEqual(roles, [])

    def test_gateway_internal_request_is_trusted(self):
        # The real gateway path (shared-secret authenticated) -> headers are honored.
        with patch.object(views, "_is_gateway_internal_request", return_value=True):
            agent_id, roles = views._gateway_forwarded_actor_identity(_req(_FORGED))
        self.assertEqual(agent_id, "abcd1234")
        self.assertEqual(roles, ["admin", "superuser"])

    def test_gateway_internal_url_quoted_roles_decoded(self):
        with patch.object(views, "_is_gateway_internal_request", return_value=True):
            _a, roles = views._gateway_forwarded_actor_identity(
                _req({"X-Gateway-Roles": "role%20one, role%2Ftwo"}))
        self.assertEqual(roles, ["role one", "role/two"])

    def test_gateway_internal_empty_headers_empty_identity(self):
        with patch.object(views, "_is_gateway_internal_request", return_value=True):
            agent_id, roles = views._gateway_forwarded_actor_identity(_req({}))
        self.assertEqual(agent_id, "")
        self.assertEqual(roles, [])
