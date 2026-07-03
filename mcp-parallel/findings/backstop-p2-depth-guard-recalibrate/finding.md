# CHG-0149 — MCP result/arg depth guard was mis-calibrated ABOVE the recursion-crash threshold it pre-empts

**Change-id:** CHG-0149
**Date:** 2026-07-03
**Severity:** LOW-MEDIUM (robustness / defense-in-depth — NOT an exploitable leak on the gateway path,
which already fail-closes a mid-scan RecursionError. Closes CHG-0148 residual #2 properly: the CHG-0115
proactive depth guard was set at 500, but `copy.deepcopy` (inside `apply_field_redaction`) and the recursive
scan crash at/below that — so the guard was defeated by the very recursion it exists to pre-empt, and the
control-plane redaction was left exposed to deep results the gateway forwarded).
**Area:** HARDEN 1.4 / HARDEN THE ARCHITECTURE — result validation / DoS depth guard (CHG-0115/0116),
completes CHG-0148.
**Files:** `gateway/ai_mesh_gateway/mcp_proxy.py` (`_MCP_MAX_RESULT_DEPTH`, which also defaults
`_MCP_MAX_ARG_DEPTH`); `gateway/ai_mesh_gateway/tests/test_mcp_result_block_count_cap.py` (+1 test).
**Whose work it touches:** the owning-session result/arg depth guard (CHG-0115/0116).

## Root cause

CHG-0115 added a PROACTIVE depth guard (`_exceeds_nesting_depth`, iterative) that is supposed to fire
BEFORE the recursive result scan hits Python's recursion limit — fail-closing cleanly with a
`RESOURCE_LIMIT` reason instead of relying on catching a fragile mid-scan `RecursionError`. Its own comment
said `500 ≫ any realistic legit result … and well under the stack limit`.

But that assumption is wrong. Empirically (measured on the gateway venv), `copy.deepcopy` — the first
recursive operation `apply_field_redaction` performs — RecursionErrors at depth **~498** with a shallow
stack, and LOWER under a real request's ambient call stack; the recursive scan/serialize crashes even
earlier. So the guard at **500** sat AT/ABOVE the crash threshold:

- A depth-498..500 result PASSED the guard (`< 500`) and then crashed `copy.deepcopy` mid-scan → caught by
  the floor's `except Exception` (mcp_proxy.py:1283) only as a **generic `SCAN_ERROR`**, not the clean
  `RESOURCE_LIMIT` the guard was designed to emit.
- More importantly, it left the **control-plane** `apply_field_redaction` (whose deepcopy-crash handling
  differs and which I cannot test here) exposed to deep results the gateway forwarded — the guard was meant
  to block those before they reach any redactor.

(Note: on the gateway *floor* path this is NOT an exploitable leak — the `except Exception` fail-closes the
RecursionError. This corrects CHG-0148 residual #2, which overstated it as a fail-open leak.)

## The fix (CHG-0149)

`_MCP_MAX_RESULT_DEPTH` default **500 → 200** (env `MCP_MAX_RESULT_DEPTH`; also the default for
`_MCP_MAX_ARG_DEPTH`). 200 is far above any realistic legitimate result (a handful of levels) yet
comfortably below the recursion-crash threshold for any plausible ambient stack — so the iterative guard
reliably fires FIRST, blocking a pathologically-deep result cleanly with `RESOURCE_LIMIT` before any
recursive `deepcopy`/scan can crash, on BOTH the gateway and (by blocking upstream) the control path.

Behavior change: a result nested at depth 201..~498 now blocks (`RESOURCE_LIMIT`, fail-closed) instead of
being redacted-and-forwarded. Depth >200 is ~10× any legitimate tool result, so this is a strictly-safer,
mandate-aligned (fail-closed) posture on pathological input.

## Verification

```
cd gateway
./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_result_block_count_cap.py -q   # 15 passed (1 new)
./.venv/bin/python -m pytest ai_mesh_gateway/tests -q                                       # 2034 passed, 0 failed
```

New `test_depth_cap_fires_below_deepcopy_recursion_limit`: a depth-300 result (below the old 500, above the
~498 deepcopy limit) is now blocked cleanly (`result_too_deeply_nested=True`, `RESOURCE_LIMIT`, and
`result_scan_error` is NOT set → it did not reach a deepcopy crash). The existing 20000-deep block test and
the shallow-passes test still pass (they track the constant / use a few levels).

## Scope / honesty note

Robustness/calibration fix; makes the CHG-0115 guard actually do what it intended and hardens the control
path. It does NOT change the fact that the gateway floor already fail-closed the crash. The remaining
CHG-0148 residual #1 (node-count overflow, `max_nodes=100k`, fail-open on a huge-but-shallow result) is
UNCHANGED — its clean fail-closed fix over-blocks benign large results, so it stays a documented tradeoff
for the owning session. Does not change the host-blocked live-stress status. Partial coverage is not
completion.
