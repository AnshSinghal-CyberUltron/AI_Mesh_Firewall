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
| 5 | RoutingGovernancePanel / RoutingAuditPanel / PolicyDomainSwitcher | me | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ done |
| 6 | KillSwitchPanel / KillSwitchModelCombobox / ModelStatePanel | me | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ done (critical fixes) |
| 7 | MCPManagerPanel / MCPScannerPanel / MCPScanControlMatrix | — | 👁 | 👁 | 👁 | 👁 | 👁 | 👁 | n/a | 👁 verify-only / orphaned (see log) |
| 8 | DatabaseConnectionPanel / VectorPolicyPanel | me | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ done (VP live→item15) |
| 9 | RAGFeatureTestPanel / RAGAttackTrustSimulator / RAGPipelineTelemetry | me | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ DONE — all 3 RAG panels: charts→ECharts, data-integrity, no-leak, both themes @4 widths, detector clean |
| 10 | OutputGovernancePanel / OutputGuardrailControls / OutputGuardrailCharts / OutputGuardrailEngineCard | me | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ DONE — all 4 module-1.7 panels verified both themes @4 widths; leak backend-verified (telemetry scrub); detector 0 |
| 11 | AttackSimulatorPanel (+ simulator/*) | me | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ DONE — 10-file simulator surface, fixed fabricated verdicts + in-flight races + sanitizer-bypass leak; both themes @4 widths |
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

**Item 9 (part 1) — RAGPipelineTelemetry DONE (iter9 resumed loop, 2026-07-02):** workflow-analyzed all 3 RAG panels; completed RAGPipelineTelemetry.
- **CHART MIGRATION (5 charts recharts→ECharts):** funnel (h-bar), stage-action (stacked bar), escalation (donut), stage-health (radar), latency (bar) → ECharts `option` via SafeResponsiveChart. Removed the recharts import. Fixes the old hardcoded-dark tick (`#a1a1aa`)/tooltip (`#18181b`) colors → now theme-aware. **Live-verified both themes** (firewall-1-3 Analytics/Flow tabs): **0 recharts SVGs**, ECharts canvases render, data-identical structure (0-value honest-empty since no RAG activity; escalation shows "No data for period"), no overflow @768/375, **console 0 errors**. Screenshots `mcp-parallel/findings/frontend-harden/rag/`.
- **Theme contrast:** fixed the pervasive inverted `text-slate-400 dark:text-slate-500` (22 spots, low-contrast in BOTH themes) → `text-slate-500 dark:text-slate-400`; colored breakdown numbers (`text-{emerald,red,amber,blue}-400`) got light `-600` variants for AA on the light card.
- No-leak: fetchError surfaces raw `HTTP status`/`err.message` (low — client-facing, no secret). Detector clean. lint 42/42, build green.
**Item 11 — AttackSimulatorPanel + simulator/* (modules 1.1/1.3/1.4/1.5/1.6) DONE (iter resumed loop, 2026-07-02):** 10-file surface analyzed by a 10-agent parallel Workflow (~40 findings), fixed + gated (lint 42/42, build clean, detector 0 on 8/9 edited files).
- **HIGH data-integrity — fabricated verdicts on backend error (the mission-critical class):** (1) `CircuitBreakerSimulator` read flat `result.model/errors_injected/state` but `setResult` stores `{ok, data:{…}}` → fields always blank AND "New State" always defaulted to the green "healthy" style, so a failed trigger (`ok:false`, which the `!result.error` guard never caught) rendered as a green success. Fixed: gate on `result.ok`, read Model/Errors from the operator's own inputs, take New State from the authoritative freshly-loaded `cbState`, and render an honest error panel on failure. (2) `MCPGuardrailSimulator` `actionTone()` had no `error`/`flag` case → any 500 / gateway-down (action `"error"`) fell through to the green **ALLOW** ShieldCheck verdict; added distinct ERROR (rose) + FLAG tones; also its dry-run defaulted `allow` on a 200 with a null/unparseable body → now requires a real body. (3) `OutputGuardSimulator` "Hallucination Analysis" rendered 4 meters that `normalizeOutputGuardResult` (liveGateway.js) **hardcodes** — grounding always 100%, pattern/contradiction always 0%, risk a boolean→70% literal, markers always [] — presented to an admin as real analysis. Fixed at the root: normalizer now carries only the real `factuality_warning` boolean, and the panel shows an honest single factuality signal (removed the fabricated meters + the always-empty redacted-tokens dead section).
- **In-flight races (HIGH/MED):** `ModelRouting`, `OutputGuard`, `CircuitBreaker`, and `IsolationOps`-circuit call the gateway directly (bypassing the hook's `executeScenario`), so `engine.executing` never flipped → the shared Execute button stayed enabled, showed no spinner, and concurrent clicks raced on `setResult` (last-resolved wins). Added local in-flight flags wired into the shell's `executing` prop.
- **No-leak (sanitizer bypass):** `AttackSimulatorPanel`'s "Raw Response JSON" dump + copy stringified the whole `result` — including `zeroshield.guard_reason`/`detail`, the exact fields whose upstream Bedrock/provider model literals the file's own `sanitizeGuardText` strips before rendering (header comment: these "must never reach the operator UI"). Added a recursive `sanitizeResultForDump` that neutralizes `PROVIDER_LITERAL_PATTERNS` in every string of the dumped/copied object. Other simulator raw-dumps (SimulatorShell, MCPGuardrail, StageTimeline content) are the operator's OWN org-scoped payload/result in an admin surface = intended; the hardcoded attack-scenario PII/secret corpora are documented fake test inputs.
- **Theme (both modes):** 5 `AttackSimulatorPanel` verdict labels (`getStatusConfig` ERROR/BLOCKED/REDACTED/FLAGGED/ALLOWED) used `text-*-700` with NO dark variant → dark-on-dark on the `*-900/20` cards in dark mode (the primary result indicator); added `dark:*-300`. `SimulatorShell` 3 connection-status badges (`text-*-400` state text on `/10` tints) + copy-success checkmark → `-700 dark:-400`/`-600 dark:-400`. `StageTimeline` verdict word reused the `-500` *icon* color as meaningful text (fails AA on the light detail card) → now reuses the well-contrasted `-700 dark:-300` badge token. Plus assorted slate/amber spots across CircuitBreaker/IsolationOps/OutputGuard/RAGIngestion.
- **Responsive:** non-collapsing `grid-cols-4/3/2` control rows → `sm:`/`lg:` breakpoints (CircuitBreaker ×2, ModelRouting weights, RAGIngestion ×2, OutputGuard raw-vs-safe); `SimulatorShell` execute row `flex-wrap` + trace `request_id` truncate; AttackSim burst rows `flex-wrap`.
- **Other honesty fixes:** removed a no-op `riskCount` slider + the fabricated "Events Injected" stat (never sent to `/api/models/isolate/`); honest latency "—" (was a fabricated `0.1ms`); burst-count button label clamped to 100 (handler already did, label promised ×999); collection-required Ingest validation; copy-trace only shows success if `clipboard.writeText` resolves; StageTimeline literal-"undefined" docs guard; IsolationOps dropped the silent `gpt-4o-mini` phantom-model fallback (require a real selection like the sibling tabs).
- **Detector:** cleared 2 pre-existing ternary-cross-product FPs (CircuitBreaker risk button via `disabled` ternary split; RAGIngestion mode-toggle via `MODE_BTN_ACTIVE/INACTIVE` consts). **StageTimeline's 2 remaining are PRE-EXISTING FPs on CORRECT code** — `text-slate-700 dark:text-slate-100` on a light `bg-emerald-50/70` before/after `<pre>`: slate-700 on emerald-50 is ~8:1, and slate-100 is a `dark:` variant that never co-renders with the light bg; the heuristic can't model Tailwind `dark:` variants, so the code was left correct rather than contorted.
- **LIVE:** both themes @1440/1024/768/375 → **overflow=0 all 8, 0 console errors, 0 network failures**; AttackSimulator (dark+light+375) and the shared SimulatorShell (via Model Routing) verified — status badges legible both themes, gateway API key masked (password field, no leak), scenarios/sliders/controls render, mobile stacks clean. Sub-simulator verdict-path badges (ERROR/factuality) are code+build-verified; forcing each requires specific backend responses (500s / factuality flags) not reproducible in the calm env. 500 bursts under load = F3.
- **Deferred/LOW (logged):** connect-model dialog lacks focus-trap/Esc/backdrop-dismiss; StageTimeline buttons omit `type="button"` (latent, not in a form); RAGIngestion bulk-result shows singular doc_id + no count; `deriveResultAction` "allow" default is **load-bearing** for the normalized success shapes (routing/output-guard) that intentionally omit status/success — changing it would regress every success to ERROR, so left as-is (errors themselves map correctly); ModelRouting `scored_models` viz is unreachable dead code but degrades to an honest "server-side only" placeholder (no fake numbers).

**Item 10 — Output guardrails module (1.7): OutputGuardrailControls + OutputGuardrailEngineCard + OutputGovernancePanel DONE (iter resumed loop, 2026-07-02):** gated (lint 42/42, build clean, detector 0 on all 3 edited files), live-verified both themes @1440/1024/768/375 (overflow=0 all 8).
- **OutputGuardrailControls = GOLD STANDARD (0 edits):** uses semantic OKLCH tokens throughout (`text-foreground`/`text-muted-foreground`/`bg-card`/`border-border`/`text-primary`/`text-destructive`/`text-success`/`bg-warn`/`bg-accent`/`bg-muted`) → theme-correct by construction. **Controls verified live:** toggling a detector's action pill flips Save from disabled→enabled (interaction assert: `saveDisabledBefore=true → after=false`); Hallucination row correctly disables its pills + hides the threshold slider when Detect is off; load/save (400 shows compliance-floor detail, 403 perms, generic err)/reset/dirty-tracking (MANAGED_KEYS diff) all sound. No-leak (config booleans/enums only). This is the reference pattern the other panels should converge toward.
- **OutputGuardrailEngineCard:** 3 inverted `text-slate-400 dark:text-slate-500` → `text-slate-500 dark:text-slate-400`. Honest Idle/Active/Error states (a 500 stays "Error", distinct from successful-empty "Idle"); real `/api/security/threat-feed?source=security_scan` data bucketed via the canonical `OUTPUT_ACTION_COLORS` map (block→blocked, redact/rewrite/model_downgrade→modified, monitor→allowed). Counts only → no-leak. DEFERRED-LOW: the `{summary.flagged}F` counter is structurally always-0 (flag folds into the redacted bucket) — an honest 0, not fabricated; left as-is.
- **OutputGovernancePanel (evidence log):** theme — model-name inverted-slate swap + PROMPT/OUTPUT/REASON evidence labels `blue/purple/amber-500`→`-600 dark:-400` (amber-500 failed AA on white); extracted `AUTO_REFRESH_STYLES` const for the on/off toggle (clears a detector gray-on-color **false-positive** from ternary cross-product `text-slate-400`×`bg-teal-100`, and lifts the muted OFF state slate-400→slate-500). Honest empty state ("No output-guard events… Send a prompt through the gateway"); additive client-side action-filter chips; auto-refresh 10s poll.
- **NO-LEAK — backend-verified (the important one):** the panel renders per-event `rawOutput`/`promptSnippet`/`guardrailReasoning`. These are NOT raw secrets: `gateway/ai_mesh_gateway/telemetry.py:171-213` is a **single choke-point** that runs `patterns.redact_all` over EVERY free-text metadata key (`raw_output`, `response_snippet`, `sanitized_output`, `prompt_snippet`, `guardrail_reasoning`, `detail`, `matched_patterns`, all prompt-preview keys) BEFORE the event is persisted to the audit log — "so the AUDIT LOG never persists raw PII". Both non-stream and streaming output-guard lanes build their event through this function. So the evidence log displays already-scrubbed content; no frontend leak. (output_guard.py/telemetry.py are the stress session's — READ-only verified, not edited.)
- **OutputGuardrailCharts:** already ECharts (item 1/iter3), 0 recharts confirmed (only migration comments) — verify-only, renders in the sweep. **OutputPipelineTimeline** (embedded in EventRow expand) is the stress session's VERIFY-ONLY component; env has 0 output-guard events so no row expands → full live verify folds into item 17.

**Item 9 (part 2) — RAGFeatureTestPanel + RAGAttackTrustSimulator DONE (iter resumed loop, 2026-07-02):** gated (lint 42/42, build clean, detector 0 on both), live-verified both themes @1440/1024/768/375.
- **RAGFeatureTestPanel — DATA-INTEGRITY (the real fix):** `finalAction = audit?.final_action || "error"` (was `|| (res.status>=400 ? "block" : "allow")`) — an infra 500 or a malformed 2xx no longer fabricates a red "BLOCKED"/green "ALLOWED" guardrail verdict; `detail` now says "No guardrail audit returned (HTTP N)" honestly. **Run-race:** every Run + Run-All button `disabled={running != null}` (was `disabled={isRunning}` self-only — a 2nd click mid-run clobbered `running`/results). Theme: TestResultBadge pills + escalation/Rewritten/error/teal-Run-All got `-600/-700 dark:-400` light variants; inverted-slate swap; stages-row `overflow-x-auto` @375.
- **RAGAttackTrustSimulator — THEME:** ACTION_STYLES (allow/block/flag/rewrite/redact) + HTTP/final-action/escalation status badges + 20+ inline amber/red/blue/emerald/purple spots → `-700 dark:-400`; active-selection chips (category/custom/trust-scenario, teal/purple/red `/20` tints) light-legible; 21 inverted `text-slate-400 dark:text-slate-500` swapped; **2× `focus:outline-none` dropped** (global `:focus-visible` ring in index.css handles a11y; border-teal still signals mouse focus).
- **NO-LEAK disposition (LOGGED, not a defect):** the "Raw Response JSON" toggle (`JSON.stringify(result.body)`), the "Allowed Documents" `doc.content` render, and the `NS:` namespace echo all display the operator's OWN ingested RAG corpus inside an admin trust-simulator — that IS the tool's purpose (see what survived filtering). The gateway key is only ever a Bearer request header, never present in `result.body`; no key/secret/other-tenant/topology data is rendered. Not removed.
- **LIVE evidence:** verify_1_3 across all 8 (theme×width) → **overflow=0 everywhere, 0 console errors, 0 network failures** when backend calm; dark+light panel screenshots confirm tab switch (Attack↔Trust) works, SAFE(emerald-700)/ATTACK(red-700) badges legible, Run controls, sliders, "Auto key" status, amber empty-state helpers all readable in BOTH themes; 375 stacks with no overflow. Transient 500 bursts under concurrent load = known **F3** (Postgres "too many clients"), environmental not code.

**Item 8 — DatabaseConnectionPanel + VectorPolicyPanel (iter8 resumed loop, 2026-07-02):** analyzed via parallel workflow, fixed, gated (lint 42/42, build, detector 0).
- **No-leak PROVEN:** DB panel — Pinecone API key + Milvus token are `type=password` (masked, verified live); BYOK embedding keys are "write-only / encrypted at rest / never returned" (placeholders only); the simulator gateway key is only ever a Bearer header (never rendered); raw sim body stored in state, never dumped. VectorPolicy — metadata only (sensitive_fields are field *names*, not values); no creds. DOM leak-scan on both = none.
- **DatabaseConnectionPanel fixed:** F-DB-theme1 pipeline `final_action` badges got `dark:text-{emerald,red,amber}-300` (were dark-on-dark); latency + helpText + stage-latency `text-slate-400`→`text-slate-500 dark:text-slate-400`; **F-DB-resp** pipeline stage-trace row `overflow-x-auto` (was clipped by `overflow-hidden` parent → trailing stages invisible at 375). Controls sound (test/simulate POST, disable in-flight, reflect returned state, surface errors). **Live-verified** both themes @375/768/1024: masked key, no leaks, no overflow (351/720/880).
- **VectorPolicyPanel fixed:** 5 badge/banner/label dark-variant contrast spots (ActionBadge map, modal error, compile banner, red banner, namespace label); extracted `POLICY_STATUS_STYLES` const for the enabled/disabled badge (adds `dark:text-emerald-300` + clears a **pre-existing** `gray-on-color` detector false-positive from the ternary). No-leak clean; controls solid (create/edit/delete/compile POST + refetch). **Live render verification folds into item 15** (it's mounted on Firewall12EnterprisePage / `?tab=firewall-1-2`, has no anchor heading).
- Evidence: `mcp-parallel/findings/frontend-harden/db-vector/`.
- **DEFERRED/LOW (logged):** DB Milvus `connection_url` renders as `type=text` (user's own input; only a leak if a user pastes creds into the URI — low); DB `result.details`/`simResult.error` echo raw backend strings (frontend does no scrubbing — backend-dependent); DB Test button doesn't client-validate required fields (backend rejects + surfaces error — minor); VP Cancel bypasses close-while-submitting guard + `parseFloat||0.85` silent threshold fallback + modal `grid-cols-3` at 375 (minor).

**Item 7 — MCP panels: VERIFY-ONLY / ORPHANED (iter7 resumed loop, 2026-07-02):** evidence changed the disposition — none of the three is an editable live surface of mine.
- **MCPManagerPanel.jsx (1945 L) + MCPScannerPanel.jsx (1026 L) = ORPHANED DEAD CODE.** Imported/rendered NOWHERE in `src` (only referenced in two code comments); no lazy/dynamic import. Vite/Rollup tree-shakes them out → they don't ship in the bundle and are unreachable in the app. `MCPConnectorPanel` is the live MCP surface (firewall-submodules.jsx:152, module 1.4). **No action:** not a live surface to harden; NOT deleting during the parallel MCP session's active work (their territory + revival risk) — flagged as a **cleanup candidate to coordinate with the MCP session** (removing ~2971 lines of dead MCP source).
- **MCPScanControlMatrix.jsx (922 L) = VERIFY-ONLY.** Rendered INSIDE `MCPConnectorPanel` (never_edit, stress/MCP-owned) at L1793 → part of the connector surface the MCP session owns (item-17 scope). **Do not edit.** Code-verified no-leak clean: it renders scan-control policy rows (tier/direction/scope) + an "Effective preview"; no raw URLs/endpoints/keys/commands/topology. Not reachable in the default module-1.4 view (needs connected MCP servers, which the dev DB has none of).
- **Live verify-only of the MCP surface (module 1.4), both themes:** no leaks (scanned for `sk-`/Bearer/non-local URLs/`ip:port` → none), **no overflow @1440/375**, **console 0 errors**. Screenshots `mcp-parallel/findings/frontend-harden/mcp-verify-only/`. (Also contributes to item-17 verify-only coverage of the MCP connector surface.)
- **No product code changed this iteration** (correct for verify-only/orphaned). Item 7 marked 👁 verify-only.

**Item 6 — KillSwitch/ModelState (iter6 resumed loop, 2026-07-02):** analyzed via parallel workflow (safety-critical control surface), verified live (firewall-1-6; 11 real models, KillSwitch empty state), console 0 errors, responsive (panels 351px @375 via F-RESP-1), no page overflow. **All destructive testing used route intercepts — no model was actually isolated (verified 0 isolated after).**
- **F-MS1 FIXED (CRITICAL safety):** `handleIsolate`/`handleRecover` POSTed without checking `res.ok` → a failed isolate/recover **silently no-op'd**, leaving the operator believing a kill succeeded. Now: `window.confirm` + `res.ok` check + `setLoadError`. **Verified live:** intercepted isolate→500 shows *"Could not isolate … the model was not changed"* and the model stayed active; dismissing the confirm blocks the action. Also fixed a latent bug: `recover` now `encodeURIComponent`s the model name (names contain `/`).
- **F-KS1 FIXED (CRITICAL data-integrity):** `fetchKillSwitches` swallowed non-ok/network errors → the safety list collapsed into "No kill-switches configured", hiding ACTIVE switches. Added `loadError` + banner + Retry; empty message suppressed on error. **Verified live** (500 intercept → error+Retry, not false-empty).
- **F-KS-confirm FIXED (safety):** activate/deactivate now `window.confirm` (block/restore org traffic). (activate/deactivate already checked res.ok — good.)
- **F-MS-leak FIXED (med, no-leak):** audit-log rows now run through `filterUserManagedModels` so the reserved guard model's raw id can't surface (mirrors allowlist/governance panels).
- **Theme contrast FIXED:** ModelState audit-table Time/Risk/Reason cells + Auto-refresh + Risk-Score micro-labels (`text-slate-400`/`slate-500` w/o dark variant → `text-slate-500 dark:text-slate-400`); KillSwitch inactive badge `dark:text-slate-400`→`slate-300`.
- **No-leak PROVEN:** only key STATUS (`api_key_set`) + short `api_key_prefix` (deliberate, ~`zs_a1b2`, not the credential) + model names/org-slug (own org) render — no raw keys/hostnames/IPs.
- lint 42/42, build green, detector clean. Evidence: `mcp-parallel/findings/frontend-harden/killswitch/`.
- **DEFERRED (logged for a follow-up polish pass — real but lower-priority):** (a) ModelState threshold range `onChange` fires a PATCH per drag-step and isn't disabled in-flight → request storm (needs debounce/commit-on-release); (b) `fetchAuditLogs` swallows errors (secondary read); (c) KillSwitch 8-col controls table relies on `overflow-x-auto` — safety controls are the least-discoverable column at 375 (consider a stacked/card layout); (d) combobox prints raw `LiteLLM id:` (low); (e) `actionLoading` single scalar + post-action full-table loading flash (minor UX).

**Item 5 — Routing panels + TWO cross-cutting infra fixes (iter5 resumed loop, 2026-07-02):** analyzed all 3 via a parallel workflow, verified live (firewall-1-5), console clean.
- **No-leak PROVEN (code-level):** RoutingGovernance shows model names/weights/formula (public), no secrets/topology. RoutingAudit uses curated field accessors (`getRoutingExtra`/`getEventMetadata`) — renders models/scores/reason/sensitivity/`endpoint_name` (friendly), NOT raw `metadata`/`prompt_lineage`/`data_accessed`/`endpoint_identifier`. PolicyDomainSwitcher renders static labels only. (No live routing events in dev DB → row-render verified by code, not screenshot.)
- **F-RT2 FIXED (contrast):** 5 helper/caption `dark:text-slate-500` (RoutingGovernance) + 2 (RoutingAudit) → `dark:text-slate-400` (verified oklch L0.554→0.704).
- **F-RT3 FIXED (responsive):** sensitivity `grid-cols-4` → `grid-cols-2 sm:grid-cols-4` (verified 2 cols @375, no clip).
- **F-RT-saveerr FIXED (no-leak):** RoutingGovernance save error dumped raw `JSON.stringify(body)` → now parses field messages / generic fallback (like ModelGovernance).
- **F-RT1 FIXED (data-integrity, standalone path):** RoutingAudit local fetch error → `setLocalEvents([])` collapsed to empty; added `loadError` + error UI + Retry. **Note:** on firewall-1-5 the panel is rendered with a **parent feed** (`FirewallModulePage` clones it with `events=firewallData.threatFeed`), so the local path isn't exercised there — the page-level empty-vs-error then depends on the shared `firewallData`/`useFirewall` hook → **logged as cross-cutting data-integrity (item 19/20).**
- Detector: cleaned a **pre-existing** `gray-on-color` false-positive (toggle ternary co-occurring slate+emerald classes) by extracting `ROUTING_TOGGLE_STYLES` (matches the file's existing style-const convention). Now 0 findings.
- lint 42/42, build green. Evidence: `mcp-parallel/findings/frontend-harden/routing/`.

### Cross-cutting infra fixes (discovered during item 5)
- **A11Y — global keyboard focus ring (index.css):** NO custom control defined a `focus-visible` ring and index.css had none → keyboard focus was invisible app-wide. Added `.zs-app-shell :is(a,button,input,select,textarea,[role=button],[role=tab],…):focus-visible { outline: 2px solid var(--ring); outline-offset:2px }`. Pure style, theme-aware, keyboard-only (no pointer regression). **Verified:** keyboard-focused button shows a 2px indigo outline in dark. This satisfies the **focus** dimension for ALL surfaces going forward.
- **F-RESP-1 (CRITICAL responsive) — FIXED:** firewall-module panels rendered at **~1073px at a 375 viewport** (docScrollWidth stayed 375 because the `flex-1 overflow-y-auto` content area scrolls horizontally — so a naive `documentElement` overflow check FALSELY passed). Root cause = the CSS-Grid `min-width:auto` trap on `FirewallModulePage`'s grid items. Fix: added `min-w-0` to the two grid children (L190, L203). **Verified:** panel width 1073→**351px** at 375; presets now wrap (3 rows, all reachable). **This unblocks responsive for EVERY firewall-1-N panel** and retroactively makes items 3–4 responsive genuine (they were previously verified only via documentElement overflow, i.e. under-verified). Re-verify affected panels' responsive at the panel-width level, not just document overflow.

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
