"""Regression: reload_models must NOT rebuild LiteLLMRouter when the model list is
unchanged — repeated rebuilds re-register litellm callbacks and hit MAX_CALLBACKS.
"""
from unittest.mock import MagicMock, patch

from ai_mesh_gateway.llm_router import LLMRouter


def _make_router():
    r = LLMRouter({"org_only_inference": True})
    # Bypass validation/fallback/active-name machinery so we isolate the dedupe
    # short-circuit that guards the LiteLLMRouter(...) construction.
    r._filter_valid_reload_models = lambda ml: (list(ml), [])
    r._build_fallbacks = lambda vm: []
    r._set_active_model_names = lambda vm: None
    return r


_ENTRY = {"model_name": "m1", "litellm_params": {"model": "openai/x", "api_key": "k"}, "_zs_org": "org1"}
_ENTRY2 = {"model_name": "m2", "litellm_params": {"model": "openai/y", "api_key": "k"}, "_zs_org": "org1"}


def test_reload_models_skips_rebuild_when_unchanged():
    r = _make_router()
    with patch("ai_mesh_gateway.llm_router.LiteLLMRouter", MagicMock()) as MockRouter:
        r.reload_models([dict(_ENTRY)])
        assert MockRouter.call_count == 1, "first reload should build the router"
        # identical list (fresh dict, same content) -> must short-circuit
        r.reload_models([dict(_ENTRY)])
        assert MockRouter.call_count == 1, "unchanged list must NOT rebuild (callback leak)"
        # a genuinely different list -> must rebuild
        r.reload_models([dict(_ENTRY2)])
        assert MockRouter.call_count == 2, "changed list must rebuild"


def test_reload_models_retries_after_failed_build():
    r = _make_router()
    with patch("ai_mesh_gateway.llm_router.LiteLLMRouter", side_effect=RuntimeError("boom")) as MockRouter:
        r.reload_models([dict(_ENTRY)])  # build raises -> caught, sig NOT cached
        assert MockRouter.call_count == 1
        r.reload_models([dict(_ENTRY)])  # same list must be retried, not skipped
        assert MockRouter.call_count == 2, "a failed build must not cache the signature"
