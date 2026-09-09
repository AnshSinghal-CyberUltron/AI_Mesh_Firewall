# Requirements — stop trace redaction scaling with prompt length

## Measured problem

py-spy on the scan-only path, 4096-char prompts, policies active:

```
39.6%  redact_all      <- redacting text for the operator TRACE
26.0%  _scan_all       <- the policy engine, the actual security work
```

**The firewall spends more CPU redacting text for the trace than evaluating policy.**

`pipeline_trace._truncate(text, limit=1200)` redacts the **whole** text and then keeps 1200
characters. `redact_all` is linear at ~1.2 µs/char, so the cost of a diagnostic artefact is
**unbounded in prompt length**:

| prompt | redact_all (hyperscan active) |
|---|---|
| 1200 | 0.81 ms |
| 4096 | 2.67 ms |
| 8192 | 5.34 ms |
| 32 KB | ~21 ms (extrapolated) |

Only the first 1200 characters are ever kept.

## Why the current order exists

The comment is explicit: *"Redact PII/secrets BEFORE truncating so raw PII is never
persisted/echoed in the trace (R17)."* Truncating first would cut a secret in half, and the
half that remains may no longer match its pattern — leaving a partial secret in the trace.
That reasoning is correct and must survive.

## Requirements

**R1 — Byte-identical output.** For every input, the new `_truncate(text, limit)` must return
exactly what the current one returns. This is a cost change, not a behaviour change.

**R2 — No new leak path.** No sensitive span that is redacted today may appear unredacted in
the trace. R1 implies this, but it is stated separately because it is the reason the task
exists and the thing a reviewer must check.

**R3 — Cost bounded by the trace limit, not the prompt.** After the change, redaction cost
for the trace must be a function of `limit`, not of `len(text)`. A 32 KB prompt must cost
what a 2 KB prompt costs. This is the point of the task; a mere constant-factor saving does
not satisfy it.

**R4 — Residual risk stated, not hidden.** If any input shape can still differ, it must be
named explicitly with its bound, not left implicit.

## Non-goals

- Not changing what `redact_all` detects.
- Not touching the enforcement path. This is the **trace** only — the text an operator reads.
  Redaction of text sent to the provider is a separate, unaffected path.
