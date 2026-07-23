# Output Validation Report

## What is validated

- Input injection blocked before upstream (403 / PermissionDeniedError on Responses)
- Output guard scans model responses for PII/credentials
- Demo shows raw vs final response path in pipeline visualizer

## Guardrail demo procedure

See [GUARDRAIL_VALIDATION.md](GUARDRAIL_VALIDATION.md) for the full attack/safe/sensitive matrix and executable gates.

1. **Guardrails** tab → select **Attack** vector → **Run Governance Check** → expect block
2. Select **Safe** → **Run Safe Prompt** → expect allow with `output_guardrail` stage = allow
3. Inspect `pipeline.stages` for `input_scan` and `output_guardrail` actions

## SDK behavior

Responses API blocked requests raise `PermissionDeniedError` with nested OpenAI error.
Chat API blocked requests raise `APIStatusError` with flat ZeroShield envelope — both
are handled in `ZeroShieldClient.scenario_guardrail_probe()`.

## Pass criteria

| Check | Expected |
|-------|----------|
| Injection input | 403, `content_blocked` or similar |
| Block shows request_id | Present in error body or zeroshield |
| Output PII in model response | redact/block per org config |
| Audit trail | Enforcement event when telemetry enabled |

## Incident correlation

Use `pipeline.request_id` / `zeroshield.request_id` to correlate:

- Gateway structured logs
- Control plane EnforcementEvent records
- SOC dashboard in ZeroShield console
