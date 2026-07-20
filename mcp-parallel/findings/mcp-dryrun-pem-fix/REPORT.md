# MCP Dry-Run vs Live PEM — fix verification

**Date:** 2026-07-15  
**Org:** zeroshield

## Root cause (confirmed)

- Dry-Run (`POST /api/policies/test/`) sent `response: ""` while PEM rule is `field=response` (output-only) → no match → ALLOW.
- Live Call executes `echo`, then evaluates tool **response** → matched `PKG2_MCP_RESPONSE_SECRET_REDACT`.
- Rule was historically **block**; product choice for demo: **redact**.

## Changes

1. `mcp_policies.py` — PEM rule → `redact`, replacement `[REDACTED_PRIVATE_KEY]`, full BEGIN..END regex.
2. `seed.py` — update existing package rules by `rule_key` (no `--reset` required).
3. `MCPGuardrailSimulator.jsx` — echo-like dry-run simulates `Echo: …` output for response rules.
4. OpenAI demo scenarios — MCP REDACT (PEM) instead of BLOCK.
5. `mcp_action_scenarios_live.py` — PEM expect `redact_pem`.

## Live proof

| Path | Result |
|------|--------|
| Dry-Run no sim | `allow` (expected — documents old gap) |
| Dry-Run with echo sim | `redact`, matched `Redact PEM private key in response`, `redacted_response=Echo: [REDACTED_PRIVATE_KEY]` |
| Live Call echo+PEM | HTTP 200, no raw BEGIN/body, `[REDACTED_PRIVATE_KEY]` in result |
| SSN regression | HTTP 200, SSN masked |
| Harness | **8/8 PASS** (`everything-1` + `linear-manual-oauth`) |

## Reseed

```
seed_policy_package --org-slug zeroshield
→ rules_updated: 1 (PEM), Redis POLICY_SYNC pushed
```
