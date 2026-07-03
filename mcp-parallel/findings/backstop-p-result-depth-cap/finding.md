# CHG-0115 — proactive nesting-depth cap on tool RESULTS (deep-nested resource bomb; robust + monitor-correct)

**Change-id:** CHG-0115
**Date:** 2026-07-03
**Severity:** LOW–MEDIUM (robustness + a monitor-semantics fix — the pre-existing fail-closed-on-RecursionError was SAFE for enforcement/leak, but crashed the observe-only `monitor` path into a wrongful block).
**Area:** HARDEN THE ARCHITECTURE — resource-bomb containment (deep-nesting DoS twin of CHG-0104 block-count / CHG-0114 exfil-walk).
**Files:** `gateway/ai_mesh_gateway/mcp_proxy.py` (`_exceeds_nesting_depth`, `_MCP_MAX_RESULT_DEPTH`, `_scan_tool_result_floor`); `gateway/ai_mesh_gateway/tests/test_mcp_result_block_count_cap.py` (+4 tests).
**Whose work it touches:** the owning-session result floor `_scan_tool_result_floor` (extends the CHG-0104 block-count cap; complements the CHG-0114 exfil-walk depth bound).

## Root cause

A tool RESULT **structure** (dict/list) nested thousands of levels deep makes the recursive scan / serialization
in `_mcp_security_scan` hit Python's recursion limit (~1000) → `RecursionError`. `_scan_tool_result_floor` wraps the
scan in a `try/except Exception` that fail-CLOSES (returns `blocked=True`, `SCAN_ERROR`) on any error — so a deep
result never LEAKED. But two problems remained:
1. **Fragile + opaque:** relying on catching a mid-scan stack overflow is brittle (recovery depends on remaining
   stack) and only yields a generic `SCAN_ERROR` — no clear resource-limit audit reason.
2. **Breaks `monitor`:** a per-tool `monitor` action is observe-only and must NEVER block. But under monitor the
   scan still ran, RecursionError'd, and the except fail-closed → a **wrongful block** violating the contract.

Byte-verified pre-fix: `_scan_tool_result_floor({"structuredContent": nested(20000)}, …)` logged
`result_scan_failed … maximum recursion depth exceeded (FAIL-CLOSED: blocking)`.

## The fix (CHG-0115)

A proactive, O(depth-bounded), **iterative** guard runs BEFORE the recursive scan:

```python
def _exceeds_nesting_depth(obj, limit):
    stack = [(obj, 0)]                    # own explicit stack — the guard never recurses,
    while stack:                          # so a deep payload can't DoS the check itself
        cur, depth = stack.pop()
        if depth > limit: return True
        if isinstance(cur, dict):  ... stack.append((v, depth+1)) ...
        elif isinstance(cur, list): ... stack.append((v, depth+1)) ...
    return False
```

In `_scan_tool_result_floor`, past `_MCP_MAX_RESULT_DEPTH` (500, env `MCP_MAX_RESULT_DEPTH` — ≫ any realistic result,
≪ the ~1000 stack limit) it branches on the resolved action:
- **real action** (tag/redact/block) → fail CLOSED, `["RESOURCE_LIMIT"]` + meta `result_too_deeply_nested`.
- **`monitor`** → forward the result UNSCANNED (`blocked=False`), meta `monitor_scan_skipped` — observe-only, never
  block, and the recursive scan (which would crash) is skipped.

So a deeply-nested result can no longer crash the scan, gives a clear audit reason, and monitor stays observe-only.

### Byte-level truth (post-fix)

- `_exceeds_nesting_depth(nested(50000), 500)` → `True` (NO RecursionError in the guard — iterative).
- `_exceeds_nesting_depth({"a":{"b":{"c":"x"}}}, 500)` → `False`; wide-shallow (5000 blocks) → `False` (no false positive).
- `{"structuredContent": nested(20000)}` under `redact` → `blocked=True`, `RESOURCE_LIMIT`, `result_too_deeply_nested=True`.
- same under `monitor` → `blocked=False` (forwarded, `monitor_scan_skipped`).
- `{"content":[{"type":"text","text":"user bob@corp.example"}]}` (shallow) → scans normally, PII masked, not blocked.

## Verification (all green)

```
cd gateway
./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_result_block_count_cap.py -q   # 11 passed (7 + 4 new)
./.venv/bin/python -m pytest ai_mesh_gateway/tests -q                                      # 1703 passed, 0 failed
```

Broker unaffected (gateway-only change).

## Scope / honesty note

Resource-bomb containment + robustness fix, action-aware. **Oracle:** N/A — a DoS-containment fix, not a PII-text
leak (no egress delta); the no-RecursionError-in-guard + clear-reason + monitor-not-blocked byte assertions are
authoritative. Notably this also CORRECTS a pre-existing monitor-semantics bug (deep result under monitor used to
wrongfully fail-closed-block). Does not change the host-blocked live-stress status (items 14–20). Partial coverage
is not completion.
