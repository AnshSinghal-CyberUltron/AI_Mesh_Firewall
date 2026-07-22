import { useEffect, useState, useCallback, useRef, useMemo } from "react";
import { motion, useReducedMotion } from "motion/react";
import {
  ShieldAlert,
  Zap,
  GitBranch,
  Database,
  Eye,
  Shield,
  AlertTriangle,
  Filter,
  Activity,
  ChevronRight,
  Loader2,
  ArrowRight,
  Settings2,
  ShieldCheck,
  RefreshCw,
} from "lucide-react";
import { useAuth } from "../context/AuthContext";
import { useRealtimeNotifications } from "../hooks/useRealtimeNotifications";
import { OWASPStatsPanel } from "../components/OWASPStatsPanel";
import { PolicyAnalyticsPanel } from "../components/PolicyAnalyticsPanel";
import { SafeResponsiveChart } from "../components/SafeResponsiveChart";
import { formatTrendBucketLabel } from "../utils/chartLabels";

const PERIOD_INTAKE_HINTS = {
  "1h": "Last hour intake across the mesh",
  "6h": "Last 6 hours intake across the mesh",
  "24h": "24 hour intake across the mesh",
  "7d": "7 day intake across the mesh",
  "30d": "30 day intake across the mesh",
};

function useCompactViewport(maxWidth = 900) {
  const [compact, setCompact] = useState(() => window.innerWidth <= maxWidth);

  useEffect(() => {
    const media = window.matchMedia(`(max-width: ${maxWidth}px)`);
    const handleChange = () => setCompact(media.matches);
    handleChange();
    media.addEventListener("change", handleChange);
    return () => media.removeEventListener("change", handleChange);
  }, [maxWidth]);

  return compact;
}

const MODULE_COLORS = {
  blue: {
    gradient: "from-sky-500 via-cyan-500 to-blue-600",
    soft: "from-sky-50 to-cyan-50 dark:from-sky-950/40 dark:to-slate-900",
    ring: "ring-sky-200/80 dark:ring-sky-500/20",
    accent: "text-sky-700 dark:text-sky-300",
    dot: "bg-sky-500",
    hex: "#0ea5e9",
  },
  teal: {
    gradient: "from-teal-500 via-cyan-500 to-emerald-500",
    soft: "from-teal-50 to-cyan-50 dark:from-teal-950/40 dark:to-slate-900",
    ring: "ring-teal-200/80 dark:ring-teal-500/20",
    accent: "text-teal-700 dark:text-teal-300",
    dot: "bg-teal-500",
    hex: "#14b8a6",
  },
  violet: {
    gradient: "from-violet-500 via-fuchsia-500 to-indigo-600",
    soft: "from-violet-50 to-fuchsia-50 dark:from-violet-950/40 dark:to-slate-900",
    ring: "ring-violet-200/80 dark:ring-violet-500/20",
    accent: "text-violet-700 dark:text-violet-300",
    dot: "bg-violet-500",
    hex: "#8b5cf6",
  },
  amber: {
    gradient: "from-amber-500 via-orange-500 to-rose-500",
    soft: "from-amber-50 to-orange-50 dark:from-amber-950/40 dark:to-slate-900",
    ring: "ring-amber-200/80 dark:ring-amber-500/20",
    accent: "text-amber-700 dark:text-amber-300",
    dot: "bg-amber-500",
    hex: "#f59e0b",
  },
  emerald: {
    gradient: "from-emerald-500 via-teal-500 to-cyan-600",
    soft: "from-emerald-50 to-teal-50 dark:from-emerald-950/40 dark:to-slate-900",
    ring: "ring-emerald-200/80 dark:ring-emerald-500/20",
    accent: "text-emerald-700 dark:text-emerald-300",
    dot: "bg-emerald-500",
    hex: "#10b981",
  },
  orange: {
    gradient: "from-orange-500 via-amber-500 to-red-500",
    soft: "from-orange-50 to-amber-50 dark:from-orange-950/40 dark:to-slate-900",
    ring: "ring-orange-200/80 dark:ring-orange-500/20",
    accent: "text-orange-700 dark:text-orange-300",
    dot: "bg-orange-500",
    hex: "#f97316",
  },
  red: {
    gradient: "from-rose-500 via-red-500 to-orange-500",
    soft: "from-rose-50 to-red-50 dark:from-rose-950/40 dark:to-slate-900",
    ring: "ring-rose-200/80 dark:ring-rose-500/20",
    accent: "text-rose-700 dark:text-rose-300",
    dot: "bg-rose-500",
    hex: "#ef4444",
  },
};

function SectionHeading({ eyebrow, title, description, action }) {
  return (
    <div className="flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
      <div>
        {eyebrow ? (
          <div className="mb-2 text-[11px] font-semibold uppercase tracking-[0.22em] text-teal-600 dark:text-teal-300">
            {eyebrow}
          </div>
        ) : null}
        <h2 className="text-2xl font-semibold tracking-tight text-slate-950 dark:text-slate-50">{title}</h2>
        {description ? <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-600 dark:text-slate-400">{description}</p> : null}
      </div>
      {action}
    </div>
  );
}

function HeroMetric({ icon: Icon, label, value, hint, tone, loading }) {
  const tones = {
    teal: "text-teal-700 dark:text-teal-300 bg-teal-500/10 border-teal-500/20",
    red: "text-rose-700 dark:text-rose-300 bg-rose-500/10 border-rose-500/20",
    blue: "text-sky-700 dark:text-sky-300 bg-sky-500/10 border-sky-500/20",
    amber: "text-amber-700 dark:text-amber-300 bg-amber-500/10 border-amber-500/20",
  };

  return (
    <div className="ai-mesh-kpi p-5">
      <div className="flex items-start justify-between gap-3">
        <div className={`inline-flex h-11 w-11 items-center justify-center rounded-2xl border ${tones[tone] || tones.teal}`}>
          <Icon className="h-5 w-5" />
        </div>
        {loading ? <Loader2 className="mt-1 h-4 w-4 animate-spin text-teal-500" /> : null}
      </div>
      <div className="mt-6 text-3xl font-semibold tracking-tight text-slate-950 dark:text-slate-50">{value}</div>
      <div className="mt-2 text-sm font-medium text-slate-700 dark:text-slate-200">{label}</div>
      <div className="mt-1 text-xs text-slate-500 dark:text-slate-400">{hint}</div>
    </div>
  );
}

function CommandShortcut({ icon: Icon, eyebrow, title, description, onClick, cta }) {
  return (
    <button
      onClick={onClick}
      className="ai-mesh-card group relative overflow-hidden rounded-[24px] p-5 text-left transition-transform duration-200 hover:-translate-y-0.5"
    >
      <div className="absolute inset-0 bg-gradient-to-br from-teal-500/8 via-transparent to-sky-500/8 opacity-80" />
      <div className="relative">
        <div className="flex items-center justify-between gap-3">
          <div className="inline-flex h-11 w-11 items-center justify-center rounded-2xl bg-slate-950 text-white dark:bg-slate-100 dark:text-slate-950">
            <Icon className="h-5 w-5" />
          </div>
          <ArrowRight className="h-4 w-4 text-slate-400 transition-transform duration-200 group-hover:translate-x-1" />
        </div>
        <div className="mt-5 text-[11px] font-semibold uppercase tracking-[0.2em] text-teal-600 dark:text-teal-300">{eyebrow}</div>
        <div className="mt-2 text-lg font-semibold text-slate-950 dark:text-slate-50">{title}</div>
        <p className="mt-2 text-sm leading-6 text-slate-600 dark:text-slate-400">{description}</p>
        <div className="mt-4 text-sm font-medium text-slate-950 dark:text-slate-100">{cta}</div>
      </div>
    </button>
  );
}

function SubModuleCard({ id, title, icon: Icon, color, summary, metrics, chartData, curveSubtitle = "Pressure over 24h", onViewDetails }) {
  const tone = MODULE_COLORS[color] || MODULE_COLORS.teal;

  return (
    <button
      onClick={onViewDetails}
      className={`ai-mesh-card group relative overflow-hidden rounded-[26px] bg-gradient-to-br ${tone.soft} p-5 text-left ring-1 ${tone.ring} transition-transform duration-200 hover:-translate-y-1`}
    >
      <div className="absolute right-0 top-0 h-28 w-28 rounded-full bg-white/30 blur-3xl dark:bg-white/5" />
      <div className="relative space-y-5">
        <div className="flex items-start justify-between gap-4">
          <div className="flex items-start gap-3">
            <div className={`flex h-12 w-12 items-center justify-center rounded-2xl bg-gradient-to-br ${tone.gradient} text-white shadow-lg`}>
              <Icon className="h-5 w-5" strokeWidth={2.4} />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <span className={`inline-flex items-center gap-2 rounded-full px-2.5 py-1 text-[11px] font-semibold ${tone.accent} bg-white/80 dark:bg-slate-900/70`}>
                  <span className={`h-1.5 w-1.5 rounded-full ${tone.dot} ai-mesh-dot`} />
                  {id}
                </span>
              </div>
              <h3 className="mt-3 text-base font-semibold leading-6 text-slate-950 dark:text-slate-50">{title}</h3>
              <p className="mt-2 text-sm leading-6 text-slate-600 dark:text-slate-400">{summary}</p>
            </div>
          </div>
          <ChevronRight className="mt-1 h-4 w-4 text-slate-400 transition-transform duration-200 group-hover:translate-x-1" />
        </div>

        <div className="grid grid-cols-2 gap-3">
          {metrics.map((metric) => (
            <div key={metric.label} className="rounded-2xl border border-white/70 bg-white/75 px-3.5 py-3 dark:border-slate-800 dark:bg-slate-950/45">
              <div className="text-[11px] font-medium uppercase tracking-[0.16em] text-slate-500 dark:text-slate-400">{metric.label}</div>
              <div className="mt-2 text-xl font-semibold text-slate-950 dark:text-slate-50">{metric.value}</div>
              {metric.change ? <div className="mt-1 text-xs text-slate-500 dark:text-slate-400">{metric.change}</div> : null}
            </div>
          ))}
        </div>

        <div className="rounded-2xl border border-white/60 bg-white/70 px-4 py-3 dark:border-slate-800 dark:bg-slate-950/45">
          <div className="mb-2 flex items-center justify-between text-[11px] font-medium uppercase tracking-[0.14em] text-slate-500 dark:text-slate-400">
            <span>Pressure Curve</span>
            <span>{curveSubtitle}</span>
          </div>
          {/* Dense time-series sparkline via uPlot (canvas, fast) — index x keeps
              recharts' even spacing; single area series in the module tone. */}
          <SafeResponsiveChart
            className="h-[72px] w-full"
            uplot={{
              sparkline: true,
              time: false,
              data: [
                (chartData || []).map((_, i) => i),
                (chartData || []).map((d) => Number(d?.value) || 0),
              ],
              series: [{ label: "Pressure", stroke: tone.hex, area: true, width: 2.2 }],
            }}
          />
        </div>
      </div>
    </button>
  );
}

// ─── Attack-vector color palette ────────────────────────────────────────────
const VECTOR_COLORS = {
  "Prompt Injection": "#ef4444",
  "Data Leakage":     "#f59e0b",
  "Jailbreak":        "#8b5cf6",
  "Goal Hijacking":   "#ec4899",
  "Tool Overreach":   "#14b8a6",
};
const VECTOR_KEYS = ["Prompt Injection", "Data Leakage", "Jailbreak", "Goal Hijacking", "Tool Overreach"];

// ─── Shared tooltip ──────────────────────────────────────────────────────────
// ─── Shared chart card wrapper ────────────────────────────────────────────────
function OverviewChartCard({ eyebrow, title, action, children }) {
  return (
    <div className="ai-mesh-card rounded-[28px] p-6">
      <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-teal-600 dark:text-teal-300">{eyebrow}</div>
          <h3 className="mt-2 text-xl font-semibold text-slate-950 dark:text-slate-50">{title}</h3>
        </div>
        {action ?? null}
      </div>
      {children}
    </div>
  );
}

// ─── Attack Vector Trend stacked area chart ──────────────────────────────────
function AttackVectorTrendChart({ data, compact = false }) {
  const empty = !data || data.length === 0;
  return (
    <OverviewChartCard
      eyebrow="Attack intelligence"
      title="Threat vector activity over time"
      action={
        <div className="flex flex-wrap gap-x-4 gap-y-1.5">
          {VECTOR_KEYS.map((k) => (
            <span key={k} className="flex items-center gap-1.5 text-[11px] text-slate-500 dark:text-slate-400">
              <span className="h-2 w-2 shrink-0 rounded-full" style={{ backgroundColor: VECTOR_COLORS[k] }} />
              {k}
            </span>
          ))}
        </div>
      }
    >
      {empty ? (
        <div className="flex h-[300px] items-center justify-center text-sm text-slate-400">No attack events recorded in this period</div>
      ) : (
        // Overlapping (non-stacked) multi-series threat areas via uPlot — canvas,
        // drag-to-zoom; index x + xLabels preserve the time axis; hover shows each
        // vector's raw value (data-identical to the prior recharts areas).
        <SafeResponsiveChart
          className={`${compact ? "h-[240px]" : "h-[300px]"} w-full`}
          uplot={{
            time: false,
            xLabels: data.map((d) => d.time),
            data: [
              data.map((_, i) => i),
              ...VECTOR_KEYS.map((k) => data.map((d) => Number(d[k]) || 0)),
            ],
            series: VECTOR_KEYS.map((k) => ({ label: k, stroke: VECTOR_COLORS[k], area: true, width: 2 })),
          }}
        />
      )}
    </OverviewChartCard>
  );
}

// ─── Attack Vector Distribution donut ────────────────────────────────────────
function AttackVectorDistributionChart({ data }) {
  const total = data.reduce((s, d) => s + d.value, 0);
  const hasData = total > 0;

  // ECharts donut (theme-aware via the registered zs-light/zs-dark theme); the
  // in-slice % label mirrors the prior recharts custom label (hidden under 5%).
  const donutOption = useMemo(() => ({
    tooltip: { trigger: "item", formatter: "{b}: {c} ({d}%)" },
    series: [{
      type: "pie", radius: ["55%", "86%"], center: ["50%", "50%"], padAngle: 3,
      avoidLabelOverlap: true,
      label: { show: true, position: "inside", formatter: (p) => (p.percent >= 5 ? `${Math.round(p.percent)}%` : ""), color: "#fff", fontSize: 11, fontWeight: 600 },
      labelLine: { show: false },
      data: data.map((d) => ({ name: d.name, value: d.value, itemStyle: { color: d.fill } })),
    }],
  }), [data]);

  return (
    <OverviewChartCard eyebrow="Threat mix" title="Attack vector distribution">
      <div className="flex flex-col gap-5 md:flex-row md:items-center">
        <div className="mx-auto shrink-0">
          {hasData ? (
            <SafeResponsiveChart className="h-[220px] w-[220px]" option={donutOption} />
          ) : (
            <div className="flex h-[220px] w-[220px] items-center justify-center text-sm text-slate-400">No data yet</div>
          )}
        </div>

        <div className="flex-1 space-y-2.5">
          {data.map((item) => {
            const pct = total > 0 ? Math.round((item.value / total) * 100) : 0;
            return (
              <div key={item.name}>
                <div className="mb-1 flex items-center justify-between text-sm">
                  <span className="flex items-center gap-2 text-slate-700 dark:text-slate-300">
                    <span className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: item.fill }} />
                    {item.name}
                  </span>
                  <span className="font-semibold tabular-nums text-slate-950 dark:text-slate-50">
                    {item.value.toLocaleString()} <span className="text-xs text-slate-400">({pct}%)</span>
                  </span>
                </div>
                <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
                  <div
                    className="h-full rounded-full transition-all duration-500"
                    style={{ width: `${pct}%`, backgroundColor: item.fill }}
                  />
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </OverviewChartCard>
  );
}

// ─── Module comparison grouped bar chart ──────────────────────────────────────
function ModuleComparisonChart({ data, compact = false }) {
  // Grouped bar (ECharts; theme-aware). Total events (teal) vs Interventions (rose).
  const barOption = useMemo(() => ({
    grid: { top: 10, right: 8, bottom: compact ? 6 : 30, left: 4, containLabel: true },
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
    xAxis: { type: "category", data: data.map((d) => d.module), axisLabel: { fontSize: 10, interval: 0, rotate: compact ? 0 : 15 } },
    yAxis: { type: "value", name: compact ? "" : "Events", nameTextStyle: { fontSize: 10 }, axisLabel: { fontSize: 11 } },
    series: [
      { name: "Total events", type: "bar", barMaxWidth: 40, itemStyle: { color: "#14b8a6", borderRadius: [6, 6, 0, 0] }, data: data.map((d) => d.requests) },
      { name: "Interventions", type: "bar", barMaxWidth: 40, itemStyle: { color: "#f43f5e", borderRadius: [6, 6, 0, 0] }, data: data.map((d) => d.interventions) },
    ],
  }), [data, compact]);
  return (
    <OverviewChartCard
      eyebrow="Cross-module"
      title="Traffic and interventions by module"
      action={
        <div className="flex items-center gap-4 text-xs text-slate-500 dark:text-slate-400">
          <span className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-sm bg-teal-500" /> Total events</span>
          <span className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-sm bg-rose-500" /> Interventions</span>
        </div>
      }
    >
      <SafeResponsiveChart className={`${compact ? "h-[260px]" : "h-[300px]"} w-full`} option={barOption} />
    </OverviewChartCard>
  );
}

// ─── Module block-rate horizontal bar chart ───────────────────────────────────
function ModuleBlockRateChart({ data }) {
  const getRateColor = (rate) => {
    if (rate >= 50) return "#ef4444";
    if (rate >= 20) return "#f59e0b";
    return "#14b8a6";
  };

  return (
    <OverviewChartCard eyebrow="Enforcement posture" title="Intervention rate by module">
      <div className="space-y-3">
        {data.map((item) => (
          <div key={item.id}>
            <div className="mb-1.5 flex items-center justify-between text-sm">
              <span className="flex items-center gap-2 text-slate-700 dark:text-slate-300">
                <span className="inline-flex h-5 w-8 items-center justify-center rounded text-[10px] font-bold text-white" style={{ backgroundColor: getRateColor(item.blockRate) }}>
                  {item.id}
                </span>
                {item.name}
              </span>
              <span className="font-semibold tabular-nums text-slate-950 dark:text-slate-50">
                {item.blockRate}%
              </span>
            </div>
            <div className="h-2 w-full overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
              <div
                className="h-full rounded-full transition-all duration-700"
                style={{ width: `${Math.max(item.blockRate, 1)}%`, backgroundColor: getRateColor(item.blockRate) }}
              />
            </div>
          </div>
        ))}
      </div>
      <div className="mt-4 flex items-center gap-4 text-[11px] text-slate-500 dark:text-slate-400">
        <span className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-full bg-teal-500" /> Low (&lt;20%)</span>
        <span className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-full bg-amber-500" /> Moderate (20–50%)</span>
        <span className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-full bg-rose-500" /> High (&ge;50%)</span>
      </div>
    </OverviewChartCard>
  );
}

function GlobalTrafficOverview({ socKpis, enforcementSeries = [], intakeTotal = 0, loading, compact = false }) {
  const total = socKpis?.total_threats || 0;
  const blocked = socKpis?.blocked || 0;
  const redacted = socKpis?.redacted || 0;
  const allowed = Math.max(0, total - blocked - redacted);

  // hasData is derived from real intake (module 1.1 total), NOT the array length —
  // module-trends always returns a full bucket array, so length is always > 0.
  const hasData = intakeTotal > 0;
  const nonZeroBuckets = enforcementSeries.filter((d) => d.allowed + d.blocked + d.redacted > 0).length;
  const sparse = hasData && nonZeroBuckets > 0 && nonZeroBuckets <= 3;

  const actionData = [
    { name: "Allowed", value: allowed || 0, color: "#10b981" },
    { name: "Blocked", value: blocked || 0, color: "#ef4444" },
    { name: "Redacted", value: redacted || 0, color: "#f59e0b" },
  ];

  const ENF_SERIES = [
    { key: "allowed", name: "Allowed / Monitored", color: "#10b981" },
    { key: "blocked", name: "Blocked", color: "#ef4444" },
    { key: "redacted", name: "Redacted", color: "#f59e0b" },
  ];

  // Enforcement-posture donut (ECharts; theme-aware). In-slice % label hidden under 6%.
  const actionDonutOption = useMemo(() => ({
    tooltip: { trigger: "item", formatter: "{b}: {c} ({d}%)" },
    series: [{
      type: "pie", radius: ["52%", "82%"], center: ["50%", "50%"], padAngle: 4,
      avoidLabelOverlap: true,
      label: { show: true, position: "inside", formatter: (p) => (p.percent >= 6 ? `${Math.round(p.percent)}%` : ""), color: "#fff", fontSize: 11, fontWeight: 700 },
      labelLine: { show: false },
      data: actionData.map((d) => ({ name: d.name, value: d.value, itemStyle: { color: d.color } })),
    }],
  }), [actionData]);

  return (
    <div className="grid gap-6 xl:grid-cols-[1.45fr,0.8fr]">
      <div className="ai-mesh-card rounded-[28px] p-6">
        <div className="mb-5 flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
          <div>
            <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-teal-600 dark:text-teal-300">Live Telemetry</div>
            <h3 className="mt-2 text-xl font-semibold text-slate-950 dark:text-slate-50">Enforcement actions over time</h3>
          </div>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs text-slate-500 dark:text-slate-400">
            {ENF_SERIES.map((s) => (
              <span key={s.key} className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: s.color }} /> {s.name}</span>
            ))}
          </div>
        </div>
        {loading ? (
          <div className="h-[280px] w-full animate-pulse rounded-2xl bg-slate-100 dark:bg-slate-800/50" />
        ) : !hasData ? (
          <div className="flex h-[280px] flex-col items-center justify-center gap-2 rounded-2xl border border-dashed border-slate-200 px-6 text-center dark:border-slate-700">
            <Activity className="h-6 w-6 text-slate-300 dark:text-slate-600" />
            <p className="text-sm text-slate-500 dark:text-slate-400">No enforcement events in this window — the chart populates as traffic flows.</p>
            <p className="text-xs text-slate-500 dark:text-slate-400">Try a wider lens (7d / 30d) using the time selector above.</p>
          </div>
        ) : (
          // Dense stacked telemetry via uPlot (canvas, fast, drag-to-zoom).
          // index x + xLabels keeps the bucket-label axis; tooltip shows raw
          // per-series values (data-identical to the prior recharts stack).
          <SafeResponsiveChart
            className="h-[280px]"
            uplot={{
              stacked: true,
              time: false,
              xLabels: enforcementSeries.map((d) => d.time),
              data: [
                enforcementSeries.map((_, i) => i),
                enforcementSeries.map((d) => Number(d.allowed) || 0),
                enforcementSeries.map((d) => Number(d.blocked) || 0),
                enforcementSeries.map((d) => Number(d.redacted) || 0),
              ],
              series: ENF_SERIES.map((s) => ({ label: s.name, stroke: s.color, area: true, width: 1.8 })),
            }}
          />
        )}
        {sparse && (
          <p className="mt-2 text-center text-[11px] text-slate-500 dark:text-slate-400">Sparse window — {nonZeroBuckets} active interval{nonZeroBuckets === 1 ? "" : "s"}. Widen the lens for more context.</p>
        )}
      </div>

      <div className="ai-mesh-card rounded-[28px] p-6">
          <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-teal-600 dark:text-teal-300">Distribution</div>
          <h3 className="mt-2 text-xl font-semibold text-slate-950 dark:text-slate-50">Enforcement posture</h3>
          <p className="mt-2 text-sm leading-6 text-slate-600 dark:text-slate-400">Breakdown of how the firewall responds to every request — allow, block, or sanitise.</p>

          <div className="mt-5 flex items-center justify-center rounded-[24px] border border-slate-200/80 bg-white/75 p-4 dark:border-slate-700 dark:bg-slate-950/40">
            <SafeResponsiveChart className="h-[200px] w-full" option={actionDonutOption} />
          </div>

          <div className="mt-4 space-y-2.5">
            {actionData.map((item) => {
              const tot = actionData.reduce((s, d) => s + d.value, 0);
              const pct = tot > 0 ? Math.round((item.value / tot) * 100) : 0;
              return (
                <div key={item.name}>
                  <div className="mb-1 flex items-center justify-between text-sm">
                    <span className="flex items-center gap-2 text-slate-700 dark:text-slate-300">
                      <span className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: item.color }} />
                      {item.name}
                    </span>
                    <span className="font-semibold tabular-nums text-slate-950 dark:text-slate-50">
                      {item.value.toLocaleString()} <span className="text-xs text-slate-400">({pct}%)</span>
                    </span>
                  </div>
                  <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
                    <div className="h-full rounded-full" style={{ width: `${Math.max(pct, 1)}%`, backgroundColor: item.color }} />
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>
  );
}

const ZERO_PRESSURE_CHART = Array.from({ length: 24 }, (_, index) => ({
  time: `${String(index).padStart(2, "0")}:00`,
  value: 0,
}));

const MODULE_PRESSURE_CURVE_LABELS = {
  "1.1": "Blocked over 24h",
  "1.2": "Blocked over 24h",
  "1.3": "Blocked over 24h",
  "1.4": "Redacted over 24h",
  "1.5": "Blocked over 24h",
  "1.6": "Critical over 24h",
  "1.7": "Blocked over 24h",
};

function moduleInterventions(moduleId, mod) {
  if (!mod) return 0;
  if (moduleId === "1.4") return (mod.blocked || 0) + (mod.redacted || 0);
  if (moduleId === "1.6") return mod.critical || 0;
  return mod.blocked || 0;
}

function moduleInterventionRate(moduleId, mod) {
  if (!mod?.total) return 0;
  return Math.round((moduleInterventions(moduleId, mod) / mod.total) * 100);
}

export function AIMeshFirewallOverview({ onTabChange }) {
  const { fetchWithAuth } = useAuth();
  const compact = useCompactViewport(900);
  const reduceMotion = useReducedMotion();
  const [socKpis, setSocKpis] = useState(null);
  const [attackTrends, setAttackTrends] = useState([]);
  const [moduleKpis, setModuleKpis] = useState(null);
  const [moduleTrends, setModuleTrends] = useState(null);
  const [loading, setLoading] = useState(true);
  const [lastUpdated, setLastUpdated] = useState(null);
  const [fetchError, setFetchError] = useState(null);
  const [period, setPeriod] = useState("24h");
  const intervalRef = useRef(null);
  const inFlightRef = useRef(false);
  const realtimeTimerRef = useRef(null);

  const fetchOverviewData = useCallback(async (showLoader = false) => {
    if (inFlightRef.current) return; // single-flight: collapse overlapping refetches
    inFlightRef.current = true;
    if (showLoader) setLoading(true);
    setFetchError(null);
    try {
      const errors = [];
      // Apply each endpoint's result to state AS SOON AS IT RESOLVES — do NOT batch
      // behind Promise.allSettled. Otherwise the slowest/hanging endpoint blocks every
      // KPI from rendering: module-trends is explicitly non-critical yet, when it stalls
      // (slow query / backend pressure), it would keep the whole dashboard on "--".
      const apply = async (promise, errorName, onData) => {
        try {
          const res = await promise;
          if (res && res.ok) onData(await res.json());
          else if (errorName) errors.push(errorName);
        } catch {
          if (errorName) errors.push(errorName);
        }
      };
      // Critical endpoints — the dashboard's readiness gates on these three only.
      const critical = [
        apply(fetchWithAuth(`/api/security/soc-kpis/?period=${period}`), "SOC KPIs", setSocKpis),
        apply(fetchWithAuth(`/api/security/attack-vector-trends/?period=${period}`), "Attack Trends", (d) => setAttackTrends(Array.isArray(d) ? d : [])),
        apply(fetchWithAuth(`/api/security/module-kpis/?period=${period}`), "Module KPIs", setModuleKpis),
      ];
      // Non-critical: applies whenever it arrives; never blocks the dashboard.
      (async () => {
        try {
          const res = await fetchWithAuth(`/api/security/module-trends/?period=${period}`);
          if (res && res.ok) setModuleTrends(await res.json());
          else console.warn("Module trends endpoint unavailable, using global chart data");
        } catch {
          console.warn("Module trends endpoint unavailable, using global chart data");
        }
      })();

      await Promise.allSettled(critical);

      if (errors.length > 0) {
        setFetchError(`Failed to load: ${errors.join(", ")}`);
        console.error(`Dashboard API errors: ${errors.join(", ")}`);
      }

      setLastUpdated(new Date());
    } catch (err) {
      setFetchError("Network error — unable to reach the backend");
      console.error("Dashboard fetch error:", err);
    } finally {
      setLoading(false);
      inFlightRef.current = false;
    }
  }, [fetchWithAuth, period]);

  const lastFetchedPeriodRef = useRef(null);
  useEffect(() => {
    // M3: fetch the 4 analytics endpoints ONCE per actual `period` value — not on
    // every effect re-run. React 18 StrictMode (and any incidental re-mount)
    // otherwise re-fires the initial loader fetch, multiplying network requests
    // per page load. The single-flight guard collapses CONCURRENT bursts, but a
    // post-resolve re-mount slips through; this ref dedupes against the period.
    if (lastFetchedPeriodRef.current !== period) {
      lastFetchedPeriodRef.current = period;
      fetchOverviewData(true);
    }
    intervalRef.current = setInterval(() => fetchOverviewData(false), 10_000);
    return () => clearInterval(intervalRef.current);
  }, [fetchOverviewData, period]);

  // Real-time WebSocket: refresh on new enforcement events, but coalesce bursts
  // with a 2s trailing debounce so N events/min collapse to a single refetch
  // (on top of the single-flight guard) instead of stacking 4 calls per event.
  useRealtimeNotifications({
    enabled: true,
    onEnforcementEvent: useCallback(() => {
      if (realtimeTimerRef.current) clearTimeout(realtimeTimerRef.current);
      realtimeTimerRef.current = setTimeout(() => fetchOverviewData(false), 2000);
    }, [fetchOverviewData]),
  });

  useEffect(() => () => clearTimeout(realtimeTimerRef.current), []);

  const total = socKpis?.total_threats ?? 0;
  const blocked = socKpis?.blocked ?? 0;
  const redacted = socKpis?.redacted ?? 0;
  const critical = socKpis?.critical_count ?? 0;
  const blockRate = socKpis?.block_rate ?? 0;
  const redactRate = socKpis?.redact_rate ?? 0;

  // Real enforcement time-series from the gateway-intake superset (module 1.1),
  // which vends per-bucket {total, blocked, redacted}. The green band is labelled
  // "Allowed / Monitored" because allowed = total − blocked − redacted still
  // includes ACTION_MONITOR events (monitor is not bucketed separately upstream).
  const intakeTotal = moduleKpis?.modules?.["1.1"]?.total ?? 0;
  const enforcementSeries = useMemo(() => {
    const buckets = moduleTrends?.["1.1"];
    if (!Array.isArray(buckets)) return [];
    return buckets.map((pt) => {
      const t = pt.total ?? 0;
      const b = pt.blocked ?? 0;
      const r = pt.redacted ?? 0;
      return {
        time: formatTrendBucketLabel(pt.time, moduleTrends?.period || period),
        allowed: Math.max(0, t - b - r),
        blocked: b,
        redacted: r,
      };
    });
  }, [moduleTrends, period]);

  const getModuleChartData = (moduleId) => {
    if (moduleTrends && moduleTrends[moduleId]) {
      return moduleTrends[moduleId].map((pt) => ({
        time: formatTrendBucketLabel(pt.time, moduleTrends.period || "24h"),
        value: pt.pressure ?? pt.value ?? 0,
      }));
    }
    return ZERO_PRESSURE_CHART;
  };

  const modules = moduleKpis?.modules || {};
  const modulesWithTraffic = Object.values(modules).filter((m) => (m?.total || 0) > 0).length;
  const fmt = (value) => (value != null ? value.toLocaleString() : "--");

  const subModules = [
    {
      id: "1.1",
      title: "AI Gateway & Traffic Ingress",
      icon: Zap,
      color: "blue",
      tabId: "firewall-1-1",
      summary: "Authenticate, throttle, and inspect every model request at the front door of the mesh.",
      metrics: [
        { label: "Intake", value: moduleKpis ? fmt(modules["1.1"]?.total) : "--" },
        { label: "Blocked", value: moduleKpis ? fmt(modules["1.1"]?.blocked) : "--", change: moduleKpis && modules["1.1"]?.total ? `${Math.round((modules["1.1"].blocked / modules["1.1"].total) * 100)}% pressure` : null },
      ],
    },
    {
      id: "1.2",
      title: "Policy Management",
      icon: GitBranch,
      color: "teal",
      tabId: "firewall-1-2",
      summary: "Manage content rules and vector isolation policies from one page instead of splitting enforcement across retrieval tabs.",
      metrics: [
        { label: "Matched", value: moduleKpis ? fmt(modules["1.2"]?.total) : "--" },
        { label: "Blocked", value: moduleKpis ? fmt(modules["1.2"]?.blocked) : "--", change: "Content + namespace policy actions" },
      ],
    },
    {
      id: "1.3",
      title: "RAG & Vector DB Firewall",
      icon: Database,
      color: "violet",
      tabId: "firewall-1-3",
      summary: "Run the full retrieval workflow from secure ingestion through vector-query simulation on one combined operations page.",
      metrics: [
        { label: "Matched", value: moduleKpis ? fmt(modules["1.3"]?.total) : "--" },
        { label: "Blocked", value: moduleKpis ? fmt(modules["1.3"]?.blocked) : "--", change: "RAG + vector enforcement" },
      ],
    },
    {
      id: "1.4",
      title: "Context Assembly & MCP",
      icon: Eye,
      color: "amber",
      tabId: "firewall-1-4",
      summary: "Guard context packaging, device metadata, tool inputs, and MCP assembly before execution.",
      metrics: [
        { label: "Matched", value: moduleKpis ? fmt(modules["1.4"]?.total) : "--" },
        { label: "Redacted", value: moduleKpis ? fmt(modules["1.4"]?.redacted) : "--", change: "Sensitive context removed" },
      ],
    },
    {
      id: "1.5",
      title: "Multi-Model Governance",
      icon: Shield,
      color: "emerald",
      tabId: "firewall-1-5",
      summary: "Route traffic across models with policy-aware controls for cost, risk, and failure domains.",
      metrics: [
        { label: "Matched", value: moduleKpis ? fmt(modules["1.5"]?.total) : "--" },
        { label: "Blocked", value: moduleKpis ? fmt(modules["1.5"]?.blocked) : "--", change: "Governance and failover" },
      ],
    },
    {
      id: "1.6",
      title: "Model Isolation & Kill-Switch",
      icon: AlertTriangle,
      color: "orange",
      tabId: "firewall-1-6",
      summary: "Escalate quickly when a model or route drifts out of bounds and provide operators with hard stop controls.",
      metrics: [
        { label: "Matched", value: moduleKpis ? fmt(modules["1.6"]?.total) : "--" },
        { label: "Critical", value: moduleKpis ? fmt(modules["1.6"]?.critical) : "--", change: "Critical + containment" },
      ],
    },
    {
      id: "1.7",
      title: "Output Guardrails",
      icon: Filter,
      color: "red",
      tabId: "firewall-1-7",
      summary: "Inspect model output for policy violations, risky disclosures, and harmful content before delivery.",
      metrics: [
        { label: "Scanned", value: moduleKpis ? fmt(modules["1.7"]?.total) : "--" },
        { label: "Blocked", value: moduleKpis ? fmt(modules["1.7"]?.blocked) : "--", change: moduleKpis && modules["1.7"]?.total ? `${Math.round((modules["1.7"].blocked / modules["1.7"].total) * 100)}% caught` : "Post-generation review" },
      ],
    },
  ];

  const activityData = [
    { module: "1.1 Gateway", requests: modules["1.1"]?.total ?? 0, interventions: moduleInterventions("1.1", modules["1.1"]) },
    { module: "1.2 Policy", requests: modules["1.2"]?.total ?? 0, interventions: moduleInterventions("1.2", modules["1.2"]) },
    { module: "1.3 RAG+Vector", requests: modules["1.3"]?.total ?? 0, interventions: moduleInterventions("1.3", modules["1.3"]) },
    { module: "1.4 Context", requests: modules["1.4"]?.total ?? 0, interventions: moduleInterventions("1.4", modules["1.4"]) },
    { module: "1.5 Routing", requests: modules["1.5"]?.total ?? 0, interventions: moduleInterventions("1.5", modules["1.5"]) },
    { module: "1.6 Isolation", requests: modules["1.6"]?.total ?? 0, interventions: moduleInterventions("1.6", modules["1.6"]) },
    { module: "1.7 Guards", requests: modules["1.7"]?.total ?? 0, interventions: moduleInterventions("1.7", modules["1.7"]) },
  ];

  // ── Attack vector computed data ──
  const vectorTrendData = attackTrends.map((b) => {
    const t = new Date(b.time);
    return {
      time: `${String(t.getHours()).padStart(2, "0")}:${String(t.getMinutes()).padStart(2, "0")}`,
      "Prompt Injection": b.promptInjection || 0,
      "Data Leakage": b.dataLeakage || 0,
      "Jailbreak": b.jailbreak || 0,
      "Goal Hijacking": b.goalHijacking || 0,
      "Tool Overreach": b.toolOverreach || 0,
    };
  });

  const vectorSums = attackTrends.reduce(
    (acc, b) => ({
      "Prompt Injection": acc["Prompt Injection"] + (b.promptInjection || 0),
      "Data Leakage": acc["Data Leakage"] + (b.dataLeakage || 0),
      "Jailbreak": acc["Jailbreak"] + (b.jailbreak || 0),
      "Goal Hijacking": acc["Goal Hijacking"] + (b.goalHijacking || 0),
      "Tool Overreach": acc["Tool Overreach"] + (b.toolOverreach || 0),
    }),
    { "Prompt Injection": 0, "Data Leakage": 0, "Jailbreak": 0, "Goal Hijacking": 0, "Tool Overreach": 0 },
  );
  const vectorDistData = VECTOR_KEYS.map((k) => ({ name: k, value: vectorSums[k], fill: VECTOR_COLORS[k] }));

  const moduleBlockRateData = [
    { id: "1.1", name: "Gateway & Ingress", total: modules["1.1"]?.total ?? 0, blockRate: modules["1.1"]?.total ? moduleInterventionRate("1.1", modules["1.1"]) : blockRate },
    { id: "1.2", name: "Policy Mgmt", total: modules["1.2"]?.total ?? 0, blockRate: moduleInterventionRate("1.2", modules["1.2"]) },
    { id: "1.3", name: "RAG & Vector DB", total: modules["1.3"]?.total ?? 0, blockRate: moduleInterventionRate("1.3", modules["1.3"]) },
    { id: "1.4", name: "Context & MCP", total: modules["1.4"]?.total ?? 0, blockRate: moduleInterventionRate("1.4", modules["1.4"]) },
    { id: "1.5", name: "Multi-Model Gov.", total: modules["1.5"]?.total ?? 0, blockRate: moduleInterventionRate("1.5", modules["1.5"]) },
    { id: "1.6", name: "Isolation & Kill-Sw", total: modules["1.6"]?.total ?? 0, blockRate: moduleInterventionRate("1.6", modules["1.6"]) },
    { id: "1.7", name: "Output Guardrails", total: modules["1.7"]?.total ?? 0, blockRate: moduleInterventionRate("1.7", modules["1.7"]) },
  ];

  const intakeHint = PERIOD_INTAKE_HINTS[period] || PERIOD_INTAKE_HINTS["24h"];

  const heroMetrics = [
    { icon: Activity, label: "Total events", value: socKpis ? total.toLocaleString() : "--", hint: intakeHint, tone: "teal" },
    { icon: AlertTriangle, label: "Block rate", value: socKpis ? `${blockRate}%` : "--", hint: "Requests denied before model execution", tone: "red" },
    { icon: ShieldCheck, label: "Redaction rate", value: socKpis ? `${redactRate}%` : "--", hint: "Requests sanitized instead of blocked", tone: "amber" },
    { icon: ShieldAlert, label: "Critical events", value: socKpis ? String(critical) : "--", hint: "High urgency incidents requiring operator review", tone: "blue" },
  ];

  return (
    <div className="ai-mesh-shell space-y-8">
      {fetchError && (
        <div className="rounded-2xl border border-rose-300 bg-rose-50 px-5 py-3 text-sm text-rose-700 dark:border-rose-800 dark:bg-rose-950/40 dark:text-rose-300">
          <div className="flex items-center gap-2">
            <AlertTriangle className="h-4 w-4 shrink-0" />
            <span className="font-medium">{fetchError}</span>
            <button onClick={() => fetchOverviewData(true)} className="ml-auto text-xs font-semibold underline hover:no-underline">Retry</button>
          </div>
        </div>
      )}
      <section className="ai-mesh-card-strong ai-mesh-grid-bg ai-mesh-sheen relative overflow-hidden rounded-[30px] px-6 py-7 lg:px-8 lg:py-8">
        <div className="ai-mesh-hero-glow left-[-6rem] top-[-5rem] h-52 w-52 bg-sky-500/20" />
        <div className="ai-mesh-hero-glow bottom-[-7rem] right-[-3rem] h-56 w-56 bg-teal-500/20" />

        <div className="relative grid gap-6 xl:grid-cols-[1.25fr,0.75fr]">
          <div>
            <div className="ai-mesh-pill px-3 py-1.5 text-xs font-semibold">
              <span className="ai-mesh-dot h-2 w-2 rounded-full bg-teal-500" />
              Module 1 live control plane
              {lastUpdated && (
                <span className="ml-3 text-slate-400">
                  Updated {lastUpdated.toLocaleTimeString()}
                </span>
              )}
              <button
                onClick={() => fetchOverviewData(false)}
                className="ml-2 inline-flex items-center text-slate-400 hover:text-teal-500 transition-colors"
                title="Refresh data"
              >
                <RefreshCw className={`h-3 w-3 ${loading ? 'animate-spin' : ''}`} />
              </button>
            </div>
            <div className="mt-5 flex items-start gap-4">
              <div className="flex h-16 w-16 items-center justify-center rounded-[22px] bg-gradient-to-br from-sky-500 via-cyan-500 to-teal-500 text-white shadow-xl shadow-cyan-500/15">
                <ShieldAlert className="h-7 w-7" strokeWidth={2.4} />
              </div>
              <div className="max-w-3xl">
                <h1 className="text-4xl font-semibold tracking-tight text-slate-950 dark:text-slate-50 md:text-[2.8rem]">AI Mesh Firewall</h1>
                <p className="mt-3 max-w-2xl text-base leading-7 text-slate-600 dark:text-slate-400">
                  A cleaner operator experience for tracing ingress, retrieval, routing, context, isolation, and output defenses without losing the live telemetry underneath.
                </p>
              </div>
            </div>

            <div className="mt-6 flex flex-col gap-3 sm:flex-row">
              <button
                onClick={() => onTabChange?.("firewall-config")}
                className="inline-flex items-center justify-center gap-2 rounded-2xl bg-slate-950 px-5 py-3 text-sm font-medium text-white transition-colors hover:bg-slate-800 dark:bg-slate-100 dark:text-slate-950 dark:hover:bg-white"
              >
                Open Gateway Controls
                <ArrowRight className="h-4 w-4" />
              </button>
              <button
                onClick={() => onTabChange?.("firewall-1-2")}
                className="inline-flex items-center justify-center gap-2 rounded-2xl border border-slate-300 bg-white/85 px-5 py-3 text-sm font-medium text-slate-700 transition-colors hover:bg-white dark:border-slate-700 dark:bg-slate-900/70 dark:text-slate-200 dark:hover:bg-slate-900"
              >
                Configure Policies
                <Settings2 className="h-4 w-4" />
              </button>
            </div>

            <div className="mt-6 flex flex-wrap items-center gap-2">
              <span className="mr-1 text-[11px] font-semibold uppercase tracking-[0.16em] text-slate-500 dark:text-slate-400">Time lens</span>
              {["1h", "6h", "24h", "7d", "30d"].map((p) => (
                <button
                  key={p}
                  onClick={() => setPeriod(p)}
                  aria-pressed={period === p}
                  className={`cursor-pointer rounded-xl px-3 py-1.5 text-xs font-medium transition-colors ${
                    period === p
                      ? "bg-teal-600 text-white dark:bg-teal-500"
                      : "border border-slate-200 bg-white/80 text-slate-600 hover:bg-white dark:border-slate-700 dark:bg-slate-900/70 dark:text-slate-300 dark:hover:bg-slate-900"
                  }`}
                >
                  {p}
                </button>
              ))}
            </div>

            <div className="mt-5 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
              {heroMetrics.map((metric, i) => (
                <motion.div
                  key={metric.label}
                  initial={reduceMotion ? false : { opacity: 0, y: 14 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.35, delay: Math.min(i * 0.06, 0.3), ease: [0.22, 1, 0.36, 1] }}
                >
                  <HeroMetric icon={metric.icon} label={metric.label} value={metric.value} hint={metric.hint} tone={metric.tone} loading={loading} />
                </motion.div>
              ))}
            </div>
          </div>

          <div className="grid gap-4">
            <div className="ai-mesh-kpi p-6">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-teal-600 dark:text-teal-300">Security posture</div>
                  <h3 className="mt-2 text-xl font-semibold text-slate-950 dark:text-slate-50">Operational guardrails at a glance</h3>
                </div>
                <ShieldCheck className="h-5 w-5 text-teal-600 dark:text-teal-300" />
              </div>

              <div className="mt-6 grid gap-3">
                {[
                  { label: "Protected submodules", value: moduleKpis ? `${Object.values(modules).filter((m) => m.total > 0).length} / 7` : "-- / 7", detail: moduleKpis ? `${Object.values(modules).filter((m) => m.total > 0).length === 7 ? "All" : "Active"} firewall lanes online` : "Awaiting data" },
                  { label: "Response posture", value: socKpis?.avg_latency_ms ? `~${Math.round(socKpis.avg_latency_ms)}ms` : "--", detail: "Average decision latency across live traffic" },
                ].map((item) => (
                  <div key={item.label} className="rounded-2xl border border-slate-200/80 bg-white/80 px-4 py-3 dark:border-slate-700 dark:bg-slate-950/45">
                    <div className="text-[11px] font-medium uppercase tracking-[0.16em] text-slate-500 dark:text-slate-400">{item.label}</div>
                    <div className="mt-2 text-2xl font-semibold text-slate-950 dark:text-slate-50">{item.value}</div>
                    <div className="mt-1 text-xs text-slate-500 dark:text-slate-400">{item.detail}</div>
                  </div>
                ))}
              </div>
            </div>

            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-1 2xl:grid-cols-2">
              <CommandShortcut
                icon={Zap}
                eyebrow="Fast path"
                title="Run ingress tests"
                description="Jump directly into the gateway simulator, issue test prompts, and validate end-to-end enforcement behavior."
                cta="Launch 1.1 controls"
                onClick={() => onTabChange?.("firewall-1-1")}
              />
              <CommandShortcut
                icon={Filter}
                eyebrow="Operator action"
                title="Review output guardrails"
                description="Inspect generated-content controls, review incidents, and verify final-response safety layers."
                cta="Open 1.7 review lane"
                onClick={() => onTabChange?.("firewall-1-7")}
              />
            </div>
          </div>
        </div>
      </section>

      <section className="space-y-5">
        <SectionHeading
          eyebrow="Telemetry"
          title="Traffic and enforcement overview"
          description="The top-level charts stay focused on operator decisions: volume, intervention mix, and where the firewall is absorbing pressure right now."
        />
        <GlobalTrafficOverview socKpis={socKpis} enforcementSeries={enforcementSeries} intakeTotal={intakeTotal} loading={loading} compact={compact} />
      </section>

      <section className="space-y-5">
        <SectionHeading
          eyebrow="Submodules"
          title="AI Mesh Firewall command grid"
          description="Each submodule card exposes the operating surface, pressure signal, and quickest next action without forcing a context switch into raw tables first."
          action={<span className="text-sm text-slate-500 dark:text-slate-400">{moduleKpis ? `${modulesWithTraffic} / 7 modules with traffic` : `${subModules.length} / 7 modules`}</span>}
        />
        <div className="flex items-start gap-2 rounded-2xl border border-amber-200/70 bg-amber-50/60 px-4 py-2.5 text-xs leading-5 text-amber-800 dark:border-amber-800/50 dark:bg-amber-950/30 dark:text-amber-200">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span>
            <strong>1.1 is the gateway intake superset</strong> (it sees every request). Lanes <strong>1.2–1.7 are independent detection lenses</strong> — a single event can match several at once, so their <em>Matched</em> counts overlap and do not sum to total intake. When a lane equals 1.1 (e.g. 1.3 mirroring intake), it means all traffic in this window was that lane&rsquo;s type — not a duplicate count.
          </span>
        </div>
        <div className="grid gap-5 md:grid-cols-2 xl:grid-cols-3">
          {subModules.map((module, i) => (
            <motion.div
              key={module.id}
              initial={reduceMotion ? false : { opacity: 0, y: 16 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, margin: "-60px" }}
              transition={{ duration: 0.4, delay: Math.min(i * 0.05, 0.25), ease: [0.22, 1, 0.36, 1] }}
            >
              <SubModuleCard
                {...module}
                chartData={getModuleChartData(module.id)}
                curveSubtitle={MODULE_PRESSURE_CURVE_LABELS[module.id] || "Pressure over 24h"}
                onViewDetails={() => onTabChange?.(module.tabId)}
              />
            </motion.div>
          ))}

          <button
            onClick={() => onTabChange?.("firewall-config")}
            className="ai-mesh-card group relative overflow-hidden rounded-[26px] border-dashed p-5 text-left transition-transform duration-200 hover:-translate-y-1"
          >
            <div className="absolute inset-0 bg-gradient-to-br from-slate-200/40 via-transparent to-teal-500/10 dark:from-slate-800/60 dark:to-teal-500/10" />
            <div className="relative flex h-full flex-col justify-between gap-8">
              <div>
                <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-slate-950 text-white dark:bg-slate-100 dark:text-slate-950">
                  <Settings2 className="h-5 w-5" />
                </div>
                <div className="mt-5 text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-500 dark:text-slate-400">Configuration</div>
                <h3 className="mt-2 text-xl font-semibold text-slate-950 dark:text-slate-50">Firewall inputs and controls</h3>
                <p className="mt-2 text-sm leading-6 text-slate-600 dark:text-slate-400">
                  Centralize policies, simulator inputs, and baseline configuration for the firewall control plane.
                </p>
              </div>
              <div className="inline-flex items-center gap-2 text-sm font-medium text-slate-950 dark:text-slate-100">
                Open configuration
                <ChevronRight className="h-4 w-4 transition-transform duration-200 group-hover:translate-x-1" />
              </div>
            </div>
          </button>
        </div>
      </section>

      <section className="space-y-5">
        <SectionHeading
          eyebrow="Security intelligence"
          title="Attack vector analysis"
          description="Track which threat categories are driving enforcement pressure across the mesh and how the mix shifts over the selected time window."
        />
        <div className="grid gap-6 xl:grid-cols-2">
          <AttackVectorTrendChart data={vectorTrendData} compact={compact} />
          <AttackVectorDistributionChart data={vectorDistData} />
        </div>
      </section>

      <section className="space-y-5">
        <SectionHeading
          eyebrow="Cross-module pressure"
          title="Traffic and intervention comparisons"
          description="Side-by-side view of event volume and intervention rate for every firewall lane. Lanes overlap — module 1.1 is the gateway intake superset; specialty modules 1.2–1.7 are non-exclusive lenses on the same traffic."
        />
        <div className="grid gap-6 xl:grid-cols-[1.4fr,1fr]">
          <ModuleComparisonChart data={activityData} compact={compact} />
          <ModuleBlockRateChart data={moduleBlockRateData} />
        </div>
      </section>

      <section className="space-y-5">
        <SectionHeading
          eyebrow="Threat intelligence"
          title="Coverage and policy depth"
          description="The existing OWASP and policy analytics panels remain intact, now framed as supporting evidence under the redesigned command surface."
        />
        <OWASPStatsPanel />
        <PolicyAnalyticsPanel />
      </section>
    </div>
  );
}
