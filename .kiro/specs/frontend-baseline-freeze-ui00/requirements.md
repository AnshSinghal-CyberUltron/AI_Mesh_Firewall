# Requirements Document

## Introduction

This spec covers **UI00 — "Freeze the current frontend baseline and operator-flow inventory"**, the first card of the §9 Frontend revamp track in the v3 Master Runbook (`docs/AI_MESH_MASTER_RUNBOOK_v3_BACKEND_REWRITE.md`, §9.5 / UI00, ~line 2789). UI00 is a **pure inventory and baseline-capture card**: it produces evidence artifacts that pin down exactly what the current frontend is, serves, and does, so that the subsequent visual redesign (UI01+) improves a known-good product rather than silently discarding working behavior and prior live-data integrity fixes.

UI00 **must finish before any visual implementation (UI01+) begins** and carries a hard rollback/stop rule: **"No functional changes."** This spec therefore changes no product behavior; it records, measures, and archives.

The current frontend is a React 19 + Vite 7 + Tailwind 4 single-page app (`frontend/`). The dev server runs in Docker, host-mapped to `:8180` (container `5173`), proxying `/api` → control plane (`:8100`) and `/v1` → gateway (`:8300`). Routing is **tab-based** via a `?tab=` whitelist (no resource routes yet). A durable visual harness already exists at `frontend/tests/visual/` (12 surfaces × 4 widths × 2 themes). Several items in UI00's exit gate (production-bundle identity through the staging edge, Core Web Vitals in staging, the full operator walkthrough against live control+gateway) genuinely require the **running Docker/staging stack**; this spec states those as requirements with an explicit stack dependency rather than deferring them out of scope.

This document is the **requirements phase only**. It does not design or implement anything.

## Glossary

- **UI00**: The runbook card specified here; "Freeze the current frontend baseline and operator-flow inventory."
- **Baseline_Engineer**: The system/actor (human or coding agent) producing UI00's evidence artifacts. Used as the SHALL subject throughout since UI00 outputs are artifacts, not running software behavior.
- **Frontend_App**: The React 19 + Vite 7 + Tailwind 4 application rooted at `frontend/`.
- **Local_Toolchain**: Evidence capturable without a running stack — git, `package-lock.json`, `npm run build`, source analysis of `frontend/src/`.
- **Staging_Stack**: The running Docker/staging deployment — Vite console on `:8180`, control plane on `:8100`, gateway on `:8300`, served through the staging edge.
- **Visual_Harness**: The existing `frontend/tests/visual/` suite (`audit.mjs`, `surfaces.json`, `run.sh`), invoked by `npm run test:visual`, covering 12 surfaces × 4 widths (1440/1024/768/375) × 2 themes (light/dark) with overflow, console-error, and request-count checks.
- **Surface**: One declared entry in `surfaces.json` (e.g. `overview`, `module-1-1` … `module-1-7`, `firewall-config`, `profile`, `settings`, `login`).
- **KNOWN_TABS**: The `?tab=` whitelist in `frontend/src/App.jsx`: `firewall`, `profile`, `settings`, `firewall-config`, `firewall-1-1` … `firewall-1-7`.
- **Production_Bundle**: The output of the production-bundle build command (`npm run build`, Vite), as served (baked) through the staging edge.
- **Asset_Hash**: A content hash of a served JS/CSS asset, compared against the build manifest to prove provenance.
- **New_IA**: The §9.3 route contract target information architecture: `/`, `/login`, `/app/overview`, `/app/requests`, `/app/policies`, `/app/guardrails/input`, `/app/guardrails/output`, `/app/models`, `/app/mcp-rag`, `/app/simulator`, `/app/settings`, `/app/profile`.
- **Large_Component**: A `frontend/src/` component source file larger than 15 KB.
- **Operator_Flow**: A current user-reachable capability (e.g. save a policy, view request detail, run a simulator, change model/routing, log out) with its mapped route, request IDs, and API calls.
- **Evidence_Package**: The six required UI00 deliverables: `frontend-baseline.json`, `route-and-flow-map.md`, baseline screenshots + `report.json`, `bundle-report.json`, `network-waterfall.har` (sanitized), and the component-size inventory.

## Requirements

### Requirement 1: No functional changes (hard constraint)

**User Story:** As the runbook owner, I want UI00 to produce only inventory and evidence artifacts, so that freezing the baseline never alters the product the redesign is meant to preserve.

#### Acceptance Criteria

1. THE Baseline_Engineer SHALL produce only inventory, measurement, and evidence artifacts for UI00.
2. THE Baseline_Engineer SHALL leave the behavior of every `frontend/src/` component, every route, and every API call unchanged by UI00 work.
3. WHERE UI00 adds files, THE Baseline_Engineer SHALL confine additions to evidence artifacts and non-shipping tooling that does not alter the served Production_Bundle behavior.
4. IF a provenance identity (branch, image, or Asset_Hash) is unknown or cannot be established, THEN THE Baseline_Engineer SHALL halt UI00 and record the deployment-identity gap as a blocker rather than approximating or substituting evidence.
5. WHEN UI00 completes, THE Baseline_Engineer SHALL preserve all existing visual-regression and live-data integrity fixes present on the current branch without modification.

### Requirement 2: Provenance and deployment identity (runbook item 139)

**User Story:** As a release engineer, I want the exact provenance of the frozen frontend pinned, so that the redesign references a known-good, reproducible baseline and no stale or dev bundle is mistaken for production.

#### Acceptance Criteria

1. THE Baseline_Engineer SHALL record the Frontend_App source commit SHA in `frontend-baseline.json`.
2. THE Baseline_Engineer SHALL record a content hash of `frontend/package-lock.json` in `frontend-baseline.json`.
3. THE Baseline_Engineer SHALL record the production-bundle build command (`npm run build`) and its resolved tool versions (Vite, Node) in `frontend-baseline.json`.
4. THE Baseline_Engineer SHALL record, in `frontend-baseline.json`, which branch and container image the Staging_Stack console actually serves.
5. WHEN the Staging_Stack is available, THE Baseline_Engineer SHALL record the Asset_Hash of each served JS and CSS asset from the staging edge in `frontend-baseline.json`.
6. WHERE the Staging_Stack is unavailable, THE Baseline_Engineer SHALL mark the served-Asset_Hash and served-branch/image fields as pending-stack-dependency in `frontend-baseline.json` rather than omitting them.

### Requirement 3: Route and operator-flow inventory (runbook item 140)

**User Story:** As a redesign lead, I want every current route and operator capability inventoried, so that no customer-reachable job is lost or overlooked when the IA changes.

#### Acceptance Criteria

1. THE Baseline_Engineer SHALL enumerate, in `route-and-flow-map.md`, every unauthenticated route (`/login`, `/signup` → `/login`, `/oauth/callback`, `*` → `/`).
2. THE Baseline_Engineer SHALL enumerate, in `route-and-flow-map.md`, every authenticated surface addressed by the KNOWN_TABS `?tab=` whitelist (`firewall`, `profile`, `settings`, `firewall-config`, `firewall-1-1` … `firewall-1-7`).
3. THE Baseline_Engineer SHALL record, in `route-and-flow-map.md`, the user roles and organization-selection behavior governing access to each surface.
4. THE Baseline_Engineer SHALL record, in `route-and-flow-map.md`, every major control form, simulator path, and request-detail flow reachable in the current Frontend_App.
5. THE Baseline_Engineer SHALL record, in `route-and-flow-map.md`, the authentication flow (`POST /api/auth/token/` → `{access, refresh}` in `localStorage`) and the 15-minute inactivity auto-logout behavior.
6. WHERE a component is present in source but not wired to a live route (`MCPManagerPanel`, `MCPScannerPanel`), THE Baseline_Engineer SHALL record it in `route-and-flow-map.md` as present-but-not-customer-reachable.

### Requirement 4: Visual baseline across the surface matrix (runbook item 141)

**User Story:** As a redesign reviewer, I want a complete visual baseline of every current surface, so that the redesign can be compared against the real starting point including its known defects.

#### Acceptance Criteria

1. THE Baseline_Engineer SHALL produce baseline screenshots for every Surface declared in `frontend/tests/visual/surfaces.json` at widths 1440, 1024, 768, and 375 pixels in both light and dark themes using the Visual_Harness.
2. THE Baseline_Engineer SHALL record, for each captured Surface, the console errors, layout overflow results, and network request counts reported by the Visual_Harness in `report.json`.
3. WHEN a Surface renders a known defect, THE Baseline_Engineer SHALL record the baseline for that Surface including the defect rather than substituting a passing screenshot.
4. IF any declared Surface fails to render, THEN THE Baseline_Engineer SHALL record the failure for that Surface in `report.json` rather than omitting the Surface or substituting another screenshot.
5. WHEN the Visual_Harness run completes, THE Baseline_Engineer SHALL confirm the count of captured Surfaces equals the count of Surfaces declared in `surfaces.json`.
6. WHERE the Staging_Stack (`:8180` with control `:8100` and gateway `:8300`) is required for an authenticated Surface to render data, THE Baseline_Engineer SHALL note that Surface's baseline as dependent on the running stack.

### Requirement 5: Performance and network demand baseline (runbook item 142)

**User Story:** As a performance owner, I want the current bundle, request waterfall, and demand profile measured, so that the redesign's performance budgets and splitting decisions start from real numbers.

#### Acceptance Criteria

1. THE Baseline_Engineer SHALL record initial Production_Bundle total size and per-chunk sizes (from `npm run build`) in `bundle-report.json`.
2. THE Baseline_Engineer SHALL capture the first-load request waterfall and save it as a sanitized `network-waterfall.har`.
3. THE Baseline_Engineer SHALL record the dashboard API request fan-out and the hidden-tab polling behavior observed on the overview surface in `bundle-report.json`.
4. WHEN a synthetic operator opens the overview, switches periods, hides the tab, returns, and navigates quickly, THE Baseline_Engineer SHALL capture the resulting network fan-out, polling, and request-cancellation behavior.
5. IF a duplicate or runaway request pattern is observed during the demand-profile capture, THEN THE Baseline_Engineer SHALL record the pattern and its explanation in `bundle-report.json`.
6. WHEN the Staging_Stack is available, THE Baseline_Engineer SHALL record current Core Web Vitals measured in staging in `bundle-report.json`.
7. WHERE the Staging_Stack is unavailable, THE Baseline_Engineer SHALL mark the Core Web Vitals, request-waterfall, and demand-profile fields as pending-stack-dependency rather than omitting them, while still recording bundle/chunk sizes captured locally.

### Requirement 6: Component-size and IA-destination inventory (runbook item 143)

**User Story:** As a refactor planner, I want every oversized component inventoried and classified, so that UI09's decomposition and the new IA have an explicit source-to-destination map.

#### Acceptance Criteria

1. THE Baseline_Engineer SHALL inventory every Large_Component (larger than 15 KB) under `frontend/src/` in the component-size inventory artifact.
2. THE Baseline_Engineer SHALL record, for each Large_Component, its source line count and byte size.
3. THE Baseline_Engineer SHALL classify, for each Large_Component, its product domain and its data ownership.
4. THE Baseline_Engineer SHALL classify, for each Large_Component, whether it becomes a route, a panel, or a primitive in the New_IA.
5. THE Baseline_Engineer SHALL record the known monolith targets — `MCPConnectorPanel.jsx`, `PolicyManagementPanel.jsx`, `MCPManagerPanel.jsx`, `AttackSimulatorPanel.jsx`, `src/utils/liveGateway.js`, `ModelConnectionPanel.jsx`, `pages/AIMeshFirewallOverview.jsx`, `MCPScannerPanel.jsx`, `RAGAttackTrustSimulator.jsx`, `LogDetailPage.jsx` — in the component-size inventory.
6. THE Baseline_Engineer SHALL note, for each Large_Component, any existing live-data integrity fix it carries so that the fix is preserved through the redesign.

### Requirement 7: New-IA ownership and destination mapping

**User Story:** As the IA owner, I want every current route and operator job assigned an owner and a destination in the new IA, so that no unowned critical flow remains after the redesign.

#### Acceptance Criteria

1. THE Baseline_Engineer SHALL map every current route and Operator_Flow from the Requirement 3 inventory to exactly one destination in the New_IA (`/`, `/login`, `/app/overview`, `/app/requests`, `/app/policies`, `/app/guardrails/input`, `/app/guardrails/output`, `/app/models`, `/app/mcp-rag`, `/app/simulator`, `/app/settings`, `/app/profile`).
2. THE Baseline_Engineer SHALL assign an owner to every current route and Operator_Flow in the mapping.
3. THE Baseline_Engineer SHALL record a deterministic legacy-redirect mapping from each current `?tab=` URL to its New_IA destination in `route-and-flow-map.md`.
4. IF a current route or Operator_Flow has no destination in the New_IA, THEN THE Baseline_Engineer SHALL flag it as an unowned critical flow and record it as a gate blocker.
5. WHEN the mapping completes, THE Baseline_Engineer SHALL confirm that no unowned critical flow remains.

### Requirement 8: Exit gate and evidence package

**User Story:** As the runbook gatekeeper, I want UI00's four acceptance scenarios proven and the full evidence package archived, so that UI00 can be declared complete and the redesign can begin.

#### Acceptance Criteria

1. WHEN the Staging_Stack serves the baked Production_Bundle, THE Baseline_Engineer SHALL verify that browser-served, source, and image JS/CSS Asset_Hashes all match the build manifest.
2. IF a stale or dev bundle is served, or any served asset has unknown provenance, THEN THE Baseline_Engineer SHALL fail the production-bundle-identity gate and halt UI00.
3. WHEN a synthetic organization saves a policy, views request detail, runs a simulator, changes model/routing, and logs out against the Staging_Stack, THE Baseline_Engineer SHALL map every exercised current job to its request IDs and API calls.
4. IF a current capability has no mapped user flow after the operator walkthrough, THEN THE Baseline_Engineer SHALL fail the operator-walkthrough gate and record the unmapped capability.
5. THE Baseline_Engineer SHALL archive the complete Evidence_Package: `frontend-baseline.json`, `route-and-flow-map.md`, baseline screenshots with `report.json`, `bundle-report.json`, sanitized `network-waterfall.har`, and the component-size inventory.
6. THE Baseline_Engineer SHALL record, for each of the four exit scenarios (production-bundle identity, surface matrix, operator walkthrough, demand profile), whether its evidence was captured against the Local_Toolchain or requires the Staging_Stack.
7. WHEN all Evidence_Package artifacts are archived and no unowned critical flow remains, THE Baseline_Engineer SHALL declare the UI00 baseline manifest complete.
