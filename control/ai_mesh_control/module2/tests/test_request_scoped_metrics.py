"""Unit tests for Module 2 self-contained request-scoped metrics."""

from django.test import SimpleTestCase

from policy.constants import ACTION_BLOCK
from module2.request_scoped_metrics import (
    collapse_events_by_request,
    summarize_request_scoped_events,
)


class Module2RequestScopedMetricsTests(SimpleTestCase):
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
