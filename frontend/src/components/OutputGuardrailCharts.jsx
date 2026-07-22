import { useState, useEffect, useCallback } from "react";
import { BarChart3, RefreshCw, Loader2, Eye, AlertTriangle } from "lucide-react";
import { useAuth } from "../context/AuthContext";
import { InfoTooltip } from "./InfoTooltip";
import { SafeResponsiveChart } from "./SafeResponsiveChart";
import { actionColor, OUTPUT_ACTION_COLORS } from "../constants/outputGuardColors";

// §1.7 Output Guardrail analytics — live views bound to the existing
// module-scoped endpoint /api/security/module-charts/1.7/?period=<lens>. The
// period follows the page operator lens (default 7d) so every surface agrees.
//
// Charts render via ECharts (theme-aware, canvas) behind SafeResponsiveChart's
// `option` API — axis/grid/tooltip/legend colors follow the active light/dark
// theme (see utils/chartTheme.js), replacing the old hardcoded-dark recharts
// styling that showed a dark grid + dark tooltip on the light theme.

const TIMELINE_SERIES = [
  { key: "blocked", color: OUTPUT_ACTION_COLORS.block },
  { key: "redacted", color: OUTPUT_ACTION_COLORS.redact },
  { key: "monitored", color: OUTPUT_ACTION_COLORS.monitor },
  { key: "allowed", color: OUTPUT_ACTION_COLORS.allow },
];

const cap = (s) => String(s || "").charAt(0).toUpperCase() + String(s || "").slice(1);

// hex (#rrggbb) + alpha → #rrggbbaa, for ECharts area gradients.
const withAlpha = (hex, a) =>
  `${hex}${Math.round(Math.max(0, Math.min(1, a)) * 255).toString(16).padStart(2, "0")}`;

const areaGradient = (color) => ({
  type: "linear",
  x: 0,
  y: 0,
  x2: 0,
  y2: 1,
  colorStops: [
    { offset: 0.05, color: withAlpha(color, 0.5) },
    { offset: 0.95, color: withAlpha(color, 0.05) },
  ],
});

const riskBucketColor = (range) => {
  const lo = parseInt(range, 10) || 0;
  return lo >= 80 ? "#ef4444" : lo >= 60 ? "#fb923c" : lo >= 40 ? "#f59e0b" : "#10b981";
};

function ChartFrame({ title, icon: Icon, children, className = "" }) {
  return (
    <div className={`rounded-2xl border border-slate-200 bg-white/60 p-4 dark:border-slate-700 dark:bg-slate-900/40 ${className}`}>
      <div className="mb-3 flex items-center gap-2">
        {Icon ? <Icon className="h-3.5 w-3.5 text-teal-500 dark:text-teal-400" /> : null}
        <h4 className="text-xs font-semibold uppercase tracking-wide text-slate-600 dark:text-slate-300">{title}</h4>
      </div>
      {children}
    </div>
  );
}

// ── ECharts option builders (data-identical to the prior recharts views) ──
function timelineOption(rows) {
  return {
    tooltip: { trigger: "axis" },
    legend: { bottom: 0, itemWidth: 10, itemHeight: 8, data: TIMELINE_SERIES.map((s) => cap(s.key)) },
    grid: { top: 12, right: 12, bottom: 30, left: 8, containLabel: true },
    xAxis: {
      type: "category",
      boundaryGap: false,
      data: rows.map((d) => d.time),
      axisLabel: { hideOverlap: true },
    },
    yAxis: { type: "value", minInterval: 1 },
    series: TIMELINE_SERIES.map((s) => ({
      name: cap(s.key),
      type: "line",
      stack: "total",
      smooth: true,
      showSymbol: false,
      lineStyle: { width: 1.5, color: s.color },
      itemStyle: { color: s.color },
      areaStyle: { color: areaGradient(s.color) },
      data: rows.map((d) => (typeof d[s.key] === "number" ? d[s.key] : 0)),
    })),
  };
}

function actionOption(rows) {
  return {
    tooltip: { trigger: "item", formatter: "{b}: {c} ({d}%)" },
    series: [
      {
        type: "pie",
        radius: ["46%", "72%"],
        center: ["50%", "50%"],
        avoidLabelOverlap: true,
        padAngle: 2,
        itemStyle: { borderRadius: 3 },
        label: { formatter: "{b} {d}%", fontSize: 11 },
        labelLine: { show: true, length: 8, length2: 8 },
        data: rows.map((d) => ({ name: d.name, value: d.value, itemStyle: { color: actionColor(d.name) } })),
      },
    ],
  };
}

function threatsOption(rows) {
  return {
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
    grid: { top: 8, right: 16, bottom: 4, left: 4, containLabel: true },
    xAxis: { type: "value", minInterval: 1 },
    yAxis: { type: "category", data: rows.map((d) => d.name) },
    series: [
      {
        type: "bar",
        data: rows.map((d) => ({
          value: d.value,
          itemStyle: { color: d.color || "#14b8a6", borderRadius: [0, 4, 4, 0] },
        })),
      },
    ],
  };
}

function riskOption(rows) {
  return {
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
    grid: { top: 12, right: 8, bottom: 4, left: 4, containLabel: true },
    xAxis: { type: "category", data: rows.map((d) => d.range) },
    yAxis: { type: "value", minInterval: 1 },
    series: [
      {
        type: "bar",
        data: rows.map((d) => ({
          value: d.count,
          itemStyle: { color: riskBucketColor(d.range), borderRadius: [4, 4, 0, 0] },
        })),
      },
    ],
  };
}

export function OutputGuardrailCharts({ timeRange = "7d", moduleId = "1.7" }) {
  const { fetchWithAuth } = useAuth();
  const [charts, setCharts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState(null);

  const fetchCharts = useCallback(
    async (background = false) => {
      if (background) setRefreshing(true);
      try {
        const res = await fetchWithAuth(`/api/security/module-charts/${moduleId}/?period=${timeRange}`);
        if (res.ok) {
          const data = await res.json();
          setCharts(Array.isArray(data.charts) ? data.charts : []);
          setError(null);
        } else {
          // Distinguish a backend failure from a genuinely-empty window: a bad
          // request/outage must NOT collapse into the benign "No activity" copy.
          setError(`Failed to load output analytics (HTTP ${res.status}).`);
        }
      } catch (err) {
        setError(`Failed to load output analytics: ${err?.message || "request failed"}`);
      } finally {
        setLoading(false);
        setRefreshing(false);
      }
    },
    [fetchWithAuth, timeRange, moduleId],
  );

  useEffect(() => { fetchCharts(); }, [fetchCharts]);
  useEffect(() => {
    const id = setInterval(() => fetchCharts(true), 30_000);
    return () => clearInterval(id);
  }, [fetchCharts]);

  const byKey = (k) => charts.find((c) => c.key === k);
  const timeline = byKey("event_timeline");
  const actions = byKey("action_distribution");
  const threats = byKey("threat_breakdown");
  const risk = byKey("risk_distribution");

  const numericTotal = (chart) =>
    (chart?.data || []).reduce(
      (sum, row) => sum + Object.values(row).reduce((s, v) => s + (typeof v === "number" ? v : 0), 0),
      0,
    );
  const hasData = [timeline, actions, threats, risk].some((c) => numericTotal(c) > 0);

  return (
    <div className="ai-mesh-card ai-mesh-grid-bg rounded-3xl p-6">
      <div className="mb-4 flex items-center justify-between">
        <div>
          <h3 className="flex items-center text-base font-semibold text-slate-900 dark:text-slate-100">
            Output Guardrail Analytics
            <InfoTooltip title="Output Analytics">
              {"Live views of generator-level output enforcement: outputs over time, action distribution, detected categories, and risk-score spread. Every chart honors the operator lens window above."}
            </InfoTooltip>
          </h3>
          <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
            Outputs over time, action &amp; category distribution, and risk spread ({timeRange} window)
          </p>
        </div>
        <button
          onClick={() => fetchCharts(true)}
          className="rounded-lg bg-slate-100 p-1.5 text-slate-400 transition-colors hover:bg-slate-200 dark:bg-slate-800 dark:hover:bg-slate-700"
          title="Refresh charts"
        >
          <RefreshCw className={`h-3.5 w-3.5 ${refreshing ? "animate-spin" : ""}`} />
        </button>
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-10">
          <Loader2 className="h-5 w-5 animate-spin text-teal-500" />
          <span className="ml-2 text-sm text-slate-500 dark:text-slate-400">Loading analytics…</span>
        </div>
      ) : error ? (
        <div className="flex flex-col items-center gap-3 py-10 text-center">
          <div className="flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 p-3 text-xs text-red-700 dark:border-red-800 dark:bg-red-900/20 dark:text-red-300">
            <AlertTriangle className="mt-0.5 h-4 w-4 flex-shrink-0" />
            <div>{error}</div>
          </div>
          <button
            onClick={() => fetchCharts(true)}
            className="rounded-lg border border-slate-200 px-3 py-1 text-xs font-medium text-slate-600 transition-colors hover:bg-slate-100 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
          >
            Retry
          </button>
        </div>
      ) : !hasData ? (
        <div className="py-10 text-center text-sm text-slate-500 dark:text-slate-400">
          <Eye className="mx-auto mb-2 h-8 w-8 text-slate-300 dark:text-slate-600" />
          No output-guardrail activity in the selected {timeRange} window.
        </div>
      ) : (
        <div className="space-y-4">
          {/* Outputs over time — full width stacked area */}
          {timeline && (
            <ChartFrame title="Outputs Over Time" icon={BarChart3}>
              <SafeResponsiveChart className="h-[220px]" option={timelineOption(timeline.data)} />
            </ChartFrame>
          )}

          <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
            {/* Action distribution donut */}
            {actions && (
              <ChartFrame title="Action Distribution">
                <SafeResponsiveChart className="h-[200px]" option={actionOption(actions.data)} />
              </ChartFrame>
            )}

            {/* Detected categories — horizontal bar */}
            {threats && (
              <ChartFrame title="Detected Categories">
                <SafeResponsiveChart className="h-[200px]" option={threatsOption(threats.data)} />
              </ChartFrame>
            )}

            {/* Risk score distribution — vertical bar */}
            {risk && (
              <ChartFrame title="Risk Score Spread">
                <SafeResponsiveChart className="h-[200px]" option={riskOption(risk.data)} />
              </ChartFrame>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export default OutputGuardrailCharts;
