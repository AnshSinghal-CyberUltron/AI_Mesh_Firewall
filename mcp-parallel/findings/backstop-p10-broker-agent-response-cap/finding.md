# CHG-0151 — broker buffered the sandbox agent's RPC response UNBOUNDED (untrusted sandbox → shared-broker OOM)

**Change-id:** CHG-0151
**Date:** 2026-07-03
**Severity:** MEDIUM (cross-tenant availability / resource-bomb containment — the broker is SHARED across all
orgs and the sandbox runs untrusted tenant code; a buggy or COMPROMISED per-org agent returning a huge body
would OOM the one broker that routes every org's sandbox).
**Area:** HARDEN THE ARCHITECTURE — resource limits / DoS bounds (items 10 & 17); the broker self-defending
against the untrusted sandbox (parity with the gateway upstream-response cap CHG-0064 and the agent's own
upstream cap CHG-0066). Closes a documented residual from CHG-0146/0147.
**Files:** `services/mcp-broker/src/sandbox/routes.py` (`_AGENT_MAX_RESPONSE_BYTES`,
`_read_agent_response_capped`, `_post_agent_rpc`); `services/mcp-broker/tests/test_sandbox_routes.py`,
`services/mcp-broker/tests/test_agent_ready_retry.py` (migrated the agent-RPC mocks from `post`→`stream`,
+2 cap tests).
**Whose work it touches:** the broker sandbox RPC forwarder.

## Root cause

`_post_agent_rpc` forwarded each RPC to the in-sandbox agent with `await client.post(url, ...)` and returned
the `httpx.Response`; `_forward_sandbox_rpc` then read it via `response.json()` / `response.text`. `httpx`'s
`.post()` buffers the ENTIRE response body into memory with no size bound. The sandbox agent runs untrusted,
tenant-registered MCP servers; gVisor + the per-org `X-Sandbox-Agent-Key` are the isolation, but the broker
still **trusted** the agent's reply size. A compromised agent (or a bug) returning a multi-GB body would
force the broker to buffer it — and the broker is a single shared process routing EVERY org's sandbox, so
that is a cross-tenant availability breach. The agent self-caps its own upstream reads at 8 MiB
(`MCP_AGENT_MAX_RESPONSE_BYTES`, CHG-0066), but a self-cap inside the untrusted boundary is not a control the
broker can rely on.

## The fix (CHG-0151)

- `_AGENT_MAX_RESPONSE_BYTES` (default **16 MiB** = 2× the agent's 8 MiB self-cap so a legitimate max-size
  reply is never clipped; env `MCP_BROKER_AGENT_MAX_RESPONSE_BYTES`).
- New `_read_agent_response_capped(client, url, payload, headers)`: `client.stream("POST", ...)` +
  `aiter_bytes()`, accumulating into a buffer and raising HTTP 502 the instant the running total crosses the
  ceiling (never holds > ceiling in memory), then rebuilds a fully-read `httpx.Response` the caller uses
  `.json()`/`.status_code`/`.text` on unchanged.
- `_post_agent_rpc` calls the helper instead of `client.post()`. A transport error while opening the stream
  (agent socket not bound on a cold start) still raises `httpx.HTTPError`, so the existing cold-start retry
  loop behaves exactly as before; a too-large response raises `HTTPException(502)` (not `httpx.HTTPError`) so
  it fails fast WITHOUT retry.

## Verification

```
cd services/mcp-broker
./.venv/bin/python -m pytest tests/test_sandbox_routes.py tests/test_agent_ready_retry.py -q   # 25 passed
./.venv/bin/python -m pytest tests -q -k "not websocket"                                        # 174 passed, 0 failed
```

New: `test_agent_response_over_cap_is_rejected` (agent streams 2 KiB with a 1 KiB cap → 502 "ceiling");
`test_agent_response_under_cap_ok` (normal reply streams through unchanged). The cold-start retry tests
(`test_agent_ready_retry.py`, `_FakeClient` migrated to a streaming fake) and every route-forward test still
pass — the `post`→`stream` mock migration preserved the assertions (call args, forwarded headers, timeout
clamp, 503-provisioning).

## Scope / honesty note

Broker-side cross-tenant OOM containment; behavior unchanged for legitimate (≤16 MiB) replies. Required
migrating two broker test files' agent-RPC mocks from `.post` to `.stream` (a shared `_agent_stream_mock`
helper) — the reason this residual was deferred across CHG-0146/0147; the migration is mechanical and all
25 affected tests pass. The realistic trigger (a >16 MiB agent reply) needs a compromised/buggy agent inside
gVisor — a high bar — but the broker is shared, so the defense-in-depth bound is warranted. Does not change
the host-blocked live-stress status. Partial coverage is not completion.
