# Gateway + broker integration checklist (P4.11 — Claude-owned seam)

> **Cursor deliverable for P4.11.** The sandbox-agent side is **complete**: a single
> `POST /rpc` accepts `transport ∈ {stdio, streamable-http, sse, websocket}` per
> `SANDBOX_TRANSPORT_CONTRACT.md`. This document lists the **exact changes** the Claude
> Code session must make in gateway + broker so the gateway **never dials upstream MCP
> URLs directly** and routes **all** transports through the per-org sandbox.
>
> **Do not edit these files from the Cursor session** — ownership is Claude Code per
> `docs/mcp/PARALLEL_CLAIMS.md`. Integration verification is **P6.18**.

---

## Cursor-side status (done)

| Item | Status | Evidence |
|------|--------|----------|
| Agent `POST /rpc` unified | ✅ | `sandbox-image/agent/main.py` |
| stdio via `stdio_manager` | ✅ | default `transport=stdio`, legacy flat fields |
| streamable-http / sse | ✅ | `upstream_manager.py` |
| websocket | ✅ | `ws_manager.py` |
| Contract tests | ✅ | `agent/tests/test_rpc_unified.py` + `test_upstream_proxy.py` |
| Gateway wiring | ❌ Claude | this checklist |
| Broker unified route | ❌ Claude | this checklist |
| iter25 recheck (P4.13/P6.18) | ❌ **BLOCKED** | `mcp-parallel/findings/p4-13/RECHECK_ITER25.md` |
| Cursor verify harness | ✅ prep | `scripts/mcp_sandbox_transport_verify.py` |

### iter25 recheck (2026-07-02 — cursor-ralph-iter25)

| Check | Result |
|-------|--------|
| `broker_send_rpc` in `mcp_sandbox_client.py` | **MISSING** (`broker_send_jsonrpc` only) |
| `POST /v1/sandbox/{org}/rpc` live | **404** |
| `POST /v1/sandbox/{org}/stdio/rpc` live | **401** (exists) |
| `mcp_proxy.py` direct upstream httpx | **PRESENT** |

**When Claude lands §1–3**, run:

```bash
# 1. Fill mcp-parallel/findings/p4-13/TRANSPORT_MANIFEST.example.json (one server per transport)
TRANSPORT_MANIFEST=... ROUNDS=3 python scripts/mcp_sandbox_transport_verify.py
# 2. Network assertion — tcpdump on gateway during harness (see script output / §5.2 below)
# 3. Recursive gate
python scripts/mcp_p10_recursive_gate.py
```

---

## 1. Broker — `services/mcp-broker/src/sandbox/routes.py`

### 1.1 Add unified route

```python
@router.post("/{org_slug}/rpc")
async def sandbox_rpc(org_slug: str, body: SandboxRpcRequest) -> dict[str, Any]:
    # Same body as agent SandboxRpcRequest (transport, stdio, upstream, method, params, jsonrpc_id, timeouts)
    # Forward verbatim to agent POST {agent_url}/rpc via _post_agent_rpc
```

### 1.2 Extend request model

Replace or extend `StdioRpcRequest` → `SandboxRpcRequest` matching agent fields:

- `transport: str` (default `"stdio"` for back-compat)
- `stdio: {command, args, env} | null`
- `upstream: {url, allowed_hosts, headers, oauth_client_role} | null`
- `method`, `params`, `jsonrpc_id`, `timeouts`

### 1.3 Deprecate alias (keep temporarily)

```python
@router.post("/{org_slug}/stdio/rpc")
async def stdio_rpc(...):
    body.transport = body.transport or "stdio"
    return await sandbox_rpc(org_slug, body)
```

### 1.4 Cold-start / errors

Keep existing `_post_agent_rpc` retry logic. Map agent `_meta.needs_reauth` → broker response (pass through JSON).

---

## 2. Gateway — `gateway/ai_mesh_gateway/mcp_sandbox_client.py`

### 2.1 Rename / extend `broker_send_jsonrpc` → `broker_send_rpc`

**Current** (`:136-166`): stdio-only payload, URL `…/stdio/rpc`.

**Target:**

```python
async def broker_send_rpc(
    org_slug: str,
    server_config: dict[str, Any],
    method: str,
    params: ...,
    timeout: float | None = None,
    *,
    msg_id: int | str | None = None,
    oauth_token: str | None = None,  # gateway-injected upstream Bearer
) -> dict[str, Any]:
    transport = server_config.get("transport", "streamable-http")
    payload = {
        "server_slug": server_config["server_slug"],
        "transport": transport,
        "method": method,
        "params": params,
        "jsonrpc_id": ...,
        "timeouts": _timeouts_payload(timeout),
    }
    if transport == "stdio":
        payload["stdio"] = {
            "command": server_config["command"],
            "args": list(server_config.get("args") or []),
            "env": dict(server_config.get("env_vars") or {}),
        }
        # mcp-remote OAuth: _maybe_inject_oauth_header on args BEFORE building payload
    else:
        payload["upstream"] = _build_upstream_block(server_config, oauth_token)
    url = f"{_BROKER_URL}/v1/sandbox/{org_slug}/rpc"
```

### 2.2 `_build_upstream_block` (new helper)

```python
def _build_upstream_block(server_config: dict, oauth_token: str | None) -> dict:
    url = server_config["url"]
    host = urlparse(url).hostname
    allowed_hosts = [host]
    # stdio+mcp-remote: parse wrapped URL from args, append host
    headers = {}
    if oauth_token:
        headers["Authorization"] = f"Bearer {oauth_token}"
    return {
        "url": url,
        "allowed_hosts": allowed_hosts,
        "headers": headers,
        "oauth_client_role": "forbidden_in_sandbox",
    }
```

### 2.3 Strip `_meta` before returning to MCP client

```python
resp = response.json()
resp.pop("_meta", None)
if resp.get("_meta", {}).get("needs_reauth"):  # or error.code -32001
    await _notify_control_needs_reauth(...)
return resp
```

---

## 3. Gateway — `gateway/ai_mesh_gateway/mcp_proxy.py`

### 3.1 Unify transport dispatch (`org_mcp_jsonrpc` ~`:1896`)

**Current:**

```python
is_adapter_transport = transport in ("stdio", "websocket")
# stdio/ws → _adapter_forward
# streamable-http/sse → direct httpx to server.url (:1196, :2324)
```

**Target:**

```python
ALL_SANDBOX_TRANSPORTS = ("stdio", "streamable-http", "sse", "websocket")

async def _sandbox_forward(...):
    from mcp_sandbox_client import broker_send_rpc
    token = await _get_upstream_oauth_token(org_slug, server_slug, server_config)
    return await broker_send_rpc(..., oauth_token=token)

# tools/list, tools/call, ping: ALL use _sandbox_forward when MCP_STDIO_IN_PROCESS=false
# Remove direct httpx POST to server.url for streamable-http/sse
```

### 3.2 Delete / bypass direct upstream paths

| Location | Current behavior | Action |
|----------|------------------|--------|
| `internal_discover_tools` ~`:1153` | direct HTTP to upstream | → `broker_send_rpc` |
| `internal_tools_call` ~`:1196` | direct HTTP | → `broker_send_rpc` |
| `org_mcp_jsonrpc` ~`:2324` | backend/control or direct HTTP | → `broker_send_rpc` for MCP JSON-RPC |
| `mcp_ws_adapter` | gateway WS client | **Remove** from hot path; ws via sandbox |

### 3.3 `_adapter_forward` (~`:1775`)

Refactor to thin wrapper:

```python
async def _adapter_forward(...):
    if transport == "stdio":
        await _maybe_inject_oauth_header(args, ...)
    return await broker_send_rpc(org_slug, server_config, method, params, ...)
```

Remove `mcp_stdio_adapter.send_jsonrpc` in-process branch when `MCP_STDIO_IN_PROCESS=false` (already default in prod compose).

### 3.4 `is_adapter_transport` flag

Replace with:

```python
use_sandbox = os.environ.get("MCP_STDIO_IN_PROCESS", "false").lower() != "true"
# When use_sandbox: ALL transports → broker_send_rpc
# When in-process dev only: keep legacy stdio in-process (document as dev-only)
```

---

## 4. OAuth token plumbing

| Transport | Token source | Injection point |
|-----------|--------------|-----------------|
| stdio + mcp-remote | gateway `_token_save` / `_maybe_inject_oauth_header` | `--header Bearer` in stdio args |
| streamable-http / sse / ws | gateway `_get_stored_token(org, server_url)` | `upstream.headers.Authorization` |

Sandbox **never** runs OAuth client (P2.7 / contract §10).

---

## 5. Verification gates (P4.13 / P6.18)

### 5.1 Unit / integration

- [ ] Gateway tests: mock broker; assert **no** `httpx.post(server_config["url"])` for MCP methods
- [ ] Broker tests: `POST /v1/sandbox/{org}/rpc` forwards full `SandboxRpcRequest`
- [ ] Agent tests: already green (`pytest sandbox-image/agent/tests -q`)

### 5.2 Network assertion (harness)

With tcpdump or test hook on gateway container during 15-MCP harness **or**
`scripts/mcp_sandbox_transport_verify.py`:

- **ALLOW:** gateway → mcp-broker:8311, broker → sandbox-agent:9320
- **DENY:** gateway → external MCP host:443 (e.g. `mcp.linear.app`, registered server URLs)

**tcpdump (preferred):**

```bash
GW=$(docker ps --filter name=gateway -q | head -1)
docker exec $GW tcpdump -i any -n 'tcp port 443' -w /tmp/gw-egress.pcap &
ROUNDS=3 python scripts/mcp_sandbox_transport_verify.py
docker exec $GW tcpdump -r /tmp/gw-egress.pcap -n | grep -v ':8311\|:8100\|:6379\|:5432'
# Expect ZERO lines to registered upstream MCP hosts.
```

**ss snapshot (weaker):** `docker exec $GW ss -tnp | grep ':443'` before/during harness — new
ESTAB from gateway to external :443 (excluding broker) = FAIL.

**Future test hook:** `MCP_EGRESS_AUDIT=1` on gateway — increment metric / fail closed if any
outbound httpx targets a registered MCP `server_config["url"]`.

### 5.3 Per-transport smoke

| Transport | Register | tools/list | tools/call |
|-----------|----------|------------|------------|
| stdio (everything) | ✓ | via sandbox | via sandbox |
| streamable-http | ✓ | via sandbox | via sandbox |
| sse (legacy) | ✓ | via sandbox | via sandbox |
| websocket | ✓ | via sandbox | via sandbox |

---

## 6. Environment flags

| Var | Prod value | Effect |
|-----|------------|--------|
| `MCP_STDIO_IN_PROCESS` | `false` | Force broker path |
| `MCP_BROKER_URL` | `http://mcp-broker:8311` | Broker base |
| `MCP_BROKER_INTERNAL_KEY` | set | Auth header |

---

## 7. Rollout order (recommended)

1. Broker: add `POST /{org}/rpc` + `SandboxRpcRequest` (stdio alias unchanged)
2. Gateway: `broker_send_rpc` + `_build_upstream_block`
3. Gateway: route **stdio** only through new payload shape (no behavior change)
4. Gateway: switch streamable-http/sse off direct httpx
5. Gateway: switch websocket off `mcp_ws_adapter`
6. Harness + network assertion (P4.13)
7. Remove dead code (`mcp_ws_adapter` hot path, direct upstream httpx)

---

## References

- `docs/mcp/SANDBOX_TRANSPORT_CONTRACT.md`
- `docs/mcp/request-path-map.md` (update after Claude lands changes)
- `gateway/ai_mesh_gateway/mcp_sandbox_client.py:136-166` (current stdio-only)
- `gateway/ai_mesh_gateway/mcp_proxy.py:1897-1933` (current transport fork)
- `services/mcp-broker/src/sandbox/routes.py:160` (current stdio/rpc)
