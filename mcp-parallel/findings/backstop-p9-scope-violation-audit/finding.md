# BACKSTOP finding — cross-tenant scope violation not audited (CHG-0045)

- **Item:** G3 item 9 ("Gateway auth/authz/validation/rate-limit/policy/audit") — audit-completeness.
- **Change-id:** CHG-0045 (2026-07-02)
- **Severity:** MEDIUM — audit/forensics gap. Enforcement was correct (the 403 blocked
  the cross-tenant attempt); the gap is that the attempt left NO record in the
  MCPEvent audit/SIEM/compliance trail.

## Title
A cross-tenant access attempt (authenticated key's org ≠ URL org → 403
`org_scope_violation`) was only `LOG.warning`'d, never written to the MCPEvent audit
trail — so the single most important multi-tenant security signal was invisible to
audit/SIEM/compliance consumers, while lesser per-key authz denials WERE audited.

## Reproduction (code trace)
1. Every tenant-facing MCP route (`org_mcp_jsonrpc`, `org_mcp_tool_call`,
   `org_mcp_tools_list`, `org_mcp_server_health`) begins with
   `err = _validate_org_scope(request, org_slug)`.
2. `_validate_org_scope` (`mcp_proxy.py:1938`) returns a 403 `org_scope_violation`
   when `auth.org_slug != org_slug`, but only does `LOG.warning(...)` — it never calls
   `_record_gateway_event`.
3. Contrast: the per-key authz denials in `org_mcp_tool_call` (tool-not-allowed,
   cap-exceeded, disabled, inbound-PII-block) all call `_record_gateway_event(
   decision="block", ...)` (CHG-0006). So the cross-tenant breach attempt was the ONE
   block decision with no audit event.

## Expected
A blocked cross-tenant access attempt is recorded as an MCPEvent so a SOC/compliance
query (`reason=org_scope_violation`) surfaces it.

## Actual (before fix)
Only a gateway log line; nothing in MCPEvent. Invisible to the audit layer.

## Root cause
`_validate_org_scope` predates the audit wiring and was never updated to emit an event
on the deny path (it is also SYNC and shared by 4 routes + patched by ~13 tests, so it
could not simply be made async without wide breakage).

## Fix
New async wrapper `_audit_and_return_scope_error(request, org_slug, server_slug)` calls
`_validate_org_scope` (unchanged) and, on a 403, emits
`_record_gateway_event(decision="block", reason="org_scope_violation")` attributed to
the CALLER's real org (`auth.org_slug` — never the target, so the record stays inside
the caller's tenant boundary), with `target_org` + `key_prefix` in metadata. All 4
routes now call the wrapper. Keeping `_validate_org_scope` sync means the ~13
`patch.object(..., "_validate_org_scope", return_value=None)` sites and the direct-call
unit tests are untouched.

## Deliberate non-changes (documented)
- Rate-limit (429) rejections are NOT audited: under a burst, one audit event per
  rejected call would amplify load exactly when the limiter is protecting the system.
  The Redis rate-limit counters already carry that signal.
- Body-too-large (413) audit is a lower-priority follow-up.
- 401 (unauthenticated) has no org to attribute → left to the auth middleware/access logs.

## Verification
- `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_org_scope_data_path.py -q`
  → **6 passed** (cross-tenant 403 emits exactly one `org_scope_violation` MCPEvent,
  attributed to the caller's org with `target_org` in metadata; same-org pass emits none).
- Route tests that patch `_validate_org_scope` unaffected (test_mcp_rate_limit /
  test_mcp_bare_proxy_scan / test_e12_* → **67 passed**).
- Broad sweep `ai_mesh_gateway/tests` → **1094 passed, 0 failed**.

## Residual (item 9 still open)
Live rate-limit THRESHOLD probe (>150 req/s controlled burst on a dedicated key/host) +
adversarial policy-enforcement checks remain — need a dedicated host. This closes the
audit-completeness half for the cross-tenant deny path.
