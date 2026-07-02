# CHG-0076 — MCP scan lacked the text-encoding obfuscation check → encoded secret/IP exfil

**Change-id:** CHG-0076
**Date:** 2026-07-02
**Severity:** MEDIUM–HIGH (a malicious upstream MCP server can exfil a stolen credential / internal IP past the firewall by text-encoding it; a markdown/HTML client decodes it back)
**Area:** HARDEN 1.4 — field-level redaction / block of tool RESULTS + inbound ARGS (fail-closed), obfuscation-bypass parity
**Files:** `gateway/ai_mesh_gateway/mcp_scan_orchestrator.py` (+ `tests/test_mcp_encoded_exfil.py`)
**Whose work it touches:** the MCP scan orchestrator tier-1 (CHG-0074/0075 lineage); reuses the chat scanner's `_decode_text_encoding_variants`.

## How it was found (devil's-advocate — parity with the chat scanner)

The chat OUTPUT scanner (`scanner._scan_output_sync`, G33/G35) decodes text-encoding variants
(`_decode_text_encoding_variants`) so an HTML-entity / percent / `\u`/`\x`-escaped PII/secret a
markdown/browser client would render is caught. Does the MCP orchestrator have parity? It did NOT — its
tier-1 (`_scan_text_tier1`) ran the raw `detect_pii/secrets/ip_leakage/credential_exposure` only.

## Gap

`detect_secrets` folds base64/hex transport, but a SECRET / CREDENTIAL / INTERNAL NETWORK IP hidden by a
TEXT-encoding dodges the raw regexes. `redact_all` does NOT mask an encoded run. So an encoded credential /
internal IP in a tool RESULT egressed, and a markdown/HTML MCP client (Claude Desktop et al.) decodes it
back to the real value — a laundering/exfil channel for an untrusted external MCP server. Same class in
tool ARGS (a tenant smuggling a secret to an upstream).

Empirically (real MCP result floor, default `tag` posture):

| input | before |
|---|---|
| `&#115;&#107;...` (HTML-entity `sk-ant-…`) | **not flagged → egressed** |
| `sk%2Dant%2D…` (percent-encoded) | **not flagged → egressed** |
| `&#49;&#48;&#46;…` (HTML-entity `10.0.0.5`) | **not flagged → egressed** |

The chat path caught all of these; the MCP path did not.

## Fix

Added an encoded-exfil check to `_scan_text_tier1` (after the raw detect branch, before return): decode
`_decode_text_encoding_variants(text)`; if a decoded variant reveals a **secret / credential /
internal-NETWORK IP** the raw text lacked → append a `threat_type="secret"` finding and **BLOCK**
(fail-closed) under any non-`monitor` posture, because `redact_all` cannot mask the encoded run. Mirrors the
chat INPUT path (`scanner._scan_prompt_sync`) and the orchestrator's own byte-verify block.

**Scoping (FP control):** generic PII is deliberately EXCLUDED — a scraped HTML page's entity-encoded
contact email is usually benign public content, and blocking the whole result would break legitimate
web/HTML tool results. Secrets/credentials/internal-IPs have no legitimate reason to be text-encoded, so
blocking them is near-zero FP. File paths (flag-tier) are also excluded (only `_INFRA_NETWORK_KEYS` IPs
count). Applies to input + output. For plain text with no encodings, `_decode_text_encoding_variants`
returns `[]` so the loop never runs (negligible overhead).

## Behaviour after fix (byte-level, real floor)

- HTML-entity / percent-encoded secret or internal IP in a RESULT → **BLOCK** (was egress).
- Encoded secret smuggled in ARGS → **BLOCK**.
- Encoded generic PII (email) → **not blocked** (scraped-HTML safe).
- Raw secret → still redacted (floored), not blocked (no regression).
- Benign HTML entities (`&amp;`, `&#8212;`, `&lt;`) / plain prose / URLs → not blocked (no FP).

## Verify

```
cd gateway
.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_encoded_exfil.py -q   # 9 passed
.venv/bin/python -m pytest ai_mesh_gateway/tests -q                              # 1327 passed, 0 failed
```
Broker regression (skip pre-existing ws hangs): `services/mcp-broker && .venv/bin/python -m pytest tests -q -k "not websocket"` → 108 passed.

## Residual / follow-ups
- Encoded GENERIC PII in results is intentionally NOT blocked (FP trade-off for web/HTML tools). If a
  future requirement is to mask (not block) encoded PII on the MCP path, port `output_guard.
  neutralize_encoded_pii` into a result-sanitizer stage.
- base64/hex transport already folded by detect_secrets/detect_pii (unchanged).
