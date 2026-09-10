# Task 1F — the per-guard-pass fixed cost

## Problem (profiled)

`docs/perf/evidence/2026-09-09-1E-stream-first-token-fix.md` established that ~3.2 ms of
fixed cost per guard pass now prices every remaining streaming trade. Profiling one
`scanner.scan_output` on 525 chars (`cProfile`, 100 iterations):

| | cumtime | share |
|---|---:|---:|
| `scan_output` total | 0.482 s | 100% |
| `canonicalize_for_detection` | 0.324 s | **67%** |
| `_canonicalize_with_map` (tottime) | 0.197 s | 41% |
| `unicodedata.category` | **315,000 calls** | — |

Two independent inefficiencies:

1. **Per-character Python loop.** 315,000 `unicodedata.category` calls for 300
   canonicalisations of a 525-char string = **2 calls per character**. Plus a
   per-character `unicodedata.normalize("NFKC", ch)` and up to four dict/set lookups.
   For plain ASCII — nearly all LLM output — every one of these is a no-op.
2. **The same text is canonicalised 3× per scan.** `canonicalize_for_detection` is
   called 300 times for 100 `scan_output` calls (`detect_pii`, `detect_secrets`, and a
   third site) on identical input.

## Requirements

**R1 — output identical, by construction.** `_canonicalize_with_map` must return
byte-identical `(text, index_map)` for every input. The fast path must be *derived from*
the existing per-character logic, not a hand-written re-implementation of it, so the two
cannot drift.

**R2 — equivalence proven over the corpus plus adversarial Unicode.** All 748 corpus
items, the 2244 long documents, and a dedicated set covering every obfuscation class the
function exists to defeat: Unicode Tag block (G18), combining marks, zero-width /
invisible Cf, fullwidth and circled compat forms, decorated alphanumerics (G101),
confusables, small-caps (G21), dashes, and non-ASCII whitespace.

**R3 — the streaming detection gate still passes.** `stream_retention_equivalence.py`
byte-identical on both regimes, as for 1E.

**R4 — measurable win.** Re-profile; `canonicalize_for_detection`'s share must fall
materially and `scan_output` wall time with it.

**R5 — caching must be bounded.** Any memoisation is capped, so a stream of distinct
texts cannot grow memory without limit.

## Out of scope

Replacing the regex engine (Hyperscan / multi-pattern) — that is task 7 and targets the
`re.search` cost, which this profile shows is only 0.042 s of 0.482 s.
