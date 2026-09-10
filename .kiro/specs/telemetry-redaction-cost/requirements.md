# Task 5b (plan G4.4) — telemetry redaction on the request path

## Status: hypothesis, not yet measured

The firewall tax reconciles as `wall = stage_sum + telemetry_enqueue_ms + overhead_ms`
(`pipeline_trace.py:96-98`). Every tail attribution so far covered the nine stages and
`overhead_ms`; **`telemetry_enqueue_ms` is not a stage**, so it was invisible.

Solving the accounting from the concurrency-16 run:

| | wall | stage_sum | overhead | ⇒ telemetry |
|---|---:|---:|---:|---:|
| median | 508.40 | ~505.8 | 2.40 | ~0.2 ms |
| tail | 561.30 | ~508.3 | 4.30 | **~48.7 ms** |

**That figure is derived by mixing medians of separate quantities and is not a
measurement.** It is the same shortcut that produced the 28% error corrected in
`…-CORRECTION-task6-effect-and-run-variance.md`. `telemetry_ms` is now surfaced into the
trace root so the next run measures it. Nothing here is acted on until it does.

## The mechanism, if confirmed

`telemetry.py:192-211` — `build_telemetry_event` loops **21 metadata text keys** and calls
`redact_all` on each non-empty one, plus one on `prompt_snippet`: up to **22 synchronous
passes per request**, after the upstream call, on the request path.

The plan's G4.4 independently measured `redact_all` at **8.19 ms p50 / 18.05 ms p99 per
pass**. Six to eight populated keys is 50–65 ms — the size of the gap.

Worse, `main.py:11195-11197` sets **three keys to the same value**:

```python
_tel_md["response_snippet"] = (response_text or "")[:2000]
_tel_md["sanitized_output"] = (response_text or "")[:2000]
_tel_md["output_text"]      = (response_text or "")[:2000]
```

Three identical `redact_all` passes over identical input, for one request.

## Requirements

**R1 — measure before changing.** Confirm `telemetry_ms` at median and tail from the
trace, over `--repeat 3`, before touching the redactor.

**R2 — the audit scrub must not weaken.** This loop exists so raw PII cannot reach the
audit log. Any change must produce **byte-identical** redacted output over the corpus.
Making the tail faster by scrubbing less would be a security regression traded for a
latency number.

**R3 — no raw PII may sit anywhere new.** If the work moves off the request path, the
buffer it moves into must not be a place raw PII newly persists.

**R4 — measured with `--repeat`,** so the result is not another n=1 claim.

## Candidate fixes, cheapest first

| # | change | risk |
|---|---|---|
| 1 | memoise `redact_all` by content, as task 1F did for `canonicalize_for_detection` — collapses the 3 identical passes and any other repeats | low: pure function, bounded cache |
| 2 | stop writing the same 2000 chars to three keys | low, but a client/telemetry contract question |
| 3 | move `build_telemetry_event` off the request path entirely | higher — R3 applies |

## Out of scope

Changing what is redacted, or the redaction patterns.
