# CHG-0105 — internal (chat→MCP) route returned stdio/websocket tool RESULTS UNSCANNED

**Change-id:** CHG-0105
**Date:** 2026-07-03
**Severity:** HIGH (fail-open 1.4 data leak — an entire call path egressed raw tool results to the LLM).
**Area:** HARDEN 1.4 — field-level redaction of tool RESULTS (byte-verified, fail-closed), internal (chat-pipeline) route.
**Files:** `gateway/ai_mesh_gateway/mcp_proxy.py` (`internal_tools_call` sandbox branch); `gateway/ai_mesh_gateway/tests/test_mcp_internal_sandbox_result_scan.py` (new).
**Whose work it touches:** the owning-session proxy `internal_tools_call` (the route the chat pipeline uses to invoke MCP tools for a user).

## Root cause

`internal_tools_call` (the `X-Gateway-Internal-Key` route the CHAT pipeline calls to run an MCP tool on a user's behalf) scans the RESULT only on the streamable-http path (`_scan_internal_result`). On the SANDBOX-routed transports (`stdio` / `websocket`, `_is_sandbox_routed`) it returned the raw `_adapter_forward(...)` response DIRECTLY — with NO result scan:

```python
if _is_sandbox_routed(transport):
    return await _adapter_forward(transport, config, org_slug, server_slug, call_body, "2.0", 1)
```

So a secret / PII / internal-IP / zero-click exfil-beacon / markdown-split value in a **stdio/ws** tool result egressed to the chat pipeline → the LLM's context (and any UI showing tool output) **unredacted** — while the SAME tool called via `org_mcp_jsonrpc` (CHG-0091 region) IS scanned. A pure transport-path parity gap.

Empirically confirmed pre-fix: a stdio tool result carrying `AKIAIOSFODNN7EXAMPLE`, `bob@corp.example`, and `![x](https://evil…/?d=<b64>)` egressed ALL THREE raw.

## The fix (CHG-0105)

The sandbox branch now buffers the adapter response, and (error-envelope aware, CHG-0091) scans the whole `result` OR a bare `error` envelope via the shared `_scan_tool_result_floor` — so ALL the hardened result-scan machinery applies (secret/PII/IP redaction, render-leak neutralization CHG-0096-0100, cross-block split-check, block-count cap). It swaps in the masked result, fails CLOSED (JSON-RPC block error) on an unmaskable survivor, and AUDITS the block/redact decision (`transport="internal_sandbox"`) — full parity with the org path.

## Verification (all green)

```
cd gateway
./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_internal_sandbox_result_scan.py -q   # 5 passed
./.venv/bin/python -m pytest ai_mesh_gateway/tests -q                                            # 1625 passed, 0 failed
cd ../services/mcp-broker && ./.venv/bin/python -m pytest tests -q -k "not websocket"            # 108 passed
```

5 new tests drive the REAL `internal_tools_call` sandbox path: secret + PII masked (+ redact audited); exfil beacon defanged; error-envelope secret masked (CHG-0091 parity); unmaskable survivor → fail-closed block (+ audited); benign unchanged. Existing internal HTTP-path tests still pass.

### Byte-level truth (internal-sandbox egress)

`result.content` text `user alice.jones@corp.example ssn 123-45-6789` → **FIXED** `user a***@c***.example ssn ***-**-6789`; secret+beacon variant → AWS key masked, beacon auto-render dropped.

### Independent oracle (aidefence)

`aidefence_has_pii` on the result JSON: **false** on the fixed egress, **true** on the raw (pre-fix) egress.

## Scope / honesty note

Closes an entire unscanned egress path (chat-pipeline → stdio/ws MCP tool → LLM). RESIDUAL: the internal streamable-http path's `_scan_internal_result` scans only `result.content` (not a bare error frame or `structuredContent`) — a NARROWER residual than the sandbox complete-skip this fixes; noted for a follow-up to bring the HTTP path to the same error-envelope-aware whole-result scan. Does not change the host-blocked live-stress status (items 14–20). Partial coverage is not completion.
