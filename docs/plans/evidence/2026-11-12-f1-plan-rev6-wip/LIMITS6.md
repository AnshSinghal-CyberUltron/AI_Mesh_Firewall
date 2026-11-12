# Customer regex: limits, compile cost and scheduling (revision 6, 11 Nov 2026)

Answers Plan Reviewer B's blocker 2 and should-fixes 3 and 4, Reviewer A's S1 and NIT 1, and B's NIT 7, for plan PR #103 §5.2. Environment as `RESULTS.md`: Python 3.12.3, `regex` 2023.8.8 (stock wheel; the clock patch is another agent's work and is not assumed here). Timings are thread CPU unless marked wall. The host has 2 CPUs shared by about ten agents (load average 3–8 during these runs); wall-time numbers are noisy and are reported next to a baseline taken in the same run.

The revision-5 files are kept in `r5_orig/`. The swap into this directory is recorded in `LIMITS6_SWAPPED.txt`.

## Summary

| Item | Before (revision 5) | After (revision 6) |
|---|---|---|
| Nesting bomb, 499 or 400 levels | `RecursionError` escapes `check_limits`, `translate`, `stopper` and `analyze` | Refused by every entry point in < 1 ms. An iterative scan checks the depth (≤ **16**) before `re._parser` runs, and every entry point turns **any** exception into `Refused` |
| **New: nested-repeat compile bomb** | `(a)+` nested 16 deep is 49 characters and passes every limit. It compiles in 166–310 ms in **one C call that holds the GIL**, and retains 113–181 MiB. Nested 32 deep, it ran the host out of memory | Refused by a **compile-units** cap (≤ 32,768). The largest accepted C call measured is 4.2 ms (bound: 32,768 units × 158 ns ≈ 5.2 ms) |
| Translated size (B should-fix 4) | `\s`×500 → 39,000 characters, compile 53–85 ms; `\b.`×330 → 27,060 characters, compile 102–113 ms | Parse-only estimate of the translated size, ≤ **16,384** per rule and ≤ **131,072** per plan. Both cases are refused. Worst accepted compile: 51 ms per rule, 0.34–0.55 s per plan |
| Estimate against reality | — | Real ≤ estimate on 10,200 fuzz patterns (0 below). Median ratio 1.00 |
| **New: one-code-point ranges** (A NIT 1) | `(?:[^a-a]\|[^b-b])` emitted as is, so `regex` merged it and it under-matched (12,548 matches against `re`'s 12,550): a bypass | Every negated set that covers one code point is doubled, however it is written. Targeted test: 0 mismatches, 0 lone sets emitted |
| **New: variable-width lookbehind** | `(?<=a+)b` passed `check_limits` and was translated, though `re.compile` rejects it | Refused, as `re` does. Fuzz: 0 patterns accepted that `re.compile` refuses |
| Aggregate inline cap (B should-fix 3) | Per-org ρ = 25% only: 4 busy orgs took 98% of the loop | Worker-wide bucket. See §3: recommended aggregate 25% |
| A's S1 (non-monotone cost) | Refused at 28 characters; the whole sentence matches in 0.04–0.10 ms | Both policies prototyped. **Recommended: (b) hold and retry**; see §4 |

## 1. The nesting bomb (B blocker 2)

### Changes (`translate_prototype.py`)

- **`group_depth(pattern, limit)`.** An iterative O(n) scan that follows `re._parser` token by token:
  - a backslash and the next character are one token;
  - a set runs to the first `]` after at least one member, so `[]…]` and `[^]…]` are handled;
  - `(?#…)` runs to its first `)` token;
  - verbose mode is tracked per group (a global `(?x)` at the start, or scoped `(?x:…)` and `(?-x:…)`): whitespace is skipped and `#` comments run to the end of the line;
  - `(?P=name)` and global flags open no group; a conditional `(?(…)` opens one.

  It is exact for every pattern `re` accepts, and exact up to the point where `re` stops for any other pattern.
- **`MAX_DEPTH = 16`.** `_parse` refuses deeper patterns **before** `re._parser.parse` runs. `analyze`, `check_limits`, `translate` and `stopper` all parse through `_parse`.
- **`_refusing`.** `analyze`, `check_limits`, `translate`, `stopper` and the new `compile_rule` turn every exception into `Refused`:
  - `re.error` becomes "invalid pattern: …";
  - `RecursionError`, `MemoryError`, `SystemError`, `OverflowError` and anything else become "refused: <type> while processing the pattern".
- **`compile_rule(pattern)`** is the gateway path: limits, translation, a check that the real text is within its estimate, then `regex.compile`.
- **`check_plan(patterns)`** is the projector and control path. Each rule is checked; then the per-plan sums. Rules are refused from the last one back until the sums fit, so every caller refuses the same rules. Nothing escapes it.

### Why 16 and not 32

The limit must keep `regex.compile` of every accepted pattern clear of Python's default recursion limit (1000), even from a deep caller. Measured as frames per level of source nesting (`depth_slope.py` in `scratch6/`, every opener × quantifier combination), the worst shape is `(?i:a|…)*+`, translated to four groups per level:

| Step | Frames per source level (worst) |
|---|---|
| `re._parser` | 2 |
| `translate` | 6 |
| `regex.compile` | **18** |

- At 32 levels, `regex.compile` of that shape needs 586 frames. Its measured headroom is 405 frames, so it raises `RecursionError` when called from a 600-frame stack.
- At 16 levels it needs about 300 frames. All 8 worst shapes, and 204 random 16-deep patterns, compile from an absolute stack depth of 600 (`nesting_test.py` [3]).
- In production the gateway compiles on a scan-scheduler thread, which starts with fewer than 40 frames.

16 is far above real patterns: the fuzz grammar nests 3 deep, and the 15-pattern performance corpus 2.

### Tests: `nesting_test.py` (results in `r6_nesting.txt`)

1. **Scanner against `re`'s own recursion.** The oracle instruments `_parse_sub`/`_parse`. The grammar has sets with `]` first, `[(]`, `[\]]`, `[[]`; escapes; `(?#…)` comments containing `(` and `[`; verbose comments with `[` and `(`; named groups; `(?P=…)`; conditionals; and scoped verbose flags. Result: 20,000 patterns, 7,505 accepted by `re`, exact on all 7,505, and never below `re` on the rejected ones.
2. **The bombs.** Depths 499, 400, 100 and 17 are refused by every entry point within 50 ms, with the depth message. Neither a comment nor a verbose comment full of `(` counts as nesting. A `[` inside `(?#…)` or inside a verbose comment does not hide the nesting after it.
3. **The limit, compiled deep.** The 8 worst 16-deep shapes are accepted, translated, compiled by `regex` and passed through `compile_rule` from an absolute stack depth of 600. Of 300 random 16-deep patterns: 204 compiled from 600 deep, and 96 were refused by another limit (compile units or counted repetition). No `RecursionError`.
4. **Every failure is a refusal.**
   - `a{4294967296}` (`OverflowError` in `re`'s parser), `(?<=a+)b`, `(?L)a` and `a\` are refused by every entry point.
   - `RecursionError`, `MemoryError`, `SystemError` and `OverflowError` injected into `regex.compile` become `Refused` in `compile_rule`.
   - A 16-deep pattern analysed with 20 frames left becomes `Refused`.
   - A plan holding a 499-deep bomb and a 16-deep `(a)+` nest checks without raising: the two good rules are accepted and the two bombs refused.

**HEAD hotfix (B's suggestion).** The `validate_plan` part is independent of the translator. In `state_control/projector.py:565,578`, catch every exception per rule and count it as a refused condition; in `detect/conditions.py:108-111`, catch every exception from `re.compile`. Shipping this ahead of F1a-1 removes the live fleet-wide stamp poisoning.

## 2. Compile and match cost (B should-fix 4)

### 2.1 New finding: `regex` unrolls repeat minimums

`build_REPEAT` (`_regex.c:24906-25090`, 2023.8.8) builds a repeat's body **min times**, plus one looping copy when the maximum is larger. So nested repeats multiply the compiled program:

| `(a)+` nested *d* deep (49 characters at d = 16) | d = 10 | d = 12 | d = 14 | d = 16 | d = 32 |
|---|---|---|---|---|---|
| `regex.compile`, thread CPU | 1.5 ms | 7–9 ms | 35 ms | 166–310 ms | process OOM-killed |
| Of which the C call `_regex.compile` (GIL held) | 1.3 ms | 6.9 ms | 34.6 ms | (≈ all) | — |
| Peak RSS | 16 MiB | 24 MiB | 39–56 MiB | 113–181 MiB | > 2 GB |

- `re` compiles the same pattern in 0.2 ms.
- Every quantifier with a minimum ≥ 1 does this (`+`, `+?`, `++`, `{1,3}`, `{2}`); `*`, `?` and `*?` do not.
- Revision 5's limits count none of it: `+` is not a counted repeat. A tenant could have every gateway worker that compiles its plan stall for seconds in one GIL-holding call, or die.

### 2.2 The estimate

The parse-only check (`analyze`) now runs the translator's own emission walk in **estimate mode**:

- every construct is emitted exactly as in translation;
- the one step that needs `regex`, the case-insensitive patch in `_ci_set`, is replaced by padding of its largest possible size.

Two numbers come from the estimate text:

- **`translated_size`**: its length. It is an upper bound on `len(translate(p))` and drives the Python-side parse and compile cost, which is linear in it.
- **`compile_units`**: each character multiplied by the product of `_copies(min, max)` over the repeats around it. It is an upper bound on what `regex`'s C compiler builds. `_copies` is `min`, plus 1 when `max > min`; 1 when `min` is 0; 1 for `{1}`. Computed by an iterative scan of the emitted vocabulary.

**The case-insensitive patch bound.** `_ci_set` adds `(?:[add]|(?![drop])native)`. Measured over every cased literal and 20,000 random sets (BMP and astral literals and ranges, categories, negation; `estimate_test.py`), the add and drop sets only ever contain:

- the 10 code points where the engines' folding differs (`FOLD_DISAGREE`, now generated into `_cased_table.py`): I, i, U+0130, U+0131, U+0390, U+03B0, U+1FD3, U+1FE3, U+FB05, U+FB06. This applies when a member covers one of them;
- **an astral literal member and its case partners.** New finding: CPython 3.12's `re` drops an astral literal from a multi-member case-insensitive set. `(?i)[\U0001e91c\x41]` does not match U+1E91C, because `_compiler._optimize_charset` keeps the literal unfolded in the tail. The translator reproduces this (it is `re`'s meaning); the estimate pads for it.

**Translation fix found while building the estimate.** `_ci_set` measured `regex`'s verdict without the prefilter guard. So `regex`'s prefilter bug hid U+0345 from `(?:(?i:[_])|\W)`, and the translator patched in a code point the final, guarded pattern already matches. That was exact but redundant.

- `_ci_set` now re-checks only the missing points behind the guard.
- A full guarded scan would cost 14× more: 0.78 against 0.057 ms per item, 79 ms against 19 ms for 64 items.

**Evidence** (`estimate_test.py`, 3 seeds × 3,400; `r6_estimate.txt`): see §6. Real ≤ estimate on every pattern for both the size and the units. The estimate is exact for case-sensitive patterns: median ratio 1.00, p99 ≈ 2.8, max ≈ 3.7, the largest ratios being patterns with many case-insensitive `i`s.

### 2.3 Caps, and why

| Limit | Value | What it bounds (measured, `perf6.md`) |
|---|---|---|
| Group nesting | ≤ 16 | Stack: ≤ 300 frames for any step (§1) |
| Translated size per rule | ≤ 16,384 characters | Compile ≤ 51 ms (worst shape: consecutive two-sided `\b`, about 3 µs per translated character); translate ≤ 24 ms (64 distinct case-insensitive sets), against the 50 ms translation CPU cap |
| Compile units per rule | ≤ 32,768 | The GIL-holding C call ≤ 3.2 ms in the shape table and ≤ 4.2 ms in the plan runs. Worst ratio 124–158 ns per unit (greedy capture nests), so the cap bounds it near 5 ms, the switch interval |
| Translated size per plan | ≤ 131,072 | Plan compile 0.34–0.55 s (8 rules of `\b.` at the size cap; the other 56 are refused by the sum) |
| Compile units per plan | ≤ 262,144 | Plan compile ≤ 38 ms; largest C call 4.2 ms |
| Case-insensitive items per plan | ≤ 512 | As revision 5. Now enforced by `check_plan` |
| Length, counted repetition, expanded repetition, ci items per pattern | unchanged | unchanged |

- **Size cap.** It accepts every realistic pattern: a 1000-character case-insensitive keyword list estimates at ≤ 16,347; a 990-character literal alternation is 2,607.
- **Units cap.** It keeps the C call below the 5 ms switch interval, so a compile step cannot stall the loop longer than any pure-Python thread does. It accepts `(?:\w{3}\s){250}` (31,250), `[a-z0-9]{1000}` (20,000) and `x{1000}` (4,000). It refuses `\s{1000}` (78,000) and `(a)+` nested 12 deep.
- **Plan size cap.** The largest realistic plan seen, 64 rules of about 1k characters translated, is 64k: half the cap.

### 2.4 Corrected cost table (revision 6, at every limit)

Copied from `perf6.md` §2 (`r6_perf6.log`); it replaces §5.2.3's measured-cost table. The match columns use 32 KiB of prose, one unbroken 32 KiB word, and 32 KiB of code-like text.

| Shape | Src chars | Translated (estimate) | Units (estimate) | Translate | Compile total | of which C (GIL held) | Retained / peak KiB | Match 32 KiB: prose / word / code | `re` prose / word / code |
|---|---|---|---|---|---|---|---|---|---|
| `\s` repeated (set expansion) | 420 | 16,380 (16,380) | 16,380 (16,380) | 0.7 | 31.9 | 0.23 | 165 / 612 | 1.2 / 0.9 / 1.7 | 0.3 / 0.2 / 0.3 |
| `\b.` repeated (two-sided boundary) | 597 | 16,318 (16,318) | 16,318 (16,318) | 0.9 | 47.9 | 0.76 | 847 / 1899 | 16.8 / 11.1 / 19.4 | 0.9 / 0.4 / 0.9 |
| `\b` repeated (two-sided, consecutive) | 439 | 16,372 (16,372) | 16,372 (16,372) | 0.8 | 51.3 | 0.85 | 1126 / 2337 | 63.3 / 1.5 / 0.3 | 34.9 / 0.5 / 37.4 |
| `(?=\w)` repeated + `a` (lookahead) | 997 | 2,826 (2,826) | 2,826 (2,826) | 1.9 | 10.8 | 0.17 | 129 / 305 | 20.4 / 43.3 / 7.6 | 65.0 / 85.9 / 51.6 |
| `(?<=\w)` repeated + `a` (lookbehind) | 995 | 2,560 (2,560) | 2,560 (2,560) | 1.9 | 8.9 | 0.16 | 112 / 264 | 7.4 / 38.0 / 7.2 | 58.7 / 61.2 / 42.3 |
| `[^\W\d]` repeated (negated set with categories) | 994 | 5,538 (5,538) | 5,538 (5,538) | 1.1 | 21.0 | 0.16 | 158 / 382 | 13.6 / 3.7 / 9.6 | 3.1 / 0.7 / 1.9 |
| ci keyword list `(?i)\b(?:…)\b` | 565 | 7,239 (16,347) | 7,239 (16,347) | 6.0 | 39.5 | 0.23 | 92 / 309 | 50.6 / 11.3 / 86.3 | 12.1 / 0.5 / 13.8 |
| ci 64 distinct 2-char sets | 260 | 1,203 (1,331) | 1,203 (1,331) | 24.2 | 5.3 | 0.03 | 19 / 65 | 9.3 / 9.0 / 9.0 | 0.5 / 0.5 / 0.5 |
| ci astral literals in sets | 934 | 14,335 (16,381) | 14,335 (16,381) | 3.0 | 17.3 | 0.19 | 147 / 579 | 13.0 / 12.3 / 12.2 | 0.5 / 0.5 / 0.6 |
| `x{1000}` repeated (unrolled minimum) | 7 | 15 (15) | 4,000 (4,000) | 0.0 | 0.2 | 0.12 | 123 / 243 | 0.1 / 0.0 / 0.2 | 0.4 / 0.4 / 0.4 |
| `\w{3}\s` × 250-style counted group | 16 | 113 (113) | 31,250 (31,250) | 0.1 | 1.1 | 0.56 | 734 / 924 | 19.3 / 20.3 / 12.1 | 2.0 / 2.0 / 1.9 |
| `(a)+` nested (deepest accepted) | 34 | 81 (81) | 32,756 (32,756) | 0.2 | 4.0 | 3.19 | 1897 / 2956 | 10.0 / 22.4 / 4.9 | 12.6 / 13.6 / 10.9 |
| `(?:a|b)+` nested (deepest accepted) | 71 | 137 (137) | 23,526 (23,526) | 0.2 | 2.6 | 1.33 | 741 / 1527 | 6.5 / 19.0 / 4.3 | 11.3 / 16.6 / 11.3 |
| literal alternation, 1000 chars | 999 | 2,607 (2,607) | 2,607 (2,607) | 2.3 | 8.0 | 0.10 | 58 / 139 | 2.9 / 0.1 / 0.0 | 0.7 / 0.0 / 0.0 |
| `(?i:a|…)*+` nested 16 (stack worst) | 145 | 477 (477) | 461 (461) | 1.0 | 3.2 | 0.04 | 24 / 55 | 244.6 / 227.8 / 188.7 | 26.2 / 24.9 / 26.5 |
| `\b\w+\s+(?:token|secret)\b` (A's rule) | 26 | 185 (185) | 274 (274) | 0.1 | 0.4 | 0.01 | 7 / 16 | 4.5 / 4.0 / 4.1 | 1.3 / 0.8 / 1.1 |
| IBAN shape | 32 | 93 (93) | 313 (313) | 0.1 | 0.2 | 0.01 | 9 / 16 | 0.0 / 0.0 / 0.5 | 0.6 / 0.3 / 0.5 |

Worst over these shapes: translate 24 ms (64 distinct case-insensitive sets), compile 51 ms (two-sided `\b` at the size cap), C call 3.2 ms (`(a)+` nested at the units cap), 1.9 MiB retained, 2.9 MiB peak. A second run at a different host load gave the same shape ranking with thread-CPU figures within 1.5×.

Reviewer B's cases, revision 5 against revision 6 (`perf6.md` §1):

| Pattern | Rev. 5: passes limits? translated chars, compile ms, match 32 KiB ms | Rev. 6: verdict | Rev. 6 estimate (size / units) |
|---|---|---|---|
| `\s`×500 | passes; 39,000 chars, compile 54 ms, match 0.9 ms | refused: translated size 39000 over 16384 | 39,000 / 39,000 |
| `\b.`×330 | passes; 27,060 chars, compile 82 ms, match 13.0 ms | refused: translated size 27060 over 16384 | 27,060 / 27,060 |
| `(?=\w)`×166 + `a` | passes; 2,826 chars, compile 9 ms, match 17.9 ms | accepted | 2,826 / 2,826 |
| `(?<=\w)`×142 + `a` | passes; 2,560 chars, compile 5 ms, match 5.3 ms | accepted | 2,560 / 2,560 |
| 400-deep groups | **RecursionError** escapes | refused: groups nested deeper than 16 | — |
| 499-deep groups (projector poison) | **RecursionError** escapes | refused: groups nested deeper than 16 | — |
| 330-deep `(?:` | Refused: pattern longer than 1000 | refused: pattern longer than 1000 | — |
| `(a)+` nested 16 (new) | passes; 116 chars, compile 193 ms, match 415.6 ms | refused: compile units 1048564 over 32768 | 116 / 1,048,564 |
| `(?:a)+` nested 24 (new) | passes; compile not run (exponential: 16 levels 0.2-0.3 s, 32 levels exhausted memory) | refused: groups nested deeper than 16 | — |
| `(a)+` nested 32 (new: OOM-killed the probe) | passes; compile not run (exponential: 16 levels 0.2-0.3 s, 32 levels exhausted memory) | refused: groups nested deeper than 16 | — |

**Plans at the per-plan limits** (`perf6.md` §3). Each plan has 64 rules; `check_plan` refuses those beyond the plan sums:

| Plan | Rules accepted / refused by the plan sums | Translated chars | Units | Translate ms | Compile ms | Largest C call ms |
|---|---|---|---|---|---|---|
| 64 × the costliest per-character shape (`\b.` at the size cap) | 8 / 56 | 130,608 | 130,608 | 8 | 545 | 1.29 |
| 64 × `\s` at the size cap | 8 / 56 | 129,824 | 129,824 | 4 | 184 | 0.22 |
| 64 × `(a)+` nested at the units cap | 8 / 56 | 712 | 262,112 | 2 | 38 | 4.22 |
| 64 × `x{1000}` at the units cap | 64 / 0 | 1,688 | 256,728 | 5 | 23 | 0.27 |
| 64 × ci 64 distinct sets (512 per-plan ci items) | 8 / 56 | 9,415 | 9,415 | 202 | 46 | 0.04 |

The two runs gave 335 and 545 ms for the costliest plan (the same work under load averages of 7 and 4.6). The bound to state is about 0.5 s of thread CPU per plan per worker, against Reviewer B's 7 s; the largest single C call in any plan was 4.2 ms.

**Parse-only check** (`perf6.md` §4). The estimate walk costs 2–4.5× revision 5's check; the worst hostile plan takes 0.5 s, well inside the 4 s round box, and is memoised by body hash. The nesting bombs are now 100× cheaper (refused before parsing):

| Plan | Rev. 5 `check_limits` × 64, ms | Rev. 6 `check_plan` ms (cold caches) | Rev. 6 per rule worst ms |
|---|---|---|---|
| 64 rules: 1000-char `\b`-dense ci patterns (each boundary asks `re` for a verdict) | 115.8 | 513.6 | 16.7 |
| 64 rules: 990-char literal alternations | 78.8 | 149.2 | 2.4 |
| 64 rules: `\b.`×330 (refused) | 54.1 | 233.1 | 3.9 |
| 64 rules: 499-deep bombs (refused) | 76.5 | 0.6 | 0.0 |
| 64 rules: (?i) + 64 distinct 2-char sets | 17.0 | 38.8 | 0.9 |

**Match cost above the plan's stated 3.7× worst ratio** (new finding; `re` and the translation on the same 32 KiB of prose):

| Pattern | `re` | Translated | Ratio | Cause |
|---|---|---|---|---|
| `(?i)\b(?:invoice\|confidential\|…10 words)\b` | 2.15 ms | 13.55 ms | 6.3× | The prefilter guard `(?=[\s\S]\|\Z)`. Without it: 2.33 ms |
| `(?i)(?:…10 words)` | 5.02 ms | 18.56 ms | 3.7× | Same; 3.31 ms without it |
| 1000-character case-insensitive keyword list | 11.6 ms | 49.1 ms (code 79.7 ms) | 4–8× | Same |
| `(?:\w+\s?)*+x` | 4.72 ms | 20.23 ms | 4.3× | `(?>(?>X)*)` |
| `(?:a\|(?:a\|b)*+)*+`, 4 deep | 13.4 ms | 51.9 ms | 3.9× | Same |
| `(?i:a\|…)*+`, 16 deep | 25–39 ms | 213–250 ms | 6–10× | Same |

These break §5.8's performance gate (6×) and §5.2.1's "worst ratio 3.7×". They are a capacity matter, not an isolation one: the per-pass budget bounds them and charges the org. Two follow-ups:

- **The guard.** It is added to every pattern containing `(?i:`, but the prefilter bug needs a pattern that can *start* with an optional case-insensitive item followed by a case-sensitive set. Narrowing the guard to that shape would remove the 4–8× on keyword lists. It changes the output, so it needs the full exactness suite. Not done here.
- **The gate corpus.** Add the keyword-list and possessive-nest shapes, and state the ratio the gate allows for them.

### 2.5 Loop stall during compile

`stall6.py` measures a worker thread translating and compiling a plan at the limits rule by rule (with and without a `time.sleep(0)` yield), against a busy pure-Python thread of the same duration, alternating in one process.

[TABLE: stall6, final run]

What bounds one rule's stall:

- The Python part of translation and compile is interrupted at every switch interval (5 ms), like any thread.
- The only part that cannot be interrupted is the C call `_regex.compile`, which the units cap bounds: ≤ 4.2 ms measured, about 5 ms by the worst ratio, which is the switch interval.

So a compile step stalls the loop no longer than a busy pure-Python thread does: about the switch interval plus host scheduling. B's 9–15 ms and revision 5's 7.3 ms are both this same quantity at different host loads. What revision 5 lacked was the cap on the C part: one nested-repeat rule held the GIL for 166–310 ms.

**§5.2.3 should state the bound as** "the switch interval plus the largest C compile call (≤ 4.2 ms measured at the units cap)", measured against a pure-Python baseline in the same run, not as an absolute millisecond figure.

### 2.6 Bytes per compiled rule (for the byte-bounded cache)

Retained bytes after `regex.compile`, from `tracemalloc` (which sees `regex`'s `PyMem` allocations), per shape at the limits (`perf6.md` §2):

- worst: 1,897 KiB retained, 2,956 KiB peak, for `(a)+` nested at the units cap;
- two-sided `\b` at the size cap: 847 KiB;
- ordinary rules: under 20 KiB.

Retained bytes track the compile units: 10–70 bytes per unit over the 17 shapes, the most for consecutive two-sided `\b` (1,126 KiB for 16,372 units) and nested capture repeats (1,897 KiB for 32,756). **Charge each entry `80 × compile_units + 16 KiB`**: it is known before compiling (the projector already has it) and covers every measured shape. Where the deployment can afford tracing, correct it by the measured `tracemalloc` delta. Revision 5's "≤ 1.3 MiB per pattern" becomes ≤ 2 MiB retained and 3 MiB peak per rule. The per-plan units cap bounds a plan's charge at about 21 MiB.

## 3. Aggregate inline cap and the DRR bound (B should-fix 3)

### 3.1 Worker-wide inline bucket

`inline_bucket6.py` extends B's `inline_bucket.py`:

- **Unchanged from B's simulation:** the per-org bucket (25%, burst 10 ms, debited with thread CPU), and K busy orgs with 4 streams each at 50 deltas/s.
- **New: a calibrated delta.** Each delta costs about 4.5 ms, just under the inline budget, so no inline call times out: the loop's worst case.
- **New: a worker-wide bucket.** An inline call runs only when both its org's bucket and the worker bucket hold credit; otherwise it goes off the loop.
- **Measured:** tenant B's 1 ms sleeper lateness. Median of 2 runs of 3 s each, under the heavy lock.

[TABLE: inline, locked run]

**Reading the numbers.**

- **The loop share.** It is CPU-accounted, and robust: the worker bucket holds the aggregate at its rate (20–25% at 25%, against 38–87% with per-org buckets only), whatever the number of busy orgs.
- **Tenant B's wall delay.** It is dominated by the host: two unlocked runs at load averages 3.3 and 5–6 gave p99 of 39–310 ms for the same configurations. "Loop CPU ahead of B" removes the preemption: it is the loop thread's own CPU between B's sleep and its wake-up. Medians: with no worker bucket, 18–124 ms (p50) as orgs are added; with 25%, 0.1–12 ms (p50) and 16–59 ms (p99).
- **40% and 50%.** They admit more inline work and give B proportionally more delay (p50 up to 24–28 ms of loop CPU at 6 orgs) without a benefit the plan needs.

**The bound.** Debits are made after each call (thread CPU), at most c = 5 ms per call (the inline budget). On a loop that has its core, a run of k consecutive inline calls refills ρ·k·c while it spends k·c. So k < burst / (c·(1 − ρ)) + 1, and B waits behind at most **burst / (1 − ρ) + c = 18.3 ms of loop CPU** (burst 10 ms, ρ = 25%). Hence the measured p99 of 15–22 ms in the quieter run.

**Recommendation: a worker-wide inline bucket at 25% of one core, burst one scan window (10 ms).**

- **Hardening.** Refill both buckets by the loop's *available* time (its thread CPU plus the time it spends idle in the selector), not by wall time. On an oversubscribed host an inline call stretches in wall time, and a wall-clock bucket refills during the very calls it limits. That is why the noisy runs exceed the bound.
- **The lag gate.** Skipping inline scans while the loop's last lateness exceeds 5 ms gave the same p99 as the plain 25% bucket (14–22 ms of loop CPU), so it is not needed on top.


### 3.2 The DRR latency bound (§5.2.5)

**The bound.** Let T be the scan threads, A the other active orgs, b the largest per-pass budget among the passes ahead, and Q the DRR quantum, with Q = b. A pass of tenant B waits for the passes running when it arrives (at most b each, on T threads), then for one DRR round. In one round each org runs less than Q + b, because DRR debits after the pass, from its measured CPU. So:

  **wait ≤ (T·b + A·(Q + b)) / T = (T + 2A)·b / T**

which for T = 2 is **(A + 1)·b, that is (active orgs including B) × (per-pass budget)**.

**Its inputs.**

- b is set by the **other** orgs' pass sizes, not B's: guard_wait_budget_s × max(1, len/128 KiB) is 100 ms for passes up to 128 KiB (32 KiB and 128 KiB alike), and 400 ms at the 512 KiB ceiling.
- The unit is thread CPU per scan thread. It equals wall time when each scan thread has a core. With more scan threads than cores, multiply by T/cores.

**Checked by a discrete-event simulation** (`drr_bound.py`, `r6_drr.txt`): DRR with debit after the pass, at most T − 1 threads per org, and B inserted last in the round.

| T | Active orgs (incl. B) | b = 100 ms (32 KiB, 128 KiB): simulated worst / bound | b = 400 ms (512 KiB): simulated worst / bound |
|---|---|---|---|
| 2 | 2 | 0 / 200 ms | 0 / 800 ms |
| 2 | 4 | 200 / 400 ms | 799 / 1,600 ms |
| 2 | 8 | 398 / 800 ms | 1,592 / 3,200 ms |
| 2 | 16 | 780 / 1,600 ms | 3,118 / 6,400 ms |
| 2 | 32 | 1,475 / 3,200 ms | 5,898 / 12,800 ms |
| 4 | 8 | 200 / 450 ms | 799 / 1,800 ms |
| 4 | 32 | 750 / 1,650 ms | 2,999 / 6,600 ms |

- The simulated worst is about half the bound: the half that covers the deficit carry-over is rarely used.
- Revision 5's "one running pass per thread, then one DRR round" is right, but needs the formula and these numbers. At 16 active orgs with 512 KiB passes, B can wait 3–6 s.
- That is a capacity statement. Admission's per-org concurrency cap and the number of scan threads are the levers, and §9 should say so.

## 4. A's S1: non-monotone pattern cost

**Prototype.** Both policies are in `incremental.py`:

- **(a) `scan()`, unchanged revision-5 behaviour.** A timeout refuses. Documented-limitation test: `s1_test.py` (a).
- **(b) `scan_or_hold(text, timeout, final)`.** A timeout on a pass that is not the final one returns `Undecided(hold_from=q)`. Nothing at or after the resume point q is released; every attempt before q is final and decided. The pass is retried only when:
  - a stopper arrives after the failed pass (at most once per growth step in the "once" variant);
  - the text has grown 2× since the failed pass;
  - or the stream ends.

  Only the final pass refuses. State is unchanged by a timeout, so a retry resumes at the same q.

**Why (b) is exact.**

- Exactness of decided results: `scan()` is unchanged, and `scan_or_hold` only decides through it. Every decided hit list was compared with a whole-text `finditer` in `s1_hold.py`: 0 mismatches. `incremental_fuzz.py` on seeds 11–13 is rerun in §6.
- Refusal: the final pass resumes at q and does a subset of the whole-text scan's work (the attempts from q on are the whole-text scan's own attempts), under the whole text's budget. So (b) refuses only where one non-streaming scan of the same text would.

**Measured** (`s1_hold.py`, `r6_s1_hold.txt`; A's case at the plan's 100 ms budget, 4-character deltas):

[TABLE: S1 case + corpus, final run]

### Recommendation: (b), "hold-once", with a dedicated undecided-hold cap of 4 KiB

- **(a) refuses streams that are not refused non-streaming:** A's sentence, and in the corpus [N] of [M] streams whose passes timed out. That contradicts requirement 4 as written. Keeping (a) means rewriting requirement 4 to "refused when a pass over the text received so far exceeds its budget".
- **(b) removes every such refusal: 0 in the corpus.** It costs, only on streams where a pass times out:
  - **CPU.** More timed-out passes, each at most one per-pass budget, charged to the org's own scheduler share. The bound with "once" is ≤ 2·log2(L_cap/L₀) + 1 timed-out passes per rule per stream, where L₀ is the length at the first timeout. With L₀ = 28 and a 4 KiB cap, that is ≤ 15 passes, at most 1.5 s at 100 ms. Revision 5 spends 1.
  - **Hold latency.** The undecided rule releases nothing past q until it is decided. In A's sentence that is the whole 108 characters (21 deltas); since the match covers the whole sentence, a correct engine must withhold it anyway. In the corpus see the table.
- **Why "once".** "always" (A's literal rule) retries on every stopper-bearing delta. Its retries are not bounded by the growth steps. In prose with commas it would spend one budget per comma for a rule that stays expensive.
- **Why a dedicated cap.** The existing holdback cap (`hold_cap_tokens`, default 3, relaxed ceiling 6 tokens, `runtime/holdback_config.py:22`) bounds word-class pattern holds. It is far too small for this: A's sentence needs about 28 tokens. So (b) needs its own cap. Over 4 KiB of undecided text (about a paragraph) the stream is refused with `policy_regex_timeout`, as in (a), and with the same audit reason.
- **Requirement 4 becomes:** "a stream is refused for regex cost only where the same text, scanned once non-streaming, would be refused, or where a rule stays undecided over 4 KiB of held text".

## 5. A's NIT 1: every one-code-point negation

- **What was missing.** Revision 5 doubled a negated set only when its text was exactly one escape. A one-code-point **range** (`[^a-a]`, and `[^\U0001d7ce-\U0001d7ce]`) went out as is. `regex` treats it as one character and merges `(?:[^a-a]|[^b-b])` into `[^ab]`: 12,548 matches against `re`'s 12,550 on the alternation test text. That is an under-match, a bypass.
- **The fix.**
  - `_parts` turns a one-code-point range into a literal.
  - `_neg_plain` doubles any negated plain part that covers one code point, whatever its spelling.
  - Every negated set the translator emits goes through `_build`. The case-sensitive `NOT_LITERAL` branch now calls `_cs_set` instead of writing its own doubled text.
- **Test: `alt_negated.py`.** It covers every spelling (`[^9]`, `[^9-9]`, `(?:[^9])`, `(?:(?:[^9]))`, `([^9])`, `[^\U0001d7ce]`, `[^\U0001d7ce-\U0001d7ce]`, `[^99]`, `[^9-99]`, `[^k]`, `[^K]`, `[^ı]`, `x`). Each runs in pairs and triples; plain, repeated, in a negative lookahead, after a literal and in a lookbehind; under no flag, `(?i)` and `(?s)`. Results: [R] patterns, 0 mismatches against `re`; 0 one-code-point negated sets in any translation, here or in 3,000 fuzz patterns. The raw `regex` bug still fires 3/3, so the workaround stays needed.
- **A's cases.** Before revision 6 they were already covered: `(?:(?:[^9])|(?:[^c]))` → `(?:[^\x39\x39]|[^\x63\x63])`, and `(?:[^\U0001d7ce]|[^a])`. Only the range spelling was not.

## 6. Regression (under `flock /home/claude/work/.heavy.lock nice -n 10`, on the swapped files)

[TABLE: regression results]

## 7. B's NIT 7: projector validation order

**Proposed rule for §5.2.3.**

- Pending plan bodies are validated **oldest-pending-first**: by the time the org's current unvalidated body was first observed, ties by slug.
- A newer body from the same org replaces the pending one but **keeps its timestamp**. So re-publishing every round neither loses an org its place nor jumps the queue.
- Each org has at most one pending body.
- The round validates in that order until its time box (a quarter of `lag_ms`) is spent.

**Bound.** A pending org waits at most ⌈(orgs pending ahead × worst plan check) / time box⌉ rounds. With the worst measured plan check (`perf6.md` §4) and a 4 s box, that is one round for up to [K] pending orgs.

**Test.** 20 orgs publish in the same round, in reverse slug order, with plans whose check exceeds the time box. They are validated in first-seen order.

## 8. Exact text changes for the plan

### §5.2.1

1. Under "Two `regex` bugs, worked around", replace the "Lone negated characters" bullet's last two sentences with:
   > The translator emits every negated set that covers one code point with its member repeated (`[^\x39\x39]`), however the pattern spells it: a lone escape, a one-code-point range (`[^a-a]`, which revision 5 missed: `(?:[^a-a]|[^b-b])` under-matched), through non-capturing groups and with astral members. All negated sets go through one function. `alt_negated.py` tests every spelling in pairs and triples, and checks that no translation contains a one-code-point negated set.
2. Add a bullet:
   > **`re`'s own case-insensitive astral bug, kept.** CPython 3.12's `re` drops an astral literal from a multi-member case-insensitive set (`(?i)[\U0001e91c\x41]` does not match U+1E91C). The translator reproduces `re`'s verdict, as for every case-insensitive item.
3. Under "Refused at translation", add: "a variable-width lookbehind (`re.compile` refuses it after parsing succeeds; revision 5 translated it)".
4. Replace "Match speed against `re` … the worst ratio is **3.7×**" with:
   > On the 15-pattern corpus the worst ratio is 3.7×. Outside it, case-insensitive keyword alternations are 4–8× slower than `re` because of the prefilter guard (2.3 ms without it, 13.6 ms with it, against `re`'s 2.2 ms), and possessive nests 4–10×. Both are bounded by the per-pass budget and charged to the org. Narrowing the guard to patterns that can start with an optional case-insensitive item is a follow-up.

### §5.2.3

5. Add rows to the limits table:

   | Limit | Value | Why |
   |---|---|---|
   | Group nesting depth | ≤ 16, checked by an iterative scan **before** `re._parser` | Reviewer B: 499 levels raised `RecursionError` in `re`, the projector and the gateway. `regex.compile` needs up to 18 frames per level; 32 levels need 586 frames |
   | Translated size (parse-only estimate) | ≤ 16,384 per rule, ≤ 131,072 per plan | Reviewer B: `\s`×500 became 39,000 characters (85 ms compile); `\b.`×330 became 27,060 (113 ms) |
   | Compile units (parse-only estimate) | ≤ 32,768 per rule, ≤ 262,144 per plan | `regex` unrolls repeat minimums: `(a)+` nested 16 deep (49 characters) compiled in 166–310 ms in one GIL-holding C call, and 32 deep exhausted memory |

6. Replace "One parse-only check, in three places" with:
   > `check_limits` and `check_plan` parse with `re._parser` after the depth scan, walk the tree for the refusal rules and limits, and run the translator's emission walk in estimate mode. In that mode the one `regex`-dependent step, the case-insensitive patch, is replaced by its proven maximum, so the estimate bounds the real translation without importing `regex`. Any exception becomes a refusal.

   The rest of the bullet (three callers, byte-identical copy, CI agreement test) stays. Add to it: "CI asserts real ≤ estimate on the fuzz corpus".
7. Replace the "Measured cost at the limits" table with §2.4's tables (`perf6.md`). Keep the parse-only rows with revision 6's values.
8. In "The projector's safety path":
   - replace "16.5 ms for a hostile 64-rule plan" with the `perf6.md` §4 figure;
   - add: "Plans are validated oldest-pending-first (§7 of LIMITS6), not in slug order".
9. In "Gateway compile", replace the GIL-yield bullet with:
   > Each rule is its own step, with a GIL yield between rules. The Python part of translation and compile yields at every switch interval. The C call `_regex.compile` holds the GIL to the end, and the compile-units cap bounds it at ≤ 4.2 ms (measured; about 5 ms by the worst cost per unit). So a compile step stalls the loop no longer than any busy pure-Python thread, which the stall test compares against in the same run.
10. Replace "≤ 1.3 MiB per pattern at the limits" with:
    > charged `80 × compile_units + 16 KiB`, known at publish (≤ 2 MiB retained and 3 MiB peak per rule at the limits; at most about 21 MiB per plan).

### §5.2.4

11. Replace the bullet "So a stream is refused for regex cost only when one pass over its text exceeds the per-pass budget…" with:
    > **Timeouts on non-final passes (Reviewer A, S1).** Pattern cost need not grow with the text: `^(\w+\s?){1,30}[.!?]` times out on six words and matches the whole sentence in 0.06 ms. A timeout on a pass that is not the final one leaves the rule undecided. The stream releases nothing at or after that rule's resume point. The pass is retried when a stopper arrives (once per growth step), when the text has doubled, or at the end of the stream. Only the final pass refuses; it does a subset of a whole-text scan's work under the whole text's budget. Over 4 KiB of undecided text the stream is refused with `policy_regex_timeout`. A stream is therefore refused for regex cost only where the same text, scanned once non-streaming, would be refused, or where a rule stays undecided over 4 KiB.
12. Correct requirement 4 in §5.2 to the same sentence.
13. NIT 6 / B blocker 1 (the clock agent's change) also lands in this section; not covered here.

### §5.2.5

14. Under "Inline share", add:
    > A **worker-wide inline bucket** (rate [chosen] of one core, burst one scan window) sits on top of the per-org buckets. When it is empty, every org's customer scans go off the loop. Measured: [numbers].
15. Replace the "Bound" bullet with the formula and table of §3.2.

### §5.8

16. In "Semantics against `re`", add:
    - `alt_negated.py`;
    - the estimate test (real ≤ estimate on the fuzz corpus; patch points ⊆ `_ci_patch_points` on random sets);
    - "the parse-only check refuses every pattern `re.compile` refuses".
17. In "Regex DoS", add:
    - the nested-repeat compile bomb (`(a)+` nested 16) is refused at publish;
    - no accepted pattern's `_regex.compile` call exceeds the measured bound.
18. In "No false refusals", add:
    > Reviewer A's S1 stream (`^(\w+\s?){1,30}[.!?]` on the quarterly-report sentence, 4-character deltas, 100 ms budget) is decided with the whole-text hit list. With a comma before the terminator, it is refused at the final pass, exactly where the whole-text scan is.
19. In "Limits", add:
    - **The nesting tests:** 499 and 400 deep are refused by control, the projector and the gateway, and the projector heartbeat stays fresh. A 16-deep worst shape compiles from a 600-frame stack. Any exception from parsing, translating or compiling is a refusal.
    - **The case table:** B's `\s`×500 and `\b.`×330, A's 166 wide ranges, and the nested-repeat bomb are refused.
    - **Projector order:** oldest-pending-first.
20. In "Performance gate", add the keyword-list and possessive-nest shapes, with the ratio allowed for them, or after the guard is narrowed.

### §9

21. Replace the "Hostile plans slow the projector or the gateway" mitigation with:
    > An iterative nesting check before any parser runs; every exception is a refusal. A parse-only estimate of translated size and compile units (`regex` unrolls repeat minimums), capped per rule and per plan. The parse-only check is memoised and time-boxed per round, validating oldest-pending-first. Single-flight, per-rule, GIL-yielding lazy compile on the scheduler, whose one uninterruptible call the units cap bounds at about 4–5 ms. A byte-bounded program cache charged by compile units.
22. Add a row:
    | Risk | Mitigation |
    |---|---|
    | Translated match cost exceeds `re`'s by more than the gate allows for some shapes (case-insensitive keyword lists 4–8×, because of the prefilter guard) | Bounded by the per-pass budget and charged to the org. Follow-up: narrow the guard; widen the gate corpus |
23. Add a row:
    | Risk | Mitigation |
    |---|---|
    | A non-monotone rule holds a stream while undecided | Retries at stoppers and on doubling; refused over 4 KiB of undecided text |
