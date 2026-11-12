**Plan Reviewer A (completeness & correctness): REQUEST CHANGES**

Reviewed at `e2c3e2cf`. Path legend: `ev/` = `docs/plans/evidence/2026-11-11-f1a1-regex/`. Measurements were taken with the supplied Python 3.12.3 venv and `regex` 2023.8.8, in thread CPU unless marked.

**Overall.** The semantics work is now very strong. I attacked each new piece directly and could not break any of them:
- the one-sided boundaries;
- the atomic-repeat rule;
- both incremental finality rules;
- the lone-negated-character workaround.

All revision-4 findings are resolved in the text except one: half of my revision-4 B1 survives through an unlimited path (B1 below). Requirement 4 also does not hold as stated (S1).

### BLOCKER

**B1. Gateway translation cost is still unbounded, through the range classifier (requirement 5; the tenant-isolation exit criterion).**

- **The unlimited path.** `_range_class` (`ev/translate_prototype.py:229-233`) builds a string of every code point in a range up to 65,535 wide, in pure Python. It runs once per distinct range that sits next to `\b`/`\B` (through `_boundary` → `_item_class` → `_set_class`), or inside a single-class repeat followed by another item (`_possessive_safe`). No §5.2.3 limit counts this work.
- **Measured.** A pattern of 166 distinct wide ranges, `[Ā-￯]+[ĥ-￯]+…`, has 996 characters, 0 case-insensitive items and 0 counted repetition, so it passes every limit:
  - translation: **986 ms**;
  - the `\b[Ā-￯]\b[ĥ-￯]…` form: 708 ms.

  §5.2.3 claims 9.3 ms for the worst pattern and 236 ms for a plan at every limit.
- **Effect on the loop.** I ran 6 such rules the way §5.2.3 compiles them: one rule per executor step, with a yield between rules. That took **5.6 s CPU**. Meanwhile the event loop fell from 870 to **105** ticks/s (p99 lag 61 ms, max **226 ms**). The §5.2.3 bound of 7.3 ms on loop stall does not hold.
- **Scale.** A 64-rule plan would be about 60 s per worker per plan version. Any tenant can repeat it by re-publishing.
- **The projector is fine.** `check_limits` on the same pattern takes 1.7 ms.
- **Fix:**
  - classify ranges from a build-time table of `re`'s `\w` run boundaries (a bisect, O(log n)), generated and checked in CI like `_cased_table.py`. Alternatively, count probed ranges in `check_limits`;
  - add a per-rule translation CPU cap in the gateway that refuses the rule;
  - add this pattern to the §5.8 limits tests.

### SHOULD-FIX

**S1. Requirement 4 does not hold for patterns whose cost is not monotone in the text.**

Requirement 4 says a stream is refused for regex cost only where the same text, scanned once non-streaming, would be. The counterexample:
- The rule `^(\w+\s?){1,30}[.!?]` passes `_redos_shape` and every limit, and is incremental with a stopper.
- The stream is the sentence *"The quarterly report shows that revenue grew in every region and the team now expects further gains next year."*
- A pass over the first 6 words (no terminator yet) exceeds the 100 ms per-pass budget. `RuleScanner` raises `TimeoutError` at 28 characters.
- The whole sentence, scanned once, matches in **0.06 ms**.

Under §5.2.4 the off-loop pass times out and the stream is refused with `policy_regex_timeout`; non-streaming, the same text is not refused. HEAD behaves the same way, so this is not a regression. But requirement 4 and §9's "the same rule as non-streaming" overstate the guarantee, and §5.8's no-false-refusal tests would not catch it.

**Fix**, either:
- state the real guarantee (refused when a pass over the text received so far exceeds its budget), and add this case to §5.8 as a tested, documented limitation; or
- treat a timeout on a non-final attempt as undecided. Hold from that attempt's start, retry when a stopper arrives, when the text has grown by a factor, or at end of stream. Refuse only on the final pass, or on the existing hold cap.

### NIT

1. **The raw `regex` merge bug is wider than §5.2.1 says.**
   - It also fires through non-capturing groups: `(?:(?:[^9])|(?:[^c]))` gives `['a']` against `re`'s `['c','9','a']`.
   - It fires with astral members: `(?:[^\U0001d7ce]|[^a])`.
   - It does not fire through capture groups, negated ranges or multi-member negated sets.

   The doubling covers every case I tried (below). Widen the text so a later refactor keeps doubling every single-character negation.
2. **§5.8 gaps.** "No false refusals" and "Limits" should name the S1 case and the B1 pattern.

### What I tried to break, and could not

1. **One-sided boundaries and atomic repeats.** I wrote a boundary-heavy differential fuzz (`bfuzz.py`):
   - items next to `\b`/`\B`: classes, ranges, negated sets, groups, alternations, lookarounds and repeats;
   - flags `(?i)`, `(?s)`, `(?i:…)` and `(?-i:…)`;
   - text with U+0301, U+0345, ², Ⅻ, ‿, ZWJ, U+00AD, ß, K, ſ, ı, İ, µ, ǅ, ١, NBSP, U+001F, U+2028 and astral characters.

   172,000 patterns × 10 texts gave **0 mismatches**. One pattern hung inside `re` itself; the translator and `regex` were fast on every pattern of that seed. Sampled emission rates were about 0.66 one-sided boundaries and 0.06 atomic repeats per pattern, roughly 110k and 10k in total. My revision-4 wide-grammar fuzz (2 new seeds, 27k pairs) also gave 0.
2. **Possessive repeats.** The revision-4 targeted fuzz against `(?>(?>X){m,n})` gave 0 mismatches in 71,981 checks (19 `re` `SystemError`s skipped).
3. **Incremental finality (reach and stoppers).**
   - The provided fuzz on new seeds 701–703: 9,945 incremental streams checked, 164,557 deltas, 0 mismatches (16 skipped on a `regex` timeout).
   - My harsher grammar (`incfuzz2.py`: multiline `^`/`$`, `\Z`, lookbehinds containing `\b`, conditionals, backreferences, possessive repeats; chunkings down to single-character deltas): 18,284 incremental streams, 0 mismatches.
   - `regex`'s `pos=` matches `re` for `^`, `\A`, `(?m)^`, `\b`, `\B` and lookbehinds that read before `pos`: 12/12.
4. **The lone-negated-character workaround.** Translated alternations of `[^9]`, `[^c]`, `[^a-c]`, `[^ab]`, `[^\d9]`, `[^\W]`, `[^\s]`, astral and `İ` negations, `(?:[^9])`, `([^c])`, `x`, `\W` and `.`:
   - in pairs, triples and some quadruples;
   - under no flag and `(?i)`;
   - plain, repeated, inside a lookbehind and nested.

   76,624 patterns gave **0 mismatches**.

### Revision-4 findings: does the revision-5 text resolve them?

| Revision-4 finding | Status |
|---|---|
| B1: translation cost; projector path | **Projector: resolved.** Parse-only, memoised and time-boxed in `_plans`. **Gateway: not resolved** (B1 above) |
| B2: per-delta rescans | **Resolved** by incremental scans, inline-to-off-loop escalation, coalescing and length-scaled budgets. Residual: S1 |
| S1: possessive repeats | Resolved; verified |
| S2: wheel, version check | Resolved: builder stage, `importlib.metadata`, functional boot check, `requires-python` |
| S3: STRICT_WITHHOLD on the loop | Resolved: the scheduler is always the runner; test (c) |
| S4: aggregate loop bound | Resolved: per-org inline bucket ρ; test (a) with C streams |
| NIT 1–4 | Resolved: §4/§5.1 text, §5.2.2 dependency, hang-safe fuzz, `SystemError` regression, `BASE_DETECTORS` |

### Checked and OK

- **Parse-only check cost:** `check_limits` on the B1 pattern takes 1.7 ms, so the projector's safety path is safe.
- **Incremental scanner logic:** `incremental.py` uses the horizon `max(len − R, last stopper)`, resumes at `q ≥ last final end`, and keeps its state unchanged on a timeout.
- **`_collect`:** excludes `\Z` and non-multiline `$`, and collects consumers inside lookarounds and conditionals.
- **`_reach`:** marks lookbehinds that read right as unbounded.

### What I ran

- Created a detached worktree at `e2c3e2cf`, read §5, §9 and §10, the evidence directory and the author's reply.
- Provided tests: `incremental_fuzz.py` (3 new seeds).
- My own fuzzers: `bfuzz.py` (boundaries, 172k patterns); `incfuzz2.py` (incremental, 7 seeds); the revision-4 `fuzz2.py` and possessive fuzz.
- Targeted probes: `pos=` semantics; the raw and translated negated-alternation bug.
- Cost measurements: range-heavy translation cost and loop lag; the prefix-catastrophe probe on a real sentence.
- Changed no code and did not edit the plan.


---
_Generated by [Claude Code](https://claude.ai/code)_
