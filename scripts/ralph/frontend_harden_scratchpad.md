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
- [x] 13. LogViewer/LogDetailPage - [x] 14. OWASPStatsPanel - [x] 15. Firewall12EnterprisePage - [ ] 16. HowToUse
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
- [ ] 17. VERIFY-ONLY (log, don't edit): MCPConnectorPanel, ModelConnectionPanel, pipeline-trace cards
- [ ] 18. Shared ui/* primitives (Button/Card/Dialog/Input/Select/Table/Tabs/Badge/Toast/Tooltip/…): both themes, API-compatible restyle

## Cross-cutting hardening
- [ ] 19. Data-integrity: prove no static/mock/placeholder/wrong data anywhere; every value binds to real backend.
- [ ] 20. No-leak: prove no raw key/PII/secret/topology is ever displayed.
- [ ] 21. Theme audit: every surface correct in dark AND light (contrast/focus/hover/disabled).
- [ ] 22. Responsive audit: no overflow/overlap at 1440/1024/768/375 on every surface.

## Freeze the frontend
- [ ] 23. Add Playwright visual+behavior snapshots per surface (both themes) as a regression gate.
- [ ] 24. Full re-verify pass; FRONTEND_AUDIT.md all resolved; impeccable clean; console/network clean.
      Only when ALL items done in BOTH themes at ALL widths → output <promise>COMPLETE</promise>.