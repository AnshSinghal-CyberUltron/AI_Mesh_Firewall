# CORRECTION — task 6's tail claim was n=1 per arm; the real effect is ~5.6% on p90

**Tag `[M]`** · same rig, concurrency 16, 3 runs (full) and 2 valid runs (metrics),
`PERF_SCANNER_POOL=4`

## What I claimed, and why it was wrong

`2026-09-09-task6-trace-projection-result.md` and commit `5c0beca7` state:

> | conc | p99 full | p99 metrics | change |
> | 16 | 148.10 ms | **106.60 ms** | **−28%** |

That compared **one run per arm**. Repeating the same configuration shows the metric
cannot support it:

| mode | p50 | p90 | p99 |
|---|---|---|---|
| full, 3 runs | 8.40 / 8.00 / 8.40 | 56.80 / 54.10 / 55.10 | **151.20 / 128.90 / 100.90** |
| metrics, 2 valid runs | 8.30 / 8.20 | 52.40 / 51.60 | **111.70 / 139.80** |

**Full mode alone ranges 100.90–151.20 ms on an unchanged build.** The 106.60 ms I
attributed to task 6 sits *inside* that range. The two p99 ranges overlap almost
completely. The 28% figure was noise dressed as a result.

## The corrected effect

| statistic | full | metrics | verdict |
|---|---|---|---|
| p50 | 8.00–8.40 | 8.20–8.30 | **no effect** |
| **p90** | 54.10–56.80 | **51.60–52.40** | **~5.6% better — ranges do not overlap, so this one is real** |
| p99 | 100.90–151.20 | 111.70–139.80 | **not resolvable at this sample size** |

So task 6 is a **~5.6% p90 improvement**, not a 28% p99 improvement. An order of
magnitude smaller than claimed, and on a different statistic.

## Why p99 was the wrong statistic to compare

Each run collects ~550 samples, so its p99 is estimated from about **five observations**.
Five samples from a heavy tail is not a measurement. p90 rests on ~55 and is
correspondingly steadier — full mode's p90 varied by 2.7 ms across three runs while its
p99 varied by 50.3 ms.

Nothing about the earlier conclusions changes: p50 still meets the 20 ms budget, the tail
still misses it, and the excess is still outside every stage. Only the size of task 6's
contribution changes — and it shrinks.

## What is unaffected

The payload measurement is a byte count, not a timing, and stands unchanged:
**35,577 B → 2,218 B (16×)**, response body 39,005 → 5,646 B, nine stages and per-stage
`latency_ms` preserved in both modes.

## The systemic fix

`load.py --repeat N` now runs each concurrency level N times and reports the **median run
with the observed spread**, e.g. `[3 runs: p90 51.6-52.4, p99 111.7-139.8]`. Reporting the
best run would be cherry-picking; reporting a single run and calling a 28% difference a
result is the specific mistake this option exists to prevent — and it is already in this
branch's history.

## A second correction, from the same session

I also wrote that the +52 ms sat in `t_addon_pre_ms` and therefore "before the model
call". `pipeline_trace.py:708` computes that field as

```python
pre = round(max(0.0, total_f - model_out - post), 1)
```

— a **residual**, not an instrument: wall minus `model_output` minus `output_guardrail`.
It cannot distinguish pre-model from post-model time, so response building, telemetry and
trace serialisation all land in it too. The correct statement is "not in the upstream and
not in the output guard", which is where the investigation already was.

Localising further needs a stage **start offset**, which the trace does not currently
carry.
