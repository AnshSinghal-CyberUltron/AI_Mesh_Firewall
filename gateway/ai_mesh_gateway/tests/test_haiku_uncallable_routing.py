"""zs-4e05841e948c: smart-mask ALLOW must not route to a display-alias / guard model.

Live Mesh Attack Simulator sent an already-masked PII prompt. input_scan ALLOW +
redact_noop (PIPELINE-0012). Routing then scored only Haiku / ZeroShield Model /
gpt-5.2 because request_risk=0.85 (scanner confidence) and the guard display name
slipped past _is_guard_only_model. LiteLLM 404'd Haiku in ~7ms.

These tests lock the three stacked causes — not the sanitized 503 string.
"""
from __future__ import annotations

from types import SimpleNamespace

from llm_router import LLMRouter, looks_like_provider_model_id


def _entry(**over):
    row = {
        "model_name": "gemini-flash-cheap",
        "model_id": "google/gemini-2.0-flash",
        "provider": "openai",
        "is_active": True,
        "api_key_set": True,
        "cost_per_1k_input_tokens": 0.00005,
        "latency_sla_ms": 2000,
        "routing_priority": 40,
        "risk_score": 0.40,
        "data_sensitivity_level": "public",
        "compliance_tags": [],
    }
    row.update(over)
    return row


ZS_CATALOG = [
    _entry(
        model_name="Haiku",
        model_id="Haiku",
        cost_per_1k_input_tokens=0.00001,
        risk_score=0.05,
        routing_priority=10,
    ),
    _entry(
        model_name="ZeroShield Model",
        model_id="zeroshield-model",
        provider="internal",
        cost_per_1k_input_tokens=0.0,
        risk_score=0.0,
        routing_priority=0,
    ),
    _entry(
        model_name="gpt-5.2",
        model_id="gpt-5.2",
        cost_per_1k_input_tokens=0.003,
        risk_score=0.10,
        routing_priority=95,
    ),
    _entry(
        model_name="gemini-flash-cheap",
        model_id="google/gemini-2.0-flash",
        cost_per_1k_input_tokens=0.00005,
        risk_score=0.40,
        routing_priority=40,
    ),
]


def _router(names=None):
    r = LLMRouter.__new__(LLMRouter)
    r._config = {"litellm_default_model": ""}
    names = names or [m["model_name"] for m in ZS_CATALOG]
    r._active_model_names = list(names)
    r._qualified_model_names = set(names)
    r._unroutable_model_names = set()
    r._deployment_params = {}
    return r


def test_haiku_display_alias_is_not_a_provider_model_id():
    assert looks_like_provider_model_id("Haiku") is False
    assert looks_like_provider_model_id("ZeroShield Model") is False
    assert looks_like_provider_model_id("google/gemini-2.0-flash") is True
    assert looks_like_provider_model_id("gpt-5.2") is True
    assert looks_like_provider_model_id("gemini-flash-cheap") is True
    assert looks_like_provider_model_id("gpt-4o-alt") is True


def test_guard_display_name_is_not_an_inference_candidate():
    from ai_mesh_gateway.main import _filter_inference_eligible_models, _is_guard_only_model

    assert _is_guard_only_model({"model_name": "ZeroShield Model"}) is True
    assert _is_guard_only_model({"model_name": "zeroshield-model"}) is True
    names = {
        str(m.get("model_name"))
        for m in _filter_inference_eligible_models(ZS_CATALOG)
    }
    assert "ZeroShield Model" not in names
    assert "Haiku" not in names
    assert "gemini-flash-cheap" in names


def test_allow_pii_smart_mask_does_not_inflate_request_risk(monkeypatch):
    """FIX-1.5c: allow verdicts are near-zero risk, even when threat_type=pii."""
    from unittest.mock import MagicMock

    from ai_mesh_gateway.main import _extract_chat_routing_preferences
    import ai_mesh_gateway.main as gm

    router = MagicMock()
    router.estimate_prompt_tokens.return_value = 12
    monkeypatch.setattr(gm, "LLM_ROUTER", router)
    monkeypatch.setattr(gm, "CONFIG", {})

    verdict = SimpleNamespace(
        action="allow",
        threat_type="pii",
        confidence=0.85,
        matched_patterns=["email_smart_masked"],
    )
    prefs = _extract_chat_routing_preferences(
        {"model": "Haiku", "messages": [{"role": "user", "content": "hi"}]},
        {"routing_enabled": True},
        None,
        verdict,
    )
    assert prefs["request_risk_score"] == 0.0


def test_redact_pii_smart_mask_does_not_inflate_request_risk(monkeypatch):
    """Tier-1 smart-mask verdict.action is redact; that must not shrink the pool."""
    from unittest.mock import MagicMock

    from ai_mesh_gateway.main import _extract_chat_routing_preferences
    import ai_mesh_gateway.main as gm

    router = MagicMock()
    router.estimate_prompt_tokens.return_value = 12
    monkeypatch.setattr(gm, "LLM_ROUTER", router)
    monkeypatch.setattr(gm, "CONFIG", {})

    verdict = SimpleNamespace(
        action="redact",
        threat_type="pii",
        confidence=0.85,
        matched_patterns=["email_smart_masked"],
    )
    prefs = _extract_chat_routing_preferences(
        {"model": "Haiku", "messages": [{"role": "user", "content": "hi"}]},
        {"routing_enabled": True},
        None,
        verdict,
    )
    assert prefs["request_risk_score"] == 0.0


def test_block_pii_does_not_inflate_routing_risk(monkeypatch):
    from unittest.mock import MagicMock

    from ai_mesh_gateway.main import _extract_chat_routing_preferences
    import ai_mesh_gateway.main as gm

    router = MagicMock()
    router.estimate_prompt_tokens.return_value = 12
    monkeypatch.setattr(gm, "LLM_ROUTER", router)
    monkeypatch.setattr(gm, "CONFIG", {})

    verdict = SimpleNamespace(
        action="block",
        threat_type="pii",
        confidence=0.85,
        matched_patterns=["ssn"],
    )
    prefs = _extract_chat_routing_preferences(
        {"model": "Haiku", "messages": [{"role": "user", "content": "hi"}]},
        {"routing_enabled": True},
        None,
        verdict,
    )
    assert prefs["request_risk_score"] == 0.0


def test_jailbreak_block_still_raises_request_risk(monkeypatch):
    from unittest.mock import MagicMock

    from ai_mesh_gateway.main import _extract_chat_routing_preferences
    import ai_mesh_gateway.main as gm

    router = MagicMock()
    router.estimate_prompt_tokens.return_value = 12
    monkeypatch.setattr(gm, "LLM_ROUTER", router)
    monkeypatch.setattr(gm, "CONFIG", {})

    verdict = SimpleNamespace(
        action="block",
        threat_type="jailbreak",
        confidence=0.85,
        matched_patterns=["jailbreak"],
    )
    prefs = _extract_chat_routing_preferences(
        {"model": "Haiku", "messages": [{"role": "user", "content": "hi"}]},
        {"routing_enabled": True},
        None,
        verdict,
    )
    assert prefs["request_risk_score"] == 0.85


def test_display_alias_name_skipped_even_if_model_id_has_hyphens():
    """Live catalog named the row Haiku while model_id looked slug-like."""
    from ai_mesh_gateway.main import _filter_inference_eligible_models

    catalog = ZS_CATALOG + [
        _entry(
            model_name="Haiku",
            model_id="claude-haiku-20240307",
            cost_per_1k_input_tokens=0.00001,
            risk_score=0.05,
        )
    ]
    names = {str(m.get("model_name")) for m in _filter_inference_eligible_models(catalog)}
    assert "Haiku" not in names
    r = _router()
    sel = r.select_model(
        routing_models=_filter_inference_eligible_models(catalog),
        request_risk_score=0.0,
        weights={"risk": 0.15, "cost": 0.50, "latency": 0.20, "priority": 0.15},
    )
    scored = [c["model_name"] for c in ((sel.candidate_scores if sel else None) or [])]
    assert "Haiku" not in scored


def test_cataloged_display_alias_is_known_to_org_even_when_uncallable():
    """Attack Simulator Haiku 404: the unknown-model gate must use the FULL catalog.

    Filtering Haiku out of inference_models is correct (LiteLLM cannot serve it).
    Treating that filtered set as 'org identities' 404s a connected alias, the
    browser logs POST /v1/chat/completions 404, and rewriteChatBodyModelAuto
    then stamps requested_model=auto so Model Routing shows Allow not Reroute.
    """
    from ai_mesh_gateway.main import (
        _concrete_requested_model_absent_from_catalog,
        _filter_inference_eligible_models,
    )

    eligible = _filter_inference_eligible_models(ZS_CATALOG)
    assert "Haiku" not in {str(m.get("model_name")) for m in eligible}
    assert _concrete_requested_model_absent_from_catalog("Haiku", eligible) is True
    assert _concrete_requested_model_absent_from_catalog("Haiku", ZS_CATALOG) is False
    assert _concrete_requested_model_absent_from_catalog("ZeroShield Model", ZS_CATALOG) is False
    assert _concrete_requested_model_absent_from_catalog("gpt-99-omniscient", ZS_CATALOG) is True
    assert _concrete_requested_model_absent_from_catalog("auto", ZS_CATALOG) is False
    assert _concrete_requested_model_absent_from_catalog("", ZS_CATALOG) is False


def test_auto_body_keeps_dropdown_preferred_model_hint(monkeypatch):
    """FE rewrite to model=auto must not erase Haiku as the operator's preference."""
    from unittest.mock import MagicMock

    from ai_mesh_gateway.main import _extract_chat_routing_preferences
    import ai_mesh_gateway.main as gm

    router = MagicMock()
    router.estimate_prompt_tokens.return_value = 12
    monkeypatch.setattr(gm, "LLM_ROUTER", router)
    monkeypatch.setattr(gm, "CONFIG", {})

    prefs = _extract_chat_routing_preferences(
        {
            "model": "auto",
            "messages": [{"role": "user", "content": "hi"}],
            "routing_preferences": {
                "enable_routing": True,
                "preferred_model": "Haiku",
            },
        },
        {"routing_enabled": True},
        None,
        None,
    )
    assert prefs["preferred_model"] == "Haiku"
    assert prefs["routing_enabled"] is True


def test_routing_on_unknown_gate_uses_full_catalog_not_inference_filter():
    from pathlib import Path

    import ai_mesh_gateway.main as gm

    src = Path(gm.__file__).read_text()
    marker = "Cataloged-but-uncallable aliases"
    idx = src.find(marker)
    assert idx > 0, "uncallable-alias remap comment missing"
    window = src[idx: idx + 800]
    assert "_concrete_requested_model_absent_from_catalog(" in window
    assert "routing_models" in window
    assert "_routing_identity_set(inference_models)" not in window


def test_high_risk_pool_never_selects_haiku_or_guard():
    from ai_mesh_gateway.main import _filter_inference_eligible_models

    r = _router()
    eligible = _filter_inference_eligible_models(ZS_CATALOG)
    sel = r.select_model(
        routing_models=eligible,
        request_risk_score=0.85,
        weights={"risk": 0.15, "cost": 0.50, "latency": 0.20, "priority": 0.15},
    )
    assert sel is not None
    assert sel.model_name not in {"Haiku", "ZeroShield Model"}
    assert sel.model_name in {"gemini-flash-cheap", "gpt-5.2"}
    scored_names = [c["model_name"] for c in (sel.candidate_scores or [])]
    assert "Haiku" not in scored_names
    assert "ZeroShield Model" not in scored_names
