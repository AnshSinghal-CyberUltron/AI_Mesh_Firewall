# CHG-0104 — content-block-count resource limit (many-tiny-block bomb; byte cap missed it)

**Change-id:** CHG-0104
**Date:** 2026-07-03
**Severity:** MEDIUM (resource-bomb / availability — a many-block result stalled the event loop; the byte cap did not catch it).
**Area:** HARDEN item 16 "CPU/mem/disk/timeout limits — resource bombs contained".
**Files:** `gateway/ai_mesh_gateway/mcp_proxy.py` (`_MCP_MAX_CONTENT_BLOCKS` + a fail-closed check in `_scan_tool_result_floor`); `gateway/ai_mesh_gateway/tests/test_mcp_result_block_count_cap.py` (new).
**Whose work it touches:** the owning-session MCP scan core; complements the CHG-0103 tier1 offload.

## Root cause

Chasing the residual loop-stall after CHG-0103, a heartbeat probe showed a ~50k-tiny-block result still stalled the loop ~0.8 s. That stall is NOT the detect scan (offloaded by CHG-0103) nor the cross-block split-check (an A/B showed offloading it made no difference — my 4.81 s isolation number was cold-start pattern compilation; warm it is small) — it is the inline JSON serialization + per-block loops over ~50k objects. The 10 MB response byte cap does NOT stop this: 50k blocks × ~200 B is ~3–10 MB, UNDER the byte cap, so it reaches the scan and amplifies cost across every per-block loop (scan target extraction, JSON serialize, tool filtering). A resource limit on the NUMBER of content blocks was missing alongside the byte limit.

## The fix (CHG-0104)

`_scan_tool_result_floor` fails CLOSED (blocks) when a result has more than `_MCP_MAX_CONTENT_BLOCKS` (default 10000, env `MCP_MAX_CONTENT_BLOCKS`) content blocks — an O(1) `len()` check BEFORE the expensive scan, so a bomb costs nothing and never reaches the per-block loops. A per-tool `monitor` action stays observe-only (no block). Default 10000 is ≫ any realistic legit result (which has a handful of blocks). Handles both `{"content":[…]}` and a bare block list.

## Verification (all green)

```
cd gateway
./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_result_block_count_cap.py -q   # 7 passed
./.venv/bin/python -m pytest ai_mesh_gateway/tests -q                                      # 1620 passed, 0 failed
cd ../services/mcp-broker && ./.venv/bin/python -m pytest tests -q -k "not websocket"      # 108 passed
```

**Behavior:** a 50k-block bomb → blocked in **dt=0.000 s with MAX loop gap 0.000 s** (was ~0.8 s inline) — the O(1) check short-circuits before the scan. 7 tests: over-cap → fail-closed with `result_too_many_content_blocks` meta + `RESOURCE_LIMIT` tag; at-cap (10000) → allowed; bare block list also capped; normal result unaffected + still masked; `monitor` observe-only; bare-string result not affected; cap positive + env-configurable.

## Scope / honesty note

An HONEST investigation detail: I first tried offloading the cross-block split-check to a thread (like CHG-0103), but a warm A/B showed it made NO difference (0.80 s inline vs 0.84 s offloaded — the split-check was not the bottleneck), so I REVERTED that and landed on the block-count cap, which addresses the actual cost (the many-object JSON/loop overhead) at O(1). This is a resource-limit fix (no content leak → no aidefence oracle); proof is the behavior test + the loop-stall elimination. RESIDUAL: a SINGLE very large block (~9 MB) still costs ~10 s of CPU to scan, but CHG-0103 keeps that off the event loop (~0.2 s loop gap); reducing that raw CPU cost is a separate product-level trade-off. Does not change the host-blocked live-stress status (items 14–20). Partial coverage is not completion.
