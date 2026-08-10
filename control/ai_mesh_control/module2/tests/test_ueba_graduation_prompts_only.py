"""Single UI prompt-target (default 50) drives baseline + observation threshold."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import TestCase

from module2.ueba_scoring import (
    graduation_progress,
    graduation_threshold_requests,
    is_graduated,
    resolve_ueba_mode,
)


def _key(*, lifetime: int = 0, override=None, created_days_ago: float = 0.0):
    created = datetime.now(timezone.utc) - timedelta(days=created_days_ago)
    return SimpleNamespace(
        ueba_lifetime_request_count=lifetime,
        ueba_graduation_requests=override,
        created_at=created,
    )


def _org(*, prompt_target: int = 50):
    return SimpleNamespace(behavior_profile_prompt_target=prompt_target)


class GraduationPromptsOnlyTests(TestCase):
    def test_default_threshold_is_fifty(self):
        key = _key(lifetime=0)
        self.assertEqual(graduation_threshold_requests(key, None), 50)
        self.assertFalse(is_graduated(key, None))

    def test_lifetime_49_not_graduated_50_is(self):
        org = _org(prompt_target=50)
        self.assertFalse(is_graduated(_key(lifetime=49), org))
        self.assertTrue(is_graduated(_key(lifetime=50), org))

    def test_age_alone_does_not_graduate(self):
        org = _org(prompt_target=50)
        old_key = _key(lifetime=0, created_days_ago=30.0)
        self.assertFalse(is_graduated(old_key, org))
        self.assertEqual(resolve_ueba_mode(old_key, org), "learning")

    def test_user_override_prompt_target_raises_threshold(self):
        """Operator changes Configure score calculation from default 50 → 75."""
        org = _org(prompt_target=75)
        self.assertEqual(graduation_threshold_requests(_key(), org), 75)
        self.assertFalse(is_graduated(_key(lifetime=50), org))
        self.assertTrue(is_graduated(_key(lifetime=75), org))

    def test_per_key_override_wins(self):
        org = _org(prompt_target=50)
        key = _key(lifetime=10, override=10)
        self.assertEqual(graduation_threshold_requests(key, org), 10)
        self.assertTrue(is_graduated(key, org))

    def test_graduation_progress_has_no_days(self):
        org = _org(prompt_target=50)
        progress = graduation_progress(_key(lifetime=25), org)
        self.assertNotIn("days", progress)
        self.assertNotIn("days", progress.get("thresholds") or {})
        self.assertEqual(progress["requests"], 25)
        self.assertEqual(progress["thresholds"]["requests"], 50)
        self.assertEqual(progress["pct_complete"], 50.0)
        self.assertFalse(progress["graduated"])
