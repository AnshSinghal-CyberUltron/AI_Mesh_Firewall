# BACKSTOP hardening — provider secret formats added to SECRET_PATTERNS (CHG-0071)

- **Item:** G2 item 2 / 1.4 ("field-level redaction of tool RESULTS ... byte-verified, fail-closed").
- **Change-id:** CHG-0071 (2026-07-02)
- **Type:** Secret-detection coverage gap (real credential formats egressed unmasked), found by an
  adversarial `redact_all` secret-format sweep.

## Gap
An adversarial sweep of ~20 real-world credential formats through the gateway's own `redact_all` found
FOUR that egressed UNMASKED and were NOT flagged by `detect_secrets`:
  * **Anthropic API key** `sk-ant-api03-…` — the OpenAI `sk-` family (`api_key_openai`) was caught, but
    the `ant` provider was not in the alternation and the `sk-[A-Za-z0-9]{32,}` fallback fails on the
    hyphens in `sk-ant-…`;
  * **SendGrid API key** `SG.<seg>.<seg>` — no pattern;
  * **GitLab PAT** `glpat-…` — no pattern (GitHub PATs were covered, GitLab was not);
  * **Slack incoming-webhook URL** `https://hooks.slack.com/services/T…/B…/…` — a secret URL, no pattern.
Such a credential in an MCP tool RESULT would egress verbatim to the LLM/client.

## The subtle part (detect vs. redact)
It is NOT enough to add these to `CREDENTIAL_EXPOSURE_PATTERNS` (the RAG-ingest master inventory) — that
makes `redact_all` MASK them but does NOT make `detect_secrets` FLAG them. The MCP tier-1 scan
(`_scan_text_tier1`, CHG-0057) uses `detect_secrets`/`detect_pii`/`detect_ip_leakage` to DECIDE whether
to redact/block/tag and only then calls `redact_all`. So if `detect_secrets` returns nothing for a
result whose ONLY sensitive content is one of these keys, NO enforcement fires and the key egresses raw
(the redact floor is never triggered). The patterns MUST live in `SECRET_PATTERNS` (which both
`detect_secrets` via `_detect_secrets_core` AND `redact_all` via `_redact_all_raw` iterate).

## Fix — `gateway/ai_mesh_gateway/patterns.py`
Added to `SECRET_PATTERNS` (the detect+redact inventory) + `COMPLIANCE_TAG_MAP` (→ ["SECRET"]):
```
anthropic_key : \bsk-ant-[A-Za-z0-9_-]{20,}\b
sendgrid_key  : \bSG\.[A-Za-z0-9_-]{16,32}\.[A-Za-z0-9_-]{32,}\b
gitlab_pat    : \bglpat-[A-Za-z0-9_-]{20,}\b
slack_webhook : https://hooks\.slack\.com/services/[A-Za-z0-9/_+-]+
```
All four are highly specific prefixes/structures → near-zero false-positive. The default masker names
the mask by the pattern key (`[ANTHROPIC_KEY_REDACTED]`, etc.), no explicit masker needed.

## No false positives
Benign strings unchanged: `SG.short.short` (segments too short), `glpatterns`/`sglist` substrings,
`hooks.slack.com/help` (not a `/services/` webhook), `sk-ant` with no body, plain prose. Full gateway
sweep unaffected (no golden-case change).

## Verification
- `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_provider_secret_formats.py -q`
  → 10 passed: each of the 4 formats is detect_secrets-flagged under its key, tagged SECRET, and masked
  by redact_all (raw value does not survive); no false positive on a benign battery.
- Full sweep `ai_mesh_gateway/tests` → 1266 passed, 0 failed.
- End-to-end reach: tier1 (`_scan_text_tier1`) detects via `detect_secrets` + masks via `redact_all`,
  both from `patterns.py`, so these formats are now enforced on real MCP tool-result egress (CHG-0057).
