# Design — fixed-interval departures, gateway-measured latency

## Shape

A scheduling loop computes each request's departure time as `start + seq * (1/rate)` and
submits it to a thread pool. Departure does not wait for any earlier response (R1).

## Three defects found while building it, all by cross-checking against the closed-loop driver

**1. Client wall time is not the latency.** The first version measured
`time.perf_counter()` around the HTTP call. At 13.6 RPS it reported **p50 22.15 ms** against
the closed-loop driver's **14.60 ms** on the same gateway at the same moment — ~7.5 ms of
Python thread-pool and GIL overhead in the *client*. A load generator that adds 7.5 ms cannot
adjudicate a 20 ms bound. Latency is now taken from the gateway's own trace
(`t_addon_pre_ms + t_addon_post_ms`), exactly as `load.py` defines it, so the two drivers
measure the same quantity. Client wall is still reported, as `wall50`, so the driver's
overhead stays visible rather than hidden.

**2. CPU was sampled after the window.** `docker stats --no-stream` ran once `drive()`
returned — i.e. against an idle gateway. It reported **1.3%** where the closed-loop driver
saw **34.2%** on the same run. Sampling now happens mid-window on a side thread.

**3. An unachievable rate hung the driver.** `ThreadPoolExecutor` used as a context manager
waits for every queued task on exit, so offering 3000 RPS left tens of thousands of futures
to drain and the sweep never returned. The pool is now shut down explicitly with
`cancel_futures=True`, and an in-flight cap stops offering once the backlog grows — which is
also the honest signal that the *driver* has become the constraint (R2).

## Validation

After the fixes, at 13.6 RPS against the same gateway:

| | p50 | p90 | wall | CPU |
|---|---|---|---|---|
| open-loop | 14.50 | 15.70 | 72.2 | 33.5% |
| closed-loop | 14.60 | 16.20 | 72.7 | 34.2% |

Agreement on every quantity that is not tail-noise. A new instrument that disagrees with the
existing one is wrong until shown otherwise; this one now agrees.

## Refusals (R2, R3)

Nine stages all non-skip · error rate ≤ 0.5% · stub-duration match · achieved ≥ 90% of target
· in-flight cap not hit · departure lateness p99 ≤ 50 ms measured at **actual departure**,
not at loop iteration (the loop can keep perfect time while every worker is busy).

## R5

Gateway CPU is printed per rate, and a passing result under ~200% CPU is annotated as
latency-bounded rather than capacity — so a bound met on a mostly-idle box is never read as
a throughput ceiling.
