# MCP Platform — Architecture & Sequence Diagrams

Grounded in the live-validated behavior of the running stack (see
`MCP_VALIDATION_ROOTCAUSE_REPORT.md` for evidence). Code refs are to the current tree.
Components: **frontend** (React) → **control plane** (Django, `control/ai_mesh_control`) →
**gateway** (FastAPI, `gateway/ai_mesh_gateway`) → **broker** (`services/mcp-broker`) →
**per-org sandbox** (`ai-mesh/mcp-sandbox`) → upstream MCP server.

---

## 1. MCP tool-call lifecycle (Streamable-HTTP JSON-RPC route)

```mermaid
sequenceDiagram
    autonumber
    participant A as Agent / SDK
    participant MW as Gateway auth middleware
    participant R as org_mcp_jsonrpc
    participant CP as Control plane (Redis+HTTP)
    participant SC as Scan orchestrator
    participant BK as MCP broker
    participant SB as Per-org sandbox (runc)
    participant U as Upstream MCP server

    A->>MW: POST /gateway/{org}/mcp/{server} (Bearer key)
    MW->>MW: sha256(key)→Redis lookup → AuthContext(org_slug)
    MW-->>A: 401 if invalid
    R->>R: _validate_org_scope: auth.org_slug == URL org? else 403 org_scope_violation
    R->>R: _enforce_mcp_org_rate_limits (TPM/burst via RATE_LIMITER/REDIS_CLIENT)
    R->>R: _mcp_body_too_large → 413 (DoS cap)
    R->>CP: _get_server_config (version-invalidated cache)
    R->>R: _server_disabled(config)? (is_active/is_exposed_to_agents) → block -32601
    R->>CP: _get_enabled_tools (effective_scan_controls, mcp_tier2_enabled)
    R->>R: _is_tool_disabled / per-key allowlist / cap → block
    R->>SC: _scan_tool_args_block (INPUT)
    SC->>SC: gate: scan_controls_configured==False → SKIP (off-by-default)
    SC->>SC: Tier-1 (regex PII/secret/injection) → tag/redact/block
    SC->>SC: Tier-2 (Bedrock) IF tier2 control enabled AND org gate → verdict
    SC-->>R: blocked? → JSON-RPC error + audit
    R->>BK: _adapter_forward (broker_send_rpc)
    BK->>SB: spawn/reuse per-org sandbox, run stdio / proxy http|sse|ws
    SB->>U: tools/call
    U-->>SB: result
    SB-->>BK: result (capped read)
    BK-->>R: result
    R->>SC: _scan_tool_result_floor (OUTPUT: redact/block PII/secret/exfil)
    R->>CP: _record_gateway_event (decision, tags, scan_trace)
    R-->>A: result (scanned) or block/redact
```

Note: **stdio / websocket** are always sandbox-routed; **streamable-http / sse** route via
the sandbox by default (`MCP_HTTP_VIA_SANDBOX`, default on) with a legacy direct-httpx
fallback. Internal routes `/v1/mcp/internal/tools-call` and `/discover-tools` (used by the
control-plane `MCPToolCallView`/`MCPToolListView`) apply the same `_server_disabled` +
tool-disable + scan gates.

---

## 2. Scan-control resolution & precedence

```mermaid
flowchart TD
    Q[tool call: org, server, tool, direction] --> ROWS[MCPScanControl rows for org]
    ROWS -->|no rows| OFF[scan_controls_configured=False → NO scan<br/>tier1+tier2 skipped]
    ROWS -->|rows exist| PICK[_pick_control per tier+direction]
    PICK --> SCOPE{scope match}
    SCOPE -->|tool: server_id+tool_name| T[tool-scope]
    SCOPE -->|server: server_id, no tool| S[server-scope]
    SCOPE -->|org: no server_id| O[org-scope]
    T & S & O --> RANK[max by SCOPE_RANK tool3>server2>org1,<br/>then priority]
    RANK --> DIR{direction matches input/output/both?}
    DIR -->|no| DEF[built-in default: tier1 on / tier2 off]
    DIR -->|yes| WIN[winning control: enabled, action, strict_mode]
    WIN --> ENF[enforce: block>redact>monitor; disabled row → tier OFF]
```

Precedence (verified live + executable probe): **tool > server > org**, higher `priority`
wins within a scope, scope dominates priority, direction filters, a winning `enabled=False`
row turns the tier OFF for that scope. Action floor: `block > redact > monitor/tag(observe)`.
Config changes bump `mcp:scan_ver:{org}[:{server}]` (Redis) → version-invalidated caches
refetch promptly.

---

## 3. Two-tier scanning decision

```mermaid
flowchart TD
    IN[payload + effective_controls + enabled_info] --> G{scan_controls_configured is False?}
    G -->|yes| SKIP[SKIP — allow, no tags/redaction/block]
    G -->|no / unknown| T1{tier1_ctrl.enabled?}
    T1 -->|no| T1S[tier1 skipped]
    T1 -->|yes| T1R[Tier-1 regex detectors<br/>PII/secret/ip/cred/injection/exfil]
    T1R --> ACT{action}
    ACT -->|block posture, any finding| BLK[BLOCK]
    ACT -->|redact| RED[mask + byte-verify fail-closed]
    ACT -->|monitor/tag| MON[detect+tag+allow]
    T1R --> T2{tier2_ctrl.enabled AND org mcp_tier2_enabled?}
    T2 -->|no| DONE[done]
    T2 -->|yes| BED[Tier-2 Bedrock judge<br/>via _get_input_scanner INPUT_SCANNER]
    BED --> V{verdict block/redact/flag}
```

Off-by-default gate keys on an **affirmative** `is False`, so a control-plane outage
(flag absent) **fails safe to scanning**. Tier-2 reaches Bedrock only when a tier2 control
is enabled AND the org `mcp_tier2_enabled` gate is on (else skipped).

---

## 4. Sandbox architecture (per-org isolation)

```mermaid
flowchart LR
    BK[broker docker_manager] -->|one per org| SB
    subgraph SB[org sandbox container]
      direction TB
      RT[runtime = runc default<br/>gVisor/runsc opt-in via MCP_SANDBOX_RUNTIME]
      SEC[SecurityOpt: no-new-privileges + default seccomp]
      CAP[CapDrop ALL]
      RO[ReadonlyRootfs, non-root uid=1000]
      NS[own PID namespace]
      CG[cgroups: 2GB mem / 1 CPU / 256 pids]
      NET[per-org bridge net mcp_sandbox_net_ORG<br/>egress open for npx/uvx; SSRF-guarded remote URLs]
      TMP[tmpfs /tmp noexec,nosuid]
    end
    SB -->|stdio spawn: node/npx/python/python3/uv/uvx ONLY| U[upstream MCP]
    BK -->|idle reaper| REAP[stop/cleanup]
```

Command allowlist blocks arbitrary executables; SSRF egress guard blocks reserved/internal
addresses on remote MCP URLs; idle reaper cleans up sandboxes. All verified live.

---

## 5. Config → behavior propagation

```mermaid
sequenceDiagram
    participant FE as Frontend / API
    participant DB as Postgres
    participant SIG as Django signals
    participant RDS as Redis (mcp:scan_ver, firewall:config)
    participant GW as Gateway caches
    FE->>DB: save MCPScanControl / MCPServerRegistration / MCPToolRegistration / FirewallConfig
    SIG->>RDS: bump_scan_version(org[,server]) INCR
    SIG->>RDS: FirewallConfig → publish config_updates + bump org scan_ver
    GW->>RDS: _current_scan_version = mget(org_ver, srv_ver) → composite
    GW->>GW: composite changed → invalidate _server_config / _enabled_tools cache → refetch
    GW->>GW: new config applied on next call (no full-TTL wait)
```

`_get_server_config` and `_get_enabled_tools` are both version-invalidated (the #17 fix
brought `_get_server_config` in line), so disable/enable/scan-control/Tier-2 toggles take
effect on the next call rather than after the 120s/30s TTL.
