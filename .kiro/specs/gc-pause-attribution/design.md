# Task 7 — design

## The monitor

```python
class _GCMonitor:
    total_pause_ms: float          # monotonic, process-lifetime
    counts: dict[int, int]         # per generation
```

`gc.callbacks` fires `("start", info)` and `("stop", info)` around every collection.
The callback stores a start timestamp and, on stop, adds the elapsed time to a running
total and increments `counts[info["generation"]]`.

O(1), no allocation beyond a float and an int, wrapped so it can never raise (R2).

## Per-request attribution

`total_pause_ms` is monotonic, so the pause time *during* a request is a subtraction:

- at request start, capture `gc_pause_at_start`;
- at trace build, `gc_pause_ms = monitor.total_pause_ms - gc_pause_at_start`.

That is a measurement of what actually happened during this request, not a residual (R1).

Surfaced next to `telemetry_ms` via `attach_offstage_timings`, so `load.py`'s existing
root-timing attribution picks it up with no new plumbing.

## Reading the result

| `gc_pause_ms` on tail requests | conclusion |
|---|---|
| ≈ the excess (tens of ms) | **GC confirmed.** Fix is allocation reduction + generation tuning |
| ≈ 0 | **GC refuted.** Remaining: GIL contention, host CPU steal |
| between | partial, as task 6 was — report the fraction, do not round up to a cause |

The third row is written down deliberately: task 6 was a partial contributor that I
initially reported as a 28% win. A number between the two extremes is a fraction to state,
not a cause to claim.

## Verification

1. Unit test: with instrumentation on, forcing `gc.collect()` makes `total_pause_ms` rise
   and the gen2 count increment.
2. Unit test: with it off, the callback is not registered and the counters stay zero.
3. E2E: `--repeat 2`, read `gc_pause_ms` at median and tail.
