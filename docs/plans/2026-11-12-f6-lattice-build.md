# F6 build plan: Lattice console and public landing page

| | |
|---|---|
| Status | **Plan for review, revision 2.** No `frontend/` code in this PR. Feature PRs start only after two plan reviewers approve (owner rule). |
| Date | 12 Nov 2026 |
| Base | `develop` at `562619c8` (code facts below are read at this commit) |
| Design input | PR #123, branch `plan/f6-frontend-directions` at `2e1b6f09`: Lattice mockups, tokens, sample data, `DIRECTIONS.md`, PNGs. The owner approved Lattice on 12 Nov. |
| Evidence | [`evidence/2026-11-12-f6-lattice-build/`](evidence/2026-11-12-f6-lattice-build/) |
| Revision 2 | Answers both rev-1 reviews (Plan Reviewer A, review 5871851572; Plan Reviewer B, review 5871852636). Every finding has a row in [Appendix B](#appendix-b-rev-1-review-dispositions). New probe: `apiprobe/test_lattice_probe_rev2.py` (§6.5). |

Mockup permalinks (PR #123 is not merged, so links are pinned to its head):
[console](https://github.com/CyberUltron-Consulting-Private-Limited/AI_Mesh_Firewall/blob/2e1b6f096b280038990477e9cfff7636a779fb74/docs/plans/evidence/2026-11-11-f6-frontend/directions/mockups/lattice/console.html),
[request](https://github.com/CyberUltron-Consulting-Private-Limited/AI_Mesh_Firewall/blob/2e1b6f096b280038990477e9cfff7636a779fb74/docs/plans/evidence/2026-11-11-f6-frontend/directions/mockups/lattice/request.html),
[keys](https://github.com/CyberUltron-Consulting-Private-Limited/AI_Mesh_Firewall/blob/2e1b6f096b280038990477e9cfff7636a779fb74/docs/plans/evidence/2026-11-11-f6-frontend/directions/mockups/lattice/keys.html),
[policy](https://github.com/CyberUltron-Consulting-Private-Limited/AI_Mesh_Firewall/blob/2e1b6f096b280038990477e9cfff7636a779fb74/docs/plans/evidence/2026-11-11-f6-frontend/directions/mockups/lattice/policy.html),
[landing](https://github.com/CyberUltron-Consulting-Private-Limited/AI_Mesh_Firewall/blob/2e1b6f096b280038990477e9cfff7636a779fb74/docs/plans/evidence/2026-11-11-f6-frontend/directions/mockups/lattice/landing.html),
[tokens.css](https://github.com/CyberUltron-Consulting-Private-Limited/AI_Mesh_Firewall/blob/2e1b6f096b280038990477e9cfff7636a779fb74/docs/plans/evidence/2026-11-11-f6-frontend/directions/mockups/lattice/tokens.css),
[tokens.mjs](https://github.com/CyberUltron-Consulting-Private-Limited/AI_Mesh_Firewall/blob/2e1b6f096b280038990477e9cfff7636a779fb74/docs/plans/evidence/2026-11-11-f6-frontend/directions/mockups/tokens.mjs).

## 0. Summary

Lattice is built as a new console shell at real URLs under `/console/…`, inside the existing React 19 + Vite 7 app, behind a build flag until a route-level cutover. Legacy pages are mounted in the new shell from the second slice on, so every view gets a URL early (A-02, A-03) and is then rebuilt page by page. The public landing page is a separate, prerendered static entry in the same Vite project. By default it is served from its **own origin**, not from `/` on the console host (owner Q1, default changed in rev 2).

The mockup's 16 overview boxes name 14 sources. Measured against `develop`: **5 boxes can be fully sourced today, 10 are partial, and 1 has no endpoint** (firewall overhead). The gateway fleet box is partial and is held back by default. The v1 gateway already measures firewall overhead; control just does not expose it (rev-1 N-10 was wrong). The keys, request detail and policy editor pages now have their own source maps (§6.1b). A box, field or chip ships only when its source exists; no number or status word on screen is derived, padded or invented.

Rev 2 changes the plan where the reviews proved it wrong:

- **Policy editor:** rule writes go live one at a time and republish the org plan, and the 409 check does not cover them (N-11). So S6 is split. S6a ships editing that is labelled live. S6b (draft, test against the draft, atomic publish) waits for backend issue B13, which is now required.
- **Agreement with logs:** control writes no log line that carries the request id (N-13). A new backend issue, B16, adds one, and S3 depends on it.
- **Load:** analytics cadence goes from 15 s to 30 s, the WebSocket leaves the Lattice shell, retries back off and honour `Retry-After`, and there is a load model plus an S3 load gate. A per-org response cache (B18) is a cutover criterion unless S3 measures enough headroom.
- **Freshness:** analytics boxes say "Updated", not "Fresh", until the server reports data age (B1, B12). Clocks are skew-corrected everywhere, and sleep and resume are handled.
- **Security:** the `next` guard algorithm and its tests are specified, a real CSP is added, token storage is recorded as a risk (B20), and legacy key persistence is removed.

Delivery takes 12 PRs after this plan, plus one PR per S7 page (§8). Each slice has its own dependencies and acceptance criteria. Every slice runs Playwright e2e on Chromium, Firefox and WebKit, visual baselines (1440x900, 834x1112, 390x844; light and dark), axe, and UI = API = DB = log agreement tests on a seeded database, all in a new `frontend-e2e` CI job on GitHub runners. The backend gaps are now **21 issues plus one optional** (§6.4); #160 needs the corrections listed there.

## 1. Owner brief (binding, as relayed on 12 Nov)

- Lattice: "inspired by AI-DSPM, own identity". Violet accent `#A48CFF` (dark) / `#6B4FE0` (light); Geist and Geist Mono.
- The overview is many small boxes side by side. Each box has its own purpose and a source and freshness line.
- **No** notification bell. **No** notification or activity feed. **No** "needs attention" lists. **No** tab strips of small views.
- Rendered visual output is the owner's quality gate.
- Process: plan PR first, two plan reviewers, then feature PRs. Tests run on GitHub runners.
- v1 `gateway/` is the live data plane until the v2 cutover; v1 is frozen (no v1 changes in this work).

These become automated checks (§7.4), not just review items.

## 2. Discovered architecture (develop `562619c8`)

### 2.1 Frontend

| Area | Fact | Evidence |
|---|---|---|
| Stack | React 19.2, react-router-dom 7, Vite 7.3.3, Tailwind 4 (`@theme` in CSS, no `tailwind.config.js`), CVA primitives in `components/ui/` (20), lucide, motion, ECharts 6 (+ `echarts-for-react`), uPlot | `frontend/package.json` |
| Size | 47,624 lines of `.js`/`.jsx` in `src/`; the biggest files are `MCPConnectorPanel.jsx` (2,739), `PolicyManagementPanel.jsx` (2,060) and `MCPManagerPanel.jsx` (1,955) | `counts.txt` |
| Routing | 5 router routes: `/login`, `/signup` (redirects to `/login`), `/oauth/callback`, `/` (protected) and `*` (redirects to `/`). The view comes from `?tab=` plus two in-memory states (`resultsTab`, `logDetailData`) | `App.jsx:50-51,97-110,184-200` |
| State | No store library. `AuthContext` (JWT access and refresh in `localStorage`, refresh mutex, 15-minute inactivity logout), `ThemeContext`, `FirewallConfigProvider`, per-panel `useState` + `fetch` | `context/*.jsx`, `AuthContext.jsx:29-33`, `DashboardLayout.jsx:17-20` |
| Polling | `startVisibleInterval` (skips ticks while the document is hidden): overview every 10 s, `useFirewallData` every 15 s, simulators every 3 s | `utils/visiblePoll.js:4-9`, `AIMeshFirewallOverview.jsx:686`, `useFirewallData.js:169` |
| Realtime | One shared WebSocket `/ws/notifications/?token=<JWT>` (the token is in the query string). It feeds the bell, triggers an overview refetch with a 2 s trailing debounce, and is also opened by `useFirewallData` (module pages, results page, 1.2 page) | `useRealtimeNotifications.js:51-55,166`, `AIMeshFirewallOverview.jsx:695-706`, `useFirewallData.js:5,157` |
| Key material in storage | Three legacy hooks write gateway key plaintext to `localStorage` | `hooks/useGatewayCredential.js:52`, `hooks/useSimulatorEngine.js:54`, `hooks/useIsolationPlayground.js:51` |
| Build output | **One JS chunk of 2,211.83 kB (646.14 kB gzip)** and 224.66 kB of CSS (28.53 kB gzip). There is no code splitting, so `/login` downloads everything | `vite-build-develop-562619c8.txt` |
| Fonts | Three Google Fonts `@import`s (Inter, JetBrains Mono, Plus Jakarta Sans) | `index.css:2-4` |
| `innerHTML` | No `dangerouslySetInnerHTML` or `innerHTML` anywhere in `src/` (0 matches) | `grep -rn` at `562619c8` |
| Tests | `node --test` unit tests only. `npm run lint` runs the unit tests, so there is no lint. `tests/visual/run.sh` needs Docker and a running stack, and is not in CI | `package.json` scripts, `tests/visual/run.sh` |
| CI | The `frontend` job calls `reusable-node-ci.yml` (`npm ci`, `test:unit`, `vite build`). It runs no browser tests | `.github/workflows/ci.yml:38-44` |

### 2.2 Audit findings this plan relies on (re-verified here; AUDIT.md's findings section is still a placeholder, issue #129)

| ID | Finding | Evidence at `562619c8` | Fixed in slice |
|---|---|---|---|
| A-02 | No real URLs. "Detailed results" and the log detail are in-memory state, so a refresh or a shared link loses them | `App.jsx:50-51` (`useState` for `resultsTab`, `logDetailData`), `App.jsx:98-110` | S2 (shell), S4 (request detail) |
| A-03 | Tab changes use `replace: true`, so Back leaves the app instead of returning to the previous view | `App.jsx:54-60` (`setSearchParams(…, { replace: true })`) | S2 |
| A-04 | The header search is not connected: `App` passes no `onSearchSubmit`, so Enter just clears the field | `App.jsx:178` (no search props), `Header.jsx:253-259` | S2 |
| A-05 | **9,746** raw Tailwind palette classes in 85 files (the audit's regex counted 9,651), plus 4,471 `dark:` variants and 157 hex literals in JS | `counts.sh` → `counts.txt` | S1 (gate), S3–S7 (migration), S10 (palette removed) |
| A-08 | The sidebar item "Inputs" opens the firewall configuration page, which holds model and database connections plus nine config cards | `Sidebar.jsx:35`, `App.jsx:173`, `AIMeshFirewallConfig.jsx:14-25` | S2 (mounted at `/console/connections`), S7 (split) |
| A-09 | DESIGN.md says the theme "default resolves to dark", but the code defaults to light | `DESIGN.md:13` against `ThemeContext.jsx:20` | S1 |

New findings from this exploration (each one changes a slice). Rev 2 corrects N-5, N-10, N-11 and N-13 and adds N-15 to N-23.

| ID | Finding | Evidence | Consequence |
|---|---|---|---|
| N-1 | Login returns only to `from.pathname`. The query string is dropped, so `/?tab=firewall-1-2` comes back as `/` after a session expiry | `Login.jsx:28`, `ProtectedRoute.jsx:19` | S2: `next` keeps path, query and hash, with the guard in §3.4 |
| N-2 | The Settings page stores "desktop" and "sound" notification toggles that nothing reads | `Settings.jsx:33-46`; no other reader of `zeroshield_*_notifs` | Removed (brief: no notifications) |
| N-3 | Tab strips: 14 uses of `role="tablist"`, `<Tabs>` or `SegmentedControl` outside `ui/`, 5 of them in `MCPConnectorPanel.jsx` | `counts.txt` | Replaced by grouped lists or sections (S6, S7) |
| N-4 | Heavy analytics views take a per-worker semaphore of 2 (`ANALYTICS_MAX_INFLIGHT`) and return **503 `analytics_busy`** with no wait and no `Retry-After`. Under ASGI each request runs in its own thread (`ThreadSensitiveContext`, Django 6.0.10 `core/handlers/asgi.py:169`), so the third concurrent analytics call per worker gets the 503 | `policy/analytics_concurrency.py:10-44`; used by `soc-kpis`, `module-kpis`, `module-trends`, `attack-vector-trends`, `dashboard/model-usage`, `dashboard/risk-distribution`; probe P4 | §6.3 scheduler; B19 |
| N-5 | **Rev 2 (corrected).** The window parameter differs per endpoint, and so does the handling of bad values: `period` views return 400 `invalid_period` (`analytics_period.py:42-47`). `days`/`hours` views that parse with a bare `int()` return 400 **without** a `code` for non-numbers (`exception_handlers.py:57`). `dashboard/model-usage` silently falls back to 30 days (`clamp_days`, `analytics_period.py:58-65`; probe P6: `days=abc` → 200). `owasp-stats` accepts `hours=-5` and returns an empty 200 for a window in the future (`security_views.py:1587`; probe P6). `threat-feed` silently clamps `hours` to 1–8,760 (`security_views.py:479`) | as listed | B2 forbids silent fallbacks. A box footer states the window the **response** says it used (B1), never the one the client asked for |
| N-6 | `/api/health/` returns only `{"status": "ok"}` | `core/views.py:21-23` | No fleet source there (B4) |
| N-7 | `GatewayAPIKey.last_used_at` is stamped in the gateway's Redis payload and never written back to Postgres, so the keys API reports it as null. The model's help text "Updated asynchronously…" is wrong | Writer only in `gateway/ai_mesh_gateway/middleware.py:531-559` (Redis Lua); no writer in `control/`, `workers/`, `services/`, `shared/` or `gateway_v2/`; `core/models.py:365` | "Idle" and "last used" have no source (B5) |
| N-8 | 401 auth failures are never recorded. `auth_status` and `rate_limit_status` on events are flags derived from `threat_type` for requests that already passed auth | `core/tasks.py:271-272` | "Auth failures" and "Rate limit headroom" have no source (B5) |
| N-9 | MCP `last_health_at` and `last_health_status` are serialised read-only but never written (the default is `unreachable`). `mcp-connector/health/` reports the MCP firewall service and its detector list, not server reachability | `mcp_connector/models.py:88-97`, `serializers.py:45-46,68-69`; writers: none; `views.py:1123-1149` | The MCP box uses `connection_status` from the last sync (B6) |
| N-10 | **Rev 2 (corrected; rev 1 said v1 does not measure it).** v1 measures firewall overhead. It exports `amf_gateway_t_addon_pre_seconds`, `amf_gateway_t_addon_post_seconds`, `amf_gateway_t_t2_seconds` and `amf_gateway_pipeline_stage_seconds` per org, observed in the chat handler's `finally`. Every traced v1 event also carries `pipeline_trace.t_addon_pre_ms` and `t_addon_post_ms`. **Control exposes none of it** | `gateway/ai_mesh_gateway/metrics.py:215-233,270-305,457-485`; `main.py:11946-11949,12064-12075`; `pipeline_trace.py:775,1766`; control reads the trace at `policy/security_views.py:886-899` | The overhead box needs an endpoint (B3), not v2. It must **never** say "Not measured" on v1 |
| N-11 | **Rev 2 (corrected).** There is no draft state, **and rules are not covered by the policy version.** `PolicySerializer.rules` is read-only, so rules are written through `RuleViewSet`. Every rule create, update or delete republishes the org plan and never reads or bumps `Policy.version`. Probe P1: 4 rule writes (PATCH, 2 POSTs, DELETE) → 4 plan publishes, version still 1, and a policy PATCH carrying the original `version` → **200**, not 409 | `policy/serializers.py:221`; `policy/views.py:22-39` (`_stamp_plan`, `_publish_org`), `:900-972` (`RuleViewSet`), `:597-636` (policy-level 409 only); probe P1 | S6 split into S6a (live, labelled) and S6b (draft and atomic publish, needs B13) |
| N-12 | Trend grids run from `since` to `now` **inclusive**, aligned to the current UTC hour (or 5 min for `1h`, 30 min for `6h`). So 24 h at 60-minute buckets gives 25 buckets, the last one partial. For 7 d (240-minute buckets) and 30 d (1,440-minute buckets) the cells are rolling windows ending at the current hour, not calendar days | `policy/analytics_sql.py:98-113` (`trend_grid`, `while cursor <= now`); `analytics_period.py:9-15`; bucket count measured in §6.5 | §5.4: the strip renders the API's buckets and labels them as start times |
| N-13 | **Rev 2 (corrected).** Control echoes `X-Request-ID` and binds it to log lines written **during** the request, but writes no access line. gunicorn runs without `--access-logfile`. Django's `django.request` 4xx/5xx line is written after the middleware has reset the id, so it carries an empty id. Probe P3: a 200 with an id writes no access line (the only records that carried the id were incidental config-signal lines naming no path or status); a 404 gives one `django.request` record with `rid=""` | `main_app/log_extras.py:151-165,170-183`; `control/server-entrypoint.sh:86-94`; Django `core/handlers/base.py:140-148`; probe P3 | A2 needs B16; S3 depends on it |
| N-14 | `GET /api/policies/` without `?policy_domain=` returns **400**. The domains are `pipeline`, `rag` and `mcp`; vector policies live at `/api/vector-policies/` | Probe §6.5; `policy/views.py:96-98,519-527`, `policy/models.py:27-33` | The policy list and box 6 make 4 calls (3 domains plus vector policies) until B15 |
| N-15 | **New.** The requests list (`threat-feed`, collapsed by request) pages with `offset` inside the newest **4,000 events** only, while `count` is the full distinct-request total. Pages past the scan are empty. Each call also runs several full-window queries for the action counts | `policy/security_views.py:356,531-552,565-611`; probe P11 | S4 needs B17 (a keyset-paged request list) |
| N-16 | **New.** `POST /api/policies/test/` evaluates the **saved, enabled** rules of a policy only. It accepts no draft rules, so a client-side draft cannot be tested. A disabled policy returns 404 | `policy/evaluation_views.py:922-1004`; probe P2 | "Test against the draft" needs B13 |
| N-17 | **New.** The analytics semaphore is taken in `dispatch()` before DRF authenticates. An anonymous call while both permits are held gets **503**, not 401 | `policy/analytics_concurrency.py:29-44`; probe P4 | §6.3: refresh the token before a request, not after a 503; B19 |
| N-18 | **New.** `EnforcementEvent.created_at` is the drain's insert time. The request's own time is only in `metadata.event_timestamp`. Analytics windows and trend buckets are therefore by **ingest** time | `policy/models.py:200`; `core/tasks.py:236` | §6.2: "Updated", not "Fresh", until B1 and B12; strips say "by ingest time" |
| N-19 | **New.** Escalations reach admins only as `Notification` rows plus a WebSocket broadcast. Control has no outbound email or webhook path; `api/webhooks/alertmanager/` is inbound | `policy/security_views.py:2323-2353`; `core/isolation_notify.py` (called from `kill_switch_views.py:216`, `model_state_views.py:317`); no `send_mail` or outbound webhook in control | Q12 restated; B9 is an S9 entry criterion |
| N-20 | **New.** The v1 Prometheus `decision` label is HTTP-status based: `allowed` = 2xx (so redacted and monitored requests too), `blocked` = any 4xx (including 401, 429 and validation errors that never reach telemetry), `error` = 5xx | `gateway/ai_mesh_gateway/main.py:235-252` | A4 maps labels to console words explicitly (§6.6) |
| N-21 | **New.** Gateway key PATCH carries no version, so concurrent edits are last-write-wins. The keys list has no `is_active` or expiry filter and pages at 100 (max 500) | `core/gateway_serializers.py:176-200`; `core/gateway_key_views.py:26-31,329-334`; probes P9, P10 | Box 5 and the keys page count in the browser across all pages until B5; the edit form says edits are last-write-wins |
| N-22 | **New.** Vector policies have a UUID primary key, no `version` and their own compile endpoint. System and MCP-seeded policies refuse rule writes from anyone who is not a platform admin | `policy/vector_models.py:56`, `policy/vector_urls.py:9-13`; `policy/views.py:939,958,965` | Own route; read-only state (§3.1, S6a) |
| N-23 | **New.** `gateways/stats` counts every enforcement event per gateway agent over **all time** (no window), adds a per-agent top-rules query, and never prunes agents, so dead agents stay in M | `core/gateway_views.py:135-181` (`:146` the unwindowed count) | Not polled by any box; box 4 held back (Q5) |

### 2.3 Backend, infrastructure and data path

- Control: Django 6.0 + DRF under gunicorn with `UvicornWorker` and a dynamic worker count (`control/server-entrypoint.sh:37-94`). Analytics reads use the `analytics` DB alias under `AnalyticsTimeoutMiddleware`, which returns **504 `query_timeout`** on a statement timeout (`main_app/analytics_timeout_middleware.py:41-42`). Transient DB faults return **503 `db_unavailable` with `Retry-After: 2`** (`main_app/exception_handlers.py:43-51`). The gateway admin proxy (used for RAG collections) is a synchronous `requests` call with a 10 s timeout that returns **502 `gateway_unreachable`** on failure (`core/gateway_admin_proxy_views.py:84-97`). The C-2 hourly rollups serve 24h/7d/30d windows when `ANALYTICS_SERVE_ROLLUPS` is on; `1h` and `6h` always read raw rows (`ROLLUP_PERIODS`, `policy/analytics_rollup.py:32`). Every analytics response says which path served it in `X-Analytics-Source: rollup|raw`.
- Data: the gateways emit telemetry to Redis, `drain_telemetry_from_redis` writes `EnforcementEvent` rows (`core/tasks.py:569`), and the analytics read those rows by insert time (N-18). **The UI cannot see drain lag today**; no endpoint exposes the backlog (B12).
- Serving: production bakes `frontend/dist/` into the nginx image (`deploy/Dockerfile.nginx:31`), behind Cloudflare. `location /` already does `try_files $uri $uri/ /index.html` with `Cache-Control: no-store` (`deploy/nginx-ssl.conf:115-120`), and `/assets/` is immutable for one year. Real client-side URLs therefore need no nginx change. nginx-generated 4xx return a JSON envelope with nginx's own `$request_id` (`deploy/edge-error.inc:16-23`); nginx-generated 5xx (upstream down) return nginx's stock HTML page.
- CSP: only `frame-ancestors 'none'` (`deploy/security-headers.inc`). Its comment says "a script-src policy would break the hashed Vite SPA". The source `index.html` has a single external module script and no inline script (`frontend/index.html`), and Vite emits it as an external hashed asset, so `script-src 'self'` does not break it. S2 proves this in e2e (§4.6).
- Data plane: v1 `gateway/` serves production and v2 is profile-gated off (SESSION_MEMORY §3, R16). Any field that only v2 produces (plan version per replica, `plan v14` on events) shows **Unavailable** on v1, never zero. Firewall overhead is **not** in that category (N-10).

## 3. Scope: screens, routes and migration

### 3.1 Route map (real URLs; Back, Forward and refresh work on each)

| Screen | Route | URL state | Drawn in Lattice | Replaces |
|---|---|---|---|---|
| Landing (public) | its own origin by default (Q1); `/` on the console host only if the owner chooses it | – | yes | new |
| Console host root | `/` → `/console/overview` (signed in) or `/login?next=/console/overview` | – | – | today's `/?tab=` app (legacy `?tab=` links are mapped first) |
| Sign in | `/login?next=…` | `next` (§3.4) | no (restyle) | `pages/Login.jsx` |
| Sign up | `/signup` → `/login` (unchanged) | – | – | `App.jsx:188` |
| OAuth callback | `/oauth/callback` (unchanged: registered with identity providers) | – | – | `pages/OAuthCallback.jsx` |
| Console root | `/console` → `/console/overview` (replace) | – | – | – |
| Overview | `/console/overview` | `range=1h\|6h\|24h\|7d\|30d` | yes | `AIMeshFirewallOverview` |
| Requests (list) | `/console/requests` | `range, checkpoint, action, source, model, q, status, before` (`q`, `status` only once B9 lands; `before` is the keyset cursor from B17) | **no** (owner Q7) | `SubModuleResultsPage` (`components/SubmoduleResultsPage.jsx`), `LogViewerPanel` |
| Request detail | `/console/requests/:eventId` (any event id of the request; the detail merges siblings, §6.1b) | – | yes | `LogDetailPage` |
| 1.1 Gateway and keys | `/console/gateway`, `/console/gateway/keys/new`, `/console/gateway/keys/:keyId` | – | yes | `Firewall11Page`, `GatewayKeyPanel` |
| 1.2 Policies | `/console/policies` (first policy selected), `/console/policies/:policyId` (integer: pipeline, RAG, MCP), `/console/policies/vector/:vectorPolicyId` (UUID, N-22), `/console/policies/new?domain=pipeline\|rag\|mcp\|vector` | `rule=<id>` (selected rule) | yes | `Firewall12EnterprisePage`, `PolicyManagementPanel`, `VectorPolicyPanel`, `PolicyAnalyticsPanel`, `PolicyDomainSwitcher` |
| 1.3 RAG and vectors | `/console/rag` | – | no (design PR D2) | `Firewall13Page` |
| 1.4 MCP tools | `/console/mcp`, `/console/mcp/servers/:serverId` | – | no (D2) | `Firewall14Page`, `MCPConnectorPanel` |
| 1.5 Model routing | `/console/routing` | – | no (D2) | `Firewall15Page` |
| 1.6 Kill switch | `/console/kill-switch` | – | no (D2) | `Firewall16Page` |
| 1.7 Output guardrails | `/console/output` | – | no (D2) | `Firewall17Page` |
| Attack simulator | `/console/simulator` | `scenario`, `replay=<eventId>` | no (D2) | `AttackSimulatorPanel` and the per-module simulators |
| Connections | `/console/connections` | – | no (D2) | S2 mounts `AIMeshFirewallConfig` here unchanged; S7 splits it (A-08) |
| Settings | `/console/settings` (firewall settings and appearance), `/console/settings/profile` | `#section` anchors | no (D2) | `Settings.jsx`, `Profile.jsx`, the nine config cards of `AIMeshFirewallConfig` (from S7) |
| Not found | any other `/console/*` path → a 404 page inside the shell; any other non-console path → a 404 page with "Open the console" and "Sign in" links | – | – | today `*` silently redirects to `/` |

Rules:

- Navigation **pushes** a history entry. Only normalisation (an unknown `range` corrected to `24h`, `/console` to `/console/overview`, `/` to `/console/overview`) **replaces**.
- Filters and the time range live in the URL, so a copied link reproduces the view. A text filter (`q`) is applied and pushed **on submit** (Enter or the button), never per keystroke.
- After sign-in with no valid `next`, the app goes to `/console/overview`.
- Legacy URLs keep working: `/?tab=firewall` → `/console/overview`, `firewall-1-1` … `firewall-1-7` → the checkpoint routes above, `firewall-config` → `/console/connections` (which links to the matching Settings sections once S7 splits it), `profile` → `/console/settings/profile`, `settings` → `/console/settings`. The map matches `KNOWN_TABS` (`App.jsx:37-43`). It lives in the SPA, because `/` on the console host stays the SPA (Q1 default). If the owner puts the landing page at `/`, the map moves to an nginx `map $arg_tab` on `location = /`, mirrored in a Vite dev/preview middleware so dev matches prod.

### 3.2 Existing console pages: migration or retirement

| Existing view (today) | Fate | Slice |
|---|---|---|
| Shell: `DashboardLayout`, `Header` (bell, notification dropdown, health badge, search), `Sidebar` ("Inputs", collapse) | **Replaced** by the Lattice shell. The bell, the dropdown and the `/api/notifications/` calls are removed. **The shell opens no WebSocket** (rev 2: no live UI, and the JWT no longer goes into a query string from the shell). Legacy pages mounted in the shell that use `useFirewallData` keep their socket until they are rebuilt (S7). The health badge is dropped (the fleet box replaces it once B4 lands) | S2 |
| Overview (`AIMeshFirewallOverview`, 1,134 lines) | Rebuilt as the box lattice | S3 |
| Detailed results (`SubModuleResultsPage`) | **Retired**. Becomes `/console/requests?checkpoint=1.x` | S4 |
| Log detail (`LogDetailPage`, "Scan Detail Report") | Rebuilt as request detail at its own URL | S4 |
| 1.1 (`GatewayKeyPanel` and `AttackSimulatorPanel` on the page) | Keys page rebuilt; the simulator moves to `/console/simulator` | S5, S7 |
| 1.2 (`Firewall12EnterprisePage`, policy tab strip) | Policy editor; policy domains become groups in one list (no tabs) | S6a, S6b |
| 1.3–1.7 module pages (`FirewallModulePage` + panels) | Mounted unchanged in the new shell in S2; rebuilt in Lattice after design PR D2 | S2, S7 |
| Inputs (`AIMeshFirewallConfig`) | Mounted unchanged at `/console/connections` in S2. In S7 it is split: connections stay there, the config cards move to `/console/settings` sections, each linked from its checkpoint page | S2, S7 |
| Profile, Settings | Restyled; the notification toggles (N-2) are removed | S7 |
| `HowToUse`, `RetiredOnGatewayV2`, `OrgIsolationBanner` | Audited in S7: each one is kept as a box, folded into a page, or deleted, and recorded in that PR | S7 |
| Login, OAuth callback, signup redirect | Restyled; behaviour unchanged except `next` (N-1, §3.4) | S2 |

### 3.3 Landing page

- **Where it lives:** a second, **prerendered static entry** in the same Vite project (`frontend/landing/`). React components (`Box`, `Chip`, the chart primitives from S3) are rendered to HTML at build time with a Vite SSR build and `renderToStaticMarkup`. The output is `dist/landing/index.html` with `tokens.css`, the Geist files and **no React runtime** (one inline script under 2 KB for the theme toggle and the mobile menu, allowed by its hash). Sharing components avoids a second, hand-written copy of the box markup drifting from the console.
- **How it is served (Q1 default, changed in rev 2): its own origin.** The console origin keeps JWTs in `localStorage` (N-19 area, `AuthContext.jsx:29-33`). Any script ever added to a marketing page on that origin would run with console privileges. A separate origin also keeps `/` on the console host for the SPA, so operators' bookmarks of `/` keep working and no nginx `location = /` or `?tab=` juggling is needed. Two ways to do it, same build output:
  - a second nginx `server` block in the same image, with its own `server_name` (a host the owner names), its own root and the landing CSP; or
  - the files published on zeroshield.ai.

  If the owner keeps `/` on the console host instead, all of these apply:
  - `location = /` serves the landing page, with the legacy `?tab=` map first;
  - the landing CSP below;
  - a CI check that `landing/index.html` loads no external script, stylesheet, font, image or `connect` target;
  - a visible "Sign in" and "Open the console" link;
  - a release note that `/` changed.
- **Landing CSP:** `default-src 'none'; script-src 'sha256-<inline script>'; style-src 'self'; font-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'`.
- **SEO and meta:**
  - one `<h1>`, `lang="en"`, `<title>` and meta description from approved copy;
  - `rel=canonical` on the owner's chosen domain;
  - Open Graph and Twitter card (a 1200x630 image rendered from the landing hero by the e2e job, committed);
  - `robots.txt` that allows `/` (and on the console host disallows everything);
  - a `sitemap.xml` listing `/`.

  The SPA `index.html` gets `<meta name="robots" content="noindex">`, and nginx adds `X-Robots-Tag: noindex` on the console host.
- **Performance budget (CI-gated on metrics, Lighthouse mobile preset, median of 3 runs, `numberOfRuns: 3`):**
  - LCP ≤ 2.0 s, CLS ≤ 0.02, TBT ≤ 100 ms (gated);
  - HTML + CSS ≤ 35 kB gzip; JS ≤ 2 kB (inline); fonts ≤ 2 woff2 files, ≤ 90 kB together, the UI font preloaded (gated);
  - Lighthouse category scores (Performance, Accessibility, Best Practices, SEO) are reported, not gated, because scores on shared runners are noisy; axe 0 violations is gated;
  - no third-party requests: no Google Fonts, analytics or trackers (owner Q11), enforced by the CSP and a network assertion.
- **Content:** the copy and structure of the Lattice mockup. The console preview is HTML with a visible "Sample data" marker. There are no performance numbers, customer names or certifications until the owner approves claims (DIRECTIONS Q6, owner Q10).

### 3.4 The `next` guard (sign-in return path)

The algorithm, applied to the raw `next` query value (after the single decode `URLSearchParams` does):

1. Reject if it is empty, longer than 2,048 characters, or contains a backslash, an ASCII control character (U+0000–U+001F, U+007F), whitespace, or any `%` escape. A `%` left after one decode means double encoding.
2. Reject unless it starts with exactly one `/` followed by a character that is not `/`. This rejects `//evil`, `https:evil.com` and `javascript:…`.
3. Parse it with `new URL(next, location.origin)`, and reject unless `url.origin === location.origin`.
4. With the Lattice flag on, require `url.pathname === "/console"` or `url.pathname.startsWith("/console/")`, checked **after** URL normalisation, so `/console/..//evil.com` (pathname `//evil.com`) is rejected. With the flag off (before cutover), the only allowed target is `/` whose sole parameter is `tab`, with a value in `KNOWN_TABS`.
5. Navigate to `url.pathname + url.search + url.hash`, rebuilt from the parsed URL and never the raw string.

Anything rejected goes to `/console/overview` (flag on) or `/` (flag off), and the rejection is logged to the console in dev only.

`nav.spec` tests at least: `https://evil`, `//evil`, `/\evil.com`, `\/evil.com`, `/%5Cevil.com`, `%2F%2Fevil.com`, `https:evil.com`, `javascript:alert(1)`, `/console/..//evil.com`, `/console/%2e%2e/x`, a 3,000-character value (all rejected), and `/console/policies/12?rule=4#test` and `/console` (accepted, with query and hash kept).

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
- The same script bans `dangerouslySetInnerHTML` and `.innerHTML` under `src/console/` and `frontend/landing/` (0 exist today).
- The baseline starts at **9,746 / 85 files** (`counts.txt`, now reproducible: `counts-reruns.txt`). It falls as pages migrate (S3–S7) and reaches 0 in S10, where the palette is also removed from Tailwind.
- `npm run lint` is redefined to run lint (palette ratchet, token freshness, contrast, the bans) instead of the unit tests. CI passes it as `lint-command` to the existing `reusable-node-ci.yml`.

### 4.3 Themes (A-09)

- **Decision proposed: dark by default** for the console and the landing page (Lattice is dark first, `DESIGN.md:13` already says dark, and the owner's reference is dark). The user's stored choice (`zeroshield_theme`: `dark` | `light` | `system`) wins. Owner Q3.
- **Rev 2:** the dark default applies **only when the Lattice flag is on**. Prod builds keep `VITE_LATTICE=off` until S9, so prod's legacy pages keep today's light default until cutover, and S1 changes nothing visible in prod. DESIGN.md is updated in S1 to say exactly that. Shipping dark to prod's legacy pages earlier is an owner decision (Q3), not a side effect.
- One mechanism: `ThemeContext` sets `data-theme` on `<html>` (and keeps the `.dark` class while legacy `dark:` variants exist). An inline pre-paint script in `index.html` and the landing page applies it before the bundle loads. Today the only defence against a flash of the wrong theme is `ThemeContext` running at module load, after a 2.2 MB download. The inline script is allowed by its SHA-256 hash in the CSP (§4.6). The build computes the hash, and a test fails if the served CSP and the built script disagree.
- A theme change in one tab applies to other open tabs through the `storage` event.
- Tests:
  - a fresh profile renders dark (flag on);
  - stored `light` renders light with no dark frame (a screenshot taken at `DOMContentLoaded`);
  - `system` follows `prefers-color-scheme`;
  - a flag-off build renders today's light default.

### 4.4 Contrast and accessibility gate

- `scripts/check-contrast.mjs` is ported from the mockups. It checks every text and UI pair in both themes: text ≥ 4.5:1, large text and UI boundaries ≥ 3:1 (WCAG 1.4.3 and 1.4.11). It runs in `npm run lint`, so a token change that breaks a pair fails CI. PR #123 measured 46 + 24 Lattice pairs with 0 failures; the ported check covers the same pairs plus any new token.
- axe-core through `@axe-core/playwright`, rules `wcag2a`, `wcag2aa`, `wcag21aa` and `wcag22aa`, on every route, both themes and all three viewports: **0 violations** is the gate. `best-practice` findings are reported in the PR.
- Keyboard: a skip link, landmarks, a visible `:focus-visible` ring in `--ring`, a focus-trapped search dialog (Ctrl/Cmd K, Esc closes), roving focus in the sidebar, and every action reachable without a pointer. One keyboard-only e2e walk per drawn screen.
- Reflow at 320 CSS px and 200% zoom (WCAG 1.4.10): no horizontal scroll except inside data tables, which scroll on their own. `prefers-reduced-motion` is honoured, and numbers never animate on refresh.

### 4.5 Fonts

- Geist and Geist Mono (SIL OFL 1.1), **self-hosted** from the `geist` npm package's woff2 files with `font-display: swap`. Only the UI font is preloaded. Subsets are latin and latin-ext. Licence text is shipped in `public/fonts/`.
- No Google Fonts requests from the new shell or the landing page (CSP `font-src 'self'` plus a Playwright network assertion). The three legacy imports in `index.css:2-4` are removed in S10, when no legacy page needs them. Until then the console CSP allows `fonts.googleapis.com` and `fonts.gstatic.com` for legacy pages only, and S10 removes them from the CSP.
- Fallback stacks: `'Geist', system-ui, sans-serif` and `'Geist Mono', ui-monospace, monospace`, with `size-adjust` metrics set so swapping in Geist causes no layout shift (CLS budget).

### 4.6 Content Security Policy for the console (rev 2)

- The policy for the SPA (all `/console/*`, `/login`, `/oauth/callback`):
  - `default-src 'self'`, `script-src 'self' 'sha256-<pre-paint>'`, `style-src 'self'`;
  - `img-src 'self' data:`, `font-src 'self'` (plus the two Google Fonts hosts until S10, §4.5);
  - `connect-src 'self'`, plus the gateway's public origin while the legacy simulators call it directly (`utils/liveGateway.js`, `gatewayChatFetch.js`), plus `wss:` to the console host while legacy pages open the WebSocket. Both extras go when S7 and S10 remove their users;
  - `object-src 'none'`, `base-uri 'none'`, `form-action 'self'`, `frame-ancestors 'none'`.
- `style-src` starts without `'unsafe-inline'`. React style props go through the CSSOM, which CSP does not block. If e2e shows a library injecting `<style>` elements, that library is named in the PR, and `'unsafe-inline'` is added for styles only, never for scripts.
- Rollout:
  - **S2:** `vite preview` in `frontend-e2e` serves the policy **enforced**, and every spec fails on any `securitypolicyviolation` event, on every route, legacy-mounted pages included. nginx sends it as `Content-Security-Policy-Report-Only` from S2.
  - **S9:** nginx enforces it.
- Why it matters: the console keeps JWTs in `localStorage` and renders attacker-controlled text (prompts, model output, rule regexes, MCP tool names), so any XSS means token theft. The CSP is the interim mitigation until B20 moves the refresh token to an HttpOnly cookie. React escapes text by default, and the `innerHTML` ban (§4.2) keeps it that way.

## 5. Components

### 5.1 Box (the primitive every Lattice screen is built from)

```
<Box span={3} title="Traffic" purpose="Requests through the gateway, per hour"
     source={useSource("soc-kpis", { period })}   // §6.2
     tag={<Chip tone="neutral" source="…">…</Chip>}>
  …body…
</Box>
```

- **Fixed anatomy, in order:**
  1. a header with the title (h2, one line, ellipsis) and an optional tag area for chips and IDs;
  2. a one-line purpose;
  3. the body;
  4. the footer.

  The footer shows, in order, the freshness word (§6.2), the age, and the source label. The label is a button whose popover holds:
  - the endpoint and parameters;
  - the window the response says it used;
  - the fetch time and the data-as-of time;
  - the HTTP status;
  - the `X-Request-ID` **sent and returned**. They differ when nginx answered, because `edge-error.inc:22` sets nginx's own `$request_id`.

  A non-JSON error body (nginx's HTML 5xx page) is never rendered: the popover shows the status only.
- `title`, `purpose` and `source` are required. A box without them throws in dev, and the e2e invariant (§7.4) fails.
- **Chip rule (rev 2):** a chip or status word is allowed only if it is either:
  - (a) a state field the server returns (for example `enforcement_mode`, `is_active`); or
  - (b) a comparison of a number shown in the same box with a threshold stated in the same box (for example p99 against the 20 ms budget).

  Every chip carries `data-chip-source`, naming the field or the comparison, and its popover says which. Chips that meet neither rule are not drawn.
- Corner registration ticks (the Lattice signature) are pure CSS, `aria-hidden`, and turn off under `forced-colors`.
- **On mobile the source stays visible.** The mockup hides `.src` below 760 px (`style.css`, `.s3 .bf .src { display: none }`); that contradicts "every box says where its number came from". The footer wraps instead: freshness and age on line 1, source on line 2.

### 5.2 Box states (every box, every source)

| State | When | Shows |
|---|---|---|
| Loading | No data yet, first fetch in flight | A skeleton of the box's own body shape (no spinner). The footer reads "Loading · \<source\>" |
| Fresh | The source reports how old its data is (B1/B12), that age is within the source threshold, and the last fetch is within `freshFor`. Or the source is a direct DB read (config and lists) and the last fetch is within `freshFor` | Body; footer "Fresh · 40 s ago" |
| Updated (rev 2) | An analytics source that does not report data age: the last fetch is within `freshFor`, but the data could lag behind the drain (N-18) | Body; footer "Updated · 12 s ago"; the popover says "Data age not reported; counts are by ingest time" |
| Stale | There is data, but refreshes are failing or paused past `freshFor`, **or** the source reports data older than its threshold, **or** the page has just resumed from sleep or a hidden tab and the refetch has not completed | Last good body, unchanged; footer "Stale · 4 min ago" in the monitor colour (word plus glyph, not colour alone). The popover says which condition applies (refresh failing with status and request id, resumed, or newest event 26 min old) |
| Error | No usable data, and the last fetch failed | "Could not load: \<reason\>". The reason comes from the API's `code` or `error` field (`analytics_busy`, `query_timeout`, `db_unavailable`, `gateway_unreachable`, `invalid_period`) or the HTTP status. Retry button, both request ids |
| Empty | The fetch succeeded with zero rows or zero counts | A purpose-specific sentence, for example "No requests in the last 24 hours" or "No keys yet: create one", with the window named |
| Unavailable | The endpoint exists, but this deployment cannot produce the field (for example plan version per replica on v1), or a capability flag says so | "Not available on this gateway: \<why\>". Never a zero. **Not** used for firewall overhead on v1 (N-10) |
| Forbidden | 403 for the whole box. A 403 on one source of a multi-source box hides that field only, and the popover says "needs the admin role" (for example RAG collections, `gateway_admin_proxy_views.py:231`) | "Your role cannot see this" |

### 5.3 Chips, IDs and verdicts

- Outlined, square glyph, always a word (never colour alone). Tones: `allow`, `redact`, `monitor`, `block`, `neutral`, `accent`. Sizes: default (20 px) and large (26 px).
- One verdict vocabulary everywhere: Allowed, Redacted, Watched (`monitor` and `flag`), Blocked. "Watched" is shown only where a request-level monitored count exists (B7); until then the Verdicts box shows "Allowed or watched", because that is what `requests_allowed` counts.
- Status vocabulary, and the field that drives each word:
  - Enforcing, Monitoring, Audit only: `firewall/config.enforcement_mode` = `block` / `monitor` / `audit`, from `core/models.py:870-874`;
  - Paused: `firewall_enabled` or the checkpoint's own `*_enabled` flag is false;
  - Isolated: an active kill switch;
  - Serving and Degraded: waiting for B4 and B6.
- Module IDs (`1.1`…`1.7`) are mono ID tags.

### 5.4 Charts (SVG primitives; no chart library on the overview)

`Sparkline`, `CellStrip`, `MixBar`, `Histogram`, `BarRows`. These are small, token-coloured SVG/CSS components, as in `shell.js` of the mockups.

- Each has `role="img"` and an `aria-label` carrying the actual values, plus a "Show values" disclosure that renders the numbers as a table (no tabs).
- `CellStrip` renders **the API's buckets** (25 for 24 h, N-12) and marks the last, partial bucket. Each cell is labelled with its start time in the viewer's local time. Bucket boundaries follow UTC hours, so in IST (+05:30) the labels read hh:30. They are not shifted, because shifting would misstate the data.
  - For 7 d (4-hour cells) and 30 d (24-hour cells ending at the current hour) the labels are start and end times, **never calendar days** (A-N5).
  - The strip's popover says "by ingest time" (N-18).
  - A strip covers its window plus a partial bucket, while the KPI next to it covers exactly the window. So no strip shows a total, and no strip is described as summing to the KPI.
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
- Forms: labels above fields, errors inline and in an `aria-live` summary, server validation messages mapped to fields. Submit buttons are disabled while in flight, so a double click sends one request. Leaving with unsaved edits asks for confirmation (router blocker plus `beforeunload`). A form whose saves go live immediately says so next to its submit button ("Saves live: the gateway plan is republished").

## 6. Data

### 6.1 Per-box source map: overview (16 boxes as drawn)

Status key: **exists** = the endpoint returns what the box shows; **partial** = some fields or chips have no source; **missing** = no endpoint. "Mockup source" is the source line drawn in PR #123; "Proposed source" is what the code supports today. Cadence = refresh interval while the tab is visible (rev 2: analytics at 30 s, §6.3). "Chip source" applies the chip rule (§5.1).

| # | Box | Mockup source | Proposed source and fields | Chip or tag: source | Cadence / `freshFor` | Status | Gap |
|---|---|---|---|---|---|---|---|
| 1 | Traffic | `soc-kpis` | `soc-kpis?period=` → `requests_inspected`. The hourly spark has no request-level series: `module-trends` counts **events** for 1.1, so the spark would not sum to the KPI and is not drawn | "+6%" delta: **none** (not drawn) | 30 s / 60 s | partial | B7 (hourly request series, previous-window total) |
| 2 | Verdicts | `policies/analytics` | **`soc-kpis`**: `requests_allowed` (shown as "Allowed or watched"), `requests_redacted`, `requests_blocked`. `policies/analytics` counts **events** over `days`, has no allowed count, and its smallest window is a day | verdict words are the field labels | 30 s / 60 s | partial | B7 (`requests_monitored`) |
| 3 | Firewall overhead | `stage_metrics_ms` | No control endpoint. v1 **does** measure it (N-10): per-org histograms and `pipeline_trace.t_addon_pre_ms`/`t_addon_post_ms` on every traced event. `stage_metrics_ms` is a per-event field, not an endpoint | "Within budget": rule (b), p99 against the 20 ms budget, once B3 returns p99 | 30 s / 60 s | **missing** (endpoint only) | B3 |
| 4 | Gateway fleet | `health` | `gateways/stats`: HEALTHY/WARNING per gateway agent on 5-minute staleness, so "N of M reporting" has a source. But agents are never pruned (M counts dead agents), the call counts every event per agent over all time (N-23), and plan version per replica and config-sync age have no source. **Held back by default** (Q5) | "Serving": none until B4 | not polled | partial (held) | B4 |
| 5 | 1.1 Gateway and keys | `gateways/stats` | **`gateways/keys`**: active and revoked counts and "expires within 7 days" from `expires_at`, **counted in the browser over every page** (`page_size=500`), with simulator keys (`project_id` starting `simulator-`, `vector_models.py:68-70`) counted separately and named (N-21). `module-trends` 1.1 for the strip. Auth failures (N-8) and idle (N-7) have no source | "Enforcing" etc.: `enforcement_mode` + `firewall_enabled` | 60 s / 120 s (keys), 30 s (strip) | partial | B5 (incl. a counts endpoint) |
| 6 | 1.2 Policies | `policies/analytics` | `module-kpis` 1.2 (blocked, redacted); `/api/policies/?policy_domain=` ×3 plus `/api/vector-policies/` (enabled count; N-14); `policies/compile/status` (`version`, `compiled_at`); `module-trends` 1.2 strip | `enforcement_mode`; "plan vN": `compile/status.version` | 30 s / 60 s (policies 60 s) | exists | B15 (one call instead of 4) |
| 7 | 1.3 RAG and vectors | `rag-pipeline-kpis` | `rag-pipeline-kpis` (`document_funnel`, `stages`); `vector-providers` for stores; chunk totals from `admin/gateway/rag/collections`, which is **admin only** (`gateway_admin_proxy_views.py:231`) and a synchronous control-to-gateway call (`:84`, 10 s timeout), so it is polled every **5 minutes** and hidden for other roles; `module-trends` 1.3 strip. "Chunks withheld" has no source | `enforcement_mode` + `rag_enabled` | 60 s / 120 s (collections 300 s / 600 s) | partial | B14 (withheld) |
| 8 | 1.4 MCP tools | `mcp-connector/health` | **`mcp-connector/servers`** (`connection_status` at last sync, shown as "N of M connected at last sync") plus `mcp-connector/events/summary?hours=` (`decisions`); `module-trends` 1.4 strip. That is not live reachability (N-9) | "Degraded": none until B6 (not drawn); `enforcement_mode` | 30 s / 60 s | partial | B6 |
| 9 | 1.5 Model routing | `firewall/models` | `firewall/models` (count); `module-kpis` 1.5 (total); "reroute switches" = active `kill-switches` with `action=reroute` (`core/models.py:771,799`). The drawn label "fallback rule" becomes "reroute switches", so the word matches the source. The reroute **count** has no source | `enforcement_mode` + `routing_enabled` | 60 s / 120 s | partial | B14 (reroutes) |
| 10 | 1.6 Kill switch | `kill-switches` | `kill-switches` (`is_active`, `model_name`, `activated_at`); `models/audit-log` for the isolated-since strip | "N isolated": count of `is_active` (rule a) | **10 s / 20 s** (safety state) | exists | – |
| 11 | 1.7 Output guardrails | `module-kpis` | `module-kpis` 1.7 (`total`, `blocked`, `redacted`, `flagged`, `critical`; probe P7); `module-trends` 1.7 strip. The drawn "0 withheld" has no source | `enforcement_mode` + the `output_*_enabled` flags | 30 s / 60 s | **partial** (rev 2) | B14 (1.7 withheld) |
| 12 | Detectors | `firewall/config` | `firewall/config` (`tier2_enabled`, `tier2_strict`, `tier2_fail_closed_enabled`); Tier-1 preset and custom-regex counts from the policy lists. Tier-2 p95 has no source | "All up": none until B11 (not drawn) | 60 s / 120 s | partial | B11 |
| 13 | Rules that fired most | `policy/top-rules` | `policy/top-rules?days=1&limit=5`. The smallest window is a day, so `1h` and `6h` cannot be honoured; the footer says "last 1 day" | – | 60 s / 120 s | exists (window caveat) | B2 |
| 14 | OWASP LLM Top 10 | `owasp-stats` | `owasp-stats?hours=` (maximum 720; negative values are accepted, N-5) | – | 60 s / 120 s | exists | B2 |
| 15 | Models | `firewall/models` | `firewall/models` (list) plus `dashboard/model-usage?days=1` (share) plus `kill-switches` (Isolated chip) | "Isolated": active kill switch (rule a) | 60 s / 120 s | exists (three sources; window is days) | B2 |
| 16 | Enforcement log | `threat-feed` | `soc-kpis.total_threats` (records written in the window). "Last written", "dropped" and drain lag have no source. Rev 1's `threat-feed?limit=1` is dropped: its default window is 48 h (probe P5: newest event 60 h old → 0 rows), and widening it to 8,760 h makes every tick aggregate a year of events (`security_views.py:566-611` collapsed, `:617-624` uncollapsed). The drawn "Plan versions v13, v14" is v2-only and is not drawn | "Writing": none until B12 (not drawn) | 30 s / 60 s | partial | B12 |

Totals (rev 2): **exists 5** (6, 10, 13, 14, 15), **partial 10** (1, 2, 4, 5, 7, 8, 9, 11, 12, 16), **missing 1** (3). The mockup's source line is wrong for boxes 2, 4, 5 and 8, and box 3 names a field (`stage_metrics_ms`), not an endpoint.

**What ships when (owner Q5 default):**
- A box whose only source is missing (3) is not rendered until its backend issue lands.
- Box 4 is held back too, by default, although `gateways/stats` could source "N of M reporting".
- A partial box ships with only its sourced fields and chips. A field without a source is left out, not shown as zero.
- When a backend issue lands, the field or box arrives in a small follow-up PR with its agreement test.
- The header line counts the boxes actually shown, for example "14 boxes · 9 fresh, 5 updated".

### 6.1b Per-box source maps: request detail (S4), keys (S5), policy editor (S6) (rev 2, A-S3)

Same status key. All request-detail boxes read one call, `GET /api/security/threat-feed/<eventId>/`. It merges the sibling events of the same `request_id` (`security_views.py:1053-1058`; `related_event_ids` in the response). Their freshness is "Record · \<event time\>", and they do not poll.

**Request detail (`request.html`, 11 boxes)**

| Box | Mockup source | Proposed source | Status | Gap or decision |
|---|---|---|---|---|
| Outcome | `threat-feed/<id>` | Per **event**: `action`, `metadata.input_action`, `metadata.output_action` (`core/tasks.py:241-242`). The detail returns the action of the event that was opened, not the request's outcome (probe P8: the two events of one request return `redact` and `block`). So the box lists the action of each of the request's events (from `related_event_ids`), and states a request-level outcome only once B9 returns one (counted the way `soc-kpis` counts requests). "HTTP status" from `metadata.status_code` is shown only after S4's agreement test proves it equals what the client received for each event type | partial | B9 for the request-level outcome; verify the status in S4, otherwise leave it out |
| Latency | `stage_metrics_ms` | `metadata.latency_ms` (total), `pipeline_trace.t_addon_pre_ms + t_addon_post_ms` (firewall), the upstream stage (model). "First token" only for streamed requests (`pipeline_trace.ttft_ms`, `stream_orchestration.py:804-805`). A trace that control rebuilt from `stage_metrics_ms` (`security_views.py:886-899`) is marked "reconstructed" | exists for traced events | – |
| Tokens and cost | "OpenAI usage object" | `metadata.input_tokens`, `output_tokens`, `total_tokens` (`core/tasks.py:251-253`). **Cost has no source**: control stores none, and the gateway strips `cost` from `usage` (`main.py:12037-12038`) | partial | cost left out (Q14) |
| Risk | `risk_score` | `metadata.risk_score`/`security_risk_score`, `owasp_code(s)`, `compliance_tags`, `incident_status` | exists | – |
| Pipeline, stage by stage | `pipeline_trace.stages` | `metadata.pipeline_trace.stages` (v1 chat path, `main.py:11946-11949`). Events without a trace show Unavailable ("no trace recorded for this event type") | exists for traced events | – |
| Routing decision | `zeroshield.routing` | `pipeline_trace` routing fields (`requested_model`, `routed_model`, `fallback_chain`, decision source; `pipeline_trace.py:502-504,1095-1250`). The "Rerouted" chip uses rule (b): requested ≠ routed, both shown | exists for traced events | – |
| Prompt and response | `pipeline_trace.input_text_before/after` | `metadata.extra.input_text`, `prompt_submitted`, `output_text`, `sanitized_output` (`main.py:11953-11980`). **Underlining personal data has no source**: no span offsets are stored | partial | shown without underlines (Q14) |
| Rules that decided | `zeroshield.matched_rules` | `metadata.matched_policy_codes`, `matched_rule_names`, `matched_rule_ids` (`core/tasks.py:256-265`). The "no match" rows (rules that did not match) and "plan v14" have no source on v1 | partial | matched rules only; plan version Unavailable on v1 |
| Request | `enforcement_event` | `request_id`, `id`, `timestamp`, `metadata.method`, `endpoint`, `source_ip`. **User agent is never set by v1** (`_emit_telemetry` sets org, source IP, method and status only, `main.py:6157-6160`), plus the list of the request's events (`related_event_ids`) | partial | user agent left out (Q14) |
| Security checks | `enforcement_event.metadata` | **No honest source.** `pii_detected`, `prompt_injection_detected`, `auth_status`, `rate_limit_status`, `input_validation` and `content_safety` are **derived from `threat_type`** at drain time (`core/tasks.py:268-275`). They are not checks that ran, so they must not be shown as checks. The stage box already shows what ran | missing | **dropped** (Q14; optional B22) |
| Context | `metadata.extra` | org, `project_id`, `key_prefix`, agent, `event_type`; the key name by looking up the prefix in `gateways/keys` (two sourced facts joined). "Plan version" is Unavailable on v1 | partial | – |

URL and siblings (A-N8; probe P8):
- `/console/requests/:eventId` accepts any event id of the request. Both sibling ids return the same `request_id` and point at each other, but each returns its own `action`, which is why the Outcome box lists every event's action.
- "Copy link" copies the current URL.
- The requests list links to the collapsed representative event that `threat-feed` returns.
- Once B9 lands, `/console/requests/by-id/:requestId` resolves a gateway request id directly.
- An unknown or foreign id returns the 404 page (tenant canary in S4).

**Keys (`keys.html`, 8 boxes; S5)**

| Box | Mockup source | Proposed source | Status | Gap or decision |
|---|---|---|---|---|
| Active keys | `gateways/keys` | Active, revoked and total counted in the browser over every page (N-21). "Used today" and "idle" need `last_used_at` (N-7). The "Enforcing" chip has no source for keys and is not drawn | partial | B5 |
| Requests by key | `gateways/stats` | **None.** `gateways/stats` is per gateway agent, not per key | missing | B5 (per-key requests) |
| Auth failures | `enforcement_event.auth_status` | **None** (N-8) | missing | B5 |
| Expiring soon | `gateways/keys` | Replaced by "**Key lifetimes**": counts of keys by expiry bucket (none, < 7 d, < 30 d, later) from `expires_at`, computed against the server-clock estimate (§6.2), with no key list and no action (Q6 default) | exists | Q6 |
| Keys (table) | `gateways/keys` | Name, prefix, project, allowed models, rate limit, risk, expires and status exist. "Requests 24 h" and "Last used" need B5. The order is `created_at` descending (`gateway_key_views.py:333-334`), not "newest traffic first" | partial | B5 |
| Call the gateway | `gateways/public-url` | `gateways/public-url` (`api_endpoint`). The snippet names an environment variable, never a key | exists | – |
| Rate limit headroom | `enforcement_event.rate_limit_status` | **None**: `rate_limit_status` is derived from `threat_type` (N-8). If B5 ships current TPM, this becomes a **distribution** (keys by % of limit), not a ranked key list (A-S5) | missing | B5 |
| Key hygiene | `gateways/keys` | Replaced by "**Key scope**": counts of keys by allowed-model breadth (one model, several, every model), with no key list and no action (Q6 default) | exists | Q6 |

The page-header action "Delete revoked (N)" stays: it is the existing `purge-revoked` action (`gateway_key_views.py:428`), with a confirmation dialog. It is a control, not a list (Q6 names it so the owner can veto it). Edits are last-write-wins (N-21), and the edit form says so.

**Policy editor (`policy.html`, 9 boxes; S6a and S6b)**

| Box | Mockup source | Proposed source | Status | Slice |
|---|---|---|---|---|
| Policies (grouped list) | `policies` | `/api/policies/?policy_domain=` ×3 plus `/api/vector-policies/` (N-14) | exists (4 calls) | S6a (B15 later) |
| Policy (fields) | `policies/12` | `GET/PATCH /api/policies/<id>/` with `version`, which gives a 409 on a policy-field conflict (`views.py:597-636`). The "Enforcing" chip comes from `enabled` and `enforcement_mode`. System and MCP-seeded policies are read-only for non-platform-admins (N-22). Vector policies have their own route, have no version, and save live (last-write-wins, stated) | exists | S6a |
| Draft against live | `policies/compile/status` | Needs B13: a draft state, a dry-run compile and an atomic publish. "Live vN published at" comes from `policies/<id>/versions` (`PolicyVersion`). "Edited by" needs a server draft (B13 optional part) | missing | S6b |
| Rules, in the order they run | `policies/12/rules` | `GET /api/policies/<id>/rules/` (`views.py:694-706`). S6a: create, edit and delete save **live**, one rule at a time, each republishing the plan (N-11), labelled so; **no reorder** (it would be N publishes). S6b: reorder and batch edits through B13. The "24 h" hits column needs B8 | partial | S6a, S6b |
| Test against the draft | `policies/test` | S6a: "**Test against live**" with `policies/test` and `policy_id` (saved, enabled rules only, N-16). S6b: test against the draft needs B13 | partial | S6a, S6b |
| Where it applies | `policies/12` | `allowed_user_ids`, `allowed_agent_ids`, `allowed_roles` (empty = everyone; `policy/models.py` G8 block). The "Models" line has no per-policy source and is not drawn | partial | S6a |
| Hits by rule | `policies/analytics` | **None.** `policies/analytics` has no per-rule counts | missing | waits for B8 |
| Rollout | `policies/compile/status` | `compiled_at`, `version` and `policy_count` exist; replicas and versions per replica need B4. "In sync" has no source (not drawn) | partial | S6a (compile facts), B4 later |
| Mapped to | `policy.compliance_tags` | **None** per policy (only the `ComplianceTag` catalogue exists) | missing | waits for B10, or dropped |

### 6.2 Freshness model (what "Fresh", "Updated" and "Stale" mean, per source)

- Every box reads through `useSource(id, params)`: one hook over TanStack Query v5 (one new runtime dependency, about 13 kB gzip; the alternative is an in-house hook with the same contract). It returns `{data, status, fetchedAt, dataAsOf, window, requestIdSent, requestIdReturned, httpStatus, errorCode}`. Query keys include the **user id and org id** (§6.3).
- **Fetch age** is measured with the monotonic clock: `performance.now()` at receipt, against `performance.now()` at render. It is immune to wall-clock steps.
- **Sleep and resume.** Browsers differ on whether `performance.now()` advances while the machine sleeps, so the monotonic clock alone can under-report age after a laptop wakes. The scheduler therefore treats each of these as a resume:
  - `visibilitychange` to visible;
  - `pageshow` with `persisted`;
  - `online`;
  - a 1 s ticker that sees `Date.now()` jump more than 5 s past its expected value.

  On resume, every source is marked **Stale ("resumed; refreshing")** and refetched in priority order.
- **Server timestamps** (event times, kill switch "since", key expiry and last use, MCP last sync, `as_of`) are converted with a skew estimate: the median over the last 5 responses of the `Date` header minus the client receipt time. `Date` has 1 s resolution, which is enough for ages shown in seconds. Every relative age and every "within 7 days" bucket uses `client now + skew`, never the raw client clock.
- Ages re-render on a 1 s ticker. Only the age text changes; it uses tabular numerals at a fixed width, so there is no layout shift.
- **Fresh** (config and list sources: keys, policies, models, kill switches, firewall config, compile status): the last fetch succeeded and fetch age ≤ `freshFor` (2 × cadence). A direct DB read is the truth at fetch time.
- **Updated** (analytics sources until B1 and B12): the same fetch condition, but the server does not say how old the data is.
- **Fresh** (analytics sources after B1 and B12): the fetch condition holds and drain lag ≤ 60 s.
- **Stale** otherwise, while there is data. The source thresholds:

  | Source group | Stale when |
  |---|---|
  | Analytics (soc/module KPIs, trends, OWASP, top rules, MCP summary, RAG KPIs) | fetch age > `freshFor`; and once B12 lands, drain lag > 60 s |
  | Config and lists (keys, policies, models, kill switches, firewall config) | fetch age > `freshFor` |
  | Fleet (B4) | any replica's heartbeat older than 30 s (today's `gateways/stats` uses 5 minutes for WARNING; B4 makes it one number) |
  | Overhead (B3) | the response's `as_of` is more than 60 s old |

- **Record** (request detail): immutable records show "Record · \<event time\>" and do not poll.
- **Hidden tabs** pause polling (as today). On becoming visible, every source past `freshFor` refetches at once (a resume, above).
- **Inactivity logout** (B-N5). The 15-minute inactivity logout (`DashboardLayout.jsx:17-20`) is kept: polling is not user activity, so a wall display left on the overview signs out after 15 minutes. That is accepted and stated in the S3 PR. Changing it is a separate owner decision.

### 6.3 Request scheduling, caching, load and invalidation

- **Deduplication:** `soc-kpis` feeds 3 boxes (1, 2, 16), `module-kpis` 3 (6, 9, 11), `module-trends` 6 strips (5–9, 11), `kill-switches` 3 (9, 10, 15). One query key per (user, org, endpoint, params) means one request per refresh.
- **Requests per visible tab (rev 2, computed from §6.1):** about **30 GETs per minute**. **7** of them are semaphore-capped analytics calls (`soc-kpis`, `module-kpis` and `module-trends` at 30 s, `model-usage` at 60 s), and **12** aggregate the events table (those 7 plus OWASP, top rules, MCP summary ×2 and RAG KPIs). Rev 1 had 42 and 13. Today's overview makes about 66 per minute (11 GETs every 10 s) plus the shell's health and notification calls.
- **Load model (B-S1):**
  - Event-table aggregates arrive at about 0.2 per second per open console: 4/s at 20 consoles, 40/s at 200, 400/s at 2,000.
  - Without a cache, cost grows with consoles, not orgs. Ten operators of one org cost ten times the queries.
  - Capacity per control worker is 2 concurrent capped analytics calls (N-4), so capped throughput per worker is about 2 / (seconds per query).
  - `1h` and `6h` always read raw rows (`ROLLUP_PERIODS`), so they are the worst case.
  - The per-query cost on a realistic table is **measured in the S3 load gate** below, not guessed here. The projections for 200 and 2,000 consoles are computed from it and recorded in the S3 PR.
  - **B18** (a per-org, per-endpoint, per-params response cache with TTL ≤ half the cadence) makes cost grow with active orgs instead of consoles. It is an **S9 entry criterion** unless the S3 measurements show headroom for 200 consoles at the production worker count.
- **S3 load gate (pass criteria; run on a GitHub runner by `workflow_dispatch`; results in the S3 PR):**
  - **Data:** an events table of ≥ 10M rows over 30 days in 100 orgs, generated in SQL; rollups both **on** and **off**; ranges `1h`, `24h` and `7d`.
  - **Load:** 20 concurrent console sessions in one org, then 20 spread over 20 orgs, against control under gunicorn with 2 `UvicornWorker`s and Postgres on the same runner.
  - **Pass:** p95 per source ≤ 1 s; the 503 rate after client retries ≤ 1 %; control CPU ≤ 70 % on average; no box in Error at the end.
  - **Recorded:** the per-source DB time and the runner type, plus whatever else ran.
- **Phase offsets:** a source's first fetch is immediate (in priority order, under the caps below). Its second fetch comes after a random delay in [0.5, 1.0] × cadence, and later ones at the fixed cadence. Tabs and consoles therefore do not tick together.
- **Concurrency:** at most 4 requests in flight per tab, and **at most 2 analytics requests**. They queue in fixed order: above the fold first, then row order.
- **Before each request:** if the access token's `exp` claim (read, not verified) is less than 30 s away, the refresh mutex runs first. A 503 from the semaphore therefore never hides an expired token (N-17).
- **Retry (full jitter: delay = random(0, min(2 s, 250 ms × 2^attempt)), at most 2 retries per tick):**

  | Response | Behaviour |
  |---|---|
  | 503 `analytics_busy` (no `Retry-After` today) | retry with full jitter |
  | 503 `db_unavailable` with `Retry-After: 2` | wait `max(Retry-After, jitter)`, then retry |
  | 502 `gateway_unreachable` (box 7 collections) | no retry in this tick |
  | 504 `query_timeout` | no retry in this tick |
  | nginx HTML 5xx, network error, offline | no retry in this tick; the body is never rendered |
  | 401 | the refresh mutex once; on failure, go to `/login?next=` |
  | 403 | Forbidden state; no retry until navigation |
  | 400, 404 from a box | no retry; a 400 is a bug, and the e2e tests fail on any 400 or 5xx from a box |

- **Back-off under failure:** after 2 consecutive failed ticks a source's cadence doubles, and after 4 it is ×4, until a success resets it. So a struggling control sees less traffic, not more.
- **Invalidation after mutations**, broadcast to the user's other tabs over `BroadcastChannel("lattice")`. This is **required** for safety state (B-S5a):

  | Mutation | Invalidates (this tab and other tabs) |
  |---|---|
  | Create, revoke or delete a key | keys sources, box 5 |
  | Publish a policy, any live rule write (S6a), vector policy save | policies, compile status, box 6 |
  | Activate or deactivate a kill switch | kill switches, boxes 9, 10 and 15 |

- **Users and orgs (B-S5b):** query keys include user id and org id. `queryClient.clear()` and a `BroadcastChannel` "signed-out" message run on sign-out, on sign-in and on an org switch, so user B never sees user A's cached boxes, even in the same tab without a reload.
- **Realtime:** **none in the Lattice shell** (rev 2). The brief has no live UI, and every box states its age. A new blocked request appears within the next tick (≤ 30 s). This also removes the shell's `?token=` WebSocket URL (`useRealtimeNotifications.js:51-55`), which ends up in proxy access logs.

### 6.4 Gaps: backend issues (the coordinator opens them; frontend slices depend on them)

Rev 2 corrects B2, B3, B4, B5, B9, B12, B13 and B14, and adds B16 to B22. #160 must be updated to match (its B3 note, "v1 measures only end-to-end latency", is wrong: N-10).

| ID | Issue | Needed by | Notes |
|---|---|---|---|
| B1 | Every analytics response carries `as_of` (server time the numbers are true to) and `window` (`{since, until, param}` as **used**, after any clamping) | "Fresh" for every analytics box; footers | A header (`X-Data-As-Of`) works too |
| B2 | One window parameter (`period=1h\|6h\|24h\|7d\|30d`) on `policies/analytics`, `policy/top-rules`, `owasp-stats`, `threat-feed`, `dashboard/model-usage`, `mcp-connector/events/summary`. Every 400 carries a machine-readable `code`. **No silent fallback**: invalid or out-of-range values give 400 `invalid_period`, never a default (`model-usage days=abc` → 30 d today) or an empty future window (`owasp-stats hours=-5` today) | Boxes 2, 13–15; the requests list | N-5; probe P6 |
| B3 | Firewall overhead per org for the window: p50, p95, p99 and a histogram of `t_addon_pre_ms + t_addon_post_ms`, plus the budget value and `as_of`. **v1 measures it today** (event `pipeline_trace` fields, or the C-2 rollup extended with an overhead histogram); v2's `amf_addon_latency_ms` later. Not blocked on F1/F3 | Box 3 | N-10 |
| B4 | `GET /api/gateways/instances/`: replicas, serving state, plan or state version per replica, last heartbeat, last config sync; dead agents pruned or marked; no all-time event counts | Box 4, policy "Rollout" | N-6, N-23 |
| B5 | Key usage: write `last_used_at` back to Postgres (debounced batch); per-key requests in the window; auth failures by reason (unknown, expired, revoked) and by key prefix; current TPM against the limit; **a counts endpoint** (active, revoked, expiry buckets, simulator keys separated) | Box 5; keys page | N-7, N-8, N-21 |
| B6 | MCP server reachability: write `last_health_at` and `last_health_status` from a periodic probe, or remove the fields | Box 8 | N-9 |
| B7 | `soc-kpis`: `requests_monitored`, an hourly request-level series, and the previous-window total | Boxes 1–2 | |
| B8 | `policy/top-rules?policy_id=`: per-rule hits for one policy | Policy "Hits by rule", rules table "24 h" | |
| B9 | Request lookup and search: `GET /api/security/requests/<request_id>/` returning a **request-level outcome** counted as `soc-kpis` counts it (probe P8); text/ID (`q`) and `incident_status` filters; counts by `incident_status` | Search (A-04); requests list `q`, `status`; the Escalations box (Q12) | `security_views.py:479-524`; **S9 entry criterion** (N-19) |
| B10 | Policy → compliance tag mapping (the catalogue `ComplianceTag` exists; there is no mapping per policy) | Policy "Mapped to" | Otherwise the box is dropped |
| B11 | Detectors summary: Tier-1 preset and custom counts, Tier-2 p95, fail-closed state, Tier-2 availability | Box 12 and its chip | |
| B12 | Enforcement pipeline health: drain backlog, drain lag in seconds, dropped events, last drain time, newest event time | Box 16; "Fresh" for every analytics box | N-18 |
| B13 | **Required (was optional).** Atomic policy publish: `POST /api/policies/<id>/publish/` with `{base_version, policy fields, ordered rules}` in **one transaction**, with a version check (409) and **one** plan publish. Every rule write bumps `Policy.version`, so a stale policy PATCH after a rule write gets 409. A **dry-run** test and compile of an unsaved draft body. Optional: server-side drafts with author and time | S6b | N-11, N-16; probes P1, P2 |
| B14 | Checkpoint counters defined server-side: 1.3 chunks withheld, 1.5 reroutes, **1.7 withheld** | Boxes 7, 9, 11 | |
| B15 | Policies across domains: `GET /api/policies/?policy_domain=all` including vector policies (or a counts endpoint: enabled policies and rules per domain) | Box 6; the grouped policy list | N-14; until then, 4 calls |
| B16 | **New.** One structured access log line per API request with `request_id`, method, path, status, `duration_ms`, org and `X-Analytics-Source`, written while the id is still bound; `django.request` 4xx/5xx lines carry the id too | Agreement check A2; **S3 prerequisite** | N-13; probe P3 |
| B17 | **New.** A request-level list (one row per request) with keyset paging (`before=<created_at,id>`) and a fixed `until`, not bounded by the 4,000-event scan, without full-window aggregates on every page | S4 requests list | N-15; probe P11 |
| B18 | **New.** A per-(org, endpoint, params) analytics response cache, TTL ≤ half the cadence, shared across workers, keyed by org (no cross-org reuse), size-bounded | Load at 200+ consoles; **S9 entry criterion** unless S3 shows headroom | §6.3 |
| B19 | **New.** Take the analytics semaphore after authentication (an anonymous call gets 401 and cannot hold a permit), and set `Retry-After` on `analytics_busy` | §6.3 | N-17; probe P4 |
| B20 | **New.** Refresh token in an HttpOnly, Secure, SameSite=Strict cookie; access token in memory only | Security (§4.6) | `AuthContext.jsx:29-33` |
| B21 | **New.** Stop writing, or expire, `Notification` rows once the bell UI is retired (S10); nothing will read them | S10 | `security_views.py:2329-2337`, `core/isolation_notify.py` |
| B22 | **New, optional.** Per-check results on each event, including clean checks (not derived from `threat_type`) | Request detail "Security checks" (only if the owner wants it, Q14) | `core/tasks.py:268-275` |

### 6.5 API probes (measured on throwaway test databases)

**Rev 1 probe.** `apiprobe/test_lattice_probe.py` runs in the control test runner. It seeds 12 events in 11 requests (6 allow, 2 redact, 1 monitor, 1 block, and one request with redact then block, which must count as one blocked request), plus 1 event outside the window. It calls every endpoint above as a box would, and records status, keys, `X-Analytics-Source` and the numbers. Output: [`apiprobe/probe-results.json`](evidence/2026-11-12-f6-lattice-build/apiprobe/probe-results.json); summary: [`apiprobe/SUMMARY.txt`](evidence/2026-11-12-f6-lattice-build/apiprobe/SUMMARY.txt). Run: `Ran 1 test in 2.336s`, OK (command and environment in `run.txt`). Analytics were served by the raw path, so the rollup path was not exercised. The timings come from a tiny database and say nothing about production.

| What was checked | Measured | Plan consequence |
|---|---|---|
| Request-level counts | `soc-kpis?period=24h`: `requests_inspected` 11, `requests_blocked` 2 (the redact-then-block request counted once), `requests_redacted` 2: **all equal the seed**; the 30-hour-old event is excluded | `soc-kpis` is the source for Traffic and Verdicts |
| Watched | `requests_allowed` is 7 = 6 allow + 1 monitor; there is no request-level monitored field | "Allowed or watched"; B7 |
| Event-level numbers | `policies/analytics?days=1`: `total_violations` 12 (it counts **all** events, including the 6 allow events), `total_redacted` **3** against the request-level truth of 2 | Must not source the Verdicts box |
| Bucket count | `module-trends?period=24h`: 25 buckets per module; `attack-vector-trends`: 25 | N-12 confirmed |
| `/api/health/` | `{"status": "ok"}` | N-6, B4 |
| `/api/health/services/` | 641 ms, unauthenticated (`AllowAny`), probes Celery and RabbitMQ | Not for box polling |
| Keys | `last_used_at` null on the new key; the create response carries the plaintext once with a `warning` | N-7 (not proof on its own: there was no traffic); S5 "shown once" |
| `/api/policies/` | **400 `{"policy_domain": …}`**: the list requires a domain | N-14, B15 |
| Malformed parameters | `soc-kpis?period=abc` → 400 with `code: invalid_period`; `owasp-stats?hours=abc`, `policies/analytics?days=abc`, `policy/top-rules?days=abc`, `threat-feed?hours=abc` → **400** `"Invalid request parameter."` **without a `code`** | B2 |
| Unknown event | `threat-feed/999999999/` → 404 `"Event not found."` | Request detail 404 page (S4) |
| Analytics cap | `soc-kpis` with both permits held → **503 `{"error": "analytics_busy", …}`** | §6.3 retry |

**Rev 2 probe.** `apiprobe/test_lattice_probe_rev2.py` checks every rev-1 review claim the plan now relies on, in its own test database. Output: [`apiprobe/probe-rev2-results.json`](evidence/2026-11-12-f6-lattice-build/apiprobe/probe-rev2-results.json); summary and run record: [`apiprobe/SUMMARY-rev2.txt`](evidence/2026-11-12-f6-lattice-build/apiprobe/SUMMARY-rev2.txt), [`apiprobe/run-rev2.txt`](evidence/2026-11-12-f6-lattice-build/apiprobe/run-rev2.txt). The measured values are in `SUMMARY-rev2.txt`, and the plan cites them as probe P1 to P11.

| Probe | Claim checked | Where the plan uses it |
|---|---|---|
| P1 | Rule writes: version unchanged, one plan publish per write, stale policy PATCH after rule writes → 200 | N-11, S6a/S6b, B13 |
| P2 | `policies/test` ignores draft rules in the body; a disabled policy → 404 | N-16, B13 |
| P3 | A 200 with `X-Request-ID` writes no access line (2 incidental signal records carried the id, naming no path or status); a 404's `django.request` record has an empty id | N-13, B16, A2 |
| P4 | `analytics_busy` has no `Retry-After`; an anonymous call while busy → 503, while idle → 401 | N-4, N-17, B19 |
| P5 | `threat-feed?limit=1` returns 0 rows when the newest event is 60 h old; `hours=8760` returns it | Box 16 |
| P6 | `model-usage?days=abc` → 200; `owasp-stats?hours=-5` → 200 with no events | N-5, B2 |
| P7 | `module-kpis` 1.7 has no "withheld" field | Box 11, B14 |
| P8 | Both events of one request return the same `request_id` and point at each other, **but each returns its own `action`** (redact vs block) | §6.1b URL rule |
| P9 | Key PATCH has no version; three PATCHes all 200; the last one wins | N-21 |
| P10 | `gateways/keys?is_active=true` returns the same count as no filter | N-21, box 5 |
| P11 | Collapsed `threat-feed` paging returns no rows past the 4,000-event scan, while `count` is larger | N-15, B17 |

### 6.6 Agreement checks (CLAUDE.md phase 7: UI, API, DB, logs and metrics must agree)

| Check | Where | How |
|---|---|---|
| A1: UI = API = DB | Every PR, `frontend-e2e` | A seed script writes a fixture with known counts (the probe's shape, extended per slice). Each box renders its raw value in `data-value`. The test fetches the same endpoint with the same parameters through `page.request` and asserts DOM value = API value = the seed's expected count. Any mismatch fails |
| A2: UI = logs | Every PR **from the slice after B16 merges**; S3 depends on B16 | Each box fetch sends `X-Request-ID: ui-<box>-<uuid>`. The test collects the ids from the network log and asserts that control's captured stdout has one B16 access line per id, with the right path and status. Before B16 no such line exists (N-13), so A2 is not claimed |
| A3: freshness truth | Every PR | Playwright blocks one source (route abort or 503), so that box, and only that box, becomes Stale within `freshFor` and Error on a cold load. Unblocking returns it to Fresh or Updated. A 503 `analytics_busy` is retried and recovers. A `visibilitychange` resume marks every box Stale until its refetch lands |
| A4: live stack | **S9 gate**, plus a **weekly scheduled run** of the same workflow on `develop` | A compose stack on a GitHub runner, with no secrets. N valid, authenticated, under-limit requests carry a per-run nonce through the gateway (v1 today; v2 once F3 ships; ties to F1 exit L36-1). After the drain, the v1 gateway metrics, DB rows, API values and the console UI must agree. The label mapping (N-20): `amf_gateway_requests_total{decision="allowed"}` = Allowed + Watched + Redacted requests; `{decision="blocked"}` = Blocked requests, because the run sends no request that fails auth, rate limits or validation; `{decision="error"}` = 0. A run against staging needs a staging credential the owner must provide as an environment secret; until then the scheduled run uses the compose stack |
| A5: mutations (rev 2) | S5, S6a/S6b, and the kill-switch page (S7) | **Key create:** DB row `is_active=true`, Redis `auth:apikey:{hash}` present (`core/signals.py:45`), the B16 access line; the plaintext is in no storage, log or DB column. **Key revoke** (PATCH `is_active=false`): DB false, Redis entry gone, the other tab shows Revoked within 1 s (`BroadcastChannel`). **Key delete:** row and Redis entry gone. **Policy publish (S6b, B13):** exactly one new `PolicyVersion` row, `Policy.version` +1, `compile/status.version` advanced, one plan publish (counted in B13's backend test). **Live rule write (S6a):** the rule row changed and `compile/status` advanced. **Kill switch:** a `KillSwitchAuditLog` row and the Redis `kill_switch:model:{name}` key. Key mutations write no audit row today, only `logger.info` (`gateway_key_views.py:364,378`), so A5 uses the B16 line for them |

## 7. Testing

### 7.1 Backend validation first (phase 6), for every backend issue B1–B22

Each backend issue gets its own PR with:

- Django tests for malformed input (400 with `code`), boundaries (empty window, maximum window, 0 rows, 100k rows via the rollup path), tenant isolation (a canary in a foreign org must not appear: the #120 class), and concurrency (the semaphore, the 503 shape);
- the timeout path (504 shape) and logs (request id present, no secrets);
- for B13: a mid-transaction failure leaves the live policy and rules unchanged, and two concurrent publishes give one 200 and one 409;
- for B18: one org's cache entries are never served to another org, and the cache size bound holds under 100k orgs.

A frontend slice that uses a new field starts only after that PR is merged.

### 7.2 Playwright suites (phase 7)

| Suite | Contents | Browsers |
|---|---|---|
| `nav.spec` | Every route by URL, refresh on every route, Back/Forward across sidebar navigation and filter changes, `q` pushes once on submit, legacy `?tab=` redirects, `/` → `/console/overview`, `/signup` → `/login`, 404 inside and outside `/console`, `next` keeps path, query and hash, and the `next` attack list in §3.4 | Chromium, Firefox, WebKit |
| `session.spec` | Expired access token refreshes silently, before the request; expired refresh token → `/login?next=<deep link>` and back after sign-in; the 15-minute inactivity warning and logout (clock-controlled), **with an unsaved policy draft**, which is restored for the same user and invisible to another user; logout in tab A → tab B goes to login on its next action; sign out and sign in as a different user **in the same tab** → no box or draft of the first user is visible at any point; theme change propagates across tabs; key revoked in tab A → tab B shows Revoked within 1 s; sleep and resume (clock jump plus `visibilitychange`) → every box Stale, then refreshed | Chromium, WebKit |
| `boxes.spec` | The §7.4 invariants on every page; A1–A3 and A5 agreement (A2 once B16 is in); each box state reached on purpose: loading (delayed route), empty (empty fixture), error (500, 502, 504, HTML 5xx), stale (blocked source), updated, unavailable (v1 capability), forbidden (role without access, and a forbidden field in a multi-source box); `Retry-After` honoured | Chromium |
| `forms.spec` | Keys: create (secret shown once, copy, warning before leaving), edit (last-write-wins notice), revoke, delete revoked, validation errors. Policies, S6a: policy fields with a **409** from a second tab, live rule create, edit and delete with the "Saves live" label, test against live, read-only system policy, vector policy save. Policies, S6b: draft, reorder, test against the draft, publish, a **rule conflict from a second tab → 409**, **a failure mid-publish (route abort) leaves the live policy unchanged**, the draft survives a reload, discard draft | Chromium, Firefox, WebKit |
| `workflows.spec` | Overview → box source → requests list (filtered) → request detail → policy that decided → test → publish; request detail "Replay in simulator"; interrupted publish (reload during compile, then compile status is truthful) | Chromium |
| `a11y.spec` | axe on every route × theme × viewport; keyboard-only walk per drawn screen; 320 px / 200% reflow | Chromium (axe), WebKit (keyboard) |
| `visual.spec` | `toHaveScreenshot` per drawn screen × {1440x900, 834x1112, 390x844} × {dark, light} = 6 baselines per screen (viewport, not full page), with relative ages and absolute times **masked**. Full-page captures are uploaded as artifacts for the owner | Chromium |
| `csp.spec` | Every route under the enforced CSP (§4.6): no `securitypolicyviolation` event; the pre-paint hash matches the built script | Chromium, Firefox, WebKit |
| `long-session.spec` | 30 minutes of polling on an accelerated clock: JS heap after GC at minute 30 ≤ heap at minute 2 + 10 MB (CDP `HeapProfiler.collectGarbage`, `Runtime.getHeapUsage`), no long task > 200 ms after first paint, DOM node count within ±5 % | Chromium |
| `landing.spec` | Landing meta, `robots`, CSP, no third-party requests, no external script, stylesheet or font, the Lighthouse metrics (§3.3), visual baselines (6) | Chromium |

Determinism for screenshots and windows:

- The seed script writes rows at fixed offsets at least 1 hour inside each window edge, and records the server time it used (`seed_now`).
- The browser clock is installed at `seed_now` (`page.clock.install({ time: seed_now })`), so the UI's "now" and the server's windows agree within the suite's run time. The suite must finish within 50 minutes, so the 1-hour margin holds.
- Relative ages and absolute times are masked in screenshots, because control runs on real time.
- Self-hosted fonts awaited (`document.fonts.ready`); animations disabled.
- Playwright version pinned by the lockfile; the suites run in the **Playwright container image pinned by digest** at that version (§8.1), so OS and browser builds do not drift with the runner image.

The tolerance is `maxDiffPixelRatio: 0.001`. Any larger change needs new baselines, reviewed as images.

### 7.3 Extreme and edge cases (phase 8), added per slice until no new failures appear

- **Maximum limits:** 500 keys (`max_page_size`) and 501 (two pages, counted correctly), 1,000 policies, long names and codes (ellipsis plus full text in the tooltip and detail), counts of 1e9 (formatting, no box overflow), a 30 d window on rollups, a requests list deeper than 4,000 events (B17).
- **Minimum limits:** a new org with zero data (every box Empty with its sentence); one event.
- **Failures:** 401 mid-poll; a 503 storm (all analytics busy) → back-off visible in the request log; 503 `db_unavailable` with `Retry-After`; 502 `gateway_unreachable`; 504; nginx HTML 5xx; network offline and back; control restart during polling; slow 3G (no layout shift; skeletons).
- **Repeated actions:** double-click create, rapid range switching (only the last range's data renders, using the generation guard as today's overview does), rapid Back/Forward.
- **Concurrency:** two operators editing one policy (409 on fields; on rules from S6b); two operators editing one key (last write wins, stated); revoking a key while it is shown in another tab; two tabs polling (hidden-tab pause verified).
- **Time:** IST and UTC bucket labels (N-12); 7 d and 30 d labels are not calendar days; DST zones; the client clock off by ±10 minutes (ages and expiry buckets unaffected, because skew is applied); sleep and resume.

### 7.4 Brief-as-tests (owner rules made executable)

- Every element with `data-box` has a non-empty title, purpose, freshness word, age and source label, at every viewport.
- Every chip has `data-chip-source` (§5.1).
- No `role="tablist"` anywhere under `/console/` (segmented value pickers use `role="radiogroup"`).
- No bell: no control whose name matches `/notif/i`, **no request to `/api/notifications/`** and **no WebSocket** from any Lattice-drawn route (network assertions).
- **No needs-attention content (rev 2, testable form of Q6):**
  - no box lists individual items (keys, policies, requests, rules) filtered by a problem condition (expiring, idle, failing, risky, over limit);
  - no box contains a bulk-action button;
  - every list inside a box declares `data-list-kind`, one of `inventory` (all items), `ranking` (top N by volume) or `facts`, and the test fails on anything else.
- **Denylist** of box titles and purposes (case-insensitive): "needs attention", "for review", "since you last looked", "expiring soon", "stop working within", "key hygiene", "worth a second look", "rotate", "action required".
- No live region that announces new events or records (form error summaries may use `aria-live`).
- The palette lint: 0 raw classes, `dark:` variants, hex literals or `innerHTML` under `src/console/`.

## 8. Delivery slices (one PR each, into `develop`)

Every slice:

- carries before and after screenshots for the owner (the quality gate) in `docs/plans/evidence/<date>-f6-<slice>/`;
- passes `frontend` (lint, unit, build, budgets) and `frontend-e2e`;
- gets two reviewers (A and B; B merges).

Prod stays on the legacy console until S9 (`VITE_LATTICE=off` in prod builds; on for develop and staging builds).

| Slice | PR title | Depends on | Contents | Acceptance criteria (summary; full checklists go in each PR) |
|---|---|---|---|---|
| **S1** | `feat(frontend): Lattice tokens, fonts, theme default and lint gates` | – | `design/tokens.mjs` → css/json; `@theme inline`; `[data-ui=lattice]` scope; Geist self-hosted; dark default **under the flag** plus pre-paint script; `lint-palette` ratchet (baseline 9,746) and `innerHTML` ban; contrast gate; Playwright and axe harness plus the `frontend-e2e` job (§8.1) with a smoke spec (login, both themes); bundle-budget script (**report only**) | Token files regenerate with no diff; contrast 0 failures; the ratchet fails a planted raw class in `src/console/`; a fresh profile is dark with no flash when the flag is on; a flag-off build looks exactly like today (every view in AUDIT.md's route map, before and after); DESIGN.md and code agree; the `changes` filter is named in the PR body |
| **S2** | `feat(frontend): Lattice shell and real routes` | S1 | Router with `/console/*` routes (lazy, code-split); sidebar (numbered checkpoints, Tools, Settings; no bell); top bar (search dialog, range picker, theme, account); legacy pages mounted at new routes (`AIMeshFirewallConfig` at `/console/connections`); legacy `?tab=` map; `/` → overview; `next` guard (§3.4); 404s; CSP enforced in e2e and report-only in nginx (§4.6); **budget gate switched to enforcing** | `nav.spec`, `session.spec` and `csp.spec` green on 3 browsers; Back/Forward and refresh on every route; no `/api/notifications/` calls and no WebSocket from the shell; search finds pages, keys (client-side over the keys list) and policies (4 calls until B15), and an exact event id via `threat-feed/<id>` (request-id lookup arrives with B9); initial JS for `/login` ≤ 120 kB gzip (from 646 kB), as defined in §8.2 |
| **S3** | `feat(frontend): Lattice overview boxes` | S2, **B16** | Box, Chip (chip rule), chart primitives, all box states; `useSource`, the scheduler (§6.3), X-Request-ID, `BroadcastChannel`, skew and resume handling; the boxes whose sources exist (§6.1: 14 of 16, partial ones with their sourced fields and chips only) | A1–A3 green (A2 with B16); every state reached in `boxes.spec`; `long-session.spec` green; visual baselines (6); axe 0; initial JS for `/console/overview` ≤ 250 kB gzip; the **S3 load gate** in §6.3 passes and its numbers, plus the 200 and 2,000-console projections, are in the PR |
| **S4** | `feat(frontend): requests list and request detail` | S3, **B17**; B9 for `q`, `status` and request-id URLs | `/console/requests` (keyset-paged table from B17, filters in the URL, manual refresh, no auto-insert; `q` and `status` are **hidden**, not disabled, until B9 merges) and `/console/requests/:eventId` (the §6.1b boxes, Record freshness, Copy link, Replay) | A deep link works after a refresh; any sibling event id opens the same request; an unknown or foreign event id gives a 404 page (tenant canary); paging past 4,000 events works; stage durations equal `pipeline_trace`; v1 events without a trace show Unavailable for the stage box; the HTTP status field is shown only if its agreement test passes |
| **S5** | `feat(frontend): gateway and API keys (1.1)` | S3 | Keys page per §6.1b; create, edit, revoke and delete forms at URLs; call snippet from `gateways/public-url`; "Key lifetimes" and "Key scope" (Q6 default) | `forms.spec` keys green; A5 for keys; the secret is shown once and is never stored: no storage, URL, log or IndexedDB entry contains it, checked **with the whole shell loaded and every route visited** (legacy pages included); boxes without B5 not rendered; §7.4 needs-attention tests green |
| **S6a** | `feat(frontend): policy editor, live edits (1.2)` | S3 | Grouped policy list (Pipeline, RAG, MCP, Vector; no tabs); policy fields with `version` (409); rules created, edited and deleted **live, one at a time, labelled "Saves live"**, with **no reorder**; "Test against live"; read-only state for system and MCP-seeded policies; vector policies at their own route, saved live; compile facts | `forms.spec` S6a rows green; 409 on policy fields from two tabs; A5 for live rule writes; the "Draft against live", "Hits by rule", "Rollout" replica line and "Mapped to" boxes are absent |
| **S6b** | `feat(frontend): policy drafts and atomic publish (1.2)` | S6a, **B13** | Client draft in `sessionStorage` keyed `lattice:draft:<userId>:<orgId>:<policyId>:<baseVersion>` (cleared on explicit sign-out); reorder; "Draft against live" diff; test against the draft (B13 dry run); publish through B13; poll `compile/status` | A rule conflict from a second tab → 409; a failure mid-publish leaves the live policy unchanged; the draft survives a reload and an inactivity logout for the same user, and is invisible to another user; one publish = one `PolicyVersion` row (A5) |
| **D2** | `docs(design): Lattice mockups for 1.3–1.7, simulator, connections, settings` | – | Static mockups in the same folder plus PNGs (desktop, mobile, both themes) for the owner, **with a source map per box in the §6.1 format** | Owner picks before S7 code; every drawn box has a source status |
| **S7a–e** | `feat(frontend): Lattice <page>` (one PR per page or pair) | D2, S3 | 1.3, 1.4 (removes 5 tab strips), 1.5, 1.6, 1.7, simulator, connections and settings (the A-08 split), profile | The same criteria as S3 for the page's boxes (states, A1–A3, A5 where it mutates, visual baselines, axe 0, §7.4); the page's palette count reaches 0; legacy panel files deleted when unused. **The simulator stops persisting key plaintext** (`useSimulatorEngine.js:54`, `useIsolationPlayground.js:51`, `useGatewayCredential.js:52`): keys live in memory only, asserted by a storage scan |
| **S8** | `feat(landing): static Lattice landing page` | S3 (shares `Box`, `Chip` and the chart primitives), Q1, Q10 | `frontend/landing/` prerendered entry, SEO files, OG image, landing CSP, Lighthouse CI; served per Q1 | §3.3 metrics green; no third-party requests; no external script, stylesheet or font; visual baselines; copy approved by the owner |
| **S9** | `feat(frontend): cut over to the Lattice console` | **Entry criteria below** | `VITE_LATTICE=on` in prod; CSP enforced in nginx; `noindex` on the console host; `robots.txt`; the landing route only if the owner chose `/` | A4 green on the compose stack; rollback rehearsed on staging (flag off → legacy back); owner sign-off on the screenshots |
| **S10** | `chore(frontend): retire the legacy console` (at least one release after S9, owner OK) | S9 + one release | Delete the legacy shell, pages, notification UI, WebSocket client and unused chart library; `--color-*: initial`; drop the Google Fonts imports and their CSP hosts; move tokens to `:root`; B21 in the backend | Palette baseline 0 (the lint baseline file is deleted); bundle numbers recorded; all suites green |

**S9 entry criteria (rev 2):**

- S1–S5, S6a and S6b are merged. If B13 is late, S6b may be replaced by the legacy rule editor mounted in the shell, with the owner's explicit OK.
- D2 is approved, and each S7 page is either merged or its legacy page is shown inside the Lattice shell, with the owner's explicit OK per page.
- B9 is merged and the Q12 decision is implemented (escalations stay visible; N-19).
- B16 is merged and A2 is green.
- B18 is merged, unless the S3 load gate showed headroom for 200 consoles at the production worker count.
- A4 is green.
- Rollback is rehearsed on staging.
- The landing page (S8) is **not** an S9 criterion when it has its own origin (Q1 default).

### 8.1 CI jobs (on GitHub runners; local runs are targeted only)

- `frontend` (existing, `reusable-node-ci.yml`): add `lint-command: npm run lint`; `build-command: npm run build && npm run check:budget`.
- `frontend-e2e` (new reusable `reusable-web-e2e.yml`, called from `ci.yml`):
  1. **Container:** the Playwright image pinned **by digest** at the lockfile's Playwright version (no `npx playwright install --with-deps`, so no apt installs and no runner-image drift). Services: the same pinned images as `reusable-django-ci.yml` (`pgvector/pgvector:pg16@sha256:0dd3149a…` and `redis:7.4-alpine@sha256:7b1753b4…`). Python 3.12 from the image with control installed in a venv (`-e control[dev] -e shared`).
  2. `migrate`, then the seed script.
  3. Control under gunicorn with 2 `UvicornWorker`s (prod-like), with stdout captured for A2. The e2e suites do **not** exercise the N-4 semaphore: one browser with a per-tab cap of 2 never trips it. Only the S3 load gate does.
  4. `vite preview` with the console CSP header (§4.6); its proxy defaults to `server.proxy`.
  5. The suites, as a **browser matrix** (`chromium`, `firefox`, `webkit`), with Chromium sharded in 2.
  6. Upload the HTML report, diffs and full-page screenshots as artifacts with `retention-days: 14` (as `reusable-django-ci.yml:133-138`). Traces are kept **on failure only**.

  `timeout-minutes: 30` per matrix job. Every `uses:` is pinned to a full SHA.
- **Least privilege:**
  - `permissions: contents: read` at workflow **and** job level;
  - `actions/checkout` with `persist-credentials: false`;
  - triggers are `pull_request`, `push` and `workflow_dispatch` only, **never `pull_request_target`**;
  - **no repository secrets**: every credential (DB password, Django secret, test users, gateway keys) is generated per run.

  Traces, HARs and the captured control stdout carry those per-run JWTs and the once-shown key from `forms.spec`. That is harmless only because they are generated per run and expire with it; the workflow file says so in a comment, and nothing may be added that reuses a credential across runs.
- **The `changes` filter is a gate, not a convenience.** A `changes` step skips quickly (with success) when nothing relevant changed, so the check can be required without hanging. Its filter is:
  - `frontend/**`, `control/**`, `shared/**`;
  - `.github/workflows/ci.yml` and `.github/workflows/reusable-web-e2e.yml`;
  - every script the job runs (`frontend/scripts/**`, the seed script);
  - from S9 on, `deploy/nginx*.conf` and `deploy/security-headers.inc`.

  The S1 PR body names the filter, and a change to it needs reviewer B's rule-6 sign-off.
- **Baseline updates:** `workflow_dispatch` with `update_snapshots=true` runs the visual specs with `--update-snapshots` and uploads `__screenshots__`. The author downloads the artifact with `gh api …/actions/artifacts/<id>/zip` into an empty directory (verified reachable from this host on 12 Nov), looks at every image, and commits them. No workflow gets write access to the repository.
- `landing-lighthouse` (S8): Lighthouse CI against `vite preview` of the landing build, asserting the §3.3 metrics on the median of 3 runs.
- `a4-live` (S9, and weekly on a schedule): the compose stack and the A4 check (§6.6), with no secrets.
- `s3-load` (S3, `workflow_dispatch`): the load gate in §6.3.
- Rule 6: S1 changes `.github/` (adds jobs; relaxes no gate) and needs reviewer B's explicit sign-off. `frontend-e2e` becomes a required check after 3 consecutive green runs on `develop` (owner or B updates the ruleset).
- Cost: the repository is **public today**, so standard GitHub-hosted Linux minutes are free. If it becomes private, the e2e matrix costs about 4 jobs × 10–15 minutes per PR that touches the filter, about 40–60 minutes per PR, which has to fit the plan's Actions minutes, and the owner should know that.
- **Local runs on this host** are targeted only: 2 CPUs, no Docker, and native Postgres 16 **without pgvector**. Creating a control test database needs the out-of-repo settings shim other agents use (`/home/claude/work/runs/113-A/harness/rv113_settings.py`, copied, with a private test database name; it strips `CREATE EXTENSION vector` from migration `policy/0021`). Both probes in §6.5 ran that way. CI needs no shim.

### 8.2 Bundle budgets: how they are measured (B-S9)

- **Gated number:** from the Vite manifest (`build.manifest: true`), a route's initial JS = its entry chunk plus every chunk reachable through **static** `imports`, excluding `dynamicImports`. Each file is gzipped at level 9 with Node's `zlib`, and the sizes are summed. `scripts/check-budget.mjs` computes it for `/login` and `/console/overview`.
- **CSS budget:** the CSS those chunks pull in, ≤ 32 kB gzip until S10 (today 28.53 kB for the whole app). S10 records the new number and sets the budget at +5 % of it.
- **Reported, not gated:** Playwright's transferred bytes on a cold load of each route (JS, CSS and fonts), so caching or font changes show up.
- S1 adds the script in report-only mode, and **S2 switches it to failing** in the same PR that adds code splitting. §9's "budgets enforced from S2" refers to this.

## 9. Risks and rollback

| Risk | Mitigation |
|---|---|
| Boxes without honest sources (§6.1, §6.1b) | Never render a fake, derived or zero value; the chip rule; ship sourced boxes; B-issues tracked; owner Q5 and Q14 |
| Mockup source lines are wrong for 4 overview boxes (2, 4, 5, 8) and several detail boxes; box 3 names a field | §6.1 and §6.1b correct them; the footer names the endpoint actually read |
| Live rule writes republish the plan one at a time (N-11) | S6a labels every rule save "Saves live" and offers no reorder; S6b and B13 make publish atomic |
| v1 is the production data plane (R16); v2-only fields are absent in prod | Unavailable state; capability detection per source. Overhead is not v2-only (N-10) |
| Analytics load and N-4 503s | 30 s cadence; deduplication; an in-flight cap of 2; full-jitter retry and back-off; no WebSocket refetch; the S3 load gate; B18 and B19 |
| "Fresh" overstating drained data (N-18) | "Updated" until B1 and B12 |
| Tokens in `localStorage`; attacker-controlled text rendered | CSP (§4.6), the `innerHTML` ban, B20; legacy key persistence removed in S7 |
| Visual baselines flaky across environments | A pinned Playwright container; a fixed clock; masked ages; self-hosted fonts; viewport-sized shots |
| Legacy code is large (47.6k lines); migration is long | Legacy pages are mounted in the new shell from S2, so URLs and the shell are fixed early; per-page slices; the palette ratchet shows progress |
| Interim look on staging (Lattice shell around legacy pages) | Prod is unaffected until S9; owner screenshots per slice |
| Bundle growth | Budgets enforced from S2 (§8.2); route-level code splitting; ECharts lazy |
| New dependencies (`@tanstack/react-query`, `@playwright/test`, `@axe-core/playwright`, `geist`, Lighthouse CI) and the 178 open Dependabot alerts | Lockfile pinned; the dependency-scan job; dev dependencies kept out of the prod bundle; the owner can veto TanStack Query (in-house hook fallback) |
| Public repository | No secrets in fixtures or workflows; AI-DSPM reference images (owner Q8); wordmark source (owner Q9) |

**Rollback:**

- Before S9, every slice is a plain revert, and prod never sees `/console/*` (or the dark default) because of the build flag.
- After S9, rebuild with `VITE_LATTICE=off` (the legacy shell and pages remain until S10). If the CSP breaks something, the nginx header can be switched back to report-only on its own. S9 rehearses both on staging.
- S10 is only done after one release with no rollback, and the owner agrees.
- Backend issues ship independently, each with its own rollback.

## 10. What this plan does NOT do

- No code (no `frontend/`, `control/` or CI changes in this PR).
- No designs for 1.3–1.7, the simulator, connections, settings or the requests list (D2 and owner Q7).
- No backend endpoints: B1–B22 are proposals for separate issues and PRs.
- No v1 gateway changes (v1 is frozen); B3 and B5 read what v1 already emits or add control-side writers.
- No marketing copy beyond the mockup, no pricing page, no claims (owner Q6/Q10).
- No i18n or RTL, no offline/PWA, no product analytics or trackers, no SSO changes, no change to `/oauth/callback`.
- No push channel for escalations (email or webhook) unless the owner asks (Q12).
- It does not settle AUDIT.md's placeholder (#129). The audit findings this plan needs are re-verified in §2.2.

## 11. Owner questions (each with the default used if there is no answer)

None of these is answered yet. Rev 2 changes the default of Q1 and Q12, rewords Q5 and Q6 to match corrected facts, and adds Q14.

| # | Question | Recommended default |
|---|---|---|
| Q1 | Where does the landing page live: `/` on the console host, a separate host, or zeroshield.ai? | **Rev 2: its own origin** (a host you name, served by a second nginx `server` block in the same image, or published on zeroshield.ai). The console host keeps `/` → `/console/overview`; `/login` and `/oauth/callback` are unchanged. Reason: the console origin holds JWTs in `localStorage` (B-S8). If you prefer `/` on the console host, §3.3 lists the conditions |
| Q2 | Product name on the landing page | "ZeroShield" wordmark with "AI Mesh Firewall" as the product line (as drawn) |
| Q3 | Default theme | Dark, for the Lattice console and the landing page; the user's choice persists; Light and System in Settings. The dark default reaches prod only at cutover (S9), not with S1 |
| Q4 | Confirm violet `#A48CFF` / `#6B4FE0` and Geist as AI Mesh Firewall's identity | Yes, as approved with Lattice |
| Q5 | Boxes whose data does not exist yet: overhead (v1 measures it, but control has no endpoint, B3), fleet (`gateways/stats` gives "N of M reporting", but counts dead agents and is costly, B4), and partial fields and chips | Overhead and fleet hidden until B3 and B4 land; partial boxes show only sourced fields and chips; never a zero or a placeholder number |
| Q6 | The keys page's "Expiring soon" (with "Rotate both") and "Key hygiene" boxes read as "needs attention" lists, which the brief forbids | Replace them with neutral distribution boxes: "Key lifetimes" (counts by expiry bucket: none, < 7 d, < 30 d, later) and "Key scope" (counts by allowed-model breadth), with no key lists and no action buttons. Testable rule: no box lists individual keys filtered by a problem condition or offers a bulk action (§7.4). The page-header "Delete revoked (N)" action (the existing `purge-revoked`, with a confirmation) stays unless you say otherwise |
| Q7 | The Requests list is not drawn. Is a filterable, paged table acceptable? | Yes: newest first, keyset-paged (B17), filters in the URL, manual refresh, **no** live insertion, no "new" badges, so it is not a feed |
| Q8 | May the AI-DSPM reference screenshots of the public demo (`ref-aidspm-*.png`, `f6-compare-lattice-*.png` in PR #123) stay in this **public** repo? | No: remove them from #123 before merge and keep them privately; the Lattice PNGs stay |
| Q9 | Wordmark source: the PNG was taken from the AI-DSPM console's assets | Use it until an official SVG wordmark is supplied; please supply one |
| Q10 | Landing CTA target ("Book a demo") and approved claims | CTA links to a contact URL you give; no numbers, customers or certifications until approved |
| Q11 | Any analytics or tracking on the landing page? | None, enforced by the landing CSP and a network assertion |
| Q12 | **Rev 2, restated.** Escalations and isolation notices reach admins today **only** through the bell (`Notification` rows plus the WebSocket); control has no email or webhook alerting (N-19). With no bell, how should admins learn about escalations? | **Rev 2 default:** an "Escalations" count box on the overview (open escalated incidents as a **number**, with a link to the Requests list filtered `?status=escalated`; no list of incidents, no badge, no push), plus the status on request detail. Both need B9, which is an S9 entry criterion. If you want push (email or webhook to admins), that is a new backend issue; if you want no surfacing at all, say so |
| Q13 | Keep the 1.1–1.7 numbering in navigation | Yes, number plus name (as drawn) |
| Q14 | **New.** Some drawn detail fields have no honest source: cost, the "Security checks" list (today's flags are derived from `threat_type`), underlined personal data, "no match" rule rows, user agent, plan version on v1 | Leave them out; no backend work. Ask for any of them and it becomes a backend issue (B22 for security checks) |

## 12. Author's contradiction pass (rev 2; for reviewers to attack)

- **Lens 1, false positives:**
  - N-7 rests on a search for writers of `last_used_at` across `control/`, `gateway*/`, `workers/`, `services/` and `shared/`, which found none, and on the probe (no traffic, so the probe alone cannot prove a sync never happens). To prove it either way, call the gateway with a key on the live stack and read `/api/gateways/keys/`. Planned in B5.
  - Rev 1 claimed that N-4 might never fire because Django runs sync views on one thread per worker. **That was wrong** (A-N1): Django 6.0.10 wraps each ASGI request in its own `ThreadSensitiveContext` (`core/handlers/asgi.py:169`), so the third concurrent analytics call per worker gets the 503. The repo's `main_app/asgi.py` docstring is outdated in the same way.
  - Rev 1 claimed v1 does not measure overhead (N-10). That was wrong, and it is corrected.
- **Lens 2, hidden failures:**
  - Request-level KPIs and event-level series (box 1) and event counts (`policies/analytics`) disagree by design. The plan never puts an event-based number next to a request-based number in one box.
  - Rule writes are live and unversioned (N-11), the requests list cannot page past 4,000 events (N-15), and the policy test ignores drafts (N-16). All three were hidden behind the rev-1 plan.
- **Lens 3, state:**
  - stale caches after mutations, broadcast across tabs (§6.3);
  - cache and drafts keyed by user and org;
  - sleep and resume;
  - skew applied to every server timestamp;
  - a draft against a moved live version (409; for rules from S6b).
- **Lens 4, observability:**
  - box errors today go only to `console.error`;
  - the plan sends request ids on every fetch and checks them against control's access lines, which do not exist until B16 (N-13), so A2 is gated on B16;
  - server-side data age is not observable until B1 and B12, so analytics say "Updated";
  - nginx-generated errors carry nginx's request id, so the popover shows both ids;
  - Prometheus `decision` labels are status-based (N-20), and A4 maps them explicitly.
- **Lens 5, edge cases:**
  - 25-bucket grids, IST labels and rolling 7 d and 30 d cells (N-12);
  - 0 rows and 1e9 counts;
  - forbidden roles and forbidden fields;
  - malformed URL parameters: `?range=abc` is normalised with `replace` and never reaches the API as a 400;
  - `next` attacks (§3.4);
  - 501 keys;
  - requests lists deeper than the scan.

## Appendix A: evidence index

| File | What it backs |
|---|---|
| `evidence/2026-11-12-f6-lattice-build/counts.sh`, `counts.txt` | A-05 counts, tab-strip counts, fonts, line counts at the `frontend/src` tree `81157b28` (identical at `562619c8`) |
| `evidence/2026-11-12-f6-lattice-build/counts-reruns.txt` | A-S9: the rev-1 script truncates its output in some runs; the rev-2 script gives identical output in 10 of 10 runs |
| `evidence/2026-11-12-f6-lattice-build/vite-build-develop-562619c8.txt` | The bundle baseline (2,211.83 kB JS, one chunk) |
| `evidence/2026-11-12-f6-lattice-build/apiprobe/test_lattice_probe.py` | The rev-1 probe (runs in the control test runner; throwaway DB) |
| `evidence/2026-11-12-f6-lattice-build/apiprobe/probe-results.json`, `SUMMARY.txt`, `run.txt` | §6.5 rev-1 results, command and duration |
| `evidence/2026-11-12-f6-lattice-build/apiprobe/test_lattice_probe_rev2.py` | The rev-2 probe (P1–P11) |
| `evidence/2026-11-12-f6-lattice-build/apiprobe/probe-rev2-results.json`, `SUMMARY-rev2.txt`, `run-rev2.txt` | §6.5 rev-2 results, command and duration |

## Appendix B: rev-1 review dispositions

Every finding of Plan Reviewer A (review 5871851572) and Plan Reviewer B (review 5871852636), with what rev 2 does about it. "Fixed" means the plan text changed (section given). "Argued" means the plan keeps or changes its position for the reason given. "Issue" means a backend issue the coordinator opens (§6.4).

| Reviewer | ID | Severity | Disposition | Where |
|---|---|---|---|---|
| A | A-B1 | BLOCKER | **Fixed.** Verified by probe P1 (4 rule writes → 4 publishes, version 1, stale policy PATCH → 200). N-11 rewritten. S6 split: S6a (live, labelled, no reorder) and S6b (draft, test, atomic publish). B13 promoted to required, with a per-rule version bump and a draft dry run (N-16, probe P2). Acceptance: a mid-publish failure leaves the live policy unchanged, and a rule conflict from a second tab → 409 | §2.2 N-11, N-16; §6.1b; §6.4 B13; §7.2 `forms.spec`; §8 S6a, S6b |
| A | A-S1 | SHOULD-FIX | **Fixed.** N-10 rewritten: v1 measures overhead (metrics, `pipeline_trace` fields). B3 reads v1 data; box 3 never shows "Not measured" on v1; the Unavailable example changed. #160's B3 note must be corrected (hand-back) | §2.2 N-10; §5.2; §6.1 box 3; §6.4 B3 |
| A | A-S2 | SHOULD-FIX | **Fixed.** Box 4 and box 11 are now partial; totals 5/10/1; a "Chip or tag: source" column and the chip rule; chips without a source are not drawn; B14 adds 1.7 withheld; box 16's "Plan versions" dropped; box 9's label follows its source ("reroute switches") | §0; §5.1; §5.3; §6.1; §6.4 B14 |
| A | A-S3 | SHOULD-FIX | **Fixed.** New §6.1b source maps for request detail (11 boxes), keys (8) and policy editor (9), with status and gaps. New finding: "Security checks" flags are derived (`core/tasks.py:268-275`), so the box is dropped (Q14, optional B22). The PR body no longer ticks a criterion the plan did not meet | §6.1b; §6.4 B22; §11 Q14 |
| A | A-S4 | SHOULD-FIX | **Fixed.** Vector policies at `/console/policies/vector/:vectorPolicyId` (UUID), no version, live saves stated as last-write-wins; a read-only state for system and MCP-seeded policies (N-22) | §2.2 N-22; §3.1; §6.1b; §8 S6a |
| A | A-S5 | SHOULD-FIX | **Fixed.** A testable needs-attention rule (no filtered-by-problem item lists, no bulk action in a box, `data-list-kind` allowlist) and a denylist that includes "expiring soon", "key hygiene", "worth a second look", "stop working within", "rotate"; "Rate limit headroom", if B5 ships it, becomes a distribution | §6.1b keys; §7.4; §11 Q6 |
| A | A-S6 | SHOULD-FIX | **Fixed.** Q12 restated with the facts (N-19: `Notification` rows + WebSocket only; no outbound path). Default changed to an Escalations count box (no list) plus request-detail status; B9 is an S9 entry criterion | §2.2 N-19; §6.4 B9; §8 S9 criteria; §11 Q12 |
| A | A-S7 | SHOULD-FIX | **Fixed**, all parts. S2: search scope stated (event-id lookup until B9); `useSource`, scheduler and X-Request-ID moved to S3; Connections consistent (S2 mounts, S7 splits). S3: load gate with thresholds and environment. S4: needs B17 (keyset; also N-15, probe P11), and `q`/`status` are hidden until B9. S7: the D2 source map plus S3's criteria. S8 depends on S3. S9: entry criteria. S1: the dark default is behind the flag | §3.1; §6.3; §8 table and S9 criteria; §4.3 |
| A | A-S8 | SHOULD-FIX | **Fixed.** `/` → `/console/overview`; the post-login default is `/console/overview`; 404s for non-console paths. With the Q1 default (own origin), bookmarks of `/` keep working. If the owner picks `/`, the "Sign in"/"Open the console" links and a release note are required | §3.1; §3.3; §11 Q1 |
| A | A-S9 | SHOULD-FIX | **Fixed.** `head` replaced by `awk 'NR<=10'`; `counts.txt` regenerated with every figure; `counts-reruns.txt`: rev-1 script truncated in 2 of 10 runs here, rev-2 script identical in 10 of 10 | evidence `counts.sh`, `counts.txt`, `counts-reruns.txt` |
| A | A-N1 | NIT | **Fixed.** §12 corrected (Django `ThreadSensitiveContext` per request); N-4 states it | §2.2 N-4; §12 |
| A | A-N2 | NIT | **Fixed.** N-5 rewritten (clamps, negative windows, silent fallbacks; probe P6); B2 forbids silent fallbacks; footers state the window the response used (B1) | §2.2 N-5; §6.4 B1, B2 |
| A | A-N3 | NIT | **Fixed, differently.** Probe P5 confirms the 48 h default. Rev 2 does not request `hours=8760`: that would make every tick aggregate a year of events (`security_views.py:566-611,617-624`). Box 16 shows records from `soc-kpis`, and "last written" waits for B12 (which now includes the newest event time) | §6.1 box 16; §6.4 B12 |
| A | A-N4 | NIT | **Fixed.** Box 5 and the keys page count in the browser over every page, with simulator keys separated and named; B5 adds a counts endpoint (probe P10: `is_active` filter ignored) | §2.2 N-21; §6.1 box 5; §6.4 B5 |
| A | A-N5 | NIT | **Fixed.** 7 d and 30 d cells are labelled as rolling windows, never calendar days; no strip is presented as summing to its KPI | §2.2 N-12; §5.4 |
| A | A-N6 | NIT | **Fixed.** `SubModuleResultsPage` (file `SubmoduleResultsPage.jsx`); `/signup` in the route map | §3.1; §3.2 |
| A | A-N7 | NIT | **Fixed.** `q` is pushed on submit only; tested in `nav.spec` | §3.1; §7.2 |
| A | A-N8 | NIT | **Fixed.** Probe P8: any sibling event id returns the same `request_id` and its siblings, **but each returns its own `action`** (redact vs block). The URL names the opened event; the Outcome box lists every event's action and gives a request-level outcome only from B9; `/console/requests/by-id/:requestId` after B9 | §6.1b; §3.1; §6.4 B9 |
| A | Edge-case table | – | **Fixed.** Key PATCH last-write-wins stated and tested (probe P9); an unsaved draft during the inactivity logout is in `session.spec`; `firewall-config` → `/console/connections`, which links to Settings after the S7 split | §2.2 N-21; §7.2; §3.1 |
| A | Owner-question views | – | **Adopted** where they changed facts (Q3 timing, Q5 wording, Q6 testable, Q7 keyset, Q12 premise). No question is marked answered | §11 |
| B | B-B1 | BLOCKER | **Fixed.** Verified by probe P3 (a 200 with an id → no access line, only 2 incidental signal records; a 404 → one `django.request` record with an empty id). N-13 rewritten; new backend issue B16; A2 is claimed only after B16; S3 depends on B16 | §2.2 N-13; §6.4 B16; §6.6 A2; §8 S3 |
| B | B-S1 | SHOULD-FIX | **Fixed (a, b, d); issue (c).** (a) A load model in §6.3, with the per-source cost **measured** in the S3 load gate (≥ 10M rows, rollups on and off, `1h` included); it is not measured here, because this host is shared and has 2 CPUs. (b) S3 pass thresholds and environment. (c) B18, a per-org response cache, is an S9 entry criterion unless S3 shows headroom. (d) Phase offsets. Also: analytics cadence 30 s; 7 capped calls per minute per tab instead of 13 | §6.3; §6.4 B18; §8 S3, S9 |
| B | B-S2 | SHOULD-FIX | **Fixed (a, b); issue (c).** (a) A retry table for every control and nginx response, full jitter, `Retry-After` honoured, cadence back-off ×2/×4. (b) The WebSocket is removed from the Lattice shell, which also removes the shell's token-in-query-string. (c) Tokens refresh before a request when `exp` is near, so a 503 never hides an expired token (N-17, probe P4); B19 moves the semaphore after auth | §2.2 N-17; §3.2; §6.3; §6.4 B19 |
| B | B-S3 | SHOULD-FIX | **Fixed.** The new "Updated" state for analytics until B1 and B12; N-18; strips say "by ingest time" | §2.2 N-18; §5.2; §5.4; §6.2 |
| B | B-S4 | SHOULD-FIX | **Fixed, with one change.** (a) `Date` skew applied to every server timestamp; a 1 s ticker for ages. Fetch age uses the monotonic clock, **but** browsers differ on whether it advances during sleep, so resume signals mark all boxes Stale and refetch. (b) Seed offsets ≥ 1 h from window edges; the browser clock is installed at the seed's server time; ages and times are masked in screenshots | §6.2; §7.2 determinism |
| B | B-S5 | SHOULD-FIX | **Fixed.** (a) `BroadcastChannel` is required for keys, policies and kill switches. (b) Query keys include user and org; the cache is cleared on sign-out, sign-in and org switch. (c) Drafts keyed by user, org, policy and version, and cleared on explicit sign-out; tested with two users in one tab | §6.3; §8 S6b; §7.2 `session.spec` |
| B | B-S6 | SHOULD-FIX | **Fixed.** A5 mutation agreement (DB, Redis, `PolicyVersion`, `compile/status`, `KillSwitchAuditLog`). Key mutations have no audit row today, only `logger.info`, so A5 uses the B16 line. A4 is an S9 gate plus a weekly scheduled run on the compose stack; the Prometheus `decision` mapping is explicit (N-20). A staging run needs an owner-provided secret, so it is not planned without one | §2.2 N-20; §6.6 A4, A5; §8.1 |
| B | B-S7 | SHOULD-FIX | **Fixed.** The `changes` filter is listed and named in S1; a pinned Playwright container by digest; `persist-credentials: false`, no `pull_request_target`, no secrets, `contents: read` at both levels; artifacts kept 14 days, traces on failure only, per-run credentials stated; a browser matrix with shards; public-versus-private cost stated; the N-4 claim dropped | §8.1 |
| B | B-S8 | SHOULD-FIX | **Fixed (guard, CSP, key persistence, landing); issue (token storage).** The `next` algorithm and its attack list (§3.4, including flag-off behaviour); a console CSP (§4.6; `security-headers.inc`'s comment is contradicted by the built `index.html`); the `innerHTML` ban; S5's storage assertion runs with the whole shell; S7 stops key persistence; Q1 defaults to its own origin, with CSP and CI checks if `/` is kept; B20 for an HttpOnly refresh cookie | §3.3; §3.4; §4.2; §4.6; §8 S5, S7; §6.4 B20; §11 Q1 |
| B | B-S9 | SHOULD-FIX | **Fixed.** Budgets measured from the Vite manifest (static imports, gzip -9), transfer bytes reported; a CSS budget; report-only in S1 and failing from S2; `long-session.spec` | §8.2; §7.2 |
| B | B-N1 | NIT | **Fixed.** The popover shows both request ids; HTML error bodies are never rendered | §5.1; §6.3 |
| B | B-N2 | NIT | **Fixed.** Lighthouse gates on metrics (median of 3); scores are reported | §3.3 |
| B | B-N3 | NIT | **Fixed.** RAG collections polled every 5 minutes, hidden for non-admins | §6.1 box 7; §5.2 Forbidden |
| B | B-N4 | NIT | **Issue.** B21 stops or expires `Notification` rows at S10 | §6.4 B21; §8 S10 |
| B | B-N5 | NIT | **Fixed.** The wall-display inactivity logout is accepted and stated | §6.2 |
| B | Lens 3 "rehydration after sleep/resume" | – | **Fixed.** Resume handling | §6.2; §7.2 |
| B | Owner-question views | – | **Adopted:** Q1 default changed to its own origin; Q3 CSP hash; Q11 enforced by CSP; Q12 premise corrected. No question is marked answered | §11 |
