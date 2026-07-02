# Claude Code Ralph — Frontend Hardening + Impeccable Revamp (main; 50–100 iters)
# COMPLETE only when EVERY panel + ui/* primitive is verified & fixed in BOTH themes at all 4 widths,
# charts migrated behind SafeResponsiveChart, data-integrity + no-leak proven, impeccable clean.

## Setup
- [x] 0. /impeccable init; add pre-flight claims check (mcp-parallel/claims); create FRONTEND_AUDIT.md.
      DONE iter1: PRODUCT.md + DESIGN.md written (register=product, real OKLCH tokens both themes);
      FRONTEND_AUDIT.md scaffold w/ claims protocol + VERIFY-ONLY set (MCPConnectorPanel,
      ModelConnectionPanel, OutputPipelineTimeline) + gate + baseline (lint/build green) + risks R1–R4.

## Chart migration (contained slice — do early)
- [ ] 1. Migrate SafeResponsiveChart internals recharts→ECharts (keep API identical); theme-aware colors.
- [ ] 2. uPlot for dense time-series charts (telemetry/logs/trends). Verify data-identical + interactive + faster.

## Per-surface sweep (ONE panel per iteration → verify (both themes, 4 widths) → fix → re-verify → commit)
- [ ] 3. GatewayKeyPanel     - [ ] 4. ModelGovernancePanel   - [ ] 5. RoutingGovernance/Audit
- [ ] 6. KillSwitch/ModelState - [ ] 7. MCPManager/Scanner/ScanControlMatrix
- [ ] 8. DatabaseConnection/VectorPolicy - [ ] 9. RAGFeatureTest/RAGAttackTrust/PipelineTelemetry
- [ ] 10. OutputGuardrailControls/Charts - [ ] 11. AttackSimulatorPanel - [ ] 12. PolicyManagement/Analytics
- [ ] 13. LogViewer/LogDetailPage - [ ] 14. OWASPStatsPanel - [ ] 15. Firewall12EnterprisePage - [ ] 16. HowToUse
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