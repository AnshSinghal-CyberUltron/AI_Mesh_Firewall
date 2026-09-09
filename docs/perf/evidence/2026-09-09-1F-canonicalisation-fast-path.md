# Task 1F — the per-guard-pass fixed cost: 1.4–1.7× faster, output byte-identical

**Tag `[M]`** · offline bench, `InputScanner.scan_output`, n=200 after 20 warmup,
Xeon 8581C @ 2.30 GHz

## Why this was the next lever

1E left streaming first-token latency governed by one number: the fixed cost of a guard
pass. Every remaining trade is priced by it — the Pareto table in `secure_streaming.py`
shows that halving the flush threshold roughly doubles guard passes, so lowering the
per-pass cost moves the whole curve instead of sliding along it.

`cProfile` over 100 `scan_output` calls on 525 chars said where it goes:

| | cumtime | share |
|---|---:|---:|
| `scan_output` total | 0.482 s | 100% |
| `canonicalize_for_detection` | 0.324 s | **67%** |
| `unicodedata.category` | **315,000 calls** | — |

315,000 calls for 300 canonicalisations of a 525-char string is **2 per character**, in a
pure-Python loop, alongside a per-character `unicodedata.normalize("NFKC", ch)`. And 300
canonicalisations for 100 scans means the same text was canonicalised **3×** per scan.

## Result

| case | before | after | speedup |
|---|---:|---:|---:|
| ASCII 525 chars | 1.302 ms | **0.829 ms** | 1.57× |
| ASCII 2048 chars | 4.805 ms | **2.801 ms** | **1.72×** |
| Unicode 525 chars | 1.812 ms | **1.316 ms** | 1.38× |
| Unicode 2048 chars | 6.658 ms | **4.700 ms** | 1.42× |

No case regressed. Unicode improves too: it still takes the per-character path, but the
3×→1× collapse benefits it equally. After the change `canonicalize_for_detection` no
longer appears in the profile's top twelve; the leading cost is now
`_iter_transport_decodes`.

## The change

**1. An ASCII identity fast path, generated rather than written.** The per-character body
became `_canon_char(ch)`, and the table is derived from it at import:

```python
_ASCII_IDENTITY = frozenset(c for c in map(chr, range(128)) if _canon_char(c) == c)
```

A hand-written table would be free to drift from the rules it mirrors; a generated one
changes whenever they do. When every character of the input is in that set, the canonical
form *is* the input and the map is the identity — no Python loop, no `unicodedata` calls.
The membership test is a compiled character-class regex so the fast path pays no
per-character interpreter cost either.

Independently verified, without reference to the generator: the table is exactly the 95
printable ASCII characters plus `\t`, `\n`, `\r` = 98, with `DEL` and every other C0
control correctly excluded.

**2. `lru_cache(maxsize=32)` on `canonicalize_for_detection`**, collapsing the three
calls per scan to one plus two hits. `_canonicalize_with_map` is deliberately *not*
cached — it returns a mutable list, and handing the same list to two callers would be a
sharing hazard.

## Equivalence

`test_canonicalisation_equivalence.py` freezes a **verbatim copy of the pre-1F
implementation** as `_reference_canonicalize` and asserts the live one agrees on
`(canonical_text, index_map)`. A snapshot of expected outputs would only cover the inputs
captured at snapshot time; a reference implementation covers every input anyone adds
later.

| check | result |
|---|---|
| Every code point below 0x2000, exhaustively | identical |
| 748 corpus items | identical |
| 17 adversarial classes: G18 tag-block smuggling, G101 decorated alphanumerics, G21 small-caps, combining marks, zero-width Cf, fullwidth/circled folds, confusables, dashes, non-ASCII whitespace, kept vs dropped controls, past the truncation cap, astral + ZWJ | identical |
| `index_map` contract (length, in-range, monotonic) | holds |
| Streaming gate vs the **pre-1E** baseline, 748 + 2244 documents | byte-identical, every enforced verdict unchanged |

That last row is the strongest one: 1E and 1F **combined** are byte-equivalent to the
original behaviour.

## Regression

Full gateway suite, run with and without 1F (1E kept in place both times):

| | failures |
|---|---:|
| before 1F | 240 |
| after 1F | **240** |
| new | **0** |
| fixed | 0 |

4,457 passed. The 240 are pre-existing and belong to the `policy-driven-detection` WIP's
fail-toward-no-detection state, not to this change.

## A caveat I am not going to paper over

`lru_cache` is a process-wide cache with an internal lock, and scans run in a thread
pool. Under real concurrency two things are unmeasured here: lock contention, and whether
32 entries survive interleaving between the three calls of one scan (they are microseconds
apart in the same thread, so mostly yes — but "mostly" is not a measurement). The
principled alternative is to canonicalise once in `_scan_output_sync` and thread the
result through `detect_pii` / `detect_secrets` explicitly, removing the cache entirely.
That is a signature change across several call sites and is deferred; this caveat should
be settled by the task-10 load measurement, not by assertion.
