import { useEffect, useRef, useState } from "react";
import {
  ArrowRight,
  ChevronRight,
  FileSearch,
  AlertTriangle,
  CheckCircle,
  Clock,
  MoreVertical,
  Eye,
  Download,
  Copy,
  Loader2,
  X,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import {
  LineChart,
  Line,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  AreaChart,
  Area,
  PieChart,
  Pie,
  Cell,
  RadarChart,
  Radar,
  PolarGrid,
  PolarAngleAxis,
  PolarRadiusAxis,
} from "recharts";
import { SafeResponsiveChart } from "./SafeResponsiveChart";
import { createPortal } from "react-dom";
import { copyToClipboard } from "../lib/clipboard";
import { useTheme } from "../context/ThemeContext";

function cn(...values) {
  return values.filter(Boolean).join(" ");
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
  const raw = String(value).toLowerCase();
  const isNumber = !Number.isNaN(Number(value));
  const level = isNumber
    ? Number(value) >= 80
      ? "critical"
      : Number(value) >= 60
        ? "high"
        : Number(value) >= 40
          ? "medium"
          : "low"
    : raw;
  const map = {
    critical: "bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-300 border border-red-200 dark:border-red-700",
    high: "bg-orange-100 dark:bg-orange-900/30 text-orange-700 dark:text-orange-300 border border-orange-200 dark:border-orange-700",
    medium: "bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-300 border border-amber-200 dark:border-amber-700",
    low: "bg-emerald-100 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-300 border border-emerald-200 dark:border-emerald-700",
  };
  const cls = map[level] || "bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-300 border border-slate-200 dark:border-slate-600";
  return <span className={`inline-flex rounded-md px-2 py-0.5 text-xs font-semibold ${cls}`}>{value}</span>;
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

function ChartCard({ title, description, children, className }) {
  return (
    <div className={cn("ai-mesh-card rounded-[28px] p-6", className)}>
      <div className="mb-5">
        <h3 className="text-lg font-semibold text-slate-950 dark:text-slate-50">{title}</h3>
        {description ? <p className="mt-2 text-sm leading-6 text-slate-600 dark:text-slate-400">{description}</p> : null}
      </div>
      {children}
    </div>
  );
}

function ResponsiveChart({ height, children, placeholderMessage = "Preparing chart..." }) {
  const containerRef = useRef(null);
  const [isReady, setIsReady] = useState(false);

  useEffect(() => {
    const element = containerRef.current;
    if (!element) return undefined;

    let rafId = 0;
    const update = () => {
      const rect = element.getBoundingClientRect();
      setIsReady(rect.width > 24 && rect.height > 24);
    };

    update();

    const observer = new ResizeObserver(() => {
      if (rafId) cancelAnimationFrame(rafId);
      rafId = requestAnimationFrame(update);
    });
    observer.observe(element);

    return () => {
      observer.disconnect();
      if (rafId) cancelAnimationFrame(rafId);
    };
  }, []);

  return (
    <div ref={containerRef} style={{ height }}>
      {isReady ? (
        <SafeResponsiveChart className="h-full w-full">
          {children}
        </SafeResponsiveChart>
      ) : (
        <EmptyState message={placeholderMessage} compact={height <= 260} />
      )}
    </div>
  );
}

export function SubModuleDetailPage({
  moduleId,
  title,
  description,
  icon: Icon,
  flowNodes,
  metrics,
  previewColumns,
  previewData,
  onViewResults,
  onViewLogDetail,
  timeSeriesData: timeSeriesProp,
  actionDistributionData: actionDistProp,
  latencyData: latencyProp,
  healthRadarData: healthRadarProp,
  statusMetrics: statusMetricsProp,
  loading,
  onTimeRangeChange,
  configurationPanels,
  simulatorPanels,
  inspectionPanels,
  children,
}) {
  const [timeRange, setTimeRange] = useState("24h");
  const [openDropdown, setOpenDropdown] = useState(null);
  const [dropdownPos, setDropdownPos] = useState({ top: 0, left: 0 });
  const [jsonPreviewRow, setJsonPreviewRow] = useState(null);

  const themeContext = typeof useTheme === "function" ? useTheme() : null;
  const resolvedTheme = themeContext?.resolvedTheme || "light";
  const isDark = resolvedTheme === "dark";

  const chartTheme = isDark
    ? {
        grid: "#334155",
        axis: "#94a3b8",
        tooltipBg: "rgba(15, 23, 42, 0.96)",
        tooltipBorder: "#475569",
        tooltipText: "#e5e7eb",
        areaPrimary: "#14b8a6",
        areaSecondary: "#8b5cf6",
        barFill: "#0ea5e9",
      }
    : {
        grid: "#e2e8f0",
        axis: "#64748b",
        tooltipBg: "#f9fafb",
        tooltipBorder: "#cbd5e1",
        tooltipText: "#0f172a",
        areaPrimary: "#14b8a6",
        areaSecondary: "#8b5cf6",
        barFill: "#0ea5e9",
      };

  const timeSeriesData = timeSeriesProp || [];
  const actionDistributionData = actionDistProp || [];
  const latencyRanges = latencyProp || [];
  const healthRadarData = healthRadarProp || [];
  const totalActions = actionDistributionData.reduce((sum, item) => sum + item.value, 0);

  const hasStructuredPanels =
    (Array.isArray(configurationPanels) && configurationPanels.length > 0) ||
    (Array.isArray(simulatorPanels) && simulatorPanels.length > 0) ||
    (Array.isArray(inspectionPanels) && inspectionPanels.length > 0);
  const primaryKey = timeSeriesData[0]?.primary !== undefined ? "primary" : "allowed";
  const secondaryKey = timeSeriesData[0]?.secondary !== undefined ? "secondary" : "blocked";

  const handleTimeRangeChange = (range) => {
    setTimeRange(range);
    onTimeRangeChange?.(range);
  };

  useEffect(() => {
    if (!jsonPreviewRow) return undefined;

    const handleKeyDown = (event) => {
      if (event.key === "Escape") {
        setJsonPreviewRow(null);
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [jsonPreviewRow]);

  return (
    <div className="ai-mesh-shell space-y-8 pb-6">
      <section className="ai-mesh-card-strong ai-mesh-grid-bg ai-mesh-sheen relative overflow-hidden rounded-[30px] px-6 py-7 lg:px-8 lg:py-8">
        <div className="ai-mesh-hero-glow left-[-4rem] top-[-3rem] h-44 w-44 bg-cyan-500/20" />
        <div className="ai-mesh-hero-glow bottom-[-6rem] right-[-3rem] h-52 w-52 bg-teal-500/20" />

        <div className="relative grid gap-6 xl:grid-cols-[1.25fr,0.75fr]">
          <div>
            <div className="ai-mesh-pill px-3 py-1.5 text-xs font-semibold">
              <span className="ai-mesh-dot h-2 w-2 rounded-full bg-teal-500" />
              Submodule {moduleId}
            </div>

            <div className="mt-5 flex items-start gap-4">
              <div className="flex h-16 w-16 items-center justify-center rounded-[22px] bg-gradient-to-br from-sky-500 via-cyan-500 to-teal-500 text-white shadow-xl shadow-cyan-500/15">
                <Icon className="h-7 w-7" strokeWidth={2.4} />
              </div>
              <div>
                <div className="flex flex-wrap items-center gap-3">
                  <h1 className="text-3xl font-semibold tracking-tight text-slate-950 dark:text-slate-50 md:text-[2.4rem]">{title}</h1>
                  {loading ? <Loader2 className="h-5 w-5 animate-spin text-teal-500" /> : null}
                </div>
                <p className="mt-3 max-w-3xl text-sm leading-7 text-slate-600 dark:text-slate-400">{description}</p>
              </div>
            </div>

            <div className="mt-7 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
              {metrics.map((metric, index) => (
                <div key={`${metric.label}-${index}`} className="ai-mesh-kpi p-5">
                  <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-slate-500 dark:text-slate-400">{metric.label}</div>
                  <div className="mt-4 text-3xl font-semibold tracking-tight text-slate-950 dark:text-slate-50">{metric.value}</div>
                  <div className={cn("mt-2 text-xs font-medium", metric.trend === "up" ? "text-emerald-600 dark:text-emerald-300" : "text-rose-600 dark:text-rose-300")}>
                    {metric.change ? `${metric.change} vs baseline` : "Stable in current window"}
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div className="grid gap-4">
            <div className="ai-mesh-kpi p-6">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-teal-600 dark:text-teal-300">Time scope</div>
                  <h3 className="mt-2 text-xl font-semibold text-slate-950 dark:text-slate-50">Adjust the operator lens</h3>
                </div>
                <Sparkles className="h-5 w-5 text-teal-600 dark:text-teal-300" />
              </div>

              <div className="mt-5 flex flex-wrap gap-2">
                {["1h", "6h", "24h", "7d", "30d"].map((range) => (
                  <button
                    key={range}
                    onClick={() => handleTimeRangeChange(range)}
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
                <QuickStatus label="Recent events" value={previewData.length.toLocaleString()} detail="Rows available in the current activity preview" />
                <QuickStatus label="Charts available" value={moduleChartData.length.toLocaleString()} detail={moduleChartsLoading ? "Refreshing analytics" : "Module-specific visualizations loaded"} />
                <QuickStatus label="Telemetry state" value={loading ? "Syncing" : "Live"} detail="Polling and realtime updates are active" />
              </div>
            </div>

            <div className="ai-mesh-card rounded-[26px] p-5">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-teal-600 dark:text-teal-300">Operator action</div>
                  <h3 className="mt-2 text-lg font-semibold text-slate-950 dark:text-slate-50">Detailed investigation</h3>
                </div>
                <ShieldCheck className="h-5 w-5 text-teal-600 dark:text-teal-300" />
              </div>
              <p className="mt-3 text-sm leading-6 text-slate-600 dark:text-slate-400">
                Move from this overview into the full results stream when you need complete logs, payload context, and deeper triage.
              </p>
              <button
                onClick={onViewResults}
                className="mt-5 inline-flex items-center gap-2 rounded-2xl bg-teal-600 px-4 py-3 text-sm font-medium text-white transition-colors hover:bg-teal-700 dark:bg-teal-500 dark:hover:bg-teal-400"
              >
                <FileSearch className="h-4 w-4" />
                View All Results & Detailed Logs
                <ChevronRight className="h-4 w-4" />
              </button>
            </div>
          </div>
        </div>
      </section>

      {hasStructuredPanels ? (
        <section className="space-y-5">
          <SectionHeading
            eyebrow="Main canvas"
            title="Configuration, simulation, and inspection"
            description="Existing controls are reorganized into an operator-friendly layout without changing behavior."
          />
          <div className="grid gap-6 xl:grid-cols-[1.45fr,0.95fr]">
            <div className="space-y-6">
              {Array.isArray(configurationPanels) && configurationPanels.length > 0 ? (
                <div className="space-y-4">
                  <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-teal-600 dark:text-teal-300">Configuration zone</div>
                  <div className="space-y-5">{configurationPanels}</div>
                </div>
              ) : null}

              {Array.isArray(simulatorPanels) && simulatorPanels.length > 0 ? (
                <div className="space-y-4">
                  <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-teal-600 dark:text-teal-300">Live simulator zone</div>
                  <div className="space-y-5">{simulatorPanels}</div>
                </div>
              ) : null}
            </div>

            {Array.isArray(inspectionPanels) && inspectionPanels.length > 0 ? (
              <div className="space-y-4 xl:sticky xl:top-6 xl:self-start">
                <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-teal-600 dark:text-teal-300">Inspection zone</div>
                <div className="space-y-5">{inspectionPanels}</div>
              </div>
            ) : null}
          </div>
        </section>
      ) : (
        children
      )}

      <section className="space-y-5">
        <SectionHeading
          eyebrow="Flow"
          title="AI traffic path"
          description="Trace how this module evaluates requests from first signal to enforcement decision."
        />
        <div className="ai-mesh-card rounded-[28px] p-6">
          <div className="flex flex-col gap-4 xl:flex-row xl:items-center xl:justify-between">
            {flowNodes.map((node, index) => (
              <div key={`${node.label}-${index}`} className="flex flex-1 items-center gap-4 min-w-0">
                <FlowNode label={node.label} value={node.value} color={node.color} />
                {index < flowNodes.length - 1 ? <div className="ai-mesh-stage-line hidden xl:block" /> : null}
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="space-y-5">
        <SectionHeading
          eyebrow="Core telemetry"
          title="Traffic, enforcement, and system posture"
          description="The primary charts stay consistent across submodules so operators can compare lanes quickly."
        />

        <div className="grid gap-6 xl:grid-cols-2">
          <ChartCard title="Request volume over time" description="Incoming demand or accepted traffic over the selected window.">
            {timeSeriesData.length > 0 ? (
              <ResponsiveChart height={280}>
                <AreaChart data={timeSeriesData}>
                  <defs>
                    <linearGradient id="modulePrimary" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor={chartTheme.areaPrimary} stopOpacity={0.35} />
                      <stop offset="95%" stopColor={chartTheme.areaPrimary} stopOpacity={0.02} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke={chartTheme.grid} strokeOpacity={0.18} />
                  <XAxis dataKey="time" stroke={chartTheme.axis} tick={{ fontSize: 11, fill: chartTheme.axis }} />
                  <YAxis stroke={chartTheme.axis} tick={{ fontSize: 11, fill: chartTheme.axis }} />
                  <Tooltip
                    contentStyle={{
                      border: `1px solid ${chartTheme.tooltipBorder}`,
                      borderRadius: "14px",
                      backgroundColor: chartTheme.tooltipBg,
                      color: chartTheme.tooltipText,
                    }}
                  />
                  <Area type="monotone" dataKey={primaryKey} stroke={chartTheme.areaPrimary} strokeWidth={2.4} fill="url(#modulePrimary)" />
                </AreaChart>
              </ResponsiveChart>
            ) : (
              <EmptyState message="Awaiting request-volume data" />
            )}
          </ChartCard>

          <ChartCard title="Enforcement actions" description="Blocks, redactions, or other interventions generated by this submodule.">
            {timeSeriesData.length > 0 ? (
              <ResponsiveChart height={280}>
                <LineChart data={timeSeriesData}>
                  <CartesianGrid strokeDasharray="3 3" stroke={chartTheme.grid} strokeOpacity={0.18} />
                  <XAxis dataKey="time" stroke={chartTheme.axis} tick={{ fontSize: 11, fill: chartTheme.axis }} />
                  <YAxis stroke={chartTheme.axis} tick={{ fontSize: 11, fill: chartTheme.axis }} />
                  <Tooltip
                    contentStyle={{
                      border: `1px solid ${chartTheme.tooltipBorder}`,
                      borderRadius: "14px",
                      backgroundColor: chartTheme.tooltipBg,
                      color: chartTheme.tooltipText,
                    }}
                  />
                  <Line type="monotone" dataKey={secondaryKey} stroke={chartTheme.areaSecondary} strokeWidth={2.2} dot={false} />
                </LineChart>
              </ResponsiveChart>
            ) : (
              <EmptyState message="Awaiting enforcement data" />
            )}
          </ChartCard>
        </div>

        <div className="grid gap-6 xl:grid-cols-3">
          <ChartCard title="Action distribution" description="Relative share of allow, block, redact, and monitor outcomes.">
            {actionDistributionData.length > 0 ? (
              <ResponsiveChart height={260}>
                <PieChart>
                  <Pie
                    data={actionDistributionData}
                    cx="50%"
                    cy="50%"
                    innerRadius={48}
                    outerRadius={84}
                    dataKey="value"
                    label={({ name, value }) => `${name} ${totalActions ? ((value / totalActions) * 100).toFixed(0) : 0}%`}
                    labelLine={false}
                  >
                    {actionDistributionData.map((entry, index) => <Cell key={`cell-${index}`} fill={entry.color} />)}
                  </Pie>
                  <Tooltip
                    formatter={(value) => [value.toLocaleString(), "Count"]}
                    contentStyle={{
                      border: `1px solid ${chartTheme.tooltipBorder}`,
                      borderRadius: "14px",
                      backgroundColor: chartTheme.tooltipBg,
                      color: chartTheme.tooltipText,
                    }}
                  />
                </PieChart>
              </ResponsiveChart>
            ) : (
              <EmptyState message="Awaiting action mix" compact />
            )}
          </ChartCard>

          <ChartCard title="Latency distribution" description="Latency buckets across the chosen time range.">
            {latencyRanges.length > 0 ? (
              <ResponsiveChart height={260}>
                <BarChart data={latencyRanges}>
                  <CartesianGrid strokeDasharray="3 3" stroke={chartTheme.grid} strokeOpacity={0.18} />
                  <XAxis
                    dataKey="range"
                    stroke={chartTheme.axis}
                    tick={{ fontSize: 10, fill: chartTheme.axis }}
                    angle={-15}
                    textAnchor="end"
                    height={60}
                  />
                  <YAxis stroke={chartTheme.axis} tick={{ fontSize: 11, fill: chartTheme.axis }} />
                  <Tooltip
                    contentStyle={{
                      border: `1px solid ${chartTheme.tooltipBorder}`,
                      borderRadius: "14px",
                      backgroundColor: chartTheme.tooltipBg,
                      color: chartTheme.tooltipText,
                    }}
                  />
                  <Bar dataKey="count" fill={chartTheme.barFill} radius={[8, 8, 0, 0]} />
                </BarChart>
              </ResponsiveChart>
            ) : (
              <EmptyState message="Awaiting latency buckets" compact />
            )}
          </ChartCard>

          <ChartCard title="System health score" description="A compact radar view of this module's current operating condition.">
            {healthRadarData.length > 0 ? (
              <ResponsiveChart height={260}>
                <RadarChart data={healthRadarData}>
                  <PolarGrid stroke={chartTheme.grid} strokeOpacity={0.22} />
                  <PolarAngleAxis dataKey="metric" stroke={chartTheme.axis} tick={{ fontSize: 11, fill: chartTheme.axis }} />
                  <PolarRadiusAxis angle={90} domain={[0, 100]} stroke={chartTheme.axis} tick={{ fontSize: 10, fill: chartTheme.axis }} />
                  <Radar name="Score" dataKey="value" stroke={chartTheme.areaPrimary} fill={chartTheme.areaPrimary} fillOpacity={0.45} />
                  <Tooltip
                    contentStyle={{
                      border: `1px solid ${chartTheme.tooltipBorder}`,
                      borderRadius: "14px",
                      backgroundColor: chartTheme.tooltipBg,
                      color: chartTheme.tooltipText,
                    }}
                  />
                </RadarChart>
              </ResponsiveChart>
            ) : (
              <EmptyState message="Awaiting health signals" compact />
            )}
          </ChartCard>
        </div>

        <ChartCard title="Multi-metric trend analysis" description="Overlay total traffic with interventions to reveal divergence, drift, or sudden policy pressure.">
          {timeSeriesData.length > 0 ? (
            <ResponsiveChart height={320}>
              <LineChart data={timeSeriesData}>
                <CartesianGrid strokeDasharray="3 3" stroke={chartTheme.grid} strokeOpacity={0.18} />
                <XAxis dataKey="time" stroke={chartTheme.axis} tick={{ fontSize: 11, fill: chartTheme.axis }} />
                <YAxis stroke={chartTheme.axis} tick={{ fontSize: 11, fill: chartTheme.axis }} />
                <Tooltip
                  contentStyle={{
                    border: `1px solid ${chartTheme.tooltipBorder}`,
                    borderRadius: "14px",
                    backgroundColor: chartTheme.tooltipBg,
                    color: chartTheme.tooltipText,
                  }}
                />
                <Line
                  type="monotone"
                  dataKey={primaryKey}
                  stroke={chartTheme.areaPrimary}
                  strokeWidth={2.5}
                  dot={{ r: 3, fill: chartTheme.areaPrimary }}
                  name="Total requests"
                />
                <Line
                  type="monotone"
                  dataKey={secondaryKey}
                  stroke={chartTheme.areaSecondary}
                  strokeWidth={2}
                  dot={{ r: 3, fill: chartTheme.areaSecondary }}
                  name="Enforcements"
                />
              </LineChart>
            </ResponsiveChart>
          ) : (
            <EmptyState message="Awaiting trend telemetry" />
          )}
        </ChartCard>
      </section>

      <section className="space-y-5">
        <SectionHeading
          eyebrow="Status"
          title="Operational health"
          description="Three consistent service indicators for quick triage before drilling into module-specific charts."
        />
        <div className="grid gap-4 md:grid-cols-3">
          <StatusCard icon={CheckCircle} title="Success rate" value={statusMetricsProp?.successRate || "--"} description="Requests processed successfully" color="emerald" />
          <StatusCard icon={Clock} title="Avg response time" value={statusMetricsProp?.avgResponseTime || "--"} description="P95 latency across all requests" color="blue" />
          <StatusCard icon={AlertTriangle} title="Active alerts" value={statusMetricsProp?.activeAlerts || "--"} description="Requiring attention within 24h" color="amber" />
        </div>
      </section>

      <section className="space-y-5">
        <SectionHeading
          eyebrow="Activity preview"
          title="Recent events"
          description="A compact live stream of the most recent module events, with fast access to raw JSON and full log detail."
          action={<span className="text-sm text-slate-500 dark:text-slate-400">Showing {Math.min(previewData.length, 5)} of {previewData.length} records</span>}
        />

        <div className="ai-mesh-card rounded-[28px] p-6">
          {loading ? (
            <div className="flex items-center justify-center py-14">
              <Loader2 className="h-6 w-6 animate-spin text-teal-500" />
              <span className="ml-3 text-sm text-slate-500 dark:text-slate-400">Loading events...</span>
            </div>
          ) : previewData.length === 0 ? (
            <EmptyState message="No events found for this time range" />
          ) : (
            <div className="overflow-x-auto rounded-[24px] border border-slate-200/80 dark:border-slate-700">
              {/* L9: responsive min-width — on small screens the table can shrink
                  further (the overflow-x-auto wrapper still scrolls if needed)
                  rather than forcing a fixed 780px internal scroll. */}
              <table className="w-full min-w-[600px] sm:min-w-[700px] md:min-w-[780px] text-sm">
                <thead>
                  <tr className="bg-slate-50/80 dark:bg-slate-900/60 border-b border-slate-200 dark:border-slate-700">
                    {previewColumns.map((column, index) => (
                      <th key={`${column}-${index}`} className="px-4 py-3 text-left text-[11px] font-semibold uppercase tracking-[0.16em] text-slate-500 dark:text-slate-400">
                        {column}
                      </th>
                    ))}
                    <th className="w-16 px-4 py-3 text-left text-[11px] font-semibold uppercase tracking-[0.16em] text-slate-500 dark:text-slate-400">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-200 dark:divide-slate-700">
                  {previewData.slice(0, 5).map((row, rowIdx) => {
                    const displayValues = Object.values(row).slice(0, previewColumns.length);
                    const rowKey = row.id || row.request_id || row.timestamp || row.event_id || `${moduleId}-${rowIdx}`;

                    return (
                      <tr key={rowKey} className="transition-colors duration-150 hover:bg-slate-50 dark:hover:bg-slate-900/50">
                        {displayValues.map((cell, cellIdx) => {
                          const normalizedColumn = String(previewColumns[cellIdx] || "").toLowerCase();
                          const displayValue = typeof cell === "string" ? cell : JSON.stringify(cell);

                          if (normalizedColumn.includes("action")) {
                            return (
                              <td key={cellIdx} className="px-4 py-3">
                                <ActionBadge value={displayValue} />
                              </td>
                            );
                          }

                          if (normalizedColumn.includes("severity") || normalizedColumn.includes("risk")) {
                            return (
                              <td key={cellIdx} className="px-4 py-3">
                                <SeverityBadge value={displayValue} />
                              </td>
                            );
                          }

                          return (
                            <td key={cellIdx} className="px-4 py-3 font-mono text-xs text-slate-700 dark:text-slate-300">
                              {displayValue}
                            </td>
                          );
                        })}

                        <td className="relative px-4 py-3">
                          <button
                            aria-expanded={openDropdown === rowKey}
                            aria-haspopup="menu"
                            aria-label="Open row actions"
                            onClick={(event) => {
                              event.stopPropagation();
                              if (openDropdown === rowKey) {
                                setOpenDropdown(null);
                                return;
                              }
                              const rect = event.currentTarget.getBoundingClientRect();
                              const top = Math.min(rect.bottom + 4, window.innerHeight - 220);
                              const left = Math.max(8, rect.right - 208);
                              setDropdownPos({ top, left });
                              setOpenDropdown(rowKey);
                            }}
                            className="rounded-lg p-1 text-slate-500 transition-colors hover:bg-slate-200 hover:text-slate-700 dark:text-slate-400 dark:hover:bg-slate-700 dark:hover:text-slate-200"
                          >
                            <MoreVertical className="h-4 w-4" />
                          </button>

                          {openDropdown === rowKey ? createPortal(
                            <>
                              <button
                                type="button"
                                aria-label="Close row actions"
                                className="fixed inset-0 z-[60] cursor-default bg-transparent"
                                onClick={() => setOpenDropdown(null)}
                              />
                              <div
                                role="menu"
                                className="fixed z-[70] w-52 rounded-2xl border border-slate-200 bg-white shadow-xl dark:border-slate-700 dark:bg-slate-800"
                                style={{ top: `${dropdownPos.top}px`, left: `${dropdownPos.left}px` }}
                              >
                                <DropdownAction
                                  icon={Eye}
                                  iconClassName="text-teal-600"
                                  label="View Details"
                                  rounded="rounded-t-2xl"
                                  onClick={() => {
                                    setOpenDropdown(null);
                                    onViewLogDetail?.(row);
                                  }}
                                />
                                <DropdownAction
                                  icon={Eye}
                                  iconClassName="text-sky-600"
                                  label="View JSON"
                                  onClick={() => {
                                    setOpenDropdown(null);
                                    setJsonPreviewRow(row);
                                  }}
                                />
                                <DropdownAction
                                  icon={Copy}
                                  iconClassName="text-slate-500 dark:text-slate-400"
                                  label="Copy JSON"
                                  onClick={() => {
                                    setOpenDropdown(null);
                                    copyToClipboard(JSON.stringify(row, null, 2));
                                  }}
                                />
                                <DropdownAction
                                  icon={Download}
                                  iconClassName="text-slate-500 dark:text-slate-400"
                                  label="Download"
                                  rounded="rounded-b-2xl"
                                  onClick={() => {
                                    setOpenDropdown(null);
                                    downloadRow(row, moduleId);
                                  }}
                                />
                              </div>
                            </>,
                            document.body,
                          ) : null}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </section>

      {jsonPreviewRow ? createPortal(
        <div
          aria-modal="true"
          role="dialog"
          className="fixed inset-0 z-[80] flex items-center justify-center bg-black/50 backdrop-blur-sm"
          onClick={() => setJsonPreviewRow(null)}
        >
          <div className="m-4 flex max-h-[80vh] w-full max-w-2xl flex-col rounded-[28px] border border-slate-200 bg-white shadow-2xl dark:border-slate-700 dark:bg-slate-800" onClick={(event) => event.stopPropagation()}>
            <div className="flex items-center justify-between border-b border-slate-200 p-4 dark:border-slate-700">
              <div>
                <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-teal-600 dark:text-teal-300">Event JSON</div>
                <h3 className="mt-1 text-base font-semibold text-slate-950 dark:text-slate-50">Raw event payload</h3>
              </div>
              <div className="flex items-center gap-2">
                <button
                  onClick={() => copyToClipboard(JSON.stringify(jsonPreviewRow, null, 2))}
                  className="inline-flex items-center gap-1.5 rounded-xl bg-slate-100 px-3 py-1.5 text-xs font-medium text-slate-700 transition-colors hover:bg-slate-200 dark:bg-slate-700 dark:text-slate-200 dark:hover:bg-slate-600"
                >
                  <Copy className="h-3.5 w-3.5" />
                  Copy
                </button>
                <button aria-label="Close JSON preview" onClick={() => setJsonPreviewRow(null)} className="rounded-lg p-1 text-slate-500 transition-colors hover:bg-slate-100 dark:hover:bg-slate-700">
                  <X className="h-5 w-5" />
                </button>
              </div>
            </div>
            <div className="flex-1 overflow-auto p-4">
              <pre className="whitespace-pre-wrap break-all rounded-2xl bg-slate-50 p-4 text-xs text-slate-700 dark:bg-slate-950/50 dark:text-slate-300">
                {JSON.stringify(jsonPreviewRow, null, 2)}
              </pre>
            </div>
          </div>
        </div>,
        document.body,
      ) : null}
    </div>
  );
}

function downloadRow(row, moduleId) {
  const payload = JSON.stringify(row, null, 2);
  const blob = new Blob([payload], { type: "application/json;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `module-${moduleId.replace(".", "-")}-event.json`;
  document.body.appendChild(anchor);
  anchor.click();
  document.body.removeChild(anchor);
  URL.revokeObjectURL(url);
}

function QuickStatus({ label, value, detail }) {
  return (
    <div className="rounded-2xl border border-slate-200/80 bg-white/80 px-4 py-3 dark:border-slate-700 dark:bg-slate-950/45">
      <div className="text-[11px] font-medium uppercase tracking-[0.16em] text-slate-500 dark:text-slate-400">{label}</div>
      <div className="mt-2 text-2xl font-semibold text-slate-950 dark:text-slate-50">{value}</div>
      <div className="mt-1 text-xs text-slate-500 dark:text-slate-400">{detail}</div>
    </div>
  );
}

function EmptyState({ message, compact = false }) {
  return (
    <div className={cn("flex items-center justify-center text-sm text-slate-500 dark:text-slate-400", compact ? "h-[260px]" : "h-[280px]")}>
      {message}
    </div>
  );
}

function DropdownAction({ icon: Icon, iconClassName, label, onClick, rounded }) {
  return (
    <button
      role="menuitem"
      onClick={onClick}
      className={cn(
        "flex w-full items-center gap-3 px-4 py-2.5 text-sm text-slate-700 transition-colors hover:bg-slate-50 dark:text-slate-300 dark:hover:bg-slate-700",
        rounded,
      )}
    >
      <Icon className={cn("h-4 w-4", iconClassName)} />
      <span>{label}</span>
    </button>
  );
}

function FlowNode({ label, value, color }) {
  const colorClasses = {
    blue: "from-blue-500 to-indigo-600",
    teal: "from-teal-500 to-cyan-600",
    purple: "from-purple-500 to-pink-600",
    emerald: "from-emerald-500 to-green-600",
    amber: "from-amber-500 to-orange-600",
    orange: "from-orange-500 to-red-600",
    red: "from-red-500 to-rose-600",
    cyan: "from-cyan-500 to-teal-600",
    indigo: "from-indigo-500 to-purple-600",
  };

  return (
    <div
      className={`ai-mesh-flow-node group min-w-[180px] bg-gradient-to-br ${colorClasses[color] || colorClasses.teal} px-5 py-4 text-white`}
      title={`${label}: ${value}`}
    >
      <div className="text-xs font-semibold uppercase tracking-[0.14em] opacity-85">{label}</div>
      <div className="mt-2 text-2xl font-semibold tracking-tight">{value}</div>
      <div className="pointer-events-none mt-2 text-[11px] opacity-0 transition-opacity duration-200 group-hover:opacity-90">
        Hover detail: {label} stage telemetry is currently {value}.
      </div>
    </div>
  );
}

function StatusCard({ icon: Icon, title, value, description, color }) {
  const colorClasses = {
    blue: "bg-blue-500",
    teal: "bg-teal-500",
    purple: "bg-purple-500",
    emerald: "bg-emerald-500",
    amber: "bg-amber-500",
    orange: "bg-orange-500",
    red: "bg-red-500",
    cyan: "bg-cyan-500",
    indigo: "bg-indigo-500",
  };

  return (
    <div className="ai-mesh-card rounded-[24px] p-5">
      <div className="flex items-center gap-4">
        <div className={`flex h-11 w-11 flex-shrink-0 items-center justify-center rounded-2xl ${colorClasses[color] || colorClasses.teal}`}>
          <Icon className="h-5 w-5 text-white" />
        </div>
        <div className="min-w-0 flex-1">
          <h4 className="text-sm font-semibold text-slate-950 dark:text-slate-50">{title}</h4>
          <p className="text-xs text-slate-600 dark:text-slate-400">{description}</p>
        </div>
      </div>
      <div className="mt-4 text-3xl font-semibold tracking-tight text-slate-950 dark:text-slate-50">{value}</div>
    </div>
  );
}
