# Sandbox Transport Contract — gateway ↔ broker ↔ sandbox-agent

> **Version:** 1.0.0-draft  
> **Status:** Provisional ratification (see §12)  
> **Hive:** `hive-1782976205971-u1gav1`  
> **Authors:** Cursor Ralph iter8 (`cursor-ralph-iter8`)  
> **Consumers:** gateway (`mcp_sandbox_client`, `mcp_proxy`), broker (`routes.py`), sandbox-agent (`sandbox-image/agent/`)

This document is the **authoritative seam contract** for moving **all MCP transports**
(stdio, streamable-http, sse, websocket) through the per-org gVisor sandbox. Both parallel
sessions MUST implement against this spec. Research inputs: `oss-research-remote-transport-proxies.md`,
`oss-research-oauth-sandbox-client.md`, `oss-research-docker-hardening.md`, `broker-sandbox-lifecycle.md`.

---

## 1. Goals & non-goals

### Goals

1. **Single data path:** gateway never dials upstream MCP URLs directly; all JSON-RPC goes
   `gateway → broker → sandbox-agent → upstream`.
2. **Transport-agnostic RPC:** one request/response envelope; transport-specific fields are nested.
3. **OAuth control plane external:** gateway/control own PKCE/browser/DCR; sandbox receives
   **injected Bearer only** (`oss-research-oauth-sandbox-client.md`).
4. **Egress allowlist:** sandbox may connect only to hosts declared per server registration.
5. **Error compatibility:** gateway maps agent/broker errors to existing JSON-RPC `-32000` surfaces (B3).

### Non-goals (v1)

- OAuth client implementation inside sandbox.
- Gateway-direct upstream HTTP/WS (legacy path deprecated, not removed in v1).
- Full duplex streaming of `tools/call` progress notifications (v1 buffers; see §6).

---

## 2. Architecture

```
┌──────────────┐     internal key      ┌──────────────┐    org network    ┌─────────────────────┐
│   Gateway    │ ────────────────────► │ mcp-broker   │ ────────────────► │ sandbox-agent :9320 │
│  mcp_proxy   │  POST /v1/sandbox/    │ routes.py    │  POST /rpc        │ (per-org container) │
│              │  {org}/rpc            │              │                   │                     │
└──────────────┘                       └──────────────┘                   └──────────┬──────────┘
                                                                                    │
                         ┌──────────────────────────────────────────────────────────┤
                         │ stdio          │ streamable-http/sse │ websocket          │
                         ▼                ▼                     ▼                    │
                    subprocess         httpx client          websockets client        │
                    (npx/uvx)          + Bearer              + Bearer               │
                         │                │                     │                    │
                         └────────────────┴─────────────────────┴──► upstream MCP  │
                                                              (egress allowlisted)    │
```

**Trust boundaries**

| Component | Trust level | Responsibilities |
|-----------|-------------|------------------|
| Gateway | trusted control plane | policy, scan, audit, OAuth token storage, build `upstream` block |
| Broker | trusted orchestrator | Docker lifecycle, forward RPC, cold-start retry |
| Sandbox-agent | **untrusted code runs here** | transport proxy only; enforce egress allowlist |
| Upstream MCP | external | resource server |

---

## 3. Endpoints

### 3.1 Sandbox-agent (in-container, port 9320)

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/health` | Liveness + readiness probe |
| `POST` | `/rpc` | **Transport-agnostic** JSON-RPC forward (v1) |
| `POST` | `/rpc/stream` | Optional v1.1 — SSE upstream streaming (§6.2) |

**Authentication:** v1 relies on per-org Docker network isolation (broker-only reachability).
v1.1 MAY add `X-Sandbox-Internal-Key` — out of scope until agent `/rpc` auth gap closed
(`broker-sandbox-lifecycle.md` §2).

### 3.2 Broker (mcp-broker :8311)

| Method | Path | Status |
|--------|------|--------|
| `POST` | `/v1/sandbox/{org_slug}/ensure` | existing |
| `POST` | `/v1/sandbox/{org_slug}/rpc` | **NEW** — unified transport RPC |
| `POST` | `/v1/sandbox/{org_slug}/stdio/rpc` | **DEPRECATED** — alias to `/rpc` with `transport=stdio` inferred |
| `GET` | `/v1/sandbox/{org_slug}/status` | existing |
| `DELETE` | `/v1/sandbox/{org_slug}` | existing |

**Auth:** `X-MCP-Broker-Key` (unchanged).

### 3.3 Gateway client

Replace `broker_send_jsonrpc` stdio-only URL with:

```text
POST {MCP_BROKER_URL}/v1/sandbox/{org_slug}/rpc
```

Legacy `…/stdio/rpc` supported until P6 integration complete.

---

## 4. Request envelope — `SandboxRpcRequest`

Both broker and agent accept the same JSON body (broker forwards verbatim).

```jsonc
{
  "server_slug": "linear",           // required — cache key inside agent
  "transport": "stdio",              // required enum — see §5
  "method": "tools/call",            // required — MCP JSON-RPC method
  "params": { "name": "echo", "arguments": {} },
  "jsonrpc_id": 1,                   // required — echoed in response
  "timeouts": {
    "connect_seconds": 30,           // upstream TCP/TLS/WS handshake
    "init_seconds": 120,             // through MCP initialize complete
    "method_seconds": 60             // single JSON-RPC round-trip
  },

  // §5.1 — required when transport == "stdio"
  "stdio": {
    "command": "npx",
    "args": ["-y", "mcp-remote", "https://mcp.linear.app/sse"],
    "env": { "MCP_REMOTE_CONFIG_DIR": "/data/mcp-auth" }
  },

  // §5.2 — required when transport ∈ {streamable-http, sse, websocket}
  "upstream": {
    "url": "https://mcp.example.com/mcp",   // canonical registered URL
    "allowed_hosts": ["mcp.example.com"],    // gateway-computed; agent MUST enforce
    "headers": {
      "Authorization": "Bearer <access_token>",
      "Mcp-Session-Id": "<optional>"
    },
    "oauth_client_role": "forbidden_in_sandbox"  // literal sentinel — agent rejects if "client"
  }
}
```

### 4.1 Validation rules (agent)

| Rule | Error code |
|------|------------|
| `transport` missing or unknown | `-32004` transport_unsupported |
| `stdio` block missing for `transport=stdio` | `-32602` invalid_params |
| `upstream` missing for http/sse/ws | `-32602` invalid_params |
| `upstream.url` host ∉ `allowed_hosts` | `-32002` egress_denied |
| `oauth_client_role == "client"` | `-32602` invalid_params |
| `server_slug` empty | `-32602` invalid_params |

### 4.2 Validation rules (gateway — before broker call)

- Build `allowed_hosts` from registered `server.url` hostname only (exact match after IDNA/punycode normalize).
- For stdio+mcp-remote: parse wrapped HTTPS URL from args; add that host to `allowed_hosts`.
- Inject `Authorization` from per-org token store; never forward inbound gateway client token.
- Strip secrets from `env` except allowlisted passthrough (`mcp_stdio_common._build_child_env`).

---

## 5. Per-transport upstream behavior (inside sandbox)

### 5.1 `stdio`

**Implementation:** existing `stdio_manager.send_jsonrpc` (unchanged semantics).

| Phase | Behavior |
|-------|----------|
| Install | Lazy `npx`/`uvx` fetch inside container on first spawn |
| Start | `create_subprocess_exec`, command allowlist, `shell=False` |
| Health | `initialized==true` after successful MCP `initialize` |
| Reuse | Process pool keyed `{org_slug}/{server_slug}`; env/args mismatch → respawn |
| OAuth | Bearer via `--header` in args (mcp-remote) or env BYOK; `needs_reauth` on interactive prompt |

**Egress:** subprocess may reach npm/PyPI registries + hosts in `allowed_hosts` (mcp-remote upstream).
v1: registry egress unrestricted (P7 H13); v1.1: default-deny proxy (`oss-research-docker-hardening.md`).

### 5.2 `streamable-http`

**Implementation:** `httpx.AsyncClient` (new in P4.9).

| Step | Action |
|------|--------|
| Connect | `POST upstream.url` with JSON-RPC body, headers from `upstream.headers` |
| Session | Persist `Mcp-Session-Id` from response per `server_slug` |
| Initialize | First RPC MUST be `initialize`; cache capabilities per connection pool entry |
| Later calls | Reuse client + session; attach session header on subsequent POSTs |
| 401 upstream | Return `needs_reauth: true` in `_meta` (do not attempt OAuth in sandbox) |

**URL:** single endpoint per MCP Streamable HTTP spec (POST `/mcp`).

### 5.3 `sse` (legacy HTTP+SSE)

**Implementation:** `httpx` long-poll + POST message endpoint.

| Step | Action |
|------|--------|
| Connect | `GET {base}/sse` → obtain `sessionId` from first event |
| Messages | `POST {base}/messages?sessionId=` with JSON-RPC |
| Deprecation | New registrations SHOULD use `streamable-http`; sse supported for back-compat |

### 5.4 `websocket`

**Implementation:** `websockets` library (new in P4.10).

| Step | Action |
|------|--------|
| Connect | `websockets.connect(upstream.url, extra_headers=upstream.headers)` |
| Multiplex | One WS per `server_slug`; serialize JSON-RPC with lock per connection |
| Initialize | Same as HTTP — first message `initialize` |
| Close | Idle timeout closes WS; re-init on next RPC |

---

## 6. Streaming semantics

### 6.1 v1 — buffered (MUST implement first)

For `tools/call` and all methods: agent **buffers** the complete upstream response before returning
to broker. Maximum buffer: `MCP_AGENT_MAX_RESPONSE_BYTES` (default **8 MiB**, matches stdio line cap).

If upstream returns SSE/stream body, agent reads until JSON-RPC `result` or `error` complete or cap exceeded.
Cap exceeded → `-32000` with message `upstream response too large`.

### 6.2 v1.1 — streaming extension (OPTIONAL, post-P4 verify)

`POST /rpc/stream` — request body identical to `/rpc` with additional:

```json
{ "accept_streaming": true, "stream_format": "sse" }
```

Response: `Content-Type: text/event-stream` with events:

```text
event: jsonrpc
data: {"jsonrpc":"2.0","id":1,"result":{...}}

event: done
data: {}
```

Gateway MAY consume stream for progressive tool output; not required for P4 gate.

### 6.3 WebSocket upstream notifications

If upstream sends unsolicited JSON-RPC notifications on WS, agent **drops** them in v1 (log at DEBUG).
v1.1 MAY buffer latest `notifications/progress` per call id.

---

## 7. Response envelope

### 7.1 Success / JSON-RPC error (HTTP 200 from agent)

```jsonc
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": { /* or "error": { "code": -32603, "message": "..." } */ },
  "_meta": {
    "transport": "streamable-http",
    "needs_reauth": false,
    "upstream_status": 200,
    "session_id": "abc123",          // if applicable
    "duration_ms": 42
  }
}
```

**Gateway MUST** strip `_meta` before returning to external MCP clients unless audit needs it.

### 7.2 Agent-defined error codes (`error.code`)

| Code | Name | When | Gateway maps to |
|------|------|------|-----------------|
| `-32000` | sandbox_error | generic / adapter error | existing B3 message |
| `-32001` | needs_reauth | upstream 401 or oauth prompt | trigger `needs_reauth` on control row |
| `-32002` | egress_denied | host not in allowlist | `-32000` "upstream not allowed" |
| `-32003` | upstream_timeout | connect/init/method timeout | `-32000` with timeout hint |
| `-32004` | transport_unsupported | unknown transport | `-32000` |
| `-32602` | invalid_params | contract validation | `-32602` |

### 7.3 Broker HTTP errors (gateway `mcp_sandbox_client` mapping)

| Broker status | Detail | Gateway user message |
|---------------|--------|----------------------|
| 401 | bad broker key | MCP broker authentication failed |
| 429 | org quota | MCP sandbox capacity exceeded |
| 502 | agent connection/error | MCP sandbox is temporarily unavailable |
| 503 | docker down / not running / provisioning | MCP sandbox is starting; please retry shortly |

**B3 fix (P6):** cold-start SHOULD return **503** + `Retry-After`, not 502 (`mcp_progress.md` B3).

### 7.4 Agent down (broker → gateway)

Broker raises 502 when agent HTTP fails after retries (`routes.py:_post_agent_rpc`).

---

## 8. Timeouts & lifecycle

### 8.1 Defaults (align with today)

| Timeout | Env var | Default | Applies to |
|---------|---------|---------|------------|
| Broker→agent HTTP | `MCP_BROKER_AGENT_TIMEOUT` | 130s | broker client |
| Init | `MCP_STDIO_INIT_TIMEOUT` | 120s | stdio + upstream initialize |
| Method | `MCP_STDIO_METHOD_TIMEOUT` | 60s | per JSON-RPC |
| Connect | `MCP_AGENT_CONNECT_TIMEOUT` | 30s | http/ws TLS handshake (new) |
| Agent ready retries | `MCP_SANDBOX_AGENT_READY_RETRIES` | 8 | broker cold-start |
| Idle reap (container) | `MCP_SANDBOX_IDLE_TIMEOUT` | 600s | broker reaper |
| Idle reap (stdio child) | `MCP_STDIO_IDLE_TIMEOUT` | 600s | agent stdio_manager |

**Rule:** `broker_timeout >= max(timeouts.*) + 10s`.

### 8.2 Install / start / health lifecycle

```
ensure(org) → container running → GET /health (broker probe, optional)
  → first /rpc with method=initialize
      stdio: spawn → initialize handshake
      http/sse/ws: connect → POST/WS initialize
  → subsequent /rpc reuses connection/process
```

**Readiness:** `/health` returns `200` with:

```json
{
  "status": "ok",
  "service": "mcp-sandbox-agent",
  "org_slug": "acme",
  "process_count": 3,
  "connection_count": { "stdio": 3, "streamable-http": 1, "websocket": 0 }
}
```

`process_count` / `connection_count` are informational; broker MUST NOT treat `process_count==0` as unhealthy.

---

## 9. Egress allowlist policy

### 9.1 Rules (agent-enforced)

1. For `upstream.url`: parse host; MUST equal one entry in `allowed_hosts` (case-insensitive, IDNA-normalized).
2. **Deny** literal IP hosts unless explicitly listed (blocks metadata SSRF `169.254.169.254`).
3. **Deny** if host is `localhost` / `127.0.0.1` / `host.docker.internal` unless registered (normally never).
4. stdio subprocess outbound: agent logs but does not intercept in v1 except mcp-remote HTTPS to `allowed_hosts`.
5. v1.1: all TCP egress via `HTTP(S)_PROXY` + nftables default-deny (`oss-research-docker-hardening.md` H13).

### 9.2 Gateway construction of `allowed_hosts`

```python
allowed_hosts = [urlparse(server.url).hostname]
if transport == "stdio" and mcp_remote_url_in_args:
    allowed_hosts.append(urlparse(mcp_remote_url).hostname)
# Future: merge org-level registry hosts from control DB
```

---

## 10. OAuth — external control plane only

| Role | Location | Allowed operations |
|------|----------|-------------------|
| OAuth **client** | gateway / control | PRM, DCR, PKCE, browser, token refresh |
| Token **storage** | control DB + per-org volume `/data/mcp-auth` | encrypted at rest |
| Data plane | sandbox-agent | `Authorization: Bearer` on upstream requests only |

**Prohibited in sandbox:** `oauth_client_role: "client"`, browser redirect, token endpoint calls, refresh tokens in env vars.

**`needs_reauth` flow:**

1. Agent sets `_meta.needs_reauth=true` or `error.code=-32001`.
2. Gateway calls `_notify_control_needs_reauth` (existing).
3. UI shows Re-authorize; human completes OAuth on gateway path.
4. Retry with fresh Bearer in `upstream.headers`.

---

## 11. Gateway migration checklist (Claude session)

> **Full step-by-step:** `docs/mcp/gateway-integration-checklist.md` (P4.11 deliverable).

**Cursor agent side: COMPLETE** (P4.9–P4.11). Single `POST /rpc` handles all transports.

Claude session must still implement broker + gateway wiring below before P4.13/P6.18.

- [ ] `broker_send_jsonrpc` → `broker_send_rpc` with full `SandboxRpcRequest`
- [ ] Remove direct `_adapter_forward` HTTP branch for streamable-http/sse (P4.11)
- [ ] Remove `mcp_ws_adapter` direct dial (P4.11)
- [ ] Map `_meta.needs_reauth` to control notification
- [ ] Build `upstream` block from server config + stored OAuth token
- [ ] Network assertion test: gateway makes zero upstream TCP to MCP URLs during harness (P4.13)

## 12. Cursor implementation checklist (P4.9–10)

- [ ] Extend `RpcRequest` model in `agent/main.py`
- [ ] Add `upstream_manager.py` (httpx pool + sse + ws)
- [ ] Enforce `allowed_hosts` before connect
- [ ] Broker route `POST /v1/sandbox/{org}/rpc`
- [ ] Deprecation shim `stdio/rpc` → `/rpc`

---

## 13. Hive-mind consensus

| Field | Value |
|-------|-------|
| Proposal ID | `proposal-1782979656279-rgcvqe` |
| Hive | `hive-1782976205971-u1gav1` |
| Type | `contract` — SANDBOX_TRANSPORT_CONTRACT v1.0.0-draft |
| Cursor vote | **YES** (`cursor-ralph-iter8`, 2026-07-02) |
| Claude Code session | **PENDING** — required for full bilateral ratification |
| Status | **PROVISIONALLY RATIFIED** — Cursor MAY implement agent + docs; gateway/broker MUST wait for Claude ACK |

To ratify fully, Claude session runs:

```bash
npx ruflo@latest hive-mind consensus -a vote \
  --proposal-id proposal-1782979656279-rgcvqe --vote yes --voter-id <claude-agent-id>
```

Update this section to **RATIFIED** when For ≥ 2 (Cursor + Claude) or hive queen merges.

---

## 14. Versioning

| Version | Change |
|---------|--------|
| 1.0.0-draft | Initial contract (P3.8) |
| 1.0.0 | Drop `-draft` after bilateral ratification |
| 1.1.0 | Add `/rpc/stream`, egress proxy requirement |

---

## References

- `docs/mcp/broker-sandbox-lifecycle.md`
- `docs/mcp/oss-research-oauth-sandbox-client.md`
- `docs/mcp/oss-research-remote-transport-proxies.md`
- `docs/mcp/oss-research-docker-hardening.md`
- `docs/mcp/request-path-map.md`
- MCP Streamable HTTP: https://modelcontextprotocol.io/specification/draft/basic/transports/streamable-http
