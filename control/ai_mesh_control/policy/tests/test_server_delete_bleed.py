"""Server/tool-binding red-team (wf_9ca3814d): deleting a server must NOT convert a user's
server-bound MCP policy into an org-wide one (SET_NULL orphan bleed, invariant E)."""
from django.test import TestCase


class ServerDeleteOrphanBleedTest(TestCase):
    def _mk(self):
        from auth.models import Organization
        from mcp_connector.models import MCPServerRegistration
        from policy.models import Policy, Rule
        org = Organization.objects.create(name="bleed-org")
        a = MCPServerRegistration.objects.create(
            organization=org, name="serverA", server_slug="server-a", url="http://a", transport="streamable-http")
        b = MCPServerRegistration.objects.create(
            organization=org, name="serverB", server_slug="server-b", url="http://b", transport="streamable-http")
        pol = Policy.objects.create(
            organization=org, code="USERPOL1", name="user block A", policy_domain="mcp",
            mcp_server=a, is_system=False, enabled=True)
        Rule.objects.create(
            policy=pol, name="blk", rule_type="keywords", action="block",
            condition={"keywords": ["secret"], "direction": "both", "scope": "entire"}, enabled=True)
        return org, a, b, pol

    def test_deleting_server_disables_bound_user_policy_no_orgwide_bleed(self):
        org, a, b, pol = self._mk()
        self.assertTrue(pol.enabled)
        self.assertEqual(pol.mcp_server_id, a.id)
        a.delete()
        pol.refresh_from_db()
        # FIX: the pre_delete handler disabled the bound user policy BEFORE SET_NULL nulled the FK.
        self.assertFalse(pol.enabled, "bound user policy must be DISABLED on server delete, not left enabled")
        self.assertIsNone(pol.mcp_server_id, "FK is SET_NULL as before")
        # And it must not appear ENABLED/org-wide in a fresh compile → no bleed onto serverB.
        from policy.compiler import PolicyCompiler
        PolicyCompiler().compile_all(org)
        pol.refresh_from_db()
        self.assertFalse(pol.enabled, "still disabled after recompile")
