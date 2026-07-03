# CHG-0150 — result node-count guard: close the field-redaction WIDTH bypass + contain the deepcopy resource bomb (CHG-0148 residual #1)

**Change-id:** CHG-0150
**Date:** 2026-07-03
**Severity:** MEDIUM (1.4 leak + resource-bomb containment). Closes CHG-0148 residual #1: a wide-but-shallow
result with a `redaction_fields` target beyond `apply_field_redaction`'s `max_nodes` walk limit had the walk
STOP early and return a PARTIALLY-redacted result → the target egressed RAW. Same wide result also forced a
multi-second `copy.deepcopy` + recursive scan (a resource bomb).
**Area:** HARDEN 1.4 — field-level redaction of tool RESULTS ("fail-closed") + HARDEN THE ARCHITECTURE —
resource-bomb containment (items 2 / 10 / 17). Completes CHG-0148/0149.
**Files:** `gateway/ai_mesh_gateway/mcp_proxy.py` (`_MCP_MAX_RESULT_NODES`, `_exceeds_node_count`,
`_scan_tool_result_floor`); `gateway/ai_mesh_gateway/policy_engine.py` + `control/ai_mesh_control/policy/redaction.py`
(`apply_field_redaction` `max_nodes` 100k→2M); tests `test_mcp_result_block_count_cap.py` (+3),
`test_mcp_scan_orchestrator.py` (+1).
**Whose work it touches:** the two `apply_field_redaction` impls + the owning-session result guard chain (CHG-0115).

## Root cause (empirically confirmed)

`apply_field_redaction` bounds its walk with `max_nodes=100_000` and on overflow returned the PARTIALLY-masked
result (fail-open). The depth guard (CHG-0115/0149) bounds NESTING but not WIDTH — so a wide-but-shallow
result (e.g. a 10 MB list of small objects) passes the depth + content-block caps yet has millions of nodes.
Pure-import repro: a `{"deep": {"ssn": "LEAK"}, "pad": [{"i": i} for i in range(150_000)]}` (~300k nodes,
`ssn` late in the LIFO walk order) → with `max_nodes=100_000` the **`ssn` egressed RAW**; with the limit
raised it is masked. Opaque name-redacted fields (`session_token`) aren't caught by the content/pattern scan,
so this was a real leak.

Separately, measured: for a ~5M-node result, `copy.deepcopy` alone (which `apply_field_redaction` runs on the
FULL structure regardless of `max_nodes`) is **~2.1s**, and a full walk adds ~1.1s — i.e. a wide result is a
multi-second, ~1 GB-memory resource bomb even today.

## The fix (CHG-0150)

1. **Node-count guard** `_exceeds_node_count(obj, limit)` (iterative, short-circuits at `limit+1`, O(limit))
   + `_MCP_MAX_RESULT_NODES` (default **1,000,000**, env-tunable), wired into `_scan_tool_result_floor`
   right after the depth guard: a result wider than the cap is BLOCKED cleanly (`RESOURCE_LIMIT`,
   `result_too_many_nodes`) BEFORE the expensive `deepcopy`/scan/redact — fail-closed (monitor forwards
   unscanned, parity with the depth guard). This contains the resource bomb AND stops a too-wide result
   before redaction can partial-mask it. 1M nodes ≫ any realistic result (a DB page is thousands of nodes)
   and its deepcopy is ~0.4s.
2. **Redaction `max_nodes` 100k → 2,000,000** in both impls — ABOVE the 1M guard, so any result that PASSES
   the guard is FULLY walked (2M margin absorbs node-counting differences between the guard and the walk); a
   `redaction_fields` target can no longer sit beyond the walk limit.

## Verification

```
cd gateway
./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_result_block_count_cap.py ai_mesh_gateway/tests/test_mcp_scan_orchestrator.py -q   # all pass
./.venv/bin/python -m pytest ai_mesh_gateway/tests -q                                                                                        # 2053 passed, 0 failed
```

New tests (kept light — cap monkeypatched small / small explicit `max_nodes`, no million-node builds):
`_exceeds_node_count` iterative+short-circuit; a wide result → `RESOURCE_LIMIT` + `result_too_many_nodes`
(monitor → forward unscanned); a moderate result not node-blocked; and `apply_field_redaction` masking a
'late' target within the budget (the bypass-mechanism is shown to leak under a too-small budget). Control
variant verified via standalone pure-import repro (wide-result `ssn` now masked). The one full-suite
failure seen mid-work (`test_output_guard_redos` regex-linearity) was flakiness induced by my initial
million-node test payloads (it passes in isolation and references none of this code) — fixed by making the
tests light.

## Scope / honesty note

Closes CHG-0148 residual #1 (the last open field-redaction residual) AND contains the wide-result deepcopy
resource bomb. Behavior change: a result wider than 1M nodes now blocks (fail-closed) under a real action —
1M is ~10× any realistic result, so this only trims resource bombs. Does not change the host-blocked
live-stress status. Partial coverage is not completion.
