import { useMemo, useState, cloneElement, isValidElement } from "react";
import { ArrowRight, ChevronRight, Loader2, ShieldCheck, Sparkles } from "lucide-react";
import { useFirewallData } from "../hooks/useFirewallData";
import { buildModulePageData, getModuleEvidenceEmptyMessage, getModulePageConfig } from "./firewall-module-utils";
import { HowToUse } from "./HowToUse";

function cn(...values) {
  return values.filter(Boolean).join(" ");
}

// Modules whose enforcement events fire infrequently (isolation/kill-switch,
// output guardrails) age out of a 24h window, leaving the board blank on load.
// Open them on a wider lens so operators see real recent activity by default.
const DEFAULT_TIME_RANGE_BY_MODULE = {
  "1.5": "7d",
  "1.6": "7d",
  "1.7": "7d",
};

// Control-lane panels that fetch their own data and should follow the operator
// lens window (receive a `timeRange` prop via cloneElement). Keep in sync with
// the keys assigned in firewall-submodules.jsx.
const LENS_AWARE_PANEL_KEYS = new Set(["engine-card", "output-governance", "output-charts"]);

export function FirewallModulePage({
  moduleId,
  title,
  description,
  icon: Icon,
  flowNodes,
  onViewResults,
  onViewLogDetail,
  controlPanels = [],
  simulatorPanels = [],
  inspectionPanels = [],
  secondaryPanels = [],
  footerPanels = [],
  showEvidenceSection = true,
}) {
  const [timeRange, setTimeRange] = useState(DEFAULT_TIME_RANGE_BY_MODULE[moduleId] || "24h");
  const firewallData = useFirewallData(moduleId, timeRange);
  // Only reserve a right column when there is real inspection content.
  // Flow-only sidebars (e.g. module 1.4) left a large empty gap and starved
  // control panels of width — traffic path renders full-width above instead.
  const hasInspectionSidebar = Array.isArray(inspectionPanels) && inspectionPanels.length > 0;
  const hasFlowNodes = Array.isArray(flowNodes) && flowNodes.length > 0;

  const pageConfig = getModulePageConfig(moduleId);
  const pageData = useMemo(
    () =>
      buildModulePageData(moduleId, firewallData.threatFeed, {
        socKpis: firewallData.socKpis,
        gatewayStats: firewallData.gatewayStats,
        ragPipelineKpis: firewallData.ragPipelineKpis,
        threatFeedCount: firewallData.threatFeedCount,
        threatFeedActionCounts: firewallData.threatFeedActionCounts,
      }),
    [moduleId, firewallData.threatFeed, firewallData.socKpis, firewallData.gatewayStats, firewallData.ragPipelineKpis, firewallData.threatFeedCount, firewallData.threatFeedActionCounts],
  );

  const isLoading = firewallData.loading;
  // During the FIRST load the KPI cards would otherwise render a full board of
  // computed "0"s (empty data → summarizeEvents total 0), indistinguishable from a
  // real "0 events" state. Show a muted placeholder until the card's PRIMARY source
  // has resolved. That source differs by module: 1.1's summary cards derive from the
  // (slow) soc-kpis distinct-request partition, every other module from the
  // threat-feed. Since useFirewallData now streams state in independently, gating on
  // the right source stops 1.1 from flashing the raw-feed total before soc-kpis
  // lands, and lets the feed-based modules reveal the instant the feed is in.
  // `threatFeedCount !== null` marks "feed resolved" (0 included), and prior data is
  // retained across lens changes / polls, so neither blanks good data.
  const primaryKpiReady =
    moduleId === "1.1" ? firewallData.socKpis != null : firewallData.threatFeedCount !== null;
  const awaitingFirstData = isLoading && !primaryKpiReady;

  const resolvedFooterPanels = useMemo(() => {
    if (!footerPanels?.length) return [];
    return footerPanels.map((panel) => {
      if (isValidElement(panel) && panel.key === "routing-audit") {
        return cloneElement(panel, {
          events: firewallData.threatFeed,
          loading: isLoading,
          onRefresh: () => firewallData.refetch?.({ background: true }),
        });
      }
      return panel;
    });
  }, [footerPanels, firewallData.threatFeed, firewallData.refetch, isLoading]);

  // Thread the operator-lens window into panels that fetch their own data, so
  // they stay consistent with the page KPIs/Evidence instead of a fixed window.
  // (Same clone-by-key convention as the routing-audit footer above.)
  const resolvedControlPanels = useMemo(() => {
    if (!controlPanels?.length) return controlPanels;
    return controlPanels.map((panel) =>
      isValidElement(panel) && LENS_AWARE_PANEL_KEYS.has(panel.key)
        ? cloneElement(panel, { timeRange })
        : panel,
    );
  }, [controlPanels, timeRange]);

  return (
    <div className="ai-mesh-shell space-y-8 pb-6">
      <section className="ai-mesh-card-strong ai-mesh-grid-bg ai-mesh-sheen relative overflow-hidden rounded-[30px] px-6 py-7 lg:px-8 lg:py-8">
        <div className="ai-mesh-hero-glow left-[-5rem] top-[-4rem] h-48 w-48 bg-sky-500/20" />
        <div className="ai-mesh-hero-glow bottom-[-6rem] right-[-4rem] h-56 w-56 bg-teal-500/20" />

        <div className="relative grid gap-6 xl:grid-cols-[1.2fr,0.8fr]">
          <div>
            <div className="ai-mesh-pill px-3 py-1.5 text-xs font-semibold">
              <span className="ai-mesh-dot h-2 w-2 rounded-full bg-teal-500" />
              Submodule {moduleId} · {pageConfig.badge}
            </div>

            <div className="mt-5 flex items-start gap-4">
              <div className="flex h-16 w-16 items-center justify-center rounded-[22px] bg-gradient-to-br from-sky-500 via-cyan-500 to-teal-500 text-white shadow-xl shadow-cyan-500/15">
                <Icon className="h-7 w-7" strokeWidth={2.4} />
              </div>
              <div className="max-w-3xl">
                <div className="flex flex-wrap items-center gap-3">
                  <h1 className="text-3xl font-semibold tracking-tight text-slate-950 dark:text-slate-50 md:text-[2.5rem]">{title}</h1>
                  {isLoading ? <Loader2 className="h-5 w-5 animate-spin text-teal-500" /> : null}
                </div>
                <p className="mt-3 text-sm leading-7 text-slate-600 dark:text-slate-400">{description}</p>
                <p className="mt-3 max-w-2xl text-sm leading-7 text-slate-500 dark:text-slate-400">{pageConfig.workspaceDescription}</p>
              </div>
            </div>

            <div className="mt-7 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
              {pageData.summaryCards.map((card) => (
                <SummaryCard key={card.label} {...card} loading={awaitingFirstData} />
              ))}
            </div>
          </div>

          <div className="grid gap-4">
            <div className="ai-mesh-kpi p-6">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-teal-600 dark:text-teal-300">Operator lens</div>
                  <h3 className="mt-2 text-xl font-semibold text-slate-950 dark:text-slate-50">Choose the time horizon</h3>
                </div>
                <Sparkles className="h-5 w-5 text-teal-600 dark:text-teal-300" />
              </div>

              <div className="mt-5 flex flex-wrap gap-2">
                {["1h", "6h", "24h", "7d", "30d"].map((range) => (
                  <button
                    key={range}
                    onClick={() => setTimeRange(range)}
                    className={cn(
                      "rounded-2xl px-4 py-2 text-sm font-medium transition-colors",
                      timeRange === range
                        ? "bg-teal-600 text-white dark:bg-teal-500"
                        : "border border-slate-200 bg-white/80 text-slate-600 hover:bg-white dark:border-slate-700 dark:bg-slate-900/70 dark:text-slate-300 dark:hover:bg-slate-900",
                    )}
                  >
                    {range}
                  </button>
                ))}
              </div>

              <div className="mt-6 space-y-3">
                {pageData.spotlightCards.map((card) => (
                  <QuickStatus key={card.label} {...card} loading={awaitingFirstData} />
                ))}
              </div>
            </div>

            <div className="ai-mesh-card rounded-[26px] p-5">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-teal-600 dark:text-teal-300">Operator priorities</div>
                  <h3 className="mt-2 text-lg font-semibold text-slate-950 dark:text-slate-50">What should dominate this page</h3>
                </div>
                <ShieldCheck className="h-5 w-5 text-teal-600 dark:text-teal-300" />
              </div>
              <div className="mt-4 space-y-3">
                {pageConfig.focusAreas.map((item) => (
                  <div key={item} className="rounded-2xl border border-slate-200/80 bg-white/80 px-4 py-3 text-sm leading-6 text-slate-600 dark:border-slate-700 dark:bg-slate-950/45 dark:text-slate-300">
                    {item}
                  </div>
                ))}
              </div>
              <button
                onClick={onViewResults}
                className="mt-5 inline-flex items-center gap-2 rounded-2xl bg-teal-600 px-4 py-3 text-sm font-medium text-white transition-colors hover:bg-teal-700 dark:bg-teal-500 dark:hover:bg-teal-400"
              >
                Open detailed results
                <ArrowRight className="h-4 w-4" />
              </button>
            </div>
          </div>
        </div>
      </section>

      <section>
        <HowToUse moduleId={moduleId} />
      </section>

      <section className="space-y-5">
        <SectionHeading
          eyebrow="Workflow"
          title={pageConfig.workspaceTitle}
          description={pageConfig.workspaceDescription}
        />
        {hasFlowNodes && !hasInspectionSidebar ? (
          <FlowSection flowNodes={flowNodes} summary={pageData.summary} layout="strip" />
        ) : null}
        <div className={cn("grid gap-6", hasInspectionSidebar && "xl:grid-cols-[minmax(0,1.55fr)_minmax(280px,0.85fr)]")}>
          <div className="min-w-0 space-y-6">
            {controlPanels.length > 0 ? (
              <PanelLane label={pageConfig.panelLabels.control} panels={resolvedControlPanels} />
            ) : null}
            {simulatorPanels.length > 0 ? (
              <PanelLane label={pageConfig.panelLabels.simulator} panels={simulatorPanels} />
            ) : null}
            {secondaryPanels.length > 0 ? (
              <PanelLane label={pageConfig.panelLabels.secondary} panels={secondaryPanels} />
            ) : null}
          </div>

          {hasInspectionSidebar ? (
            <div className="min-w-0 space-y-6 xl:sticky xl:top-6 xl:self-start">
              {hasFlowNodes ? (
                <FlowSection flowNodes={flowNodes} summary={pageData.summary} />
              ) : null}
              <PanelLane label={pageConfig.panelLabels.inspection} panels={inspectionPanels} />
            </div>
          ) : null}
        </div>
      </section>

      {showEvidenceSection ? (
      <section className="space-y-5">
        <SectionHeading
          eyebrow="Evidence"
          title={pageConfig.evidenceTitle}
          description={pageConfig.evidenceDescription}
          action={
            <button
              onClick={onViewResults}
              className="inline-flex items-center gap-2 text-sm font-medium text-slate-600 transition-colors hover:text-teal-600 dark:text-slate-300 dark:hover:text-teal-300"
            >
              View full results
              <ChevronRight className="h-4 w-4" />
            </button>
          }
        />

        <div className="ai-mesh-card rounded-[28px] p-6">
          <EvidenceTable
            moduleId={moduleId}
            rows={pageData.rows}
            onViewLogDetail={onViewLogDetail}
            loading={isLoading}
          />
        </div>
      </section>
      ) : null}

      {resolvedFooterPanels.length > 0 ? (
        <section className="space-y-5">
          <SectionHeading
            eyebrow="Audit trail"
            title="Routing audit trail"
            description="Detailed requested-vs-routed decisions for chat completions, including policy context and failover metadata."
          />
          <div className="space-y-5">{resolvedFooterPanels}</div>
        </section>
      ) : null}
    </div>
  );
}

function SectionHeading({ eyebrow, title, description, action }) {
  return (
    <div className="flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
      <div>
        <div className="text-[11px] font-semibold uppercase tracking-[0.2em] text-teal-600 dark:text-teal-300">{eyebrow}</div>
        <h2 className="mt-2 text-2xl font-semibold tracking-tight text-slate-950 dark:text-slate-50">{title}</h2>
        {description ? <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-600 dark:text-slate-400">{description}</p> : null}
      </div>
      {action}
    </div>
  );
}

function SummaryCard({ label, value, detail, loading }) {
  return (
    <div className="ai-mesh-kpi p-5">
      <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-slate-500 dark:text-slate-400">{label}</div>
      <div className="mt-4 text-3xl font-semibold tracking-tight text-slate-950 dark:text-slate-50">
        {loading ? <span className="text-slate-400 dark:text-slate-500" aria-hidden="true">—</span> : value}
      </div>
      <div className="mt-2 text-xs text-slate-500 dark:text-slate-400">{detail}</div>
    </div>
  );
}

function QuickStatus({ label, value, detail, loading }) {
  return (
    <div className="rounded-2xl border border-slate-200/80 bg-white/80 px-4 py-3 dark:border-slate-700 dark:bg-slate-950/45">
      <div className="text-[11px] font-medium uppercase tracking-[0.16em] text-slate-500 dark:text-slate-400">{label}</div>
      <div className="mt-2 text-2xl font-semibold text-slate-950 dark:text-slate-50">
        {loading ? <span className="text-slate-400 dark:text-slate-500" aria-hidden="true">—</span> : value}
      </div>
      <div className="mt-1 text-xs text-slate-500 dark:text-slate-400">{detail}</div>
    </div>
  );
}

function PanelLane({ label, panels }) {
  return (
    <div className="space-y-4">
      <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-teal-600 dark:text-teal-300">{label}</div>
      <div className="space-y-5">{panels}</div>
    </div>
  );
}

function FlowSection({ flowNodes = [], summary, layout = "stack" }) {
  if (!Array.isArray(flowNodes) || flowNodes.length === 0) {
    return null;
  }

  const isStrip = layout === "strip";

  return (
    <div className={cn("ai-mesh-card rounded-[28px]", isStrip ? "p-4 sm:p-5" : "p-6")}>
      <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-teal-600 dark:text-teal-300">Traffic path</div>
      <div
        className={cn(
          "mt-4",
          isStrip
            ? "grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4"
            : "space-y-3",
        )}
      >
        {flowNodes.map((node) => (
          <div
            key={`${node.label}-${node.value}`}
            className={cn(
              "rounded-2xl border border-slate-200/80 bg-white/80 dark:border-slate-700 dark:bg-slate-950/45",
              isStrip ? "px-4 py-3" : "px-4 py-4",
            )}
          >
            <div className="text-xs font-semibold uppercase tracking-[0.14em] text-slate-500 dark:text-slate-400">{node.label}</div>
            <div className={cn("font-semibold text-slate-950 dark:text-slate-50", isStrip ? "mt-1.5 text-xl" : "mt-2 text-2xl")}>
              {typeof node.format === "function" ? node.format(summary) : node.value}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function ChartCard({ title, children }) {
  return (
    <div className="ai-mesh-card rounded-[28px] p-6">
      <h3 className="text-lg font-semibold text-slate-950 dark:text-slate-50">{title}</h3>
      <div className="mt-5">{children}</div>
    </div>
  );
}

function EvidenceTable({ moduleId, rows, onViewLogDetail, loading }) {
  if (loading && rows.length === 0) {
    return <EmptyState message="Loading recent evidence..." />;
  }

  if (rows.length === 0) {
    return <EmptyState message={getModuleEvidenceEmptyMessage(moduleId)} />;
  }

  const headers = rows[0].cells.map((cell) => cell.label);

  return (
    <div className="overflow-x-auto rounded-[24px] border border-slate-200/80 dark:border-slate-700">
      <table className="w-full min-w-[760px] text-sm">
        <thead>
          <tr className="border-b border-slate-200 bg-slate-50/80 dark:border-slate-700 dark:bg-slate-900/60">
            {headers.map((header) => (
              <th key={header} className="px-4 py-3 text-left text-[11px] font-semibold uppercase tracking-[0.16em] text-slate-500 dark:text-slate-400">
                {header}
              </th>
            ))}
            <th className="px-4 py-3 text-left text-[11px] font-semibold uppercase tracking-[0.16em] text-slate-500 dark:text-slate-400">Inspect</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-200 dark:divide-slate-700">
          {rows.map((row) => (
            <tr key={row.id} className="cursor-pointer transition-colors duration-150 hover:bg-slate-50 dark:hover:bg-slate-900/50" onClick={() => onViewLogDetail?.(row.raw)}>
              {row.cells.map((cell, index) => (
                <td key={`${row.id}-${cell.label}-${index}`} className="px-4 py-3 font-mono text-xs text-slate-700 dark:text-slate-300">
                  {cell.kind === "action" ? <ActionBadge value={cell.value} /> : null}
                  {cell.kind === "severity" ? <SeverityBadge value={cell.value} /> : null}
                  {!cell.kind ? cell.value : null}
                </td>
              ))}
              <td className="px-4 py-3">
                <button
                  onClick={(event) => {
                    event.stopPropagation();
                    onViewLogDetail?.(row.raw);
                  }}
                  className="inline-flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs font-medium text-slate-600 transition-colors hover:border-teal-300 hover:text-teal-600 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300 dark:hover:border-teal-500 dark:hover:text-teal-300"
                >
                  Open
                  <ChevronRight className="h-3.5 w-3.5" />
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ActionBadge({ value }) {
  const normalized = String(value).toLowerCase();
  const map = {
    block: "bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-300 border border-red-200 dark:border-red-700",
    redact: "bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-300 border border-amber-200 dark:border-amber-700",
    allow: "bg-emerald-100 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-300 border border-emerald-200 dark:border-emerald-700",
    monitor: "bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300 border border-blue-200 dark:border-blue-700",
  };
  const cls = map[normalized] || "bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-300 border border-slate-200 dark:border-slate-600";
  return <span className={`inline-flex rounded-md px-2 py-0.5 text-xs font-semibold uppercase tracking-wide ${cls}`}>{value}</span>;
}

function SeverityBadge({ value }) {
  const number = Number(value);
  const level = !Number.isNaN(number)
    ? number >= 80
      ? "critical"
      : number >= 60
        ? "high"
        : number >= 40
          ? "medium"
          : "low"
    : String(value).toLowerCase();
  const map = {
    critical: "bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-300 border border-red-200 dark:border-red-700",
    high: "bg-orange-100 dark:bg-orange-900/30 text-orange-700 dark:text-orange-300 border border-orange-200 dark:border-orange-700",
    medium: "bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-300 border border-amber-200 dark:border-amber-700",
    low: "bg-emerald-100 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-300 border border-emerald-200 dark:border-emerald-700",
  };
  const cls = map[level] || "bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-300 border border-slate-200 dark:border-slate-600";
  return <span className={`inline-flex rounded-md px-2 py-0.5 text-xs font-semibold ${cls}`}>{value}</span>;
}

function EmptyState({ message }) {
  return <div className="flex h-[280px] items-center justify-center text-sm text-slate-500 dark:text-slate-400">{message}</div>;
}