# CHG-0124 — regression-lock: chat-path (internal_tools_call) disabled-tool authz never forwards/executes

**Change-id:** CHG-0124
**Date:** 2026-07-03
**Severity:** LOW (test-only regression-lock — **no production code change**). Locks in a security invariant (chat-path tool authorization) that had only unit coverage, so a future parallel-session edit can't silently remove it.
**Area:** HARDEN 1.4 — per-user/agent/role tool authorization, chat-pipeline surface. Complements the direct-MCP-path per-API-key allowlist (`_tool_allowed_by_key`, CHG-0006/0038).
**Files:** `gateway/ai_mesh_gateway/tests/test_mcp_internal_http_result_scan.py` (+2 tests, +1 driver). No `mcp_proxy.py` change.
**Whose work it touches:** locks in an invariant of the owning-session `internal_tools_call` handler; guards against regressions from the many parallel sessions currently churning `mcp_proxy.py`.

## Context — the authz model (verified, sound)

Two tool-call authorization models coexist by design:

- **Direct MCP path** (VS Code etc., `org_mcp_jsonrpc` / `org_mcp_tool_call`): the end user presents an **API
  key**, whose `mcp_allowed_tools` allowlist is enforced call-time (`_tool_allowed_by_key`, L3743/L4506) and
  reflected in tools/list visibility (`_filter_tools_by_key_allowlist`). Plus the org-level disabled set + the
  per-key tool-call cap.
- **Chat-pipeline path** (`internal_tools_call`, `/v1/mcp/internal/tools-call`, called by the control-plane
  `MCPToolCallView`): the caller is the trusted backend over an internal key — there is **no end-user API key**,
  so `mcp_allowed_tools` (a per-key concept) does not apply. The chat path's tool governance is the **org-level
  enabled/disabled** set: `internal_tools_call` blocks a disabled tool near the top of the handler
  (`_is_tool_disabled(tool_name, enabled_info)` → JSON-RPC `-32000 "Tool '<t>' is disabled for this server."`,
  mcp_proxy.py L2701) BEFORE any upstream forward, and then runs the full 1.4 chain (inbound arg scan/block,
  outbound result scan/redact, compliance tagging, audit).

This was verified end-to-end by reading the handler and the control-plane caller (`views.py` L834 — the payload
carries `org_slug/server_slug/tool_name/arguments/url/transport/auth*`, no actor allowlist, confirming the
per-key allowlist is intentionally a direct-path concept). **No live bypass.**

## The gap (coverage, not behavior)

The disabled-tool block was covered only at the **unit** level (`_is_tool_disabled` + the enabled-tools cache
in `test_mcp_enabled_tools_cache.py`). There was **no end-to-end test** proving that `internal_tools_call`,
given a disabled tool, (a) returns the block error and (b) **short-circuits execution — the tool is never
forwarded upstream on EITHER path** (sandbox `_adapter_forward` or legacy httpx), i.e. nothing egresses. With
many parallel sessions editing `mcp_proxy.py`, an accidental reordering/removal of the disabled check would be
a real 1.4/authz regression that no test would catch.

## The lock (CHG-0124)

`test_internal_disabled_tool_blocked_and_not_forwarded` drives the real `internal_tools_call` with a disabled
tool and asserts:
1. JSON-RPC error `code == -32000` and message contains `is disabled for this server`.
2. **SECURITY INVARIANT:** `_adapter_forward.call_count == 0` (sandbox/broker forward never happened) AND the
   httpx `client.post.call_count == 0` (legacy forward never happened) → the tool never executed → no egress.

`test_internal_enabled_tool_is_forwarded_control` (positive control, legacy path) proves the harness can
actually observe a forward (`client.post.call_count >= 1`) — so the no-forward assertion is not vacuous.

## Verification

```
cd gateway
./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_internal_http_result_scan.py -q   # 10 passed
./.venv/bin/python -m pytest ai_mesh_gateway/tests -q                                          # 1787 passed, 0 failed
```

## Scope / honesty note

Test-only regression-lock; no behavior change. The disabled check is a policy control (fail-OPEN on an
enabled-tools lookup failure by deliberate availability design — see `test_backend_failure_negative_cached`);
the actual leak floor (result scan/redact) is separately fail-CLOSED, so a disabled tool executing during a
backend outage would still have its result scanned. Does not change the host-blocked live-stress status
(items 14–19). Partial coverage is not completion.
