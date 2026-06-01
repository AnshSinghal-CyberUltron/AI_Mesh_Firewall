# Phase 6E — Output Guardrail Controls verification (2026-06-01)

## Scope
Module 1.7 **Output Guardrail Controls** for org `zeroshield`, target model `anthropic/claude-haiku-4.5`.

UI settings verified against DB/Redis:
| Control | Expected | DB/Redis |
|---------|----------|----------|
| Master (response filtering) | Enabled | `response_filtering_enabled=True`, `output_scan_enabled=True` |
| PII | Detect → Redact | `output_pii_action=redact` |
| Credential | Detect → Block | `output_credential_action=block` |
| IP Leakage | Detect → Flag | `output_ip_leakage_action=flag` |
| Policy | Detect → Block | `output_policy_action=block` |
| Hallucination | Detect → Rewrite | `output_hallucination_action=rewrite`, threshold `0.20` |
| Incident logging | Enabled | `output_incident_logging_enabled=True` |

## Fixes applied during verification

### 1. `config_sync.py` — LLM model reload never ran (Critical)
**Root cause:** `_reload_llm_models` used `import main as gateway_main` while uvicorn loads `ai_mesh_gateway.main`. Two module objects → `LLM_ROUTER` always `None` on reload → BYOK models had no decrypted API keys → 500/502 on inference.

**Fix:** `from ai_mesh_gateway import main as gateway_main`

**Evidence:** After rebuild, startup log: `LLM model configs reloaded from Redis (8 models)`.

### 2. `main.py` — org master switch ignored (OG5)
**Root cause:** Non-stream output guard gated only on global `CONFIG.output_guard_enabled`, not per-org `output_scan_enabled` (mapped from UI `response_filtering_enabled`).

**Fix:** Added `_output_guard_active` requiring `org_config.output_scan_enabled`.

## Live E2E results (gateway `:8300`, zeroshield API key)

| Case | HTTP | Output action | Notes |
|------|------|---------------|-------|
| PII generation prompt | 200 | **redact** | `X-ZeroShield-Action: redact`, patterns `email,phone_us`; SSN masked in body |
| Hallucination (context: blue widgets only) | 200 | **rewrite** | Rewritten ungrounded answer; header `X-ZeroShield-Action: rewrite` |
| Benign "say hello" | 200 | **rewrite** | Hallucination threshold 0.20 is aggressive — low grounding on short replies triggers rewrite |
| Credential-ish prompt (tutorial sk-proj) | 200 | input **redact** | Blocked at input scan / redact before LLM; not output-guard credential path |
| IP/internal URL prompt | 403 | input **block** | Tier-2 input scan blocks before output stage |
| Baseline "Reply exactly…" | 403 | input **block** | Tier-2 flags phrasing as prompt injection |

**Routing note:** Requests for `anthropic/claude-haiku-4.5` were **rerouted** to `live-triage-openai` by policy adjudicator (`X-ZeroShield-Rerouted: true`). Output guard still enforced on final response.

## Direct `OutputGuard.inspect()` matrix (zeroshield org_config)

| Synthetic output | Action | Threat |
|------------------|--------|--------|
| SSN + email + phone | redact | pii |
| `Authorization: Bearer eyJ…` (64+ chars) | **block** | credential |
| `https://staging.zeroshield.internal/admin` | **flag** | ip_leakage |
| Ungrounded claim vs context | rewrite | hallucination |

## Unit tests
`test_output_guardrails.py` (metadata/redact) — runnable via unittest.
`test_output_guard_modes.py` — requires `fakeredis` (not in gateway image); not run in container.

## Sign-off

```
Fix: Output guardrail controls + model reload
─────────────────────────────────────
Services running locally: YES
Docker containers healthy: YES
Config sync (Redis ↔ DB): YES
LLM models loaded after fix: YES (8 models)
PII redact (live): YES
Credential block (inspect): YES
IP flag (inspect): YES
Hallucination rewrite (live): YES (threshold very sensitive)
UI settings match backend: YES
─────────────────────────────────────
Status: ✅ VERIFIED (with caveats below)
```

## Caveats / follow-ups
1. **Live credential block E2E** hard to trigger: `CREDENTIAL_EXPOSURE_PATTERNS` does not include `sk-proj`/OpenAI key formats; `sk-proj` may hit scanner `secret` path with **redact** not **block**. Bearer tokens **do** block.
2. **Hallucination threshold 0.20** rewrites many benign short responses — consider raising to 0.5+ for production UX.
3. **Tier-2 input scan** intercepts many output-test prompts before output guard runs — use benign inputs + model-generated sensitive output for E2E.
4. **Dynamic routing** may not use haiku even when requested; disable routing or adjust policy adjudicator for strict model testing.
5. **Streaming path:** `secure_streaming.py` may still omit `org_config` on `inspect()` (OG3) — not re-verified this session.
