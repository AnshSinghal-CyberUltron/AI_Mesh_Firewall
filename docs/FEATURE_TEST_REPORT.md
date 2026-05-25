# AI Mesh Firewall — Feature Test Report

**Date:** 2026-05-25  
**Stack:** control `:8100`, gateway `:8300`, frontend `:8180`

## Credentials

| Field | Value |
|--------|--------|
| Login | `admin@zeroshield.io` |
| Password | `Adm1n!Pass#2024` |

## API smoke (`scripts/feature_smoke_test.sh`)

**15/15 PASS** including:

- Overview + modules 1.1–1.7 data APIs
- Inputs: firewall config, vector providers, MCP servers
- Policies (`?policy_domain=global`), kill-switches, model status
- `GET /api/gateways/public-url/` (firewall SKU endpoint)
- Gateway `/health` (200, `policy_cache_loaded: true`)
- Simulator route reachable (401 without gateway key — auth enforced)

## Playwright (`scripts/playwright_firewall_tabs.mjs`)

**9/9 tabs OK** after `ALLOWED_HOSTS` includes `control` for Vite proxy:

| Tab | Route |
|-----|--------|
| Overview | `?tab=firewall` |
| 1.1 Gateway | `firewall-1-1` |
| 1.2 Policy | `firewall-1-2` |
| 1.3 RAG | `firewall-1-3` |
| 1.4 Context/MCP | `firewall-1-4` |
| 1.5 Multi-model | `firewall-1-5` |
| 1.6 Isolation | `firewall-1-6` |
| 1.7 Output | `firewall-1-7` |
| Inputs | `firewall-config` |

## Fixes applied this session

1. **Firewall-only gateway APIs** — `/api/gateways/public-url/`, `instances/register/`, `instances/telemetry/` (replaces agent distribution URLs).
2. **Frontend** — `useGatewayConfig.js` uses `public-url`.
3. **Removed AIGuardX POC dead code** from `poc_views.py`.
4. **`by_device` policy action** — HTTP 410 (device fleet excluded).
5. **Compose** — `ALLOWED_HOSTS` includes `control` for frontend proxy login.
6. **Workers** — Dockerfile aligns with control deps; `main_app.settings`; Celery autodiscover; workers container **Up**.

## Not yet deep-tested

- Simulator **Run** with seeded gateway API key (expect 200 on auth stage)
- Policy compile → worker queue end-to-end
- RAG ingest / vector index jobs
- MCP server register + tool invoke

## Verdict

**Dev/POC ready** for UI navigation and read APIs. **Not production-ready** until security hardening, simulator E2E with keys, and prod compose secrets.
