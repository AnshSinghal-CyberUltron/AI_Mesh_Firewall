# Demo MCP tool selection — verification

Date: 2026-07-15

## Summary

Extended the ZeroShield OpenAI SDK Demo so operators can **select a specific MCP
server and tool** (Pattern B/C), while keeping `extra_body.mcp_context` as
structured-context injection only (Pattern A).

## What shipped

| Area | Change |
|------|--------|
| Docs | `examples/zeroshield-openai-demo/docs/MCP_INTEGRATION.md` + README/DEMO_SCRIPT |
| Backend | `GET /api/mcp/servers`, `GET /api/mcp/servers/{id}/tools`, `POST /api/mcp/tools/call`, `GET /api/mcp/gateway-endpoint` |
| Frontend | MCP tab split: Context injection + Tool execution (server/tool selects) |
| SDK sample | `mcp_gateway_client.py` (httpx JSON-RPC; optional official `mcp` package) |
| Trace UX | Blocked requests without `pipeline_trace` show block reason, not generic empty |

## Live evidence (zeroshield org)

### Pattern B — demo proxy tools/call

```
server=everything-1 tool=echo decision=allow has_nonce=true snippet_ok=true
LIVE_DEMO_TOOL_CALL_PASS
UI_MARKERS_OK (mcp-tool-section, mcp-server, mcp-tool-run)
```

### Pattern C — mcp_gateway_client.py

```
url=http://gateway:8300/gateway/zeroshield/mcp/everything-1
tools/list → 13 tools
tools/call echo → "Echo: gateway-client-nonce-9c2e"
MCP_GATEWAY_CLIENT_PASS
```

### Pattern A regression — mcp_context chat

```
context_ok True action allow has_stages True
```

### Unit tests (in demo container)

```
tests/test_mcp_tool_proxy.py → 7 passed
```

## Where to select server/tool

1. **Demo UI** → MCP tab → Tool execution (server + tool dropdowns)
2. **Main console** → Firewall 1.4 → Guardrail Simulator / Connector Tool Execution
3. **Gateway JSON-RPC** → `POST {host}/gateway/{org}/mcp/{server_slug}` with Bearer org key
4. **Control API** → `POST /api/mcp-connector/tools/call/` with `server_slug` + `name`

`extra_body.mcp_context` does **not** select an MCP server — it injects scanned context into chat.
