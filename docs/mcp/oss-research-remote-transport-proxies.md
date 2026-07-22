# OSS research — remote MCP transport proxies (P2 item #5)

> Deliverable for `scripts/ralph/mcp_progress.md` **P2 item #5** and Ralph scratchpad P2.5.
> Synthesizes MCP spec transports, `modelcontextprotocol/servers` reference patterns,
> community proxy tools, and implications for moving **all** transports into the per-org
> gVisor sandbox. Companion docs: `oss-research-stdio-patterns.md` (item #6),
> `oss-research-mcp-remote.md` (item #8), `oss-research-oauth-transport-ux.md` (item #10).

## 1. MCP transport landscape (spec, 2025-03-26+)

| Transport | Client↔server shape | URL required? | OAuth (MCP auth spec)? | Status |
|-----------|---------------------|---------------|------------------------|--------|
| **stdio** | subprocess, JSON-RPC on stdin/stdout | No | No — env/BYOK only | Core |
| **Streamable HTTP** | single endpoint `POST /mcp` (+ optional GET SSE stream per request) | Yes | Yes (RFC 9728 PRM, OAuth 2.1) | **Preferred remote** |
| **HTTP+SSE** (`sse`) | dual endpoints: `GET /sse` + `POST /messages?sessionId=` | Yes | Yes | Deprecated 2025-03-26, back-compat |
| **WebSocket** | community / SDK extensions; not in the two core spec transports | Yes (ws://) | varies | Used in practice via adapters |

Sources: [MCP Transports](https://modelcontextprotocol.io/specification/2025-03-26/basic/transports),
[Streamable HTTP](https://modelcontextprotocol.io/specification/draft/basic/transports/streamable-http).

**Key spec shift:** Streamable HTTP replaces legacy HTTP+SSE with one endpoint, optional
per-request SSE streaming, `Mcp-Session-Id` header for session continuity. Reverse proxies
and auth middleware work on ordinary HTTP — a major motivator for the change (Auth0, Simplescraper).

## 2. `modelcontextprotocol/servers` — what the reference repo teaches

The official repo is **reference implementations**, not production servers. All showcase
**stdio by default** via `npx -y @modelcontextprotocol/server-<name>`.

Remote-capable reference pattern (Everything server README):
```shell
npx @modelcontextprotocol/server-everything stdio          # default
npx @modelcontextprotocol/server-everything sse            # legacy HTTP+SSE
npx @modelcontextprotocol/server-everything streamableHttp # modern remote
```

Takeaways for ZeroShield:
- **Official remote servers are independent HTTP processes** — the SDK provides
  `StreamableHTTPServerTransport` / `SSEServerTransport`; clients connect over the network.
- **stdio and remote are alternate launch modes of the same server code**, not a proxy layer
  inside the server. Bridging stdio-only clients to remote servers is always an **external proxy**
  (mcp-remote, supergateway, mcp-proxy).
- Readiness for stdio = `initialize` JSON-RPC probe, not a stdout banner (`oss-research-stdio-patterns.md`).

## 3. How OSS proxies handle http / sse / ws

### 3.1 Taxonomy (proxy vs router vs gateway)

| Layer | Job | Examples |
|-------|-----|----------|
| **Transport proxy** | Convert stdio ↔ HTTP/SSE/WS/Streamable HTTP | mcp-remote, supergateway, mcp-proxy |
| **Router** | Tool-name → backend dispatch | custom registries |
| **Gateway** | Proxy + auth, RBAC, audit, rate limits | Kong AI MCP plugin, ZeroShield gateway |

(Framework: TrueFoundry MCP Gateway vs Proxy, ChatForest gateway patterns.)

### 3.2 `geelen/mcp-remote` — stdio ↔ remote HTTP/SSE + OAuth

**Direction:** stdio (local client) ↔ remote HTTPS MCP server.

- Bidirectional `mcpProxy`: `StdioServerTransport` ↔ `connectToRemoteServer` (HTTP or SSE).
- **Lazy OAuth 2.1 + PKCE** on 401; local callback server (port 3334); opens browser.
- **`--header "Authorization: Bearer …"`** bypasses OAuth entirely — ZeroShield uses this for headless sandbox.
- **`--transport`**: `http-first` (default), `sse-first`, `http-only`, `sse-only`.
- Token dir: `~/.mcp-auth` or **`MCP_REMOTE_CONFIG_DIR`** (per-org in our broker).

**Linear case:** remote `https://mcp.linear.app/sse` registered as stdio row with
`args=[mcp-remote, <url>]`, `auth_type=none`; gateway owns OAuth + Bearer injection.
See `oss-research-mcp-remote.md` for repo integration map.

### 3.3 `supercorp-ai/supergateway` — multi-direction transport bridge

**Directions (all supported):**
- stdio → SSE / WS / Streamable HTTP (expose local stdio server on network)
- SSE / Streamable HTTP → stdio (pull remote server to local stdio client)

Flags: `--outputTransport stdio|sse|ws|streamableHttp`, `--port`, `--streamableHttpPath /mcp`.

Use case: Claude Desktop (stdio-only) ↔ remote hosted MCP; debugging; Docker sidecars.
Does **not** implement OAuth — auth is upstream or via separate layer.

### 3.4 `sparfenyuk/mcp-proxy` — Streamable HTTP ↔ stdio (Python)

**Modes:**
1. **stdio → SSE/Streamable HTTP** — listen on `--port`, spawn local stdio server for each session.
2. **SSE/Streamable HTTP → stdio** — `mcp-proxy <remote-url>` with `--transport=sse|streamablehttp`.

Features: `--oauth` client support, CORS, stateless streamable HTTP, container images.
Closest Python analogue to supergateway; popular for Docker/K8s sidecars.

### 3.5 Reverse-proxy patterns for remote MCP

For **native** HTTP/SSE servers (no stdio bridge):
- **Streamable HTTP:** standard `POST /mcp` — normal reverse proxy + auth headers per request.
- **Legacy SSE:** requires `proxy_buffering off`, `Connection ''` (not WebSocket upgrade), long timeouts.
- nginx/Caddy/Traefik terminate TLS and forward `Authorization` — enterprise pattern.

ZeroShield **today** (`mcp_proxy.py:1196`): gateway POSTs JSON-RPC **directly** to upstream URL for
`streamable-http` / `sse` — no in-sandbox hop. Stdio/ws use `_adapter_forward` → sandbox or ws adapter.

## 4. Answer matrix — key P2.5 questions

### Q1: How do OSS proxies handle http/sse/ws MCP transports?

| Tool | stdio→remote | remote→stdio | HTTP (streamable) | SSE (legacy) | WebSocket | OAuth |
|------|-------------|--------------|-------------------|--------------|-----------|-------|
| mcp-remote | ✓ (primary) | — | ✓ (`http-first`) | ✓ (fallback) | — | ✓ lazy + `--header` bypass |
| supergateway | ✓ | ✓ | ✓ | ✓ | ✓ | — |
| mcp-proxy | ✓ | ✓ | ✓ (`streamablehttp`) | ✓ | — | ✓ optional |

None of these run **inside** a hardened multi-tenant sandbox by default — they are client-side or
sidecar bridges. Production multi-tenant isolation is an **outer** concern (our broker/gVisor).

### Q2: How does mcp-remote bridge stdio↔remote HTTP + OAuth?

1. MCP client spawns `npx mcp-remote <https-url>` as stdio subprocess.
2. Proxy speaks JSON-RPC on stdio to client; opens HTTP/SSE transport to remote URL.
3. On 401: OAuth discovery (PRM) → browser callback → tokens in `MCP_REMOTE_CONFIG_DIR`.
4. With pre-injected `--header Authorization: Bearer …` (ZeroShield gateway): step 3 skipped.

**Why Linear is stdio in our UI:** most MCP clients only speak stdio; mcp-remote is the
community-standard bridge until clients support remote OAuth natively.

### Q3: Implications for ALL transports in per-org gVisor sandbox

**Current ZeroShield architecture:**

| Transport | Today | Upstream dialer |
|-----------|-------|-----------------|
| stdio | mcp-broker sandbox → agent → stdio subprocess | In sandbox ✓ |
| websocket | `mcp_ws_adapter` in gateway | **Gateway direct** ✗ |
| streamable-http / sse | gateway HTTP client to `server.url` | **Gateway direct** ✗ |
| stdio+mcp-remote | sandbox runs mcp-remote → HTTPS upstream | Hybrid (wrapper in sandbox) ✓ egress |

**Target (P4 items #9–11):** gateway calls **only** `broker → sandbox-agent /rpc`; agent runs
transport-specific clients inside the container with **egress allowlist per registered upstream**.

| Transport | Sandbox agent strategy | OSS analogue |
|-----------|------------------------|--------------|
| stdio | **Keep** — spawn allowlisted command in container | official servers pattern |
| streamable-http / sse | **Add** — in-sandbox `httpx` client to registered URL only | inverse of mcp-proxy remote→stdio; same JSON-RPC over HTTP |
| websocket | **Add** — in-sandbox `websockets` client to registered URL | supergateway ws mode, but client inside sandbox |
| stdio+mcp-remote | **Keep short-term**; optional **deprecate** once native HTTP oauth row works through sandbox HTTP client | mcp-remote today |

**Design principles from OSS research:**
1. **Do not run mcp-remote for native HTTP transport rows** — duplicate stack; use direct HTTP client in agent with gateway-stored OAuth token injection (same security, simpler cold-start).
2. **Keep mcp-remote for stdio-wrapped-remote (Linear)** until Streamable HTTP + oauth through sandbox is proven — or migrate Linear to `transport=streamable-http` + sandbox HTTP proxy.
3. **OAuth tokens never in gateway egress for HTTP path** — inject in sandbox from per-org volume (`MCP_REMOTE_CONFIG_DIR` pattern already proven).
4. **Egress allowlist** per server URL host — mcp-remote makes arbitrary HTTPS calls today (P7 gap); native agent proxy can enforce.
5. **Streamable HTTP preferred over sse** for new registrations — aligns with spec; simpler proxy semantics in sandbox.

**Contract seam (P3 item #8):** sandbox-agent endpoints for HTTP/SSE/WS must mirror what
`mcp-remote` / SDK transports do: `initialize`, `tools/list`, `tools/call`, session headers,
SSE stream lifecycle — but over a single broker `POST /v1/sandbox/{org}/stdio/rpc`-style RPC
envelope extended per transport.

## 5. Linear case — decision summary

| Approach | Pros | Cons |
|----------|------|------|
| **A. stdio + mcp-remote (current)** | Works with stdio-only mental model; OSS-proven OAuth bridge | Extra npm dep; double proxy; cold-start B3; egress not allowlisted |
| **B. streamable-http + sandbox HTTP client (target)** | One transport stack in agent; direct OAuth token injection; clearer B2 pending state | Requires P4 agent work + contract; gateway must not dial upstream |
| **C. streamable-http + gateway direct (today for HTTP rows)** | Simple | Violates "nothing in main backend" / no direct upstream goal |

**Recommendation:** implement **B** via P4; retain **A** for compatibility until P4 verification passes (P6 item #18).

## Sources

- [modelcontextprotocol/servers README](https://github.com/modelcontextprotocol/servers/blob/main/README.md)
- [Everything server — streamableHttp/sse modes](https://github.com/modelcontextprotocol/servers/blob/main/src/everything/README.md)
- [geelen/mcp-remote](https://github.com/geelen/mcp-remote)
- [supercorp-ai/supergateway](https://github.com/supercorp-ai/supergateway)
- [sparfenyuk/mcp-proxy](https://github.com/sparfenyuk/mcp-proxy)
- [MCP Streamable HTTP spec](https://modelcontextprotocol.io/specification/draft/basic/transports/streamable-http)
- Internal: `docs/mcp/request-path-map.md`, `oss-research-mcp-remote.md`, `oss-research-stdio-patterns.md`
