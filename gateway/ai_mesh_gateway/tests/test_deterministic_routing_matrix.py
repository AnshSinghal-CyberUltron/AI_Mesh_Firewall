"""Deterministic model routing — the permutation matrix.

Locks in the properties the LLM adjudicator used to paper over:

  * every Strategy Preset selects the model its weights actually imply, using
    PRODUCTION-REALISTIC USD/1k prices and the production 30000 ms latency budget
    (the pre-existing suite only "worked" with ~1000x-inflated prices and an
    artificially tight budget — see the fixture audit in
    docs/plans/2026-08-18-deterministic-model-routing.md);
  * identical inputs always produce the identical winner, regardless of candidate
    list order (the old stable-sort resolved ties by Redis sync order);
  * compliance tags are matched CANONICALLY across all six frameworks, so casing and
    separator drift ("hipaa" vs "HIPAA", "PCI-DSS" vs "pci_dss") cannot manufacture a
    spurious "no compliant model" 403;
  * data sensitivity is a HARD floor (D3) — never a soft fallback;
  * no LLM is reachable from the routing decision at any weight vector.
"""
from __future__ import annotations

import itertools
import random
from unittest.mock import patch

import pytest

from llm_router import LLMRouter, canonical_compliance_tag


# ───────────────────────── realistic catalogue ─────────────────────────
# USD per 1k input tokens, real 2026 list prices. Deliberately spans 3 orders of
# magnitude (1.9e-05 .. 3.0e-03) exactly like a real OpenRouter/BYOK catalogue.
def _m(name, *, cost, sla, prio, risk, sens="public", tags=()):
    return {
        "model_name": name,
        "model_id": f"prov/{name}",
        "provider": "custom",
        "is_active": True,
        "api_key_set": True,
        "cost_per_1k_input_tokens": cost,
        "latency_sla_ms": sla,
        "routing_priority": prio,
        "risk_score": risk,
        "data_sensitivity_level": sens,
        "compliance_tags": list(tags),
    }


#                        cheapest  fastest  top-prio  safest
CATALOGUE = [
    _m("mistral-nemo",   cost=0.000019, sla=900,  prio=20, risk=0.45),
    _m("llama-3.2-1b",   cost=0.000027, sla=600,  prio=15, risk=0.50),
    _m("gpt-4o-mini",    cost=0.000150, sla=1500, prio=60, risk=0.20, sens="internal"),
    _m("deepseek-chat",  cost=0.000257, sla=3000, prio=55, risk=0.30, sens="internal"),
    _m("gpt-4o",         cost=0.002500, sla=5000, prio=90, risk=0.10, sens="confidential"),
    _m("claude-sonnet-4", cost=0.003000, sla=6000, prio=95, risk=0.05, sens="restricted"),
]

PRESETS = {
    "Balanced":         {"risk": 0.30, "cost": 0.20, "latency": 0.20, "priority": 0.30},
    "Cost Optimized":   {"risk": 0.15, "cost": 0.50, "latency": 0.20, "priority": 0.15},
    "Low Latency":      {"risk": 0.15, "cost": 0.15, "latency": 0.55, "priority": 0.15},
    "Maximum Security": {"risk": 0.55, "cost": 0.10, "latency": 0.10, "priority": 0.25},
    "Quality First":    {"risk": 0.20, "cost": 0.10, "latency": 0.10, "priority": 0.60},
}

FRAMEWORKS = ["SOC2", "ISO27001", "HIPAA", "GDPR", "PCI-DSS", "NIST"]


def _router(active=None) -> LLMRouter:
    """Real scoring code without litellm/network init."""
    r = LLMRouter.__new__(LLMRouter)
    r._config = {"litellm_default_model": ""}
    names = [m["model_name"] for m in CATALOGUE] if active is None else list(active)
    r._active_model_names = names
    r._qualified_model_names = set(names)
    r._unroutable_model_names = set()
    return r


def _pick(models=None, **kw):
    sel = _router().select_model(routing_models=models or CATALOGUE, **kw)
    return sel


# ───────────────────────── preset differentiation ─────────────────────────
# NOTE on what is asserted here. A weighted SUM is the right model for governance
# weights, but it means a preset is not a dictatorship: "Low Latency" at 55% still
# leaves 45% on the other dimensions, so a model that is fastest but also worst on
# risk AND priority can legitimately lose. Asserting an absolute winner per preset
# therefore over-fits the fixture. The real, catalogue-independent guarantees are
# RELATIVE — a cost-biased preset must land on something cheaper than a
# quality-biased one, and so on. Those are asserted here; absolute per-dimension
# behaviour is pinned separately with weight=1.0 and with an independent catalogue.
_BY_NAME = {m["model_name"]: m for m in CATALOGUE}


def _winner(preset: str) -> dict:
    return _BY_NAME[_pick(weights=PRESETS[preset]).model_name]


def test_presets_do_not_all_collapse_to_one_model():
    """The headline defect: all five presets used to select the SAME model."""
    winners = {name: _pick(weights=w).model_name for name, w in PRESETS.items()}
    assert len(set(winners.values())) >= 2, (
        f"presets are not differentiating at all — winners: {winners}"
    )


def test_cost_preset_lands_cheaper_than_quality_preset():
    assert (_winner("Cost Optimized")["cost_per_1k_input_tokens"]
            < _winner("Quality First")["cost_per_1k_input_tokens"])


def test_latency_preset_lands_faster_than_quality_preset():
    assert (_winner("Low Latency")["latency_sla_ms"]
            < _winner("Quality First")["latency_sla_ms"])


def test_security_preset_lands_safer_than_cost_preset():
    assert (_winner("Maximum Security")["risk_score"]
            < _winner("Cost Optimized")["risk_score"])


def test_quality_preset_lands_higher_priority_than_cost_preset():
    assert (_winner("Quality First")["routing_priority"]
            > _winner("Cost Optimized")["routing_priority"])


# When the catalogue's dimensions are INDEPENDENT (each model best at exactly one
# thing), every preset must land on its own dimension's champion. This is the crisp
# differentiation proof the correlated real-world catalogue above cannot give.
INDEPENDENT = [
    _m("champ-cost",     cost=0.000001, sla=5000, prio=50, risk=0.50),
    _m("champ-latency",  cost=0.002000, sla=100,  prio=50, risk=0.50),
    _m("champ-priority", cost=0.002000, sla=5000, prio=99, risk=0.50),
    _m("champ-risk",     cost=0.002000, sla=5000, prio=50, risk=0.01),
]


@pytest.mark.parametrize("preset,expected", [
    ("Cost Optimized", "champ-cost"),
    ("Low Latency", "champ-latency"),
    ("Quality First", "champ-priority"),
    ("Maximum Security", "champ-risk"),
])
def test_presets_select_their_own_champion_when_dimensions_are_independent(preset, expected):
    sel = _router().select_model(routing_models=INDEPENDENT, weights=PRESETS[preset])
    assert sel.model_name == expected, (
        f"{preset} -> {sel.model_name} (expected {expected})"
    )


@pytest.mark.parametrize("dim,expected", [
    ("cost", "mistral-nemo"),
    ("latency", "llama-3.2-1b"),
    ("priority", "claude-sonnet-4"),
    ("risk", "claude-sonnet-4"),
])
def test_each_dimension_alone_selects_its_own_winner_at_production_budget(dim, expected):
    """No artificially tight latency_budget_ms — the production 30000 ms default."""
    weights = {k: 0.0 for k in ("risk", "cost", "latency", "priority")}
    weights[dim] = 1.0
    sel = _pick(weights=weights, latency_budget_ms=30000)
    assert sel.model_name == expected


def test_cost_dimension_is_not_inert_at_realistic_prices():
    """RC-1a: cost_component used to span 0.995..0.999995 — a dead dimension."""
    scored = _router()._score_routing_models(CATALOGUE, weights=PRESETS["Balanced"])
    spread = max(s["cost_component"] for s in scored) - min(s["cost_component"] for s in scored)
    assert spread > 0.9, f"cost dimension is inert (spread={spread})"


def test_latency_dimension_is_not_inert_at_production_budget():
    """RC-1b: latency_component was exactly 1.0 for every model (spread 0.000000)."""
    scored = _router()._score_routing_models(
        CATALOGUE, weights=PRESETS["Balanced"], latency_budget_ms=30000
    )
    spread = max(s["latency_component"] for s in scored) - min(s["latency_component"] for s in scored)
    assert spread > 0.9, f"latency dimension is inert (spread={spread})"


# ───────────────────────── determinism ─────────────────────────
def test_identical_inputs_are_stable_across_200_repeats():
    first = _pick(weights=PRESETS["Balanced"]).model_name
    for _ in range(200):
        assert _pick(weights=PRESETS["Balanced"]).model_name == first


@pytest.mark.parametrize("preset", list(PRESETS))
def test_winner_is_independent_of_candidate_list_order(preset):
    """RC-4: a stable sort resolved ties by input order == Redis sync order."""
    rng = random.Random(1234)
    baseline = _pick(weights=PRESETS[preset]).model_name
    for _ in range(50):
        shuffled = CATALOGUE[:]
        rng.shuffle(shuffled)
        got = _router().select_model(routing_models=shuffled, weights=PRESETS[preset]).model_name
        assert got == baseline, f"{preset} order-dependent: {got} != {baseline}"


def test_all_default_catalogue_still_breaks_ties_deterministically():
    """Every dimension identical -> total ordering must still pick one stable winner."""
    flat = [
        _m(n, cost=0.0, sla=30000, prio=0, risk=0.0)
        for n in ("zeta", "alpha", "middle", "beta")
    ]
    rng = random.Random(99)
    winners = set()
    for _ in range(50):
        shuffled = flat[:]
        rng.shuffle(shuffled)
        winners.add(_router().select_model(routing_models=shuffled).model_name)
    assert winners == {"alpha"}, f"tie-break not deterministic: {winners}"


def test_degenerate_single_candidate_scores_cleanly():
    sel = _pick(models=[CATALOGUE[0]])
    assert sel is not None and sel.candidate_count == 1
    assert 0.0 <= sel.score <= 1.0


# ───────────────────────── compliance, all six frameworks ─────────────────────────
@pytest.mark.parametrize("raw,canon", [
    ("soc2", "SOC2"), ("SOC 2", "SOC2"), ("soc_2", "SOC2"),
    ("iso27001", "ISO27001"), ("ISO-27001", "ISO27001"), ("iso_27001", "ISO27001"),
    ("hipaa", "HIPAA"), ("HIPAA", "HIPAA"), ("Hippa", "HIPAA"),
    ("gdpr", "GDPR"), ("GDPR", "GDPR"),
    ("pci-dss", "PCI_DSS"), ("PCI_DSS", "PCI_DSS"), ("pci dss", "PCI_DSS"), ("PCI", "PCI_DSS"),
    ("nist", "NIST"), ("NIST-CSF", "NIST"), ("nist_800_53", "NIST"),
])
def test_compliance_tag_canonicalization(raw, canon):
    assert canonical_compliance_tag(raw) == canon


@pytest.mark.parametrize("framework", FRAMEWORKS)
def test_compliance_tag_is_a_hard_filter_per_framework(framework):
    tagged = _m("compliant-one", cost=0.001, sla=2000, prio=50, risk=0.2, tags=[framework])
    models = CATALOGUE + [tagged]
    sel = _router().select_model(
        routing_models=models,
        required_compliance=[framework],
        weights={"risk": 0, "cost": 1, "latency": 0, "priority": 0},
    )
    assert sel is not None and sel.model_name == "compliant-one"


@pytest.mark.parametrize("framework", FRAMEWORKS)
def test_compliance_matching_survives_case_and_separator_drift(framework):
    """RC-8: operator types one casing, client sends another. Must still match."""
    tagged = _m("compliant-one", cost=0.001, sla=2000, prio=50, risk=0.2,
                tags=[framework.lower().replace("-", "_")])
    sel = _router().select_model(
        routing_models=CATALOGUE + [tagged],
        required_compliance=[framework.upper().replace("_", "-")],
        weights={"risk": 0, "cost": 1, "latency": 0, "priority": 0},
    )
    assert sel is not None and sel.model_name == "compliant-one", (
        f"{framework}: canonical matching failed"
    )


@pytest.mark.parametrize("framework", FRAMEWORKS)
def test_unsatisfiable_compliance_fails_closed_per_framework(framework):
    assert _router().select_model(
        routing_models=CATALOGUE, required_compliance=[framework]
    ) is None


@pytest.mark.parametrize("combo", [c for n in (2, 3) for c in itertools.combinations(FRAMEWORKS, n)][:20])
def test_multi_framework_requires_superset(combo):
    """A model must carry ALL requested frameworks, not merely one of them."""
    partial = _m("partial", cost=0.0001, sla=500, prio=99, risk=0.0, tags=[combo[0]])
    full = _m("full", cost=0.002, sla=4000, prio=10, risk=0.4, tags=list(combo))
    sel = _router().select_model(
        routing_models=[partial, full],
        required_compliance=list(combo),
        weights={"risk": 0, "cost": 1, "latency": 0, "priority": 0},
    )
    assert sel is not None and sel.model_name == "full", (
        f"{combo}: partial-match model was accepted"
    )


# ───────────────────────── sensitivity is a hard floor (D3) ─────────────────────────
@pytest.mark.parametrize("level,allowed", [
    ("public", {"mistral-nemo", "llama-3.2-1b", "gpt-4o-mini", "deepseek-chat", "gpt-4o", "claude-sonnet-4"}),
    ("internal", {"gpt-4o-mini", "deepseek-chat", "gpt-4o", "claude-sonnet-4"}),
    ("confidential", {"gpt-4o", "claude-sonnet-4"}),
    ("restricted", {"claude-sonnet-4"}),
])
def test_sensitivity_floor_restricts_the_candidate_pool(level, allowed):
    for preset in PRESETS.values():
        sel = _pick(weights=preset, data_sensitivity=level)
        assert sel is not None
        assert sel.model_name in allowed, f"{level} floor leaked {sel.model_name}"


def test_sensitivity_unsatisfiable_returns_none_not_a_fallback():
    public_only = [m for m in CATALOGUE if m["data_sensitivity_level"] == "public"]
    assert _router().select_model(
        routing_models=public_only, data_sensitivity="restricted"
    ) is None


def test_unknown_sensitivity_value_fails_closed():
    """An unrecognised level must be treated as MOST restrictive, never as public."""
    public_only = [m for m in CATALOGUE if m["data_sensitivity_level"] == "public"]
    assert _router().select_model(
        routing_models=public_only, data_sensitivity="topsecret"
    ) is None


# ───────────────────────── soft constraints degrade, not fail ─────────────────────────
def test_latency_budget_unsatisfiable_degrades_and_reports():
    sel = _pick(weights=PRESETS["Balanced"], latency_budget_ms=1)
    assert sel is not None, "an impossible latency budget must degrade, not 503"
    assert "latency_budget_unsatisfiable" in sel.decision_factors


def test_latency_budget_is_a_hard_filter_when_satisfiable():
    """Models slower than the budget are excluded outright, not merely penalised."""
    sel = _pick(weights={"risk": 0, "cost": 0, "latency": 0, "priority": 1},
                latency_budget_ms=1000)
    assert sel.model_name in {"mistral-nemo", "llama-3.2-1b"}, sel.model_name


def test_high_request_risk_excludes_risky_models():
    sel = _pick(weights={"risk": 0, "cost": 1, "latency": 0, "priority": 0},
                request_risk_score=0.9)
    assert sel is not None
    assert sel.model_name in {"gpt-4o", "claude-sonnet-4"}, (
        f"high-risk request served by risky model {sel.model_name}"
    )


def test_low_request_risk_does_not_constrain_candidacy():
    sel = _pick(weights={"risk": 0, "cost": 1, "latency": 0, "priority": 0},
                request_risk_score=0.1)
    assert sel.model_name == "mistral-nemo"


# ───────────────────────── router servability (RC-7) ─────────────────────────
def test_unroutable_models_are_never_selected():
    r = _router()
    r._unroutable_model_names = {"mistral-nemo", "llama-3.2-1b"}
    sel = r.select_model(
        routing_models=CATALOGUE,
        weights={"risk": 0, "cost": 1, "latency": 0, "priority": 0},
    )
    assert sel.model_name == "gpt-4o-mini", (
        "scorer ranked a model the router cannot serve"
    )


# ───────────────────────── no LLM anywhere ─────────────────────────
def test_routing_never_constructs_a_bedrock_client():
    def _forbidden(*_a, **_kw):
        raise AssertionError("routing must not construct a Bedrock client")

    with patch("ai_mesh_gateway.bedrock_client.default_bedrock_client", _forbidden):
        for preset in PRESETS.values():
            for sens in ("public", "internal", "confidential", "restricted"):
                _pick(weights=preset, data_sensitivity=sens)


def test_adjudicator_surface_is_gone():
    assert not hasattr(LLMRouter, "adjudicate_model_selection")


def test_decision_source_is_always_deterministic_weighted():
    for preset in PRESETS.values():
        sel = _pick(weights=preset)
        assert sel.decision_source == "deterministic_weighted"
        assert sel.evaluator_model == ""


# ───────────────────────── weight hygiene ─────────────────────────
@pytest.mark.parametrize("weights", [
    {"risk": 0, "cost": 0, "latency": 0, "priority": 0},
    {"risk": 1, "cost": 1, "latency": 1, "priority": 1},
    {"risk": 0.25, "cost": 0.25, "latency": 0.25, "priority": 0.25},
])
def test_degenerate_weight_vectors_still_select_deterministically(weights):
    winners = {_pick(weights=weights).model_name for _ in range(25)}
    assert len(winners) == 1
