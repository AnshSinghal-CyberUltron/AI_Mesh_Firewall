# Literal prefilter: skip regexes that provably cannot match

At the driver's real 4096-char input the policy stage is 93% regex matching. Half the
rules carry a literal that every match must contain, and on benign traffic none of those
literals is present — so half the patterns were being scanned for nothing.

## Soundness first

Skipping a rule whose regex would have matched is a **missed detection** — a security
failure, not a performance regression. So literals are extracted with `re`'s **own parser**
(`re._parser.parse`), never by scraping the pattern text.

An earlier heuristic — longest `[A-Za-z0-9 _-]{4,}` run — claimed **65 of 92** rules. It was
wrong: it lifts literals out of alternation branches, where they are not required at all.
`(?:alpha|beta)` would have yielded `"alpha"`, and every prompt containing only `beta`
would have skipped a rule that matches. The sound extractor yields **46 of 92**.

A literal is required only where the parser shows it is unconditionally traversed:

| construct | contributes? |
|---|---|
| top-level `LITERAL` run | yes |
| `SUBPATTERN` (group body) | yes — recurse |
| `MAX_REPEAT`/`MIN_REPEAT` min ≥ 1 | yes — recurse |
| `BRANCH` | only if **every** branch yields one; result is their **union** |
| `AT` (`\b`, `^`, `$`) | zero-width — neither contributes nor blocks |
| `IN`, `ANY`, `ASSERT`, min = 0 repeats | no — unfilterable, rule always runs |

## casefold, not lower

`re.IGNORECASE` matches `s` against `ſ` (U+017F). `'ſ'.lower()` is `'ſ'`, so a
`.lower()`-based filter would have **skipped a rule that genuinely matches**.
`'ſ'.casefold()` is `'s'`.

casefold disagrees in the other direction too — `'ß'.casefold()` is `'ss'`, which `re`
does *not* match — but that admits a rule that cannot match, costing one wasted scan.
**Every discrepancy errs toward running the rule.** Both cases are pinned by tests.
Non-ASCII literals are refused outright.

## Equivalence

`scripts/detection/policy_batch_equivalence.py`, real 127-rule bundle, prefilter and
batching both disabled in the control arm: **758 prompt-side + 120 response-side inputs,
identical on all 14 observable fields.** 48 unit tests. 0 test failures introduced
(79 pre-existing on this branch before any of today's policy work, unchanged).

## Result

| metric | baseline | +batching | +prefilter |
|---|---|---|---|
| **p50 addon** | 27.00 ms | 22.70 ms | **18.20 ms** |
| **p90** | 63.20 ms | 42.10 ms | **35.70 ms** |
| p99 | 102.10 ms | 58.50 ms | 62.60 ms |
| policy stage | 8.80 ms | 7.00 ms | **4.80 ms** |
| overhead_ms | 11.70 ms | 9.70 ms | **7.35 ms** |
| gateway CPU | 125.6% | 118.7% | **103.8%** |

`evaluate()` in isolation at 4096 chars: 7249.8 → **4609.7 µs** (36% off).

**p50 is under 20 ms for the first time.** The target is bounded on **p99**, which is
62.60 ms — within the 52.8–64.0 run-to-run spread of the previous arm, so p99 is
**unchanged** by this step. It is not met.

## What now dominates

`rate_limit`: **45% of per-request outliers**, median 0.70 ms but tail 6.70 ms. Four Redis
commands over two pipelined round-trips per request. It is not the median cost that hurts —
it is that the tail moved there.
