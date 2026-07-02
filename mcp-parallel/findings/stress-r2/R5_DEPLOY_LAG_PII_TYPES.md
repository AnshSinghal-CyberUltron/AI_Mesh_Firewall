# R5 finding — live gateway runs STALE patterns.py; new PII types (MAC/gov-id) not redacted live

**By:** `claude-ralph-stress` · **Date:** 2026-07-02 · Severity: MEDIUM (deployment lag, NOT a code defect)

## What the comprehensive live corpus showed (model=google/gemma-4-31b-it:free)
- **Injections: 18/18 BLOCK live** (plain/homoglyph/small-caps/tag/zero-width/base64/double-b64/
  b64∘zw/b64∘tag/spaced/disregard/forget/override/DAN/reveal/AWS-key/ghp/RAG-inject).
- **Benign: 4/4 allow.**
- **Standard PII redacted, NO leak:** SSN → prompt_preview `My SSN is ***-**-6789` (policy→redact,
  raw absent from egress); email/card likewise. Secrets (AKIA/ghp) blocked.
- **NEW PII types reach the model:** `device 00:1A:2B:3C:4D:5E` → input_scan=allow, policy=allow,
  RAW MAC forwarded to the model. `passport 987654321` → flag (not redact). These are the
  G25 (mac_address, government_id) and G24 (google_api_key/npm_token/aws_secret_access_key) types.

## Root cause: deployment lag (baked image), NOT a code bug
- Gateway container **started 2026-07-02T13:30:59**; image is **baked** (no source bind-mount — only
  npm_cache/uv_cache volumes). So the live gateway's `patterns.py` is frozen at image-build (~13:30).
- My scanner.py injection fixes G17 (13:19) + G19 (13:26) predate 13:30 → **baked in → live** (all
  injections block). My patterns.py PII additions G24 (13:51) + G25 (13:56) postdate 13:30 →
  **NOT in the image** → MAC/gov-id/modern-secrets not detected/redacted live.
- Confirmed in-process (`gateway/.venv`): detect_pii/redact_all DO detect+mask MAC, government_id,
  google_api_key, npm_token, aws_secret_access_key (G24/G25 golden cases green 3×). The code is correct.

## Fix (outside chat-module ownership; requires infra action)
Rebuild + recreate the gateway image so it carries the current gateway source:
`docker compose build gateway && docker compose up -d gateway`. NOT performed here: the gateway is
shared infra used by concurrent sessions and a rebuild mid-run risks breaking it for everyone; a
coordinated redeploy window is the right venue.

## Impact on the COMPLETE promise
"no PII reaches models" (R5) is violated LIVE for the MAC/gov-id types **only because the deployed
image predates my G24/G25 commits**. Core enforcement — every injection class, standard PII
(SSN/email/card), secrets, kill-switch, routing, traces — is validated live and correct. Withholding
COMPLETE honestly: the live system currently leaks a MAC (a device identifier), pending a gateway
redeploy of already-committed, in-process-verified code.
