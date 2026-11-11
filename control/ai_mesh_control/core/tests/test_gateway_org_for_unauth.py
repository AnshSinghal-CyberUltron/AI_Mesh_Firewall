"""Integration live finding (4 Nov 2026): mapping GW35's `_unauthenticated` audit stream to a slug
(Req 4.4) made `gateway_org_for` ambiguous, so the dev plan copy stopped (`dev_copy: not_mapped`)
and console saves no longer reached the dev gateway."""
from django.test import SimpleTestCase

from core.firewall_v2_publish import gateway_org_for


class GatewayOrgForTests(SimpleTestCase):
    def test_the_unauthenticated_stream_does_not_make_the_alias_ambiguous(self):
        raw = "dev-org=zeroshield,_unauthenticated=zeroshield"
        self.assertEqual(gateway_org_for("zeroshield", raw), "dev-org")

    def test_two_real_gateway_orgs_for_one_slug_stay_ambiguous(self):
        self.assertIsNone(gateway_org_for("zeroshield", "a=zeroshield,b=zeroshield"))

    def test_only_the_unauthenticated_stream_maps_no_gateway_org(self):
        self.assertIsNone(gateway_org_for("zeroshield", "_unauthenticated=zeroshield"))
