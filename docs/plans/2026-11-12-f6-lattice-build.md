# F6 build plan: Lattice console and public landing page

| | |
|---|---|
| Status | **Plan for review.** No `frontend/` code in this PR. Feature PRs start only after two plan reviewers approve (owner rule). |
| Date | 12 Nov 2026 |
| Base | `develop` at `562619c8` (code facts below are read at this commit) |
| Design input | PR #123, branch `plan/f6-frontend-directions` at `2e1b6f09`: Lattice mockups, tokens, sample data, `DIRECTIONS.md`, PNGs. The owner approved Lattice on 12 Nov. |
| Evidence | [`evidence/2026-11-12-f6-lattice-build/`](evidence/2026-11-12-f6-lattice-build/) |

Mockup permalinks (PR #123 is not merged, so links are pinned to its head):
[console](https://github.com/CyberUltron-Consulting-Private-Limited/AI_Mesh_Firewall/blob/2e1b6f096b280038990477e9cfff7636a779fb74/docs/plans/evidence/2026-11-11-f6-frontend/directions/mockups/lattice/console.html),
[request](https://github.com/CyberUltron-Consulting-Private-Limited/AI_Mesh_Firewall/blob/2e1b6f096b280038990477e9cfff7636a779fb74/docs/plans/evidence/2026-11-11-f6-frontend/directions/mockups/lattice/request.html),
[keys](https://github.com/CyberUltron-Consulting-Private-Limited/AI_Mesh_Firewall/blob/2e1b6f096b280038990477e9cfff7636a779fb74/docs/plans/evidence/2026-11-11-f6-frontend/directions/mockups/lattice/keys.html),
[policy](https://github.com/CyberUltron-Consulting-Private-Limited/AI_Mesh_Firewall/blob/2e1b6f096b280038990477e9cfff7636a779fb74/docs/plans/evidence/2026-11-11-f6-frontend/directions/mockups/lattice/policy.html),
[landing](https://github.com/CyberUltron-Consulting-Private-Limited/AI_Mesh_Firewall/blob/2e1b6f096b280038990477e9cfff7636a779fb74/docs/plans/evidence/2026-11-11-f6-frontend/directions/mockups/lattice/landing.html),
[tokens.css](https://github.com/CyberUltron-Consulting-Private-Limited/AI_Mesh_Firewall/blob/2e1b6f096b280038990477e9cfff7636a779fb74/docs/plans/evidence/2026-11-11-f6-frontend/directions/mockups/lattice/tokens.css),
[tokens.mjs](https://github.com/CyberUltron-Consulting-Private-Limited/AI_Mesh_Firewall/blob/2e1b6f096b280038990477e9cfff7636a779fb74/docs/plans/evidence/2026-11-11-f6-frontend/directions/mockups/tokens.mjs).

## 0. Summary

Lattice is built as a new console shell at real URLs under `/console/…`, inside the existing React 19 + Vite 7 app, behind a build flag until a route-level cutover. Legacy pages are mounted in the new shell from the second slice on, so every view gets a URL early (A-02, A-03) and is then rebuilt page by page. The public landing page is a separate, prerendered static entry in the same Vite project. It shares the token file and fonts and is served at `/` only at cutover.

The mockup's 16 overview boxes name 14 sources. Measured against `develop`, **only 6 boxes can be fully sourced today**. Eight are partial, and 2 have no source at all: firewall overhead and gateway fleet. The enforcement log's "dropped" count has none either. Four source lines in the mockups name the wrong endpoint, for example `health` for the fleet and `policies/analytics` for verdicts, and one names a field instead of an endpoint. The gaps become 15 backend issues (§6.4). A box ships only when its source exists; no number on screen is derived, padded or invented.

Delivery takes 11 PRs after this plan (§8). Each slice has its own acceptance criteria, Playwright e2e on Chromium, Firefox and WebKit, visual baselines (1440x900, 834x1112, 390x844; light and dark), axe, and UI = API = DB agreement tests on a seeded database. All of it runs in a new `frontend-e2e` CI job on GitHub runners.

## 1. Owner brief (binding, as relayed on 12 Nov)

- Lattice: "inspired by AI-DSPM, own identity". Violet accent `#A48CFF` (dark) / `#6B4FE0` (light); Geist and Geist Mono.
- The overview is many small boxes side by side. Each box has its own purpose and a source and freshness line.
- **No** notification bell. **No** notification or activity feed. **No** "needs attention" lists. **No** tab strips of small views.
- Rendered visual output is the owner's quality gate.
- Process: plan PR first, two plan reviewers, then feature PRs.

These become automated checks (§7.4), not just review items.

## 2. Discovered architecture (develop `562619c8`)

### 2.1 Frontend

| Area | Fact | Evidence |
|---|---|---|
| Stack | React 19.2, react-router-dom 7, Vite 7.3.3, Tailwind 4 (`@theme` in CSS, no `tailwind.config.js`), CVA primitives in `components/ui/` (20), lucide, motion, ECharts 6 (+ `echarts-for-react`), uPlot | `frontend/package.json` |
| Size | 47,624 lines of `.js`/`.jsx` in `src/`; the biggest files are `MCPConnectorPanel.jsx` (2,739), `PolicyManagementPanel.jsx` (2,060) and `MCPManagerPanel.jsx` (1,955) | `counts.txt` |
| Routing | 4 router routes: `/login`, `/signup`, `/oauth/callback` and `/` (protected). The view comes from `?tab=` plus two in-memory states (`resultsTab`, `logDetailData`) | `App.jsx:50-51,97-110,184-200` |
| State | No store library. `AuthContext` (JWT access and refresh in `localStorage`, refresh mutex, 15-minute inactivity logout), `ThemeContext`, `FirewallConfigProvider`, per-panel `useState` + `fetch` | `context/*.jsx`, `DashboardLayout.jsx:17-20` |
| Polling | `startVisibleInterval` (skips ticks while the document is hidden): overview every 10 s, `useFirewallData` every 15 s, simulators every 3 s | `utils/visiblePoll.js:4-9`, `AIMeshFirewallOverview.jsx:686`, `useFirewallData.js:169` |
| Realtime | One shared WebSocket `/ws/notifications/`. It feeds the bell and triggers an overview refetch with a 2 s trailing debounce | `useRealtimeNotifications.js:166`, `AIMeshFirewallOverview.jsx:695-706` |
| Build output | **One JS chunk of 2,211.83 kB (646.14 kB gzip)** and 224.66 kB of CSS (28.53 kB gzip). There is no code splitting, so `/login` downloads everything | `vite-build-develop-562619c8.txt` |
| Fonts | Three Google Fonts `@import`s (Inter, JetBrains Mono, Plus Jakarta Sans) | `index.css:2-4` |
| Tests | `node --test` unit tests only. `npm run lint` runs the unit tests, so there is no lint. `tests/visual/run.sh` needs Docker and a running stack, and is not in CI | `package.json` scripts, `tests/visual/run.sh` |
| CI | The `frontend` job calls `reusable-node-ci.yml` (`npm ci`, `test:unit`, `vite build`). It runs no browser tests | `.github/workflows/ci.yml:38-44` |

### 2.2 Audit findings this plan relies on (re-verified here; AUDIT.md's findings section is still a placeholder, issue #129)

| ID | Finding | Evidence at `562619c8` | Fixed in slice |
|---|---|---|---|
| A-02 | No real URLs. "Detailed results" and the log detail are in-memory state, so a refresh or a shared link loses them | `App.jsx:50-51` (`useState` for `resultsTab`, `logDetailData`), `App.jsx:98-110` | S2 (shell), S4 (request detail) |
| A-03 | Tab changes use `replace: true`, so Back leaves the app instead of returning to the previous view | `App.jsx:54-60` (`setSearchParams(…, { replace: true })`) | S2 |
| A-04 | The header search is not connected: `App` passes no `onSearchSubmit`, so Enter just clears the field | `App.jsx:178` (no search props), `Header.jsx:253-258` | S2 |
| A-05 | **9,746** raw Tailwind palette classes in 85 files (the audit's regex counted 9,651), plus 4,471 `dark:` variants and 157 hex literals in JS | `counts.sh` → `counts.txt` | S1 (gate), S3–S7 (migration), S10 (palette removed) |
| A-08 | The sidebar item "Inputs" opens the firewall configuration page, which holds model and database connections plus nine config cards | `Sidebar.jsx:35`, `App.jsx:173`, `AIMeshFirewallConfig.jsx:14-25` | S2 (rename and split), S7 |
| A-09 | DESIGN.md says the theme "default resolves to dark", but the code defaults to light | `DESIGN.md:13` against `ThemeContext.jsx:20` | S1 |

New findings from this exploration (each one changes a slice):

| ID | Finding | Evidence | Consequence |
|---|---|---|---|
| N-1 | Login returns only to `from.pathname`. The query string is dropped, so `/?tab=firewall-1-2` comes back as `/` after a session expiry | `Login.jsx:28`, `ProtectedRoute.jsx:19` | S2: `next` keeps path, query and hash, with an open-redirect guard |
| N-2 | The Settings page stores "desktop" and "sound" notification toggles that nothing reads | `Settings.jsx:33-46`; no other reader of `zeroshield_*_notifs` | Removed (brief: no notifications) |
| N-3 | Tab strips: 14 uses of `role="tablist"`, `<Tabs>` or `SegmentedControl` outside `ui/`, 5 of them in `MCPConnectorPanel.jsx` | `counts.txt` | Replaced by grouped lists or sections (S6, S7) |
| N-4 | Heavy analytics views take a per-worker semaphore of 2 (`ANALYTICS_MAX_INFLIGHT`) and return **503 `analytics_busy`** with no wait and no `Retry-After` | `policy/analytics_concurrency.py:10-44`; used by `soc-kpis`, `module-kpis`, `module-trends`, `attack-vector-trends`, `dashboard/model-usage`, `dashboard/risk-distribution` | §6.3 scheduler: at most 2 analytics calls in flight per tab, jittered retry on 503 |
| N-5 | Each endpoint uses a different window parameter: `period` (allowed values in `analytics_period.py:9`), `days` (`policies/analytics`, `policy/top-rules`, `dashboard/model-usage`), `hours` (`owasp-stats`, `threat-feed`, `mcp-connector/events/summary`). The `days` and `hours` views parse with a bare `int()`; the global handler turns the `ValueError` into a 400 with no `code` (measured, §6.5) | `security_views.py:479,1587`, `analytics_views.py:180,550`, `main_app/exception_handlers.py:57` | B2. Each box footer states the window it actually used |
| N-6 | `/api/health/` returns only `{"status": "ok"}` | `core/views.py:21-23` | The "Gateway fleet" box has no source (B4) |
| N-7 | `GatewayAPIKey.last_used_at` is stamped in the gateway's Redis payload and never written back to Postgres, so the keys API reports it as null | Writer only in `gateway/ai_mesh_gateway/middleware.py:533-545` (Redis Lua); no writer in `control/`, `workers/`, `services/` or `shared/`; probe in §6.5 | The "idle" and "last used" fields have no source (B5) |
| N-8 | 401 auth failures are never recorded. `auth_status` and `rate_limit_status` on events are flags derived from `threat_type` for requests that already passed auth | `core/tasks.py:271-272` | The "Auth failures" and "Rate limit headroom" boxes have no source (B5) |
| N-9 | MCP `last_health_at` and `last_health_status` are serialised but never written (the default is `unreachable`). `mcp-connector/health/` reports the MCP firewall service and its detector list, not server reachability | `mcp_connector/models.py:88-97`; writers: none; `views.py:1123-1149` | The MCP box uses `connection_status` from the last sync (B6) |
| N-10 | Firewall overhead is not exposed anywhere in control. v1 (the **live production data plane**, register R16) has only end-to-end latency (`amf_gateway_request_latency_seconds`); v2 has `amf_addon_latency_ms` on its own `/metrics` | `gateway/ai_mesh_gateway/metrics.py:82-88`, `gateway_v2/…/edge/publisher.py:118` | The "Firewall overhead" box has no source (B3) and shows "Unavailable" on v1 |
| N-11 | Policies are edited live. There is version history (`PolicyVersion`) and `version`-based 409 conflict detection on update, but **no draft state** | `policy/models.py:285-291`, `policy/views.py:325-336,597-636` | The editor's "Draft against live" becomes a client-side draft (S6) |
| N-12 | Hourly trend grids run from `since` to `now` **inclusive**, so 24 h at 60-minute buckets gives 25 buckets, the last one partial, aligned to server-clock hours | `policy/analytics_sql.py:98-113` `trend_grid` (`while cursor <= now`); bucket count measured in §6.5 | The cell strip renders the API's bucket count, not a hard-coded 24 (§5.4) |
| N-13 | Control echoes and logs `X-Request-ID` (`CorrelationIDMiddleware`) | `main_app/log_extras.py:124-165`, `settings.py:135` | Every box fetch sends `X-Request-ID: ui-<box>-<uuid>`, which is shown in its error state and checked against the logs in e2e (§6.6 A2) |
| N-14 | `GET /api/policies/` without `?policy_domain=` returns **400**; the domains are `pipeline`, `rag` and `mcp`, and vector policies live at `/api/vector-policies/` | Probe §6.5; `policy/views.py:96-98,520-527`, `policy/models.py:27-33` | The policy list and box 6 make 4 calls (3 domains plus vector policies) until B15 |

### 2.3 Backend, infrastructure and data path

- Control: Django + DRF under gunicorn with `UvicornWorker` and a dynamic worker count (`control/server-entrypoint.sh:37-94`). Analytics reads use the `analytics` DB alias under `AnalyticsTimeoutMiddleware`, which returns **504 `query_timeout`** on a statement timeout (`main_app/analytics_timeout_middleware.py:41-42`). The C-2 hourly rollups serve 24h/7d/30d windows when `ANALYTICS_SERVE_ROLLUPS` is on; the remainder comes from raw events, so the response is current to request time (`policy/analytics_rollup.py:107-149`), and every analytics response says which path served it in `X-Analytics-Source: rollup|raw`.
- Data: the gateways emit telemetry to Redis, `drain_telemetry_from_redis` writes `EnforcementEvent` rows (`core/tasks.py:569`), and the analytics read those rows. **The UI cannot see drain lag today**: `created_at` is set at insert, and no endpoint exposes the backlog (B12).
- Serving: production bakes `frontend/dist/` into the nginx image (`deploy/Dockerfile.nginx:31`). `location /` already does `try_files $uri $uri/ /index.html` with `Cache-Control: no-store` (`deploy/nginx-ssl.conf:115-120`), and `/assets/` is immutable for one year. Real client-side URLs therefore need no nginx change; only the landing page at `/` does. The CSP is `frame-ancestors 'none'` only (`deploy/security-headers.inc`).
- Data plane: v1 `gateway/` serves production and v2 is profile-gated off (SESSION_MEMORY §3, R16). Any box that needs v2-only data, such as `stage_metrics_ms`, overhead or plan version per replica, shows **Unavailable** on v1, never zero.

## 3. Scope: screens, routes and migration

### 3.1 Route map (real URLs; Back, Forward and refresh work on each)

| Screen | Route | URL state | Drawn in Lattice | Replaces |
|---|---|---|---|---|
| Landing (public) | `/` (static; served at cutover, S9) | – | yes | new |
| Sign in | `/login?next=/console/…` | `next` (same-origin `/console/` paths only) | no (restyle) | `pages/Login.jsx` |
| OAuth callback | `/oauth/callback` (unchanged: registered with identity providers) | – | – | `pages/OAuthCallback.jsx` |
| Console root | `/console` → `/console/overview` | – | – | – |
| Overview | `/console/overview` | `range=1h\|6h\|24h\|7d\|30d` | yes | `AIMeshFirewallOverview` |
| Requests (list) | `/console/requests` | `range, checkpoint, action, source, model, q, page` | **no** (owner Q7) | `SubmoduleResultsPage`, `LogViewerPanel` |
| Request detail | `/console/requests/:eventId` | – | yes | `LogDetailPage` |
| 1.1 Gateway and keys | `/console/gateway`, `/console/gateway/keys/new`, `/console/gateway/keys/:keyId` | – | yes | `Firewall11Page`, `GatewayKeyPanel` |
| 1.2 Policies | `/console/policies` (first policy selected), `/console/policies/:policyId`, `/console/policies/new?domain=pipeline\|rag\|mcp\|vector` | `rule=<id>` (selected rule) | yes | `Firewall12EnterprisePage`, `PolicyManagementPanel`, `VectorPolicyPanel`, `PolicyAnalyticsPanel`, `PolicyDomainSwitcher` |
| 1.3 RAG and vectors | `/console/rag` | – | no (design PR D2) | `Firewall13Page` |
| 1.4 MCP tools | `/console/mcp`, `/console/mcp/servers/:serverId` | – | no (D2) | `Firewall14Page`, `MCPConnectorPanel` |
| 1.5 Model routing | `/console/routing` | – | no (D2) | `Firewall15Page` |
| 1.6 Kill switch | `/console/kill-switch` | – | no (D2) | `Firewall16Page` |
| 1.7 Output guardrails | `/console/output` | – | no (D2) | `Firewall17Page` |
| Attack simulator | `/console/simulator` | `scenario`, `replay=<eventId>` | no (D2) | `AttackSimulatorPanel` and the per-module simulators |
| Connections | `/console/connections` | – | no (D2) | the model and database cards of `AIMeshFirewallConfig` (A-08) |
| Settings | `/console/settings` (firewall settings and appearance), `/console/settings/profile` | `#section` anchors | no (D2) | `Settings.jsx`, `Profile.jsx`, the nine config cards of `AIMeshFirewallConfig` |
| Not found | any other `/console/*` path | – | – | today `*` silently redirects to `/` |

Rules:

- Navigation **pushes** a history entry. Only normalisation (an unknown `range` corrected to `24h`, `/console` to `/console/overview`) **replaces**.
- Filters and the time range live in the URL, so a copied link reproduces the view.
- Legacy URLs keep working: `/?tab=firewall` → `/console/overview`, `firewall-1-1` … `firewall-1-7` → the checkpoint routes above, `firewall-config` → `/console/connections`, `profile` → `/console/settings/profile`, `settings` → `/console/settings`. Until cutover this map lives in the SPA (`/` with `?tab=`). From S9 it is an nginx `map $arg_tab` on `location = /`, mirrored in a Vite dev/preview middleware so dev matches prod.

### 3.2 Existing console pages: migration or retirement

| Existing view (today) | Fate | Slice |
|---|---|---|
| Shell: `DashboardLayout`, `Header` (bell, notification dropdown, health badge, search), `Sidebar` ("Inputs", collapse) | **Replaced** by the Lattice shell. Bell, dropdown and `/api/notifications/` calls removed. The WebSocket stays only as a silent refresh trigger. The health badge becomes a box, not a header widget | S2 |
| Overview (`AIMeshFirewallOverview`, 1,134 lines) | Rebuilt as the box lattice | S3 |
| Detailed results (`SubmoduleResultsPage`) | **Retired**. Becomes `/console/requests?checkpoint=1.x` | S4 |
| Log detail (`LogDetailPage`, "Scan Detail Report") | Rebuilt as request detail at its own URL | S4 |
| 1.1 (`GatewayKeyPanel` and `AttackSimulatorPanel` on the page) | Keys page rebuilt; the simulator moves to `/console/simulator` | S5, S7 |
| 1.2 (`Firewall12EnterprisePage`, policy tab strip) | Policy editor; policy domains become groups in one list (no tabs) | S6 |
| 1.3–1.7 module pages (`FirewallModulePage` + panels) | Mounted unchanged in the new shell in S2; rebuilt in Lattice after design PR D2 | S2, S7a–e |
| Inputs (`AIMeshFirewallConfig`) | Split: connections → `/console/connections`; config cards → `/console/settings` sections, each linked from its checkpoint page | S7 |
| Profile, Settings | Restyled; the notification toggles (N-2) are removed | S7 |
| `HowToUse`, `RetiredOnGatewayV2`, `OrgIsolationBanner` | Audited in S7: each one is kept as a box, folded into a page, or deleted, and recorded in that PR | S7 |
| Login, OAuth callback | Restyled; behaviour unchanged except `next` (N-1) | S2 |

### 3.3 Landing page

- **Where it lives:** a second, **prerendered static entry** in the same Vite project (`frontend/landing/`). React components (`Box`, `Chip`, the chart primitives) are rendered to HTML at build time with a Vite SSR build and `renderToStaticMarkup`. The output is `dist/landing.html` with `tokens.css`, the Geist files and **no React runtime** (one inline script under 2 KB for the theme toggle and the mobile menu). Sharing components avoids a second, hand-written copy of the box markup drifting from the console.
- **How it is served (S9):** nginx `location = / { try_files /landing.html =404; }` on the console host, with the legacy `?tab=` map first. Every other path stays with the SPA. The owner may prefer a separate host or a page on zeroshield.ai; the build output is the same either way (owner Q1).
- **SEO and meta:**
  - one `<h1>`, `lang="en"`, `<title>` and meta description from approved copy;
  - `rel=canonical` on the owner's chosen domain;
  - Open Graph and Twitter card (a 1200x630 image rendered from the landing hero by the e2e job, committed);
  - `robots.txt` that allows `/` and disallows `/console/`, `/api/`, `/login`;
  - a `sitemap.xml` listing `/`.

  The SPA `index.html` gets `<meta name="robots" content="noindex">`, and nginx adds `X-Robots-Tag: noindex` on `/console/`.
- **Performance budget (CI-gated, Lighthouse mobile preset):**
  - Lighthouse scores: Performance ≥ 95, Accessibility = 100, Best Practices ≥ 95, SEO = 100;
  - LCP ≤ 2.0 s, CLS ≤ 0.02, TBT ≤ 100 ms;
  - HTML + CSS ≤ 35 kB gzip; JS ≤ 2 kB (inline); fonts ≤ 2 woff2 files, ≤ 90 kB together, the UI font preloaded;
  - no third-party requests: no Google Fonts, analytics or trackers (owner Q11).
- **Content:** the copy and structure of the Lattice mockup. The console preview is HTML with a visible "Sample data" marker. There are no performance numbers, customer names or certifications until the owner approves claims (DIRECTIONS Q6).

## 4. Design system

### 4.1 Tokens: one source

- `frontend/src/design/tokens.mjs` is copied from the Lattice entry of the mockups' `tokens.mjs` (27 tokens per theme, dark primary; `check-contrast.mjs` checks 23 text and UI pairs of them per theme). It is the **only** place colours, fonts, radius and spacing are defined.
- `npm run tokens` generates `src/design/tokens.css` (CSS variables) and `src/design/tokens.json` (for charts and tests). CI regenerates both and fails on any diff (`git diff --exit-code`).
- Name mapping to the app's semantic names, following the DIRECTIONS contract:

  | Lattice | App variable |
  |---|---|
  | `page` | `--background` |
  | `surface` / `surface2` / `raised` | `--card` and `--surface-1` / `--surface-2` and `--muted` / `--popover` |
  | `ink` / `ink2` | `--foreground` / `--muted-foreground` |
  | `accent`, `onAccent`, `accentText` | `--primary`, `--primary-foreground`, new `--primary-text` |
  | `control` / `line` | `--input` / `--border` |
  | `focus` | `--ring` |
  | `allow` / `monitor` / `block` (each with `*Ink` and `*Text`) | `--success` / `--warning` / `--destructive` (with foreground and text) |
  | `redact`, `redactInk`, `redactText` | new `--redact`, `--redact-foreground`, `--redact-text` |
  | `nav`, `navInk`, `navInk2` | new `--sidebar`, `--sidebar-foreground`, `--sidebar-muted` |

  **Collision:** the app's `--accent` is a soft hover background (shadcn convention), not the brand colour. The Lattice brand colour therefore maps to `--primary`, and `--accent` keeps its meaning. A unit test asserts the mapping table.
- **Scope during migration:** the Lattice values apply under `[data-ui="lattice"]`, which the new shell's root sets. Legacy pages outside the new shell are untouched. Legacy pages mounted inside it get Lattice values only through `components/ui/*`. In S10 the scope selector is removed and the tokens move to `:root`.
- **Tailwind 4:** `@theme inline` maps utilities to the variables, for example `bg-page`, `bg-surface`, `bg-surface-2`, `text-ink`, `text-ink-2`, `border-line`, `border-control`, `bg-allow`, `text-allow-text`, `bg-redact`, `text-block-text`, `ring-focus`, `font-ui`, `font-mono`. The radius is 6 px for boxes and 5 px for controls. In S10, `--color-*: initial;` removes Tailwind's default palette, so a raw `bg-slate-800` **stops compiling at all**.

### 4.2 Raw palette removal (A-05) and the lint gate

- `scripts/lint-palette.mjs` (new, no dependencies) counts raw palette classes (the regex in `counts.sh`), `dark:` variants and hex literals per file, and compares them with the committed `frontend/palette-baseline.json`. **Ratchet:** a file's count may only go down; a new file must be 0; anything under `src/console/` (the new shell and its pages) must have 0 raw palette classes, 0 `dark:` variants and 0 hex literals. Tokens and the chart theme are the only allowed hex sources.
- The baseline starts at **9,746 / 85 files** (`counts.txt`). It falls as pages migrate (S3–S7) and reaches 0 in S10, where the palette is also removed from Tailwind.
- `npm run lint` is redefined to run lint (palette ratchet, token freshness, contrast) instead of the unit tests. CI passes it as `lint-command` to the existing `reusable-node-ci.yml`.

### 4.3 Themes (A-09)

- **Decision proposed: dark by default** for the console and the landing page (Lattice is dark first, `DESIGN.md:13` already says dark, and the owner's reference is dark). The user's stored choice (`zeroshield_theme`: `dark` | `light` | `system`) wins. Owner Q3.
- One mechanism: `ThemeContext` sets `data-theme` on `<html>` (and keeps the `.dark` class while legacy `dark:` variants exist). An inline pre-paint script in `index.html` and `landing.html` applies it before the bundle loads. Today the only defence against a flash of the wrong theme is `ThemeContext` running at module load, after a 2.2 MB download. If a `script-src` CSP is added later, the inline script is allowed by hash.
- A theme change in one tab applies to other open tabs through the `storage` event.
- Tests: a fresh profile renders dark; stored `light` renders light with no dark frame (a screenshot taken at `DOMContentLoaded`); `system` follows `prefers-color-scheme`.

### 4.4 Contrast and accessibility gate

- `scripts/check-contrast.mjs` is ported from the mockups. It checks every text and UI pair in both themes: text ≥ 4.5:1, large text and UI boundaries ≥ 3:1 (WCAG 1.4.3 and 1.4.11). It runs in `npm run lint`, so a token change that breaks a pair fails CI. PR #123 measured 46 + 24 Lattice pairs with 0 failures; the ported check covers the same pairs plus any new token.
- axe-core through `@axe-core/playwright`, rules `wcag2a`, `wcag2aa`, `wcag21aa` and `wcag22aa`, on every route, both themes and all three viewports: **0 violations** is the gate. `best-practice` findings are reported in the PR.
- Keyboard: a skip link, landmarks, a visible `:focus-visible` ring in `--ring`, a focus-trapped search dialog (Ctrl/Cmd K, Esc closes), roving focus in the sidebar, and every action reachable without a pointer. One keyboard-only e2e walk per drawn screen.
- Reflow at 320 CSS px and 200% zoom (WCAG 1.4.10): no horizontal scroll except inside data tables, which scroll on their own. `prefers-reduced-motion` is honoured, and numbers never animate on refresh.

### 4.5 Fonts

- Geist and Geist Mono (SIL OFL 1.1), **self-hosted** from the `geist` npm package's woff2 files with `font-display: swap`. Only the UI font is preloaded. Subsets are latin and latin-ext. Licence text is shipped in `public/fonts/`.
- No Google Fonts requests from the new shell or the landing page (a Playwright network assertion). The three legacy imports in `index.css:2-4` are removed in S10, when no legacy page needs them.
- Fallback stacks: `'Geist', system-ui, sans-serif` and `'Geist Mono', ui-monospace, monospace`, with `size-adjust` metrics set so swapping in Geist causes no layout shift (CLS budget).

## 5. Components

### 5.1 Box (the primitive every Lattice screen is built from)

```
<Box span={3} title="Traffic" purpose="Requests through the gateway, per hour"
     source={useSource("soc-kpis", { period })}   // §6.2
     tag={<Chip tone="allow">Serving</Chip>}>
  …body…
</Box>
```

- **Fixed anatomy, in order:**
  1. a header with the title (h2, one line, ellipsis) and an optional tag area for chips and IDs;
  2. a one-line purpose;
  3. the body;
  4. the footer.

  The footer shows, in order, the freshness word, the age, and the source label. The label is a button whose popover holds the endpoint and parameters, the window used, the fetch time, the data-as-of time, the HTTP status and the `X-Request-ID`.
- `title`, `purpose` and `source` are required. A box without them throws in dev, and the e2e invariant (§7.4) fails.
- Corner registration ticks (the Lattice signature) are pure CSS, `aria-hidden`, and turn off under `forced-colors`.
- **On mobile the source stays visible.** The mockup hides `.src` below 760 px (`style.css`, `.s3 .bf .src { display: none }`); that contradicts "every box says where its number came from". The footer wraps instead: freshness and age on line 1, source on line 2.

### 5.2 Box states (every box, every source)

| State | When | Shows |
|---|---|---|
| Loading | No data yet, first fetch in flight | A skeleton of the box's own body shape (no spinner). The footer reads "Loading · \<source\>" |
| Fresh | The last fetch succeeded and its age is within the source's `freshFor` (§6.2); the data age is within the source threshold where the source reports one | Body; footer "Fresh · 40 s ago" |
| Stale | There is data, but refreshes are failing or paused past `freshFor`, **or** the source reports data older than its threshold | Last good body, unchanged; footer "Stale · 4 min ago" in the monitor colour (word plus glyph, not colour alone). The popover says which condition applies (refresh failing with status and request id, tab was hidden, or newest event 26 min old) |
| Error | No usable data, and the last fetch failed | "Could not load: \<reason\>". Reason comes from the API's `code` or `error` field (`analytics_busy`, `query_timeout`, `invalid_period`) or the HTTP status. Retry button, request id. |
| Empty | The fetch succeeded with zero rows or zero counts | A purpose-specific sentence, for example "No requests in the last 24 hours" or "No keys yet: create one", with the time window named |
| Unavailable | The endpoint exists, but this deployment does not measure the source (for example overhead on the v1 gateway), or a capability flag says so | "Not measured on this gateway: \<why\>". Never a zero |
| Forbidden | 403 | "Your role cannot see this" |

### 5.3 Chips, IDs and verdicts

- Outlined, square glyph, always a word (never colour alone). Tones: `allow`, `redact`, `monitor`, `block`, `neutral`, `accent`. Sizes: default (20 px) and large (26 px).
- One verdict vocabulary everywhere: Allowed, Redacted, Watched (`monitor` and `flag`), Blocked. Status vocabulary: Enforcing, Degraded, Isolated, Serving, Paused.
- Module IDs (`1.1`…`1.7`) are mono ID tags.

### 5.4 Charts (SVG primitives; no chart library on the overview)

`Sparkline`, `CellStrip`, `MixBar`, `Histogram`, `BarRows`. These are small, token-coloured SVG/CSS components, as in `shell.js` of the mockups.

- Each has `role="img"` and an `aria-label` carrying the actual values, plus a "Show values" disclosure that renders the numbers as a table (no tabs).
- `CellStrip` renders **the API's buckets** (25 for 24h, N-12), labels each cell with its start in the viewer's local time, and marks the last, partial bucket. Bucket boundaries follow server-clock hours, so in IST (+05:30) the labels read hh:30. They are not shifted, because shifting would misstate the data.
- ECharts stays only for the larger module-page charts that need it, **lazy-loaded** per route. In S10, uPlot (3 files: `UPlotChart.jsx`, `SafeResponsiveChart.jsx`, `uplotStack.js`) or ECharts is removed if one of them covers the remaining charts. That decision is recorded with bundle numbers.

### 5.5 Grid and responsive rules

- A strict 12-column grid, 14 px gap (10 px on mobile). Box spans are fixed per breakpoint and never computed from content:

  | Width | Columns | `span 3` box | `span 4/6/8/12` |
  |---|---|---|---|
  | ≥ 1280 (desktop; test viewport 1440x900) | 12 | 4 per row | as declared |
  | 1024–1279 (laptop, tablet landscape 1112x834) | 12 | 3 per row (span 4) | 6→6, 8→12, 4→6 |
  | 768–1023 (tablet portrait; test viewport 834x1112) | 12 | 2 per row (span 6) | all → 12 |
  | < 768 (mobile; test viewport 390x844) | 2 | 2 per row (span 1 of 2, as the mockup) | all → full width (span 2) |

- The overview keeps reading order on collapse: row-major order is the DOM order, and the tests assert it. The policy editor's list moves last on mobile (`m-last` in the mockup).
- Box heights within a row are equal (grid stretch). No masonry.

### 5.6 Tables and forms

- Tables: mono uppercase headers, numbers right-aligned and tabular. Rows are links when they open a URL (whole-row link, keyboard-focusable). Low-priority columns hide below 1024 px but stay available in the row's detail. Sorting is server-side where the endpoint supports it and in the URL.
- Forms: labels above fields, errors inline and in an `aria-live` summary, server validation messages mapped to fields. Submit buttons are disabled while in flight, so a double click sends one request. Leaving with unsaved edits asks for confirmation (router blocker plus `beforeunload`).

## 6. Data

### 6.1 Per-box source map: overview (16 boxes as drawn)

Status key: **exists** = the endpoint returns what the box shows; **partial** = some fields have no source; **missing** = no endpoint. "Mockup source" is the source line drawn in PR #123; "Proposed source" is what the code supports today. Cadence = refresh interval while the tab is visible.

| # | Box | Mockup source | Proposed source and fields | Cadence / `freshFor` | Status | Gap |
|---|---|---|---|---|---|---|
| 1 | Traffic | `soc-kpis` | `soc-kpis?period=` → `requests_inspected`. The hourly spark has no request-level series: `module-trends` counts **events** for 1.1, so the spark would not sum to the KPI. The "+6%" delta has no source | 15 s / 30 s | partial | B7 (hourly request series, previous-window total) |
| 2 | Verdicts | `policies/analytics` | **`soc-kpis`**: `requests_allowed`, `requests_redacted`, `requests_blocked`. `policies/analytics` counts **events** over `days`, has no allowed count, and its smallest window is a day. "Watched" has no request-level count (`requests_allowed` includes monitored) | 15 s / 30 s | partial | B7 (`requests_monitored`) |
| 3 | Firewall overhead | `stage_metrics_ms` | None. `stage_metrics_ms` is a per-event field, not an endpoint (N-10) | 15 s / 30 s | **missing** | B3 |
| 4 | Gateway fleet | `health` | None. `/api/health/` is `{"status":"ok"}` (N-6); `gateways/stats` lists v1 gateway agents with HEALTHY/WARNING on 5-minute telemetry; nothing reports plan version per replica or config-sync age | 10 s / 20 s | **missing** | B4 |
| 5 | 1.1 Gateway and keys | `gateways/stats` | **`gateways/keys`**: active count, expiring within 7 days from `expires_at`; `module-trends` 1.1 for the strip. Auth failures (N-8) and idle (N-7) have no source | 60 s / 120 s (keys), 15 s (strip) | partial | B5 |
| 6 | 1.2 Policies | `policies/analytics` | `module-kpis` 1.2 (blocked, redacted); `/api/policies/?policy_domain=` ×3 plus `/api/vector-policies/` (enabled count; N-14); `policies/compile/status` (`version`); `module-trends` 1.2 strip | 15 s / 30 s (policies 60 s) | exists | B15 (one call instead of 4) |
| 7 | 1.3 RAG and vectors | `rag-pipeline-kpis` | `rag-pipeline-kpis` (`document_funnel`, `stages`) for withheld; `vector-providers` for stores; chunk totals from `admin/gateway/rag/collections`; `module-trends` 1.3 strip | 60 s / 120 s | partial | B14 (withheld count defined server-side) |
| 8 | 1.4 MCP tools | `mcp-connector/health` | **`mcp-connector/servers`** (`connection_status` at last sync) plus `mcp-connector/events/summary?hours=` (`decisions`); `module-trends` 1.4 strip. "Up" means "connected at last sync", which is not live reachability (N-9) | 30 s / 60 s | partial | B6 |
| 9 | 1.5 Model routing | `firewall/models` | `firewall/models` (count); `module-kpis` 1.5 (total). The reroute count and fallback-rule count have no source | 60 s / 120 s | partial | B14 |
| 10 | 1.6 Kill switch | `kill-switches` | `kill-switches` (`is_active`, `model_name`, `activated_at`); `models/audit-log` for the isolated-since strip | **10 s / 20 s** (safety state) | exists | – |
| 11 | 1.7 Output guardrails | `module-kpis` | `module-kpis` 1.7; `module-trends` 1.7 strip | 15 s / 30 s | exists | – |
| 12 | Detectors | `firewall/config` | `firewall/config` (`tier2_enabled`, `tier2_strict`, `tier2_fail_closed_enabled`); Tier-1 preset and custom-regex counts from `/api/policies/` rules. Tier-2 p95 has no source | 60 s / 120 s | partial | B11 |
| 13 | Rules that fired most | `policy/top-rules` | `policy/top-rules?days=1&limit=5`. The smallest window is a day; `1h` and `6h` cannot be honoured | 30 s / 60 s | exists (window caveat) | B2 |
| 14 | OWASP LLM Top 10 | `owasp-stats` | `owasp-stats?hours=` (maximum 720) | 60 s / 120 s | exists | B2 (malformed → §6.5) |
| 15 | Models | `firewall/models` | `firewall/models` (list) plus `dashboard/model-usage?days=1` (share) plus `kill-switches` (Isolated chip) | 60 s / 120 s | exists (three sources; window is days) | B2 |
| 16 | Enforcement log | `threat-feed` | `threat-feed?limit=1` (newest record: "last written") plus `soc-kpis.total_threats` (records). "Dropped" and drain lag have no source | 15 s / 30 s | partial | B12 |

Totals: **exists 6** (6, 10, 11, 13, 14, 15), **partial 8** (1, 2, 5, 7, 8, 9, 12, 16), **missing 2** (3, 4). Box 16 is partial: "records" and "last written" have sources, "dropped" does not. The mockup's source line is wrong for boxes 2, 4, 5 and 8, and box 3 names a field (`stage_metrics_ms`), not an endpoint.

**What ships when (owner Q5 default):** a box whose **only** source is missing (3, 4) is not rendered until its backend issue lands. A partial box ships with only its sourced fields: the field without a source is left out, not shown as zero. When a backend issue lands, the field or box arrives in a small follow-up PR with its agreement test. The header line counts the boxes actually shown ("14 boxes · 14 fresh").

### 6.2 Freshness model (what "Fresh" and "Stale" mean, per source)

- Every box reads through `useSource(id, params)`: one hook over TanStack Query v5 (one new runtime dependency, about 13 kB gzip; the alternative is an in-house hook with the same contract). It returns `{data, status, fetchedAt, dataAsOf, window, requestId, httpStatus, errorCode}`.
- **Fetch age** = now − `fetchedAt`, both on the client clock, so server clock skew does not matter. **Data age** = now − `dataAsOf`, converted with the skew measured from the response `Date` header, used only when the source reports `as_of` (B1) or drain lag (B12).
- **Fresh** if: the last fetch succeeded, AND fetch age ≤ `freshFor` (2 × cadence), AND (data age ≤ the source threshold, or the source reports no data age).
- **Stale** otherwise, while there is data. The source thresholds once B1/B12 land:

  | Source group | Stale when |
  |---|---|
  | Analytics (soc/module KPIs, trends, OWASP, top rules) | drain lag > 60 s |
  | Config and lists (keys, policies, models, kill switches, firewall config) | fetch age > `freshFor` (a direct DB read: fetch age is the truth) |
  | Fleet (B4) | any replica's heartbeat older than 30 s (today's `gateways/stats` uses 5 minutes for WARNING; B4 makes it one number) |
  | Overhead (B3) | the metric window ended more than 60 s ago |

- **Record** (request detail): immutable records show "Record · \<event time\>" and do not poll.
- **Hidden tabs** pause polling (as today). On becoming visible, every source past `freshFor` refetches at once, so a returning operator sees "Stale" for at most one round trip.
- Until B1 and B12 land, analytics boxes can only claim "fetched N s ago". The popover says "data age not reported by this endpoint", so the word "Fresh" never overstates.

### 6.3 Request scheduling, caching and invalidation

- **Deduplication:** `soc-kpis` feeds 3 boxes (1, 2, 16), `module-kpis` 3 (6, 9, 11), `module-trends` 6 strips (5–9, 11). One query key per (endpoint, params) means one request per refresh. With the cadences in §6.1, the full overview makes about **42 GETs per minute** per visible tab (4 sources at 15 s, kill switches at 10 s, 3 sources at 30 s, 14 at 60 s including the 4 policy-domain calls until B15). Today's overview makes about 66 per minute (11 GETs every 10 s) plus the shell's health and notification calls.
- **Concurrency:** at most 4 requests in flight per tab, and **at most 2 analytics requests** (N-4). They queue in fixed order: above-the-fold first, then row order.
- **Retry:**
  - **503 `analytics_busy`:** jittered backoff of 250 ms, 500 ms, then 1 s; after that the box goes to Stale or Error.
  - **504 `query_timeout`:** no automatic retry within the same tick; shown as Stale or Error with its code.
  - **401:** the existing refresh mutex; on failure, go to `/login?next=`.
  - **400:** no retry. A 400 is a bug, and the e2e tests fail on any 400 or 500 from a box.
- **Invalidation after mutations:**

  | Mutation | Invalidates |
  |---|---|
  | Create, revoke or delete a key | keys sources, box 5 |
  | Publish a policy | policies, compile status, box 6 |
  | Activate or deactivate a kill switch | kill switches, boxes 10 and 15 |

  Optional: the same invalidation is broadcast to other tabs over `BroadcastChannel`.
- **Realtime:** the existing WebSocket stays, without any UI. Enforcement and escalation events trigger a 2 s debounced refetch of the analytics sources only.

### 6.4 Gaps: backend issues to open (the coordinator opens them; frontend slices depend on them)

| ID | Issue | Needed by | Notes |
|---|---|---|---|
| B1 | Every analytics response carries `as_of` (server time the numbers are true to) and `window` (`{since, until, param}`) | Freshness, every analytics box | A header (`X-Data-As-Of`) works too |
| B2 | One window parameter (`period=1h\|6h\|24h\|7d\|30d`) on `policies/analytics`, `policy/top-rules`, `owasp-stats`, `threat-feed`, `dashboard/model-usage`, `mcp-connector/events/summary`. Every 400 carries a machine-readable `code`; they are already 400, not 500 | Boxes 2, 13–15; the requests list | Evidence §6.5 |
| B3 | Firewall overhead p50/p95/p99 and a histogram for the window (v2: `amf_addon_latency_ms`; v1: an explicit "not measured") | Box 3 | Ties to F1/F3 |
| B4 | `GET /api/gateways/instances/`: replicas, serving state, plan or state version per replica, last heartbeat, last config sync | Box 4, policy "Rollout" | v2 state-plane data |
| B5 | Key usage: write `last_used_at` back to Postgres (debounced batch), per-key requests in the window, auth failures by reason (unknown, expired, revoked) and by key prefix, current TPM against the limit | Box 5; keys page "Requests by key", "Auth failures", "Rate limit headroom" | N-7, N-8 |
| B6 | MCP server reachability: write `last_health_at` and `last_health_status` from a periodic probe, or remove the fields | Box 8 | N-9 |
| B7 | `soc-kpis`: `requests_monitored`, an hourly request-level series, and the previous-window total | Boxes 1–2 | |
| B8 | `policy/top-rules?policy_id=`: per-rule hits for one policy | Policy editor "Hits by rule" | |
| B9 | Request lookup and search: `GET /api/security/requests/<request_id>/` plus text/ID and `incident_status` filters on `threat-feed` (today it filters by `source`, `action`, `threat_type`, `model`, `module_id` only) | Search (A-04); requests list `q` and `status` | `security_views.py:479-524` |
| B10 | Policy → compliance tag mapping (the catalogue `ComplianceTag` exists; there is no mapping per policy) | Policy editor "Mapped to" | Otherwise the box is dropped |
| B11 | Detectors summary: Tier-1 preset and custom counts, Tier-2 p95, fail-closed state, Tier-2 unavailable count | Box 12 | |
| B12 | Enforcement pipeline health: drain backlog, drain lag in seconds, dropped events, last drain time | Box 16; Stale for every analytics box | |
| B13 | (optional) Server-side policy drafts | Policy editor | The default is a client draft (§8, S6) |
| B14 | Checkpoint counters defined server-side: 1.3 chunks withheld, 1.5 reroutes and fallback rules | Boxes 7, 9 | |
| B15 | Policies across domains: `GET /api/policies/?policy_domain=all` including vector policies (or a counts endpoint: enabled policies and rules per domain) | Box 6; the grouped policy list (S6) | N-14; until then, 4 calls |

### 6.5 API probe (measured on a throwaway test database)

`apiprobe/test_lattice_probe.py` runs in the control test runner. It seeds 12 events in 11 requests (6 allow, 2 redact, 1 monitor, 1 block, and one request with redact then block, which must count as one blocked request), plus 1 event outside the window. It calls every endpoint above as a box would, and records status, keys, `X-Analytics-Source` and the numbers. Output: [`apiprobe/probe-results.json`](evidence/2026-11-12-f6-lattice-build/apiprobe/probe-results.json); summary: [`apiprobe/SUMMARY.txt`](evidence/2026-11-12-f6-lattice-build/apiprobe/SUMMARY.txt).

Run: `Ran 1 test in 2.336s`, OK (command and environment in `run.txt`; analytics were served by the raw path, so the rollup path was not exercised; the timings come from a tiny database and say nothing about production).

| What was checked | Measured | Plan consequence |
|---|---|---|
| Request-level counts | `soc-kpis?period=24h`: `requests_inspected` 11, `requests_blocked` 2 (the redact-then-block request counted once), `requests_redacted` 2: **all equal the seed**; the 30-hour-old event is excluded | `soc-kpis` is the source for Traffic and Verdicts |
| Watched | `requests_allowed` is 7 = 6 allow + 1 monitor; there is no request-level monitored field | B7 |
| Event-level numbers | `policies/analytics?days=1`: `total_violations` 12 (it counts **all** events, including the 6 allow events), `total_redacted` **3** against the request-level truth of 2 | Must not source the Verdicts box (§6.1 box 2) |
| Bucket count | `module-trends?period=24h`: 25 buckets per module; `attack-vector-trends`: 25 | N-12 confirmed; `CellStrip` follows the API |
| `/api/health/` | `{"status": "ok"}` | N-6, B4 |
| `/api/health/services/` | 641 ms, unauthenticated (`AllowAny`), probes Celery and RabbitMQ | Not for box polling |
| Keys | `last_used_at` null on the new key; the create response carries the plaintext once with a `warning` | N-7 (not proof on its own: there was no traffic); S5 "shown once" |
| `/api/policies/` | **400 `{"policy_domain": …}`**: the list requires a domain | N-14, B15 |
| Malformed parameters | `soc-kpis?period=abc` → 400 with `code: invalid_period`; `owasp-stats?hours=abc`, `policies/analytics?days=abc`, `policy/top-rules?days=abc`, `threat-feed?hours=abc` → **400** `"Invalid request parameter."` **without a `code`** (`main_app/exception_handlers.py:57` maps `ValueError` to 400) | B2 narrowed: one parameter, plus a `code` on every 400. There is no 500 to fix |
| Unknown event | `threat-feed/999999999/` → 404 `"Event not found."` | Request detail 404 page (S4) |
| Analytics cap | `soc-kpis` with both permits held → **503 `{"error": "analytics_busy", …}`** | §6.3 retry; no `Retry-After` is set (`analytics_concurrency.py:34-40`) |

### 6.6 Agreement checks (CLAUDE.md phase 7: UI, API, DB, logs and metrics must agree)

| Check | Where | How |
|---|---|---|
| A1: UI = API = DB | Every PR, `frontend-e2e` | A seed script writes a fixture with known counts (the probe's shape, extended per slice). Each box renders its raw value in `data-value`. The test fetches the same endpoint with the same parameters through `page.request` and asserts DOM value = API value = the seed's expected count. Any mismatch fails |
| A2: UI = logs | Every PR | Each box fetch sends `X-Request-ID: ui-<box>-<uuid>`. The test collects these from the network log and asserts that control's captured stdout has each id with the right path and status (`CorrelationIDMiddleware`, N-13) |
| A3: freshness truth | Every PR | Playwright blocks one source (route abort or 503), so that box (and only that box) becomes Stale within `freshFor` and Error on a cold load. Unblocking returns it to Fresh. A 503 `analytics_busy` is retried and recovers |
| A4: live stack | Release candidate (manual workflow, live compose on a GitHub runner) | N requests with a per-run nonce through the gateway (v2 once F3 ships; ties to F1 exit L36-1). Wait for the drain. Then gateway metrics (`amf_gateway_requests_total` on v1), DB rows, API values and the console UI must agree. The result goes in the cutover PR |

## 7. Testing

### 7.1 Backend validation first (phase 6), for every backend issue B1–B15

Each backend issue gets its own PR with:

- Django tests for malformed input (400 with `code`), boundaries (empty window, maximum window, 0 rows, 100k rows via the rollup path), tenant isolation (a canary in a foreign org must not appear: the #120 class), and concurrency (the semaphore, 503 shape);
- the timeout path (504 shape) and logs (request id present, no secrets).

A frontend slice that uses a new field starts only after that PR is merged.

### 7.2 Playwright suites (phase 7)

| Suite | Contents | Browsers |
|---|---|---|
| `nav.spec` | Every route by URL, refresh on every route, Back/Forward across sidebar navigation and filter changes, legacy `?tab=` redirects, 404, `next` after login keeps path and query, open-redirect guard (`next=https://evil`, `next=//evil` → `/console/overview`) | Chromium, Firefox, WebKit |
| `session.spec` | Expired access token refreshes silently; expired refresh token → `/login?next=<deep link>` and back after sign-in; the 15-minute inactivity warning and logout (clock-controlled); logout in tab A → tab B goes to login on its next action; theme change propagates across tabs | Chromium, WebKit |
| `boxes.spec` | The §7.4 invariants on every page; A1–A3 agreement; each box state reached on purpose: loading (delayed route), empty (empty fixture), error (500), stale (blocked source), unavailable (v1 capability), forbidden (role without access) | Chromium |
| `forms.spec` | Keys: create (secret shown once, copy, warning before leaving), edit, revoke, delete revoked, validation errors. Policies: create, edit, add and reorder rules, test, publish, **409 conflict** from a second tab, discard draft, draft survives a reload | Chromium, Firefox, WebKit |
| `workflows.spec` | Overview → box source → requests list (filtered) → request detail → policy that decided → test → publish; request detail "Replay in simulator"; interrupted publish (reload during compile, then compile status is truthful) | Chromium |
| `a11y.spec` | axe on every route × theme × viewport; keyboard-only walk per drawn screen; 320 px / 200% reflow | Chromium (axe), WebKit (keyboard) |
| `visual.spec` | `toHaveScreenshot` per drawn screen × {1440x900, 834x1112, 390x844} × {dark, light} = 6 baselines per screen (viewport, not full page). Full-page captures are uploaded as artifacts for the owner | Chromium |
| `landing.spec` | Landing page meta, `robots`, no third-party requests, Lighthouse budget (§3.3), visual baselines (6) | Chromium |

Determinism for screenshots:

- a fixed clock (`page.clock.install`);
- seeded data;
- self-hosted fonts awaited (`document.fonts.ready`);
- animations disabled;
- Playwright version pinned by the lockfile;
- baselines generated **on the GitHub runner image only** (`ubuntu-24.04`).

The tolerance is `maxDiffPixelRatio: 0.001`. Any larger change needs new baselines, reviewed as images.

### 7.3 Extreme and edge cases (phase 8), added per slice until no new failures appear

- **Maximum limits:** 500 keys (`max_page_size`), 1,000 policies, long names and codes (ellipsis plus full text in the tooltip and detail), counts of 1e9 (formatting, no box overflow), a 30d window on rollups.
- **Minimum limits:** a new org with zero data (every box Empty with its sentence); one event.
- **Failures:** 401 mid-poll; 503 storm (all analytics busy); 504; network offline and back; control restart during polling; WebSocket drop and reconnect (no UI, refetch on reconnect); slow 3G (no layout shift; skeletons).
- **Repeated actions:** double-click create, rapid range switching (only the last range's data renders, using the generation guard as today's overview does), rapid Back/Forward.
- **Concurrency:** two operators editing one policy (409); revoking a key while it is shown in another tab; two tabs polling (hidden-tab pause verified).
- **Time:** IST and UTC bucket labels (N-12); DST zones; the client clock off by ±10 min (fetch age unaffected; data age uses `Date` skew).

### 7.4 Brief-as-tests (owner rules made executable)

- Every element with `data-box` has a non-empty title, purpose, freshness word, age and source label, at every viewport.
- No `role="tablist"` anywhere under `/console/` (segmented value pickers use `role="radiogroup"`).
- No bell: no control whose name matches `/notif/i`, and **no request to `/api/notifications/`** from any console route (network assertion).
- No list headed "needs attention", "for review" or "since you last looked", and no live region that announces new events or records (form error summaries may use `aria-live`).
- The palette lint: 0 raw classes, `dark:` variants or hex literals under `src/console/`.

## 8. Delivery slices (one PR each, into `develop`)

Every slice:

- carries before and after screenshots for the owner (the quality gate) in `docs/plans/evidence/<date>-f6-<slice>/`;
- passes `frontend` (lint, unit, build, budgets) and `frontend-e2e`;
- gets two reviewers (A and B; B merges).

Prod stays on the legacy console until S9 (`VITE_LATTICE=off` in prod builds; on for develop and staging builds).

| Slice | PR title | Contents | Acceptance criteria (summary; full checklists go in each PR) |
|---|---|---|---|
| **S1** | `feat(frontend): Lattice tokens, fonts, theme default and lint gates` | `design/tokens.mjs` → css/json; `@theme inline`; `[data-ui=lattice]` scope; Geist self-hosted; dark default plus pre-paint script; `lint-palette` ratchet (baseline 9,746); contrast gate; Playwright and axe harness plus the `frontend-e2e` job with a smoke spec (login, both themes); bundle-budget script (report only) | Token files regenerate with no diff; contrast 0 failures; the ratchet fails a planted raw class in `src/console/`; a fresh profile is dark with no flash; DESIGN.md and code agree; no legacy view changes except the default theme (every view in AUDIT.md's route map, before and after) |
| **S2** | `feat(frontend): Lattice shell and real routes` | Router with `/console/*` routes (lazy, code-split); sidebar (numbered checkpoints, Tools, Settings; no bell); top bar (search dialog, range picker, theme, account); legacy pages mounted at new routes; legacy `?tab=` map; `next` handling; 404; "Connections" (A-08); `useSource` and the scheduler; X-Request-ID | `nav.spec` and `session.spec` green on 3 browsers; Back/Forward and refresh on every route; no `/api/notifications/` calls; search finds keys, policies, rules and event ids (A-04); the initial JS for `/login` ≤ 120 kB gzip (from 646 kB) |
| **S3** | `feat(frontend): Lattice overview boxes` | Box, Chip, chart primitives, all box states; the boxes whose sources exist (§6.1: 14 of 16, partial ones with their sourced fields only) | A1–A3 green; every state reached in `boxes.spec`; visual baselines (6); axe 0; initial JS for `/console/overview` ≤ 250 kB gzip; 20 concurrent sessions against gunicorn and uvicorn (2 workers): p95 per source and 503 rate recorded, no box in Error |
| **S4** | `feat(frontend): requests list and request detail` | `/console/requests` (paged threat-feed table, filters in the URL, manual refresh, no auto-insert) and `/console/requests/:eventId` (the boxes from `request.html`, Record freshness, Copy link, Replay) | A deep link works after a refresh; an unknown or foreign event id gives a 404 page (tenant canary); stage durations equal `pipeline_trace.stage_metrics_ms`; v1 events without a trace show Unavailable for the stage box |
| **S5** | `feat(frontend): gateway and API keys (1.1)` | Keys page from `keys.html`; create, edit, revoke and delete forms at URLs; call snippet from `gateways/public-url` | `forms.spec` keys green; the secret is shown once and never stored (no storage or URL contains it; network assertion); boxes without B5 not rendered; "Expiring soon" and "Key hygiene" per owner Q6 |
| **S6** | `feat(frontend): policy editor (1.2)` | Grouped policy list (Pipeline, RAG, MCP, Vector; no tabs); editor; rules table with reorder; test against the draft (`policies/test`); client draft in `sessionStorage` keyed by policy id and base `version`; publish = PATCH with `version` (409 handled) then compile, then poll `compile/status` | 409 path tested from two tabs; the draft survives a reload and is discarded on publish; the "Rollout" box waits for B4; "Hits by rule" waits for B8; "Mapped to" waits for B10 or is dropped |
| **D2** | `docs(design): Lattice mockups for 1.3–1.7, simulator, connections, settings` | Static mockups in the same folder plus PNGs (desktop, mobile, both themes) for the owner | Owner picks before S7 code |
| **S7a–e** | `feat(frontend): Lattice <page>` (one PR per page or pair) | 1.3, 1.4 (removes 5 tab strips), 1.5, 1.6, 1.7, simulator, connections, settings and profile | Each page's palette count reaches 0; legacy panel files deleted when unused |
| **S8** | `feat(landing): static Lattice landing page` | `frontend/landing/` prerendered entry, SEO files, OG image, Lighthouse CI | §3.3 budget green; no third-party requests; visual baselines; copy approved by the owner |
| **S9** | `feat(frontend): cut over to the Lattice console and landing page` | `VITE_LATTICE=on` in prod; nginx `location = /` landing plus the legacy `?tab=` map; `noindex` on the console; `robots.txt` | A4 live-stack agreement recorded; rollback rehearsed on staging (flag off → legacy back) |
| **S10** | `chore(frontend): retire the legacy console` (at least one release after S9, owner OK) | Delete the legacy shell, pages, notification UI and unused chart library; `--color-*: initial`; drop the Google Fonts imports; move tokens to `:root` | Palette baseline 0 (the lint baseline file is deleted); bundle numbers recorded; all suites green |

### 8.1 CI jobs (on GitHub runners; local runs are targeted only)

- `frontend` (existing, `reusable-node-ci.yml`): add `lint-command: npm run lint`; `build-command: npm run build && npm run check:budget`.
- `frontend-e2e` (new reusable `reusable-web-e2e.yml`, called from `ci.yml`):
  1. the same pinned service images as `reusable-django-ci.yml` (`pgvector/pgvector:pg16@sha256:0dd3149a…` and `redis:7.4-alpine@sha256:7b1753b4…`); Python 3.12 with control installed (`-e control[dev] -e shared`);
  2. `migrate`, then the seed script;
  3. control under gunicorn with 2 `UvicornWorker`s (prod-like, so N-4 is exercised), stdout captured for A2;
  4. `vite preview` (its proxy defaults to `server.proxy`);
  5. `npx playwright install --with-deps chromium firefox webkit`, then the suites;
  6. upload the HTML report, diffs and full-page screenshots as artifacts.

  `timeout-minutes: 30`. Every `uses:` pinned to a full SHA, `permissions: contents: read`. A `changes` step skips quickly (with success) when neither `frontend/` nor `control/` changed, so the check can be required without hanging.
- **Baseline updates:** `workflow_dispatch` with `update_snapshots=true` runs the visual specs with `--update-snapshots` and uploads `__screenshots__`. The author downloads the artifact with `gh api …/actions/artifacts/<id>/zip` into an empty directory (verified reachable from this host on 12 Nov), looks at every image, and commits them. No workflow gets write access to the repository.
- `landing-lighthouse` (S8): Lighthouse CI against `vite preview` of the landing build, asserting §3.3.
- Rule 6: S1 changes `.github/` (adds jobs; relaxes no gate) and needs reviewer B's explicit sign-off. `frontend-e2e` becomes a required check after 3 consecutive green runs on `develop` (owner or B updates the ruleset).
- Cost: about 12–18 minutes per PR touching `frontend/` or `control/`, well inside the 50,000 Actions minutes per month.
- **Local runs on this host** are targeted only: 2 CPUs, no Docker, and native Postgres 16 **without pgvector**, so creating a control test database needs the out-of-repo settings shim other agents use (it strips `CREATE EXTENSION vector` from migration `policy/0021` and gives the test database its own name). The probe in §6.5 ran that way. CI needs no shim.

## 9. Risks and rollback

| Risk | Mitigation |
|---|---|
| Boxes without honest sources (§6.1) | Never render a fake, derived or zero value; ship sourced boxes; B-issues tracked; owner Q5 |
| Mockup source lines are wrong for 4 boxes (2, 4, 5, 8), and box 3 names a field | §6.1 corrects them; the footer names the endpoint actually read |
| v1 is the production data plane (R16); v2-only fields are absent in prod | Unavailable state; capability detection per source |
| Analytics load and N-4 503s | Deduplication, an in-flight cap of 2, jittered retry; the S3 load measurement with 20 sessions |
| Visual baselines flaky across environments | Generated only on the CI runner; fixed clock; self-hosted fonts; viewport-sized shots |
| Legacy code is large (47.6k lines); migration is long | Legacy pages are mounted in the new shell from S2, so URLs and the shell are fixed early; per-page slices; the palette ratchet shows progress |
| Interim look on staging (Lattice shell around legacy pages) | Prod is unaffected until S9; owner screenshots per slice |
| Bundle growth | Budgets enforced from S2; route-level code splitting; ECharts lazy |
| New dependencies (`@tanstack/react-query`, `@playwright/test`, `@axe-core/playwright`, `geist`, Lighthouse CI) and the 178 open Dependabot alerts | Lockfile pinned; the dependency-scan job; dev dependencies kept out of the prod bundle; the owner can veto TanStack Query (in-house hook fallback) |
| Public repository | No secrets in fixtures; AI-DSPM reference images (owner Q8); wordmark source (owner Q9) |

**Rollback:**

- Before S9, every slice is a plain revert, and prod never sees `/console/*` because of the build flag.
- After S9, rebuild with `VITE_LATTICE=off` (the legacy shell and pages remain until S10) and revert the nginx `location = /` change. S9 rehearses this on staging.
- S10 is only done after one release with no rollback, and the owner agrees.
- Backend issues ship independently, each with its own rollback.

## 10. What this plan does NOT do

- No code (no `frontend/`, `control/` or CI changes in this PR).
- No designs for 1.3–1.7, the simulator, connections, settings or the requests list (D2 and owner Q7).
- No backend endpoints: B1–B15 are proposals for separate issues and PRs.
- No marketing copy beyond the mockup, no pricing page, no claims (owner Q6/Q10).
- No i18n or RTL, no offline/PWA, no product analytics or trackers, no SSO changes, no change to `/oauth/callback`.
- It does not settle AUDIT.md's placeholder (#129). The audit findings this plan needs are re-verified in §2.2.

## 11. Owner questions (each with the default used if there is no answer)

| # | Question | Recommended default |
|---|---|---|
| Q1 | Where does the landing page live: `/` on the console host, a separate host, or zeroshield.ai? | `/` on the console host; console at `/console/…`; `/login` and `/oauth/callback` unchanged |
| Q2 | Product name on the landing page | "ZeroShield" wordmark with "AI Mesh Firewall" as the product line (as drawn) |
| Q3 | Default theme | Dark, for the console and the landing page; the user's choice persists; Light and System in Settings |
| Q4 | Confirm violet `#A48CFF` / `#6B4FE0` and Geist as AI Mesh Firewall's identity | Yes, as approved with Lattice |
| Q5 | Boxes whose data does not exist yet (overhead, fleet; partial fields) | Hidden until the source exists; partial boxes show only sourced fields; never a zero or a placeholder number |
| Q6 | The keys page's "Expiring soon" (with "Rotate both") and "Key hygiene" boxes read as "needs attention" lists, which the brief forbids | Replace with neutral distribution boxes: "Key lifetimes" (counts by expiry bucket: none, < 7 d, < 30 d, later) and "Key scope" (counts by allowed-model breadth), with no action buttons |
| Q7 | The Requests list is not drawn. Is a filterable, paged table acceptable? | Yes: newest first, filters in the URL, manual refresh, **no** live insertion, no "new" badges, so it is not a feed |
| Q8 | May the AI-DSPM reference screenshots of the public demo (`ref-aidspm-*.png`, `f6-compare-lattice-*.png` in PR #123) stay in this **public** repo? | No: remove them from #123 before merge and keep them privately; the Lattice PNGs stay |
| Q9 | Wordmark source: the PNG was taken from the AI-DSPM console's assets | Use it until an official SVG wordmark is supplied; please supply one |
| Q10 | Landing CTA target ("Book a demo") and approved claims | CTA links to a contact URL you give; no numbers, customers or certifications until approved |
| Q11 | Any analytics or tracking on the landing page? | None |
| Q12 | The bell carried incident escalations to admins. With no bell, escalations show in the request detail and as a Requests filter (`?status=escalated`, which needs the `threat-feed` filter in B9) only | Yes; no other surfacing (email or webhook alerting stays as the backend already provides) |
| Q13 | Keep the 1.1–1.7 numbering in navigation | Yes, number plus name (as drawn) |

## 12. Author's contradiction pass (for reviewers to attack)

- **Lens 1, false positives:**
  - N-7 rests on a search for writers of `last_used_at` across `control/`, `gateway*/`, `workers/`, `services/` and `shared/`, which found none, and on the probe (no traffic, so the probe alone cannot prove a sync never happens). To prove it either way, call the gateway with a key on the live stack and read `/api/gateways/keys/`. Planned in B5.
  - N-4 may never fire in production: under ASGI, Django runs sync views on one thread per worker (`thread_sensitive`). The UI handles 503 anyway because it is the endpoint's documented contract.
- **Lens 2, hidden failures:** request-level KPIs against event-level series (box 1) and event counts (`policies/analytics`) disagree by design. The plan never puts an event-based number next to a request-based number in one box.
- **Lens 3, state:** stale caches after mutations (§6.3 invalidation), multiple tabs (§7.2 `session.spec`), hidden-tab pause, a draft against a moved live version (409).
- **Lens 4, observability:** box errors today go only to `console.error`. The plan adds request ids to every fetch and checks them against control logs (A2). Server-side data age is not observable until B1/B12, so the plan does not claim it.
- **Lens 5, edge cases:** 25-bucket grids and IST labels (N-12), the v1 Unavailable state, 0 rows and 1e9 counts, forbidden roles, malformed URL parameters (`?range=abc` → normalised with `replace`, never a 400 to the API).

## Appendix: evidence index

| File | What it backs |
|---|---|
| `evidence/2026-11-12-f6-lattice-build/counts.sh`, `counts.txt` | A-05 counts, tab-strip counts, fonts, line counts at `562619c8` |
| `evidence/2026-11-12-f6-lattice-build/vite-build-develop-562619c8.txt` | The bundle baseline (2,211.83 kB JS, one chunk) |
| `evidence/2026-11-12-f6-lattice-build/apiprobe/test_lattice_probe.py` | The probe (runs in the control test runner; throwaway DB) |
| `evidence/2026-11-12-f6-lattice-build/apiprobe/probe-results.json`, `SUMMARY.txt`, `run.txt` | §6.5 results, command and duration |
