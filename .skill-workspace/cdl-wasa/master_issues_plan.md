# CDL WASA Master Issues Plan

Source: `docs/CDL_ZeroShield_AiSecShield_WebApp_Security_Assessment_Report.v1.0.pdf`  
Scope: `https://aisecshield.zeroshield.ai/` | Local verify: `:8180` / `:8100` / `:8300`  
Date: 2026-07-07

## Runtime baseline (pre-fix probes)

| Probe | Result | Evidence |
|-------|--------|----------|
| `GET /api/health/` | 200 | control healthy |
| `GET /api/schema/` unauth | **200** (openapi body) | **GAP #12** — public schema |
| `GET /api/dashboard/summary/` unauth | 401 | dashboard protected |
| Stack | control/gateway healthy ~1h | `docker compose ps` |

## Finding status matrix

| # | Finding | Sev | Status | Action |
|---|---------|-----|--------|--------|
| 1 | Session management (CWE-613) | Medium | **Partial → Fix** | Refresh 7d→1d; access 60m + rotate/blacklist already wired |
| 2 | Django 6.0.3 vulns (CWE-1104) | Medium | **Fix** | Pin `django>=6.0.5,<6.1` in pyproject.toml |
| 3 | nginx 1.24 EOL (CWE-1395) | Medium | **Ops** | OPS_RUNBOOK: upgrade nginx image |
| 4 | OpenSSH vulns (CWE-1395) | Low | **Ops** | OPS_RUNBOOK: upgrade + harden sshd |
| 5 | No rate limit `/api/policies/` (CWE-770) | Low | **Fix** | `PolicyWriteThrottle` on create/update/destroy |
| 6 | Error handling threat-feed (CWE-209) | Low | **Verified** | `safe_exception_handler` → 400 on bad `hours`; add regression test |
| 7 | Missing security headers (CWE-693) | Low | **Verified (prod nginx)** | `deploy/nginx.conf` has HSTS/XFO/CSP; local Vite may differ |
| 8 | Clickjacking (CWE-1021) | Low | **Verified (prod nginx)** | X-Frame-Options + CSP frame-ancestors |
| 9 | RAG/URL validation (CWE-918) | Low | **Verified** | `is_safe_outbound_url` on admin db-test + MCP paths; regression test |
| 10 | CORS `*` (CWE-942) | Low | **Verified** | `CORS_ALLOW_ALL_ORIGINS = DEBUG`; regression when DEBUG=false |
| 11 | Credentials over HTTP (CWE-319) | Low | **Verified (prod)** | `SECURE_SSL_REDIRECT` + HSTS when DEBUG=false |
| 12 | Open API disclosure (CWE-200) | Low | **Fix** | Gate `/api/schema/` + `/docs/` to admin |
| 13 | Weak password policy (CWE-521) | Low | **Fix** | Min 12 + complexity validators |
| 14 | Weak TLS ciphers (CWE-319) | Low | **Ops** | OPS_RUNBOOK: ALB/edge TLS policy |
| 15 | Weak SSH MACs (CWE-327) | Low | **Ops** | OPS_RUNBOOK: sshd MACs/Ciphers |
| 16 | SSH Terrapin (CWE-222) | Low | **Ops** | OPS_RUNBOOK: OpenSSH ≥9.6 |
| 17 | SSL cert expiry (CWE-295) | Info | **Ops** | OPS_RUNBOOK: ACME + 30d alert |

## Agent exploration summaries

See `memory_agent_01.md` … `memory_agent_10.md` for per-area notes (auth, policies, gateway CORS/RAG, deploy, harness).

## Implementation groups (Phase 4)

- **A:** Django pin  
- **B:** Session TTL  
- **C:** Policy write throttle  
- **D:** Schema/docs protection  
- **E:** Password policy 12+  
- **F:** Regression tests (threat-feed, CORS, URL guard)

## Non-goals

- No MCP/pipeline enforcement changes  
- No production deploy in this iteration  
- No git push
