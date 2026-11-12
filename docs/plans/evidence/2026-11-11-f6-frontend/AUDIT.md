# F6 audit of the current console UI (`frontend/`)

Date: 11 Nov 2026. Base: `origin/develop` at the commit this branch starts from. Author: F6 design lead.
Companion document: [DIRECTIONS.md](DIRECTIONS.md) (three visual directions for the owner to pick from).

Everything below is backed by a screenshot, a script output or a `file:line` reference. Severity:
**High** = broken flow, data-trust or security exposure, or blocks the redesign; **Medium** = clear
usability, consistency or accessibility defect; **Low** = polish.

## How this was produced

| Step | Command / tool | Output |
|---|---|---|
| Production build | `npx vite build` (Vite 7.3.3) | bundle sizes below |
| Serve | `vite preview` of that build, proxying `/api` to a local control plane | same proxy table as `vite.config.js` |
| Control plane | Django `runserver` on a **private, freshly migrated database** (`f6_ui_audit` in the shared Postgres), Redis DB 9, one admin user created with `ensure_zeroshield_admin` | real API responses, no traffic, so every page shows its honest empty state |
| Gateway | **not running** (port refused) | shows how pages behave when the gateway is down |
| Screenshots | [`audit/scripts/capture.mjs`](audit/scripts/capture.mjs) (Playwright Chromium, logs in through the real login form) | every view x {desktop 1440x900, mobile 390x844 @2x} x {light, dark} |
| Accessibility | axe-core 4.10.3 injected into each desktop page, both themes | per-page violations below |
| Flows | [`audit/scripts/flows.mjs`](audit/scripts/flows.mjs) | `audit/flows.json` |
| Route to API map | [`audit/scripts/apimap.mjs`](audit/scripts/apimap.mjs) (walks each view's import graph) plus the API calls recorded at runtime | table below |
| Performance | Lighthouse 12.6.1 on `/login` (desktop and mobile presets) | below |

Credentials came from environment variables only; recorded console output has tokens redacted.

## System map

**Stack.** React 19.2, react-router-dom 7, Vite 7 with `@vitejs/plugin-react-swc`, Tailwind CSS 4
(`@theme` tokens in `src/index.css`), class-variance-authority primitives in `src/components/ui/`
(20 components), lucide-react icons, `motion`, charts in ECharts 6 (`echarts-for-react`) and uPlot.
No state library: `AuthContext` (JWT access and refresh tokens in `localStorage`, refresh on 401,
15-minute inactivity logout), `ThemeContext` (`zeroshield_theme` in `localStorage`), a
`FirewallConfigProvider` for the shared firewall config, and per-panel `useState` plus `fetch` with
visibility-aware polling (`startVisibleInterval`, 17 call sites). Notifications arrive over a
WebSocket (`useRealtimeNotifications`).

**Routing.** Four router routes: `/login`, `/signup` (redirects to `/login`), `/oauth/callback`, and
`/` (protected). Everything inside the console is one route; the view is chosen by `?tab=` and two
pieces of in-memory state (`resultsTab`, `logDetailData`) in `App.jsx:50-51`.

| View | How it is reached | Purpose | Control / gateway endpoints (static import graph) | Calls on load (runtime) |
|---|---|---|---|---|
| Login | `/login` | email + password sign-in | `POST /api/auth/token/`, `GET /api/auth/me/` | see capture |
| Shell (header, sidebar) | every console view | navigation, theme, notifications, health | `/api/notifications/` (+ `mark-all-read`, `{id}/read`), `/api/health/`, WebSocket `/ws/notifications/` | `GET /api/health/`, `GET /api/notifications/`, WS |
| Overview | `/` | KPIs, posture, OWASP, trends | `/api/security/{soc-kpis,module-kpis,module-trends,attack-vector-trends,owasp-stats}/`, `/api/policies/{analytics,top-violators}/`, `/api/policy/top-rules/` | 11 GETs, all 200 |
| 1.1 AI Gateway & Traffic Ingress | `?tab=firewall-1-1` | gateway keys, ingress telemetry, attack simulator | `/api/gateways/keys/` (CRUD, `purge-revoked`), `/api/gateways/{public-url,simulator-default}/`, `/api/firewall/{config,models}/`, `POST /v1/chat/completions` | see capture |
| 1.2 Policy Management | `?tab=firewall-1-2` | policies and rules (pipeline, RAG, MCP), compile, analytics | `/api/policies/` (CRUD), `/api/policies/{id}/rules/`, `/api/policies/rules/{id}/`, `/api/policies/compile/` (+ `status/`), `/api/policies/mcp-presets/`, `/api/vector-policies/` (CRUD, `compile/`), analytics endpoints | see capture |
| 1.3 RAG & Vector DB Firewall | `?tab=firewall-1-3` | vector providers, DB connections, collections, ingestion, RAG tests | `/api/vector-providers/` (CRUD), `/api/admin/gateway/{db-test,rag/collections}/`, `/api/security/rag-pipeline-kpis/`, `POST /v1/rag/ingest`, `POST /v1/chat/completions` | see capture |
| 1.4 Context Assembly & MCP | `?tab=firewall-1-4` | MCP servers, tools, scan controls, MCP policies, simulator | `/api/mcp-connector/{servers,tools,tools/call,scan-controls,org-gateway-key,health,events}/...`, `/api/policies/{mcp-server-states,test}/` | see capture |
| 1.5 Multi-Model Governance | `?tab=firewall-1-5` | model connections, routing governance and audit | `/api/firewall/models/` (CRUD), `/api/gateways/keys/`, `/api/security/threat-feed/`, `POST /v1/chat/completions` | see capture |
| 1.6 Model Isolation & Kill-Switch | `?tab=firewall-1-6` | kill switches, model state, circuit breaker | `/api/kill-switches/` (CRUD, `activate`, `deactivate`), `/api/models/{status,isolate,recover,sync,audit-log}/`, `/api/admin/gateway/circuit-breaker/{state,trigger,reset}/`, `/api/gateways/isolation-playground/` | see capture |
| 1.7 Output Guardrails | `?tab=firewall-1-7` | output guard engine, charts, controls, simulator | `/api/security/module-charts/{id}/`, `/api/security/threat-feed/`, `/api/firewall/{config,models}/`, `POST /v1/chat/completions` | see capture |
| Inputs (configuration) | `?tab=firewall-config` | firewall config cards, model and DB connections, guard-model test | `/api/firewall/{config,models}/`, `/api/admin/gateway/{bedrock-test,db-test}/` | see capture |
| Detailed results | button on a module page (no URL) | records table for one module | via `useFirewallData`: `/api/security/{threat-feed,soc-kpis,attack-vector-trends,rag-pipeline-kpis}/`, `/api/gateways/stats/` | |
| Log detail | row in a results table (no URL) | one event | `/api/security/threat-feed/{id}/` | |
| Profile | `?tab=profile` | name, password | `/api/auth/me/profile/`, `/api/auth/change-password/` | see capture |
| Settings | `?tab=settings` | theme, links | none | see capture |

88 distinct endpoint paths are referenced from `src/` (excluding tests).

<!-- CAPTURE-RESULTS -->
