# Task 6 — pipeline_trace off the allow path

## Measured

One allowed non-streaming request, 3.5 KB prompt:

| | bytes | share |
|---|---:|---:|
| response body | 38,225 | 100% |
| `pipeline_trace` | **35,572** | **91%** |
| ├─ `stages` | 28,354 | 79.7% of the trace |
| │  └─ text payload inside stages | 23,538 | — |
| ├─ `prompt_submitted` / `prompt_preview` / `input_text` | 3,624 | 3 copies of the same prompt |
| └─ everything else | ~3,594 | — |
| **stages reduced to name/action/latency** | **587** | — |
| **a metrics-only trace** | **684** | **1.9% — a 52× reduction** |

`_scrub_trace_for_client` (`main.py:723`) is applied **only** on the blocked path
(`:957`). The allow paths (`:2876`, `:9211`, `:11109`) attach the trace unscrubbed.

## Why this is a candidate for the out-of-stage tail

`…-tail-is-outside-every-stage.md`: 97% of the p99 excess is spent while no stage is
executing. Building and serialising a 35 KB structure per request happens **outside every
stage timer**, and the allocation churn — several copies of the prompt per request, ~1
MB/s of garbage at 27 RPS — is a plausible source of intermittent GC pauses, which is the
shape of a tail that leaves the median untouched.

**It is a candidate, not a conclusion.** Two hypotheses have already been refuted this
session (thread churn by experiment; blocking Redis publishes by measurement — Redis
averages 0.13 ms and `publish` costs 3.84 µs server-side, so seven of them cannot be
90 ms). This one gets the same treatment: make it switchable, measure both ways, record
the result whichever way it falls.

## Requirements

**R1 — the default must not change.** `full` stays the default so no client contract
moves on the strength of an unproven hypothesis. Changing the default becomes a separate
decision with the measurement in hand.

**R2 — the harness must keep working in the reduced mode.** `drive.py` and `load.py`
verify all nine stages ran and read per-stage latencies. A mode that removed those would
make the very experiment unrunnable — and would silently disable the nine-stage honesty
check, which is the check that exists to stop a shorter pipeline being reported as a
full one.

**R3 — no evidence leak.** The reduced projection must not expose anything the full trace
does not already expose to the same caller. It removes fields; it must never add or
rename.

**R4 — one projection function, applied at every allow-path attach site**, so the sites
cannot drift apart.

**R5 — measured on the same sweep**, comparable with the baseline, task 2, and the tail
attribution.

## Out of scope

Deciding the production default, and the UI's "rehydrate by reference" redesign the plan
sketches for G4.2. This task makes the cost measurable; what to ship follows from it.
