# MCP Gateway Exploration — Memory Agent Findings

## Architecture Overview

```
Client → FastAPI Gateway (ai_mesh_gateway) → [stdio subprocess IN gateway | HTTP upstream | ws adapter]
         ↕ Redis auth + policy sync
         ↕ Django Control Plane (mcp_connector, policy engine, MCPEvent audit)
```

**Planned:** Client → Gateway → Sandbox Controller → per-tenant sandboxes  
**Actual:** Client → Gateway → shared container subprocess (stdio) or direct upstream proxy

## Key Files

### Gateway (Data Plane)

| Path | Role |
|------|------|
| gateway/ai_mesh_gateway/main.py | App startup, MCP router mount L4028-4075 |
| gateway/ai_mesh_gateway/mcp_proxy.py | Org MCP gateway, JSON-RPC, scanning, audit |
| gateway/ai_mesh_gateway/mcp_stdio_adapter.py | npx/uvx subprocess spawn, env sandbox |
| gateway/ai_mesh_gateway/mcp_ws_adapter.py | WebSocket MCP transport |
| gateway/ai_mesh_gateway/mcp_oauth_proxy.py | Per-org upstream OAuth (Linear etc.) |
| gateway/ai_mesh_gateway/mcp_oauth.py | Gateway-as-OAuth-server for VS Code |
| gateway/ai_mesh_gateway/mcp_scan_orchestrator.py | Tier-1/Tier-2 MCP scan |
| gateway/ai_mesh_gateway/middleware.py | AuthMiddleware, MCP key fields L79-104 |
| gateway/ai_mesh_gateway/policy_engine.py | evaluate_mcp_policies L559+ |
| gateway/ai_mesh_gateway/rate_limiter.py | TPM limiter (not wired to MCP) |

### Control Plane

| Path | Role |
|------|------|
| control/ai_mesh_control/mcp_connector/models.py | MCPServerRegistration, MCPToolRegistration, MCPEvent |
| control/ai_mesh_control/mcp_connector/views.py | CRUD, tool discovery, event ingestion |
| control/ai_mesh_control/mcp_connector/mcp_firewall_client.py | No-op stubs (external firewall removed) |
| control/ai_mesh_control/policy/policy_package/mcp_policies.py | MCP policy catalog |
| control/ai_mesh_control/core/models.py | GatewayAPIKey MCP fields L352-359 |

### Frontend

| Path | Role |
|------|------|
| frontend/src/components/MCPConnectorPanel.jsx | Server registration, stdio/npx UI |
| frontend/src/components/MCPScanControlMatrix.jsx | Scan control matrix |

### Infra / Services

| Path | Role |
|------|------|
| docker-compose.yml | gateway + mcp-stub; MCP_BROKER_URL L187; npm_cache volume L207 |
| gateway/Dockerfile | Node/npx baked into gateway runtime |
| services/mcp-broker/src/main.py | Scaffold (health only) |
| services/mcp-stub/src/main.py | Hermetic streamable-http MCP for E2E |

### Tests

| Path | Role |
|------|------|
| gateway/ai_mesh_gateway/tests/test_e12_mcp_security.py | Credential block, redaction, key allowlist |
| gateway/ai_mesh_gateway/tests/test_mcp_bare_proxy_scan.py | External proxy scanning |
| gateway/ai_mesh_gateway/tests/test_mcp_oauth_org_scope.py | OAuth org scope regression |

## Request Flow (Current)

1. Client POST `/gateway/{org}/mcp/{server}` with Bearer API key
2. AuthMiddleware validates key → AuthContext (org_slug, mcp_allowed_tools, etc.)
3. org_mcp_jsonrpc validates org scope, resolves server config from control plane
4. tools/call: key allowlist → scan args → forward (adapter or upstream) → scan result → MCPEvent audit
5. stdio: mcp_stdio_adapter spawns npx IN gateway container

## Graphify (gateway/ai_mesh_gateway/graphify-out/)

- 1488 nodes, Community 4/11/12/23 = MCP cluster
- No sandbox-related nodes
- Stale vs HEAD — run `graphify update .` after changes
