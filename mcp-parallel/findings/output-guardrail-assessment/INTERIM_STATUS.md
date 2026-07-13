# Interim status — Output Guardrail Assessment

**Date:** 2026-07-10  
**Phase:** 2 in progress (report-first; no production fixes yet)  
**Evidence:** `mcp-parallel/findings/output-guardrail-assessment/`

## Executive snapshot

Chat output guard **holds** when it runs (PII redact / credential block proven).  
**Critical fail-open:** MCP with zero scan-control rows skips all RESULT scanning and leaks raw canaries.  
**UI honesty broken:** Flag/Rewrite/IP Flag do not match runtime under common org settings.

## Top fix queue (Phase 6)

1. **MCP-OG-001** — Always-on result floor even when `scan_controls_configured=false` (fail-closed)
2. **F-001** — Honor UI IP action or remove Flag/Rewrite/Block options that are coerced
3. **F-002/F-003** — UI must not offer Rewrite on stream / Flag under block mode without labeling coercion; or change lattice to match UI
4. **F-009** — Fix `/health` `config_sync_loaded` for org-scoped config
5. **F-013** — Align stream block HTTP status with non-stream (or document SDK contract)

## Coverage so far

- Phase 1 maps: chat + MCP/RAG + live discovery
- Chat attack batch1 (PII/cred/obfuscation/stream)
- MCP attack batch1 (F-008 + MCP-OG-001)
- UI parity proofs F-001, F-002, F-003, F-009

## Still required before completion claim

- Full detector×action matrix (Allow/Block/Redact/Rewrite/Flag/Disabled)
- RAG egress live matrix
- OpenAI SDK stream/non-stream priority
- All connected models spot-check
- Hallucination/rewrite live enforce (user req 7A)
- Remaining bypass classes (XML/YAML/JSON nested, CoT, cross-request)
- Phase 5 full report → Phase 6 fixes by severity

## RAG / OpenAI SDK (parent completed after agent failure)

- Config restored to `output_pii_action=redact` (was dirty `rewrite` at agent start).
- RAG: **INCONCLUSIVE** — `/v1/rag/query` requires `collection` (400); E11 not live-exercised. `attack-matrix-rag-batch1.json`
- OpenAI SDK: gateway venv client used; raw canary absent from egress (`proof-openai-sdk-stream.json`). Some calls input_scan-blocked before OG.
