"""Tests for compliant fallback-chain precomputation."""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running without full Django test runner discovery path.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.routing_fallback import (
    FALLBACK_CHAIN_SCHEMA_VERSION,
    build_compliant_fallback_chains,
    fallback_profile_key,
)


def _routing_entry(
    name: str,
    *,
    sensitivity: str = "public",
    tags: list[str] | None = None,
    priority: int = 0,
):
    return {
        "model_name": name,
        "model_id": name,
        "is_active": True,
        "data_sensitivity_level": sensitivity,
        "compliance_tags": tags or [],
        "routing_priority": priority,
    }


def test_fallback_profile_key_stable():
    assert fallback_profile_key("confidential", ["hipaa", "soc2"]) == "confidential|hipaa,soc2"
    assert fallback_profile_key("RESTRICTED", ["HIPAA"]) == fallback_profile_key("restricted", ["hipaa"])


def test_build_chains_case_insensitive_compliance_tags():
    entries = [
        _routing_entry("hipaa-model", sensitivity="restricted", tags=["HIPAA"], priority=10),
        _routing_entry("public-model", sensitivity="public"),
    ]
    payload = build_compliant_fallback_chains(entries)
    chain = payload["chains"][fallback_profile_key("restricted", ["hipaa"])]
    assert chain[0] == "hipaa-model"


def test_build_chains_respects_sensitivity_and_tags():
    entries = [
        _routing_entry("public-model", sensitivity="public"),
        _routing_entry("hipaa-model", sensitivity="confidential", tags=["hipaa"], priority=10),
        _routing_entry("other", sensitivity="confidential", tags=["soc2"]),
    ]
    payload = build_compliant_fallback_chains(entries)
    assert payload["version"] == FALLBACK_CHAIN_SCHEMA_VERSION
    assert "content_hash" in payload

    hipaa_chain = payload["chains"][fallback_profile_key("confidential", ["hipaa"])]
    assert hipaa_chain[0] == "hipaa-model"
    assert "public-model" not in hipaa_chain

    per_primary = payload["per_primary"]
    assert "hipaa-model" in per_primary
    assert "hipaa-model" not in per_primary["hipaa-model"]
    # Single-tag profile generated from multi-tag model.
    assert fallback_profile_key("confidential", ["hipaa"]) in payload["chains"]
