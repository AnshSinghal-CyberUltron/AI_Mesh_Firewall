# Output Validation Report

## What is validated

- Input injection blocked before upstream (403 / PermissionDeniedError on Responses)
- Output guard scans model responses for PII/credentials
- Demo shows raw vs final response path in pipeline visualizer

## Guardrail demo procedure

1. **Guardrails** tab → injection prompt → expect block
2. Safe prompt → expect allow with output_guardrail stage = allow
3. Inspect `pipeline.stages` for `output_guardrail` action

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
