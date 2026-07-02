# BACKSTOP hardening — ext_mcp_proxy audit trail completed: SSE-result block + tool-call allow (CHG-0070)

- **Item:** G3 item 9 (gateway audit) / 1.4 "...→tag→AUDIT". Completes CHG-0068.
- **Change-id:** CHG-0070 (2026-07-02)
- **Type:** Audit-completeness (self-correction of a CHG-0068 omission + usage-audit addition).

## Gap
CHG-0068 added ext_mcp_proxy audit for the SSRF block, inbound credential block, and the JSON-branch
result block/redact — but it MISSED:
  1. the **SSE result block** (`_scan_reframe_sse_tool_result` returns `_block_info` when a tools/call
     SSE result matches a block posture) — a security-critical DENY on the SSE path that egressed NO
     audit event, so a blocked SSE tool result was invisible in the MCPEvent trail while the equivalent
     JSON block WAS audited (inconsistent);
  2. any **successful tool-call** (allow) — so external tool USAGE (which tools an org actually invoked)
     was not recorded, only the blocks/redacts.

## Fix — `gateway/ai_mesh_gateway/mcp_proxy.py`
Reuses the CHG-0068 `_ext_audit` helper (best-effort, fire-and-forget, no-op without org):
  * SSE result block → `_ext_audit("block", "pii_blocked_outbound", tool=..., tags=_block_info["tags"])`.
  * SSE result success (finite tools/call SSE, not blocked) → `_ext_audit("allow", "ok", tool=...)` when
    a tool name is present.
  * JSON result success (clean, not redacted) → `_ext_audit("allow", "ok", tool=...)` in the `elif` of
    the CHG-0068 redact branch (so a call is recorded exactly once: block XOR redact XOR allow).
The allow events are gated on `_ext_tool_name` so protocol overhead (initialize / tools-list) does not
flood the audit — only actual tool invocations (tools/call, prompts/get) record usage.

## Verification
- `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_bare_proxy_scan.py -q`
  → 51 passed. New: a clean tools/call result records `decision=allow` with `tool_name=fetch`; an SSE
  tools/call result that blocks (scan fail-closed) records `decision=block`. Together with CHG-0068 the
  ext-proxy now audits block XOR redact XOR allow for every tool call on both the JSON and SSE paths.
- Full sweep `ai_mesh_gateway/tests` → 1256 passed, 0 failed.

## Residual (follow-up)
The infra-error withholds (non-200 / non-JSON body-withheld [CHG-0061], upstream-response-too-large
[CHG-0064]) are still not audited — those are transport/infra errors already logged, lower priority.
