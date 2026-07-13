# Output Guardrail — Security Report (CHAT PIPELINE SCOPE)

**Date:** 2026-07-10  
**Scope (locked):** Chat pipeline **stream + non-stream + OpenAI SDK** only.  
**Out of scope:** MCP / RAG / vector.  
**Evidence:** `mcp-parallel/findings/output-guardrail-assessment/`  
**Baseline restored:** `output_pii_action=redact`, `output_credential_action=block`, `factuality_check_enabled=false`

## Verdict (chat)

**Production-ready for proven chat paths:** redact, rewrite, flag, and hallucination (flag + rewrite) hold on HTTP and OpenAI SDK for both stream and non-stream.

| Area | Status | Evidence |
|------|--------|----------|
| Redact | **LIVE PASS** | `proof-chat-redact-sdk.json` |
| Rewrite (stream) | **LIVE PASS** (F-002) | `proof-chat-rewrite-stream-sdk.json`, `proof-chat-pii-rewrite-honesty-sdk.json` |
| Flag under block mode | **LIVE PASS** (F-003) | `proof-chat-flag-honesty-sdk.json` |
| Hallucination rewrite | **LIVE PASS** | `proof-chat-hallucination-rewrite-sdk.json` |
| Hallucination flag | **LIVE PASS** | `proof-chat-hallucination-flag-sdk.json` |
| OpenAI SDK | **LIVE PASS** | all of the above (stream + non-stream) |
| MCP / RAG | Out of scope | — |

## Architecture (chat only)

```
Model → raw completion
  → OutputGuard.inspect (org §1.7; factuality OR-enables hall)
  → non-stream: enforce_output → rewrite | redact | flag | block
  → stream: SecureStreamingResponse
       rewrite: hold mid-stream → rewrite at DONE (+ redact_pii net)
       redact / flag / block as configured (flag does not escalate to block)
  → client (HTTP / OpenAI SDK)
```

## Findings (chat)

| ID | Sev | Status | Notes |
|----|-----|--------|-------|
| F-002 | HIGH | **FIXED + LIVE** | Stream rewrite at DONE; no coerce→block |
| F-003 | HIGH | **FIXED + LIVE** | Flag delivers under `enforcement_mode=block` |
| F-005/6 | MED | **FIXED + LIVE** | Hall rewrite/flag via SDK; factuality OR-enable |
| PII rewrite honesty | HIGH | **FIXED + LIVE** | Sanitize honors rewrite (static rewrite, no raw email) |
| F-001 | HIGH | Open | IP UI flag/rewrite still coerced→redact (fail-closed) |
| F-004 | MED | Open | Maskable block→redact (document or UI label) |
| F-013 | MED | Open | Stream block HTTP 200+SSE vs non-stream 400 |

## Code changes (this lane)

- `enforcement.py` — no rewrite→block / flag→block coercions
- `secure_streaming.py` — rewrite at DONE; flag delivers
- `output_guard.py` — factuality OR-enable; sanitize honors rewrite
- Tests: enforcement, secure_streaming, stream_trace, `test_output_guard_hallucination_rewrite.py`

## Remaining risks (chat)

1. IP detector UI honesty (flag/rewrite→redact)
2. Stream vs non-stream HTTP status on hard block
3. Full detector×action matrix beyond PII/hall (credential/IP/policy live permutations)
4. Concurrent/long-output soak not re-run in this lane

## Evidence index

- `proof-chat-redact-sdk.json`, `proof-chat-rewrite-stream-sdk.json`, `proof-chat-flag-honesty-sdk.json`
- `proof-chat-hallucination-rewrite-sdk.json`, `proof-chat-hallucination-flag-sdk.json`
- `proof-chat-pii-rewrite-honesty-sdk.json`, `proof-chat-fix-summary.json`
- Unit: `config-action-matrix-unit.json`, gateway pytest gates
