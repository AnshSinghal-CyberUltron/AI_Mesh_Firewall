# The output guard runs once per TOKEN, not once per 64 — task 1C diagnosis

**Tag `[M]`** · Docker `aimeshperf`, 45 policies, block posture, Tier-2 OFF, streaming, 100 tok/s

## Correction to the previous evidence file

`2026-09-09-output-guard-superlinear.md` attributed the cost to **O(n²) from a growing scan buffer**:
*"each flush scans the cumulative buffer … total work ≈ Σₖ(k·64)"*.

**That mechanism is wrong.** It was inferred from `full_text = "".join(self._content_buffer)` without
checking what remains in the buffer after a flush. Direct instrumentation refutes it.

## What the instrumentation shows

A temporary `LOG.info` in `_flush_buffer` recording flush index, scanned length and queue depth:

| tokens | **flushes** | scan_chars | queue depth | guard_accum | **ms/flush** |
|---:|---:|---:|---:|---:|---:|
| 100 | **37** | ~522 | 87–88 | 112.5 ms | 3.04 |
| 200 | **137** | ~525 | 75 | 454.6 ms | 3.32 |
| 300 | **237** | ~525 | 75 | 793.2 ms | 3.35 |

Two facts, both contradicting the earlier file:

1. **`scan_chars` is CONSTANT (~525).** The buffer does not grow; the lookahead retention works as
   documented. There is no O(n²) scan.
2. **Per-flush cost is CONSTANT (~3.2 ms).** The cost is linear in *flush count*.

The apparent super-linearity was an artefact of the offset: flushes ≈ **n − 63**, and (n−63) grows
faster than proportionally against n at small n (37 → 137 is 3.7×, not 2×). Fitting an exponent to
three points of a linear-with-offset relation produced a spurious "a ≈ 1.9".

## The actual bug

```python
if len(self._chunk_queue) >= self._max_buffer_chunks:      # :285, default 64
    async for flushed in self._flush_buffer(FlushReason.BUFFER_LIMIT): ...
```

`_release_with_lookahead_tail` retains a trailing **`STREAM_LOOKAHEAD_BYTES` = 512** window. For
token-sized deltas (~6 bytes) that tail is **75–88 chunks** — observed directly in `queue=75`,
`queue=87`, `queue=88`.

**512-byte retention ≈ 85 chunks > `max_buffer_chunks` = 64.**

So once the queue first fills, the retained tail alone keeps it permanently at or above the limit, and
**every subsequent chunk triggers a full flush**. The guard runs once per token for the whole
remainder of the stream. Two constants that were chosen independently — a byte-denominated lookahead
and a chunk-denominated flush trigger — are in direct conflict whenever deltas are small.

Total cost = `(n − 63) × ~3.2 ms`. At 300 tokens that is **793 ms of CPU per request**.

## Why this matters more than the earlier framing

The earlier file argued the guard "becomes the critical path at ≈1,200 tokens". With the correct
model the picture is different and simpler:

- cost is **linear in tokens** (slope ~3.2 ms/token beyond the first 63), not quadratic
- but the **constant is enormous** — one full guard pass per token
- at 100 tok/s generation, 3.2 ms/token of guard work against 10 ms/token of generation means the
  guard consumes **~32% of a core continuously** for the whole stream

This is a throughput bug first and a latency bug second. It is also a *much easier* fix than the
quadratic story implied.

## Fix direction (task 1C, revised)

The two constants must be reconciled. Options, cheapest first:

1. **Make the chunk trigger account for retained chunks** — flush when *new* chunks since the last
   flush reach the limit, not when total queue depth does. One-line change to the condition; removes
   the death spiral entirely.
2. **Denominate both limits in bytes** — drop the chunk-count trigger and rely on
   `buffer_max_bytes` (4,096) plus boundaries. Fewer knobs, no unit mismatch.
3. Raise `max_buffer_chunks` above the worst-case retained-chunk count — a band-aid; the conflict
   returns for smaller deltas.

Option 1 preserves every existing safety property (lookahead retention, secret anchor, open-media
holdback) because it changes only *when* a flush fires, never *what* is scanned.

**Per-flush cost of ~3.2 ms for 525 characters is separately suspicious** and worth its own
investigation once the flush count is fixed — but at ~4 flushes instead of ~237 it stops being the
headline.
