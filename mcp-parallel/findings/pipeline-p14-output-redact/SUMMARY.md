# PIPELINE-0014 — Output redact byte-verified

## Fix
- `enforce_output()`: maskable PII/secret/credential block verdict → redact; noop scrub → block.
- Connected sync Path D + `_apply_output_guard_nonstream` use `_out_decision` from `enforce_output()`.

## Proof
- `test_pipeline_output_redact.py`: SSN+email masked in egress; noop → 403 block.
- Full gateway gate: 2029 passed.
