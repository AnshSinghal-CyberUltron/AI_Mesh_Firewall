# BACKSTOP hardening — more provider secret formats + AWS STS keys (CHG-0072)

- **Item:** G2 item 2 / 1.4 ("field-level redaction of tool RESULTS ... byte-verified, fail-closed").
  Second round of the adversarial secret-format sweep (after CHG-0071).
- **Change-id:** CHG-0072 (2026-07-02)
- **Type:** Secret-detection coverage gap (real credential formats egressed unmasked).

## Gap
A second adversarial sweep of real-world credential formats through `redact_all` found 11 more that
egressed UNMASKED (and were not flagged by detect):
  * **AWS STS temporary access-key id** `ASIA…` — the `aws_access_key` pattern was `\bAKIA[0-9A-Z]{16}\b`
    (long-term keys ONLY); temporary/session credentials start with `ASIA` and leaked;
  * DigitalOcean PAT `dop_v1_…`, Shopify `shp{at,ss,ca,pa}_…`, Square `sq0{atp,csp,idp}-…`,
    Databricks `dapi…`, HashiCorp Vault `hv{s,b}.…`, Figma `figd_…`, Telegram bot `<id>:AA…`,
    PyPI `pypi-…`, Linear `lin_api_…`, Mailgun `key-<32hex>` — none had a pattern.

## Fix — `gateway/ai_mesh_gateway/patterns.py`
- Widened `aws_access_key` (in PII_PATTERNS, detected by `detect_pii`) to `\b(?:AKIA|ASIA)[0-9A-Z]{16}\b`
  so STS temporary keys are caught (AKIA regression preserved).
- Added 10 distinctive-prefix provider tokens to `SECRET_PATTERNS` (detected by `detect_secrets` +
  masked by `redact_all`) + `COMPLIANCE_TAG_MAP` (→ ["SECRET"]):
  `digitalocean_pat`, `shopify_token`, `square_token`, `databricks_token`, `hashicorp_vault_token`,
  `figma_token`, `telegram_bot_token`, `pypi_token`, `linear_api_key`, `mailgun_key`.
- The Telegram pattern allows the optional `bot` URL prefix (`\b(?:bot)?\d{8,10}:AA[A-Za-z0-9_-]{32,}\b`)
  so a token embedded in the `https://api.telegram.org/bot<token>/…` API URL is masked too.

## No false positives
Each token has a fixed provider prefix + length → near-zero FP. Benign battery unchanged: `api-key-value`,
`monkey-`, `shpattern`/`sq0`/`figd` substrings, `robot12345`/`chatbot999` (no `:AA` token), plain prose.

## Verification
- `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_more_provider_secrets.py -q`
  → 18 passed: each new secret is detected under its key + tagged SECRET + masked; ASIA is detect_pii-
  flagged + masked (AKIA regression intact); a Telegram token inside the API URL is masked; benign no-FP.
- Full sweep `ai_mesh_gateway/tests` → 1284 passed, 0 failed.

## Note (detect location)
`aws_access_key` is in PII_PATTERNS (detected by `detect_pii`, tagged SECRET); the 10 new tokens are in
SECRET_PATTERNS (detected by `detect_secrets`). The MCP tier1 scan runs BOTH, so all trigger enforcement.
