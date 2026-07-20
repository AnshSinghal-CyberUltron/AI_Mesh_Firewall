# CDL WASA Compliance Sign-Off

Date: 2026-07-07  
Verifier: local Docker stack (`:8180` / `:8100` / `:8300`)

| # | Finding | Verdict | Evidence |
|---|---------|---------|----------|
| 1 | Session management | **VERIFIED** | Refresh TTL 1d; access 60m; rotate+blacklist |
| 2 | Django vulns | **VERIFIED** | `django>=6.0.5,<6.1`; container reports 6.0.5 |
| 3 | nginx EOL | **DEFERRED (ops)** | OPS_RUNBOOK.md |
| 4 | OpenSSH | **DEFERRED (ops)** | OPS_RUNBOOK.md |
| 5 | Policy rate limit | **VERIFIED** | `PolicyWriteThrottle` + test 429 |
| 6 | Threat-feed errors | **VERIFIED** | `hours=48'--` → 400, no stack trace |
| 7 | Security headers | **DEFERRED (edge)** | nginx.conf present; Vite dev may omit |
| 8 | Clickjacking | **DEFERRED (edge)** | nginx.conf XFO + CSP frame-ancestors |
| 9 | RAG/URL validation | **VERIFIED** | `test_cdl_wasa_url_guard.py` metadata IP blocked |
| 10 | CORS wildcard | **VERIFIED** | `CORS_ALLOW_ALL_ORIGINS = DEBUG`; regression test |
| 11 | HTTP credentials | **DEFERRED (prod)** | SSL redirect when DEBUG=false |
| 12 | Open API schema | **VERIFIED** | `/api/schema/` + `/docs/` admin-gated |
| 13 | Password policy | **VERIFIED** | Min 12 + complexity validators + tests |
| 14 | TLS ciphers | **DEFERRED (ops)** | OPS_RUNBOOK.md |
| 15 | SSH MACs | **DEFERRED (ops)** | OPS_RUNBOOK.md |
| 16 | SSH Terrapin | **DEFERRED (ops)** | OPS_RUNBOOK.md |
| 17 | Cert expiry | **DEFERRED (ops)** | OPS_RUNBOOK.md |

## Gates run

- `policy.tests.test_cdl_wasa_compliance` — 8/8 OK
- `test_cdl_wasa_url_guard.py` — 1/1 OK
- `scripts/ralph/cdl_wasa_verify.py` — **PASS** (all checks green)
- `scripts/ralph/cdl_wasa_playwright_smoke.mjs` — **PASS** (0 console errors)
- Gateway suite — 2091 passed (1 pre-existing `test_circuit_breaker` flake unrelated to CDL)
- Frontend `npm run lint && npm run build` — green

MCP item-23 freeze not re-run (control-only CDL changes; no `mcp_proxy.py` / pipeline edits).

## Production follow-up

Complete OPS_RUNBOOK.md checklist on `aisecshield.zeroshield.ai` before external reassessment.
