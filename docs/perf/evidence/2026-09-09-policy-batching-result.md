# Result: batching the policy regex handoff — and a refuted prediction

Valid A/B. Both arms: stub 0.5 s (pinned by the new R7 refusal), concurrency 16,
3 runs, `--max-tokens 0`, policies active, 0.00% errors, nine stages, RPS 28.0 vs 28.5.

| metric | before | after | change |
|---|---|---|---|
| **p50 addon** | 27.00 ms | **22.70 ms** | −16% |
| **p90** | 63.20 ms | **42.10 ms** | −33% |
| **p99** | 102.10 ms | **58.50 ms** | −43% |
| policy stage median | 8.80 ms | 7.00 ms | −20% |
| policy stage tail | 26.30 ms | 7.50 ms | −71% |
| policy share of outliers | 67% | 4% | collapsed |
| overhead_ms | 11.70 ms | 9.70 ms | −17% |
| gateway CPU | 125.6% | 118.7% | −5% |

**The <20 ms target is still NOT met** — p99 58.50 ms. The harness refused to report success.

## The prediction was wrong

R4 predicted the stage would fall by **more** than the 53% the idle bench implied, on the
theory that a queue round-trip ping-pongs the GIL and so costs more under contention than
in an idle bench.

It fell **20%**. Not more than 53% — less.

The explanation is simpler and I had it backwards: **`evaluate()` was never the whole
policy stage.** The bench measured `evaluate()` at 1890.9 µs and the change took it to
626.2 µs — a saving of 1.26 ms. The stage fell 8.80 → 7.00, a fall of 1.80 ms. Those
agree. The stage cost 8.80 ms while `evaluate()` cost 1.89 ms, so **~7 ms of that stage
was always something other than rule evaluation**, and optimising rule evaluation could
never have touched it.

`PipelineStageTimer.mark_segment_end` is segment-based: it charges everything elapsed
since the previous boundary to the stage being closed. So "the policy stage" is not
"`policy_engine.evaluate`" — it is everything between the rate_limit boundary and the
policy boundary. I read a stage name as if it were a function name. That is the same
error shape as reading `t_addon_pre_ms` as an instrument when it is a residual.

## What the change genuinely bought

The **tail**, not the median. Policy went from 67% of per-request outliers to 4%, its tail
excess from +17.50 ms to +0.50 ms, and p99 fell 43%. Removing 92 cross-thread round-trips
per request removed a source of *variance* — each one is a scheduling point where a
request can lose the GIL and wait behind fifteen others. The median barely moved because
the median request was not the one losing that race.

That is a better result than the one predicted, arrived at for a different reason than
the one I gave.

## Two runs were void before this one

The first post-change run showed model_output 2000 ms against the baseline's 500 ms:
recreating the container without `PERF_STUB_DURATION_S` exported reset the stub to
compose's default of 2 s. Same confound as earlier in the project. `load.py --expect-stub-s`
now reads the container's environment and refuses before the run rather than leaving it to
be spotted afterwards in a suspicious model_output median.

## Next, in order of measured size

1. **The other ~7 ms of the policy stage.** Not rule evaluation. Unknown — must be
   profiled, not guessed.
2. **rate_limit is now the top outlier**: 43% of outliers, tail 0.70 → 8.70 ms. Four
   Redis commands over two pipelined round-trips per request.
3. `overhead_ms` 9.70 ms — still outside every stage.
4. The literal/Hyperscan prefilter (65 of 92 rules skippable) — now clearly a *smaller*
   prize than it looked, since it targets the 1.89 ms that is already down to 0.63 ms.
