# Triage-reconciled plan (5 parallel agents)

Synthesis after optimist, skeptic, security, QA, and DevOps triage on the standalone `AI_Mesh_Firewall/` extraction.

## Consensus

| Area | Agreement |
|------|-----------|
| Scope | Hybrid split is correct: gateway + trimmed control + firewall frontend + shared jobs |
| Exclusions | No `device/`, `agent_distribution/`, desktop agent MSI, Module 2–5 UI |
| Current state | **POC/dev ready** for 9-tab UI + API smoke |
| Production | **No-go** until P0 items below |

## P0 (do before prod)

1. **Secrets & hosts** — rotate `DJANGO_SECRET_KEY`; set `POLICY_SIGNING_KEY`; tighten `ALLOWED_HOSTS`/`DEBUG`; add `control` only for dev compose.
2. **Gateway agent path** — use `/api/gateways/instances/*` (done); add signing key on workers (warn today).
3. **Login via UI** — Vite proxy requires internal host in `ALLOWED_HOSTS` (done).
4. **Workers** — Celery image must match control `INSTALLED_APPS` and `main_app.settings` (done; verify queues process jobs).

## P1 (quality)

1. **E2E** — Playwright tab sweep (done); extend with simulator Run + gateway key from `/api/gateways/keys/`.
2. **Smoke** — stop counting simulator 401 as full pass; assert 200 with Bearer gateway key.
3. **Naming cleanup** — reduce AIGuardX strings in OpenAPI title, Header help, env var aliases (`AI_MESH_*` shims).
4. **Strip dead code** — `gateway/Dockerfile.monorepo`, offering-only Sidebar branches if firewall-only SKU.

## P2 (nice)

1. Mongo telemetry profile documentation
2. Rust shadow track (parent monorepo plan)
3. Separate prod `docker-compose.prod.yml` without debug defaults

## Documented exclusions

See `docs/EXCLUDED_FROM_COPY.md` and `docs/COPY_PLAN.md`.
