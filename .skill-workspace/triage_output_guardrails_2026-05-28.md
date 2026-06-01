# Triage verdict: Output guardrails live verification (2026-05-28)

## Final plan

| Step | Action |
|------|--------|
| 1 | PUT round-trip: confirm `/api/firewall/config/` returns saved detector actions |
| 2 | Live E2E: `POST /v1/chat/completions` non-stream, `anthropic/claude-haiku-4.5`, generation prompts (not echo) for PII/credential/IP |
| 3 | Assert headers: `X-ZeroShield-Action`, status 403 for block, redacted body for redact |
| 4 | Fix if confirmed: non-stream path must respect org `output_scan_enabled`; streaming must pass `org_config` to `inspect()` |
| 5 | Run gateway unit tests `test_output_guard_modes.py` |

## Agent summaries

- **OG1:** APPROVE conditional live gateway test (generation prompts, stream:false)
- **OG2:** PUT alone insufficient for enforcement proof
- **OG3:** CONFIG_SYNC carries fields; streaming missing org_config on inspect()
- **OG4:** APPROVE OutputGuardSimulator for UX; use non-stream curl for deterministic matrix
- **OG5:** CONFIRMED master switch mismatch (`response_filtering_enabled` vs global `output_guard_enabled`)
