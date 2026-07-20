# CHG-0141 — stdio transport default was fail-OPEN (spawned untrusted npm/uvx IN the gateway host process)

**Change-id:** CHG-0141
**Date:** 2026-07-03
**Severity:** MEDIUM (secure-default / tenant-isolation — a fail-OPEN default meant a prod deployment that
forgot a single env var silently ran untrusted, org-supplied stdio MCP servers directly in the shared
gateway process on the host, with cross-tenant process/fs sharing and no gVisor sandbox).
**Area:** HARDEN THE ARCHITECTURE — "ALL transports in the per-org sandbox; NOTHING in the main backend;
no unknown npm on the host." Secure-by-default on a security-critical toggle.
**Files:** `gateway/ai_mesh_gateway/mcp_stdio_adapter.py` (`_STDIO_IN_PROCESS_DEFAULT`);
`gateway/ai_mesh_gateway/tests/test_mcp_stdio_adapter_branch.py` (+2 tests). No behavior change for any
deployment that already sets the env (compose/prod set `false`).
**Whose work it touches:** the owning-session stdio adapter (the sandbox-routing migration).

## Root cause

`send_jsonrpc()` (the single entry point every gateway stdio call funnels through) dispatches on
`_stdio_in_process()`:

```python
# mcp_stdio_adapter.py
_STDIO_IN_PROCESS_DEFAULT = "true"          # <-- the bug

def _stdio_in_process() -> bool:
    raw = os.environ.get("MCP_STDIO_IN_PROCESS", _STDIO_IN_PROCESS_DEFAULT)
    return raw.lower() in ("1", "true", "yes")

async def send_jsonrpc(org_slug, server_slug, ...):
    if not _stdio_in_process():
        return await _send_jsonrpc_broker(...)      # -> per-org gVisor sandbox (via broker)
    return await _send_jsonrpc_in_process(...)       # -> spawn npx/uvx/node IN the gateway host process
```

- `_stdio_in_process() == False` → `_send_jsonrpc_broker`: the org's stdio MCP server is launched **inside
  that org's gVisor sandbox** by the broker. This is the architecture invariant — every transport
  (http/ws/sse/stdio) lives in the per-tenant sandbox; nothing untrusted runs in the main backend.
- `_stdio_in_process() == True` → `_send_jsonrpc_in_process`: the gateway spawns the stdio server's
  command (`npx …`, `uvx …`, `node …`, `python …`) **as a child of the gateway process, on the host**.

With `_STDIO_IN_PROCESS_DEFAULT = "true"`, the mode you got **by omission** was the unsafe one. Any
deployment path — helm, k8s manifest, bare-metal, or a dev config promoted to prod — that did not
explicitly set `MCP_STDIO_IN_PROCESS=false` ran **org-registered, untrusted, third-party npm/PyPI code
directly in the shared multi-tenant gateway process on the host**: no gVisor, no per-tenant network/fs
isolation, and all orgs' stdio servers sharing the gateway's process namespace and filesystem. That
directly defeats the mandate's "ALL transports in the sandbox / no unknown npm on the host / NOTHING in
the main backend."

Fail-OPEN defaults are the classic secure-default anti-pattern: the safe behavior required an explicit
opt-out, so a single forgotten variable degraded straight to "runs untrusted code on the host" instead of
degrading to "sandboxed."

## The fix (CHG-0141)

```python
# CHG-0141: default FALSE = route stdio through the per-org sandbox (no unknown npm on the gateway
# host; ALL transports in the sandbox — a core 1.4/architecture invariant). ... set
# MCP_STDIO_IN_PROCESS=true to opt INTO the in-gateway spawn (dev only / single-tenant, no broker).
_STDIO_IN_PROCESS_DEFAULT = "false"
```

Now, with `MCP_STDIO_IN_PROCESS` unset, `send_jsonrpc()` routes stdio through the per-org sandbox
(`_send_jsonrpc_broker`) — parity with the remote transports (http/ws/sse), which are already
sandbox-routed by default. The in-gateway spawn is still fully available but is now **opt-in**: set
`MCP_STDIO_IN_PROCESS=true` for a dev / single-tenant box where no broker is running.

This is a deliberate flip of a dev-convenience default to secure-by-default. It is behavior-preserving for
every deployment that already sets the variable (compose/prod set `false`), and it makes the *safe* mode
the one you get for free.

## Why the full suite stays green (test-safety)

All four pre-existing tests that exercise the in-process spawn path set `MCP_STDIO_IN_PROCESS` **explicitly**
via `monkeypatch.setenv(..., "true")` / `"false"` in their fixtures — none of them relied on the module
default. So flipping the default changed no existing test's behavior; the full gateway suite is unchanged
at **1948 passed, 0 failed**.

## Verification

```
cd gateway
./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_stdio_adapter_branch.py -q   # 8 passed
./.venv/bin/python -m pytest ai_mesh_gateway/tests -q                                     # 1948 passed, 0 failed
```

New regression locks (`test_mcp_stdio_adapter_branch.py`):

- `test_stdio_default_is_sandbox_secure` — `delenv(MCP_STDIO_IN_PROCESS)` (overrides any autouse fixture),
  then asserts `_STDIO_IN_PROCESS_DEFAULT == "false"` **and** `_stdio_in_process() is False`. Guards against
  a silent regression back to the fail-open (host-spawn) default.
- `test_stdio_in_process_still_opt_in` — `setenv(..., "true")` → `_stdio_in_process() is True` (the
  in-gateway spawn remains reachable when explicitly opted in).

## Scope / honesty note

This closes a fail-open default on a security-critical isolation toggle; it does not change the runtime
sandbox mechanism itself (that is the broker/gVisor path, already exercised elsewhere). It does not change
the host-blocked live-stress status (items 14–19: 300–500 concurrent sandboxes, 5k–10k concurrent calls,
soak, chaos) or the cross-plane frontend item (21). Partial coverage is not completion.
