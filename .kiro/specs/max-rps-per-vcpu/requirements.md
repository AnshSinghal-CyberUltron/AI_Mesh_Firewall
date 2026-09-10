# Task 10 — maximum RPS per vCPU, measured

## Why this one next

Of the two halves of the goal, latency now has evidence behind it and throughput has
none. Non-streaming p50 is **15.2 ms** (measured); streaming first-token is
**358–962 ms** (measured). But every RPS figure in `docs/plans/` is derived, not
measured: the ~426 RPS/vCPU ceiling came from dividing a vCPU by the policy engine's
per-request cost, which ignores everything else in the request path.

An unmeasured claim is worse than a short one. This task replaces the derivation with a
number.

## What "maximum RPS" must mean here

RPS is meaningless without the conditions that produced it. A figure is only admissible
with all six:

1. **vCPU denominator** — the gateway's `cpus` limit, pinned and recorded.
2. **Latency bound** — the RPS at which p99 crosses a stated threshold, not the RPS at
   which the box stops responding. Saturation throughput with 4-second tails is not
   capacity.
3. **Rule count** — policy-engine cost is linear in rules (0.065 ms/rule measured), so
   RPS without a rule count is unquotable. Primary point: 45 rules, as measured today.
4. **Answer length** — output-guard cost scales with content; a 1-token answer measures
   a different pipeline.
5. **Posture** — block vs monitor, Tier-2 on/off.
6. **Stream or not** — these are different pipelines with different bottlenecks.

## Requirements

**R1 — the nine stages must actually run.** `compute_full_nine_stages` must be true for
every sampled request. A capacity number taken with scans disabled is the failure mode
the whole harness exists to prevent.

**R2 — the upstream must not be the bottleneck, and must be honest about it.** The
in-gateway stub is already flagged by the capacity gate (`chatcmpl-loadtest-stub`)
because it short-circuits before provider I/O. Two consequences that must both be
stated: it removes provider latency (good — isolates gateway cost) and it removes
provider I/O wait (bad — real async waits let a gateway overlap more requests per core).
So a stub-derived RPS is a **CPU-bound floor**, not a ceiling. It must be labelled that
way, never as "max RPS".

**R3 — find the knee, not the cliff.** Sweep concurrency upward and report the curve:
offered load, achieved RPS, p50/p99, and per-container CPU. The admissible number is the
achieved RPS at the highest concurrency where p99 still meets the bound.

**R4 — CPU must be attributed.** `docker stats` per container throughout, so RPS/vCPU
divides by CPU actually consumed rather than by the limit. A gateway pinned to 4 CPUs but
using 2.1 has a different per-vCPU figure than one using 4.0.

**R5 — the client must not be the bottleneck.** The load generator's own CPU is recorded;
if it saturates first the run is void. `drive.py` is sequential and cannot generate load —
this needs a concurrent driver.

**R6 — refuse rather than flatter.** If any of R1–R5 is unmet the run reports no number.
Same discipline as the existing honesty checks, which have already caught two of my own
bad measurements this session.

## Out of scope

Multi-node or horizontally scaled throughput. This measures one gateway container so the
per-vCPU figure has a clean denominator.
