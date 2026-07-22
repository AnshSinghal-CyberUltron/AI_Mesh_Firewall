"""PIPELINE-0030: BYOK model ids must not collapse to zeroshield-model."""
from __future__ import annotations

from policy.security_views import _canonicalize_model_name


def test_nemotron_120b_byok_not_collapsed():
    name = "nvidia/nemotron-3-super-120b-a12b:free"
    assert _canonicalize_model_name(name) == name


def test_platform_guard_still_collapsed():
    assert _canonicalize_model_name("zeroshield-guard-120b") == "zeroshield-model"
    assert _canonicalize_model_name("bedrock-gpt-oss-120b") == "zeroshield-model"
