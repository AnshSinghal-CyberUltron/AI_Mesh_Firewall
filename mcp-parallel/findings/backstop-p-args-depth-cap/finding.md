# CHG-0116 — proactive nesting-depth cap on inbound tool ARGS (input-side twin of CHG-0115)

**Change-id:** CHG-0116
**Date:** 2026-07-03
**Severity:** LOW–MEDIUM (robustness + monitor-semantics fix on the INPUT path; attacker-controlled args).
**Area:** HARDEN THE ARCHITECTURE — resource-bomb containment (input-side parity for the CHG-0115 result depth cap).
**Files:** `gateway/ai_mesh_gateway/mcp_proxy.py` (`_scan_tool_args_block` + `_MCP_MAX_ARG_DEPTH`); `gateway/ai_mesh_gateway/tests/test_mcp_result_block_count_cap.py` (+3 tests).
**Whose work it touches:** the owning-session inbound scanner `_scan_tool_args_block` (input-side twin of CHG-0115; parity with the CHG-0104 block-count cap).

## Root cause

CHG-0115 added a proactive, iterative depth cap to the RESULT floor (`_scan_tool_result_floor`). But the INPUT
scanner `_scan_tool_args_block` — which scans **attacker-controlled tool-call ARGUMENTS** (the primary input
surface) — had none. A deeply-nested args payload makes the recursive input scan hit Python's recursion limit
(~1000) → `RecursionError`. `_scan_tool_args_block`'s `try/except` caught it and fail-CLOSED (no raw egress), BUT:
1. **Fragile + opaque:** a mid-scan RecursionError with only a generic `arg_scan_error` reason.
2. **Breaks `monitor`:** under a per-tool `monitor` action (observe-only, must NEVER block), the crash fail-closed →
   a **wrongful block**.

Byte-verified pre-fix: `_scan_tool_args_block({"payload": nested(20000)}, enabled_info={"tool_scan_actions":
{"fetch":"monitor"}})` returned `blocked=True` (the monitor violation), logging `arg_scan_failed … maximum recursion
depth exceeded`.

## The fix (CHG-0116)

`_scan_tool_args_block` now runs the same iterative `_exceeds_nesting_depth` guard (introduced in CHG-0115 — its own
explicit stack, so the guard can't itself be recursion-DoS'd) BEFORE the scan. Past `_MCP_MAX_ARG_DEPTH` (defaults
to `_MCP_MAX_RESULT_DEPTH`=500, env `MCP_MAX_ARG_DEPTH`) it branches on the resolved action:
- **real action** (tag/redact/block) → fail CLOSED, `["RESOURCE_LIMIT"]` + meta `args_too_deeply_nested`.
- **`monitor`** → forward the args UNCHANGED (`blocked=False`), meta `monitor_scan_skipped`.

So deeply-nested args can no longer crash the input scan, produce a clear audit reason, and monitor stays
observe-only. The credential force-block on shallow args is unchanged.

### Byte-level truth (post-fix)

- `{"payload": nested(20000)}` under `redact` → `blocked=True`, `RESOURCE_LIMIT`, `args_too_deeply_nested=True`.
- same under `monitor` → `blocked=False` (forwarded, `monitor_scan_skipped`).
- `{"q":"email bob@corp.example"}` (shallow) → scans normally, PII masked, not blocked.
- `{"key":"AKIAIOSFODNN7EXAMPLE"}` (shallow credential) → still force-blocked.

## Verification (all green)

```
cd gateway
./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_result_block_count_cap.py -q   # 14 passed (11 + 3 new)
./.venv/bin/python -m pytest ai_mesh_gateway/tests -q                                      # 1706 passed, 0 failed
```

Broker unaffected (gateway-only change).

## Scope / honesty note

Resource-bomb containment parity: the INPUT path now matches the RESULT path (CHG-0104/0115) — proactive, iterative,
action-aware. **Oracle:** N/A — DoS-containment fix, no PII-text egress delta; the no-crash + clear-reason +
monitor-not-blocked byte assertions are authoritative. Also corrects the input-side monitor-semantics bug (deep args
under monitor used to wrongfully fail-closed-block). Does not change the host-blocked live-stress status (items
14–20). Partial coverage is not completion.
