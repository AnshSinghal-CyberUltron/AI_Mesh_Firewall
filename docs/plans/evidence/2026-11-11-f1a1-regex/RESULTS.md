# Customer-regex translator prototype: results (revision 5, 11 Nov 2026)

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
