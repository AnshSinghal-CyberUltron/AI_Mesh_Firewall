# Task 2 — 2.4× on the policy engine, and my capacity hypothesis was wrong

**Tag `[M]`** · same sweep, same configuration, same commit lineage as
`2026-09-09-task10-capacity-baseline.md`

## The change worked

Real policy engine, ~1,230-char input:

| rules | before | after | speedup |
|---:|---:|---:|---:|
| 10 | 0.789 ms | 0.320 ms | 2.47× |
| 45 | 3.486 ms | **1.450 ms** | **2.40×** |
| 60 | 4.654 ms | 1.983 ms | 2.35× |

Full gateway suite: 240 failures before, 240 after, zero new.

## The hypothesis it was meant to test is refuted

The capacity baseline said:

> Leading hypothesis: … the gateway is creating and destroying roughly 1,200 threads per
> second, and every one of them contends for the GIL … which is the shape that produces a
> collapsing tail at low CPU.

and committed to running the discriminating experiment either way. Here it is:

| conc | p99 before | p99 after | RPS before | RPS after | gw CPU after |
|---:|---:|---:|---:|---:|---:|
| 4 | 53.20 ms | **53.30 ms** | 6.9 | 7.0 | 0.42 vCPU |
| 16 | 145.60 ms | **148.10 ms** | 26.7 | 26.7 | 1.61 vCPU |

**Unchanged.** Removing ~1,200 thread creations per second moved the tail by 0.1 ms and
2.5 ms — noise. The throughput is identical. **Thread churn was not what binds the tail,
and I was wrong to name it as the leading explanation.**

Task 2 remains worth keeping: 2.4× less CPU in the policy engine is real, and it will
matter at higher rule counts where that cost dominates. But it does not touch the
constraint measured here.

## What the numbers actually say now

At concurrency **4** the gateway serves **7 RPS** using **0.42 of 4 vCPU**, and p99 is
**6.7× p50** (53.3 vs 7.9 ms). Four concurrent requests against four Uvicorn workers is
roughly one request per worker — there should be no queueing at all. A tail that wide at
what is effectively idle is not contention *for* a resource; it is something that
periodically stalls.

Candidates, none yet evidenced:

- the **7 synchronous Redis publishes per request** measured in task 5.0, each inline on
  the event loop with `socket_timeout=1.0`;
- other blocking I/O on the loop (rate limiter, config refresh, control-plane calls);
- periodic work — GC, snapshot refresh, breaker bookkeeping — landing on some requests;
- the 41 KiB `pipeline_trace` serialised on every allow path (G4.2), though that should
  raise CPU rather than leave it idle.

## What I am going to do instead of guessing again

I named a cause once and it was wrong. The next step is not another hypothesis but
**direct attribution**: `load.py` already collects `pipeline_trace` for every request and
throws away everything except the nine-stage check and the total. It should report
**per-stage p99**, so the tail requests say for themselves which stage is slow.

That turns "which of my four guesses is right" into a reading.
