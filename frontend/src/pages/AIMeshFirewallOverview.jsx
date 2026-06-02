import { useEffect, useState, useCallback, useRef } from "react";
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
import {
  LineChart,
  Line,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
  AreaChart,
  Area,
  PieChart,
  Pie,
  Cell,
} from "recharts";
import { useAuth } from "../context/AuthContext";
import { useRealtimeNotifications } from "../hooks/useRealtimeNotifications";
import { OWASPStatsPanel } from "../components/OWASPStatsPanel";
import { PolicyAnalyticsPanel } from "../components/PolicyAnalyticsPanel";

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

function SubModuleCard({ id, title, icon: Icon, color, summary, metrics, chartData, onViewDetails }) {
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
            <span>24h</span>
          </div>
          <ResponsiveContainer width="100%" height={72}>
            <AreaChart data={chartData} margin={{ top: 4, right: 0, bottom: 0, left: 0 }}>
              <defs>
                <linearGradient id={`mesh-card-${id}`} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor={tone.hex} stopOpacity={0.28} />
                  <stop offset="95%" stopColor={tone.hex} stopOpacity={0.02} />
                </linearGradient>
              </defs>
              <Area type="monotone" dataKey="value" stroke={tone.hex} strokeWidth={2.2} fill={`url(#mesh-card-${id})`} dot={false} />
            </AreaChart>
          </ResponsiveContainer>
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
function ChartTooltip({ active, payload, label, unit = "" }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-2xl border border-slate-700/80 bg-slate-900/95 px-4 py-3 shadow-2xl backdrop-blur-sm">
      {label != null ? (
        <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">{label}</div>
      ) : null}
      {payload.map((p) => (
        <div key={p.dataKey ?? p.name} className="flex items-center gap-2.5 py-0.5 text-sm">
          <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ backgroundColor: p.color ?? p.fill }} />
          <span className="text-slate-300">{p.name}</span>
          <span className="ml-auto pl-4 font-semibold tabular-nums text-white">
            {typeof p.value === "number" ? p.value.toLocaleString() : p.value}{unit}
          </span>
        </div>
      ))}
    </div>
  );
}

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
        <ResponsiveContainer width="100%" height={compact ? 240 : 300}>
          <AreaChart data={data} margin={{ top: 4, right: compact ? 0 : 4, bottom: 0, left: compact ? -14 : -8 }}>
            <defs>
              {VECTOR_KEYS.map((k) => (
                <linearGradient key={k} id={`vgrad-${k.replace(/\s+/g, "")}`} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor={VECTOR_COLORS[k]} stopOpacity={0.28} />
                  <stop offset="95%" stopColor={VECTOR_COLORS[k]} stopOpacity={0.02} />
                </linearGradient>
              ))}
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="#94a3b8" strokeOpacity={0.15} />
            <XAxis
              dataKey="time"
              stroke="#94a3b8"
              tick={{ fontSize: 11, fill: "#94a3b8" }}
              tickLine={false}
              axisLine={{ stroke: "#94a3b8", strokeOpacity: 0.3 }}
              interval={compact ? 1 : 0}
              minTickGap={compact ? 18 : 8}
              label={compact ? undefined : { value: "Time (UTC)", position: "insideBottom", offset: -2, fontSize: 10, fill: "#64748b" }}
            />
            <YAxis
              stroke="#94a3b8"
              tick={{ fontSize: 11, fill: "#94a3b8" }}
              tickLine={false}
              axisLine={{ stroke: "#94a3b8", strokeOpacity: 0.3 }}
              width={compact ? 28 : 40}
              label={compact ? undefined : { value: "Events", angle: -90, position: "insideLeft", offset: 12, fontSize: 10, fill: "#64748b" }}
            />
            <Tooltip content={<ChartTooltip />} />
            {VECTOR_KEYS.map((k) => (
              <Area
                key={k}
                type="monotone"
                dataKey={k}
                stroke={VECTOR_COLORS[k]}
                strokeWidth={2}
                fill={`url(#vgrad-${k.replace(/\s+/g, "")})`}
                dot={false}
                activeDot={{ r: 4, strokeWidth: 0 }}
                name={k}
              />
            ))}
          </AreaChart>
        </ResponsiveContainer>
      )}
    </OverviewChartCard>
  );
}

// ─── Attack Vector Distribution donut ────────────────────────────────────────
function AttackVectorDistributionChart({ data }) {
  const total = data.reduce((s, d) => s + d.value, 0);
  const hasData = total > 0;

  const renderCustomLabel = ({ cx, cy, midAngle, innerRadius, outerRadius, percent }) => {
    if (percent < 0.05) return null;
    const RADIAN = Math.PI / 180;
    const r = innerRadius + (outerRadius - innerRadius) * 0.5;
    const x = cx + r * Math.cos(-midAngle * RADIAN);
    const y = cy + r * Math.sin(-midAngle * RADIAN);
    return (
      <text x={x} y={y} fill="#fff" textAnchor="middle" dominantBaseline="central" fontSize={11} fontWeight={600}>
        {`${(percent * 100).toFixed(0)}%`}
      </text>
    );
  };

  return (
    <OverviewChartCard eyebrow="Threat mix" title="Attack vector distribution">
      <div className="flex flex-col gap-5 md:flex-row md:items-center">
        <div className="mx-auto shrink-0">
          {hasData ? (
            <ResponsiveContainer width={220} height={220}>
              <PieChart>
                <Pie
                  data={data}
                  cx="50%"
                  cy="50%"
                  innerRadius={60}
                  outerRadius={95}
                  dataKey="value"
                  paddingAngle={3}
                  labelLine={false}
                  label={renderCustomLabel}
                >
                  {data.map((entry, i) => (
                    <Cell key={i} fill={entry.fill} stroke="transparent" />
                  ))}
                </Pie>
                <Tooltip content={<ChartTooltip />} />
              </PieChart>
            </ResponsiveContainer>
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
      <ResponsiveContainer width="100%" height={compact ? 260 : 300}>
        <BarChart data={data} margin={{ top: 4, right: 4, bottom: compact ? 12 : 24, left: compact ? -14 : -8 }} barGap={3} barCategoryGap={compact ? "18%" : "28%"}>
          <defs>
            <linearGradient id="bargrad-teal" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#14b8a6" stopOpacity={1} />
              <stop offset="100%" stopColor="#0d9488" stopOpacity={0.8} />
            </linearGradient>
            <linearGradient id="bargrad-rose" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#f43f5e" stopOpacity={1} />
              <stop offset="100%" stopColor="#e11d48" stopOpacity={0.8} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="#94a3b8" strokeOpacity={0.15} vertical={false} />
          <XAxis
            dataKey="module"
            stroke="#94a3b8"
            tick={{ fontSize: 10, fill: "#94a3b8" }}
            tickLine={false}
            axisLine={{ stroke: "#94a3b8", strokeOpacity: 0.3 }}
            angle={compact ? 0 : -15}
            textAnchor={compact ? "middle" : "end"}
            height={compact ? 28 : 54}
          />
          <YAxis
            stroke="#94a3b8"
            tick={{ fontSize: 11, fill: "#94a3b8" }}
            tickLine={false}
            axisLine={{ stroke: "#94a3b8", strokeOpacity: 0.3 }}
            width={compact ? 28 : 40}
            label={compact ? undefined : { value: "Events", angle: -90, position: "insideLeft", offset: 12, fontSize: 10, fill: "#64748b" }}
          />
          <Tooltip content={<ChartTooltip />} cursor={{ fill: "rgba(148, 163, 184, 0.08)" }} />
          <Bar dataKey="requests" fill="url(#bargrad-teal)" radius={[6, 6, 0, 0]} name="Total events" maxBarSize={40} />
          <Bar dataKey="blocked"  fill="url(#bargrad-rose)"  radius={[6, 6, 0, 0]} name="Interventions" maxBarSize={40} />
        </BarChart>
      </ResponsiveContainer>
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
    <OverviewChartCard eyebrow="Enforcement posture" title="Block rate by module">
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

function GlobalTrafficOverview({ socKpis, timeSeriesData, loading, compact = false }) {
  const total = socKpis?.total_threats || 0;
  const blocked = socKpis?.blocked || 0;
  const redacted = socKpis?.redacted || 0;
  const allowed = Math.max(0, total - blocked - redacted);
  const critical = socKpis?.critical_count || 0;

  const trafficData = timeSeriesData.length > 0
    ? timeSeriesData.map((item) => ({ time: item.time, total: item.primary, blocked: item.secondary }))
    : Array.from({ length: 24 }, (_, index) => ({ time: `${index}:00`, total: 0, blocked: 0 }));

  const actionData = [
    { name: "Allowed", value: allowed || 0, color: "#14b8a6" },
    { name: "Blocked", value: blocked || 0, color: "#ef4444" },
    { name: "Redacted", value: redacted || 0, color: "#f59e0b" },
  ];

  const stats = [
    { label: "Total Events", value: socKpis ? total.toLocaleString() : "--", hint: socKpis ? `${socKpis.block_rate || 0}% block rate` : "Awaiting feed", icon: Activity, tone: "teal" },
    { label: "Blocked", value: socKpis ? blocked.toLocaleString() : "--", hint: "Hard enforcement decisions", icon: AlertTriangle, tone: "red" },
    { label: "Redacted", value: socKpis ? redacted.toLocaleString() : "--", hint: socKpis ? `${socKpis.redact_rate || 0}% content sanitation` : "Awaiting feed", icon: Shield, tone: "amber" },
    { label: "Critical", value: socKpis ? String(critical) : "--", hint: "Events requiring immediate escalation", icon: ShieldCheck, tone: "blue" },
  ];

  return (
    <div className="space-y-6">
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {stats.map((stat) => (
          <HeroMetric key={stat.label} icon={stat.icon} label={stat.label} value={stat.value} hint={stat.hint} tone={stat.tone} loading={loading} />
        ))}
      </div>

      <div className="grid gap-6 xl:grid-cols-[1.45fr,0.8fr]">
        <div className="ai-mesh-card rounded-[28px] p-6">
          <div className="mb-5 flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
            <div>
              <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-teal-600 dark:text-teal-300">Live Telemetry</div>
              <h3 className="mt-2 text-xl font-semibold text-slate-950 dark:text-slate-50">Global request pressure over the last 24 hours</h3>
            </div>
            <div className="flex items-center gap-4 text-xs text-slate-500 dark:text-slate-400">
              <span className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-full bg-teal-500" /> Total</span>
              <span className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-full bg-rose-500" /> Prompt injection</span>
            </div>
          </div>
          <ResponsiveContainer width="100%" height={compact ? 230 : 280}>
            <AreaChart data={trafficData} margin={{ top: 4, right: 4, bottom: compact ? 8 : 16, left: compact ? -14 : -8 }}>
              <defs>
                <linearGradient id="mesh-traffic" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#14b8a6" stopOpacity={0.28} />
                  <stop offset="95%" stopColor="#14b8a6" stopOpacity={0.02} />
                </linearGradient>
                <linearGradient id="mesh-blocked" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#ef4444" stopOpacity={0.18} />
                  <stop offset="95%" stopColor="#ef4444" stopOpacity={0.01} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="#94a3b8" strokeOpacity={0.15} vertical={false} />
              <XAxis
                dataKey="time"
                stroke="#94a3b8"
                tick={{ fontSize: 11, fill: "#94a3b8" }}
                tickLine={false}
                axisLine={{ stroke: "#94a3b8", strokeOpacity: 0.3 }}
                interval={compact ? 1 : 0}
                minTickGap={compact ? 16 : 8}
                label={compact ? undefined : { value: "Hour (UTC)", position: "insideBottom", offset: -4, fontSize: 10, fill: "#64748b" }}
              />
              <YAxis
                stroke="#94a3b8"
                tick={{ fontSize: 11, fill: "#94a3b8" }}
                tickLine={false}
                axisLine={{ stroke: "#94a3b8", strokeOpacity: 0.3 }}
                width={compact ? 28 : 40}
                label={compact ? undefined : { value: "Events", angle: -90, position: "insideLeft", offset: 12, fontSize: 10, fill: "#64748b" }}
              />
              <Tooltip content={<ChartTooltip />} cursor={{ stroke: "#94a3b8", strokeWidth: 1, strokeDasharray: "4 4" }} />
              <Area type="monotone" dataKey="total" stroke="#14b8a6" strokeWidth={2.4} fill="url(#mesh-traffic)" dot={false} activeDot={{ r: 4, strokeWidth: 0 }} name="Total" />
              <Area type="monotone" dataKey="blocked" stroke="#ef4444" strokeWidth={1.8} fill="url(#mesh-blocked)" dot={false} activeDot={{ r: 4, strokeWidth: 0 }} name="Prompt injection" />
            </AreaChart>
          </ResponsiveContainer>
        </div>

        <div className="ai-mesh-card rounded-[28px] p-6">
          <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-teal-600 dark:text-teal-300">Distribution</div>
          <h3 className="mt-2 text-xl font-semibold text-slate-950 dark:text-slate-50">Enforcement posture</h3>
          <p className="mt-2 text-sm leading-6 text-slate-600 dark:text-slate-400">Breakdown of how the firewall responds to every request — allow, block, or sanitise.</p>

          <div className="mt-5 flex items-center justify-center rounded-[24px] border border-slate-200/80 bg-white/75 p-4 dark:border-slate-700 dark:bg-slate-950/40">
            <ResponsiveContainer width="100%" height={200}>
              <PieChart>
                <Pie
                  data={actionData}
                  cx="50%"
                  cy="50%"
                  innerRadius={50}
                  outerRadius={80}
                  dataKey="value"
                  paddingAngle={4}
                  labelLine={false}
                  label={({ cx, cy, midAngle, innerRadius, outerRadius, percent }) => {
                    if (percent < 0.06) return null;
                    const R = Math.PI / 180;
                    const r = innerRadius + (outerRadius - innerRadius) * 0.5;
                    const x = cx + r * Math.cos(-midAngle * R);
                    const y = cy + r * Math.sin(-midAngle * R);
                    return (
                      <text x={x} y={y} fill="#fff" textAnchor="middle" dominantBaseline="central" fontSize={11} fontWeight={700}>
                        {`${(percent * 100).toFixed(0)}%`}
                      </text>
                    );
                  }}
                >
                  {actionData.map((entry, index) => <Cell key={`action-${index}`} fill={entry.color} stroke="transparent" />)}
                </Pie>
                <Tooltip content={<ChartTooltip />} />
              </PieChart>
            </ResponsiveContainer>
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
    </div>
  );
}

export function AIMeshFirewallOverview({ onTabChange }) {
  const { fetchWithAuth } = useAuth();
  const compact = useCompactViewport(900);
  const [socKpis, setSocKpis] = useState(null);
  const [attackTrends, setAttackTrends] = useState([]);
  const [moduleKpis, setModuleKpis] = useState(null);
  const [moduleTrends, setModuleTrends] = useState(null);
  const [loading, setLoading] = useState(true);
  const [lastUpdated, setLastUpdated] = useState(null);
  const [fetchError, setFetchError] = useState(null);
  const intervalRef = useRef(null);

  const fetchOverviewData = useCallback(async (showLoader = false) => {
    if (showLoader) setLoading(true);
    setFetchError(null);
    try {
      const results = await Promise.allSettled([
        fetchWithAuth("/api/security/soc-kpis/?period=24h"),
        fetchWithAuth("/api/security/attack-vector-trends/?period=24h"),
        fetchWithAuth("/api/security/module-kpis/?period=24h"),
        fetchWithAuth("/api/security/module-trends/?period=24h"),
      ]);

      const errors = [];

      if (results[0].status === "fulfilled" && results[0].value.ok) {
        setSocKpis(await results[0].value.json());
      } else {
        errors.push("SOC KPIs");
      }
      if (results[1].status === "fulfilled" && results[1].value.ok) {
        const data = await results[1].value.json();
        setAttackTrends(Array.isArray(data) ? data : []);
      } else {
        errors.push("Attack Trends");
      }
      if (results[2].status === "fulfilled" && results[2].value.ok) {
        setModuleKpis(await results[2].value.json());
      } else {
        errors.push("Module KPIs");
      }
      if (results[3].status === "fulfilled" && results[3].value.ok) {
        setModuleTrends(await results[3].value.json());
      } else {
        // Non-critical: fallback to global chart data if module-trends unavailable
        console.warn("Module trends endpoint unavailable, using global chart data");
      }

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
    }
  }, [fetchWithAuth]);

  useEffect(() => {
    fetchOverviewData(true);
    intervalRef.current = setInterval(() => fetchOverviewData(false), 10_000);
    return () => clearInterval(intervalRef.current);
  }, [fetchOverviewData]);

  // Real-time WebSocket: refresh dashboard data on new enforcement events
  useRealtimeNotifications({
    enabled: true,
    onEnforcementEvent: useCallback(() => {
      fetchOverviewData(false);
    }, [fetchOverviewData]),
  });

  const timeSeriesData = attackTrends.map((bucket) => {
    const time = new Date(bucket.time);
    return {
      time: `${time.getHours()}:00`,
      primary:
        (bucket.promptInjection || 0) +
        (bucket.dataLeakage || 0) +
        (bucket.jailbreak || 0) +
        (bucket.goalHijacking || 0) +
        (bucket.toolOverreach || 0),
      secondary: bucket.promptInjection || 0,
    };
  });

  const total = socKpis?.total_threats ?? 0;
  const blocked = socKpis?.blocked ?? 0;
  const redacted = socKpis?.redacted ?? 0;
  const critical = socKpis?.critical_count ?? 0;
  const blockRate = socKpis?.block_rate ?? 0;
  const redactRate = socKpis?.redact_rate ?? 0;

  const chartDataFromTrends = timeSeriesData.length > 0
    ? timeSeriesData.map((item) => ({ time: item.time, value: item.primary }))
    : Array.from({ length: 24 }, (_, index) => ({ time: `${index}:00`, value: 0 }));

  // Per-module pressure curve data from /api/security/module-trends/
  const getModuleChartData = (moduleId) => {
    if (moduleTrends && moduleTrends[moduleId]) {
      return moduleTrends[moduleId].map((pt) => {
        const t = new Date(pt.time);
        return { time: `${t.getHours()}:00`, value: pt.value };
      });
    }
    // Fallback: use global chart data if per-module trends unavailable
    return chartDataFromTrends;
  };

  const modules = moduleKpis?.modules || {};
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
        { label: "Events", value: moduleKpis ? fmt(modules["1.1"]?.total) : socKpis ? total.toLocaleString() : "--" },
        { label: "Blocked", value: moduleKpis ? fmt(modules["1.1"]?.blocked) : socKpis ? blocked.toLocaleString() : "--", change: moduleKpis && modules["1.1"]?.total ? `${Math.round((modules["1.1"].blocked / modules["1.1"].total) * 100)}% pressure` : `${blockRate}% pressure` },
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
        { label: "Events", value: moduleKpis ? fmt(modules["1.2"]?.total) : "--" },
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
        { label: "Events", value: moduleKpis ? fmt(modules["1.3"]?.total) : "--" },
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
        { label: "Events", value: moduleKpis ? fmt(modules["1.4"]?.total) : "--" },
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
        { label: "Events", value: moduleKpis ? fmt(modules["1.5"]?.total) : "--" },
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
        { label: "Events", value: moduleKpis ? fmt(modules["1.6"]?.total) : "--" },
        { label: "Critical", value: moduleKpis ? fmt(modules["1.6"]?.critical) : socKpis ? String(critical) : "--", change: "Critical + containment" },
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
    { module: "1.1 Gateway", requests: modules["1.1"]?.total ?? total, blocked: modules["1.1"]?.blocked ?? blocked },
    { module: "1.2 Policy", requests: modules["1.2"]?.total ?? 0, blocked: modules["1.2"]?.blocked ?? 0 },
    { module: "1.3 RAG+Vector", requests: modules["1.3"]?.total ?? 0, blocked: modules["1.3"]?.blocked ?? 0 },
    { module: "1.4 Context", requests: modules["1.4"]?.total ?? 0, blocked: modules["1.4"]?.redacted ?? 0 },
    { module: "1.5 Routing", requests: modules["1.5"]?.total ?? 0, blocked: modules["1.5"]?.blocked ?? 0 },
    { module: "1.6 Isolation", requests: modules["1.6"]?.total ?? 0, blocked: modules["1.6"]?.blocked ?? 0 },
    { module: "1.7 Guards", requests: modules["1.7"]?.total ?? 0, blocked: modules["1.7"]?.blocked ?? 0 },
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
    { id: "1.1", name: "Gateway & Ingress",   total: modules["1.1"]?.total ?? 0, blockRate: modules["1.1"]?.total ? Math.round((modules["1.1"].blocked  / modules["1.1"].total) * 100) : blockRate },
    { id: "1.2", name: "Policy Mgmt",         total: modules["1.2"]?.total ?? 0, blockRate: modules["1.2"]?.total ? Math.round((modules["1.2"].blocked  / modules["1.2"].total) * 100) : 0 },
    { id: "1.3", name: "RAG & Vector DB",     total: modules["1.3"]?.total ?? 0, blockRate: modules["1.3"]?.total ? Math.round((modules["1.3"].blocked  / modules["1.3"].total) * 100) : 0 },
    { id: "1.4", name: "Context & MCP",       total: modules["1.4"]?.total ?? 0, blockRate: modules["1.4"]?.total ? Math.round((modules["1.4"].redacted / modules["1.4"].total) * 100) : 0 },
    { id: "1.5", name: "Multi-Model Gov.",    total: modules["1.5"]?.total ?? 0, blockRate: modules["1.5"]?.total ? Math.round((modules["1.5"].blocked  / modules["1.5"].total) * 100) : 0 },
    { id: "1.6", name: "Isolation & Kill-Sw", total: modules["1.6"]?.total ?? 0, blockRate: modules["1.6"]?.total ? Math.round((modules["1.6"].blocked / modules["1.6"].total) * 100) : 0 },
    { id: "1.7", name: "Output Guardrails",   total: modules["1.7"]?.total ?? 0, blockRate: modules["1.7"]?.total ? Math.round((modules["1.7"].blocked  / modules["1.7"].total) * 100) : 0 },
  ];

  const heroMetrics = [
    { icon: Activity, label: "Total events", value: socKpis ? total.toLocaleString() : "--", hint: "24 hour intake across the mesh", tone: "teal" },
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

            <div className="mt-7 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
              {heroMetrics.map((metric) => (
                <HeroMetric key={metric.label} icon={metric.icon} label={metric.label} value={metric.value} hint={metric.hint} tone={metric.tone} loading={loading} />
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
                  { label: "Allow vs enforce mix", value: socKpis ? `${Math.max(0, total - blocked - redacted)} / ${blocked + redacted}` : "--", detail: "Allowed requests versus intervention events" },
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

      <div className="ai-mesh-card rounded-[28px] p-6">
          <div className="flex items-center justify-between gap-3">
            <div>
              <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-teal-600 dark:text-teal-300">Live focus</div>
              <h3 className="mt-2 text-xl font-semibold text-slate-950 dark:text-slate-50">Current operator priorities</h3>
            </div>
            <Activity className="h-5 w-5 text-teal-600 dark:text-teal-300" />
          </div>

          <div className="mt-6 space-y-3">
            {[
              { title: "Watch ingress pressure", detail: "Authentication, rate limiting, and request identity still define the dominant control surface.", value: socKpis ? `${blockRate}%` : "--" },
              { title: "Keep redaction visible", detail: "Sanitized flows are operationally different from blocked flows; they need a separate operator lane.", value: socKpis ? `${redactRate}%` : "--" },
              { title: "Escalate criticals early", detail: "Model isolation and output guardrails become the priority once critical events rise.", value: socKpis ? String(critical) : "--" },
            ].map((item) => (
              <div key={item.title} className="rounded-2xl border border-slate-200/80 bg-white/75 px-4 py-4 dark:border-slate-700 dark:bg-slate-950/45">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <div className="text-sm font-semibold text-slate-900 dark:text-slate-100">{item.title}</div>
                    <div className="mt-1 text-sm leading-6 text-slate-600 dark:text-slate-400">{item.detail}</div>
                  </div>
                  <div className="rounded-full bg-slate-950 px-3 py-1 text-sm font-semibold text-white dark:bg-slate-100 dark:text-slate-950">{item.value}</div>
                </div>
              </div>
            ))}
          </div>
      </div>

      <section className="space-y-5">
        <SectionHeading
          eyebrow="Telemetry"
          title="Traffic and enforcement overview"
          description="The top-level charts stay focused on operator decisions: volume, intervention mix, and where the firewall is absorbing pressure right now."
        />
        <GlobalTrafficOverview socKpis={socKpis} timeSeriesData={timeSeriesData} loading={loading} compact={compact} />
      </section>

      <section className="space-y-5">
        <SectionHeading
          eyebrow="Submodules"
          title="AI Mesh Firewall command grid"
          description="Each submodule card exposes the operating surface, pressure signal, and quickest next action without forcing a context switch into raw tables first."
          action={<span className="text-sm text-slate-500 dark:text-slate-400">{subModules.length} active modules</span>}
        />
        <div className="grid gap-5 md:grid-cols-2 xl:grid-cols-3">
          {subModules.map((module) => (
            <SubModuleCard key={module.id} {...module} chartData={getModuleChartData(module.id)} onViewDetails={() => onTabChange?.(module.tabId)} />
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
          title="Traffic and block-rate comparisons"
          description="Side-by-side view of event volume and intervention rate for every firewall lane — quickly spot which modules are under the heaviest load."
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
