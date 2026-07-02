# Gateway MCP Request Path — file:line map

> Deliverable for `scripts/ralph/mcp_progress.md` **P0 item #1**: map the gateway MCP
> request path `proxy → oauth → adapter → sandbox_client → broker` with precise
> `file:line` anchors. Every anchor below was read from the tree and spot-verified
> (see "Verification" at bottom). Line numbers are as of commit `60f28c0c`.
>
> Scope = the **gateway-side** request path only. Broker sandbox *lifecycle*
> (create→install→start→health→reap) is item #2; control-plane connector is item #3;
> frontend `MCPConnectorPanel` is item #4. This doc feeds the item-#5 consolidation
> into `docs/mcp/ARCHITECTURE_AND_THREATS.md`.

## 0. Mount points (`main.py`)

| Router | Prefix | Mounted at |
|---|---|---|
| `mcp_proxy.router` | `/v1/mcp` | `main.py:4023` |
| `mcp_proxy.org_gateway_router` | `/gateway` | `main.py:4024` |
| `mcp_oauth.router` | `/.well-known/oauth-*`, `/oauth/*` | `main.py:4033` |
| `mcp_oauth_proxy.router` | `/gateway/{org}/mcp/{server}/oauth/*` | `main.py:4041` |
| stdio + ws adapter reapers | (lifecycle) | `main.py:4061` start; `:4113-4121` shutdown |

`AuthContext` (org_slug, user_id, prefix, roles, project_id, key_hash,
`mcp_allowed_tools`, `mcp_max_tool_calls`) is populated UPSTREAM by
`middleware.py:419` into `request.state.auth_context` and read by
`mcp_proxy._get_auth_context` (`mcp_proxy.py:1557`).

## 1. The spine — `org_mcp_jsonrpc` (mcp_proxy.py:1848)

`POST /gateway/{org_slug}/mcp/{server_slug}` — the org-scoped Streamable-HTTP
JSON-RPC handler. Ordered lifecycle of a live `tools/call`:

| # | Step | file:line | Notes |
|---|---|---|---|
| 1 | Org-scope + auth | `mcp_proxy.py:1855` → `_validate_org_scope:1562` | 401 no auth (`:1565`); **403 org_scope_violation** if `auth.org_slug != url org` (`:1570`) — the per-tenant isolation gate |
| 2 | Derive actor | `:1861` (`_get_auth_context:1557`) | `mcp_actor{user_id,agent_id=prefix,roles}` built `:1864-1868` for actor-scoped policy |
| 3 | Parse body | `:1870` | JSON parse error → `-32700` (`:1873`); extract method/params/id/jsonrpc `:1882-1885` |
| 4 | **Per-org rate limit** | `:1888` → `_enforce_mcp_org_rate_limits:1629` | TPM (`main._enforce_org_tpm_rate_limit:1649`) + burst/RPM (`:1659`); 429 → JSON-RPC `-32000` via `_rate_limit_response_to_jsonrpc:1586` |
| 5 | **Transport decision** | `:1897` (`_get_server_config:214`, 120s TTL) | `transport` default `"streamable-http"` (`:245`); `is_adapter_transport = transport in ("stdio","websocket")` (`:1899`) |
| 6 | `initialize` handled LOCALLY | `:1902-1919` | Returns protocolVersion+capabilities+serverInfo **without** touching backend/OAuth. **B2**: succeeds even for an un-authorized HTTP-oauth server. `notifications/initialized` ack `:1922`; `ping` `:2565` |
| 7 | `tools/list` | `:1928` | adapter path `:1932-1946` (`_adapter_forward:1933` → `_filter_tools_by_enabled:1944`); HTTP path GETs backend `/api/mcp-connector/tools/` (`:1950`), filters disabled (`:1977`), **returns `{tools:[]}` even when empty (`:1979-1986`) — B2 ambiguous 0-tools payload** |
| 8 | tools/call: key allowlist + call cap | `:2007` | `_tool_allowed_by_key:770` → block `tool_not_allowed_for_key`; `mcp_max_tool_calls` cap via `_incr_tool_call_count:799` + `_tool_call_cap_exceeded:788` |
| 9 | tools/call: per-tool enable/disable | `:2085` (`_is_tool_disabled:418`) | Enforced for **ALL** transports before forwarding — closes the stdio/ws bypass of backend `MCPToolCallView` |
| 10 | **Inbound arg scan + credential hard-block** | `:2121` (`_mcp_security_scan:834`) | E12 force-block if `scan_action!='monitor'` and finding is a credential (`:2136-2143`); block → audit `pii_blocked_inbound` + return (`:2147-2182`); redaction swapped into params `:2183-2187` |
| 11a | **DISPATCH — adapter (stdio/ws)** | `:2189` → `_adapter_forward:1775` @ `:2203` | see §2 |
| 12a | Outbound result scan + redaction floor | `:2218-2294` | two-tier scan; block → `[BLOCKED]`; redaction floor re-scan `enforcement_override='redact'` |
| 11b | **DISPATCH — backend HTTP (streamable-http)** | `:2324` | `POST` backend `/api/mcp-connector/tools/call/` (`:2326`) with `_backend_proxy_headers:1672` + `X-Request-Id`; backend `"blocked"` → audit+return (`:2344-2379`) |
| 12b | Outbound scan + redaction floor (HTTP) | `:2385`, floor `:2449-2469` | |
| 13 | **Audit + response** | `:2482` (`_record_gateway_event:425`) | exactly one event per call; content normalized `:2495-2502`; adapter path audits at `:2301`. Backend timeout → `-32000` (`:2545`); RequestError → "Backend unreachable" (`:2554`); unknown method → `-32601` (`:2571`) |

Sibling entry points (same auth+scan discipline): `org_mcp_tool_call` (bare REST,
`:2589`), `org_mcp_tools_list` (`:2751`), `org_mcp_server_health` (`:2787`).
Internal (backend-originated) routes gated by `_valid_internal_key:81` (constant-time):
`internal_discover_tools:1148`, `internal_tools_call:1296`. Transparent external
proxy `ext_mcp_proxy:926` is egress-allowlisted by `_ALLOWED_MCP_DOMAINS:97` (`:939`).

## 2. Transport dispatch — `_adapter_forward` (mcp_proxy.py:1775)

```
_adapter_forward(transport, server_config, org, server, body, jsonrpc, msg_id)
  ├─ transport == "stdio"      (:1788)
  │    ├─ _maybe_inject_oauth_header(args, org, server)   (:1793 → def :1732)   [OAuth §4]
  │    └─ mcp_stdio_adapter.send_jsonrpc(...)             (:1789 import, :1798 call)
  ├─ transport == "websocket"  (:1809)
  │    └─ mcp_ws_adapter.send_jsonrpc(url=server_config['url'], ...) (:1811, url @ :1814)
  ├─ else                      (:1820) → JSON-RPC -32000 "Unsupported adapter transport"
  └─ except Exception          (:1832) → JSON-RPC -32000 "Adapter error: {exc}"   ← B3 surfacing point
```

`mcp_stdio_adapter.send_jsonrpc` (`mcp_stdio_adapter.py:666`) picks broker-vs-in-process
via `_stdio_in_process()` (`:678`; default `"true"` dev, compose/prod sets
`MCP_STDIO_IN_PROCESS=false`):
- **broker path** `_send_jsonrpc_broker:591` → `from ...mcp_sandbox_client import broker_send_jsonrpc` (`:602`) → `broker_send_jsonrpc(effective_org, cfg, ...)` (`:607-613`)
- **in-process path** `_send_jsonrpc_in_process:628` → `_ensure_process:332` → `_ensure_initialized:548`

Adapter security checkpoints (defense-in-depth for the "no unknown npm on host" invariant):
- command allowlist / no path / no shell: `mcp_stdio_adapter.py:342` (reject path-qualified), `:350` (`_ALLOWED_COMMANDS` npx/node/python/…), subprocess `shell=False` (`:407`)
- on-demand package allowlist / pinning: `:362-366` (`_PACKAGE_ALLOWLIST`), `:367-371` (`_REQUIRE_PINNED_PACKAGES`)
- per-org + global process caps: `_MAX_PROCESSES_PER_ORG` (`:395-401`), `_MAX_PROCESSES` eviction (`:389-392`), `_MAX_CONCURRENT_INITS` semaphore (`:548`)
- sandboxed child env: `_build_child_env` (`:403`) allowlists host vars + pins per-org `MCP_REMOTE_CONFIG_DIR` so OAuth tokens can't leak across orgs
- secret-free error surfacing: stderr tail logged server-side only; client/DB msg carries just exit code + generic hint (`:287-314`)
- WS SSRF guard: `mcp_ws_adapter.py:124` `is_safe_outbound_url` (schemes ws/wss/http/https), reaper `:299/:313`

## 3. Sandbox client → broker handoff (mcp_sandbox_client.py + broker routes.py)

`broker_send_jsonrpc` (`mcp_sandbox_client.py:136`) builds
`{server_slug,command,args,env,method,params,jsonrpc_id,timeouts}` (`:150-159`) and
`POST`s to `{MCP_BROKER_URL}/v1/sandbox/{org}/stdio/rpc` (`:161`) via
`_request_with_503_retry` (`:86`), auth header `X-MCP-Broker-Key` (`:39`, key required `:32-36`).

Broker side (`services/mcp-broker/src/sandbox/routes.py`), all routes behind
`Depends(require_broker_key)` (`:149`; impl `auth.py:17`, fail-closed 401 `:22-23`):
- `stdio_rpc` (`:161`): docker gate `cached_docker_ok()` → 503 "Docker unavailable" (`:162`); `_resolve_running_sandbox` (`:93`, lazy `docker_manager.ensure` @ `:103`); non-running → 503 "Sandbox not running" (`:165`); `_post_agent_rpc` (`:106`) proxies to in-container agent `/rpc` (`:126`) with bounded connection-error retry (`_AGENT_READY_RETRIES=8`, `:123-140`); failures collapse to **502** (`:184-198`)
- `ensure_sandbox` (`:153`): docker gate + `_check_org_quota` (`:66`, 429 at `MCP_SANDBOX_MAX_ORGS` default 50) + `docker_manager.ensure`; returns when container `status=='running'` — **not** when agent socket bound
- `sandbox_status` (`:202`): GETs agent `/health` (off hot path)

Return mapping — `_raise_for_broker_error:68` → `_safe_broker_error:56`:
`502 → "MCP sandbox is temporarily unavailable"` (`:58`), `503 → "starting; retry shortly"` (`:60`, retried),
`429 → capacity` (`:62`), `401 → auth failed` (`:64`).

## 4. OAuth stage (mcp_oauth.py + mcp_oauth_proxy.py + injection in mcp_proxy.py)

Two distinct OAuth roles:
- **Gateway self-AS** (`mcp_oauth.py`) — the gateway is an OAuth 2.1 AS for MCP clients (VS Code): RFC 9728 protected-resource (`:99`), RFC 8414 AS metadata (`:140`), RFC 7591 dyn-register (`:167`), authorize page/submit (`:206`/`:382`), token (`:464`; the returned `access_token` **IS** the raw gateway API key `:542-544` so AuthMiddleware validates it directly). PKCE S256 verified `:527`; redirect_uri allowlist `:80`.
- **Per-org upstream OAuth proxy** (`mcp_oauth_proxy.py`) — obtains/stores/refreshes tokens for external MCP servers: `oauth/start` (`:427`, `_require_org_scope:399`, **requires non-empty `server_url` `:444`**, SSRF-guard `:455`, discovery `_discover_oauth_metadata:283`), `oauth/callback` (`:555`, the ONLY unauthenticated route, validated by signed flow state; persists token `_token_save:137`, writes mcp-remote token files `_write_mcp_remote_tokens:358`, kills stdio proc `:673`), `oauth/status` (`:709`, returns `{authorized,expired,refreshable}`), `get_stored_token:236`, `has_stored_token:259`.

**Injection into outbound stdio (mcp-remote):** `_maybe_inject_oauth_header`
(`mcp_proxy.py:1732`) scans args for `mcp-remote` + extracts the wrapped URL (`:1735-1744`);
if a stored token exists, appends `--header Authorization: Bearer <token>` (`:1759`);
if a token *record* exists but is unusable, `_notify_control_needs_reauth:1698`. If no
URL is extractable it is a **silent no-op**.

## 5. Bug-site relevance found on the request path

- **B1 (oauth+stdio / 2 buttons / "Server has no URL")** — the gateway data-plane does
  **not** block oauth+stdio and **actively supports** it via `mcp-remote`
  (`_maybe_inject_oauth_header:1732`). `oauth/start` only requires `server_url` be
  non-empty + SSRF-safe (`mcp_oauth_proxy.py:444-455`); **no transport-type check** exists.
  ⚠️ **CRITICAL NUANCE for the B1 fix (item #13):** a `stdio` server *can* legitimately
  have a URL — `mcp-remote` is a stdio process wrapping a *remote HTTP* MCP URL and does
  OAuth. So "block oauth+stdio" must mean block oauth+**pure-local-stdio (no URL)**, NOT
  oauth+mcp-remote-stdio. The string `"Server has no URL"` is **absent** from every
  backend file (grep-confirmed) → it originates in the frontend. B1 is a frontend +
  control-plane validation concern, not a data-plane one.
- **B2 (0-tools card before oauth)** — the data source of the ambiguous payload is the
  spine's `tools/list` returning `{tools:[]}` with no oauth-needed signal
  (`mcp_proxy.py:1979-1986`) and `initialize` always succeeding (`:1902`). The ground-truth
  signal to distinguish "never authed" from "authed but empty" is
  `mcp_oauth_proxy.oauth_status:709` / `has_stored_token:259`. Fix (item #15) must make
  the frontend/control consult that signal.
- **B3 ("MCP sandbox is temporarily unavailable")** — exact string originates at
  `mcp_sandbox_client.py:58` (broker 502) and `:100` (broker unreachable after
  `_RETRY_MAX=5`). Root causes: (a) `broker_send_jsonrpc:136` never calls `ensure_sandbox`
  first — **no eager provisioning**; (b) `_request_with_503_retry:104` retries **only 503**,
  a cold-start **502 is not retried**; (c) broker readiness == container `"running"`, not
  agent-socket-bound (`docker_manager.ensure`), and `_post_agent_rpc:106` is the *only*
  readiness mitigation (bounded 8 retries); (d) broker **over-broadens** every agent
  HTTP ≥400/≥500 into 502 (`routes.py:189-198`), conflating real tool errors with
  unavailability. Surfaces to the client via `_adapter_forward`'s broad except
  (`mcp_proxy.py:1832` → "Adapter error: …"). Fix = items #19-21.
- **B4 (modal focus loss)** — pure frontend; **no** relevance anywhere on this backend path.

## Verification

Anchors spot-verified against the working tree (`awk`/`grep -n`) on the following
representative sample — all matched exactly:
`mcp_proxy.py`: 1732, 1775, 1793, 1832, 1848, 1898, 1899, 1902, 1979, 2189, 2203;
`mcp_stdio_adapter.py`: 342, 350, 395, 602, 666, 678;
`mcp_sandbox_client.py`: 56, 58, 100, 104, 136, 163 (whole file read);
`services/mcp-broker/src/sandbox/routes.py`: 66, 93, 106, 149, 161, 164, 184;
`mcp_oauth_proxy.py`: 427, 444, 455, 555, 709; `mcp_oauth.py`: 99, 140, 464;
`main.py`: 4024. (One immaterial imprecision: `mcp_oauth_proxy.py:455` is `_safe_url(server_url)`,
the SSRF-guard wrapper, not `is_safe_outbound_url` directly.)
