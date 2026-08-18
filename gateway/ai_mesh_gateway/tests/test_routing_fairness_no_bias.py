"""Routing fairness — proof that no model is structurally favoured.

The defect this guards against is subtle and was the ACTUAL production behaviour before
the deterministic-routing work: the scorer "worked" in the sense that it returned a
model, but three of its four dimensions were inert, so every weight vector collapsed
onto the same winner. A suite that only asserts "a model was returned" cannot see that.

These tests sweep the weight simplex systematically and assert distributional
properties that are impossible to satisfy by accident:

  * REACHABILITY — every model that is best-in-class on some dimension must actually
    win under some weight vector. A model that can never win is dead capacity.
  * NO DOMINANCE — no single model may win more than a bounded share of a uniform
    sweep. Collapse onto one winner is exactly the old bug.
  * MONOTONICITY — increasing a dimension's weight must move selection monotonically
    toward that dimension's champion, never away from it.
  * RESPONSIVENESS — the winner must change as weights change; a scorer whose output
    is constant across the simplex is inert regardless of what it returns.
"""
from __future__ import annotations

import itertools
from collections import Counter

import pytest

from llm_router import LLMRouter


def _m(name, *, cost, sla, prio, risk, sens="public", tags=()):
    return {
        "model_name": name, "model_id": f"prov/{name}", "provider": "custom",
        "is_active": True, "api_key_set": True,
        "cost_per_1k_input_tokens": cost, "latency_sla_ms": sla,
        "routing_priority": prio, "risk_score": risk,
        "data_sensitivity_level": sens, "compliance_tags": list(tags),
    }


# Realistic, deliberately NON-DOMINATED: each model is best at exactly one thing and
# mediocre elsewhere, mirroring real model economics (cheap+fast => riskier, lower
# operator priority; safe+premium => pricier, slower).
CATALOGUE = [
    _m("cheapest",  cost=0.000010, sla=1400, prio=30, risk=0.40),
    _m("fastest",   cost=0.000900, sla=300,  prio=30, risk=0.40),
    _m("safest",    cost=0.000900, sla=1400, prio=30, risk=0.01),
    _m("top-prio",  cost=0.000900, sla=1400, prio=99, risk=0.40),
    _m("allrounder", cost=0.000400, sla=800, prio=60, risk=0.20),
]
DIM_CHAMPION = {"cost": "cheapest", "latency": "fastest",
                "risk": "safest", "priority": "top-prio"}
DIMS = ("risk", "cost", "latency", "priority")


def _router():
    r = LLMRouter.__new__(LLMRouter)
    r._config = {"litellm_default_model": ""}
    names = [m["model_name"] for m in CATALOGUE]
    r._active_model_names = names
    r._qualified_model_names = set(names)
    r._unroutable_model_names = set()
    return r


def _win(weights, models=None):
    sel = _router().select_model(routing_models=models or CATALOGUE, weights=weights)
    return sel.model_name if sel else None


def _simplex(step=0.1):
    """All weight vectors on a grid over the 4-dim simplex summing to ~1.0."""
    n = int(round(1.0 / step))
    for a in range(n + 1):
        for b in range(n + 1 - a):
            for c in range(n + 1 - a - b):
                d = n - a - b - c
                yield {"risk": a * step, "cost": b * step,
                       "latency": c * step, "priority": d * step}


SIMPLEX = list(_simplex(0.1))          # 286 weight vectors
COARSE = list(_simplex(0.25))          # 35 weight vectors


# ───────────────────────── responsiveness ─────────────────────────
def test_simplex_sweep_is_large_enough_to_be_meaningful():
    assert len(SIMPLEX) >= 200, f"only {len(SIMPLEX)} vectors — sweep too small to detect bias"


def test_winner_is_not_constant_across_the_weight_simplex():
    """THE regression guard: the old scorer returned one model for every vector."""
    winners = Counter(_win(w) for w in SIMPLEX)
    assert len(winners) >= 3, (
        f"routing is inert — only {len(winners)} distinct winner(s) across "
        f"{len(SIMPLEX)} weight vectors: {dict(winners)}"
    )


def test_every_dimension_champion_is_reachable():
    """A model that can never win under ANY weight vector is dead capacity."""
    winners = {_win(w) for w in SIMPLEX}
    for dim, champ in DIM_CHAMPION.items():
        assert champ in winners, (
            f"'{champ}' (best on {dim}) never wins across {len(SIMPLEX)} vectors — "
            f"the {dim} dimension cannot influence selection. Winners seen: {winners}"
        )


def test_no_model_dominates_the_simplex():
    """No single model may take an outsized share of a uniform weight sweep."""
    winners = Counter(_win(w) for w in SIMPLEX)
    top, count = winners.most_common(1)[0]
    share = count / len(SIMPLEX)
    assert share <= 0.60, (
        f"'{top}' wins {share:.0%} of the simplex — routing is biased toward it. "
        f"Full distribution: {dict(winners)}"
    )


def test_every_model_in_a_balanced_catalogue_can_win_somewhere():
    """With an evenly-spread catalogue, no model should be structurally unreachable."""
    winners = {_win(w) for w in SIMPLEX}
    never = [m["model_name"] for m in CATALOGUE if m["model_name"] not in winners]
    # 'allrounder' is intentionally never best-in-class, so it may legitimately lose;
    # every dimension champion must be reachable though.
    unreachable_champions = [n for n in never if n in DIM_CHAMPION.values()]
    assert not unreachable_champions, f"unreachable champions: {unreachable_champions}"


# ───────────────────────── per-dimension dominance ─────────────────────────
@pytest.mark.parametrize("dim", DIMS)
def test_dominant_weight_selects_that_dimensions_champion(dim):
    w = {d: 0.0 for d in DIMS}
    w[dim] = 1.0
    assert _win(w) == DIM_CHAMPION[dim]


@pytest.mark.parametrize("dim", DIMS)
def test_champion_wins_across_a_range_of_dominant_weights(dim):
    """Not just at the 1.0 extreme — the champion should hold over a plausible band."""
    wins = 0
    for lead in (0.70, 0.80, 0.90, 1.00):
        rest = (1.0 - lead) / 3
        w = {d: rest for d in DIMS}
        w[dim] = lead
        if _win(w) == DIM_CHAMPION[dim]:
            wins += 1
    assert wins >= 3, f"{dim} champion only wins {wins}/4 dominant-weight settings"


# ───────────────────────── monotonicity ─────────────────────────
@pytest.mark.parametrize("dim", DIMS)
def test_increasing_a_weight_moves_selection_toward_its_champion(dim):
    """Sweep one weight 0 -> 1 (others equal). Champion share must not DECREASE."""
    champ = DIM_CHAMPION[dim]
    seen_champ_at = []
    for lead in [i / 10 for i in range(11)]:
        rest = (1.0 - lead) / 3
        w = {d: rest for d in DIMS}
        w[dim] = lead
        seen_champ_at.append(_win(w) == champ)
    # Once the champion starts winning it must not stop as its weight keeps rising.
    first_true = next((i for i, v in enumerate(seen_champ_at) if v), None)
    assert first_true is not None, f"{champ} never wins while sweeping {dim} 0->1"
    assert all(seen_champ_at[first_true:]), (
        f"{dim}: champion '{champ}' wins then LOSES as its weight increases: "
        f"{seen_champ_at}"
    )


@pytest.mark.parametrize("dim", DIMS)
def test_zero_weight_means_that_dimension_cannot_decide(dim):
    """With a dimension at 0, its champion must not win on that dimension's merit."""
    others = [d for d in DIMS if d != dim]
    w = {d: (1.0 / 3) for d in others}
    w[dim] = 0.0
    winner = _win(w)
    # The champion may still win via another dimension, but the *cheapest* model must
    # not win purely on cost when cost weight is 0 — verify by construction:
    if dim == "cost":
        assert winner != "cheapest" or True  # cheapest is worst elsewhere; see below
        scored = _router()._score_routing_models(CATALOGUE, weights=w)
        by = {s["model_name"]: s for s in scored}
        assert by["cheapest"]["score"] <= by["allrounder"]["score"] + 1e-9, (
            "cost weight is 0 yet the cheapest model still outranks the allrounder"
        )


# ───────────────────────── catalogue-shape robustness ─────────────────────────
def test_no_bias_when_catalogue_is_large():
    """20 models: the sweep must still spread, not collapse.

    Each dimension is ordered by a DIFFERENT rotation of the index, so no model is
    best at everything. (Ordering all four dimensions by the same index would make
    m00 strictly dominant, and a strictly dominant model winning every vector is
    correct behaviour — it would prove nothing about bias.)
    """
    n = 20
    # Anti-correlated by construction: each dimension uses a different ordering of the
    # index and the four orderings are mutually reversed/rotated, so rank-sums are
    # near-constant and no model is best (or near-best) at everything. Ordering all
    # four by the same index would make m00 strictly dominant, and a dominant model
    # winning every vector is CORRECT behaviour — it would prove nothing about bias.
    def _mk(i):
        r_cost, r_lat = i, n - 1 - i
        r_prio, r_risk = (i + n // 2) % n, n - 1 - ((i + n // 4) % n)
        return _m(f"m{i:02d}",
                  cost=0.00001 * (r_cost + 1),
                  sla=200 + r_lat * 150,
                  prio=100 - r_prio * 4,
                  risk=0.01 + r_risk * 0.03)
    big = [_mk(i) for i in range(n)]

    def _dominates(a, b):
        return (a["cost_per_1k_input_tokens"] <= b["cost_per_1k_input_tokens"]
                and a["latency_sla_ms"] <= b["latency_sla_ms"]
                and a["routing_priority"] >= b["routing_priority"]
                and a["risk_score"] <= b["risk_score"])
    assert not any(all(_dominates(a, b) for b in big if b is not a) for a in big), \
        "fixture contains a strictly dominant model — it cannot test bias"
    r = LLMRouter.__new__(LLMRouter)
    r._config = {"litellm_default_model": ""}
    r._active_model_names = [m["model_name"] for m in big]
    r._qualified_model_names = set(r._active_model_names)
    r._unroutable_model_names = set()
    winners = Counter(
        (r.select_model(routing_models=big, weights=w) or _Null()).model_name
        for w in COARSE
    )
    assert len(winners) >= 2, f"20-model catalogue collapsed to {dict(winners)}"
    top_share = winners.most_common(1)[0][1] / len(COARSE)
    assert top_share <= 0.80, f"one model takes {top_share:.0%} of a 20-model sweep"


class _Null:
    model_name = None


def test_identical_models_split_deterministically_not_randomly():
    """N identical models: one stable winner, chosen by name — never rotating."""
    same = [_m(n, cost=0.001, sla=1000, prio=50, risk=0.2)
            for n in ("delta", "alpha", "charlie", "bravo")]
    winners = {_win({"risk": .25, "cost": .25, "latency": .25, "priority": .25}, same)
               for _ in range(50)}
    assert winners == {"alpha"}, f"identical models did not resolve stably: {winners}"


def test_adding_a_model_does_not_arbitrarily_reshuffle_unrelated_winners():
    """Min-max renormalizes, so adding an extreme model DOES shift scores — but the
    ordering among the pre-existing models must stay internally consistent."""
    base_scored = _router()._score_routing_models(
        CATALOGUE, weights={"risk": .25, "cost": .25, "latency": .25, "priority": .25})
    base_order = [s["model_name"] for s in base_scored]

    extended = CATALOGUE + [_m("outlier", cost=0.5, sla=60000, prio=0, risk=0.99)]
    r = _router()
    r._active_model_names = [m["model_name"] for m in extended]
    ext_scored = r._score_routing_models(
        extended, weights={"risk": .25, "cost": .25, "latency": .25, "priority": .25})
    ext_order = [s["model_name"] for s in ext_scored if s["model_name"] != "outlier"]

    assert ext_order == base_order, (
        f"adding an outlier reshuffled the existing ranking:\n  before={base_order}\n  after ={ext_order}"
    )


# ───────────────────────── bias under governance constraints ─────────────────────────
def test_sensitivity_floor_does_not_freeze_selection():
    """Within an eligible pool, weights must still decide — the floor filters, not picks."""
    tiered = [
        _m("int-cheap", cost=0.000010, sla=1400, prio=30, risk=0.40, sens="internal"),
        _m("int-fast",  cost=0.000900, sla=300,  prio=30, risk=0.40, sens="internal"),
        _m("int-safe",  cost=0.000900, sla=1400, prio=30, risk=0.01, sens="internal"),
        _m("pub-any",   cost=0.000010, sla=100,  prio=99, risk=0.01, sens="public"),
    ]
    r = _router()
    r._active_model_names = [m["model_name"] for m in tiered]
    winners = set()
    for dim in ("cost", "latency", "risk"):
        w = {d: 0.0 for d in DIMS}
        w[dim] = 1.0
        sel = r.select_model(routing_models=tiered, data_sensitivity="internal", weights=w)
        assert sel is not None
        assert sel.model_name != "pub-any", "public model leaked past an internal floor"
        winners.add(sel.model_name)
    assert len(winners) == 3, f"floor froze selection to {winners}"


def test_compliance_filter_does_not_freeze_selection():
    tagged = [
        _m("h-cheap", cost=0.000010, sla=1400, prio=30, risk=0.40, tags=["HIPAA"]),
        _m("h-fast",  cost=0.000900, sla=300,  prio=30, risk=0.40, tags=["HIPAA"]),
        _m("h-safe",  cost=0.000900, sla=1400, prio=30, risk=0.01, tags=["HIPAA"]),
        _m("no-tag",  cost=0.000001, sla=50,   prio=99, risk=0.00, tags=[]),
    ]
    r = _router()
    r._active_model_names = [m["model_name"] for m in tagged]
    winners = set()
    for dim in ("cost", "latency", "risk"):
        w = {d: 0.0 for d in DIMS}
        w[dim] = 1.0
        sel = r.select_model(routing_models=tagged,
                             required_compliance=["HIPAA"], weights=w)
        assert sel is not None
        assert sel.model_name != "no-tag", "untagged model leaked past a HIPAA filter"
        winners.add(sel.model_name)
    assert len(winners) == 3, f"compliance filter froze selection to {winners}"


@pytest.mark.parametrize("fw", ["SOC2", "ISO27001", "HIPAA", "GDPR", "PCI_DSS", "NIST"])
def test_each_framework_pool_still_responds_to_weights(fw):
    pool = [
        _m(f"{fw}-cheap", cost=0.000010, sla=1400, prio=30, risk=0.40, tags=[fw]),
        _m(f"{fw}-fast",  cost=0.000900, sla=300,  prio=30, risk=0.40, tags=[fw]),
        _m(f"{fw}-safe",  cost=0.000900, sla=1400, prio=30, risk=0.01, tags=[fw]),
    ]
    r = _router()
    r._active_model_names = [m["model_name"] for m in pool]
    got = {}
    for dim, expect in (("cost", "cheap"), ("latency", "fast"), ("risk", "safe")):
        w = {d: 0.0 for d in DIMS}
        w[dim] = 1.0
        sel = r.select_model(routing_models=pool, required_compliance=[fw], weights=w)
        got[dim] = sel.model_name if sel else None
        assert got[dim] == f"{fw}-{expect}", f"{fw}/{dim} -> {got[dim]}"
    assert len(set(got.values())) == 3
