"""red-team wf_51ca33ea fixes: (1) GET clamps rule state to the global ceiling + hides globally-
disabled rules (UI honesty); (2) a non-UUID server_id returns a clean 4xx, not a 500."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

User = get_user_model()


class PerServerOverrideRedteamFixTest(TestCase):
    def _setup(self):
        from auth.models import Organization, UserProfile
        from mcp_connector.models import MCPServerRegistration
        from policy.models import Policy, Rule, MCPServerRuleState
        org = Organization.objects.create(name="rt-org")
        user = User.objects.create_user(username="rt", password="pw")
        prof, _ = UserProfile.objects.get_or_create(user=user)
        prof.organization = org
        prof.save(update_fields=["organization"])
        srv = MCPServerRegistration.objects.create(organization=org, name="sx", server_slug="sx", url="http://x", transport="streamable-http")
        pol = Policy.objects.create(organization=org, code="RTP", name="rt policy", policy_domain="mcp", enabled=True)
        r_on = Rule.objects.create(policy=pol, name="r_on", rule_type="keywords", action="block",
                                   condition={"keywords": ["x"]}, enabled=True)
        r_off = Rule.objects.create(policy=pol, name="r_off", rule_type="keywords", action="block",
                                    condition={"keywords": ["y"]}, enabled=False)
        MCPServerRuleState.objects.create(organization=org, server=srv, rule=r_off, enabled=True)
        return org, user, srv

    def test_get_hides_globally_disabled_rule_and_clamps_to_ceiling(self):
        from policy.views import MCPServerStateView
        org, user, srv = self._setup()
        req = APIRequestFactory().get(f"/api/policies/mcp-server-states/?server_id={srv.id}")
        force_authenticate(req, user=user)
        resp = MCPServerStateView.as_view()(req)
        self.assertEqual(resp.status_code, 200, resp.data)
        rtp = next(p for p in resp.data["policies"] if p["code"] == "RTP")
        names = {r["name"]: r["enabled_for_server"] for r in rtp["rules"]}
        self.assertNotIn("r_off", names, "globally-disabled rule must not appear enabled in the per-server view")
        self.assertEqual(names.get("r_on"), True)
        print("GET rules (honest):", names)

    def test_non_uuid_server_id_returns_clean_4xx_not_500(self):
        from policy.views import MCPServerStateView
        org, user, srv = self._setup()
        req = APIRequestFactory().get("/api/policies/mcp-server-states/?server_id=not-a-uuid")
        force_authenticate(req, user=user)
        resp = MCPServerStateView.as_view()(req)
        self.assertEqual(resp.status_code, 404, f"expected clean 404, got {resp.status_code}")
        print("non-uuid server_id ->", resp.status_code, dict(resp.data))


class PerServerPolicyOffRuleParityTest(TestCase):
    """red-team wf_1f7cd667: a rule under a policy that is OFF for this server (bound elsewhere, or
    per-server policy-override disabled) must render enabled_for_server=False — the gateway drops the
    whole policy + all its rules, so the view must not light the rules green."""
    def test_rule_of_policy_bound_elsewhere_is_off_for_this_server(self):
        from django.contrib.auth import get_user_model
        from auth.models import Organization, UserProfile
        from mcp_connector.models import MCPServerRegistration
        from policy.models import Policy, Rule
        from policy.views import MCPServerStateView
        from rest_framework.test import APIRequestFactory, force_authenticate
        User = get_user_model()
        org = Organization.objects.create(name="parity-org")
        user = User.objects.create_user(username="par", password="pw")
        prof, _ = UserProfile.objects.get_or_create(user=user); prof.organization = org
        prof.save(update_fields=["organization"])
        a = MCPServerRegistration.objects.create(organization=org, name="A", server_slug="pa", url="http://a", transport="streamable-http")
        b = MCPServerRegistration.objects.create(organization=org, name="B", server_slug="pb", url="http://b", transport="streamable-http")
        # policy bound ONLY to server B, with a globally-enabled rule
        pol = Policy.objects.create(organization=org, code="P_ELSEWHERE", name="bound B", policy_domain="mcp", enabled=True, mcp_server=b)
        Rule.objects.create(policy=pol, name="rE", rule_type="keywords", action="block", condition={"keywords": ["x"]}, enabled=True)
        # GET for server A: the policy is off for A -> its rule must be off for A too
        req = APIRequestFactory().get(f"/api/policies/mcp-server-states/?server_id={a.id}")
        force_authenticate(req, user=user)
        resp = MCPServerStateView.as_view()(req)
        self.assertEqual(resp.status_code, 200, resp.data)
        pe = next(p for p in resp.data["policies"] if p["code"] == "P_ELSEWHERE")
        self.assertFalse(pe["enabled_for_server"], "policy bound elsewhere is off for server A")
        for r in pe["rules"]:
            self.assertFalse(r["enabled_for_server"], f"rule {r['name']} of an off-for-A policy must show OFF, matching gateway (which drops it)")
        print("policy-off rule parity OK:", pe["enabled_for_server"], [r["enabled_for_server"] for r in pe["rules"]])
