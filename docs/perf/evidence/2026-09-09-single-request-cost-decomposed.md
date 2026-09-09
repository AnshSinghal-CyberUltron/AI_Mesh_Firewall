# The single-request cost, decomposed — and where the budget actually goes

Measured at **concurrency 1** (7–9% of the 4-vCPU limit, no contention), 4096-char prompt,
policies active, median of 10.

```
auth                     0.50
kill_switch              0.20
rate_limit               0.75
policy                   4.80      <- largest stage
input_scan               0.10      (passthrough by design — detection lives in policy)
model_routing            0.70
model_input              0.30
output_guardrail         1.50
                        ------
stages (excl. model)     8.85 ms
overhead_ms              7.35 ms   <- 44% of the cost, outside EVERY stage
telemetry_ms             0.00
gc_pause_ms              0.00
                        ------
ADDON                   16.65 ms   against a 20 ms budget
```

**`overhead_ms` is the largest single item in the firewall's cost — larger than the policy
stage — and it is unattributed.** It is present with no load at all, so it is not queueing.

## p99 vs concurrency

| conc | RPS | p50 | p90 | p99 (3 runs) | CPU |
|---|---|---|---|---|---|
| 1 | 1.8 | 16.80 | 17.60 | **18.20** (17.5–102.9) | 7.2% |
| 2 | 3.7 | 17.90 | 20.00 | 20.50 (20.4–38.9) | 15.4% |
| 4 | 7.3 | 17.00 | 19.40 | 38.60 (35.4–42.7) | 25.9% |

p99 fits the budget only at **concurrency 1**, and even there one of three runs hit 102.9 ms.
p50 is ~17 ms at *every* concurrency — the single-request cost, not a load effect.

With 16.65 ms of unavoidable single-request cost against a 20 ms budget, there is **3.35 ms
of headroom for the entire tail**. That is why p99 fails the moment concurrency rises.

## REFUTED: it is not the pipeline trace

`GATEWAY_PIPELINE_TRACE_MODE=full` returns the whole trace — `input_text`,
`input_text_before`/`after`, `output_text`, `prompt_preview` — with every response. For a
4096-char prompt that is several copies of the text serialised per request, and it is a
*measurement* setting rather than a production one. The obvious suspect for 7 ms.

| mode | p50 (conc 1) | overhead_ms |
|---|---|---|
| full | 16.60 | 7.20 |
| metrics | 16.30 | 7.20 |

**Identical.** The trace is not the overhead. (Task 8 earlier removed the expensive part of
trace building — the redaction pass — which is likely why nothing is left to find here.)

## Where that leaves the target

Two independent problems, and they need different fixes:

1. **p50 = 16.65 ms of real single-request cost**, of which 7.35 ms is unattributed and
   4.80 ms is policy. Reducible, and the work so far cut it from 27.00 → 16.65 ms.
2. **p99 is scheduling**, not stage work — a no-op endpoint reaches 20.90 ms p99 under
   16-way load. Stage optimisation cannot touch it.

Neither is solved. The next question for (1) is what the 7.35 ms is; ruled out so far: the
trace, GC, telemetry, and queueing (it is present at concurrency 1).
