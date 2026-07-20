"""Differentiated routing metadata profiles for org LLM catalogs.

Used by ``differentiate_routing_catalog`` so weight extremes (cost/risk/latency/
priority) and sensitivity floors produce distinct winners instead of tied scores
that collapse to list-order (north-mini).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any


# Stable profiles keyed by coarse lane. Matching is by substring on model_name
# (lowercased). First match wins; unmatched free/unknown models get balanced.
ROUTING_PROFILE_RULES: list[tuple[tuple[str, ...], str]] = [
    # Cheap / fast free lane
    (("north-mini", "mini-code", ":free", "free/", "grok-code-fast"), "cheap_fast"),
    # Safe / elevated sensitivity
    (("claude", "anthropic", "sonar", "gemini-2.5-pro", "o3", "o4"), "safe_sensitive"),
    # Paid / strong capability
    (("gpt-5", "gpt-4", "o1", "deepseek-r1", "qwen3", "nemotron"), "paid_strong"),
    # Mid / balanced defaults
    (("llama", "mistral", "gemma", "phi", "qwen"), "balanced"),
]

ROUTING_PROFILES: dict[str, dict[str, Any]] = {
    "cheap_fast": {
        "cost_per_1k_input_tokens": Decimal("0.000010"),
        "cost_per_1k_output_tokens": Decimal("0.000020"),
        "latency_sla_ms": 800,
        "risk_score": 0.35,
        "routing_priority": 40,
        "data_sensitivity_level": "public",
    },
    "balanced": {
        "cost_per_1k_input_tokens": Decimal("0.000500"),
        "cost_per_1k_output_tokens": Decimal("0.001500"),
        "latency_sla_ms": 2500,
        "risk_score": 0.18,
        "routing_priority": 70,
        "data_sensitivity_level": "internal",
    },
    "safe_sensitive": {
        "cost_per_1k_input_tokens": Decimal("0.003000"),
        "cost_per_1k_output_tokens": Decimal("0.015000"),
        "latency_sla_ms": 4000,
        "risk_score": 0.05,
        "routing_priority": 90,
        "data_sensitivity_level": "confidential",
    },
    "paid_strong": {
        "cost_per_1k_input_tokens": Decimal("0.010000"),
        "cost_per_1k_output_tokens": Decimal("0.030000"),
        "latency_sla_ms": 6000,
        "risk_score": 0.12,
        "routing_priority": 80,
        "data_sensitivity_level": "internal",
    },
}


def resolve_routing_profile(model_name: str) -> str:
    """Return profile key for a model name (never raises)."""
    name = (model_name or "").strip().lower()
    if not name:
        return "balanced"
    # Platform / internal models keep elevated sensitivity, not inference lanes.
    if name.startswith("zeroshield") or "guard" in name:
        return "safe_sensitive"
    for needles, profile in ROUTING_PROFILE_RULES:
        if any(n in name for n in needles):
            return profile
    # Explicit free suffix without earlier match
    if name.endswith(":free") or "/free" in name:
        return "cheap_fast"
    return "balanced"


def profile_fields(profile_key: str) -> dict[str, Any]:
    return dict(ROUTING_PROFILES.get(profile_key) or ROUTING_PROFILES["balanced"])


def _stable_lane_offset(model_name: str) -> int:
    """0..9 stable offset so same-lane models do not score-tie on list order."""
    h = 0
    for ch in (model_name or "").lower():
        h = (h * 33 + ord(ch)) & 0xFFFFFFFF
    return h % 10


def differentiate_model_defaults(model_name: str) -> dict[str, Any]:
    """Idempotent field dict suitable for LLMModelConfig.update(**fields)."""
    key = resolve_routing_profile(model_name)
    fields = profile_fields(key)
    off = _stable_lane_offset(model_name)
    # Keep lane identity but break exact ties within a lane (preference-stable).
    base_in = fields["cost_per_1k_input_tokens"]
    fields["cost_per_1k_input_tokens"] = base_in + Decimal(off) * Decimal("0.000001")
    fields["cost_per_1k_output_tokens"] = (
        fields["cost_per_1k_output_tokens"] + Decimal(off) * Decimal("0.000002")
    )
    fields["latency_sla_ms"] = int(fields["latency_sla_ms"]) + off * 25
    fields["routing_priority"] = max(0, int(fields["routing_priority"]) - off)
    # risk_score stays within [0, 1]
    risk = float(fields["risk_score"]) + off * 0.005
    fields["risk_score"] = round(min(0.99, max(0.0, risk)), 4)
    fields["_profile"] = key
    return fields
