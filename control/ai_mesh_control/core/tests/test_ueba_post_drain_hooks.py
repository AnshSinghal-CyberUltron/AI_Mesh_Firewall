"""UEBA post-drain hooks: samples + lifetime + reassess queue."""

from __future__ import annotations

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch


class PostDrainUebaHooksTests(TestCase):
    @patch("module2.tasks.reassess_ueba_keys_for_prefixes.delay")
    @patch("module2.ueba_metrics.increment_lifetime_request_counts")
    @patch("module2.ueba_behavior_profile.append_prompt_samples_for_events")
    def test_hooks_collect_samples_bump_lifetime_and_queue_reassess(
        self, append_samples, incr_lifetime, reassess_delay
    ):
        from core.tasks import _post_drain_ueba_hooks

        events = [
            SimpleNamespace(
                organization_id=7,
                metadata={"api_key_prefix": "abc12345", "prompt": "hello"},
            ),
            SimpleNamespace(
                organization_id=7,
                metadata={"api_key_prefix": "abc12345", "prompt": "world"},
            ),
            SimpleNamespace(
                organization_id=9,
                metadata={"api_key_prefix": "zzz99999"},
            ),
        ]
        _post_drain_ueba_hooks(events)

        append_samples.assert_called_once_with(events)
        incr_lifetime.assert_called_once_with(events)
        self.assertEqual(reassess_delay.call_count, 2)
        called = {call.args[0]: set(call.args[1]) for call in reassess_delay.call_args_list}
        self.assertEqual(called[7], {"abc12345"})
        self.assertEqual(called[9], {"zzz99999"})

    def test_empty_events_noop(self):
        from core.tasks import _post_drain_ueba_hooks

        # Must not import/raise when nothing drained.
        _post_drain_ueba_hooks([])
        _post_drain_ueba_hooks(None)  # type: ignore[arg-type]
