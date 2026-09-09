# CORRECTION — "the tail is outside every stage" was an artefact of my attribution

**Tag `[M]`** · concurrency 16, same rig

## What the instrument said, and why it was wrong

Since `2026-09-09-tail-is-outside-every-stage.md` every tail attribution reported the same
shape: nine stage deltas summing to ~+2 ms against a ~+50 ms tail excess, labelled
**UNATTRIBUTED — the time is NOT inside any stage**. That conclusion redirected the whole
effort and led to five hypotheses, four of them refuted by experiment.

Surfacing `stage_latency_sum_ms` — the gateway's own per-request sum — refuted the
framing on the same run:

| | median | tail | delta |
|---|---:|---:|---:|
| sum of my per-stage medians | — | — | **+2.20 ms** |
| `stage_latency_sum_ms` (summed per request, then median) | 506.00 | 551.40 | **+45.40 ms** |
| firewall tax total | 8.05 | 55.90 | +47.85 ms |

**The sum of medians is not the median of the sum.** I computed each stage's median
across tail requests *independently*. If one slow request is slow in `policy` and the
next in `auth`, every per-stage median stays flat while every request carries one slow
stage. The per-request sum sees it; nine independent medians cannot.

So **the time was inside the stages** — just not the same stage on each request. The
"outside every stage" claim was a property of my arithmetic, not of the gateway.

## What this invalidates, and what survives

**Invalidated:** the framing in `…-tail-is-outside-every-stage.md`, and the strategic
claim built on it — that Gate 2 / Phase 2 stage work "targets the 3%". Stage work may be
exactly right; I cannot say from that evidence.

**Survives, because each was measured directly rather than inferred from the attribution:**

| finding | why it stands |
|---|---|
| thread churn refuted | p99 unchanged after the fix — a before/after measurement |
| Redis publishes refuted | Redis 0.13 ms avg, `publish` 3.84 µs — measured at the server |
| scanner pool refuted | pool 4/16/32 sweep, no trend |
| `telemetry_ms` = 0.00 | read from the trace, median and tail |
| trace payload 35,577 → 2,218 B | a byte count |
| median tax 8.1 ms, inside the 20 ms budget | direct |
| p99 misses the budget | direct |

Four of the five refutations were before/after experiments, not attributions, so they are
unaffected. What is lost is the *direction* the attribution pointed.

## The fix

`load.py` now asks each slow request which stage was **its** outlier and counts them:

```
PER-REQUEST: which stage was THIS request's outlier?
stage                 requests   share   mean excess ms
```

A culprit scattered across stages can no longer hide behind flat per-stage medians.

## Two method failures in one session

Both had the same shape — a statistic that looked like a measurement:

1. **n=1 per arm.** A 28% p99 "improvement" inside a 100.90–151.20 ms spread on an
   unchanged build. Fixed by `--repeat N` reporting median-of-N with the spread.
2. **Sum of medians.** Reported as "not in any stage" for several iterations. Fixed by
   per-request attribution.

The pattern to watch for is the same in both: a number derived by combining summary
statistics of *different* quantities, rather than measured on the same requests.
