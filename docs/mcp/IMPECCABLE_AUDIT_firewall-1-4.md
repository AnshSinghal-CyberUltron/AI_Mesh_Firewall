# Impeccable Audit — Submodule 1.4 (Context Assembly & MCP Guardrails)

`/impeccable init + audit` · target: `frontend/src/components/MCPConnectorPanel.jsx`
(+ `MCPScanControlMatrix.jsx`, `PolicyManagementPanel.jsx`) · route `?tab=firewall-1-4`.

`init` — PRODUCT.md present (ZeroShield security control plane; register=`product`;
principles: *every number real or honestly absent · never leak · state you can trust*).
No init needed. This is the `audit` step: code-level technical scoring, document only —
CP44 addresses.

## Audit Health Score

| # | Dimension | Score | Key Finding |
|---|-----------|-------|-------------|
| 1 | Accessibility | 3/4 | 22 aria-labels, semantic Buttons (0 div-onClick), Radix tab roles — BUT 3 icon buttons are 32px (< 44px touch min) and muted `text-slate-400/500` on tinted surfaces needs a contrast pass |
| 2 | Performance | 3/4 | 12 `useCallback`, 1 bounded 15s poll, no layout thrash — BUT **0 `useMemo`** (derived lists like `executableTools`/filtered tools recompute each render) and the event list renders up to 500 rows unvirtualized |
| 3 | Responsive | 3/4 | No horizontal overflow at 1440/1024/768/375 (CP42-proven), flex/grid — the 32px touch targets are the only gap |
| 4 | Theming | 4/4 | **0 hard-coded hex**, 111 `dark:` variants, dark+light verified end-to-end (CP42) — full token system, dark mode correct |
| 5 | Anti-Patterns | 3/4 | **0 gradient text, 0 glassmorphism-as-decoration** (1 legit sticky-bar `backdrop-blur`), no hero-metric/AI-slop tells — BUT **24 `<Card>`** = card-heavy layout (DESIGN.md: "Cards are the lazy answer"); the server list + tool list + summary are all cards |
| **Total** | | **16/20** | **Good — approaching excellent. The prior hardening (CP01–42) already secured data honesty; remaining work is structural + polish.** |

## Findings by dimension (grounded)

### 1. Accessibility (3/4)
- ✅ 22 `aria-label`s on icon buttons/inputs; `<Button>` everywhere (0 raw `<div onClick>`); Radix `TabsTrigger` (role=tab); `focus-visible:ring-2` focus rings; Tooltips on icon-only actions.
- ⚠️ **Touch targets**: 3× `h-8 w-8` (32px) icon buttons (sync/details/delete on server cards) are below the 44×44 min — fails WCAG 2.5.5 on touch.
- ⚠️ **Contrast**: heavy use of `text-slate-400`/`text-slate-500` for secondary copy on `bg-slate-50`/tinted surfaces — the classic "muted gray on near-white" risk; needs a ≥4.5:1 verification pass (placeholders too).
- ⚠️ Only 2 explicit `role=` (rest implicit via Radix/semantic) — acceptable but the expandable tool-controls region could use `aria-expanded`.

### 2. Performance (3/4)
- ✅ `useCallback` on all loaders (12); a single bounded `setInterval` (15s) for observability polling; CP26 added graceful 503 retry.
- ⚠️ **0 `useMemo`**: `executableTools` (filter), the tools-per-server map, and `connectedCount`/`toolsDiscovered` reductions recompute every render. Memoize.
- ⚠️ The event list renders up to `limit=500` rows without virtualization — fine now, but a windowed list (react-window) would scale.

### 3. Responsive (3/4)
- ✅ CP42 proved zero horizontal overflow at 1440/1024/768/375 in both themes; layout uses flex + responsive grids.
- ⚠️ Same 32px touch-target gap as A11y (counts here too).

### 4. Theming (4/4)
- ✅ **Zero hard-coded hex** — everything is a Tailwind token; 111 `dark:` variants; both themes verified working (CP42). This is the strongest dimension.

### 5. Anti-Patterns (3/4)
- ✅ No gradient text, no decorative glassmorphism, no hero-metric marketing template, no AI-slop color palette — matches PRODUCT.md anti-references (this is instrumentation, not a wellness app).
- ⚠️ **Card density**: 24 `<Card>` — the server list, tool cards, execution panel, and summary are all boxed cards. Per DESIGN.md/skill guidance, some of these (e.g. the server list) would read as denser, more scannable **rows/table** — instrumentation an operator stakes an incident on, not a card gallery.

## Cross-cutting (beyond the 5 dims)

- 🔴 **Maintainability**: `MCPConnectorPanel.jsx` is **2428 lines** (repo CLAUDE.md caps files at 500). It holds 6 tab renderers + modal + execution + observability + policy filter. This is the #1 structural debt — split into per-tab sub-components (`ServersTab`, `ToolDiscoveryTab`, `ToolExecutionTab`, `ObservabilityTab`) + the already-separate `MCPScanControlMatrix`/`PolicyManagementPanel`.
- ✅ **Data honesty (PRODUCT.md principles 1–3)**: already SATISFIED by CP01–42 — every stage/count binds to real backend data (CP31/32 telemetry, CP24 counters), honest empty/error states (CP16–19 clean errors, CP26 graceful 503), and no secret/PII/topology leakage (CP16 sanitizer, CP18 staff-only dev channel). The revamp must PRESERVE this, not regress it.

## Prioritized backlog for CP44 (revamp)
1. **Split** the 2428-line panel into per-tab sub-components (maintainability + testability).
2. **Server list → dense rows/table** (reduce card density; scannable instrumentation).
3. **Touch targets** 32px → ≥44px on icon actions.
4. **Contrast pass** on muted secondary text/placeholders (≥4.5:1).
5. **Memoize** derived lists (`useMemo`) + consider a windowed event list.
6. **PRESERVE** all CP01–42 behavior (real data, honest states, no leakage, both themes, every control works) — the revamp is structural/visual, NOT a functional rewrite.

**Verdict**: 16/20. The 1.4 page is functionally impeccable after CP01–42; the revamp is a targeted structural + visual-craft pass, not a rebuild that risks the verified behavior.
