# A combined-alternation prefilter does not work in Python `re` — measured

**Tag `[M]`** · offline bench, 4,096-char inputs, n=40 (sweep) / n=200 (prefilter)

## The idea, and why it looked good

`_redact_all_raw` runs **63 full-text `.sub()` scans per call** — one per pattern across
five families (PII 20, PHI 3, PCI 3, SECRET 25, CREDENTIAL 12). On benign prose every one
finds nothing.

A single combined alternation of the same 63 patterns should decide "does anything match?"
in one pass. If it finds nothing, no individual pattern can match, so every `.sub()` is
provably a no-op and the whole sweep can be skipped. That is a *sound* skip, not a
heuristic — the correctness argument is airtight.

## It is 0.9× — i.e. slightly slower

| input | 63 sequential `.sub()` | 1 combined alternation | verdict |
|---|---:|---:|---|
| benign prose | 3.300 ms | **3.478 ms** (no match) | **slower than what it replaces** |
| text containing PII | 3.172 ms | 1.667 ms (match) | pure overhead: full sweep still runs, **+53%** |

On the benign path — the one that matters, since it is nearly all traffic — the prefilter
costs *more* than the work it was meant to avoid.

## Why

Python's `re` has no parallel-automaton execution. A 63-branch alternation is tried branch
by branch with backtracking, so it does roughly the same work as 63 separate scans plus
alternation overhead. There is no single-pass multi-pattern matching to exploit.

That capability is exactly what a multi-pattern engine (Hyperscan/Vectorscan, RE2) provides
and `re` does not.

## Consequence

This **closes the cheap alternative** and strengthens the case for the plan's task 7 rather
than substituting for it. The 63-scan structure is not fixable by rearranging `re` calls;
it needs a different matcher.

One implementation note for whoever takes task 7: a naive `"|".join(patterns)` will not
even compile — several patterns carry an inline `(?i)` global flag mid-expression
("global flags not at the start of the expression"). Each branch must be wrapped as
`(?i:…)` with any embedded global flags stripped first. That is done and verified here:
63 branches, 0 unusable.

## Ledger

Eight hypotheses and one optimisation refuted this session, against two confirmed fixes
(tasks 8 and 8b). Both confirmations rest on an A/B control plus two independent
measurement methods agreeing; the refutations each cost minutes and prevented a change that
would not have paid.
