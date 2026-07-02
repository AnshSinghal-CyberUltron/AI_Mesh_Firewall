# P4.13/P6.18 §1 — broker unified /rpc route (DONE, deployed live)

The sandbox-agent already exposes a transport-agnostic `POST /rpc`
(`SandboxRpcRequest`: transport ∈ {stdio, streamable-http, sse, websocket} +
nested `stdio`/`upstream` blocks). The broker only had `/{org}/stdio/rpc`, so the
gateway had no unified path to route HTTP/SSE/WS through the sandbox.

## Change (`services/mcp-broker/src/sandbox/routes.py`)
- `StdioRpcRequest` → `SandboxRpcRequest` matching the agent contract: added
  `transport`, nested `stdio`/`upstream`; KEPT legacy flat `command`/`args`/`env`
  (back-compat with the pre-contract gateway payload). `StdioRpcRequest` kept as an
  alias.
- Refactored the forward logic into `_forward_sandbox_rpc(org, body)` (resolve
  running sandbox → POST agent `/rpc` with cold-start retry → map 5xx/4xx→502).
- Added `POST /{org}/rpc` (unified) → `_forward_sandbox_rpc`.
- Kept `POST /{org}/stdio/rpc` as a DEPRECATED alias (forces `transport=stdio`).

## Verification
- Broker suite: **97 passed** (+2: `test_unified_rpc_route_forwards_remote_transport`
  asserts a streamable-http + upstream body forwards verbatim to the agent;
  `test_stdio_rpc_alias_still_forwards` asserts the alias defaults transport=stdio).
- LIVE probe (blocker acceptance): `POST /v1/sandbox/{org}/rpc` **404 → 401**
  (route now exists, auth-gated) after `docker cp` + `docker restart ai_mesh_mcp_broker`.
- Regression: stdio path still works end-to-end (gateway echo `Echo: post-deploy-stdio-ok`).

## Next (Claude-owned)
- §2 gateway `mcp_sandbox_client.broker_send_rpc` → `…/sandbox/{org}/rpc`, build the
  `upstream` block (url, allowed_hosts, injected Bearer) for HTTP transports.
- §3 `mcp_proxy`: route streamable-http/sse/ws through `broker_send_rpc` instead of
  direct httpx (`is_adapter_transport` currently only stdio+websocket).
- §4 network assertion: gateway must not dial external MCP :443.
