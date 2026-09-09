# The untraced ~50 ms is `redact_all`, run to build a trace that is then discarded

**Tag `[M]`** · py-spy 0.4.2, live gateway worker under concurrency-16 load, 25 s,
200 Hz, **121,169 samples, 0 errors**, commit `245cc536`

## The profile

On-CPU self time, idle/blocked frames excluded:

| by file | share |
|---|---:|
| **`patterns.py`** | **74.54%** |
| `socket.py` | 6.49% |
| `base64.py` | 6.15% |
| everything else | <2% each |

| by function | share |
|---|---:|
| `_redact_all_raw` | **43.16%** |
| `_iter_transport_decodes` | 13.54% |
| `_redact_obfuscated` | 5.89% |
| `a85decode` | 4.46% |
| `_decode_one` | 3.22% |
| `_iter_short_b64_infra` | 1.91% |
| `_decode_one_a85` | 1.42% |
| `b64decode` | 1.20% |
| **redaction + transport decoding** | **≈78%** |

## Whose redaction

Not telemetry's: `telemetry_ms` measures **0.00 ms** because `TELEMETRY` is unconfigured in
this stack, so `_emit_telemetry` returns immediately. The redaction is the **trace
builder's**.

`_redact_trace_text` (`main.py:4904`) runs `redact_all` over "any text destined for the
operator pipeline_trace". The non-stream allow path calls it on four fields per request:

```python
prompt=_redact_trace_text(prompt),
forwarded_prompt=_redact_trace_text(redacted_prompt or prompt),
policy_redacted_prompt=_redact_trace_text(policy_redacted_prompt),
response_text=_redact_trace_text(_extract_response_from_completion(resp)),
```

At the plan's own measured **8.19 ms p50 per `redact_all` pass** (G4.4), four to six passes
over a 3.5 KB prompt is **33–49 ms** — which is the ~50 ms of untraced CPU, arrived at
independently from `docker stats ÷ RPS`.

**It is untraced by construction**: it runs while *building* the trace, after every stage
timer has closed.

## The part that makes it worth fixing

In `metrics` trace mode those fields are **projected away immediately afterwards**. The
gateway redacts several kilobytes of text, four to six times, and then discards the result.

This is also why task 6 gave only ~5.6% p90: the projection dropped the *payload* but the
*building* — and its redaction — still ran in full.

## Why eight hypotheses missed it

Every one targeted the traced region. The traced firewall tax is 8.2 ms; the real
per-request cost is ~56 ms. **The work was outside the instrument, in code that exists to
populate the instrument.**

| # | hypothesis | verdict |
|---|---|---|
| 1 | thread-per-regex | refuted — p99 unchanged |
| 2 | Redis publishes | refuted — 0.13 ms avg |
| 3 | trace payload size | partial — ~5.6% p90 (payload, not building) |
| 4 | scanner pool | refuted — no trend at 4/16/32 |
| 5 | telemetry enqueue | refuted — 0.00 ms |
| 6 | GC | refuted — +0.07 ms |
| 7 | CFS throttling | refuted — 22.8 ms lifetime |
| 8 | **trace redaction** | **confirmed — 78% of on-CPU time** |

## What must not be broken by the fix

`_redact_trace_text` exists so raw PII cannot reach the operator UI: "the very thing the
firewall redacts before the LLM ever sees it" must not reappear in the trace. The fix is
therefore **not to redact less** — it is to **not build fields that are about to be
discarded**, so that redaction is skipped only where its output provably never reaches
anyone.

Any change must keep `full` mode byte-identical, since that is the mode where the text
does reach the UI.

## Method note

The profile needed no new instrumentation in the gateway — py-spy sampled the running
process from outside. That is the tool that should have been reached for eight hypotheses
ago: the trace can only report regions someone chose to instrument, and this work was, by
construction, in none of them.
