# BACKSTOP finding — private-key BODY survives redaction (CHG-0054)

- **Item:** G2 item 2 ("field-level redaction of tool RESULTS — byte-verified, fail-closed") / 1.4.
- **Change-id:** CHG-0054 (2026-07-02)
- **Severity:** HIGH — a PEM private key in a tool RESULT was "redacted" but the actual
  secret material (the base64 body) egressed intact.
- **Discovery:** adversarial 1.4 verification (mandate: "aidefence_scan as a leak oracle").

## Title
`redact_all` masked only the `-----BEGIN … PRIVATE KEY-----` header line of a PEM
private key (→ `[PRIVATE_KEY]`), leaving the base64 KEY BODY + `-----END-----` marker
intact. The body is the actual secret; `[PRIVATE_KEY]` is trivially replaced with the
fixed BEGIN line to reconstruct the full key. And the old pattern only matched **RSA**
keys — EC/DSA/OPENSSH keys were not matched at all, so the whole key egressed raw.

## Reproduction (byte-truth)
`redact_all("-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA…secretmaterial\n-----END RSA PRIVATE KEY-----")`
→ `"[PRIVATE_KEY]\nMIIEpAIBAAKCAQEA…secretmaterial\n-----END RSA PRIVATE KEY-----"`.
The body (`MIIEpAIBAAKCAQEA…`, `secretmaterial`) SURVIVED.

## Root cause
In `_redact_all_raw`, `PII_PATTERNS` (which contains `private_key_header`) is applied
FIRST and masks the BEGIN header. The later `private_key_block` pattern (in
`CREDENTIAL_EXPOSURE_PATTERNS`) also matched only the BEGIN line — and by then it was
already gone — so nothing ever matched the multi-line body. Both patterns were
header-only regexes; neither consumed the key material.

## Oracle note
`aidefence_scan` / `aidefence_has_pii` return `piiFound: false` on BOTH the raw key AND
the redacted output — the AIMDS oracle has no PEM-private-key recognizer, so it is NOT a
substitute oracle here. Confirmation is via the gateway's OWN `detect_pii` (which DOES
flag `private_key_header`, so the result IS scanned + "redacted") + byte inspection.

## Fix
`gateway/ai_mesh_gateway/patterns.py` — `private_key_header` now matches the ENTIRE PEM
block: `-----BEGIN\s+(?:[A-Z0-9]+\s+)?PRIVATE\s+KEY-----(?:[\s\S]*?-----END\s+(?:[A-Z0-9]+\s+)?PRIVATE\s+KEY-----|[A-Za-z0-9+/=\s]*)`.
- Generic `(?:[A-Z0-9]+\s+)?` prefix covers RSA / EC / DSA / OPENSSH / ENCRYPTED / plain
  (old pattern was RSA-only).
- The `…-----END…` alternative masks a complete block; the `[A-Za-z0-9+/=\s]*` fallback
  consumes the base64 body of a truncated key (BEGIN with no END) so material still
  can't survive.
The masker (`[PRIVATE_KEY]`) now replaces the whole key.

## Verification
- `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_private_key_redaction.py -q`
  → 5 passed: RSA body fully masked; EC key (old pattern missed it) masked with
  surrounding text preserved; OPENSSH masked; truncated key body masked; prose
  mentioning "a private key" NOT redacted (no false positive).
- Redaction-adjacent suites (bare-phone / output-guardrails / input-detail-masking /
  break-regressions / scan-orchestrator) → 74 passed. Broad sweep `ai_mesh_gateway/tests`
  → 1122 passed, 0 failed.

## Follow-up (documented)
- The gateway `detect_secrets` inventory does NOT include private keys (they live in
  `PII_PATTERNS`); `detect_pii` covers them, so detection holds — but the two secret
  inventories could be unified (cross-plane, owning-session decision; cf. test_e14_grounding
  fix_hint for `_RESIDUAL_PII_PATTERNS` in the RAG embedder).
