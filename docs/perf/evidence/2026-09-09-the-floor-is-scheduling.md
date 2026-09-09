# The 20 ms budget is exhausted by scheduling before any stage runs

## The measurement that separates the two explanations

Every latency number in this project mixes two things: the work the firewall does, and the
delay before the process gets round to running it. They are fixed by opposite means, so
telling them apart decides where effort should go.

`scripts/perf/e2e/noop_under_load.py` drives a **trivial endpoint** — one that does no
firewall work at all — from a single sequential thread while the chat load runs.

| | p50 | p90 | **p99** | max |
|---|---|---|---|---|
| `/health`, idle | 1.19 | 1.44 | **2.50** | 17.63 |
| `/health`, during 16-way chat load | 1.27 | 1.85 | **20.90** | **99.28** |
| chat addon, same moment | 17.00 | 25.00 | 53.10 | — |

## What it says

**A request that does nothing has a p99 of 20.90 ms under this load.** The entire 20 ms
budget is consumed by scheduling delay before a single stage executes.

Note the shape: p50 and p90 barely move (1.19 → 1.27, 1.44 → 1.85). The process answers
*most* trivial requests promptly. It is specifically the **tail** that explodes — 2.50 →
20.90 ms p99, 17.63 → 99.28 ms max. Whatever stalls the process does so rarely and deeply,
and it stalls a no-op exactly as it stalls real work.

## Consequences

**1. The < 20 ms p99 target is unreachable at this concurrency-per-vCPU ratio, regardless of
firewall efficiency.** Sixteen concurrent requests on a 4-vCPU limit cannot answer a no-op
inside 20 ms at p99. Driving the policy stage to zero would still leave p99 above budget.

**2. Every stage optimisation in this session was aimed below the floor.** The batching and
prefilter work was real — the policy stage went 8.80 → 4.80 ms and p50 27.00 → 17.00 ms —
but p99 was never going to follow, because p99 is not made of stage work.

**3. p50 and p99 need separate answers.** p50 addon is 17.00 ms and is genuinely firewall
work, which stage optimisation does improve. p99 is scheduling, which it does not.

**4. The honest form of the target changes.** "< 20 ms p99 for the nine-stage pipeline" is
only meaningful with a stated concurrency and vCPU count. The right question is: *at what
load per vCPU does the full pipeline hold p99 under 20 ms, and what RPS is that?* That is
measurable, and it is what the concurrency sweep answers.

## Not yet explained

What the stall *is* remains open. Ruled out: host noise (steal 0, load 0.49/16), CPU
saturation (103% of 400%), GC (`gc_pause_ms` 0.00), CFS throttling, worker count (12/16/24
identical), GIL switch interval (four arms, refuted). It is visible from outside the
firewall, it is rare, and it is deep — a ~100 ms max on an endpoint that does nothing.
