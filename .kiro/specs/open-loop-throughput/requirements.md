# Requirements — measure throughput at a latency bound, not at a concurrency

## Why the closed-loop driver cannot answer the question

`load.py` holds N requests in flight and issues a new one only when one completes. Two
consequences make it unable to answer "what is the maximum RPS at p99 < 20 ms?":

**It can only sample discrete rates.** Concurrency is an integer. Measured: concurrency 1 →
13.6 RPS (p99 17.60), concurrency 2 → 27.0 RPS (p99 28.40). The bound is crossed somewhere
between, and the driver cannot ask what happens at 18 RPS.

**It conflates offered load with queue depth.** Raising concurrency raises both together, so
a p99 measured at concurrency 4 cannot be attributed to arrival rate or to queueing. A real
client does not wait for a response before sending the next request; load arrives whether or
not the server is keeping up.

## Requirements

**R1 — Arrival rate is independent of completion.** Requests are issued on a schedule. A slow
response must not slow the next request's departure; that is the definition of open loop and
the whole point.

**R2 — Refuse when the driver cannot keep the schedule.** If dispatch falls behind its own
timetable, the measurement is of the client, not the gateway. Report the achieved rate
alongside the target and refuse when they diverge materially.

**R3 — Keep every refusal the closed-loop driver already has.** Nine stages all present and
non-skip; error rate ≤ 0.5%; stub-duration match (R7). A fast 5xx is not throughput, and a
six-stage pipeline is not the pipeline.

**R4 — Report the answer as a rate, not a concurrency.** The output is "the highest arrival
rate at which p99 ≤ bound", with the spread across repeats, or an explicit refusal.

**R5 — Distinguish "not yet saturated" from "at the limit".** Report gateway CPU at each
rate, so a bound met at 10% utilisation is not mistaken for capacity.

## Non-goals

- Not replacing `load.py`. Closed-loop is the right tool for per-stage attribution; this
  answers a different question and both stay.
- Not modelling bursty traffic. Fixed-interval arrivals first; Poisson is a later refinement
  and would only make the tail worse, not better.
