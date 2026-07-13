# MCP Platform Lifecycle Sequences (2026-07-08)

End-to-end request flows validated against live stack (frontend :8180, control :8100, gateway :8300, broker :8311).

## 1. Server registration (UI)

```
Operator → MCPConnectorPanel addServer
  → POST /api/mcp-connector/servers/
  → connection_status=syncing + background _resync_server_tools
  → POST /api/mcp-connector/servers/{id}/tools/ (internal discover)
  → gateway internal_discover_tools → broker → sandbox agent → upstream tools/list
  → MCPToolRegistration rows + connection_status connected|failed
```

## 2. Scan control CRUD

```
MCPScanControlMatrix → POST/PATCH/DELETE /api/mcp-connector/scan-controls/
  → MCPScanControl row (Postgres)
  → post_save/post_delete → bump_scan_version INCR mcp:scan_ver:{org}[:{server}]
  → gateway _get_enabled_tools cache version mismatch → refetch enabled-tools
  → scan_controls_configured = bool(scan_rows)
```

## 3. Org Tier-2 gate toggle

```
MCPScanControlMatrix → PUT /api/firewall/config/ { mcp_tier2_enabled }
  → FirewallConfig.save → Redis SET + config_updates PUBLISH
  → bump_scan_version (org) [signals.py — iteration 5]
  → gateway enabled-tools embeds mcp_tier2_enabled
  → orchestrator _org_tier2_allowed → tier2_skipped | tier2 scan
```

**Note:** PATCH on `/api/firewall/config/` returns **405**; UI and harnesses must use **PUT**.

## 4. Direct MCP tool call (org JSON-RPC)

```
Client → POST /gateway/{org}/mcp/{server} (Bearer API key)
  → org scope + rate limits + key allowlist
  → _get_enabled_tools (scan_controls_configured, per-tool actions)
  → _mcp_security_scan (SKIP if scan_controls_configured=false)
  → broker_send_rpc → sandbox agent → stdio|http|sse|ws upstream
  → _scan_tool_result_floor (E12 + render-leak + resource caps)
  → _record_gateway_event (async audit) + Prometheus metrics
```

## 5. Chat pipeline tool call (internal)

```
Control MCPToolCallView → gateway internal_tools_call (X-Gateway-Internal-Key)
  → same scan wrappers; actor=None (policy field-RBAC gap documented)
  → sandbox or legacy HTTP path → result floor scan
```

## 6. Control-plane tools/call (policy plane)

```
PolicyTestView / tools/call → policy_evaluate BEFORE gateway
  → gateway forward → independent of scan_controls_configured on gateway
  → masks per MCP Security Policies even when scan controls = monitor
```

## 7. External MCP proxy

```
ext_mcp_proxy → enabled_info=None (transport-level)
  → tier1 may run (fail-safe); default posture tag/monitor
  → NOT gated by org scan_controls_configured
```

## 8. Sandbox ensure / RPC

```
gateway broker_send_rpc → broker /v1/sandbox/{org}/rpc
  → _require_canonical_org_slug (CHG-0111)
  → find_container (label verify CHG-0112)
  → agent /rpc (optional X-Sandbox-Agent-Key CHG-0136)
  → upstream_manager (SSRF + egress allowlist + response caps)
```

## 9. Cache invalidation failure mode (proven)

```
Harness SET mcp:scan_ver = float → control INCR fails silently
  → gateway stale enabled-tools up to 30s
FIX: INCR-only; DEL+INCR repair; never SET scan_ver keys
```

## 10. Scan-off by default (product contract)

```
0 MCPScanControl rows → scan_controls_configured=false
  → gateway JSON-RPC: NO tier1/tier2/tags/policy scan in _mcp_security_scan
  → server default_scan_action=block does NOT re-enable scanning
  → static resource floors may still block (depth/blocks/split-secret)
```
