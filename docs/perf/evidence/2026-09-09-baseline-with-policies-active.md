# Honest baseline: nine stages, policies ACTIVE, no refusals

First measurement in this session where the policy engine actually evaluates rules.
Supersedes every earlier latency figure (see `2026-09-09-RETRACTION-policies-were-inert.md`).

## Conditions

```
PERF_STUB_DURATION_S=0.5 PERF_TRACE_MODE=full PERF_SCANNER_POOL=4 PERF_GC_INSTRUMENTATION=1
load.py --concurrency 16 --warmup-s 5 --window-s 16 --repeat 3 --settle-s 8 --max-tokens 0
```

Bundle: org `default`, 17 pipeline-domain policies, **127 rules** (92 regex, 35 keyword).

## Result

| | p50 | p90 | p99 |
|---|---|---|---|
| **addon (firewall tax)** | **27.00 ms** | **63.20 ms** | **102.10 ms** |

28.0 RPS · 0.00% errors · all nine stages present · 3 runs (p90 spread 54.9–71.0, p99 85.5–128.0)

**The <20 ms target is NOT met.** The harness refused to report success:
`no concurrency level met p99 <= 20.0 ms with all nine stages running`.

## Where the time is

Per-request attribution (which stage was *this* request's outlier), not sum-of-medians:

| stage | outlier share | mean excess |
|---|---|---|
| **policy** | **67%** | 27.36 ms |
| output_guardrail | 9% | 14.12 ms |
| rate_limit | 9% | 16.03 ms |
| auth | 7% | 47.80 ms |
| model_output | 7% | 9.70 ms |

Policy stage: **8.80 ms median → 26.30 ms tail (+17.50)**. Nothing else exceeds +1.75 ms.

## A rate ceiling was masquerading as a capacity limit

The first run with policies active reported **83% HTTP 429**. `FirewallConfig.requests_per_minute`
defaults to **1000**; a 25 RPS run is 1500/min, so the ceiling tripped mid-window. This was
invisible before the org fix because a null org meant no config and no enforcement.

Raised to 100M rpm / 1M burst in `bootstrap_org.py`, with `rate_limit_enabled` left **True** so
the stage still performs its full Redis work every request (INCR + EXPIRE NX on the burst bucket
and again on the RPM bucket — four commands over two pipelined round-trips). Disabling the stage
would have removed that work and quietly shortened the pipeline.

## Component measurement of the policy stage

`scripts/perf/policy/bench_policy_engine.py`, real bundle, single-threaded, median of 200:

```
evaluate() WHOLE STAGE         1890.9 us
   92 regex rules              1829.5 us ( 19.89 us/rule)   <- 97% of the stage
   35 keyword rules              42.1 us (  1.20 us/rule)   <-  2%

   90 compiled patterns
    INLINE  .search()            426.7 us  (  4.74 us/rule)
    VIA WORKER (current)        1434.0 us  ( 15.93 us/rule)
    HANDOFF OVERHEAD            1007.2 us  ( 11.19 us/rule)  <- 53% of the whole stage

   35 redundant .lower() calls     4.3 us   <- hoisting this would save nothing
   literal-prefilterable: 65/92 rules; all 65 skippable on a benign prompt
```

**53% of the policy stage is cross-thread queue round-trips, not regex matching.**
Every regex rule hands its `search()` to a worker thread and blocks on a reply queue.

Hoisting the repeated `text.lower()` — the obvious-looking fix from reading the code —
would save **4.3 µs**. Measuring first is what kept that from becoming an afternoon.

## A prediction to test, not assume

The bench says 1.89 ms single-threaded; the trace says 8.80 ms at concurrency 16. A **4.7×
inflation under contention** is the signature of a cost that is *not* CPU work: a queue
round-trip forces the GIL to ping-pong between two threads, and that gets worse with
concurrency, whereas `re.search` itself holds the GIL and does not.

If that reading is right, removing the handoff shrinks the stage by **more** than the 53%
the idle bench predicts. If the stage instead falls by ~53% and no further, the extra cost
is elsewhere and this explanation is wrong. Both outcomes are informative; the measurement
after the change decides it.
