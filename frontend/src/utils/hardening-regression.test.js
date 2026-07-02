/**
 * Browserless regression gate for the frontend-hardening loop.
 *
 * Runs in `npm test` / `npm run lint` (node --test, no browser, no backend) and
 * statically locks in the SPECIFIC source-level fixes from the loop so they cannot
 * be silently reverted. It complements the live Playwright gate in
 * `tests/visual/audit.mjs` (which needs the running stack).
 *
 * These assertions target invariants, not exact strings — each checks the minimal
 * substring that proves the fix is still in place.
 */
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const SRC = join(dirname(fileURLToPath(import.meta.url)), "..");
const read = (rel) => readFileSync(join(SRC, rel), "utf8");
const has = (rel, s) => read(rel).includes(s);

// --- Chart migration: these surfaces were migrated recharts -> ECharts (items 1, 9-15).
// A regression would re-introduce a recharts import. (SubmoduleResultsPage /
// SubmoduleDetailPage / AIMeshFirewallOverview are intentionally excluded — their
// recharts removal is tracked separately as item 24.)
const MIGRATED_NO_RECHARTS = [
  "components/PolicyAnalyticsPanel.jsx",
  "components/LogDetailPage.jsx",
  "components/module-specific-log-charts.jsx",
  "components/OWASPStatsPanel.jsx",
  "components/Firewall12EnterprisePage.jsx",
  "components/RAGPipelineTelemetry.jsx",
];
for (const f of MIGRATED_NO_RECHARTS) {
  test(`chart-migration: ${f} has no recharts import`, () => {
    const src = read(f);
    assert.ok(!/from\s+["']recharts["']/.test(src), `${f} must not import from "recharts" (chart migration regressed)`);
  });
}

test("chart-migration: SafeResponsiveChart supports the ECharts/uPlot option API", () => {
  const src = read("components/SafeResponsiveChart.jsx");
  assert.ok(src.includes("option") && src.includes("uplot"), "SafeResponsiveChart must accept `option` (ECharts) and `uplot` props");
});

// --- Responsive fixes (item 22): panel headers/toolbars/grids that overflowed <main>
// at 375 must keep their wrap/scroll containment.
test("responsive: SubModuleResultsPage flow pipeline is a horizontal scroll container", () => {
  const src = read("components/SubmoduleResultsPage.jsx");
  assert.ok(src.includes("overflow-x-auto"), "flow-pipeline card must keep overflow-x-auto so it scrolls within its card, not <main>");
  assert.ok(src.includes("flex-wrap"), "Detailed Records toolbar / pagination must keep flex-wrap");
});
test("responsive: CollectionManagerPanel create-form stacks on mobile", () => {
  assert.ok(has("components/rag/CollectionManagerPanel.jsx", "grid-cols-1 sm:grid-cols-3"), "create form must be grid-cols-1 sm:grid-cols-3 (was grid-cols-3, overflowed at 375)");
});
test("responsive: ModelStatePanel header wraps", () => {
  assert.ok(has("components/ModelStatePanel.jsx", "flex flex-wrap items-center justify-between"), "ModelStatePanel header must flex-wrap");
});
test("responsive: OutputGovernancePanel header wraps", () => {
  assert.ok(has("components/OutputGovernancePanel.jsx", "flex flex-wrap items-center justify-between"), "OutputGovernancePanel header must flex-wrap");
});
test("responsive: HowToUse steps grid avoids the auto-min-width trap", () => {
  const src = read("components/HowToUse.jsx");
  assert.ok(src.includes("grid-cols-1") && src.includes("min-w-0"), "HowToUse must keep grid-cols-1 base + min-w-0 (item 16 clipping fix)");
});

// --- Data-integrity: real backend health, not hardcoded green (item 19).
test("data-integrity: Header/Sidebar bind status to useBackendHealth", () => {
  assert.ok(has("components/layout/Header.jsx", "useBackendHealth"), "Header must derive its connection badge from useBackendHealth");
  assert.ok(has("components/layout/Sidebar.jsx", "useBackendHealth"), "Sidebar must derive system status from useBackendHealth");
});

// --- Theme contrast (item 21): the inverted-slate pair renders muted text DARKER on
// the dark card; it must not reappear in the files that were fixed.
const NO_INVERTED_SLATE = [
  "components/RAGPipelineTelemetry.jsx",
  "components/ui/EmptyState.jsx",
  "components/layout/Sidebar.jsx",
];
for (const f of NO_INVERTED_SLATE) {
  test(`theme: ${f} has no inverted-slate text-slate-400 dark:text-slate-500`, () => {
    assert.ok(!read(f).includes("text-slate-400 dark:text-slate-500"), `${f} must not use the inverted-slate pair (low-contrast in both themes)`);
  });
}
