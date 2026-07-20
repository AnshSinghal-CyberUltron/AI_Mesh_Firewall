# CHG-0134 — regression-lock: Tier-2 (Bedrock) scan error fails CLOSED under strict mode

**Change-id:** CHG-0134
**Date:** 2026-07-03
**Severity:** LOW (test-only regression-lock — **no production code change**). Locks in a critical 1.4 fail-closed invariant that was verified-but-untested, so a parallel refactor of the scanner/orchestrator can't silently flip it to fail-open (which would leak unscanned results during a Bedrock outage).
**Area:** HARDEN 1.4 — field-level redaction / scan of tool RESULTS, **fail-closed**; "no leakage during recovery/degradation" (item 18). Complements the tier-1 fail-closed tests (redact-that-leaks CHG-0057, noop-setter CHG-0047).
**Files:** `gateway/ai_mesh_gateway/tests/test_mcp_scan_orchestrator.py` (+1 test). No orchestrator change.
**Whose work it touches:** locks an invariant of the owning-session `_scan_text_tier2` / `scan_mcp_payload`; guards against regressions from the many parallel sessions editing `mcp_scan_orchestrator.py` / the scanner.

## Context — the invariant (verified sound)

The MCP scan runs Tier-1 (regex/policy, always) then conditional Tier-2 (Bedrock semantic). On a Tier-2
**exception** (`scan_prompt_with_tier2` raises — a Bedrock outage/throttle/timeout), `_scan_text_tier2`:

```python
except Exception as exc:
    LOG.warning("MCP Tier-2 scan failed: %s", exc)
    if strict_mode == "strict":
        return text, [], True, "tier2_error_strict"      # BLOCK (fail-closed)
    return text, [], False, "tier2_error_fail_open"       # forward (tier-1 already ran)
```

`strict_mode` **defaults to "strict"** (`tier2_ctrl.get("strict_mode") or "strict"`), so by default a Bedrock
error fails **closed**. Under an explicit `fail_open` posture it forwards — which is safe because Tier-1's
field-level redaction/blocking already ran (only the Tier-2 *semantic* layer is skipped). Verified end-to-end by
driving `scan_mcp_payload` with a scanner whose `scan_prompt_with_tier2` raises: strict → `blocked=True`,
fail_open → `blocked=False`. No live bypass.

(Also verified sound, not a gap: the `scanner is None` early-return fails-open even under strict — this is
intentional so a deployment WITHOUT Bedrock isn't bricked into blocking every request; the `"strict" in fallback`
gate at the block site distinguishes a runtime `tier2_error_strict` from a `scanner_unavailable` baseline.)

## The gap (coverage, not behavior)

`test_mcp_scan_orchestrator.py` had extensive strict/fail_open coverage over Tier-2 *action* verdicts
(block/redact/flag) but **no test that forces a Tier-2 exception** and asserts the fail-closed-under-strict
outcome. So a refactor of `_scan_text_tier2` that dropped the `if strict_mode == "strict"` branch (or inverted
the block flag) would silently start **forwarding results unscanned-by-Tier-2 during a Bedrock outage** — a 1.4
degradation leak — with a green suite.

## The lock (CHG-0134)

`test_tier2_bedrock_exception_fails_closed_under_strict` drives the real `scan_mcp_payload`: Tier-1 allows a
benign text (so Tier-2 runs), the scanner's `scan_prompt_with_tier2` raises, and it asserts **strict →
`blocked is True`** and **fail_open → `blocked is False`**.

## Verification

```
cd gateway
./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_scan_orchestrator.py -q   # 38 passed
```

## Scope / honesty note

Test-only regression-lock; no behavior change. Does not alter the host-blocked live-stress status (items
14–19). Partial coverage is not completion.
