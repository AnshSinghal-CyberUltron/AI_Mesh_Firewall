# Streaming after tasks 8/8b — the tail is solved, first-token is untouched

**Tag `[M]`** · `drive.py`, n=10 + 2 warmup, sequential, `full` trace mode, stub
duration 3 s, 45 policies, Tier-2 off, gateway 4 vCPU · baseline is task 1E

## Result

| tokens | HEAD (1E → now) | **TAIL (1E → now)** | ADDED WALL CLOCK | guard_accum |
|---:|---|---|---|---|
| 100 | 962.33 → **933.68** (−3%) | 34.69 → **4.45** (**7.8×**) | 138.41 → **52.31** (2.6×) | 6.80 → 4.50 |
| 200 | 509.95 → **481.95** (−5.5%) | 46.00 → **4.52** (**10.2×**) | 141.65 → **37.05** (3.8×) | 14.90 → 9.50 |
| 300 | 358.13 → **331.61** (−7%) | 50.21 → **4.40** (**11.4×**) | 142.01 → **31.71** (4.4×) | 23.50 → 14.60 |

**The tail is now flat at ~4.4–4.5 ms regardless of answer length.** It was 34–50 ms and
rising with token count.

## Why the tail moved and the head did not — predicted before the run

**Tail:** `_redact_trace_text` runs while *building* the pipeline_trace, which happens after
the response completes. That is the tail by definition. Tasks 8/8b removed it, so the same
fix that halved non-streaming CPU pays out again here — on a path it was not designed for.

**Head:** unchanged, as predicted. `drive.py` issues requests **sequentially**, so there is
no queueing for a CPU saving to relieve, and first-token latency is set by task 1E's
constants — the 512-byte retention cap and the 160-byte flush threshold. Neither depends on
per-request CPU.

The prediction was recorded before the numbers arrived. It was right about HEAD and did not
anticipate the TAIL collapse, which in hindsight follows directly from where trace building
sits in the request.

## What remains, stated exactly

Streaming first-token latency is **one thing now: the hold-back.**

`_release_with_lookahead_tail` retains up to `STREAM_LOOKAHEAD_BYTES` (512) so a PII token
that is *starting* at the buffer edge cannot be half-streamed before a later scan can block
it. With ~6-byte deltas that is ~86 chunks of content the client cannot see yet.

Shrinking it **trades detection safety for latency**. That is a decision to argue with the
corpus equivalence gate in hand — not an optimisation to slip in behind a latency number.
Task 1E already made the retention content-derived (from a flat 512 to
`min(512, trailing_run + 64)`); going further means changing what the guard can catch.

## Position against the goal

| | |
|---|---|
| non-streaming p50 | **7.5 ms — meets the <20 ms goal** |
| non-streaming p99 | 26.4–40.8 ms — 1.6× over |
| streaming first token | 332–934 ms — the open problem |
| streaming tail | **~4.5 ms — solved** |
| CPU per request | ~27.3 ms, from ~56 (A/B controlled) |
