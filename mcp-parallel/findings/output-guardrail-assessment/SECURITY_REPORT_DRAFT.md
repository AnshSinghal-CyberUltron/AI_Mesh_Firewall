# Output Guardrail — Phase 5 draft security report (interim)

**Status:** Assessment in progress — report-first; production fixes NOT applied yet.  
**Date:** 2026-07-10  
**Evidence root:** `mcp-parallel/findings/output-guardrail-assessment/`

## Production readiness (current)

**NOT READY** for a claim that “every detector/action/path is proven secure.”

| Area | Status |
|------|--------|
| Chat OG when reached | Holds (redact/block) — batch1 |
| UI ↔ runtime honesty | **FAIL** — F-001, F-002, F-003 |
| MCP RESULT default | **CRITICAL FAIL-OPEN** — MCP-OG-001 |
| Config sync | Works (F-009 health flag misleading) |
| RAG E11 live | INCONCLUSIVE (needs `collection`) |
| OpenAI SDK | No raw leak in blocked paths; OG often not reached |
| Full action matrix (unit) | **DONE** — 21 UI≠resolved coercions (`config-action-matrix-unit.json`) |
| All models | Not complete |
| Hallucination live ON | Not proven (detector off in baseline) |

## Confirmed findings (severity order)

1. **MCP-OG-001 / F-015 CRITICAL** — Zero `MCPScanControl` → `scan_skipped` → raw tool RESULT leak (email/SSN/AKIA/IP/exfil). `attack-matrix-mcp-batch1.json`
2. **F-001 HIGH** — UI IP `flag` → runtime `redact`. `proof-f001-ip-flag.json`
3. **F-002 HIGH** — UI `rewrite` + stream → `block`. `proof-f002-rewrite-stream.json` (+ v2)
4. **F-003 HIGH** — UI `flag` + org `enforcement_mode=block` → `block`. `proof-f003-flag-escalation.json` (+ v2)
5. **F-008 HIGH** — §1.7 does not control MCP/RAG
6. **F-013 MED** — Stream block HTTP 200 vs non-stream 400
7. **F-009 LOW** — Misleading `config_sync_loaded`

## Architecture (verified)

```
Chat: model → OutputGuard.inspect → enforce_output (non-stream) | SecureStreaming coerce (stream)
MCP:  tool result → _mcp_security_scan (SKIP if no controls) → floor
RAG:  /v1/rag/query → E11/E11b (not §1.7)
```

## Phase 6 fix queue (do not start until report accepted)

1. Always-on MCP result floor when `scan_controls_configured=false`
2. UI honesty: label or remove coerced actions (IP flag/rewrite/block; stream rewrite; flag under block mode)
3. Health `config_sync_loaded` for org keys
4. Align stream block HTTP status / document SDK contract
5. Wire stream path through `enforce_output` (F-010) for one authority

## Evidence index

- `PLAN.md`, `INTERIM_STATUS.md`, `EARLY_FINDINGS.md`
- `PHASE1_CHAT_OUTPUT_MAP.md`, `PHASE1_MCP_RAG_EGRESS.md`
- `live-discovery.json`
- `attack-matrix-chat-batch1.json`, `attack-matrix-mcp-batch1.json`, `attack-matrix-rag-batch1.json`
- `proof-f001-*.json`, `proof-f002-*.json`, `proof-f003-*.json`, `proof-f009-*.json`
- `config-action-matrix-unit.json` (50 enforce + 20 inspect; 21 UI≠enforce coercions)
- `proof-openai-sdk-stream.json`
