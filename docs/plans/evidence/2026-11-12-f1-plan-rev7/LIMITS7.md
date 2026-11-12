# Revision 7 evidence, finding by finding

Plan: `docs/plans/2026-11-11-f1-production-serving.md` (§ references are to it). Reviews answered:
Plan Reviewer A 5870922717 and Plan Reviewer B 5871123045, both at `967bea7b` (revision 6).
`ev6/` is `../2026-11-12-f1-plan-rev6/`. Every `r7_*.txt` file here comes from one run of `run_r7.sh`
under the heavy lock on the 12 Nov host; `RERUN7.md` records the command, each step's duration, its
result and the host load. Times are thread CPU unless marked wall.

## What is in this directory

| File | What it is |
|---|---|
| `translate_prototype.py` | The revision-7 translator (diff against `ev6/translate_prototype.py` shows every change) |
| `incremental.py` | The revision-7 streaming scanner (`scan_or_hold` bounds, `scan_pass`, `keep_final`, one-search last stopper) |
| `gen_cased.py`, `_cased_table.py` | Build-time tables; `--check` now also asserts the clip properties |
| `clock/` | The three patches (clock, match-memory cap, upstream 611-614), the build script, and the two probes re-run here (`asan_fuzz.py`, `cross_tenant.py`) |
| `run_r7.sh` | The whole revision-7 run (exactness on the candidate wheel, then the measurements, then ASAN) |
| `exhaustive.py`, `exhaustive_boundary.py`, `icase_exhaustive.py`, `alt_exhaustive.py`, `alt_negated.py`, `fuzz.py`, `fuzz_gen.py`, `incremental_fuzz.py`, `nesting_test.py`, `estimate_test.py`, `s1_test.py` | Revision 6's harnesses, copied unchanged so that they run against the revision-7 translator in this directory |
| `ci_clip_test.py`, `ci_model7.py`, `ci_cost7.py`, `renumber_test.py`, `mem7.py`, `s1_hold7.py`, `rewrite_check7.py`, `idle7.py`, `inline_fair7.py`, `drr_fast7.py`, `cost7.py`, `cache7.py`, `check_plan7.py` | The revision-7 harnesses (below) |

## Reviewer A

### B1: case-insensitive translation cost (§5.2.1, §5.2.3)
- **Cause, reproduced:** `re._compiler._optimize_charset` folds every code point of a case-insensitive
  range; revision 6 checked its CPU cap once per `_emit` call. `ci_cost7.py` part 1-2 (`r7_ci_cost.txt`)
  re-runs A's `ciwidth.py`, `capcheck.py` and `ciplan.py` cases against both translators.
- **Fix 1, clipping:** `verdict_source` clips each BMP-only range to the cased runs it covers. Exact by
  four properties checked over every code point (`gen_cased.py --check`, `r7_table.txt`; also part 1 of
  `ci_clip_test.py`): P1 outside the table a code point is its own lowercase; P2 the lowercase of a
  cased code point is cased; P3 `_sre.unicode_iscased` implies cased; P6 `re._casefix._EXTRA_CASES` is
  inside the table. Differential check `ci_clip_test.py 1 20000`: `r7_ci_clip_prefix.txt` (the run's first form: 12 mismatches in the random sets, the `_merge` bug) and `r7c_ci_clip_random.txt` (the same random sets after the fix: 0); see `RERUN7.md`.
- **Why BMP-to-astral ranges stay whole:** `ci_clip_test.py` part 1 lists the 5 code points where a split
  at U+FFFF would change `re`'s verdict (the `RANGE_UNI_IGNORE` tail's upper-case test).
- **Fix 2, runs:** heavy items are scanned as `(?:X)+` (one Python match per run, not per code point).
- **Fix 3, per-item budget checks:** `_check_budget` before every item of `_emit` and around each scan.
- **Fix 4, the cost model `ci_units`:** constants in `translate_prototype.py` (`_UNIT_*`); calibrated and
  validated by `ci_model7.py 4 300` (`r7_ci_model.txt`): part 1 measures each feature alone, part 2 fills
  300 random rules up to the cap and reports real/model; `ci_cost7.py` part 3 fills the cap with each
  worst shape.

### B2: idle timeout (§5.2.4)
`idle7.py` (`r7_idle.txt`) runs the real `StreamTimeouts` of this branch with a fake clock, with and
without keep-alives, and feeds keep-alive comments to the OpenAI SDK's decoder.

### S1: hold and retry (§5.2.4)
`s1_hold7.py 100` (`r7_s1_hold.txt`): A's attack streams and longer ones, revision 6's policy against
revision 7's, plus `scan_pass` with 10 rules. `s1_test.py` (`r7_s1_test.txt`) still passes.

### S4: upstream 611-614
`clock/regex-2023.8.8-upstream-611-614.patch` is the exact diff between PyPI's `regex-2026.7.19` and
`regex-2026.8.31` sdists (sha256-verified against PyPI's JSON) for `src/_regex.c` and
`regex/_regex_core.py`, re-pathed to the 2023.8.8 layout; it applies with `--fuzz=0`.

## Reviewer B

| Finding | Harness | Output |
|---|---|---|
| 1 match memory | `mem7.py corpus`, `mem7.py all 0`, `mem7.py all 16777216`, `mem7.py speed` (candidate and clock-only wheels) | `r7_mem_corpus.txt`, `r7_mem_nocap.txt`, `r7_mem_cap16.txt`, `r7_mem_speed.txt`, `r7_upstream_tests.txt` |
| 2 control REWRITE check | `rewrite_check7.py` | `r7_rewrite_check.txt` |
| 3 inline bypass | `inline_fair7.py` (Reviewer B's `xl/inline_fair.py` plus mode `bypass`) | `r7_inline_fair.txt` |
| 4 fast lane | `drr_fast7.py` | `r7_drr_fast.txt` |
| 5 cost per request | `cost7.py` (Reviewer B's `cost/cost_per_request.py` with scan points) | `r7_cost.txt` |
| 6 program cache | `cache7.py` | `r7_cache.txt` |
| 7 projector restart | `check_plan7.py` | `r7_check_plan.txt` |

## Found in revision 7
- A conditional on a group from inside that group: `re` and `regex` disagree. `renumber_test.py` found it
  (17 mismatches in 20,760 checks with revision 6's translator as well); now refused (`r7_renumber.txt`).
- Revision 6's "worst legal rule 16.9 ms" measured only narrow two-character sets; wide ranges cost
  6 ms per item and negated or category sets 1.4-2 ms per item (`ci_cost7.py`, `ci_model7.py`).
