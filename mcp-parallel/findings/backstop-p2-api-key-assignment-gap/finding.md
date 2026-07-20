# BACKSTOP finding — api_key/access_key assignments not redacted (CHG-0055)

- **Item:** G2 item 2 ("field-level redaction of tool RESULTS") / 1.4.
- **Change-id:** CHG-0055 (2026-07-02)
- **Severity:** MEDIUM — a secret-labeled assignment (`API_KEY=<token>`) egressed
  unmasked when the value didn't match a provider-specific pattern.
- **Discovery:** continuation of the CHG-0054 adversarial 1.4 verification (a battery of
  structured/multi-line secrets through `redact_all`).

## Title
The secret inventory redacted `password=` / `secret=` / `token=` assignments but had NO
`api_key=` / `apikey=` / `access_key=` assignment pattern — so an `API_KEY=<value>` whose
value did not match a provider-specific format (OpenAI `sk-`+32, AWS `AKIA…`, Google
`AIza…`, Slack, Stripe, etc.) egressed UNMASKED.

## Reproduction (byte-truth)
```
redact_all("API_KEY=sk-abcdef0123456789ABCDEFxyz")  ->  "API_KEY=sk-abcdef0123456789ABCDEFxyz"   # UNMASKED
redact_all("password=hunter2secret")                ->  "password=***"                            # (control) masked
```
`sk-abcdef0123456789ABCDEFxyz` is 24 chars after `sk-` — below the `api_key_openai`
32-char threshold — and no `api_key` key-name assignment pattern existed, so nothing
matched. `api_key=`, `apikey:`, `access_key=`, `api-key =` (all cases) all survived.

## Fix
`gateway/ai_mesh_gateway/patterns.py` — new `api_key_assignment` in `SECRET_PATTERNS`,
mirroring `token_assignment`:
`(?:api[_-]?key|access[_-]?key)["\s]*[:=][\s"\']*` + `_TOKEN_VALUE`.
- Reuses the existing `_TOKEN_VALUE` FP guard (>=8 chars with a digit, not an
  instructional prose word) so `api_key=none` / `api key: forgotten?` / `api_key=`
  (empty) / `DEBUG=true` are NOT masked.
- Tagged `["SECRET"]` in `COMPLIANCE_TAG_MAP` and masked with the existing generic
  `_mask_secret_assignment` (keeps the key name + separator, masks the value → `api_key=***`).
- Case-insensitive (compile_pattern adds IGNORECASE — verified: `password_assignment`
  already matches `PASSWORD=`).

## Verification
- `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_api_key_assignment_redaction.py -q`
  → 13 passed: 6 should-mask (api_key/API_KEY/apikey/access_key/api-key/quoted, all
  `detect_secrets` → `api_key_assignment`), 6 FP-safe (none/prose/empty/DEBUG), and an
  env-dump case masking `API_KEY=` alongside the connection-string password while leaving
  `DEBUG=true`.
- Broad sweep `ai_mesh_gateway/tests` → 1135 passed, 0 failed (no regression from the
  core patterns.py change).

## Note
Provider-specific keys (OpenAI/AWS/Google/Slack/Stripe/GitHub/npm) were already covered
by their own patterns; this closes the generic `api_key=<non-standard-token>` gap and
brings the assignment inventory to parity (password/secret/token + api_key/access_key).
