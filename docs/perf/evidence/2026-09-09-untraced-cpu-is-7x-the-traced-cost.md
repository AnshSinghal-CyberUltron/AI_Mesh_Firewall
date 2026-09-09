# The gateway burns ~56 ms of CPU per request and traces 8.2 ms of it

**Tag `[M]`** · `docker stats` + achieved RPS, non-streaming, 50-token answers, 45 policies,
Tier-2 off, gateway 4 vCPU, commit `01131b0a`

## The arithmetic

| conc | achieved RPS | gateway CPU | **CPU per request** |
|---:|---:|---:|---:|
| 4 | 7.0 | 0.42 cores | **60.3 ms** |
| 16 | 27.1 | 1.53 cores | **56.3 ms** |
| 16 | 26.6 | 1.48 cores | **55.7 ms** |

Stable across a 4× change in concurrency, so it is a per-request cost, not a load artefact.

What the gateway's own trace accounts for:

| | |
|---|---|
| firewall tax p50 | **8.2 ms** |
| `model_output` | 500.3 ms — the stub **sleeps**; not CPU |
| `telemetry_ms` | 0.00 ms |
| `gc_pause_ms` | 0.67 ms |

**~50 ms of CPU per request is burned outside the traced region — roughly 7× what the
trace reports.**

## Why this explains the tail, and every failed hypothesis

Four Uvicorn workers, each a single-threaded event loop. At concurrency 16 that is 4
in-flight requests per loop, each carrying ~56 ms of CPU. A request can therefore sit
behind ~3 others — ~170 ms of work — before its own resumes. The wait is charged by
`mark_segment_end` to whichever stage happens to be open, which is exactly the scattered,
work-independent pattern measured:

> auth 25% (mean excess 59 ms), rate_limit 23% (51 ms), model_output 25% (26 ms)…

It is not a stall. It is **queueing behind untraced CPU work**.

And it explains why six hypotheses failed: each targeted something inside the 8.2 ms, which
is **15% of the actual per-request cost**.

| # | hypothesis | verdict |
|---|---|---|
| 1 | thread-per-regex churn | refuted — p99 unchanged after fixing it |
| 2 | synchronous Redis publishes | refuted — 0.13 ms avg, 3.84 µs/publish |
| 3 | `pipeline_trace` build | partial — ~5.6% p90, not the cause |
| 4 | scanner thread pool | refuted — pool 4/16/32, no trend |
| 5 | telemetry enqueue | refuted — `telemetry_ms` 0.00 |
| 6 | GC pauses | refuted — `gc_pause_ms` 0.67 → 0.74, **+0.07 ms** |
| 7 | CFS quota throttling | refuted — `nr_throttled` 5 of 680 periods, 22.8 ms **lifetime total** |

## The question this replaces them with

**Where does ~50 ms of CPU per request go?**

That is answerable by sampling the running process rather than by hypothesis. The trace
cannot help: it measures the regions someone thought to instrument, and by construction
the missing work is outside them.

Note this also revises the capacity picture. At ~56 ms of CPU per request, one vCPU
sustains ~18 RPS, and 4 vCPU ~71 RPS — against the plans' derived ~426 RPS/vCPU. The
derivation divided a vCPU by the *traced* per-request cost, which is 15% of the real one.

## Method note

This came from dividing `docker stats` CPU by achieved RPS — two numbers the harness was
already collecting and printing side by side, unexamined, for several iterations. Nothing
new was instrumented to find it.
