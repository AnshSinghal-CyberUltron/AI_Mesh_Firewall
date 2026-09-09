# 97% of the tail latency is outside all nine stages

**Tag `[M]`** · Docker `aimeshperf`, concurrency 16, 542 samples, 45 policies, block
posture, Tier-2 OFF, non-streaming, 50-token answers, gateway 4 vCPU, commit `249ec2ed`

## The measurement

For the slowest decile by firewall tax (54 samples) against the median cohort (271):

| stage | median ms | tail ms | delta |
|---|---:|---:|---:|
| output_guardrail | 1.90 | 2.55 | +0.65 |
| rate_limit | 0.70 | 1.25 | +0.55 |
| auth | 0.40 | 0.85 | +0.45 |
| model_routing | 1.50 | 1.95 | +0.45 |
| policy | 0.20 | 0.55 | +0.35 |
| kill_switch | 0.20 | 0.40 | +0.20 |
| input_scan | 0.10 | 0.20 | +0.10 |
| model_input | 0.40 | 0.50 | +0.10 |
| model_output | 500.60 | 500.50 | −0.10 |
| **firewall tax total** | **8.70** | **101.40** | **+92.70** |

**The nine stage deltas sum to +2.75 ms. The tail excess is +92.70 ms. Stages account for
3% of it.**

## What this rules out

It rules out, in one reading, every hypothesis I had been working through — including the
one I had just promoted after thread churn was refuted:

- **not the output guard** (+0.65 ms) — so tasks 1C/1E/1F, real as their wins are, cannot
  fix this;
- **not the policy engine** (+0.35 ms) — so task 2 could never have fixed it, which is
  exactly what the sweep showed;
- **not Tier-1 or Tier-2** (+0.10 ms, and Tier-2 is off);
- **not the upstream** — `model_output` is flat to within 0.1 ms across median and tail;
- **not any stage at all.** The time is spent while *no stage is executing*.

## What it points to

The firewall tax is `total − model_output`. If the stages inside that window only account
for 3% of the tail's excess, the remaining ~90 ms elapses **between** stages, before the
first, or after the last: ASGI middleware, response serialisation, or — the signature that
fits best — **the request's coroutine being ready but not scheduled**, because its event
loop is blocked.

At concurrency 16 the gateway uses **1.53 of 4 vCPU**. Idle CPU plus time passing while
no stage runs is the classic shape of **blocking I/O on the event loop**.

That makes task 5 the live candidate again — 7 synchronous `client.publish` calls per
request, inline on the loop, each with `socket_timeout=1.0`
(`…-task5.0-log-volume-measured.md`) — but *candidate* is the operative word. I named a
cause twice already; once (thread churn) it was wrong, and the correction cost a rebuild
and a sweep. So this one gets tested the same way: implement task 5, re-run this exact
sweep, and record the result whichever way it falls.

## Strategic consequence

The 20 ms goal has two separate problems and they need separate work:

| | status |
|---|---|
| **median** firewall tax | **8.70 ms — already inside the 20 ms budget** |
| **p99** firewall tax | 123.60 ms, and 97% of the excess is outside every stage |

Every task in the plan's Gate 2 and Phase 2 — multi-pattern engine, rule budgets, Tier-2
gating — targets stage time. Stage time is not the problem at this rule count. It is
worth fixing for higher rule counts, but scheduling it ahead of the out-of-stage tail
would be optimising the 3%.

## Method note

`load.py` was already collecting `pipeline_trace` for every request and discarding all
but the nine-stage check and the total. Reporting per-stage p50-vs-tail deltas — and,
crucially, checking whether they **sum to** the observed excess — converted four
competing guesses into one reading. The "UNATTRIBUTED" line exists precisely so a future
run cannot quietly attribute a tail to whichever stage happens to be largest.
