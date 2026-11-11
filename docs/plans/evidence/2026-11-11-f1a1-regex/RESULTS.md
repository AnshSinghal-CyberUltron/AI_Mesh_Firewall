# Customer-regex translator prototype: results (11 Nov 2026)

Environment: Python 3.12.3 (Unicode 15.0.0), `regex` 2023.8.8 (Unicode 15.0). Host: 2 CPUs, shared with other jobs.

## Files

| File | What it is |
|---|---|
| `translate_prototype.py` | The prototype translator |
| `exhaustive.py` | The exhaustive per-code-point test |
| `fuzz.py` | The differential fuzz against `re` |
| `perf.md` | Speed and cost against `re` |

## Results

| Check | Result |
|---|---|
| `\d \D \w \W \s \S` (outside sets, inside sets, negated sets; with and without `(?i)`) on every code point | 0 mismatches |
| `\b \B \bx x\b \Bx x\B` next to every code point, with and without `(?i)` | 0 mismatches |
| Differential fuzz, 8 seeds × 4,000 random patterns × 8 random texts (`fuzz.py <seed> 4000`) | 0 mismatches in 244,000 checks |
| `regex` versions whose `[\p{L}\p{N}_]` / `\p{Nd}` equal `re`'s `\w` / `\d` on Python 3.12 | 2023.8.8 only, of the releases tried. 2023.10.3 to 2024.5.15 (Unicode 15.1) differ on 622 code points; 2024.11.6 to 2026.9.29 (Unicode 16) differ on 5,004 |

## Bugs and workarounds found while building it

- **Case-insensitive classes.** Wrapping the categories in `(?-i:…)` is not enough with an explicit set: `regex` folds case into a set's members.
- **Turkish i, Kelvin and similar.** `re`'s special folds (i/ı/İ, k/K/K, s/ſ, …) are kept by asking `re` for its verdict on every cased code point.
- **A `regex` prefilter bug.** A leading optional case-insensitive item makes the first-character prefilter fold the following case-sensitive set: `(?i:a)?[^\p{L}]` misses U+0345 in both 2023.8.8 and 2026.9.29. Translated patterns that contain a case-insensitive part start with `(?=[\s\S]|\Z)`, which disables the prefilter.
