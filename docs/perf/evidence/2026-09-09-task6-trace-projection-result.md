# Task 6 — 16× smaller trace, 28% of the tail, and still not the cause

**Tag `[M]`** · Docker `aimeshperf`, 45 policies, block posture, Tier-2 OFF, non-streaming,
50-token answers, gateway 4 vCPU, 4 s warmup + 20 s window, commit `db1a698a` + task 6

## Payload

| | full (default) | metrics | reduction |
|---|---:|---:|---:|
| response body | 39,005 B | **5,646 B** | 6.9× |
| `pipeline_trace` | 35,577 B | **2,218 B** | **16×** |
| stages present | 9 | 9 | — |
| per-stage `latency_ms` | yes | yes | — |

The nine-stage honesty check and the tail attribution both still work in `metrics` mode,
which was requirement R2 — a projection that broke them would have disabled the check
that stops a *shorter* pipeline being reported as a full one.

## Latency

| conc | p99 full | p99 metrics | change |
|---:|---:|---:|---:|
| 4 | 53.30 ms | 51.40 ms | −3.6% |
| 16 | 148.10 ms | **106.60 ms** | **−28%** |
| | p50 8.20 ms | p50 8.30 ms | unchanged |

**A real win, and not the cause.** Removing 91% of the response body took 28% off the
tail at concurrency 16 — consistent with less allocation producing fewer or shorter GC
pauses — but 106.60 ms remains, still 5× the 20 ms budget, and the median did not move at
all.

Tail attribution after the change:

```
firewall tax total         8.30     57.25    +48.95
  stage deltas account for +2.35 ms of the +48.95 ms tail excess (5%)
  <== UNATTRIBUTED: the time is NOT inside any stage
```

Still 95% outside every stage. The trace was **a** contributor, not **the** contributor.

## Hypothesis ledger

| # | hypothesis | verdict |
|---|---|---|
| 1 | thread-per-regex churn | **refuted** — fixed it, p99 unchanged (53.20 → 53.30) |
| 2 | synchronous Redis publishes | **refuted** — Redis 0.13 ms avg, `publish` 3.84 µs; 7 cannot be 90 ms |
| 3 | `pipeline_trace` build + serialise | **partial: 28% at conc 16, 0% on the median. Not the cause.** |
| 4 | GC pauses from allocation churn | partially implicated *via* 3, and unresolved on its own |

Three named causes, one refuted by experiment, one by measurement, one confirmed as a
minority contributor. The remaining ~100 ms is still unattributed.

## What I have not yet looked at

The attribution compares the **nine stages** only. The trace root also carries
`overhead_ms` and `latency_breakdown`, which the gateway computes itself and which may
already name the gap. Reading fields I am already collecting is cheaper than another
hypothesis, and it is the next step.

Untouched so far, and each still capable of producing out-of-stage time: ASGI middleware
(the plan quotes 1.00 ms p50 / 4.70 p99), h11 request parsing of a 3.5 KB body, and
per-worker event-loop scheduling delay under the scanner thread pool.

## Default

`GATEWAY_PIPELINE_TRACE_MODE` defaults to `full` — today's behaviour byte-for-byte, the
identity function, no copy. Shipping `metrics` by default is a client-contract change and
should be argued from this measurement separately, not smuggled in with it.
