# BACKSTOP finding — non-string scan target had a no-op redaction setter (CHG-0046)

- **Item:** G2 item 2 ("Field-level redaction of MCP tool RESULTS — byte-verified, fail-closed").
- **Change-id:** CHG-0046 (2026-07-02)
- **Severity:** HIGH — 1.4 result-redaction fail-open. A detected secret/PII in a
  non-string field was **reported redacted** (audit `decision=redact`,
  `result_redacted=True`) yet **egressed RAW**. This is exactly the
  "report-redact / forward-raw" the mandate forbids.

## Title
For `key_path` / simple-key scan targeting, a target resolving to a NON-STRING value
(number / list / object) was bound to a no-op setter, so redaction of that value was
silently dropped while the system recorded a successful redaction — and the E12
result-floor was bypassed.

## Reproduction (code trace)
1. `scan_mcp_payload` (`mcp_scan_orchestrator.py`) extracts targets via
   `extract_and_bind` → `extract_scan_targets` (`mcp_scan_targets.py`). Each target is
   `(text, setter, path_label)`; the text is `_safe_json(value)` so a NUMBER/LIST/OBJECT
   IS scanned.
2. Redaction is applied ONLY through `setter(new_text)` (`mcp_scan_orchestrator.py:526`:
   `if new_text != text and tier1_action == "redact": setter(new_text); result_redacted = True`).
3. For a non-string target the setter was a NO-OP:
   - dot-path: `mcp_scan_targets.py:108` → `lambda _v, _val=val: None`
   - simple-key: `mcp_scan_targets.py:123` → `lambda _n, _p=node, _k=k: None`
4. So a detected secret/PII in e.g. `{"account": {"ssn": 123456789}}` (numeric) or
   `{"contacts": ["a@b.com", ...]}` (list) produced a masked `new_text`, `setter` no-op'd,
   but `result_redacted=True` was set.
5. Because `result_redacted=True`, `scan_mcp_payload` returns the deep-copied `mutable`
   (unmodified) — a FRESH object, so back in `_scan_tool_result_floor` the guard
   `scanned is result_content` is False and the **E12 redaction floor never fires**.
   Net: the raw numeric/list value egresses while the audit says `decision=redact`.

## Expected
A redaction reflected in `result_redacted` / the audit MUST be present in the egress
bytes; a non-maskable flagged span fails closed.

## Actual (before fix)
Raw non-string value forwarded; `result_redacted=True`; floor bypassed.

## Root cause
Non-string targets were given placeholder no-op setters (only string targets got real
mutators), so the redaction write was silently discarded.

## Fix
`extract_scan_targets` now binds the SAME real mutators for non-string targets:
- dot-path → hoisted `_make_setter(idx)` → `_mutate_dot_path` (replaces the value with
  the masked string).
- simple-key → in-place `node[key] = new`.
The setter only fires when `new_text != text` (a real mask), so clean non-string values
are untouched. Entire-mode (default) was already correct.

## Verification
- `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_scan_targets.py ai_mesh_gateway/tests/test_mcp_scan_orchestrator.py -q`
  → **39 passed** (numeric-SSN keyed / list-of-emails keyed / dot-path numeric setters
  now mutate `state[0]`; end-to-end `scan_mcp_payload` applies the mask to a non-string
  key_path target — raw value absent from the egress bytes).
- Broad sweep `ai_mesh_gateway/tests` → **1098 passed, 0 failed**.

## Follow-up (documented, out of scope for this change)
- `_mutate_dot_path` is best-effort for exotic nested-list paths (a pre-existing
  limitation the string path shares).
- A general fail-closed OUTPUT byte-check in `_scan_tool_result_floor` — block if any
  detected value survives the scrub in the output bytes — would be the ultimate backstop
  against any residual no-op scrub and is recommended as a future hardening.
