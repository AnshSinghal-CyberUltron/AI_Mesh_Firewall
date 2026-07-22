"""Tests for request-scoped enforcement counting (Module 1 / Module 2 parity)."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from policy.constants import ACTION_BLOCK
from policy.models import EnforcementEvent
from policy.request_scoped_metrics import collapse_events_by_request, summarize_request_scoped_events


class RequestScopedMetricsTests(TestCase):
    def test_collapse_three_requests_from_five_rows(self):
        rows = [
            {"action": "allow", "metadata": {"request_id": "zs-aaaaaaaaaaaa", "event_type": "request"}},
            {"action": "reroute", "metadata": {"request_id": "zs-aaaaaaaaaaaa", "event_type": "model_routed"}},
            {"action": "confirm", "metadata": {"request_id": "zs-bbbbbbbbbbbb", "event_type": "model_routed"}},
            {"action": "block", "metadata": {"request_id": "zs-bbbbbbbbbbbb", "event_type": "request"}},
            {"action": "block", "metadata": {"request_id": "zs-cccccccccccc", "event_type": "input_blocked"}},
        ]
        summary = summarize_request_scoped_events(rows)
        self.assertEqual(summary["requests_inspected"], 3)
        self.assertEqual(summary["requests_allowed"], 1)
        self.assertEqual(summary["requests_blocked"], 2)

    def test_module2_dashboard_matches_soc_kpis_for_same_request(self):
        from auth.models import Organization, UserProfile

        org = Organization.objects.create(name="Parity Org", slug="parity-org")
        user = get_user_model().objects.create_user(username="parity", password="pass")
        profile, _ = UserProfile.objects.get_or_create(user=user)
        profile.organization = org
        profile.save(update_fields=["organization"])

        rid = "zs-dddddddddddd"
        since = timezone.now() - timedelta(minutes=5)
        for action, event_type in (("allow", "request"), ("reroute", "model_routed")):
            ev = EnforcementEvent.objects.create(
                organization=org,
                action=action,
                metadata={"source": "gateway", "event_type": event_type, "request_id": rid},
            )
            EnforcementEvent.objects.filter(pk=ev.pk).update(created_at=since)

        client = APIClient()
        client.force_authenticate(user=user)
        soc = client.get("/api/security/soc-kpis/?period=24h").json()
        dash = client.get("/api/module2/dashboard/?period=24h").json()

        self.assertEqual(soc["requests_inspected"], 1)
        self.assertEqual(dash["kpis"]["total_events"], 1)
        self.assertEqual(dash["kpis"]["requests_inspected"], 1)
        self.assertEqual(dash["kpis"]["requests_blocked"], soc["requests_blocked"])
        self.assertEqual(dash["lane_summary"]["chat"]["total"], 1)

    def test_collapse_preserves_prompt_when_block_event_lacks_snippet(self):
        rows = [
            {
                "action": "allow",
                "metadata": {
                    "request_id": "zs-eeeeeeeeeeee",
                    "event_type": "request",
                    "prompt_snippet": "how are u today",
                    "prompt_submitted": "how are u today",
                },
            },
            {
                "action": ACTION_BLOCK,
                "metadata": {
                    "request_id": "zs-eeeeeeeeeeee",
                    "event_type": "input_blocked",
                    "threat_type": "llm_judge:direct",
                    "detail": "LLM Judge detected injection",
                },
            },
        ]
        collapsed = collapse_events_by_request(rows)
        self.assertEqual(len(collapsed), 1)
        self.assertEqual(collapsed[0].action, "block")
        self.assertEqual(collapsed[0].metadata.get("prompt_snippet"), "how are u today")
        self.assertEqual(collapsed[0].metadata.get("prompt_submitted"), "how are u today")
