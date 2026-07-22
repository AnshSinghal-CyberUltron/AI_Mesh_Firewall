"""POLICY VALIDATION: per-stage policy scoping + action precedence/conflict.

evaluate_for_stage is what the RAG ranker calls (stage='ranker'). These pin the
scope-isolation contract (a rule scoped to another stage must NOT fire) and the
severity precedence (block > redact > monitor > allow) when multiple rules match
— the "scope × conflict × override" cells of the policy matrix. This path had no
direct stage-scoping test.
"""
from __future__ import annotations

import pytest

from policy_engine import evaluate_for_stage


def _pol(stage, action, keyword, priority=0):
    return {
        "policy": {"id": 1, "code": "p", "priority": priority},
        "rules": [{
            "id": 1, "rule_type": "keywords",
            "condition": {"keywords": [keyword], "field": "prompt"},
            "action": action, "pipeline_stage": stage,
        }],
    }


TEXT = "this document has secret content"


# ── Scope isolation: a rule bound to another stage must not fire ──
@pytest.mark.parametrize("other_stage", ["generator", "query", "retriever"])
def test_rule_scoped_to_other_stage_does_not_fire_at_ranker(other_stage):
    r = evaluate_for_stage(TEXT, "", [_pol(other_stage, "block", "secret")], stage="ranker")
    assert r.action == "allow", f"{other_stage}-scoped rule leaked into ranker stage"


def test_rule_scoped_to_ranker_fires_at_ranker():
    r = evaluate_for_stage(TEXT, "", [_pol("ranker", "block", "secret")], stage="ranker")
    assert r.action == "block"


def test_all_stage_rule_fires_at_every_stage():
    for stage in ("query", "retriever", "ranker", "generator"):
        r = evaluate_for_stage(TEXT, "", [_pol("", "block", "secret")], stage=stage)
        assert r.action == "block", f"all-stage rule did not fire at {stage}"


def test_no_match_is_allow():
    r = evaluate_for_stage(TEXT, "", [_pol("ranker", "block", "nonexistent")], stage="ranker")
    assert r.action == "allow"


# ── Conflict resolution: highest-severity action wins ──
def test_block_beats_monitor_when_both_match():
    pols = [_pol("ranker", "monitor", "secret"), _pol("ranker", "block", "secret")]
    r = evaluate_for_stage(TEXT, "", pols, stage="ranker")
    assert r.action == "block"


def test_block_beats_redact_when_both_match():
    pols = [_pol("ranker", "redact", "secret"), _pol("ranker", "block", "secret")]
    r = evaluate_for_stage(TEXT, "", pols, stage="ranker")
    assert r.action == "block"


def test_redact_beats_monitor_when_both_match():
    pols = [_pol("ranker", "monitor", "secret"), _pol("ranker", "redact", "secret")]
    r = evaluate_for_stage(TEXT, "", pols, stage="ranker")
    assert r.action == "redact"


def test_order_independence_of_precedence():
    # Winning action must not depend on rule ordering in the bundle.
    a = evaluate_for_stage(TEXT, "", [_pol("ranker", "block", "secret"),
                                      _pol("ranker", "monitor", "secret")], stage="ranker")
    b = evaluate_for_stage(TEXT, "", [_pol("ranker", "monitor", "secret"),
                                      _pol("ranker", "block", "secret")], stage="ranker")
    assert a.action == b.action == "block"
