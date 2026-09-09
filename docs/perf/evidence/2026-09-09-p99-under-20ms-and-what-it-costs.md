# p99 < 20 ms is met — at 13.6 RPS, and that is not a capacity number

## The measurement

Every previous run was **stub-bound**: a 0.5 s upstream stub fixes RPS at
`concurrency / 0.5`, so no run could ever reach the gateway's capacity. Shortening the stub
to 0.05 s lifts that cap while keeping all nine stages genuinely running (verified: 9 stages
present, **9 non-skip**, 5 output tokens).

Full nine-stage pipeline, policies active (127 rules), 4096-char prompts, `--repeat 3`:

| conc | RPS | p50 | p90 | p99 (3 runs) | gateway CPU | err |
|---|---|---|---|---|---|---|
| **1** | **13.6** | 14.30 | 15.90 | **17.60** (16.7–18.1) | 0.38 of 4 vCPU | 0.00% |
| 2 | 27.0 | 14.60 | 16.60 | 28.40 (27.3–38.6) | 0.68 | 0.00% |
| 4 | 50.3 | 15.70 | 23.30 | 86.20 (37.8–123.8) | 1.29 | 0.00% |

**At concurrency 1 the 20 ms p99 bound is met**, reproducibly, with every stage running and
no errors. That is the first time in this project it has been.

## Why this is not yet an answer to "maximum RPS"

**At concurrency 1, RPS is not capacity — it is `1 / latency`.** One request is in flight at
a time, so 13.6 RPS is simply 1/73 ms of wall time. The gateway is using **0.38 of its 4
vCPU**: it is 90% idle. The harness says so itself and refuses to divide:

> *REFUSING TO REPORT RPS/vCPU: gateway used 0.38 of its 4.0 vCPU limit at the knee.
> Something other than the gateway binds, so the per-vCPU denominator would be wrong (R4).*

That refusal is correct. Dividing 13.6 by 0.38 would produce "35.8 RPS/vCPU" — a number
describing a machine that was mostly idle, not one at its limit.

## What the two numbers actually bound

They are different quantities and both are needed:

- **Saturated capacity** — from CPU per request: **90 RPS/vCPU** scan-only (11.11 ms CPU),
  **~36 RPS/vCPU** for the full nine-stage path with output guard (~28 ms CPU/request here).
  This is throughput *at 100% utilisation*, where p99 is unbounded.
- **Throughput at p99 < 20 ms** — somewhere **between 13.6 RPS (p99 17.6) and 27.0 RPS
  (p99 28.4)**. The bound is crossed between concurrency 1 and 2.

The gap between them is queueing. Capacity says what the box can do; the latency bound says
what it can do *while still answering quickly*, and the second is always smaller.

## The harness cannot currently pin this down

Concurrency is an integer, so a closed-loop driver can only sample 13.6 and 27.0 RPS — it
cannot ask "what happens at 18 RPS?". Worse, closed-loop load **conflates** the two: raising
concurrency raises both offered load and queue depth together, so an observed p99 cannot be
attributed to either.

Pinning the answer needs an **open-loop driver** that issues requests at a fixed arrival rate
independent of how fast they complete. That is the next harness change, and until it exists
the honest statement is a range, not a number.

## Status against the target

- **< 20 ms p99, nine stages, 0% errors: ACHIEVED at 13.6 RPS** (p99 17.60 ms, 16.7–18.1
  across three runs).
- **Maximum RPS: NOT ESTABLISHED.** Bounded between 13.6 and 27 RPS for the latency
  constraint; ~36–90 RPS/vCPU saturated, where latency is unbounded.
- 100k RPS remains ~1,330 vCPUs of firewall at the scan-only cost, and more at the full-path
  cost. The plan documents' own operating point is ~1,064 RPS.
