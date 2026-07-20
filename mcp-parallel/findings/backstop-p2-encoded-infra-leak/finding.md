# BACKSTOP hardening — encoded internal network address leak closed (CHG-0058)

- **Item:** G2 / 1.4 ("field-level redaction of tool RESULTS — byte-verified, fail-closed").
- **Change-id:** CHG-0058 (2026-07-02)
- **Type:** Real redaction leak (adversarial obfuscation bypass), fail-closed fix.

## Gap (adversarially found)
`redact_all` de-obfuscates base64/hex (G2) and URL/percent (CHG-0056) encoded blobs, but
the decode branches in `_redact_obfuscated` checked only `_detect_pii_core` /
`_detect_secrets_core` — NOT `detect_ip_leakage`. So an INTERNAL network address hidden
inside an encoded blob survived scrubbing and egressed on an MCP tool result:

- `base64("db.internal:5432")`, `base64("http://192.168.50.123:8080/admin")`,
  `base64("postgres.svc.cluster.local")` → all forwarded verbatim (recoverable by any
  base64 decoder).
- `%`-encoded `http://192.168.50.123:8080/admin` (URL query param) → forwarded verbatim.

Byte-truth confirmation: `redact_all(<blob>)` returned the encoded token unchanged (the
decoded internal address is trivially recoverable). aidefence has no internal-IP/host
recognizer, so confirmation is by the gateway's own `detect_ip_leakage` over the decoded
bytes + raw byte inspection (the encoded token still present in the "redacted" output).

## Secondary gap (found during fix verification)
The shared base64 gate `_B64ISH_RE` requires `{12,}` base64 chars (~>=9 decoded bytes).
A BARE short internal IPv4 whose string form is <=8 bytes (`10.1.2.3` → `MTAuMS4yLjM=`,
11 base64 chars) falls just under the gate, so a lone short internal IP could still egress
base64-encoded even after the primary fix. (Hex-encoded short IPs are already covered: 8
bytes = 16 hex chars >= the `{8,}` hex floor.)

## Fix
`gateway/ai_mesh_gateway/patterns.py`:
1. `_INFRA_NETWORK_KEYS = ("internal_ipv4","internal_hostname","internal_url")` +
   `_dec_has_infra(s)` helper (True iff a decoded blob carries an internal NETWORK
   address — scoped to network keys; file-path leak types are excluded, matching
   `redact_all`'s own masking scope + avoiding FP-prone encoded paths).
2. `_dec_has_infra(dec)` / `_dec_has_infra(dcanon)` added to the base64/hex decode branch
   AND `_dec_has_infra(dec)` to the CHG-0056 URL-decode branch in `_redact_obfuscated` —
   an encoded internal address now masks the whole token as `[ENCODED_SECRET_REDACTED]`.
3. `_SHORT_B64_RE` + `_iter_short_b64_infra(text)`: a dedicated SHORT-token pass (maximal
   run of 8..11 base64 chars — the band the main gate misses) that decodes and masks ONLY
   when the result is an internal network address. Deliberately network-key-only (NOT the
   full PII/secret suite), so it changes nothing about `detect_pii`/`detect_secrets` and
   only tightens the fail-closed redaction path. Look-behind/look-ahead pin it to maximal
   runs so 12+ char tokens (handled by the main pass) are never partially re-matched.
   Token count bounded by `_MAX_DECODE_TOKENS` (decode-bomb safe).

## No false positives
- The `detect_ip_leakage` example/textbook carve-out (`_IP_LEAKAGE_EXAMPLE_ADDRS`:
  192.168.0.1, 10.0.0.1, …) is preserved on the decode path: encoding a textbook address
  does NOT trigger a false redaction.
- Encoded file paths are out of scope (not masked by `redact_all` in plain form either).
- FP battery over benign short-base64 text (`b64('hello')`, `b64('secret')`,
  `b64('User123')`, 8-char garbage tokens, plain English, order/sha ids) → ZERO changed.

## Verification
- `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_encoded_infra_redaction.py -q`
  → 13 passed (base64 + hex + URL encoded internal IP/host/URL masked; example addrs +
  encoded file path NOT masked; plain internal IP still uses normal masker; base64
  PII/secret still masked).
- Full sweep `ai_mesh_gateway/tests` → 1176 passed, 18 skipped, 7 xfailed, 2 xpassed,
  0 failed (the other session's `test_mcp_enforcement_block_recording.py` now collects &
  passes — no longer excluded).
- End-to-end reach: tier1 (`_scan_text_tier1`) masks with `redact_all` from `patterns.py`,
  so this fix protects real MCP tool-result egress (see CHG-0057). The byte-verify guard
  (CHG-0057) now also unions `ip_leak.values()`, so a surviving internal address fails
  closed → block.
