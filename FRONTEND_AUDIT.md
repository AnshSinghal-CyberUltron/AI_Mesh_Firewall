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
| 1 | SafeResponsiveChart → ECharts | me | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ |
| 2 | uPlot dense time-series | me | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ |

### Panels (items 3–16)
| # | Surface | Owner | Dark | Light | 1440 | 1024 | 768 | 375 | Impec | State |
|---|---|---|---|---|---|---|---|---|---|---|
| 3 | GatewayKeyPanel | me | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ |
| 4 | ModelGovernancePanel (+Fields) | me | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ |
| 5 | RoutingGovernancePanel / RoutingAuditPanel / PolicyDomainSwitcher | me | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ |
| 6 | KillSwitchPanel / KillSwitchModelCombobox / ModelStatePanel | me | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ |
| 7 | MCPManagerPanel / MCPScannerPanel / MCPScanControlMatrix | me | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ |
| 8 | DatabaseConnectionPanel / VectorPolicyPanel | me | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ |
| 9 | RAGFeatureTestPanel / RAGAttackTrustSimulator / RAGPipelineTelemetry | me | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ |
| 10 | OutputGovernancePanel / OutputGuardrailControls / OutputGuardrailCharts / OutputGuardrailEngineCard | me | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ |
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

_Setup (2026-07-02): PRODUCT.md + DESIGN.md written; audit scaffold created;
claims pre-flight + VERIFY-ONLY set recorded; baseline green; risks R1–R4 logged._
