# BACKSTOP hardening — decode-scan decoy-padding bypass closed (CHG-0060)

- **Item:** G2 / 1.4 ("field-level redaction of tool RESULTS — byte-verified, fail-closed").
  Closes the residual I explicitly deferred in CHG-0056.
- **Change-id:** CHG-0060 (2026-07-02)
- **Type:** Real redaction leak (obfuscation decode-scan bypass), fail-closed fix.

## Gap (the CHG-0056 residual, now proven live)
`_redact_obfuscated` de-obfuscates base64/hex (G2) and URL/percent (CHG-0056) blobs, but each
decode pass stopped after a fixed token COUNT: base64/hex `_MAX_DECODE_TOKENS = 12`, url
`_MAX_URL_DECODE_TOKENS = 32`. So a tool result could hide an encoded secret PAST the cap —
`<12 benign base64 blobs> <base64("john.doe@example.com")>` — and the 13th token (the secret) was
never decoded, so it egressed verbatim, trivially recoverable by any base64 decoder.

Proven live BEFORE the fix (gateway `redact_all`):
- 20 benign base64 decoys + `base64(email)` → email token NOT masked, present in output (LEAK).
- Same for hex past 12 decoys and URL-encoding past 32 percent-tokens.

This is NOT purely adversarial: any legitimate result with >12 base64 fields where a later field
carries encoded PII would leak.

## Fix
`gateway/ai_mesh_gateway/patterns.py`:
- The decode scan is now bounded by a GLOBAL decoded-BYTE budget (`_MAX_DECODE_TOTAL_BYTES =
  262144`), shared across the base64 + hex passes, instead of a per-pass token COUNT. The input is
  already capped at `_CANON_MAX_LEN = 20000`, so decoding EVERY token in it is inherently bounded
  work; the byte budget is the real DoS bound (caps total decoded bytes incl. nested layers).
- `_MAX_DECODE_TOKENS` raised 12 → 4096 (a high backstop on the number of detect() calls, above the
  max tokens a 20K input can hold — ~1666 base64 — so it never truncates a valid-length input).
- `_MAX_URL_DECODE_TOKENS` raised 32 → 4096, and the URL pass now scans `original[:_CANON_MAX_LEN]`
  (was the full untruncated `original`).
- `_iter_transport_decodes` decrements the budget per decoded blob (top-level + each nested layer)
  and stops when exhausted. `_iter_short_b64_infra` (CHG-0058) inherits the raised token cap.

## No regression / no false positives
- Benign short input is a byte-for-byte no-op (no base64 tokens) — the frozen golden cases and all
  existing redaction outputs are unchanged (only inputs with >12 encoded tokens change: they now get
  ALL tokens decode-scanned, which is the fix).
- Scanning more tokens only masks GENUINE decoded PII/secret/infra — a battery of 60 benign base64
  decoys (decoding to non-sensitive text) is NOT tagged `[ENCODED_SECRET_REDACTED]`.
- Perf worst case (a full 20K scan-window of base64 tokens): ~35–45 ms (well within bound; the test
  asserts < 2 s to guard against a decode-scan DoS regression).

## Verification
- `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_decode_budget_no_decoy_bypass.py -q`
  → 7 passed (base64/hex/URL secret hidden past the old caps now masked; many-decoys-within-window
  masked; benign decoys not false-masked; plain + single-encoded still work; bounded-fast).
- Full sweep `ai_mesh_gateway/tests` → 1214 passed, 0 failed.
- End-to-end reach: tier1 (`_scan_text_tier1`) masks with `redact_all` from `patterns.py`, so this
  protects real MCP tool-result egress (see CHG-0057). The CHG-0057 byte-verify guard also fails
  closed if a detected value survives the scrub.

## Residual (documented, pre-existing, NOT changed)
Content beyond `_CANON_MAX_LEN = 20000` chars is not obfuscation-decode-scanned (the input-length
cap, tied to the upstream 10K prompt bound). Plain (un-encoded) PII beyond that offset IS still
masked by the raw pass; only ENCODED content past 20K escapes the decode scan. Raising
`_CANON_MAX_LEN` is a module-wide perf/DoS decision out of this change's scope.
