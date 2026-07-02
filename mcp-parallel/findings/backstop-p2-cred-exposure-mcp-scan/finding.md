# CHG-0075 — MCP tier-1 scan omitted detect_credential_exposure → Stripe/Twilio/Azure/conn-string egressed raw

**Change-id:** CHG-0075
**Date:** 2026-07-02
**Severity:** HIGH (a whole credential class egressed RAW in MCP tool RESULTS + passed unblocked in tool ARGS to untrusted upstreams)
**Area:** HARDEN 1.4 — field-level redaction of tool RESULTS (byte-verified) + compliance tagging + arg credential force-block
**Files:** `gateway/ai_mesh_gateway/mcp_scan_orchestrator.py`, `gateway/ai_mesh_gateway/patterns.py` (+ `tests/test_mcp_credential_exposure_scan.py`)
**Whose work it touches:** the MCP scan orchestrator tier-1 + the detection/redaction core (owning-session files); same wrong-dict class as CHG-0071.

## How it was found (devil's-advocate — completeness of the detect_* set)

After CHG-0074 wired IP leakage into the result floor, the next question: does the MCP tier-1 scan run
EVERY detector, or only some? `mcp_scan_orchestrator._scan_text_tier1` ran `detect_pii` + `detect_secrets`
+ `detect_ip_leakage` — but **NOT `detect_credential_exposure`**.

## Gap

`CREDENTIAL_EXPOSURE_PATTERNS` is a SEPARATE dict from `SECRET_PATTERNS`:
`bearer_token, basic_auth, exposed_password, connection_string, private_key_block, github_fine_grained_pat,
stripe_key, azure_storage_key, twilio_api_key, gcp_service_account_key, slack_token, jwt`. `detect_secrets`
does NOT read it; only `detect_credential_exposure` does. `redact_all` masks it — but the MCP tier-1 scan
uses `detect_*` to DECIDE enforcement, then `redact_all` to mask. So a credential whose ONLY match is a
CREDENTIAL_EXPOSURE kind was **never detected**, drove no enforcement, and egressed RAW.

Empirically (`detect_secrets` vs `detect_credential_exposure`, then end-to-end via the real result floor at
the default `tag` posture):

| credential | detect_secrets | before (result floor) |
|---|---|---|
| Stripe `sk_live_…` | ✗ miss | **raw LEAK** |
| Twilio `SK…32hex` | ✗ miss | **raw LEAK** |
| Azure `AccountKey=…` | ✗ miss | **raw LEAK** |
| DB `postgres://admin:pass@…` | ✗ miss | password leaked (only an incidental embedded internal IP got masked) |
| bearer / jwt / slack | ✓ (also in SECRET_PATTERNS) | already floored |

Same class in tool ARGS: `_findings_have_credential` never saw these (no finding at all), so a tenant's
Stripe/Twilio/Azure key in tool args flowed to a (possibly external / untrusted) upstream MCP server
unblocked.

## Second omission (part B) — missing compliance tags

7 CREDENTIAL_EXPOSURE keys had NO `COMPLIANCE_TAG_MAP` entry (`get_compliance_tags` → `[]`):
`github_fine_grained_pat, stripe_key, azure_storage_key, twilio_api_key, gcp_service_account_key,
slack_token, jwt`. So even once detected they would not be tagged `SECRET` → breaks enforce-by-tag +
audit, and the arg force-block's SECRET-tag fast path.

## Fix

1. `mcp_scan_orchestrator._scan_text_tier1`: import + call `detect_credential_exposure(text)`; fold it into
   the detect branch — `kinds`, `matched_kinds`, the byte-verify `_detected_values`, and threat precedence
   `pii > secret/credential > ip_leakage` (a credential exposure is `threat_type="secret"` so it drives the
   result redact floor AND the arg credential force-block).
2. `patterns.COMPLIANCE_TAG_MAP`: added the 7 missing keys → `["SECRET", "SOC2"]` (matching the existing
   credential-exposure family).

## Behaviour after fix (byte-level, real paths)

- Stripe / Twilio / Azure / connection-string / GCP-SA in a tool RESULT → **masked** (floored), tagged
  `SECRET, SOC2` (was raw).
- Same in tool ARGS → **force-blocked** (was forwarded).
- Bearer / jwt / slack unchanged (already floored). Benign prose unaffected (no FP).

## Verify

```
cd gateway
.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_credential_exposure_scan.py -q   # 11 passed
.venv/bin/python -m pytest ai_mesh_gateway/tests -q                                          # 1327 passed, 0 failed
```
Broker regression (skip pre-existing ws hangs): `services/mcp-broker && .venv/bin/python -m pytest tests -q -k "not websocket"` → 108 passed.

## Residual / follow-ups
- Detection now uniform across the tier-1 detect_* set (pii/secret/credential/ip). If a future dedicated
  detector is added (e.g. detect_phi/detect_pci), the same wiring check applies.
- `stripe_key` tagged SECRET/SOC2 (not PCI-DSS) for family consistency; revisit if payment-scope tagging
  is desired.
