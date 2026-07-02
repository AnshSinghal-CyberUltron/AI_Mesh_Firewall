# BACKSTOP finding — URL/percent-encoded PII/secret bypassed redaction (CHG-0056)

- **Item:** G2 item 2 ("field-level redaction of tool RESULTS") / 1.4.
- **Change-id:** CHG-0056 (2026-07-02)
- **Severity:** MEDIUM — a %XX-encoded PII/secret (e.g. an email in a URL query param)
  egressed; the value is trivially recoverable by any URL parser / by eye.
- **Discovery:** continuation of the CHG-0054/0055 adversarial 1.4 verification — probing
  the encoding-obfuscation surface (base64/hex/url-safe-b64/double-b64/URL/token-cap).

## Audit of the obfuscation surface
`redact_all` → `_redact_obfuscated` de-obfuscated:
- base64, hex, url-safe base64, and double-base64 encoded PII/secrets → all CAUGHT
  (`[ENCODED_SECRET_REDACTED]`). GOOD.
- **URL/percent-encoding (`%XX`): NOT decoded** — `john.doe%40example.com` (email in a
  URL query param) or a %-encoded SSN broke the raw patterns (the `@`/`-` are `%40`/`%2d`)
  and the token egressed. The value is trivially recoverable. GAP.

## Fix
`gateway/ai_mesh_gateway/patterns.py` — added a percent-decode pass to
`_redact_obfuscated`: for each token carrying a `%XX` escape
(`_PERCENT_TOKEN_RE`, bounded to `_MAX_URL_DECODE_TOKENS=32` to stay decode-bomb safe),
`urllib.parse.unquote` it and, if the decoded form matches PII/secret
(`_detect_pii_core` / `_detect_secrets_core`), mask the whole encoded token with
`[ENCODED_SECRET_REDACTED]`. Only masks when decoded PII/secret is found — benign
percent text (`50%20off` → "50 off", `C%3A%5Cpath` → "C:\\path", `95%`, `?p=2%2C3`)
is untouched.

## Verification
- `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_url_encoding_redaction.py -q`
  → 11 passed: URL-encoded email in a query param, dash-encoded SSN, fully-%-encoded SSN,
  and a mixed case all masked (decoded fragment absent); benign percent text NOT masked;
  base64/hex obfuscation still works; plain PII still uses the normal maskers.
- Broad sweep `ai_mesh_gateway/tests` → 1158 passed, 0 failed (no regression to the G1
  unicode / G2 base64-hex de-obfuscation).

## Documented residual (NOT fixed here — a perf/security tradeoff)
The base64/hex decode is capped at `_MAX_DECODE_TOKENS=12` (a decode-bomb / DoS bound).
A crafted result that pads 12+ base64-looking decoy tokens BEFORE the real encoded secret
can hide it past the cap (it survives). Raising the cap trades DoS-resistance for
evasion-resistance — a deliberate tuning decision the owning session made. The URL-decode
pass uses its own bound (32). Flagged for a future decision (e.g. block-on-suspicious-
volume rather than silently skip), not changed unilaterally.
