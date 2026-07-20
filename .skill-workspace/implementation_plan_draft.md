# MCP Gateway Implementation Plan (DRAFT — Needs User Approval)

## Phase 0 — Decisions (User Gate)

- [ ] Choose sandbox runtime (Docker recommended for repo alignment)
- [ ] Confirm sandbox granularity (per-tenant default)
- [ ] Confirm dev-mode in-process stdio fallback

## Phase 1 — Sandbox Controller MVP (2-3 weeks est.)

### 1.1 Sandbox Controller Service

- Extend services/mcp-broker OR new services/sandbox-controller
- API: POST /v1/sandbox/{org_id}/ensure, POST /v1/sandbox/{org_id}/rpc, DELETE /v1/sandbox/{org_id}
- Docker: one container per org, pinned base image, no gateway secrets mounted
- Resource limits: CPU, memory, timeout (match MCP_STDIO_* env naming)

### 1.2 Gateway Integration

- mcp_stdio_adapter.py: add SANDBOX_MODE env; when true, RPC to controller instead of create_subprocess_exec
- Preserve existing scan/audit path in mcp_proxy.py (no change to policy layer)

### 1.3 Rate Limiting (parallel track)

- Call enforce_org_tpm_rate_limit at org_mcp_jsonrpc entry
- Add MCP-specific burst counter if needed

### 1.4 Tests

- Unit: sandbox controller mock + gateway adapter
- Integration: test_e12_mcp_security.py unchanged behavior through sandbox path
- E2E: MCPConnectorPanel stdio server via sandbox (mcp-stub stdio variant optional)

## Phase 2 — Hardening

- Package allowlist enforcement at controller (MCP_STDIO_PACKAGE_ALLOWLIST)
- Network egress policy per sandbox
- Autoscale / idle reaper for sandboxes (mirror stdio reaper pattern)

## Phase 3 — Full Plan Alignment

- Arbitrary MCP server install flow with image build per registration
- mcp-broker tool mediation (if distinct from sandbox controller)
- Frontend: sandbox status / resource usage in MCPConnectorPanel

## Risks

- Latency increase on cold sandbox start (mitigate: warm pool per org)
- Docker socket access from gateway (mitigate: controller owns Docker API)
- Breaking existing stdio servers that need host filesystem (document limitations)

## Out of Scope (Phase 1)

- WASM/E2B
- Rewriting HTTP/SSE MCP proxy path
- Replacing mcp_scan_orchestrator
