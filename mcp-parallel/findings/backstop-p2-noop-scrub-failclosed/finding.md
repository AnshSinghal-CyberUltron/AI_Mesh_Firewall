# BACKSTOP hardening — fail-closed no-op-scrub guard (CHG-0047)

- **Item:** G2 item 2 ("Field-level redaction of MCP tool RESULTS — byte-verified, fail-closed").
- **Change-id:** CHG-0047 (2026-07-02)
- **Type:** Defense-in-depth (completes the CHG-0046 follow-up). Turns the mandate's
  "egress bytes are the only source of truth; fail closed on a no-op scrub" into an
  enforced invariant for the redaction path.

## Title
`scan_mcp_payload` now verifies, by payload bytes, that a Tier-1 redaction actually
applied; if the setter silently no-ops (raw value survives) it FAILS CLOSED (blocks)
instead of egressing the un-scrubbed result while `result_redacted` claims a scrub.

## Why
CHG-0046 fixed the KNOWN no-op setters (non-string dot-path / simple-key targets). But
`_mutate_dot_path` is best-effort for exotic nested-list paths, so a residual no-op
scrub could still occur — and the failure mode is silent: `result_redacted=True` is set
whenever `new_text != text`, regardless of whether the setter mutated anything. The
mandate requires bytes, not the action label, to be the source of truth.

## What it now does
In `scan_mcp_payload`'s Tier-1 loop, when a redaction is applied
(`new_text != text and tier1_action == "redact"`):
1. snapshot `_before = _safe_json(state_ref[0])`
2. `setter(new_text)`
3. if `_safe_json(state_ref[0]) == _before` (payload bytes UNCHANGED) → the scrub was a
   no-op → set `tier1_blocked = True` (+ a `noop_scrub_failclosed` scan-trace stage) →
   the whole result is blocked (`result.blocked = True`), so every caller of
   `_scan_tool_result_floor` withholds the result via its block-response shape.

This is general (catches ANY setter that fails to apply, not just the CHG-0046 cases),
precise (compares actual bytes — no out-of-scope false positives, no dependence on the
finding carrying a raw value), and cheap (one extra serialize per redacted target, and
redaction only fires when sensitive data was detected). A setter that DOES apply changes
the bytes → not blocked → redaction proceeds normally.

## Files
- `gateway/ai_mesh_gateway/mcp_scan_orchestrator.py` (import `_safe_json`; the guard in
  the Tier-1 redaction branch of `scan_mcp_payload`).
- `gateway/ai_mesh_gateway/tests/test_mcp_scan_orchestrator.py` (+2 tests).

## Verification
- `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_scan_orchestrator.py ai_mesh_gateway/tests/test_mcp_scan_targets.py -q`
  → **41 passed** (a no-op setter with a "redacted" tier1 text → `result.blocked=True` +
  `noop_scrub_failclosed` trace; a real setter that applies → not blocked, value masked).
- Broad sweep `ai_mesh_gateway/tests` → **1100 passed, 0 failed** — the guard causes ZERO
  spurious blocks on the existing legitimate redaction paths.

## Note
This is availability-yields-to-confidentiality: a genuinely un-scrubbable redaction now
withholds the result (graceful block, transport stays up) rather than leaking. Combined
with CHG-0003 (fail-closed on scan error) and CHG-0046 (real non-string setters), the
result-redaction path is now fail-closed on scan error, on setter no-op, AND for the
known non-string shapes.
