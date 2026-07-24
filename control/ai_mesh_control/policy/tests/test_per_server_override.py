from django.test import TestCase


class PerServerOverrideCompileTest(TestCase):
    def test_override_stamps_server_states_into_bundle(self):
        from auth.models import Organization
        from mcp_connector.models import MCPServerRegistration
        from policy.models import Policy, Rule, MCPServerPolicyState, MCPServerRuleState
        from policy.compiler import PolicyCompiler
        org = Organization.objects.create(name="ov-org")
        a = MCPServerRegistration.objects.create(organization=org, name="srvA", server_slug="srv-a", url="http://a", transport="streamable-http")
        b = MCPServerRegistration.objects.create(organization=org, name="srvB", server_slug="srv-b", url="http://b", transport="streamable-http")
        pol = Policy.objects.create(organization=org, code="OVP", name="ov policy", policy_domain="mcp", enabled=True)  # org-wide
        rule = Rule.objects.create(policy=pol, name="r1", rule_type="keywords", action="block",
                                   condition={"keywords": ["x"], "direction": "both", "scope": "entire"}, enabled=True)
        # disable the whole policy for serverB; disable the rule for serverA
        MCPServerPolicyState.objects.create(organization=org, server=b, policy=pol, enabled=False)
        MCPServerRuleState.objects.create(organization=org, server=a, rule=rule, enabled=False)
        bundle = PolicyCompiler().compile_all(org)
        entry = next(e for e in bundle["policies"] if e["policy"]["code"] == "OVP")
        # policy carries the per-server override map
        self.assertEqual(entry["policy"]["server_states"], {"srv-b": False})
        # the rule carries its per-server override map
        self.assertEqual(entry["rules"][0]["server_states"], {"srv-a": False})
        print("BUNDLE server_states OK:", entry["policy"]["server_states"], entry["rules"][0]["server_states"])
