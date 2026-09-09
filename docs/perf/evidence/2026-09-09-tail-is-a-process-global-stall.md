# The tail is a process-global stall, not any one stage

**Tag `[M]`** · concurrency 16, 45 slowest of ~450 samples, `--repeat 2 --settle-s 12`,
metrics trace mode, scanner pool 4, commit `4800c05e`

## The measurement

With attribution fixed to ask each slow request which stage was **its own** outlier:

| stage | requests | share | mean excess |
|---|---:|---:|---:|
| model_output | 12 | 27% | 26.72 ms |
| auth | 10 | 22% | **53.32 ms** |
| output_guardrail | 8 | 18% | 37.30 ms |
| rate_limit | 7 | 16% | 48.86 ms |
| model_routing | 4 | 9% | 42.30 ms |
| policy | 2 | 4% | 22.00 ms |
| model_input | 2 | 4% | **82.55 ms** |

**No stage dominates.** The excess lands on a different stage almost every request, and
its size (22–82 ms) is unrelated to how much work that stage normally does — `auth`
normally takes 0.40 ms and absorbs 53 ms; `model_input` normally 0.40 ms and absorbs 83.

Most telling: **27% of the excess lands on `model_output`**, whose duration is the
in-gateway stub's own fixed pacing and should not vary at all.

## Why this shape means a global pause

`PipelineStageTimer.mark_segment_end` attributes *all elapsed time since the previous
boundary* to the stage being closed. So if the process pauses for 50 ms, whichever stage
happens to be open is charged the whole 50 ms — regardless of what that stage does.

A stall that is uniformly distributed across stages, sized independently of stage work,
and lands on the upstream stub as readily as on `auth`, is not stage work. It is the
**process stopping**.

## What that rules out, and what it leaves

It explains why five targeted hypotheses all failed: **there is no single stage to fix.**

| hypothesis | why it could never have worked |
|---|---|
| thread-per-regex churn | fixes `policy`, which is 4% of the outliers |
| Redis publishes | measured at 0.13 ms; also stage-local |
| scanner thread pool | fixes `input_scan`, which never appears |
| `pipeline_trace` build | post-upstream, one location |
| telemetry enqueue | measured at 0.00 ms |

Remaining candidates, all process-global:

1. **GC pauses** — the leading one. A gen2 collection stops the whole interpreter for
   exactly this kind of scattered, work-independent interval. It also fits the one signal
   that *did* move: task 6 cut allocation (trace 35,577 → 2,218 B) and gave a small but
   real ~5.6% p90 improvement, which is what less allocation pressure would look like.
2. **GIL contention** under the scanner thread pool — a long-held lock stalls every
   coroutine on that worker.
3. **Host-level scheduling / CPU steal** — the container is limited to 4 vCPU on a 16-core
   host running other work.

## Next step

Measure GC directly rather than reason about it: `gc.callbacks` in the gateway recording
pause durations, exposed alongside `telemetry_ms`. If gen2 pauses correlate with the tail,
the fix is allocation reduction and generation tuning — neither of which any of the five
attempted fixes was.

Candidates 2 and 3 are distinguishable from the same data: GIL contention would scale with
the scanner pool (the 4/16/32 sweep showed no trend, which argues against it), and CPU
steal would show in host-level metrics rather than in-process ones.

## Standing facts

| | |
|---|---|
| median firewall tax | **8.20 ms — inside the 20 ms budget** |
| p99 firewall tax | 103–135 ms across runs |
| gateway CPU at conc 16 | ~1.5 of 4 vCPU |
| `telemetry_ms` | 0.00 at median and tail |
