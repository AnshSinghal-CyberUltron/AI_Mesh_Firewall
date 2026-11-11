"""GW35 D-21: gateway-v2 totals count threats, and list refusals and faults apart."""
from __future__ import annotations

from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from policy.models import EnforcementEvent
from policy.tests.analytics_api_testcase import AnalyticsAPITestCase

User = get_user_model()


class V2TotalsTests(AnalyticsAPITestCase):
    def setUp(self):
        from auth.models import Organization, UserProfile

        self.org = Organization.objects.create(name="V2 Org", slug="v2-org")
        self.user = User.objects.create_user(username="v2_user", password="pass")
        profile, _ = UserProfile.objects.get_or_create(user=self.user)
        profile.organization = self.org
        profile.save(update_fields=["organization"])
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def _row(self, action, request_id, **meta):
        EnforcementEvent.objects.create(
            organization=self.org,
            action=action,
            metadata={"source": "gateway_v2", "request_id": request_id, **meta},
        )

    def _v2(self):
        resp = self.client.get("/api/security/soc-kpis/?period=24h")
        self.assertEqual(resp.status_code, 200)
        return resp.json()["gateway_v2"]

    def test_threats_exclude_operational_refusals_and_errors(self):
        self._row("block", "a")  # an output block: a threat
        self._row("redact", "b")
        self._row("flag", "c")
        self._row("block", "d", refused_at="auth")  # a 401 probe: not a threat
        self._row("block", "e", refused_at="rate_limit")  # a shed: not a threat
        self._row("error", "f", refused_at="model_output")  # an upstream fault
        self._row("allow", "g")
        v2 = self._v2()
        self.assertEqual(v2["total_threats"], 3)
        self.assertEqual(v2["blocked"], 1)
        self.assertEqual(v2["redacted"], 1)
        self.assertEqual(v2["refused_total"], 2)
        self.assertEqual(v2["error_total"], 1)

    def test_rows_written_before_refused_at_existed_count_as_they_did(self):
        self._row("block", "legacy-1")
        self._row("redact", "legacy-2")
        v2 = self._v2()
        self.assertEqual((v2["total_threats"], v2["blocked"], v2["refused_total"]), (2, 1, 0))

    def test_v1_rows_never_enter_the_v2_totals(self):
        EnforcementEvent.objects.create(
            organization=self.org, action="block", metadata={"source": "security_scan"}
        )
        self.assertEqual(self._v2()["total_threats"], 0)
