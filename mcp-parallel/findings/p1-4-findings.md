# P1.4 — "MCP sandbox is temporarily unavailable" on tool call (B3)

**Date:** 2026-07-02  
**Agent:** cursor-ralph-iter4  
**Stack:** frontend :8180, control :8100, gateway :8300, broker :8311  

## Goal

Trigger a stdio MCP **tools/call** that surfaces **"MCP sandbox is temporarily unavailable"** in the
UI, with network trace + broker/gateway logs.

## Method

1. Playwright login → MCP panel → register/sync **Everything MCP** (stdio preset).
2. Tool Execution tab → select `echo` → **Execute Tool**.
3. **Before click:** `docker stop ai_mesh_mcp_broker` (forces gateway broker-unreachable path).
4. Capture UI error, `POST /api/mcp-connector/tools/call/` response, gateway logs.
5. `docker start ai_mesh_mcp_broker` (cleanup).

Script: `scripts/playwright_mcp_p1_sandbox_unavailable_repro.mjs`

## Result: B3 **confirmed**

| Check | Observed |
|-------|----------|
| UI error | `Tool execution failed: Adapter error: MCP sandbox is temporarily unavailable` |
| API `detail` | `Adapter error: MCP sandbox is temporarily unavailable` (HTTP 400) |
| `request_id` | `f36a6c57-3315-49` |
| Gateway logs | 5× `Broker unreachable … Temporary failure in name resolution` → `Adapter forward error … MCP sandbox is temporarily unavailable` |

## Root-cause chain (documented — fix is P6/B3, not Cursor-owned gateway)

```
UI Execute Tool
  → control POST /api/mcp-connector/tools/call/
  → gateway mcp_proxy internal tools-call (stdio)
  → mcp_sandbox_client.broker_send_jsonrpc
  → httpx to http://mcp-broker:8311/v1/sandbox/{org}/stdio/rpc
  → broker down / DNS fail → 5 retries (_request_with_503_retry, only 503 retried)
  → RuntimeError("MCP sandbox is temporarily unavailable")  [mcp_sandbox_client.py:100]
  → mcp_proxy wraps as Adapter error → control 400 → UI setError
```

**Production B3 variant (cold start, not broker-down):** broker returns **502** for agent-not-ready
(`routes.py:185`) but client does **not** retry 502 — only 503. First tool call after register can
hit the same user-visible string without stopping the broker. Documented in `scripts/ralph/mcp_progress.md`.

**Cursor-owned follow-up (P6):** eager sandbox warm on register + broker 503-provisioning semantics
(Claude session owns `routes.py` / gateway retry). `docker_manager.py` readiness polling is seam-adjacent.

## Artifacts

- Screenshots: `mcp-parallel/findings/p1-4/01-tool-execution-tab.png`, `02-after-failed-tool-call.png`
- `mcp-parallel/findings/p1-4/report.json`
- `mcp-parallel/findings/p1-4/network.jsonl`
- `mcp-parallel/findings/p1-4/broker-logs-before.txt`
- `mcp-parallel/findings/p1-4/gateway-logs-after.txt` (lines 115–120: broker unreachable + adapter error)

## Conclusion

B3 user-visible error **reproduced with evidence**. Repro is deterministic via broker stop; production
trigger is cold-start 502 / network wiring. No frontend fix required for the message itself — P6 items
#19–21 address root cause on gateway+broker seam.
