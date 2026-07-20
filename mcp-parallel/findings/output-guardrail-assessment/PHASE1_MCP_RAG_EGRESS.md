# Phase 1 — MCP / RAG / non-chat egress map

Source: explore agent (MCP/RAG output egress). Persisted 2026-07-10.

## Critical architecture fact

§1.7 `FirewallConfig` / `OutputGuardrailControls` configure **chat `OutputGuard` only**.
They do **not** drive MCP `_scan_tool_result_floor` or RAG E11/E11b.

## Surfaces

| Surface | Authority | §1.7 lattice? |
|---------|-----------|---------------|
| Chat completions/stream | `OutputGuard` + `enforce_output` | Yes |
| MCP org/ext/internal results | `_scan_tool_result_floor` | No |
| `/v1/rag/query` docs | E11 PII + E11b injection | No |
| `/v1/embeddings` | Input scan only | N/A |
| PolicyTestView dry-run | Policy rules only | No |
| Attack/Output simulators | Chat path | Yes |

## Residual fail-opens (to prove live)

1. MCP monitor/tag → deep/wide results may forward unscanned
2. RAG injection scanner exception → **doc kept**
3. No hallucination/rewrite on MCP
4. Operator PolicyTestView bypasses ML output guard

## Follow-up for Phase 2

- Treat MCP + RAG as **separate** attack lanes (not covered by flipping §1.7 toggles)
- Prove UI toggle of PII Block does **not** change MCP tool-result egress
- Live-prove RAG fail-safe keep with injected injection-bearing chunk
