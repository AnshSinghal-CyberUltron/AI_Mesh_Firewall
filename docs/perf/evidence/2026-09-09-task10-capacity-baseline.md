# Task 10 — capacity baseline: the tail collapses while the CPU idles

**Tag `[M]`** · Docker `aimeshperf`, 45 policies, block posture, Tier-2 OFF, non-streaming,
50-token answers (`GATEWAY_LOADTEST_STUB_DURATION_S=0.5`), gateway 4 vCPU /
`WEB_CONCURRENCY=4`, 4 s warmup + 14 s window per level, commit `f00942e8`

## Result — the driver refused to report a number

| conc | RPS | tax p50 | tax p90 | tax p99 | wall p50 | gw CPU | client cores |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 4 | 6.9 | 7.90 ms | 9.00 ms | **53.20 ms** | 553.9 ms | 58.8% (0.59 vCPU) | 0.01 |
| 16 | 26.7 | 8.10 ms | **55.90 ms** | **145.60 ms** | 556.1 ms | 152.4% (1.52 vCPU) | 0.02 |

> REFUSING TO REPORT A NUMBER: no concurrency level met p99 <= 20 ms with all nine
> stages running. Lowest p99 observed: 53.20 ms at concurrency 4.

All nine stages ran at every level; error rate 0.00%; the client used 0.02 cores, so it
is nowhere near the bottleneck.

## What this says

**The median is fine and the tail is not.** The firewall tax p50 barely moves (7.90 →
8.10 ms) between concurrency 4 and 16, but p99 goes 53 → 146 ms. A pipeline whose median
is healthy while its p99 degrades 18× under an 4× load increase is not CPU-limited — it
is **contention-limited**.

**The CPU is idle when it happens.** At concurrency 16 the gateway used **1.52 of its 4
vCPU** (38%). If work were simply queueing behind saturated cores, CPU would be near the
limit and p50 would rise with p99. Neither is true. Requests are waiting on something
that is not CPU.

**So the ~426 RPS/vCPU derivation in the plans is not merely optimistic — it measures the
wrong constraint.** Dividing a vCPU by per-request CPU cost assumes CPU is what binds. At
this rule count it is not.

## Leading hypothesis, and how it will be tested

`policy_engine._run_with_timeout` creates a **fresh daemon thread per regex**, starts it,
and immediately joins it. The thread is not parallelism — the caller blocks — it is a
mechanism for abandoning a runaway regex, since Python cannot interrupt `re.search`.

Measured cost of the mechanism (3,000 iterations, ~1,200-char input, one compiled
pattern):

| approach | per regex | overhead vs inline |
|---|---:|---:|
| inline `pattern.search` (no timeout guard) | 25.08 µs | — |
| **today: fresh thread per regex** | 79.70 µs | **54.61 µs** |
| persistent worker + queue (same guard) | 35.37 µs | 10.29 µs |

At 45 rules that is **2.46 ms → 0.46 ms** per request; at 264 rules, **14.42 ms → 2.72
ms**. But the single-threaded microbenchmark is the *floor* on the win: at 27 RPS the
gateway is creating and destroying roughly **1,200 threads per second**, and every one of
them contends for the GIL with four Uvicorn workers. Thread-creation cost under GIL
contention is far worse than in isolation, which is the shape that produces a collapsing
tail at low CPU.

This is a hypothesis, not a conclusion. It is testable: replace the per-regex thread with
a persistent worker that keeps the identical timeout semantics, re-run this exact sweep,
and see whether p99 collapses. If it does not, the hypothesis is wrong and the contention
is elsewhere — the scanner thread pool, Redis, or the synchronous log publisher (G4.5).

## Method notes

- The bound is on the **firewall tax** (`t_addon_pre_ms + t_addon_post_ms`), not wall.
  Wall is ~554 ms here because the stub deliberately paces generation, exactly as a real
  provider would; bounding capacity on wall would measure the upstream. An earlier run of
  this sweep did exactly that and refused everything at 3.1 s — my bound was wrong, not
  the gateway.
- Answer length is 50 tokens. Output-guard cost scales with content, so this RPS figure
  is per-answer-length and is not comparable to a run at a different one.
- Any number this harness eventually reports is a **CPU-bound floor**: the in-gateway stub
  answers without provider I/O, and a real socket wait would let an async gateway overlap
  more requests per core.
