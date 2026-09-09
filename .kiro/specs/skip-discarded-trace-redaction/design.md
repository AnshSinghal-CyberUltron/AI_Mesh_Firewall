# Task 8 — design

## A separate helper, not a change to the existing one

R3 forbids touching `_redact_trace_text`: it is also a detection predicate. So add

```python
def _trace_text(text) -> str:
    """Redact text destined for the trace — unless the trace mode will discard it."""
    if trace_mode() == "metrics":
        return ""
    return _redact_trace_text(text)
```

and use it **only** at the allow-path trace-builder call sites that pass text fields:
`prompt=`, `forwarded_prompt=`, `policy_redacted_prompt=`, `response_text=`.

`_redact_trace_text` keeps its current behaviour everywhere else — the predicate at
`:4935`, the blocked-path builders (R4), and every caller not feeding a projected field.

## Why returning `""` is correct here, not a shortcut

The metrics projection already drops these keys from the emitted trace, so their value is
unobservable. Returning `""` makes that explicit rather than computing a value to throw
away. In `full` mode the branch is not taken and behaviour is identical (R1).

The saving is not "less redaction" — it is *not redacting text nobody will read*.

## Verification

1. `full` mode: byte-identical trace for a fixed request, before vs after.
2. `metrics` mode: the projected trace is unchanged (those keys were already absent).
3. The `:4935` predicate returns the same answer in both modes for PII and non-PII text —
   the specific regression R3 exists to prevent.
4. Full suite.
5. E2E `--repeat 2`: CPU per request, and latency.
