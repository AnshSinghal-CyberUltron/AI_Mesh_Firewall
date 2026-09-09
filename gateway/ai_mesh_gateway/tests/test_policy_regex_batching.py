"""Batching the policy engine's regex timeout handoff must not change verdicts.

The per-rule handoff cost 11.19 us against a 4.74 us inline .search() — 53% of the
policy stage was queue round-trips. Batching collapses N round-trips into one. These
tests pin the properties that made the per-rule worker worth having in the first place,
so a future change cannot trade them away for speed silently.
"""
from __future__ import annotations

import pytest

from ai_mesh_gateway import policy_engine as pe


def _policy(pid, code, rules, **extra):
    return {"policy": {"id": pid, "code": code, "name": code, "severity": "high",
                       "category": "test", **extra}, "rules": rules}


def _rule(rid, rtype, condition, action, **extra):
    return {"id": rid, "rule_type": rtype, "condition": condition,
            "action": action, "name": f"rule-{rid}", **extra}


BUNDLE = [
    _policy(1, "P-REGEX", [
        _rule(11, "regex", {"regex": r"secret[- ]?key", "field": "prompt"}, "block"),
        _rule(12, "regex", {"regex": r"\b\d{16}\b", "field": "both"}, "redact"),
        _rule(13, "regex", {"regex": r"internal-only", "field": "response"}, "monitor"),
    ]),
    _policy(2, "P-KEYWORD", [
        _rule(21, "keywords", {"keywords": ["forbidden", "banned"], "field": "prompt"}, "monitor"),
    ]),
    _policy(3, "P-MIXED", [
        _rule(31, "regex", {"regex": r"rewrite[- ]me", "field": "prompt"}, "rewrite"),
        _rule(32, "regex", {"regex": r"(?i)downgrade", "field": "prompt"}, "model_downgrade",
              redaction_config={"downgrade_to": "small-model"}),
        _rule(33, "regex", {"regex": r"[", "field": "prompt"}, "block"),   # malformed on purpose
    ]),
]

CASES = [
    "",
    "nothing interesting here",
    "my secret-key is hunter2",
    "card 4111111111111111 please",
    "this is forbidden content",
    "rewrite me now",
    "please DOWNGRADE this",
    "secret key AND 4111111111111111 AND forbidden AND rewrite me",
    "internal-only",
    "a" * 5000,
    "café ☕ ünïcødé",
    "line1\nline2",
]


def _snapshot(r):
    return (r.action, tuple(r.matched_rule_ids), tuple(r.matched_policy_ids),
            tuple(sorted(h["rule_id"] for h in r.redaction_hints)),
            tuple(sorted(h["rule_id"] for h in r.rewrite_hints)),
            r.message, getattr(r, "model_downgrade_target", ""))


def _unbatched(monkeypatch):
    """Force the fallback, which IS the original per-rule path."""
    monkeypatch.setattr(pe, "_search_many_with_budget", lambda *a, **k: None)


@pytest.mark.parametrize("prompt", CASES)
def test_batched_matches_per_rule_prompt_side(prompt, monkeypatch):
    batched = _snapshot(pe.evaluate(prompt, "", BUNDLE))
    _unbatched(monkeypatch)
    per_rule = _snapshot(pe.evaluate(prompt, "", BUNDLE))
    assert batched == per_rule


@pytest.mark.parametrize("response", CASES)
def test_batched_matches_per_rule_response_side(response, monkeypatch):
    batched = _snapshot(pe.evaluate("", response, BUNDLE))
    _unbatched(monkeypatch)
    per_rule = _snapshot(pe.evaluate("", response, BUNDLE))
    assert batched == per_rule


def test_malformed_pattern_does_not_fail_the_batch():
    """Rule 33's pattern is invalid. It must be skipped, and every OTHER rule must
    still be evaluated — a batch is not an all-or-nothing unit."""
    r = pe.evaluate("my secret-key is hunter2", "", BUNDLE)
    assert r.action == "block"
    assert 11 in r.matched_rule_ids
    assert 33 not in r.matched_rule_ids


def test_a_hung_batch_still_evaluates_every_rule(monkeypatch):
    """Property 3 of the original design: one hung regex must not suppress the others.

    A timed-out batch returns None, and the decision loop then falls through to the
    per-rule path for EVERY rule — so the verdict is unchanged, not degraded."""
    calls = {"n": 0}
    real = pe._run_with_timeout

    def fake(fn, timeout):
        # Fail only the batch (the first call); let per-rule calls through.
        calls["n"] += 1
        if calls["n"] == 1:
            return None
        return real(fn, timeout)

    monkeypatch.setattr(pe, "_run_with_timeout", fake)
    r = pe.evaluate("secret-key and 4111111111111111", "", BUNDLE)
    assert r.action == "block"                 # rule 11 still found
    assert 12 in r.matched_rule_ids            # rule 12 still found AFTER the hung batch
    assert calls["n"] > 1                      # the fallback really ran


def test_caller_block_does_not_scale_with_rule_count(monkeypatch):
    """R3: N rules must not be able to block the caller for N x the budget.

    The batch is submitted under ONE budget, so _run_with_timeout is called once for
    the regex work regardless of how many regex rules the bundle holds."""
    seen = []
    real = pe._run_with_timeout
    monkeypatch.setattr(pe, "_run_with_timeout",
                        lambda fn, t: (seen.append(t), real(fn, t))[1])

    big = [_policy(99, "P-BIG", [
        _rule(1000 + i, "regex", {"regex": rf"pattern{i}", "field": "prompt"}, "monitor")
        for i in range(200)
    ])]
    pe.evaluate("nothing matches here", "", big)
    assert len(seen) == 1, f"expected ONE budgeted call for 200 rules, got {len(seen)}"
    assert seen[0] == pe._REGEX_MATCH_TIMEOUT_S


def test_keyword_rules_never_reach_the_worker(monkeypatch):
    """R6: keyword rules cost 1.20 us/rule and never used the worker. A bundle with
    only keyword rules must not submit a batch at all."""
    seen = []
    monkeypatch.setattr(pe, "_run_with_timeout", lambda fn, t: seen.append(t))
    kw_only = [_policy(5, "P-KW", [
        _rule(51, "keywords", {"keywords": ["alpha"], "field": "prompt"}, "monitor"),
    ])]
    r = pe.evaluate("alpha", "", kw_only)
    assert r.matched_rule_ids == [51]
    assert seen == [], "keyword-only bundle must not submit a regex batch"


def test_tool_and_actor_filters_apply_before_the_batch(monkeypatch):
    """A rule that the decision loop will skip must not have its regex run either."""
    submitted = {}
    real = pe._search_many_with_budget

    def spy(jobs):
        jobs = list(jobs)
        submitted["n"] = len(jobs)
        return real(jobs)

    monkeypatch.setattr(pe, "_search_many_with_budget", spy)
    bundle = [_policy(7, "P-TOOL", [
        _rule(71, "regex", {"regex": "aaa", "field": "prompt"}, "monitor", target_tool="other"),
        _rule(72, "regex", {"regex": "bbb", "field": "prompt"}, "monitor"),
    ])]
    pe.evaluate("aaa bbb", "", bundle, tool_name="mine")
    assert submitted["n"] == 1, "the tool-filtered rule should not be batched"
