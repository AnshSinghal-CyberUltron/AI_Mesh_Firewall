# Task 10 — design

## The driver

`drive.py` is sequential — one request at a time — so it measures latency and cannot
measure capacity. A new `scripts/perf/e2e/load.py`:

- fixed **concurrency** (in-flight requests), not a fixed rate, so the system self-paces
  and a slow gateway produces backpressure instead of an unbounded queue;
- a warmup window discarded, then a fixed measurement window;
- unique prompts per request (the existing R7.2 nonce discipline) so no cache is hit;
- per-request: wall, TTFT, stage trace, `compute_full_nine_stages`;
- samples `docker stats --no-stream` for gateway/control/postgres/redis throughout;
- samples its own process CPU (R5).

Concurrency is swept: 1, 2, 4, 8, 16, 32, 64, 128 — stopping early once p99 has crossed
the bound twice in a row, since past the knee the extra points only add queueing.

## Output

One row per concurrency level:

```
conc  offered  achieved_rps  p50  p90  p99  gw_cpu%  ctl_cpu%  client_cpu%  9stage  refusals
```

and a single admissible headline computed from it:

```
MAX RPS (p99 <= BOUND):  <rps>  at concurrency <c>, gateway CPU <x> vCPU
  => <rps/x> RPS per vCPU consumed      [CPU-BOUND FLOOR — stub upstream, see R2]
```

## Refusal conditions (R6)

The run reports **no number** if any hold:

| condition | why |
|---|---|
| any sample has `compute_full_nine_stages` false | measuring a shorter pipeline (R1) |
| client CPU > 80% of a core per worker thread | the driver is the bottleneck (R5) |
| gateway CPU < 60% of its limit at the knee | something other than the gateway binds; the denominator is wrong (R4) |
| error rate > 0.5% | a fast 5xx is not throughput |
| achieved RPS within 5% across two concurrency doublings while p99 rises | already past the knee; the earlier point is the answer |

## Why the headline is a floor, not a ceiling

The in-gateway stub returns without provider I/O. Real BYOK traffic waits on a socket,
and that wait is not CPU — an async gateway overlaps other requests during it. So real
per-core request throughput is **higher** than this measurement, while per-request CPU
cost is the same. Quoting this as "max RPS" would be the same class of error as the
2,638 ms "firewall tax" the harness already learned to refuse. It is reported as
**CPU-bound floor**, with the external `token_stub.py` wired as a BYOK provider offered
as the follow-up that puts a realistic I/O wait back in.

## Verification

Run at 45 rules, block posture, Tier-2 off, non-streaming, 200-token answers; then repeat
streaming. Record commit SHA, host CPU model, container limits, and rule count in the
evidence, so the number remains interpretable when any of them change.
