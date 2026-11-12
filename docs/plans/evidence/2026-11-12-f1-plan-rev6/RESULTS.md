# Customer-regex translator prototype: results (revision 6, 12 Nov 2026)

This directory is plan PR #103's revision-6 evidence. It started as revision 5's (`docs/plans/evidence/2026-11-11-f1a1-regex/`), was extended on 11 Nov (`LIMITS6.md`, `clock/`), and every revision-6 number was re-run on the 12 Nov host under the heavy lock: see **`RERUN.md`** for the commands, durations, results and host load. The sections below up to "Revision 6" describe revision 5's prototype and are kept for reference.

Environment: Python 3.12.3 (Unicode 15.0.0), `regex` 2023.8.8 (Unicode 15.0). Host: 2 CPUs, shared with other jobs. Timings are thread CPU time unless marked wall.

## Files

| File | What it is |
|---|---|
| `translate_prototype.py` | The translator, the parse-only `analyze`/`check_limits`, and `stopper` |
| `_cased_table.py`, `gen_cased.py` | The cased-code-point table, generated at build time (`--check` for CI) |
| `incremental.py` | The incremental streaming scanner (`RuleScanner`) |
| `exhaustive.py` | Per-code-point classes, sets and two-sided boundaries |
| `exhaustive_boundary.py` | One-sided boundaries, every code point against each neighbour class |
| `icase_exhaustive.py` | Every case-insensitive single literal against every code point (Reviewer A's test) |
| `alt_exhaustive.py` | Every pair and triple of single-character items in an alternation |
| `fuzz.py`, `fuzz_gen.py` | Differential fuzz against `re` (hang-safe worker processes) |
| `incremental_fuzz.py` | Incremental scan against a whole-text rescan after every delta |
| `overshoot.py` | Timeout behaviour (thread CPU against wall) |
| `perf5.py`, `perf5.md` | Translation, compile, parse-only check, match speed and streaming costs |

## Exactness

| Check | Result |
|---|---|
| `\d \D \w \W \s \S` in and out of sets, negated, with and without `(?i)`, every code point | 0 mismatches (13 s) |
| `\b \B \bx x\b \Bx x\B` (two-sided) next to every code point, with and without `(?i)` | 0 mismatches (161 s) |
| One-sided boundaries: 30 patterns × 2 flag sets, each neighbour class interleaved with every code point, plus text edges | 0 mismatches (102 s) |
| Every case-insensitive single literal (2,927) against every code point | 0 mismatches (38 s) |
| Alternations of 2–3 single-character items (19-item vocabulary), plain, repeated, in a negative lookahead and after a literal, with no flag, `(?i)` and `(?s)` | 0 mismatches in 86,640 patterns (419 s) |
| Differential fuzz, 9 seeds × 6,000 patterns × 8 texts; grammar with possessive repeats, atomic groups, lookbehinds and single-character alternations | 0 mismatches in 401,408 pairs. 60 patterns hung inside `re` (skipped); 21 hit `regex`'s 1 s timeout where `re` took 0.7–1.7 s |
| Incremental scan vs whole-text rescan after every delta, 8 seeds × 6,000 patterns, random chunkings, plus real DLP shapes | 0 mismatches: 39,998 incremental streams (83%), 655,969 deltas; 59 skipped on a `regex` timeout |

## `regex` versions on Python 3.12

`[\p{L}\p{N}_]` against `re`'s `\w`, and `\p{Nd}` against `\d`, over every code point:

| `regex` | Unicode | `\w` differs on | `\d` differs on |
|---|---|---|---|
| 2023.8.8 | 15.0 | 0 | 0 |
| 2023.10.3, 2023.12.25, 2024.5.15 | 15.1 | 622 | 0 |
| 2024.11.6 | 16.0 | 5,004 | 80 |
| 2026.9.29 | 18.0 | 22,484 | 90 |

(Revision 4 said 2024.11.6 to 2026.9.29 differ on 5,004; that was wrong for the later releases, as Reviewer B found.)

## Bugs and workarounds found while building it

- **Case-insensitive classes.** Wrapping the categories in `(?-i:…)` is not enough with an explicit set: `regex` folds case into a set's members.
- **Turkish i, Kelvin and similar.** `re`'s special folds (i/ı/İ, k/K/K, s/ſ, …) are kept by taking `re`'s verdict on every cased code point (one `findall` per item).
- **The first-character prefilter** (`regex` 2023.8.8 and 2026.9.29). A leading optional case-insensitive item makes the prefilter fold the following case-sensitive set: `(?i:a)?[^\p{L}]` misses U+0345. Translated patterns that contain a case-insensitive part start with `(?=[\s\S]|\Z)`.
- **Possessive repeats** (Reviewer A). `re` backtracks into neither a possessive repeat nor its iterations; emitted as `(?>(?>X){m,n})`.
- **Lone negated characters in an alternation** (new, `regex` 2023.8.8 and 2026.9.29). `regex` merges alternatives that are single negated characters into one negated set: `(?:[^9]|[^c])` matches only characters that are neither, instead of every character. It under-matches, so it would be a bypass. A negated single-character set is emitted with its member repeated (`[^\x39\x39]`), which `regex` keeps as a set. The extended fuzz finds 10 mismatches in 2,000 patterns without the workaround and none with it.
- **`re` itself.** Some patterns hang inside `re` (seed 303 of revision 4's fuzz; one pattern in seed 1 here) and some possessive patterns with a capture raise `SystemError` on 3.12.3. The fuzz runs patterns in worker processes and counts both.

## Revision 6 (limits, compile cost, scheduling): see `LIMITS6.md`

| File | What it is |
|---|---|
| `LIMITS6.md` | Every revision-6 change with before/after numbers, the caps and why, the corrected cost table, the scheduling simulations, the S1 recommendation, the regression results and the plan text changes |
| `nesting_test.py` | The iterative depth scan against `re`'s own recursion; the nesting bombs; 16-deep shapes compiled from a 600-frame stack; every exception becomes `Refused` |
| `estimate_test.py` | Real translation ≤ the parse-only estimate (size and compile units); the case-insensitive patch premise; the parse-only check refuses what `re.compile` refuses |
| `alt_negated.py` | Every spelling of a one-code-point negation in alternations (A, rev. 5, NIT 1; `[^a-a]`) |
| `s1_test.py`, `s1_hold.py` | Reviewer A's S1: policy "refuse" against policy "hold", on A's case and the incremental fuzz corpus |
| `perf6.py`, `perf6.md` | Revision-6 cost tables: B's cases, the worst shape at each limit, plans at the per-plan limits, the parse-only check |
| `stall6.py` | Event-loop stall during a plan compile, against a busy pure-Python thread |
| `inline_bucket6.py`, `drr_bound.py` | The worker-wide inline bucket simulation and the DRR latency bound |
| `r6_*.txt`, `run_r6.sh` | The revision-6 regression and measurement outputs |
| `r5_orig/` | The revision-5 translator plus the A-B1 range fix (the 11 Nov "r5b" state), used by `perf6.py` for before/after. It is **not** the committed revision 5: that one is `rev5_e2c3e2cf/` |
| `rev5_e2c3e2cf/` | The revision-5 translator exactly as committed at `e2c3e2cf` (the A-B1 "before") |
| `range_b1.py`, `r6_range_b1.txt` | Reviewer A's revision-5 B1: the bisect range classifier against brute force, and A's 166-wide-range pattern and loop harness, revision 6 against `rev5_e2c3e2cf/` |
| `RERUN.md` | The 12 Nov re-run: every command, its duration, its result and the host load |
| `r6_META.txt`, `r6_progress.txt`, `r6_top_at_end.txt`, `r6_done.txt` | The re-run's interpreter and `.so` hashes, per-step timings and load, and the completion marker |
| `r6_inline.txt`, `r6_inline_cpu.txt`, `r6_inline_main.txt` | 11 Nov runs of `inline_bucket6.py` without the heavy lock (noisy; superseded by `r6_inline_locked.txt`) |
| `clock/` | Reviewer B's revision-5 blocker 1: the thread-CPU patch, its reproducible build, and every measurement (`clock/CLOCK.md`) |
| `scratch6/` | 11 Nov probes behind `LIMITS6.md` §1 (frames per nesting level, `depth_slope.py`) and §2 (compile-unit calibration) |
