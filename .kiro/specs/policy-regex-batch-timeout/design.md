# Design — one worker round-trip per request instead of one per rule

## Shape

`evaluate()` today interleaves the match decision with bookkeeping:

```python
for entry in compiled_policies:
    for rule in rules:
        if rule_target and rule_target != tool_name: continue
        if not _evaluate_rule(rule, prompt, response_text): continue
        ...bookkeeping, action lattice, hints...
```

The match decision is **fully separable** from the bookkeeping. So: two passes.

**Pass 1 — resolve.** Walk the same policies/rules in the same order, applying the same
actor and tool filters, and collect the regex rules that need a verdict. Run them all in
**one** worker round-trip. Keyword rules are not collected: they never touched the worker.

**Pass 2 — decide.** The existing loop, unchanged, except `_evaluate_rule` consults the
precomputed verdict for regex rules. Order, lattice, hints and messages are untouched, so
R1 falls out of the structure rather than needing to be tested into existence.

## Identity, not equality, keys the verdict map

Two rules in a bundle can be dict-equal (same pattern, same action, different policy) and
dicts are unhashable anyway. The map is keyed by `id(rule)` — the rules are the caller's
own objects and are alive for the whole call, so identity is stable and unique. A list
index would also work but is fragile against a filter changing between passes; identity
cannot drift.

## Budget (R3)

The batch runs under a **single** `_REGEX_MATCH_TIMEOUT_S` (1.0 s), not N × that. 92 rules
against a 250-char prompt take 427 µs inline, four orders of magnitude inside the budget,
so the budget only ever fires on a genuine hang. The caller's worst-case block is now
*independent of rule count* — strictly better than today, where 92 rules could in principle
block for 92 seconds.

## Timeout → fall back to exactly the current path (R2)

On batch timeout the worker is retired (as today) and every regex rule is re-run through
the existing per-rule `_search_with_budget`. That path is unchanged, so property (3) —
a later regex still runs after one has hung — is preserved by *reusing the code that
already provides it* rather than by reimplementing it.

Cost of the fallback: one wasted batch (bounded by the budget) plus today's cost. It runs
only when a pattern actually hangs, which the compile-time `_has_redos_shape` rejection
already makes rare.

## Late results cannot leak (R5)

Worker retirement drops the worker **and its queues** together. A late batch result is
written to a queue nobody holds a reference to, so it is unreachable by any later call.
This is the existing invariant; batching does not weaken it because the unit of retirement
(the worker) is unchanged.

## Fail-open is preserved per rule

The batch job catches per-rule exceptions and records `False` for that rule, matching
`_search_with_budget`'s existing "re.error → skip this rule" semantics. One malformed
pattern cannot fail the batch.

## What would falsify this design

If the policy stage falls to ~53% of its current cost and no further, then handoff cost is
purely the measured 11.19 µs/rule and the 4.7× load inflation comes from somewhere else —
the design is still a win but the GIL-ping-pong explanation is wrong and the remaining
inflation needs its own investigation. Recorded either way.
