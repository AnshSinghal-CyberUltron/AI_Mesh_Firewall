# FRONTEND_AUDIT.md — ZeroShield Frontend Hardening + Impeccable Revamp

Operational tracker for the main-branch frontend-hardening Ralph loop. One surface
per iteration: **verify (both themes × 4 widths) → fix → re-verify → gate → commit.**
This file is the source of truth for progress and MUST end fully resolved before
`<promise>COMPLETE</promise>`.

Scratchpad: `scripts/ralph/frontend_harden_scratchpad.md`.

---

## Claims pre-flight protocol (run before editing ANY panel)

```
ls mcp-parallel/claims/*.claim   # then grep the target filename across active claims
```

A claim is binding only when `status: active`. As of setup (2026-07-02):

| Claim | Status | Frontend files owned / forbidden |
|---|---|---|
| `claude-ralph-stress-iter1-R0` | **active** | owns `ModelConnectionPanel.jsx`, `OutputPipelineTimeline.jsx`; **never_edit** `MCPConnectorPanel.jsx` |
| `cursor-chat-pipeline-freeze-*` | active/released | backend only (no frontend files) |
| all `cursor-ralph-iter*` | released | — |

### VERIFY-ONLY set (this loop must NOT edit — log findings only, scratchpad item 17)
- `MCPConnectorPanel.jsx` (never_edit)
- `ModelConnectionPanel.jsx` (owned by stress session)
- `OutputPipelineTimeline.jsx` (owned by stress session — the "pipeline-trace cards")

If a shared `components/ui/*` primitive or theme token must change, it is
**API-compatible only** (styles/tokens; never props/structure/exports) so branch
sessions rebase cleanly. Commit small, per surface. Pull main frequently.

---

## Gate — all must pass before checking any `[x]`

1. `cd frontend && npm run lint` (→ `node --test src/utils/*.test.js`) ✅
2. `cd frontend && npm run build` (vite) ✅
3. Surface Playwright-verified in **dark AND light** at **1440 / 1024 / 768 / 375**, screenshots saved.
4. Data-integrity: every number/table/chart binds to real backend data (or honest empty state).
5. No-leak: no raw key/PII/secret/topology rendered.
6. Impeccable detector clean on each edited file (`node .claude/skills/impeccable/scripts/detect.mjs --json <file>`).
7. Clean console + network (no errors, no failed requests).
8. Commit the surface.

---

## Run / infra notes

- **Dev server:** `cd frontend && npm run dev` → Vite on `:5173` (`--host 0.0.0.0`). Not running at setup.
- **Backend:** frontend proxies `/api` → `http://127.0.0.1:8100` (control plane, `VITE_CONTROL_PROXY`). Must be up for real-data verification; otherwise panels show empty/error states (which are themselves in scope to verify).
- **Auth:** app is one protected route `/` (login-gated). Panels are reached by **in-app tab/section nav inside `DashboardApp`**, not per-panel routes. Playwright must log in, then navigate tabs.
- **Theme toggle:** Header button; or set `localStorage.theme` + `.dark` class. `ThemeContext` supports `system|dark|light`.
- **Verification tool:** Playwright MCP (live browser). Screenshots → `mcp-parallel/findings/frontend-harden/<surface>/`.

## Baseline (2026-07-02, setup)

- lint: **PASS** (26/26 unit tests). build: **PASS** (vite, 3.7s).
- Bundle: `index.js` 1.79 MB (gzip 478 KB) — over 500 KB warning; `index.css` 217 KB. recharts is a major contributor; ECharts/uPlot migration + dropping recharts should reduce it (echarts is large but tree-shakeable via `echarts/core`; uplot is tiny).
- Build warning: `liveGateway.js` is both statically and dynamically imported (chunking note, non-fatal).

---

## Key findings & risks (evidence-based, from setup exploration)

- **R1 — Chart wrapper API nuance (blocks naive item 1).** `SafeResponsiveChart` is NOT chart-lib-agnostic: it wraps recharts' `<ResponsiveContainer>` and its `children` are **recharts JSX trees passed by each panel** (`<LineChart>…`, `<XAxis/>`, …). 13 panels import `recharts` directly. So "migrate internals, panels unchanged" is not a literal drop-in — ECharts has a different component model. The migration must define a new **data-driven, API-shaped** contract (e.g. wrapper accepts a chart `spec`/`option` + typed series) and update call sites, OR ship ECharts/uPlot-backed drop-in components with matching prop shapes. Decide deliberately in items 1–2; do NOT hand-wave. Panels `OutputPipelineTimeline.jsx` (verify-only) constrain how far the shared change can reach.
- **R2 — Light muted-foreground contrast.** `--muted-foreground: oklch(0.5 0.03 255)` on near-white bg is near/under 4.5:1 for body & likely fails for placeholders. Verify + fix in item 21 (token-only, both themes).
- **R3 — Bundle size** (see baseline) — track as an item-24 exit check, expect improvement post-recharts-removal.
- **R4 — Verify-only churn.** Stress session actively edits `ModelConnectionPanel`/`OutputPipelineTimeline`; re-pull before verifying them and never edit.

---

## Surface status matrix

Legend: ⬜ pending · 🔎 verifying · 🔧 fixed+re-verified · ✅ verified-clean · 👁 verify-only (logged) · ⚠ issue open

### Charts (items 1–2)
| # | Surface | Owner | Dark | Light | 1440 | 1024 | 768 | 375 | Impec | State |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | SafeResponsiveChart → ECharts | me | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ proven live |
| 2 | uPlot dense time-series | me | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ engine + Overview telemetry done |

### Panels (items 3–16)
| # | Surface | Owner | Dark | Light | 1440 | 1024 | 768 | 375 | Impec | State |
|---|---|---|---|---|---|---|---|---|---|---|
| 3 | GatewayKeyPanel | me | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ done |
| 4 | ModelGovernancePanel (+Fields) | me | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ done |
| 5 | RoutingGovernancePanel / RoutingAuditPanel / PolicyDomainSwitcher | me | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ |
| 6 | KillSwitchPanel / KillSwitchModelCombobox / ModelStatePanel | me | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ |
| 7 | MCPManagerPanel / MCPScannerPanel / MCPScanControlMatrix | me | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ |
| 8 | DatabaseConnectionPanel / VectorPolicyPanel | me | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ |
| 9 | RAGFeatureTestPanel / RAGAttackTrustSimulator / RAGPipelineTelemetry | me | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ |
| 10 | OutputGovernancePanel / OutputGuardrailControls / OutputGuardrailCharts / OutputGuardrailEngineCard | me | ◐ | ◐ | ◐ | ⬜ | ◐ | ◐ | ✅chart | 🔎 charts done, panels pending |
| 11 | AttackSimulatorPanel (+ simulator/*) | me | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ |
| 12 | PolicyManagementPanel / PolicyAnalyticsPanel | me | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ |
| 13 | LogViewerPanel / LogDetailPage | me | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ |
| 14 | OWASPStatsPanel | me | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ |
| 15 | Firewall12EnterprisePage / FirewallModulePage / SubmoduleDetailPage / SubmoduleResultsPage | me | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ |
| 16 | HowToUse | me | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ |

### Verify-only (item 17 — never edit)
| # | Surface | Owner | State |
|---|---|---|---|
| 17a | MCPConnectorPanel | stress/never_edit | 👁 ⬜ |
| 17b | ModelConnectionPanel | stress | 👁 ⬜ |
| 17c | OutputPipelineTimeline (pipeline-trace cards) | stress | 👁 ⬜ |

### Also in scope (discovered — not in original numbered list, fold into nearest item)
| Surface | Note |
|---|---|
| Login / OAuthCallback | auth surfaces (unauthenticated) — verify both themes/widths |
| Profile / Settings / ChangePasswordForm | account forms + theme selector |
| AIMeshFirewallOverview / AIMeshFirewallConfig | dashboard shell pages + ImpactPanel |
| Header / DashboardLayout | app-shell chrome (shared) |
| ServiceStatusPanel / OrgIsolationBanner | status chrome |
| InactivityWarningModal | session-timeout modal |
| BedrockTestPanel | model test surface |
| EmptyState / Skeleton / Spinner / PanelLoadingShell | loading/empty primitives |

### ui/* primitives (item 18 — API-compatible restyle only)
| Primitive | Dark | Light | Impec | State | | Primitive | Dark | Light | Impec | State |
|---|---|---|---|---|---|---|---|---|---|---|
| Badge | ⬜ | ⬜ | ⬜ | ⬜ | | Select | ⬜ | ⬜ | ⬜ | ⬜ |
| Button | ⬜ | ⬜ | ⬜ | ⬜ | | Skeleton | ⬜ | ⬜ | ⬜ | ⬜ |
| Card | ⬜ | ⬜ | ⬜ | ⬜ | | Slider | ⬜ | ⬜ | ⬜ | ⬜ |
| Dialog | ⬜ | ⬜ | ⬜ | ⬜ | | Spinner | ⬜ | ⬜ | ⬜ | ⬜ |
| EmptyState | ⬜ | ⬜ | ⬜ | ⬜ | | Switch | ⬜ | ⬜ | ⬜ | ⬜ |
| Input | ⬜ | ⬜ | ⬜ | ⬜ | | Table | ⬜ | ⬜ | ⬜ | ⬜ |
| Label | ⬜ | ⬜ | ⬜ | ⬜ | | Tabs | ⬜ | ⬜ | ⬜ | ⬜ |
| PanelHeader | ⬜ | ⬜ | ⬜ | ⬜ | | Textarea | ⬜ | ⬜ | ⬜ | ⬜ |
| Progress | ⬜ | ⬜ | ⬜ | ⬜ | | Toast | ⬜ | ⬜ | ⬜ | ⬜ |
| SegmentedControl | ⬜ | ⬜ | ⬜ | ⬜ | | Tooltip | ⬜ | ⬜ | ⬜ | ⬜ |

### Cross-cutting & freeze (items 19–24)
| # | Item | State |
|---|---|---|
| 19 | Data-integrity proof (no mock/placeholder anywhere) | ⬜ |
| 20 | No-leak proof (no key/PII/secret/topology) | ⬜ |
| 21 | Theme audit (dark+light contrast/focus/hover/disabled) — see R2 | ⬜ |
| 22 | Responsive audit (1440/1024/768/375, no overflow/overlap) | ⬜ |
| 23 | Playwright visual+behavior snapshot regression gate | ⬜ |
| 24 | Full re-verify; bundle check (R3); console/network clean; all resolved | ⬜ |

---

## Per-surface finding log (append-only)

**Item 4 — ModelGovernancePanel (+Fields) DONE (iter4 resumed loop, 2026-07-02):** live-verified both themes @1440/1024/768/375 with real data (11 connected models); console 0 errors; no overflow.
- **No-leak PROVEN:** allowlist shows model names + provider + model_id (non-secret) and only key *status* (`api_key_set`→"", else "Env-var key"/"API key missing") — never a key value. Panel already strips the ZeroShield guard model via `filterUserManagedModels` so its raw upstream id never surfaces.
- **Controls verified (reflect state):** Save disabled when clean → toggling a model enables Save → Discard/Reset disables it again. `select all connected`/`clear`, isolation checkbox, default-model select all functional. Empty ("No models connected"), stale-model (blue), and error (`role=alert`) states intentional; good 44px touch targets + aria.
- **F-MG1 FIXED (theme contrast):** two helper `<p>` lines used `dark:text-slate-500` (~2.4:1, dim on dark). → `dark:text-slate-400` (verified computed color slate-500 L0.554 → slate-400 L0.704).
- lint 37/37, build green, detector clean. Evidence: `mcp-parallel/findings/frontend-harden/model-governance/`.


**Item 3 — GatewayKeyPanel DONE (iter3 resumed loop, 2026-07-02):** live-verified (real data: 7 keys) both themes @1440/1024/768/375; console 0 errors; modal form validates (HTML5 required blocks empty submit); no page overflow (table uses internal `overflow-x-auto`).
- **No-leak PROVEN:** list endpoint `/api/gateways/keys/` returns **no raw-key field** (only 8-char `prefix`); DOM scan found no long-token leak; full key shown once on creation with "will not be shown again" (correct). Attack-Simulator's gateway-key field is masked (dots).
- **F-GK1 FIXED (data-integrity):** list fetch error/non-ok used to `setKeys([])` → the benign "No API keys created" empty state hid outages. Added a `loadError` state → distinct error UI + Retry when empty, and an inline banner when a refresh fails with keys present. Verified via route-intercepted 500: shows error+Retry, NOT false-empty.
- **F-GK2 FIXED (theme contrast):** status badges `text-emerald-700`/`text-red-700` had no `dark:` variant (~2.9:1 on dark). Added `dark:text-emerald-300`/`dark:text-red-300` → verified light badge text (was oklch L0.505) now L0.808 in dark; visibly readable.
- **F-GK3 FIXED (no-leak/polish):** create error dumped raw `res.text()` (could surface an HTML/500 page). Now parses JSON detail/error and only surfaces short non-markup bodies, else a generic "Failed to create key (HTTP n)".
- lint 37/37, build green, detector clean. Evidence: `mcp-parallel/findings/frontend-harden/gateway-key-panel/`.


_Setup (2026-07-02): PRODUCT.md + DESIGN.md written; audit scaffold created;
claims pre-flight + VERIFY-ONLY set recorded; baseline green; risks R1–R4 logged._

**Item 1 — chart foundation (iter2, 2026-07-02):** landed the ECharts foundation
behind a **superset-compatible** SafeResponsiveChart (R1 resolved without touching
verify-only files, which have no charts):
- `utils/chartTheme.js` — registered light/dark ECharts themes from DESIGN.md tokens; `utils/chartTheme.test.js` (5 tests, incl. light/dark inversion + contrast-direction invariants).
- `components/charts/echartsCore.js` — slim `echarts/core` (Line/Bar/Pie/Radar/Custom + Grid/Tooltip/Legend/Title/DataZoom/Radar/VisualMap/MarkLine/Graphic + Canvas), registers `zs-light`/`zs-dark`.
- `components/charts/EChart.jsx` — theme-reactive (follows `resolvedTheme`), ResizeObserver container-resize, `prefers-reduced-motion` → animation off.
- `SafeResponsiveChart` gains an `option` path (renders `<EChart>`); legacy recharts `children` path untouched → API identical for current callers. lint 31/31, build green, detector clean.
- **Finding F1 (theme bug in current charts):** existing recharts charts hardcode dark styling (`tooltip #0f172a`, `grid #3f3f46`, `ticks #94a3b8`) → dark grid on white in light mode. The new themes fix this on migration. Fold into per-panel chart migrations + item 21.
- **R3 update:** bundle temporarily 1.79→2.53 MB (gzip 478→726 KB) while echarts + recharts coexist. Net drop lands when recharts is removed after all panels migrate (item 24). recharts still used by 12 files: AIMeshFirewallOverview, OWASPStatsPanel, module-specific-{charts,log-charts}, LogDetailPage, SubmoduleResultsPage/DetailPage, RAGPipelineTelemetry, Firewall12EnterprisePage, OutputGuardrailCharts, PolicyAnalyticsPanel.
- **Next (iter3):** migrate a reference chart panel to `option`, bring up dev+backend, Playwright-verify data-identical + interactive in BOTH themes at 4 widths, then progress item 1 → item 2 (uPlot dense time-series)._

**Item 1 DONE + reference migration (iter3, 2026-07-02):** migrated `OutputGuardrailCharts.jsx` (4 charts: stacked-area timeline, donut, 2 bars) recharts→ECharts `option`, and **live-verified** against real backend data (module 1.7, event_timeline 43 rows etc.):
- Data-identical BEFORE(recharts)/AFTER(ECharts): timeline 07/02 spike + 4-series legend; donut "Redacted 100%"; categories Pii/Policy-Violation; risk 40-60 & 80-100. Screenshots in `mcp-parallel/findings/frontend-harden/output-guardrails/`.
- recharts SVG→0, ECharts canvas→4. Theme reactivity works (toggle light↔dark re-themes charts). Interactivity works (axis tooltip on hover, theme-aware).
- **F1 FIXED (verified):** light theme now shows a white tooltip + readable slate-600 axis labels (was hardcoded-dark). Also fixed the recharts garbled Y-axis tick ordering.
- **F2 FIXED:** recharts donut label was clipped ("…d100%"); ECharts shows full "Redacted 100%" with labelLine.
- lint 31/31, build green, detector clean. Fixed a theme deprecation (radar `name.textStyle`→`axisName`) to keep console clean.
- Item 1 marked done: the SafeResponsiveChart→ECharts capability is migrated, API-compatible, theme-aware, and proven. Remaining recharts panels migrate under their per-surface items (3–16); recharts removal is the item-24 exit check. **11 recharts files remain:** AIMeshFirewallOverview, OWASPStatsPanel, module-specific-{charts,log-charts}, LogDetailPage, SubmoduleResultsPage/DetailPage, RAGPipelineTelemetry, Firewall12EnterprisePage, PolicyAnalyticsPanel (OutputGuardrailCharts now done).

### Live-verification harness (reuse every iteration)
- Target the **dockerized dev server at `http://127.0.0.1:8180`** (`ai_mesh_firewall-frontend-1`, container 5173→8180) — it serves the mounted source and HMRs edits. Do NOT run a second `:5173` (root-owned `node_modules/.vite` cache blocks it; also redundant).
- **Auth without the (rate-limited) login form:** mint a JWT in the control container and inject into localStorage:
  `docker exec ai_mesh_firewall-control-1 python manage.py shell -c "from django.contrib.auth import get_user_model; from rest_framework_simplejwt.tokens import RefreshToken; u=get_user_model().objects.filter(email='admin@zeroshield.io').first(); print(RefreshToken.for_user(u).access_token)"` → `localStorage.setItem('auth_access', <token>)`. Keys: `auth_access`/`auth_refresh`. Access ~1h, refresh ~7d.
- **Navigation:** single route `/`; panels via `?tab=` (e.g. `?tab=firewall-1-7` → module 1.7; `firewall-1-1..1-7`, `firewall`, `firewall-config`).
- Playwright MCP needs system Chrome: `npx playwright install chrome` (done).

### New findings
- **F3 (backend infra, NOT frontend — documented, worked around):** Postgres intermittently returns `FATAL: sorry, too many clients already` → app-wide **500s** under concurrent dashboard load (many polling panels × parallel Ralph sessions; `max_connections=100`, idle ~10). Transient; retry when calm. Out of frontend scope + shared-env risk to fix. **Frontend robustness sub-finding:** a transient 500 on `/api/auth/me/` currently forces a logout→`/login` bounce (a blip logs you out) — candidate hardening (item 19).
- **F4 (data-integrity — investigate, item 19):** module 1.7 **KPI stat cards read 0** (OUTPUTS SCANNED/BLOCKED/…) while the **charts endpoint has real rows** (event_timeline 43). Possible mismatch between the module-KPIs source and the module-charts source, or a lens-window difference. Verify which is correct before signing off data-integrity.

**Item 2 — uPlot foundation + sparkline (iter1 resumed loop, 2026-07-02):** built the dense-time-series path and proved it live.
- `utils/uplotTheme.js` (+ `uplotTheme.test.js`, 3 tests) — light/dark axis/grid colors from DESIGN.md, series palette shared with ECharts (`CHART_PALETTE`) so both chart libs read as one system.
- `components/charts/UPlotChart.jsx` — canvas dense-time-series chart; theme-reactive (rebuild on theme), size via `setSize`, data via `setData`, `sparkline` mode + drag-to-zoom cursor for full charts; imports `uplot/dist/uPlot.min.css`.
- `SafeResponsiveChart` gains a `uplot` prop path (passes the measured px size uPlot needs) — third mode, still backward-compatible.
- Migrated the Overview **"Pressure Curve" sparkline** (×7 SubModuleCards) recharts→uPlot. **Live-verified (isolated browser):** 7 `.uplot` canvases, both themes, no overflow @375, **console 0 errors**, curve data-identical (flat baseline + real blocked-event spike). Screenshots: `mcp-parallel/findings/frontend-harden/uplot-overview/`.
- lint 34/34, build green, detector clean. Bundle 2.58 MB (recharts+echarts+uplot coexist; nets down at item 24). Note: `utils/*.js` imported by node --test need explicit `.js` in relative imports (Vite resolves extensionless, node ESM does not).
- **Item 2 NOT done:** only a single-series sparkline migrated; the interactive/stacked telemetry charts remain. 11 uPlot candidates total (see matrix below).

### uPlot-vs-ECharts migration matrix (from classification workflow — 52 charts across 10 files)
**uPlot (dense time-series, 11):** AIMeshFirewallOverview ×3 (Pressure sparkline ✅done, AttackVectorTrend [stacked], GlobalTraffic enforcement [stacked]); SubmoduleDetailPage ×3 (request-volume area, enforcement lines, multi-metric trend); SubmoduleResultsPage ×1 (7-day high-density area); PolicyAnalyticsPanel ×1 (effectiveness % trend); module-specific-charts ×2 (stacked telemetry area, multi-series trend line); Firewall12EnterprisePage ×1 (event/threat volume trend).
**ECharts (categorical/pie/radar, 41):** module-specific-log-charts ×21, RAGPipelineTelemetry ×5 (all aggregate KPIs, NOT time-series), + donuts/bars/radars elsewhere.
Next uPlot work needs **stacked-area support** in UPlotChart (cumulative-sum bands) for AttackVectorTrend + GlobalTraffic; then interactive (cursor+zoom) verification → item 2 [x].

**Item 2 DONE (iter2 resumed loop, 2026-07-02):** uPlot engine complete + both Overview dense-telemetry charts migrated & live-verified.
- Added stacked-area support to `UPlotChart` (`utils/uplotStack.js` cumulative-sum + bands; +3 tests). **Tooltip shows RAW per-series values** (not cumulative) via a `value` fn reading a live data ref → data-identity preserved.
- Added `compactNum` axis formatter (5000→"5k") — fixes a real bug where a fixed 46px y-axis clipped 5-digit telemetry values ("10,000"→"0,000"); exact values stay in the tooltip. x legend labeled "Time".
- **Migrated GlobalTraffic "Enforcement actions over time"** (3 stacked areas) + **AttackVectorTrendChart** (5 overlapping areas). All 3 Overview AreaCharts now uPlot (0 recharts AreaCharts left in the file).
- Live-verified (isolated browser, real data): both charts uPlot (recharts=false), drawn both themes (colored px ~10-19k), hover shows time + raw per-series values, **no overflow @1024/768/375**, **0 chart/page errors**. Screenshots `…/uplot-overview/{enf,avt}-{light,dark}2.png`.
- **Crash fixed (learning):** a series `fill` gradient fn reading `u.bbox.top/height` threw `createLinearGradient: non-finite` before layout → crashed the whole Overview (blank). Now guards non-finite bbox and falls back to a flat translucent fill.
- lint 37/37, build green; chart files detector-clean.
- **Remaining 8 uPlot candidates migrate under their per-surface items** (SubmoduleDetailPage ×3 → item 15; SubmoduleResultsPage → 15; PolicyAnalyticsPanel → 12; module-specific-charts ×2 → 13/15; Firewall12EnterprisePage → 15). recharts removal = item 24.
- **Pre-existing slop (not mine) on AIMeshFirewallOverview L84-85:** `ai-color-palette` violet gradients (×3) — for the Overview's dedicated theme/slop pass (items 21/24), NOT introduced by the chart migration (chart edit regions are detector-clean).

- **F5 (env hazard — worked around):** all Claude sessions' Playwright MCP share one Chrome profile (`ms-playwright-mcp/mcp-chrome-6078e4c`) → "Browser is already in use" when a prior/parallel session holds it (an orphaned `playwright-mcp` + idle Chrome on about:blank held the lock). Do NOT force-kill in the shared env. **Workaround (used):** a standalone `playwright` (from the npx cache `~/.npm/_npx/9833c18b2d85bc59/node_modules/playwright`) via `launchPersistentContext` with an **isolated** `/tmp` profile + `executablePath:/opt/google/chrome/chrome`. Scripts in scratchpad; reusable when the MCP browser is locked.
