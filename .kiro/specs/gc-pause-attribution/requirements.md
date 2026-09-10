# Task 7 — is the tail a GC pause? Measure it.

## Why

`…-tail-is-a-process-global-stall.md`: the tail excess lands on a different stage nearly
every request (auth 22%, output_guardrail 18%, rate_limit 16%, and 27% on `model_output`
whose duration is the stub's own fixed pacing). Its size (22–82 ms) is unrelated to what
the stage normally does — `auth` normally 0.40 ms, absorbing 53.

`PipelineStageTimer.mark_segment_end` charges all elapsed time since the previous boundary
to the stage being closed, so a process pause is billed to whichever stage is open. The
observed shape is the process stopping.

Leading candidate is **GC**: a gen2 collection stops the interpreter for exactly this kind
of scattered, work-independent interval. It also fits the only signal that has moved —
task 6 cut allocation 16× and produced a real ~5.6% p90 improvement.

**Five hypotheses have been refuted, four of them after a rebuild and a sweep.** This one
gets measured before anything is changed.

## Requirements

**R1 — measure, do not infer.** Record actual GC pause durations from `gc.callbacks`, and
attribute to each request the pause time that elapsed *during* it. No deriving GC cost
from residuals; that is what produced two corrections already.

**R2 — the instrument must not perturb what it measures.** The callback runs inside every
collection. It must be O(1), allocation-free on the hot path, and must never raise —
an exception in a `gc.callbacks` entry is swallowed by CPython but the work is wasted.

**R3 — per-generation counts.** gen0 collections are frequent and cheap; gen2 are rare and
expensive. "GC took 50 ms" without the generation cannot direct a fix.

**R4 — off by default.** `GATEWAY_GC_INSTRUMENTATION=1` to enable. A diagnostic that
always runs is a permanent cost for an occasional question.

**R5 — the answer must be falsifiable.** If `gc_pause_ms` is ~0 on tail requests, GC is
refuted and the remaining candidates are GIL contention and host CPU steal — both
distinguishable from data already collected (the flat 4/16/32 pool sweep argues against
GIL; CPU steal shows at host level, not in-process).

## Out of scope

Tuning GC thresholds or reducing allocation. Those follow only if the measurement
implicates GC.
