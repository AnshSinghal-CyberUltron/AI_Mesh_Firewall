# The obstacle is run-to-run variance, not a slow stage

## A retracted claim

I reported that worker count "responds strongly" to the tail: p99 69.8 → 63.4 → 46.7 for
4 → 8 → 12 workers. That was **n = 1 per arm**.

At n = 2, same harness, same code:

| WEB_CONCURRENCY | p50 | p90 (2 runs) | p99 (2 runs) | gw CPU |
|---|---|---|---|---|
| 12 | 21.20 | 36.6–77.6 | **67.0–107.6** | 224.0% |
| 16 | 18.50 | 33.7–35.5 | 60.9–72.9 | 123.2% |
| 24 | 18.00 | 31.3–32.5 | 56.6–71.0 | 183.6% |
| 32 | 194.70 | 107.2–571.0 | 370.9–2002.8 | **402.6%** |

The 12-worker arm — the one I singled out as best at 46.7 ms — spans **67–107 ms** when run
twice. Worker count does not reliably move p99 between 12 and 24. At 32 it saturates the
4-CPU limit and collapses.

This is the second time in this project that an n=1 arm produced a conclusion that did not
survive repetition. `--repeat` exists *because of* the first time. I ran without it, then
reported the result anyway.

## What the numbers do say

At a **fixed** configuration, p99 lands anywhere in roughly **56–107 ms**. p50 is stable to
about ±1 ms across the same runs.

**A p99 target cannot be demonstrated against a 2× band.** No optimisation of a stage can be
shown to have moved p99 while a repeat of the identical build moves it by 50 ms. Reducing
that variance — or explaining it — is a precondition for any p99 claim, not a follow-up to it.

## What the variance looks like

Per-request attribution shows the tail landing on a *different* stage each run, with every
stage reporting a similar mean excess (13–25 ms) when it happens to be the outlier —
rate_limit, auth, model_routing, output_guardrail, kill_switch all take turns. A slow stage
produces excess in *that* stage. This pattern — any stage, similar magnitude — is a stall
that happens to be charged to whichever stage was open when it hit.

Candidates, none yet tested: host neighbours outside the cgroup (`assert_host_idle` checks
processes, not other tenants), page-cache and Redis state differing across container
recreations, and the ~5 ms default GIL switch interval interacting with the batched regex
scan now holding the GIL for ~4.6 ms at a stretch.

`gc_pause_ms` reads 0.00 and CFS throttling was refuted earlier, so neither is the cause.

## Consequence for the target

p50 addon is **18.20 ms** and reproducible. p99 is **not** currently measurable to the
precision the < 20 ms target requires. The honest position is that the median is inside
budget and the tail is neither inside budget nor stable enough to characterise.
