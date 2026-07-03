# CHG-0137 — gateway retried tools/call after dispatch → double-execution of side-effecting tools

**Change-id:** CHG-0137
**Date:** 2026-07-03
**Severity:** MEDIUM (correctness / safety under partial failure — a non-idempotent `tools/call` that succeeded upstream but whose response was slow/lost was **retried**, re-executing the tool: a duplicate side effect — send-email-twice, double-charge, delete-then-recreate). Directly relevant to the mandate's retries / partial-failure / "no-leakage-during-recovery" edge cases.
**Area:** HARDEN THE ARCHITECTURE — gateway↔broker forwarding correctness; retries under chaos.
**Files:** `gateway/ai_mesh_gateway/mcp_sandbox_client.py` (`_request_with_503_retry`, `broker_send_rpc`); `gateway/ai_mesh_gateway/tests/test_mcp_sandbox_client.py` (+4 tests).
**Whose work it touches:** the owning-session gateway sandbox client (the single gateway→broker→sandbox forwarding path for all transports).

## Root cause

`broker_send_rpc` forwards every JSON-RPC exchange (all transports) to the broker via
`_request_with_503_retry`, which retried on:

1. **`503`** (sandbox not ready / cold start) — safe: 503 is returned *before* the request reaches the agent, so
   the tool cannot have executed.
2. **any `httpx.HTTPError`** — and this is the bug. `httpx.HTTPError` includes `ReadTimeout` (and reset
   mid-response). For a **`tools/call`** the flow is gateway → broker → agent → upstream MCP server, which
   **executes the tool** (arbitrary side effect) and then replies. If that reply is slow and the gateway's httpx
   client `ReadTimeout`s, the request was already dispatched and the tool **already ran** — but the retry re-POSTs
   and the upstream **executes the tool a second time**. `tools/call` is non-idempotent (send email, charge,
   delete, create), so this is a duplicate side effect on a routine slow call or under chaos.

## The fix (CHG-0137)

Make the retry **idempotency-aware**:

- `_NON_IDEMPOTENT_METHODS = {"tools/call"}` — the one method that runs an arbitrary tool. Every other method
  (`initialize`, `tools/list`, `resources/*`, `prompts/*`, `ping`, `completion/complete`) is a read/handshake,
  safe to repeat.
- `_request_with_503_retry` gains an `idempotent` flag (default `True` — preserves existing behavior for
  `ensure_sandbox` etc.). On an `httpx.HTTPError`, a **non-idempotent** call is retried **only** if the error is
  *pre-send* (`ConnectError` / `ConnectTimeout` / `PoolTimeout` — the connection never established, so the tool
  could not have run). A *post-send* error (`ReadTimeout` / reset) on a non-idempotent call **fails immediately**
  (`RuntimeError` — not retried), avoiding double-execution.
- The **503** retry path is unchanged and always applies (pre-execution).
- `broker_send_rpc` passes `idempotent=(method not in _NON_IDEMPOTENT_METHODS)`.

## Verification

```
cd gateway
./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_sandbox_client.py -q   # 17 passed
./.venv/bin/python -m pytest ai_mesh_gateway/tests -q                              # 1874 passed, 0 failed
```

New tests: `tools/call` + `ReadTimeout` → **not retried** (`call_count == 1`, raises "double execution");
`tools/list` + `ReadTimeout` → retried (`call_count == 2`, idempotent read); `tools/call` + `ConnectError`
(pre-send) → retried (tool could not have run); `tools/call` + `503` → retried (pre-execution).

## Scope / honesty note

Conservative: only `tools/call` is treated as non-idempotent (the clear side-effecting method); everything else
retries as before, so resilience for reads/handshakes is unchanged. A pre-send failure on `tools/call` still
retries (safe). Does not change the host-blocked live-stress status (items 14–19). Partial coverage is not
completion.
