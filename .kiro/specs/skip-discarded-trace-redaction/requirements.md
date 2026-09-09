# Task 8 — stop redacting trace text that is about to be discarded

## Measured cause

`…-ANSWER-untraced-cpu-is-trace-redaction.md`: **78% of on-CPU time** is `redact_all` and
its transport decoders (py-spy, 121,169 samples). The caller is `_redact_trace_text`, run
on four fields per request while *building* the trace — after every stage timer has closed,
which is why eight hypotheses aimed at the traced 8.2 ms all missed it.

In `metrics` trace mode those fields are projected away immediately afterwards. The gateway
redacts several kilobytes four to six times and discards the result.

## Requirements

**R1 — `full` mode byte-identical.** That is the mode where the text reaches the operator
UI. Nothing about it may change.

**R2 — never redact less where the output is visible.** `_redact_trace_text` exists so raw
PII cannot reach the operator UI — "the very thing the firewall redacts before the LLM ever
sees it". This task does not weaken redaction; it skips *building* fields whose output
provably reaches no one.

**R3 — the predicate use must not break.** `main.py:4935` calls
`_redact_trace_text(raw) != raw` as a **detection predicate** ("did redaction change
anything?"). A blanket change to `_redact_trace_text` would silently invert that answer.
This is the trap in this task: the same helper serves two purposes, one of which is not
about text at all.

**R4 — blocked paths untouched.** A 403 body is the one place a caller most needs the
detail, it is rare, and it has its own scrubber.

**R5 — measured with `--repeat`,** against the ~56 ms/request baseline, reporting CPU per
request as well as latency. The claim to test is a CPU reduction; latency follows from it
only if queueing was the mechanism.

## Success

- CPU per request falls materially from ~56 ms
- `full` mode output byte-identical
- the redaction predicate at `:4935` still returns the same answers
- zero new test failures
