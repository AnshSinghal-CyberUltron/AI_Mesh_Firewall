"""Unit tests for prompt-target lock after profile build / active mode."""

from __future__ import annotations

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from module2.ueba_service import (
    PROMPT_TARGET_LOCKED_ACTIVE,
    PROMPT_TARGET_LOCKED_PROFILE,
    assert_prompt_target_change_allowed,
    prompt_target_lock_state,
)


class PromptTargetLockTests(TestCase):
    def test_unlocked_when_no_profiles_or_active_keys(self):
        org = SimpleNamespace(id=1)
        settings = SimpleNamespace(behavior_profile_prompt_target=50, organization=org)

        with (
            patch("module2.models.ApiKeyBehaviorProfile.objects") as profiles,
            patch("core.models.GatewayAPIKey.objects") as keys,
        ):
            profiles.filter.return_value.count.return_value = 0
            keys.filter.return_value.count.return_value = 0
            lock = prompt_target_lock_state(org, settings)

        self.assertFalse(lock["prompt_target_locked"])
        self.assertEqual(lock["prompt_target_lock_reason"], "")

    def test_locked_when_profile_built(self):
        org = SimpleNamespace(id=1)
        settings = SimpleNamespace(behavior_profile_prompt_target=50, organization=org)

        with (
            patch("module2.models.ApiKeyBehaviorProfile.objects") as profiles,
            patch("core.models.GatewayAPIKey.objects") as keys,
        ):
            profiles.filter.return_value.count.return_value = 2
            keys.filter.return_value.count.return_value = 0
            lock = prompt_target_lock_state(org, settings)

        self.assertTrue(lock["prompt_target_locked"])
        self.assertEqual(lock["prompt_target_lock_reason"], PROMPT_TARGET_LOCKED_PROFILE)
        self.assertEqual(lock["built_profile_count"], 2)

    def test_locked_when_active_keys_only(self):
        org = SimpleNamespace(id=1)
        settings = SimpleNamespace(behavior_profile_prompt_target=50, organization=org)

        with (
            patch("module2.models.ApiKeyBehaviorProfile.objects") as profiles,
            patch("core.models.GatewayAPIKey.objects") as keys,
        ):
            profiles.filter.return_value.count.return_value = 0
            keys.filter.return_value.count.return_value = 1
            lock = prompt_target_lock_state(org, settings)

        self.assertTrue(lock["prompt_target_locked"])
        self.assertEqual(lock["prompt_target_lock_reason"], PROMPT_TARGET_LOCKED_ACTIVE)

    def test_assert_blocks_target_change_when_locked(self):
        org = SimpleNamespace(id=1)
        settings = SimpleNamespace(behavior_profile_prompt_target=50, organization=org)
        with patch(
            "module2.ueba_service.prompt_target_lock_state",
            return_value={
                "prompt_target_locked": True,
                "prompt_target_lock_reason": PROMPT_TARGET_LOCKED_PROFILE,
                "built_profile_count": 1,
                "active_key_count": 0,
            },
        ):
            with self.assertRaises(ValueError) as ctx:
                assert_prompt_target_change_allowed(settings, 75)
        self.assertIn("already built", str(ctx.exception).lower())

    def test_assert_allows_same_target_when_locked(self):
        org = SimpleNamespace(id=1)
        settings = SimpleNamespace(behavior_profile_prompt_target=50, organization=org)
        with patch(
            "module2.ueba_service.prompt_target_lock_state",
            return_value={
                "prompt_target_locked": True,
                "prompt_target_lock_reason": PROMPT_TARGET_LOCKED_PROFILE,
                "built_profile_count": 1,
                "active_key_count": 0,
            },
        ):
            assert_prompt_target_change_allowed(settings, 50)
