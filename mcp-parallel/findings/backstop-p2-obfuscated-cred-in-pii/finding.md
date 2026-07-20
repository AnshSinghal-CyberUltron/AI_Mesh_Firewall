# CHG-0083 — obfuscated AWS/GitHub/OpenAI credentials (misfiled in PII_PATTERNS) bypassed the encoded-exfil block

**Change-id:** CHG-0083
**Date:** 2026-07-02
**Severity:** HIGH (a malicious upstream text-encodes / zero-width-hides an AWS access key etc. → the encoded-exfil block missed it → the credential reaches the model deobfuscated, past the firewall)
**Area:** HARDEN 1.4 — obfuscation-bypass completeness (CHG-0076/0079 lineage)
**Files:** `gateway/ai_mesh_gateway/mcp_scan_orchestrator.py` (+ `tests/test_mcp_obfuscated_cred_in_pii.py`)
**Whose work it touches:** the MCP scan orchestrator tier-1 encoded/unicode-exfil probe.

## How it was found (a durable adversarial matrix pre-flight)

Building a comprehensive category × obfuscation regression matrix through the real floor, the STRICT
variant (obfuscated secret/credential/internal-IP → MUST block) flagged: **obfuscated `aws_access_key`
(`AKIAIOSFODNN7EXAMPLE`) via HTML-entity AND zero-width did NOT block**, while its raw form masks.

## Gap

The CHG-0076 (text-encoding: HTML-entity / percent / `\u`) and CHG-0079 (invisible/confusable-unicode)
encoded-exfil BLOCK computes a `_hidden` set from `detect_secrets` + `detect_credential_exposure` +
`detect_ip_leakage ∩ _INFRA_NETWORK_KEYS`. But **several CREDENTIALS live in `PII_PATTERNS`** (detected by
`detect_pii`, NOT `detect_secrets`): `aws_access_key` (AKIA/ASIA), `aws_secret_access_key`,
`api_key_openai`, `github_token`, `private_key_header`. So an OBFUSCATED AWS/GitHub/OpenAI key slipped past
the block — the raw regexes don't match the obfuscated bytes, the decode/deobfuscate reveals the key, but
the `_hidden` probe never ran `detect_pii` on the decoded variant. A markdown/model client then reads the
deobfuscated credential.

## Fix

The encoded-exfil `_hidden` probe now also includes **decoded `detect_pii` matches whose compliance tag is
`SECRET`** — the credentials-misfiled-as-PII set (aws_access_key / aws_secret_access_key / api_key_openai /
github_token / private_key_header, all tagged SECRET). Generic PII (email/phone/ssn/cc: tags
GDPR/PII/HIPAA/PCI-DSS, never SECRET) is deliberately EXCLUDED — an entity-encoded scraped-HTML contact
email/SSN must not false-block a legit web/HTML tool result (consistent with CHG-0076's PII exclusion). The
`SECRET`-tag filter is the principled, near-zero-FP boundary.

## Behaviour after fix (verified)

- Obfuscated (HTML-entity + zero-width) `AKIA`/`ASIA`/`ghp_…`/`sk-proj-…` → **BLOCK** (was egress).
- Obfuscated generic PII (email / SSN / phone) → **NOT blocked** (FP guard holds).
- Raw AWS key → still masked (no regression).
- Durable matrix: 12 sensitive categories × raw → masked/blocked; benign (weather / a /home path / a
  version string) → unchanged.

## Verify

```
cd gateway
.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_obfuscated_cred_in_pii.py -q   # 27 passed
.venv/bin/python -m pytest ai_mesh_gateway/tests -q                                       # 1369 passed, 0 failed
```
Broker regression: `services/mcp-broker && .venv/bin/python -m pytest tests -q -k "not websocket"` → 108 passed.

## Residual / follow-ups
- Root cause is that `aws_access_key` etc. live in PII_PATTERNS (they are credentials). A future cleanup
  could move them to SECRET_PATTERNS, but that touches detect_secrets/detect_pii semantics + the tag map
  across other sessions — the SECRET-tag filter here fixes the leak without that churn.
- Obfuscated SSN / credit-card (regulated PII) are still NOT blocked by the encoded-exfil path (only
  SECRET-tagged). Their raw forms mask; obfuscated regulated-PII blocking is a separate FP-weighed decision.
