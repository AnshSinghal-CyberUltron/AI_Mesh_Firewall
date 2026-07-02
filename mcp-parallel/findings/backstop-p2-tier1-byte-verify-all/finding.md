# BACKSTOP hardening — tier1 redact path now byte-verifies ALL detected categories (CHG-0057)

- **Item:** G2 item 2 ("field-level redaction of tool RESULTS — byte-verified, fail-closed") / 1.4.
- **Change-id:** CHG-0057 (2026-07-02)
- **Type:** Defense-in-depth (fail-closed byte-truth) + end-to-end verification that the
  CHG-0054/0055/0056 patterns.py fixes reach the MCP tool-result egress.

## End-to-end verification (why the patterns.py fixes matter)
Traced the MCP tool-result redaction path: `scan_mcp_payload` → `_scan_text_tier1`. The
tier1 PII/secret branch (mcp_scan_orchestrator.py ~312-345) detects with
`detect_pii` / `detect_secrets` / `detect_ip_leakage` and, under `enforcement="redact"`,
masks with `redact_all` — ALL from `patterns.py`. So CHG-0054 (private-key block),
CHG-0055 (api_key assignments) and CHG-0056 (URL-encoding) DO protect real MCP tool
results end-to-end (tier1 is patterns-based; the Presidio scanner is tier2 only).

## Gap fixed
The tier1 redact branch already had a fail-closed byte-check — "if a detected value
survives `redact_all` verbatim, BLOCK rather than forward a redacted-but-leaking result"
— but it checked ONLY `ip_leak.values()`, with a comment ASSUMING "PII/secret values are
always covered by redact_all". CHG-0054 showed that assumption can be violated (a masker
bug left a secret partially un-scrubbed). CHG-0057 byte-verifies ALL detected categories
(`pii` + `secrets` + `ip_leak`) — the assumption is now ENFORCED, not assumed.

## Fix
`gateway/ai_mesh_gateway/mcp_scan_orchestrator.py` — the byte-check now unions the raw
values of all detected categories:
```
_detected_values = list(pii.values()) + list(secrets.values()) + list(ip_leak.values())
if any(v and str(v) in candidate for v in _detected_values):
    blocked = True   # a detected value survived the scrub -> fail closed
```

## No false positives
`redact_all` REPLACES every detected match (via a masker or the default `[X_REDACTED]`
tag), so a detected value's raw form is never a substring of the scrub under normal
operation. Verified over a battery (email/ssn/card/phone/aws/openai/github/slack/stripe/
password=/token=/api_key=/private-key/internal-host): ZERO would-be false blocks. The
guard only fires when `redact_all` genuinely leaves a detected value verbatim (a real
masker bug / no-op scrub) → block.

## Verification
- `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_scan_orchestrator.py -q -k chg0057`
  → 2 passed: a stubbed no-op `redact_all` (detected email survives) → `blocked=True`
  (fail-closed); a real `redact_all` → `blocked=False`, email masked (no false positive).
- Broad sweep `ai_mesh_gateway/tests` (excluding another session's untracked, incomplete
  `test_mcp_enforcement_block_recording.py`, which fails to collect on an undefined
  `_rest_request` helper — unrelated to this change) → 1160 passed, 0 failed.
