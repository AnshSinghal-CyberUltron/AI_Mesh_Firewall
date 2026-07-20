# P4.13/P6.18 §2 done + §3 blocker (stale sandbox image)

## §2 — gateway `broker_send_rpc` (DONE, tested; commit 1aba6304)
`gateway/ai_mesh_gateway/mcp_sandbox_client.py`:
- Added `broker_send_rpc(org, server_config, method, params, *, msg_id, oauth_token)`
  → routes to the unified `…/sandbox/{org}/rpc`.
- stdio → legacy flat `command/args/env` (agent merges to `stdio`).
- remote (streamable-http/sse/websocket) → builds `upstream` block:
  `{url, allowed_hosts (upstream host + declared extras, exact-match), headers
  (injected `Authorization: Bearer <oauth_token>` when the gateway holds one),
  oauth_client_role: "forbidden_in_sandbox"}`. Requires `url` (raises otherwise).
- `broker_send_jsonrpc` kept byte-identical (proven stdio path). 11/11 tests.

## §3 — BLOCKED on stale sandbox image (KEY FINDING)

Live probe of the agent HTTP path (`POST /v1/sandbox/org-a/rpc`, transport=
streamable-http) returned a **422** from the in-container agent:
`{"loc":["body","command"],"msg":"Input should be a valid string","input":null}`.

Root: the **deployed `ai-mesh/mcp-sandbox:latest` image is STALE** — it predates
Cursor's unified-`/rpc` agent:

| | value |
|---|---|
| image `Created` | 2026-07-02T07:10:20Z |
| agent source mtime | 2026-07-02T08:15:56Z (≈1h later) |
| deployed agent `SandboxRpcRequest.command` | **required `str`** (old contract) |
| source agent `SandboxRpcRequest.command` | `str \| None = None` (unified contract) |

So the running sandboxes cannot accept the unified/remote payload — HTTP transports
can't route through the sandbox until the image is rebuilt.

### Unblock path (in progress)
1. Rebuild: `docker build -t ai-mesh/mcp-sandbox:latest -f services/mcp-broker/sandbox-image/Dockerfile .`
   (Dockerfile `COPY ... agent/` + `uv sync` → picks up the unified agent.)
2. Verify the new image's agent contract in ISOLATION (docker run + probe `/rpc`
   streamable-http + bogus-transport sentinel + egress allowlist) BEFORE rollout.
3. Recreate ONE org's sandbox on the new image; regression-test the **stdio** path
   (must stay green — backward-compatible via legacy flat fields) + the HTTP path.
4. Roll out to all orgs; re-warm the 15-MCP fleet.

## §3 proxy refactor (after image) + §4 network assertion — NOT STARTED
`mcp_proxy.py` currently routes only stdio+websocket to the sandbox
(`is_adapter_transport`, lines 1333/1531/2042); streamable-http/sse dial upstream
directly (SSRF guard + httpx). §3 replaces those direct dials with `broker_send_rpc`
(passing the gateway-held `oauth_token`). §4 asserts the gateway makes NO direct
external MCP :443 connection (tcpdump/socket capture during the 15-MCP harness).

## §3 sandbox image rebuild — DONE + deployed + verified in isolation

Rebuilt `ai-mesh/mcp-sandbox` from `services/mcp-broker/sandbox-image/Dockerfile`
(tag `unified-test` → verified → retagged `latest`; old image saved as
`ai-mesh/mcp-sandbox:pre-unified-rollback` for rollback). Verified the NEW image's
agent BEFORE and AFTER rollout:

- contract: `SandboxRpcRequest.command` optional + `transport`/`upstream` present (unified).
- standalone `/rpc` probes: bogus transport → enum-validation reject; streamable-http
  to a reachable-but-dead host → **agent DIALS** (`-32000 upstream HTTP error`, full
  `_meta`); host NOT in `allowed_hosts` → **`-32002 egress denied`** (per-org egress
  allowlist enforced INSIDE the sandbox).
- rollout: retag latest → `docker rm -f` each org sandbox → recreate on next use.
  **stdio regression GREEN** (gateway echo `Echo: new-image-stdio-ok`, first try —
  backward-compatible via legacy flat fields). HTTP path through org-a's new sandbox
  via broker `/rpc`: dials (`-32000`) + egress-denies (`-32002`) — was **422** on the
  old image.

## Status summary
- §1 broker unified route: **DONE + deployed live** (404→401) — commit aa30d807
- §2 gateway `broker_send_rpc`: **DONE + tested** (11/11) — commit 1aba6304
- §3 sandbox image (unified agent): **DONE + deployed + verified** (dials + egress-denies live)
- §3 proxy refactor (`mcp_proxy` route HTTP/SSE via `broker_send_rpc`): **NEXT**
- §4 network assertion (gateway makes NO direct external MCP :443): pending §3
- REGRESSION GUARD: re-run the P9 gate on the new image before/after the proxy refactor.
