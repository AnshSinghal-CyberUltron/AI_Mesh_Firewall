# Claude Code Ralph — Frontend Hardening + Impeccable Revamp (main; 50–100 iters)
# COMPLETE only when EVERY panel + ui/* primitive is verified & fixed in BOTH themes at all 4 widths,
# charts migrated behind SafeResponsiveChart, data-integrity + no-leak proven, impeccable clean.

## Setup
- [x] 0. /impeccable init; add pre-flight claims check (mcp-parallel/claims); create FRONTEND_AUDIT.md.
      DONE iter1: PRODUCT.md + DESIGN.md written (register=product, real OKLCH tokens both themes);
      FRONTEND_AUDIT.md scaffold w/ claims protocol + VERIFY-ONLY set (MCPConnectorPanel,
      ModelConnectionPanel, OutputPipelineTimeline) + gate + baseline (lint/build green) + risks R1–R4.

## Chart migration (contained slice — do early)
- [x] 1. Migrate SafeResponsiveChart internals recharts→ECharts (keep API identical); theme-aware colors.
      iter2: FOUNDATION (chartTheme zs-light/zs-dark tested, echartsCore slim, EChart theme-reactive+resize+
      reduced-motion, SafeResponsiveChart `option` superset API). iter3: DONE + PROVEN LIVE — migrated
      OutputGuardrailCharts (4 charts) recharts→ECharts, verified vs real backend data (module 1.7) both
      themes @1440/768/375: data-identical, interactive tooltip (theme-aware), no overflow, console clean.
      Fixed F1 (light tooltip/axis now readable) + F2 (donut label clip) + radar theme deprecation.
      Harness recipe + findings F3 (backend PG 500 bursts), F4 (KPI=0 vs chart-has-data) logged in AUDIT.
      Remaining 11 recharts panels migrate under items 3–16; recharts removal = item 24 exit check.
- [x] 2. uPlot for dense time-series charts (telemetry/logs/trends). Verify data-identical + interactive + faster.
      iter(resumed)1: foundation + Pressure sparkline (×7). iter2: DONE — stacked-area support
      (uplotStack.js +tests, raw-value tooltips), compactNum axis (fixes 5-digit y-clip), migrated Overview
      "Enforcement actions over time" (stacked) + AttackVectorTrend (overlapping). All 3 Overview AreaCharts
      → uPlot. LIVE-verified both themes @1024/768/375: uplot(recharts=false), data-identical, hover shows
      time+raw values, no overflow, 0 chart errors. Fixed a gradient-bbox crash. lint 37/37.
      Remaining 8 uPlot candidates migrate under per-surface items (12/13/15). recharts removal = item 24.

## Per-surface sweep (ONE panel per iteration → verify (both themes, 4 widths) → fix → re-verify → commit)
- [x] 3. GatewayKeyPanel     - [x] 4. ModelGovernancePanel   - [x] 5. RoutingGovernance/Audit
      item5 iter5: DONE. Routing{Governance,Audit}Panel + PolicyDomainSwitcher — analyzed via workflow,
      verified live. Fixed F-RT2 (helper contrast), F-RT3 (sensitivity grid responsive), F-RT-saveerr,
      F-RT1 (audit error path, standalone). No-leak proven (curated fields). +2 CROSS-CUTTING: global
      focus-visible ring (index.css, app-wide a11y) + F-RESP-1 CRITICAL (FirewallModulePage min-w-0 —
      panels were 1073px@375, now 351px; unblocks responsive for ALL firewall panels; re-verify 3/4 resp).
      lint 42/42, detector clean.
      item4 iter4: DONE. Live both themes @4 widths (11 real models), console clean, no overflow, no-leak
      PROVEN (key STATUS only, guard model stripped). Controls verified: clean→save-disabled, toggle→
      enabled, reset→disabled. Fixed F-MG1 (helper text dark:slate-500→slate-400). lint 37/37.
      iter3: DONE. Live-verified both themes @4 widths (7 real keys), console clean, modal validates, no
      overflow, no-leak PROVEN (list has no raw key; prefix only). Fixed F-GK1 (fetch error→error state,
      not false-empty), F-GK2 (badge dark: text variants), F-GK3 (create error text hardened). lint 37/37.
- [x] 6. KillSwitch/ModelState - [x] 7. MCPManager/Scanner/ScanControlMatrix (VERIFY-ONLY/ORPHANED)
      item7 iter7: NO editable live surface of mine. MCPManagerPanel(1945L)+MCPScannerPanel(1026L) =
      ORPHANED DEAD CODE (imported nowhere, tree-shaken; cleanup candidate to coordinate w/ MCP session,
      not deleting during their active work). MCPScanControlMatrix(922L) = VERIFY-ONLY (rendered inside
      never_edit MCPConnectorPanel → item 17 scope); code no-leak clean (policy controls, no topology).
      Live verify-only of MCP surface (module 1.4) both themes: no leaks, no overflow @1440/375, console
      clean. No product code changed. Full MCPScanControlMatrix live render folds into item 17 (needs MCP servers).
      item6 iter6: DONE (safety-critical). Workflow-analyzed + live-verified (11 real models; intercepts→
      0 real isolations). Fixed CRITICAL F-MS1 (isolate/recover res.ok+confirm+error, +encode bug),
      F-KS1 (kill-switch load-error state), F-KS-confirm, F-MS-leak (audit guard-model mask), theme
      contrast (audit cells/badges/labels). No-leak proven (key status/prefix only). lint 42/42. Deferred:
      slider PATCH-storm, audit fetch err, KS 8-col table @375, combobox litellm id — logged in AUDIT.
- [x] 8. DatabaseConnection/VectorPolicy - [x] 9. RAGFeatureTest/RAGAttackTrust/PipelineTelemetry
      item8 iter8: DONE. Workflow-analyzed. DatabaseConnectionPanel (module 1.3) live-verified both themes
      @4 widths: no-leak PROVEN (masked keys, write-only BYOK, gateway key never rendered), fixed pipeline
      badge dark-variants + helpText/latency contrast + pipeline-trace overflow-x-auto (was clipped @375).
      VectorPolicyPanel: 5 badge/banner dark-variant fixes + POLICY_STATUS_STYLES const (clears pre-existing
      gray-on-color); no-leak clean; live render on enterprise page → folds into item 15. lint 42/42.
      item9 iter9 PART1: RAGPipelineTelemetry DONE — 5 charts recharts→ECharts (funnel/stacked-bar/donut/
      radar/latency), recharts import removed, theme-aware; live both themes (0 recharts SVGs, data-identical,
      console clean). Fixed 22 inverted slate-400/dark:slate-500 spots + colored-number light variants.
      lint 42/42. PART2 next: RAGFeatureTestPanel (theme + data-integrity BLOCKED/ALLOWED conflation + Run
      race) + RAGAttackTrustSimulator 1021L (med-leak raw-resp/doc-content, focus:outline-none, contrast).
      item9 PART2 DONE: RAGFeatureTestPanel — data-integrity (finalAction=`error` when no pipeline_audit,
      never fabricate ALLOWED/BLOCKED from an infra 500; honest detail msg), Run-race (all Run buttons
      disabled while any test runs: `disabled={running != null}`), badge pills + teal/amber/blue/red light
      variants, stages-row overflow-x-auto @375, inverted-slate swap. RAGAttackTrustSimulator — ACTION_STYLES
      + status/escalation badges + 20+ inline amber/red/blue/emerald/purple got `-700 dark:-400` light
      variants, active-selection chips (teal/purple/red) light-legible, 21 inverted-slate swapped, 2×
      focus:outline-none dropped (global ring covers a11y). LEAK dispositon: raw-JSON dump + "Allowed
      Documents" content + NS echo render the operator's OWN RAG corpus in an admin trust-sim (tool's
      purpose) — gateway key lives in request headers, never in result.body → NOT a leak, LOGGED not removed.
      GATE: lint 42/42, build clean, detector clean on both. LIVE both themes @1440/1024/768/375: overflow=0
      all 8, 0 console errors + 0 net-fails when backend calm; dark+light panel shots verified (tab switch
      Attack↔Trust works, SAFE/ATTACK badges legible, Run controls, sliders, helpers). 500 bursts = known F3
      env PG "too many clients", not code. recharts still removed. Item 9 fully complete.
- [x] 10. OutputGuardrailControls/Charts - [x] 11. AttackSimulatorPanel (+ simulator/*) - [x] 12. PolicyManagement/Analytics
      item12 DONE (2-agent workflow): PolicyAnalyticsPanel recharts→ECharts (effectiveness area + violation
      stacked-bar → SafeResponsiveChart `option`, theme-aware via zs-light/zs-dark, recharts import removed);
      DATA-INTEGRITY: added missing 6th `agenticThreat` violation category (backend emits it in
      violations_by_* but VIOLATION_COLORS only had 5 → stacked bar UNDERCOUNTED) — validated vs live API +
      VISUALLY confirmed (legend shows all 6 incl. Agentic Threat). StatusBadge/error-banner dark variants,
      MetricCards grid-cols-2 sm:grid-cols-4. PolicyManagementPanel (1760L, ALSO embedded in MCPConnectorPanel
      → kept API-compatible): DATA-INTEGRITY fixed "Loaded Rules" KPI (summed only lazily-loaded policyRules →
      near-always-0; now sums backend policy.rule_count w/ live override → "Total Rules 55" verified). Theme:
      SeverityBadge/ActionBadge configs + Active badge + 4 modal/banner status boxes + Add-Rule + slate
      metadata (all text-*-700 dark-on-dark → dark:*-300). Responsive: RulesTable overflow-x-auto + min-w,
      modal grid-cols-3→sm:, metadata flex-wrap. CONTROL: RuleModal guardedClose (backdrop/X/Cancel were
      unguarded mid-save), PolicyModal Cancel guarded, both modals Esc-to-close + role="dialog"/aria-modal.
      Detector: cleared 2 pre-existing ternary FPs via RULE_STATUS_ACTIVE/DISABLED consts. GATE lint 42/42,
      build, detector 0 both. LIVE: PolicyMgmt both themes @1440/1024/768/375 overflow=0 all 8 + 0 console/net;
      migrated Violation-Breakdown chart visually verified (light). Deferred-LOW: rule-delete double-submit,
      MCP key/regex validation, full modal focus-trap (tab containment), analytics abort-guard.
      item11 DONE (module 1.1 + simulator/* across 1.1/1.3/1.4/1.5/1.6): 10-agent Workflow surfaced ~40
      findings; fixed. HIGH DATA-INTEGRITY (fabricated verdicts): CircuitBreaker read flat result.model/state
      but store is {ok,data} → always-blank + always-green "healthy" even on error (now reads data + honest
      error panel); MCPGuardrail actionTone() had no "error"/"flag" branch → 500 rendered green ALLOW (added
      ERROR/FLAG tones); OutputGuard hallucination meters were 3/4 hardcoded constants in
      normalizeOutputGuardResult (grounding always 100%, pattern/contradiction 0%, risk=boolean→70%) → replaced
      with honest single factuality_warning signal; MCPGuardrail dry-run defaulted allow on 200-with-null-body
      (now requires body). IN-FLIGHT RACES (Execute never disabled → concurrent setResult races): fixed
      ModelRouting/OutputGuard/CircuitBreaker/IsolationOps-circuit via local executing flags. LEAK: AttackSim
      raw-JSON dump/copy bypassed sanitizeGuardText (Bedrock/provider literals the file says "must never reach
      the operator UI") → added recursive sanitizeResultForDump. THEME: 5 AttackSim verdict labels (text-*-700
      no dark → dark-on-dark), SimulatorShell status badges + copy check, StageTimeline verdict word (-500 icon
      color as text → reuse -700/-300 badge token), +misc slate/amber. RESPONSIVE: grid-cols-4/3/2 → sm/lg
      breakpoints (CircuitBreaker, ModelRouting, RAGIngestion, OutputGuard, SimulatorShell trace overflow).
      MISC: removed no-op riskCount slider + fabricated events_injected; honest latency "—" (was fake 0.1ms);
      burst-count clamp to 100; collection-required validation; copy-fail no-op; StageTimeline undefined-docs
      guard; IsolationOps gpt-4o-mini phantom fallback removed. GATE lint 42/42, build, detector 0 on 8/9 edited
      (StageTimeline 2 PRE-EXISTING FPs = dark:variant on light emerald tint, correct code). LIVE both themes
      @1440/1024/768/375: overflow=0 all 8, 0 console/net; AttackSim + shared SimulatorShell verified legible,
      keys masked. Deferred-LOW (logged): dialog focus-trap/esc, StageTimeline type=button, bulk-ingest count,
      deriveResultAction "allow" default (load-bearing for normalized success shapes), ModelRouting dead
      scored_models viz (honest placeholder).
      item10 DONE (module 1.7): OutputGuardrailControls = GOLD-STANDARD semantic OKLCH tokens
      (text-foreground/muted-foreground/bg-card/text-destructive/bg-warn/text-primary) → 0 edits, theme-correct
      by construction; verified controls interactive (toggle Flag pill → Save enables: disabledBefore=true→
      after=false), Hallucination row disabled-pills state correct, load/save(400/403)/reset/dirty all sound,
      no-leak (config only). OutputGuardrailEngineCard = 3 inverted-slate contrast spots fixed; honest
      Idle/Active/Error states, real /api/security/threat-feed data w/ canonical actionBucket color-map, counts
      only (no-leak). OutputGuardrailCharts = already ECharts (item1/iter3), 0 recharts confirmed, verify-only.
      GATE lint+build+detector(0 both). LIVE both themes @1440/1024/768/375: overflow=0 all 8, panels legible
      both themes + 375 stacks clean. 500/401 cascades = F3 env (auth/me 500→downstream 401). Deferred-LOW:
      EngineCard "0F" flagged counter is structurally always-0 (flag folds into redacted bucket) — honest 0,
      logged not changed.
- [x] 13. LogViewer/LogDetailPage - [x] 14. OWASPStatsPanel - [x] 15. Firewall12EnterprisePage - [x] 16. HowToUse
      item16 DONE (HowToUse per-module getting-started card, on every firewall module page). Static docs
      content (howToUseContent.js) is LEGITIMATELY static (instructional) — no fabricated backend data; returns
      null when no content (honest). NO LEAK: example code uses placeholders throughout
      (<your_gateway_api_key>, <jwt_token>, pcsk_...) — no real keys/secrets. THEME: StepList step-number badge
      text-teal-400 → text-teal-600 dark:text-teal-400 (was weak on the light card); rest already clean (code
      block intentionally dark both themes = terminal convention; NoteRow/header/tabs have proper dark
      variants). RESPONSIVE — caught + fixed a real bug via visual check: step descriptions were CLIPPED at 375
      (CSS-grid auto-min-width trap — the steps column sized to max-content 754px inside a 293px cell, clipped
      by the card's overflow-hidden so overflow=0 masked it). Fixed: grid base grid-cols-1 (minmax(0,1fr)) +
      lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)], min-w-0 on both grid columns + step content div, break-words
      on step label/text (long URLs like aimeshgateway.zeroshield.ai/v1), header min-w-0. CONTROLS: expand/
      collapse, CopyButton (clipboard try/catch), CodeTabs all work. GATE lint 42/42, build, detector 0. LIVE
      both themes @1440/1024/768/375: overflow=0 all 8, stepTextOverflowPx=0 (measured), 0 console (F3
      transient only); expanded steps+code+notes verified legible + wrapping both themes; teal badge legible.
      item15 DONE (Firewall12EnterprisePage, module 1.2). Claim check: cursor-ralph-iter2-P1.2 is a RELEASED
      MCP-http/oauth-repro claim (scope mcp-parallel/findings/p1-2/**, docs) — does NOT cover this file. CHART
      migration recharts→ECharts: "Event Trend" LineChart (trendData time/primary) → eventTrendOption line;
      "Top Categories" horizontal BarChart (categoryData, real category counts from threatFeed) →
      topCategoriesOption (yAxis category inverse:true to keep biggest-at-top like recharts); recharts import
      removed. Theme was ALREADY clean (0 inverted-slate, 0 dark-on-dark, grids responsive, 2 text-slate-400 =
      decorative icons). DATA-INTEGRITY clean: KPIs from real socKpis (numberOrDash/percentOrDash honest "--",
      successRate null→"--"), categoryData real top-5, actionDistribution real (honest 0 fallback). Embedded
      PolicyManagementPanel/PolicyAnalyticsPanel/VectorPolicyPanel already hardened (items 12/8; VectorPolicy
      item-8 deferred live-render folds here — Vector tab, detector-clean + page renders clean). GATE lint
      42/42, build, detector 0. LIVE both themes @1440/1024/768/375: canvases=2 ALL 8, overflow=0 ALL 8, 0
      console/net (except 768-light F3 transient); line + horizontal-bar visually verified data-identical +
      theme-aware (dark AND light), Top-Categories correctly ordered biggest-at-top, KPIs stack 1-col @375.
      item14 DONE (OWASPStatsPanel, Overview): CHART migration recharts→ECharts — stacked coverage BarChart
      (Blocked/Allowed per vector) + threat RadarChart (Detected/Blocked top-active vectors) → SafeResponsiveChart
      `option` (coverageBarOption/threatRadarOption), theme-aware zs-light/zs-dark (fixes old hardcoded
      #94a3b8/#f1f5f9 axes + white recharts tooltip on dark card); recharts import removed. THEME: CoverageBadge
      (EXCELLENT/GOOD/MODERATE/LOW) + FAMILY_CONFIG (llm/mcp/agentic label+icon) + Blocked-count red-600 all
      were text-*-700/red-600 dark-on-dark → dark:*-300/dark:red-400; empty-state slate. RESPONSIVE: summary
      grid-cols-4→grid-cols-2 sm:grid-cols-4; FamilySection header flex-wrap. DATA-INTEGRITY clean (real
      /api/security/owasp-stats, honest "--"/"No detections" empty states). Removed unused AlertTriangle import.
      GATE lint 42/42, build, detector 0. LIVE both themes @1440/1024/768/375: overflow=0 all 8, 0 console/net;
      radar + stacked-bar visually verified data-identical + theme-aware (dark AND light), badges legible, 375
      summary 2-col + header wrap confirmed.
      item13 DONE (3-agent workflow inventoried 21 charts). LogViewerPanel = ORPHANED dead code (mounted
      nowhere, confirmed; item-7 precedent → logged, not edited). LogDetailPage (part1, commit 0a805a91):
      DATA-INTEGRITY — timestamp defaulted to now() → honest fmtTimestamp "—"; Method/Endpoint placeholders
      ("POST"/"/v1/chat/completions") → "—"; timelineData was a single hardcoded [{time:"Event",latency}]
      point feeding a fake "timeline" AreaChart → REAL per-stage latency from pipeline_trace, card hidden when
      no trace. CHART: AreaChart recharts→ECharts bar (theme-aware); removed recharts import + hardcoded
      logChartTheme/isDark/useTheme. THEME StatusBadge/back/Copy/loading dark variants. RESPONSIVE header
      flex-wrap + request-id truncate. (deriveStageVerdict/_STAGE_SCORE left — documented anti-under-report.)
      module-specific-log-charts (part2): the factory FABRICATED ~15 of 21 charts (latency×0.3/0.5 "breakdowns",
      prompt.length field-size splits, per-doc similarity DECAY CURVES from one value, 2-point "risk trends",
      content-analysis hardcoded by action allow?80:35, "confidence" from count/10, cost from tokens/100000,
      1-point line). REMOVED all fabricated → get13/15/16/17/default now return {charts:[]} (card hides = honest
      empty). KEPT+migrated to ECharts the 6 REAL charts: get11 stage-latency (real trace) + token-usage (real
      counts, dropped 0.6/0.4 synth split); get12 ×3 real pipeline_audit (tightened guard ragEmpty→!hasAudit,
      dropped synthetic non-audit fallback); get14 PII-detections (real matched/tags/redaction counts, guarded).
      recharts import removed. GATE lint 42/42, build, detector 0 (both files). LIVE reached LogDetailPage via
      evidence-row click, both themes @1440/375: overflow=0 all 4, 0 console/net, ECharts canvas renders,
      honest empty states verified ("no per-stage trace → no chart"), real timestamp/method/endpoint shown.
      Deferred-LOW: LogViewerPanel dead-code cleanup (coordinate); get12 synthetic-fallback compute still runs
      but is guard-dropped (never rendered).
- [x] 17. VERIFY-ONLY (log, don't edit): MCPConnectorPanel, ModelConnectionPanel, pipeline-trace cards
      item17 DONE (VERIFY-ONLY, NO EDITS — claude-ralph-stress-iter1-R0 claim ACTIVE, owns all 3). Live sweep
      module 1.4 (MCPConnector) + 1.5 (ModelConnection) both themes @1440/1024/768/375 (16 combos):
      overflow=0 ALL 16, DOM leak-scan (sk-/AKIA/JWT) = 0 hits ALL 16, console errors only 2 combos = F3
      transient 500s (env). Read-only code scan: ModelConnectionPanel + OutputPipelineTimeline = fully clean
      (0 inverted-slate / dark-on-dark / bare-400 / non-responsive-grid / focus-none / recharts / raw-key
      render). MCPConnectorPanel = 1 minor theme spot L304 inverted `text-slate-400 dark:text-slate-500`
      (subtitle) — LOGGED for stress session, NOT edited (L1381/L2330 red-700 are hover states, not defects).
      OutputPipelineTimeline live-render needs a real output-guard event (env has 0) → folds with item 10.
      Findings logged to FRONTEND_AUDIT.md for the owning session. No files changed (verify-only).
- [x] 18. Shared ui/* primitives (Button/Card/Dialog/Input/Select/Table/Tabs/Badge/Toast/Tooltip/…): both themes, API-compatible restyle
      item18 DONE. No ui/* claims. Scanned ALL 20 primitives (Badge/Button/Card/Dialog/EmptyState/Input/Label/
      PanelHeader/Progress/SegmentedControl/Select/Skeleton/Slider/Spinner/Switch/Table/Tabs/Textarea/Toast/
      Tooltip) for inverted-slate/dark-on-dark/bare-400/focus-none/hardcoded-color. Design system is
      GOLD-STANDARD: Button (all 6 variants dark-variant'd, focus-visible:ring + dark offset, disabled:opacity),
      Input (semantic tokens border-input/bg-background/text-foreground/ring-ring, theme-aware by construction),
      Dialog (portal + focus-trap + ESC + aria-modal + scroll-lock + focus-restore w/ documented focus-loss
      root-fix). All bg-white have dark:bg-slate-* (or intentional Switch thumb); Select focus:outline-none is
      correctly paired with focus:ring-2 ring-ring. ONLY FIX (API-compatible, styles only): EmptyState L7 icon
      `text-slate-400 dark:text-slate-500` → `text-slate-500 dark:text-slate-400` (inverted — icon was near-
      invisible slate-500 on the dark slate-800 container; now matches the sibling description pattern). GATE
      lint 42/42, build, detector 0. VERIFICATION: the 19 clean primitives are transitively live-verified via
      every panel in items 9-17 (Button/Card/Dialog/Badge/Switch/Select/Input rendered correct BOTH themes @4
      widths). ui/EmptyState is imported ONLY by MCPConnectorPanel + MCPScanControlMatrix (stress verify-only
      MCP surface, needs connected MCP servers the dev DB lacks) so its live render can't be triggered here —
      fix is code-verified + trivially correct. Deferred: none.

## Cross-cutting hardening
- [x] 19. Data-integrity: prove no static/mock/placeholder/wrong data anywhere; every value binds to real backend.
      item19 DONE. Codebase fabrication sweep: 0 Math.random, 0 mock/dummy/fake/placeholder data vars, 0
      synthetic-breakdown patterns (latency×0.NN / length×ratio / decay-curve — all removed in item 13), 0
      inline static chart arrays. All chart/KPI/table surfaces bind to REAL backend: useFirewallData, fetchWithAuth
      /api/*, or props from parents that fetch (SubModuleDetailPage). Honest empty states everywhere
      (numberOrDash/percentOrDash→"--", null→"--", "Awaiting X data", "No detections"). Orphaned dead code
      (module-specific-charts real-fetch-but-unrendered, MCPManagerPanel) not shown. Benign config-defaults only
      (RiskGauge threshold||80, form priority||100). BIG FABRICATION offenders already killed in items 11
      (OutputGuard hallucination meters) + 13 (log-chart factory 15 fake charts). CONCRETE FIX: 3 status
      indicators were HARDCODED (stayed green even if backend down) — Header "Connected" badge, Sidebar "System
      Status: All systems operational/Protected", Firewall12 "Operational" badge. Created useBackendHealth hook
      (polls /api/health/ 30s → connected|checking|disconnected) and bound all 3 → real Connected/Connecting/
      Offline + operational/checking/unreachable + Operational/Checking/Backend-offline. Login "10M+ calls" =
      pre-auth marketing hero stat (logged, not a dashboard-data violation). GATE lint 42/42, build, detector 0
      (Header/Firewall12/hook; Sidebar has 1 PRE-EXISTING side-tab FP on the nav border-l-2, not mine). LIVE dark
      @1440: all 3 indicators resolve from real health (Connected/operational/Operational, backend up); flip
      logic symmetric for down-state.
- [x] 20. No-leak: prove no raw key/PII/secret/topology is ever displayed.
      item20 DONE (PROVEN, no new code fixes — leak fixes were items 9-11, masking items 3/6/8). STATIC sweep:
      0 type=text key/secret inputs, 0 raw-secret-field renders (ModelConnection L689 shows env-var NAME not
      value), 0 console.log secret leaks, 0 HARDCODED creds/tokens in source. Provider/model literals only in
      CONFIG surfaces (ModelConnection catalog — admin's own model choices, verify-only) + intended mock attack
      payloads (OutputGuardSim IP-leakage test scenario). err.message renders = client-side fetch errors (no
      server stack/paths); no err.stack rendered. Hostnames/IPs = operator's own gatewayUrl config + doc URL-
      rewrite helpers (resolve to user's own deployment). POSITIVE masking confirmed: keys ENTERED via
      type=password (6 panels), DISPLAYED as prefix-only (key_prefix/api_key_prefix — GatewayKey/KillSwitch/
      MCPConnector/DatabaseConnection). Raw JSON dumps (SimulatorShell/RAGAttackTrust) = operator's own admin-sim
      data behind opt-in toggle (assessed items 9/11); AttackSim guard-model dump sanitized item 11; backend
      telemetry.py redact_all scrub verified item 10. LIVE DOM leak-scan (sk-/AKIA/pcsk_/JWT/ghp_/PEM) across
      overview + modules 1.1-1.7 = 0 hits ALL 8 (+ item-17 0 hits on 1.4/1.5). PII displays = operator's OWN
      data (profile email, allowlist emails, login field). No files changed.
- [x] 21. Theme audit: every surface correct in dark AND light (contrast/focus/hover/disabled).
      item21 DONE. Codebase-wide contrast sweep + fixes (24 real spots across 9 files): 11 inverted-slate
      `text-slate-400 dark:text-slate-500`→`slate-500 dark:slate-400` (VectorProviderConfigPanel×3, Overview×2,
      Sidebar×2, Login, SubmoduleDetailPage, SafeResponsiveChart, FirewallModulePage); 8 BedrockTestPanel
      dark-on-dark verdict strings (text-{red,amber,emerald}-700 → +dark:*-300); 5 RAGPipelineTelemetry
      meaningful bare-400 (blocked-count, status ternary, escalation-level textColors → -600 dark:-400).
      FOCUS a11y PROVEN: 0 focus:outline-none WITHOUT a paired ring across all components (Button/Input/Select/
      Switch/Tabs/Textarea/Tooltip use focus-visible:ring). Decorative section/header ICONS on bg-*-500/10 chips
      (RAGPipelineTelemetry 6, RAGFeatureTest feature-icon config, RAGAttackTrust Crosshair/Zap) left per
      convention. VERIFY-ONLY/orphaned MCP theme spots LOGGED not edited: MCPScanControlMatrix×7, MCPManagerPanel
      ×5(orphaned), MCPConnectorPanel×1. Pre-existing detector aesthetic FPs (NOT contrast bugs, NOT mine):
      Overview/SubmoduleDetail ai-color-palette = curated multi-color palette MAPS for per-module differentiation
      (violet/purple/indigo among ~9 options); Login gradient-text = BRAND teal→cyan hero (from-teal-600 to-
      cyan-600), not slop; Sidebar side-tab = legit active-nav border-l-2 — all documented, left intentional.
      GATE lint 42/42, build, detector 0 on all contrast-fixed files. Prior per-surface theme fixes (items
      9-20) + this sweep = every surface contrast-correct both themes.
- [x] 22. Responsive audit: no overflow/overlap at 1440/1024/768/375 on every surface.
      item22 DONE. KEY METHOD FIX: docOver (`documentElement.scrollWidth-clientWidth`) reads 0 even when
      content overflows, because `<main>` has `overflow-y-auto` → CSS computes overflow-x to `auto` →
      `<main>` ABSORBS horizontal overflow into an internal scrollbar. Built a `mainScroll` audit
      (`main.scrollWidth-clientWidth`) that catches it. Swept overview + 1.1-1.7 + profile/settings/
      firewall-config/login + SubModuleResultsPage drill-down, both themes @ 4 widths.
      REAL overflows FIXED (4 files, all data-dependent — only appear once panels populate):
      · SubModuleResultsPage: 4-node flow pipeline (min-w-[160px]×4≈928px) + Detailed Records toolbar
        (mainScroll 1030>375@375, 908>768@768) → flow card overflow-x-auto, toolbar/pagination flex-wrap,
        p-4 sm:p-6 lg:p-8. LOGGED for item24: fabricated arrowTimings (lat×0.3/0.4/0.3) + still-recharts. (9491ae34)
      · rag/CollectionManagerPanel: grid-cols-3 form crammed@375 (Create btn right=436, mainScroll 61)
        → grid-cols-1 sm:grid-cols-3 + sm:col-span-2 + input min-w-0.
      · ModelStatePanel: header+Sync/Audit/Live toolbar → flex-wrap + min-w-0.
      · OutputGovernancePanel: header+blocked/redacted/flagged chips → flex-wrap + min-w-0. (eb466821)
      Re-verified 3 panel fixes @ all 4 widths: mainScroll=0 @1440/1024/768, realCount=0 @375.
      VERIFY-ONLY LOGGED (never_edit): MCPConnectorPanel (1.4) MCP server cards 884px overflow main
        @768(140)/@375(521) — corrects item17's "overflow=0" (docOver blind spot). Stress session owns fix.
      Residual uniform mainScroll=12 on tall pages = vertical-scrollbar-width artifact (0 w-screen/100vw
        in src; ai-mesh-hero-glow clipped by hero overflow-hidden; dark settled run=0; screenshots clean).
      ENVIRONMENTAL F3: concurrent Playwright fleets saturated dev Postgres → control-plane restart +
        transient wide loading skeletons; all findings re-confirmed vs healthy backend. GATE lint 42/42, build, detector 0.

## Freeze the frontend
- [x] 23. Add Playwright visual+behavior snapshots per surface (both themes) as a regression gate.
      item23 DONE. Two-layer gate:
      (1) LIVE Playwright harness `frontend/tests/visual/audit.mjs` (+ surfaces.json, run.sh, README,
          .gitignore; npm run test:visual). Audits EVERY surface (overview+1.1-1.7+config+profile+settings
          +login) × BOTH themes × 4 widths. Per combo asserts: no real horizontal overflow (the mainScroll
          metric that catches <main>-absorbed overflow docOver misses; excludes overflow-hidden/scroller/
          transform), no leak (sk-/AKIA/pcsk_/ghp_/PEM/JWT scan of DOM+inputs), no pageerror/JS console
          error, and 0 recharts SVGs on migratedChart surfaces. Writes PNG per combo (visual) + report.json
          (behavior); threshold-based (no brittle pixel baselines); exit!=0 on regression. F3-robust
          (auth retry w/ backoff, inter-combo pause, network noise reported not gated; MCPConnectorPanel
          allowOverflow=reported-not-gated since stress-owned). Validated live: overview light+dark @1440
          GATE PASSED exit 0; measurements correct (mainScroll/overflow/echarts/recharts).
      (2) BROWSERLESS static gate `src/utils/hardening-regression.test.js` — runs in npm test/lint (node
          --test, no browser/backend), 16 assertions locking in the loop's fixes: no recharts import in the
          6 migrated files, SafeResponsiveChart option/uplot API, the 4 item-22 responsive fixes, HowToUse
          item-16 grid, useBackendHealth wiring, no inverted-slate in fixed files. lint now 58/58 (was 42).
      GATE lint 58/58, build green.
- [ ] 24. Full re-verify pass; FRONTEND_AUDIT.md all resolved; impeccable clean; console/network clean.
      Only when ALL items done in BOTH themes at ALL widths → output <promise>COMPLETE</promise>.
      PROGRESS (recharts removal — the remaining item-24 work):
      · [x] SubModuleResultsPage.jsx — DONE (commit e3ae804a): AreaChart+BarChart → ECharts option; killed
        hardcoded chartTheme + isDark-ternary theming; FIXED fabricated arrowTimings (lat×0.3/0.4/0.3 →
        honest "--" + real end-to-end avg in heading). Live-verified both themes: 0 recharts SVGs, honest
        empty states, 0 overflow, clean console. Added to static no-recharts guard (17 tests).
      · [x] AIMeshFirewallOverview.jsx — DONE (commit 691e58ce): 3 recharts charts → ECharts (2 donuts +
        grouped bar); removed recharts import + ChartTooltip + renderCustomLabel helpers. Live-verified both
        themes @1440/375: recharts 4→0, echarts 9-11 canvases, 0 overflow/leak, clean console. (uPlot
        time-series were already migrated.) Added to static no-recharts guard (18 tests).
      · [x] SafeResponsiveChart.jsx — DONE (commit cf7ee848): removed the recharts <ResponsiveContainer>
        children fallback + import; now option(ECharts)/uplot only. Verified no live component passes
        recharts children first.
      · [x] Orphaned dead code — DONE (cf7ee848): DELETED SubmoduleDetailPage.jsx (878L) +
        module-specific-charts.jsx (218L) — provably dead (0 importers repo-wide; superseded by live
        SubModuleResultsPage / module-specific-log-charts).
      · [x] recharts DEPENDENCY REMOVED (cf7ee848): dropped from package.json + synced package-lock.json →
        cascaded out recharts' whole transitive tree (@reduxjs/toolkit, immer, all d3-*, es-toolkit,
        decimal.js-light, eventemitter3, internmap — none imported directly). R3 bundle/install win.
        Static gate now asserts recharts is not a dep (62 tests). RECHARTS IS FULLY GONE from the frontend.
      RE-VERIFY (b2cc5866 matrix reconcile + live sweep): FRONTEND_AUDIT status matrix reconciled with the
      finding log — 20 ui/* primitives ✅, items 18-23 ✅, item 17b/17c 👁✅, item 15 SubmoduleDetailPage 🗑.
      Live sweep of the chart-migration-edited surfaces both themes @768/375: overview recharts=0 echarts=11
      overflow=0 jsErr=0; module-1-3 recharts=0 echarts=2 overflow=0 jsErr=0. Static gate 62/62. Full-matrix
      live pass is F3-limited (AUTH_FAILED bursts = dev-DB, not regressions).
      FULL RE-VERIFY SWEEP DONE (2026-07-02, live mainScroll harness both themes @768/375, the overflow-prone
      widths; 1440/1024 covered by item-22 scans): overview ✅, 1-1 ✅, 1-2 ✅ (FOUND+FIXED a data-dependent
      header opBadge-pill overflow the item-22 scan missed — 47a19544), 1-3 ✅, 1-5 ✅, 1-6 ✅, 1-7 ✅,
      firewall-config ✅, profile ✅, settings ✅, login ✅ — ALL recharts=0, overflow=0, leak=0, jsErr=0 both
      themes. ONLY 1-4 MCPConnectorPanel still overflows (data-dependent, verify-only). So EVERY OWNED surface
      is now re-verified clean both themes.
      ⛔ COMPLETE BLOCKER (the ONLY open item): item 17a MCPConnectorPanel — the MCP server cards (884px) overflow
      <main> @768/375 WHEN they render (data-dependent). It's `never_edit` (stress claim claude-ralph-stress-
      iter1-R0 still ACTIVE), so I've LOGGED it + the fix (single responsive column / min-w-0) but CANNOT fix
      it. Until the owning session fixes it (or releases the claim), "every panel fixed" + "FRONTEND_AUDIT fully
      resolved" is NOT unequivocally true → must NOT output COMPLETE. Everything else in my remit is DONE +
      verified. Each further iteration: regression-check my surfaces + re-check whether MCPConnectorPanel is
      fixed / the claim released; complete the instant that ⚠ clears.
      [iter+] DATA-INTEGRITY sweep found + FIXED module 1.5 "ROUTING DECISIONS" 500-cap undercount
      (commit 4e6c7bfc): buildModulePageData counted the limit=500 page; filterEventsForModule("1.5")
      matches every routing row via its OR `sources` clause, so the headline is the routing-source total →
      override summary.total with the uncapped threatFeedCount (same as 1.4). Live: UI 500→551 == API count
      551 (7d), verified light/1440 + dark/375 + dark/768; lint 69/69, build clean, detector []. Also
      widened `test:unit` glob (was src/utils/*.test.js only → +src/components/*.test.js) so the new 1.5
      regression test is actually gated (62→69). NEW OPEN finding (separate item, my domain, NOT blocked):
      src/constants/zeroshieldBrand.test.js:19 FAILS — expects "ZeroShield Guard Model", source emits
      "ZeroShield Model"; was ungated (constants not globbed). Needs an intent check (source vs test stale).
      → next iteration target. MCPConnectorPanel still the never_edit COMPLETE blocker (claim active).
      [iter+] RESOLVED the zeroshieldBrand stale test (commit 0006fa71): intent check proved the
      "Guard Model"->"ZeroShield Model" collapse is a DELIBERATE anti-leak (RESERVED_LABEL_FRAGMENTS,
      landed 9f8c9489); test was asserting the old leaky label → updated to assert product label +
      doesNotMatch /guard/i, and gated constants (test:unit glob +src/constants/*.test.js, 69→75).
      Then found + FIXED a REAL no-leak bug (commit 537e6682): AttackSimulatorPanel.jsx:847 fallback
      `sanitizeGuardText(guard_model) || "ZeroShield Guard Model"` rendered the raw reserved branding
      when guard_model is absent → swapped to the imported ZEROSHIELD_GUARD_MODEL_LABEL. Then swept the
      whole tree for sibling reserved-branding leaks (guard/bedrock/claude-haiku/anthropic literals as
      JSX fallbacks/raw strings): the only accidental one was :847 (fixed); all others are intentional —
      provider-selection UI (ModelConnectionPanel, stress-owned), help/docs (howToUseContent, MCPScanner),
      internal-only values (LogViewerPanel SERVICE_FILTERS value:"Bedrock" renders label not value —
      verified L243), and non-rendered error codes. liveGateway:842 "ZeroShield guard models are for
      scanning only" = intentional help copy (product term, not an id leak) — left as-is. lint 75/75,
      build clean, detector [] on both edited files. MCPConnectorPanel claim re-checked: STILL active.
      [iter+] Cross-checked modules 1.6 + 1.7 (never proven live before). BOTH data-correct: 1.6 "Isolation
      events"=14==API(module_id=1.6); 1.7 "Outputs scanned"=4==full-sweep of all 1001 security_scan (API
      ignores event_type param; 4 is honest, all in newest page). No data bug. But found + FIXED a real
      honest-loading gap (commit 88b68209): FirewallModulePage KPI summary/spotlight cards rendered computed
      "0" during initial load (board-of-zeros, looked like real empties) while the EvidenceTable already
      showed "Loading…". Added muted "—" placeholder gated on (isLoading && threatFeed empty) so bg polls/lens
      changes keep data. Verified both themes: — during load → 14 loaded. NEXT (root cause, logged): latency
      probe shows soc-kpis?period=7d = 6-8s vs threat-feed 0.45s; useFirewallData's `await Promise.allSettled`
      couples them so KPIs wait ~7s — SAME bug class as the overview fix (56ff19ca). Decouple useFirewallData
      next iter (set each state as its fetch resolves), verify all module pages. MCP claim STILL active.
      [iter+] DONE the root-cause decouple (commit 6b8b0cf2): useFirewallData now commits each state slice
      as its own fetch resolves (apply() helper) instead of awaiting Promise.allSettled — module KPIs no
      longer wait on slow soc-kpis (6-8s). Made FirewallModulePage's placeholder gate module-aware (1.1→
      socKpis!=null, others→threatFeedCount!==null) so 1.1 doesn't flash the raw-feed total and empty
      modules show honest 0 fast. LIVE: 1.6 —→14 in 163ms (was ~7s); 1.1 —→92,598 no flash; regression
      clean (1.2 1005/468/143/263/39.2% + charts, 1.5 552, 1.3 6), both themes; lint 75/75, build, detector
      []. All 5 useFirewallData consumers safe (soc-kpis timing unchanged; only feed-based values faster).
      MCP claim (claude-ralph-stress-iter1-R0) re-checked: STILL active — MCPConnectorPanel remains THE
      COMPLETE blocker. Everything else in remit continues to verify clean.
      [iter+] Exhaustive verify-only sweeps (no code change): (1) overflow @375 both themes — 1-1 + 1-3 (never
      checked at 375 before) CLEAN (0 real offenders, 12px benign gutter); ONLY overflow anywhere = 1-4
      MCPConnectorPanel server card (904px, mainScroll 529, "cp09-ens8do…", near "MCP Guardrails"),
      never_edit. So EVERY editable module surface is overflow-clean — blocker is precisely the one owned
      panel. (2) console/network @1440 all 7 tabs — 0 pageerrors, 0 console errors, 0 failed requests.
      MCP claim (claude-ralph-stress-iter1-R0) re-checked = active. Re-confirmed the server-card overflow
      root cause: bare `grid gap-3` at MCPConnectorPanel L1528 (no grid-cols-1/min-w-0) → cards size to
      max-content; break-all on URL insufficient (header row forces width).
      [iter+] FIXED org-wide-vs-scoped bug on SubModuleResultsPage flow nodes (commit c19e0a7c): the
      "Detailed Results" page (Open detailed results) showed org-wide socKpis totals for ALL modules —
      1.5 "Request/Router: 93,553" vs its 552 routing events (same class as the 1.2 fix). getModuleResults
      Config now uses scoped threatFeedCount+actionCounts for non-1.1 lanes (1.1=ingress stays org-wide);
      1.6 Risk uses scoped critical. LIVE: 1.5 →552 (both themes), 1.7 →1005/468/143/394 scoped; 1.1
      code-identical (its 0 was soc-kpis at 17-30s under F3 load). lint 75/75, build, detector no-new
      (3 pre-existing gradient/border-l-8 FPs). Noted pre-existing: 1.7 results page shows raw security_scan
      (1005) vs module page's output sub-filter (4) — separate design call. MCP claim re-checked: active.
      [iter+] Overview dashboard (?tab=firewall) verify + cross-source finding (OPEN, backend-semantics, NOT
      fixed): overview per-module cards use module-kpis endpoint; module PAGES use threat-feed source-filter
      — they DISAGREE @24h (1.2: 1110 vs 1005; 1.4: 92929 vs 4000; 1.6: 6 vs 14; 1.5 552==552). Not fabricated
      (different scopes), but a cross-surface consistency gap; canonical source is a backend/product call, so
      NOT unified from frontend (risk of substituting another arguable number + reverting verified fixes).
      Documented for backend. Overview structural verify @1440 both themes: 9 chart canvases, mainScroll 0,
      0 overflow. KPI==API values NOT confirmed this run (soc-kpis 17-30s under F3 → 9 spinners; 375 fell to
      login = env). Overview KPI==API + 375 re-verify PENDING rested backend. MCP claim: active.
      [iter+] Verified account/auth cluster CLEAN (backend-light, no F3 dependency), no fix: Profile (both
      themes 1440/768/375) — real user admin@zeroshield.io + 4 roles, org shows NAME "ZeroShield" not id:2
      (no leak), "Not set" honest empty, 3 pw fields all type=password, 0 secrets/JWT/hex, email-change
      validation, 0 overflow, 0 console err, good dark contrast. Settings — theme+notif toggles, 0 overflow,
      0 err. Login (unauth) — email+masked pw (show/hide toggle), 0 overflow, 0 err, clean both themes.
      Minor note (not fixed): login footer trust badges (99.99%/SOC2/10M+) are static marketing chrome
      (pre-auth, can't bind live) — product call, not a dashboard data-integrity bug. Backend soc-kpis STILL
      24-30s (sustained multi-session load) → overview KPI==API still pending. MCP claim: active.
      [iter+] LogDetailPage (Inspect/Scan Detail Report) NO-LEAK verified CLEAN, no fix. It renders raw
      model/output/JSON WITHOUT the app's model-id scrubbers, BUT scanned 1000+ events (routing+security_scan
      +mcp_scan) across every rendered field → 0 internal model IDs (backend sanitizes at source; routed=BYOK
      names, request IDs scrubbed zs-…). A naive sanitizer would OVER-REDACT user BYOK anthropic/claude-opus
      → "ZeroShield Model" (data-integrity regression), so NOT applied; field-specific guard-only scrub =
      backend-coordinated, flagged. Live: light/1440 + dark 1440/1024/768/375 — 0 leak, 0 overflow all
      widths, controls (Copy/Share/Export/Back), ECharts pipeline chart, honest Offline state, clean console,
      good dark contrast. MCP claim: active. Backend recovered to ~9.8s this iter (overview KPI==API still TODO).
      [iter+] Overview KPI==API VERIFIED (backend recovered to 4.4s): UI 94,597/801/552/387 == soc-kpis
      total_threats/critical_count/blocked/redacted exactly. 375 responsive VERIFIED both themes (auth-retry):
      0 real overflow, docOver=0 (12px benign gutter), charts render, honest "--" loading + Offline badge.
      Overview data-integrity + responsive now FULLY closed. No fix (renders correctly). Only OPEN items left:
      MCPConnectorPanel overflow (never_edit, active) + cross-source per-module discrepancy (backend-semantics).
      MCP claim: active.
      [iter+] ui/* primitives (all 20) verified CLEAN, no fix: detector 0-findings except Tabs border-b-2
      (=active-tab underline FP; TabsTrigger has role=tab/aria-selected/roving tabindex/focus-visible ring).
      Interactive primitives all have focus-visible:ring + disabled + hover both themes (Select = native
      wrapper w/ focus:ring, minor focus: vs focus-visible: nit, not a bug). Behavioral: Settings toggle
      flips aria-checked + persists to localStorage. Rendered in-context both themes across session. Tabs
      primitive's ONLY consumer = MCPConnectorPanel (never_edit); Firewall12 tabs are custom activeSection.
      MCP claim: active.
      [iter+] Playwright visual+behavior snapshot gate CONFIRMED in place + functional (tests/visual/,
      npm run test:visual → run.sh mints JWT → audit.mjs). Checks every surface × 2 themes × 4 widths for
      real-overflow + leak scan + chart-migration(0 recharts) + console errors; screenshots + report.json;
      exit 1 on any gate fail; module-1-4 allowOverflow (never_edit MCPConnectorPanel = warn not fail). Ran
      live on login surface (unauth, F3-independent): light+dark × 1440+375 all "ok" → "GATE PASSED — no
      visual/behavior regressions" exit 0. Full authed-suite green run is F3-throttled (env), gate itself
      sound. Completion criterion "snapshot gate in place" = SATISFIED. MCP claim: active.