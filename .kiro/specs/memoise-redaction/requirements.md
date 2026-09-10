# Task 8b — memoise `redact_all`, so the shipped default benefits too

## Why task 8 is not enough

Task 8 skips building trace text that `metrics` mode discards. **`full` is the default**,
and there all four `redact_all` passes still run — so task 8 does nothing for the shipped
configuration.

Reading the allow-path call site (`main.py:11184`):

```python
prompt=_trace_text(prompt),
forwarded_prompt=_trace_text(redacted_prompt or prompt),
```

`redacted_prompt` is `None` whenever no redaction fired (`main.py:7952`), so
`forwarded_prompt` redacts **the identical string** already redacted as `prompt`. Two
identical `redact_all` passes per request, in **both** modes, on the common clean path.

`redact_all` is **78% of on-CPU time** (py-spy, 121,169 samples) and the plan's G4.4 puts
it at 8.19 ms p50 per pass.

## Requirements

**R1 — byte-identical output.** `redact_all` is a security function; memoising must not
change a single character of any result. Proven over the corpus, not asserted from purity.

**R2 — purity verified, not assumed.** `redact_all(text)` is
`_redact_obfuscated(text, _redact_all_raw(text))` — string in, string out, no I/O and no
mutation of arguments. R1's corpus check is what actually establishes this; the reading
only makes it plausible.

**R3 — bounded.** A cache keyed on request text is unbounded by nature. `maxsize` caps it,
as task 1F's `canonicalize_for_detection` cache does.

**R4 — no cross-tenant hazard introduced.** This is the requirement that decides the
design. A process-wide cache keyed on prompt text means tenant B's identical prompt gets
tenant A's cached *redaction*. That output is a pure function of the text — no tenant
state, no config — so the result is identical either way. But the tracker's own note on
G4.1 flagged a cross-request cache as a surface worth avoiding, so the **hit must be
intra-request** to be worth taking: bound `maxsize` small enough that entries do not
survive meaningfully between requests under load, and record that reasoning.

**R5 — measured on the same sweep, `full` mode**, since that is the configuration this
task exists to improve.

## Success

- corpus byte-identical
- CPU per request falls in **`full`** mode
- zero new test failures
