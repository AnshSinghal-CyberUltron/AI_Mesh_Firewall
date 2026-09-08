"""Phase 0c C-2: hourly facts match the raw-scan oracle (T-C2)."""

from __future__ import annotations

import os
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient

from policy.constants import ACTION_BLOCK, ACTION_REDACT
from policy.models import EnforcementEvent
from policy.tests.analytics_api_testcase import AnalyticsAPITestCase

User = get_user_model()

_PERIODS = ("1h", "6h", "24h", "7d", "30d")
_ENDPOINTS = (
    "/api/security/soc-kpis/?period={p}",
    "/api/security/module-kpis/?period={p}",
    "/api/security/attack-vector-trends/?period={p}",
    "/api/security/module-trends/?period={p}",
)


class AnalyticsRollupTests(AnalyticsAPITestCase):
    def setUp(self):
        from auth.models import Organization, UserProfile

        self.org = Organization.objects.create(name="Rollup Org", slug="rollup-org")
        self.other = Organization.objects.create(name="Other Org", slug="other-org")
        self.user = User.objects.create_user(username="rollup_user", password="pass")
        profile, _ = UserProfile.objects.get_or_create(user=self.user)
        profile.organization = self.org
        profile.save(update_fields=["organization"])
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self._prev = os.environ.get("ANALYTICS_SERVE_ROLLUPS")
        os.environ["ANALYTICS_SERVE_ROLLUPS"] = "0"

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("ANALYTICS_SERVE_ROLLUPS", None)
        else:
            os.environ["ANALYTICS_SERVE_ROLLUPS"] = self._prev

    def _event(self, hours_ago, action=ACTION_BLOCK, org=None, **meta):
        payload = {"source": "security_scan", "threat_category": "prompt injection", **meta}
        ev = EnforcementEvent.objects.create(
            organization=org or self.org,
            action=action,
            metadata=payload,
        )
        EnforcementEvent.objects.filter(pk=ev.pk).update(
            created_at=timezone.now() - timedelta(hours=hours_ago)
        )
        return ev

    def test_unknown_period_still_400_when_rollups_on(self):
        os.environ["ANALYTICS_SERVE_ROLLUPS"] = "1"
        resp = self.client.get("/api/security/soc-kpis/?period=90d")
        self.assertEqual(resp.status_code, 400)

    def test_foreign_org_events_are_not_in_rollup_kpis(self):
        self._event(1, org=self.org)
        self._event(1, org=self.other)
        from policy.analytics_rollup import refresh_org_rollups

        refresh_org_rollups(self.org.id)
        os.environ["ANALYTICS_SERVE_ROLLUPS"] = "1"
        body = self.client.get("/api/security/soc-kpis/?period=24h").json()
        self.assertEqual(body["total_threats"], 1)

    def test_cross_hour_request_id_still_one_inspected(self):
        rid = "zs-rollup-req-1"
        self._event(0.5, action=ACTION_BLOCK, request_id=rid, event_type="request")
        self._event(1.5, action=ACTION_REDACT, request_id=rid, event_type="model_output")
        from policy.analytics_rollup import refresh_org_rollups

        refresh_org_rollups(self.org.id)
        os.environ["ANALYTICS_SERVE_ROLLUPS"] = "0"
        raw = self.client.get("/api/security/soc-kpis/?period=24h").json()
        os.environ["ANALYTICS_SERVE_ROLLUPS"] = "1"
        rolled = self.client.get("/api/security/soc-kpis/?period=24h").json()
        self.assertEqual(raw["requests_inspected"], 1)
        self.assertEqual(rolled["requests_inspected"], 1)
        self.assertEqual(raw["requests_blocked"], 1)
        self.assertEqual(rolled["requests_blocked"], 1)

    def test_tc2_rollup_matches_raw_across_periods_and_endpoints(self):
        self._event(0.2, action=ACTION_BLOCK, owasp_code="LLM01")
        self._event(2, action=ACTION_REDACT, threat_category="data leak", pii_detected=True)
        self._event(10, action="allow", source="mcp_scan")
        self._event(40, action=ACTION_BLOCK, owasp_code="LLM04", threat_category="jailbreak")
        self._event(200, action=ACTION_BLOCK)
        from policy.analytics_rollup import refresh_org_rollups

        refresh_org_rollups(self.org.id, hours=24 * 30)
        mismatches = []
        samples = 0
        for period in _PERIODS:
            os.environ["ANALYTICS_SERVE_ROLLUPS"] = "0"
            raws = {ep: self.client.get(ep.format(p=period)).json() for ep in _ENDPOINTS}
            os.environ["ANALYTICS_SERVE_ROLLUPS"] = "1"
            for ep in _ENDPOINTS:
                samples += 1
                rolled = self.client.get(ep.format(p=period)).json()
                if rolled != raws[ep]:
                    mismatches.append((period, ep, raws[ep], rolled))
        self.assertGreaterEqual(samples, 20)
        self.assertEqual(mismatches, [])

    def test_source_header_rollup_vs_raw_and_short_periods_stay_raw(self):
        self._event(1)
        from policy.analytics_rollup import refresh_org_rollups

        refresh_org_rollups(self.org.id)
        os.environ["ANALYTICS_SERVE_ROLLUPS"] = "1"
        rolled = self.client.get("/api/security/soc-kpis/?period=24h")
        self.assertEqual(rolled.status_code, 200)
        self.assertEqual(rolled["X-Analytics-Source"], "rollup")
        short = self.client.get("/api/security/soc-kpis/?period=1h")
        self.assertEqual(short.status_code, 200)
        self.assertEqual(short["X-Analytics-Source"], "raw")
        six = self.client.get("/api/security/module-trends/?period=6h")
        self.assertEqual(six.status_code, 200)
        self.assertEqual(six["X-Analytics-Source"], "raw")
        os.environ["ANALYTICS_SERVE_ROLLUPS"] = "0"
        off = self.client.get("/api/security/soc-kpis/?period=24h")
        self.assertEqual(off.status_code, 200)
        self.assertEqual(off["X-Analytics-Source"], "raw")

    def test_stale_watermark_keeps_totals_exact_without_abandoning_facts(self):
        """A stale watermark must lose no events -- and must not re-scan the window.

        The old gate demanded ``covered_to >= hour_floor(now)`` and dropped the
        whole rollup otherwise. Since ``covered_to`` is the refresh *timestamp*
        and the refresh runs every 900s, that went false at the top of every
        clock hour, sending all four Overview endpoints down the raw path for up
        to a quarter of each hour -- which is how 30d requests reached the 5s
        analytics deadline and surfaced as 504s.

        Facts are now trimmed to the hours they actually cover and the remainder
        is read from live events. This asserts the property the old test was
        standing in for: with a two-hour-stale watermark AND an event recorded
        after the last refresh, every endpoint still matches the raw oracle.
        """
        from policy.analytics_rollup import refresh_org_rollups
        from policy.analytics_rollup_models import AnalyticsRollupWatermark

        self._event(5)
        self._event(3, action=ACTION_REDACT)
        refresh_org_rollups(self.org.id)
        # Recorded after the rebuild: only the raw edge can account for it.
        self._event(0, action=ACTION_BLOCK, owasp_code="LLM01")

        from main_app.analytics_db import ANALYTICS_DB_ALIAS

        stale = timezone.now() - timedelta(hours=2)
        for alias in ("default", ANALYTICS_DB_ALIAS):
            AnalyticsRollupWatermark.objects.using(alias).filter(
                organization_id=self.org.id
            ).update(covered_to=stale)

        for period in ("24h", "7d", "30d"):
            for endpoint in _ENDPOINTS:
                url = endpoint.format(p=period)
                os.environ["ANALYTICS_SERVE_ROLLUPS"] = "0"
                raw = self.client.get(url)
                os.environ["ANALYTICS_SERVE_ROLLUPS"] = "1"
                rolled = self.client.get(url)
                self.assertEqual(rolled.status_code, 200)
                self.assertEqual(
                    rolled.json(),
                    raw.json(),
                    msg=f"stale-watermark mismatch on {url}",
                )
                # Still served from facts: the point is not to fall back.
                self.assertEqual(rolled["X-Analytics-Source"], "rollup", msg=url)

    def test_partial_first_hour_of_lookback_is_materialised_whole(self):
        """The oldest bucket must not lose the part of its hour before the lookback.

        ``refresh_org_rollups`` advertises ``covered_from = hour_floor(now - hours)``
        and every reader treats that hour as complete -- but the scan used to start
        at the *unaligned* ``now - hours``. Everything in
        ``[covered_from, now - hours)`` was then in neither the facts nor the raw
        edge and vanished from the oldest bucket of the trend charts, which take
        their window from ``trend_grid`` (hour-aligned) rather than from the
        refresh instant. Measured at 59 lost events on a 200k-event corpus.
        """
        from unittest import mock

        from policy.analytics_rollup import refresh_org_rollups

        # Deliberately unaligned, so hour_floor(now - 24h) is strictly earlier
        # than the lookback start and the partial region actually exists.
        frozen = timezone.now().replace(minute=37, second=0, microsecond=0)
        early = (frozen - timedelta(hours=24)).replace(minute=5)

        ev = EnforcementEvent.objects.create(
            organization=self.org,
            action=ACTION_BLOCK,
            metadata={"source": "security_scan", "threat_category": "prompt injection"},
        )
        EnforcementEvent.objects.filter(pk=ev.pk).update(created_at=early)

        with mock.patch("django.utils.timezone.now", return_value=frozen):
            refresh_org_rollups(self.org.id, hours=24)
            url = "/api/security/attack-vector-trends/?period=24h"
            os.environ["ANALYTICS_SERVE_ROLLUPS"] = "0"
            raw = self.client.get(url).json()
            os.environ["ANALYTICS_SERVE_ROLLUPS"] = "1"
            rolled = self.client.get(url)

        self.assertEqual(rolled["X-Analytics-Source"], "rollup")
        self.assertEqual(rolled.json(), raw)
        # The event is genuinely inside the charted window, not a no-op assert.
        self.assertEqual(sum(b["promptInjection"] for b in raw), 1)

    def test_refresh_lock_skips_second_caller_and_respects_disable(self):
        from unittest import mock

        from policy.analytics_rollup_refresh import try_refresh_all_locked

        client = mock.Mock()
        client.set.return_value = False
        with mock.patch("policy.analytics_rollup_refresh._redis", return_value=client):
            skipped = try_refresh_all_locked()
        self.assertEqual(skipped, {"skipped": True, "reason": "lock"})

        prev = os.environ.get("ANALYTICS_ROLLUP_REFRESH")
        os.environ["ANALYTICS_ROLLUP_REFRESH"] = "0"
        try:
            disabled = try_refresh_all_locked()
        finally:
            if prev is None:
                os.environ.pop("ANALYTICS_ROLLUP_REFRESH", None)
            else:
                os.environ["ANALYTICS_ROLLUP_REFRESH"] = prev
        self.assertEqual(disabled, {"skipped": True, "reason": "disabled"})
