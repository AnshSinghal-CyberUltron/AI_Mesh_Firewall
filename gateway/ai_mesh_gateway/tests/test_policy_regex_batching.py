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
    # The text must contain each rule's required literal, or the prefilter skips every
    # rule before the batch and nothing is submitted at all.
    pe.evaluate(" ".join(f"pattern{i}" for i in range(200)), "", big)
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


# ── Literal prefilter ────────────────────────────────────────────────────────

PREFILTER_BUNDLE = [
    _policy(40, "P-PF", [
        _rule(401, "regex", {"regex": r"\bsk-[A-Za-z0-9]{20,}\b", "field": "prompt"}, "block"),
        _rule(402, "regex", {"regex": r"-----BEGIN PRIVATE KEY-----", "field": "prompt"}, "block"),
        _rule(403, "regex", {"regex": r"\b\d{3}-\d{2}-\d{4}\b", "field": "prompt"}, "redact"),
    ]),
]


def test_prefilter_skips_only_rules_that_cannot_match(monkeypatch):
    """A rule whose required literal is absent is skipped; the verdict is unchanged."""
    submitted = {}
    real = pe._search_many_with_budget

    def spy(jobs):
        jobs = list(jobs)
        submitted["ids"] = {j[0] for j in jobs}
        return real(jobs)

    monkeypatch.setattr(pe, "_search_many_with_budget", spy)
    # No "sk-" and no "-----begin private key-----": both literal rules are skippable.
    # Rule 403 (SSN) has no literal requirement, so it must still be evaluated.
    pe.evaluate("an ordinary sentence with no secrets in it", "", PREFILTER_BUNDLE)
    ids_by_rule = {r["id"]: id(r) for p in PREFILTER_BUNDLE for r in p["rules"]}
    assert ids_by_rule[401] not in submitted["ids"]
    assert ids_by_rule[402] not in submitted["ids"]
    assert ids_by_rule[403] in submitted["ids"], "a rule with no literal must still run"


@pytest.mark.parametrize("text", [
    "sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ123",
    "-----BEGIN PRIVATE KEY-----",
    "-----begin private key-----",          # prefilter is case-insensitive, like the regex
    "ssn 123-45-6789",
    "nothing here",
    "SK-ABCDEFGHIJKLMNOPQRSTUVWXYZ123",     # uppercase literal
])
def test_prefilter_never_changes_the_verdict(text, monkeypatch):
    with_pf = _snapshot(pe.evaluate(text, "", PREFILTER_BUNDLE))
    monkeypatch.setattr(pe, "_required_literals", lambda _p: None)   # disable prefilter
    without_pf = _snapshot(pe.evaluate(text, "", PREFILTER_BUNDLE))
    assert with_pf == without_pf


@pytest.mark.parametrize("pattern,expected_required", [
    (r"\bsk-[A-Za-z0-9]{20,}\b",              True),   # top-level literal run
    (r"-----BEGIN PRIVATE KEY-----",           True),
    (r"(?:foo|bar)baz",                        True),   # literal after an alternation
    (r"\d{3}-\d{2}-\d{4}",                    False),  # no literal run >= 3
    (r"(?:alpha|beta)",                        True),   # every branch has a literal
    (r"(?:alpha|\d+)",                         False),  # one branch is unfilterable
    (r"(alpha)?beta",                          True),   # optional group ignored, beta required
    (r"(alpha)?\d+",                           False),  # nothing is required
])
def test_literal_extraction_is_sound(pattern, expected_required):
    got = pe._required_literals(pattern)
    assert (got is not None) is expected_required, f"{pattern!r} -> {got!r}"


def test_alternation_literals_are_a_union_not_a_pick():
    """An earlier heuristic lifted ONE literal out of an alternation, which is unsound:
    a match through the other branch would have been skipped."""
    lits = pe._required_literals(r"(?:alpha|beta)")
    assert lits == frozenset({"alpha", "beta"})
    # and both must actually survive evaluation
    b = [_policy(41, "P-ALT", [_rule(411, "regex", {"regex": r"(?:alpha|beta)",
                                                    "field": "prompt"}, "monitor")])]
    assert pe.evaluate("say beta please", "", b).matched_rule_ids == [411]
    assert pe.evaluate("say alpha please", "", b).matched_rule_ids == [411]
    assert pe.evaluate("say gamma please", "", b).matched_rule_ids == []


def test_non_ascii_literals_do_not_enter_the_filter():
    """Only the ASCII run is taken; the non-ASCII char breaks it. "stra" IS required by
    "straße", so keeping it is sound — what must not happen is a non-ASCII char landing
    in a literal that is then compared with a casefold the regex engine does not share."""
    assert pe._required_literals(r"straße") == frozenset({"stra"})
    assert all(lit.isascii() for lit in (pe._required_literals(r"ünbroken") or ()))


def test_long_s_is_not_skipped():
    """re.IGNORECASE matches 's' against 'ſ' (U+017F), but 'ſ'.lower() is 'ſ' — a
    .lower()-based prefilter would SKIP a rule that genuinely matches. casefold maps
    it to 's'. This is the case that decided lower() vs casefold()."""
    import re as _re
    assert _re.search("secret", "ſecret", _re.I), "premise: re matches long-s"
    b = [_policy(42, "P-LONGS", [
        _rule(421, "regex", {"regex": "secret", "field": "prompt"}, "block")])]
    assert pe.evaluate("ſecret data", "", b).matched_rule_ids == [421]


def test_sharp_s_overmatch_only_costs_a_wasted_scan():
    """casefold maps 'ß' to 'ss' where re does NOT match — so the filter admits a rule
    that cannot match. That is a wasted scan, never a missed detection."""
    b = [_policy(43, "P-SHARP", [
        _rule(431, "regex", {"regex": "class", "field": "prompt"}, "block")])]
    assert pe.evaluate("claß", "", b).matched_rule_ids == []
