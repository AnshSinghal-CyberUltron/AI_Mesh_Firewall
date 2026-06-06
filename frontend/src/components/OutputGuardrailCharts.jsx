import { useState, useEffect, useCallback } from "react";
import { BarChart3, RefreshCw, Loader2, Eye } from "lucide-react";
import {
  AreaChart, Area, PieChart, Pie, Cell, BarChart, Bar,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend,
} from "recharts";
import { useAuth } from "../context/AuthContext";
import { InfoTooltip } from "./InfoTooltip";
import { SafeResponsiveChart } from "./SafeResponsiveChart";
import { actionColor, OUTPUT_ACTION_COLORS } from "../constants/outputGuardColors";

// §1.7 Output Guardrail analytics — live recharts views bound to the existing
// module-scoped endpoint /api/security/module-charts/1.7/?period=<lens>. The
// period follows the page operator lens (default 7d) so every surface agrees.

const TOOLTIP_STYLE = {
  backgroundColor: "#0f172a",
  borderColor: "#334155",
  borderRadius: 10,
  fontSize: 12,
  color: "#e2e8f0",
};
const AXIS_TICK = { fill: "#94a3b8", fontSize: 11 };
const TIMELINE_SERIES = [
  { key: "blocked", color: OUTPUT_ACTION_COLORS.block },
  { key: "redacted", color: OUTPUT_ACTION_COLORS.redact },
  { key: "monitored", color: OUTPUT_ACTION_COLORS.monitor },
  { key: "allowed", color: OUTPUT_ACTION_COLORS.allow },
];

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

const cap = (s) => String(s || "").charAt(0).toUpperCase() + String(s || "").slice(1);

export function OutputGuardrailCharts({ timeRange = "7d", moduleId = "1.7" }) {
  const { fetchWithAuth } = useAuth();
  const [charts, setCharts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const fetchCharts = useCallback(
    async (background = false) => {
      if (background) setRefreshing(true);
      try {
        const res = await fetchWithAuth(`/api/security/module-charts/${moduleId}/?period=${timeRange}`);
        if (res.ok) {
          const data = await res.json();
          setCharts(Array.isArray(data.charts) ? data.charts : []);
        }
      } catch {
        /* non-blocking */
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
              {"Live recharts views of generator-level output enforcement: outputs over time, action distribution, detected categories, and risk-score spread. Every chart honors the operator lens window above."}
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
              <div className="h-[220px]">
                <SafeResponsiveChart className="h-full">
                  <AreaChart data={timeline.data} margin={{ top: 8, right: 8, left: -16, bottom: 0 }}>
                    <defs>
                      {TIMELINE_SERIES.map((s) => (
                        <linearGradient key={s.key} id={`og-${s.key}`} x1="0" y1="0" x2="0" y2="1">
                          <stop offset="5%" stopColor={s.color} stopOpacity={0.5} />
                          <stop offset="95%" stopColor={s.color} stopOpacity={0.05} />
                        </linearGradient>
                      ))}
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" stroke="#3f3f46" vertical={false} />
                    <XAxis dataKey="time" tick={AXIS_TICK} interval="preserveStartEnd" minTickGap={48} />
                    <YAxis tick={AXIS_TICK} allowDecimals={false} width={32} />
                    <Tooltip contentStyle={TOOLTIP_STYLE} />
                    <Legend wrapperStyle={{ fontSize: 11 }} />
                    {TIMELINE_SERIES.map((s) => (
                      <Area
                        key={s.key}
                        type="monotone"
                        dataKey={s.key}
                        name={cap(s.key)}
                        stackId="1"
                        stroke={s.color}
                        fill={`url(#og-${s.key})`}
                        strokeWidth={1.5}
                      />
                    ))}
                  </AreaChart>
                </SafeResponsiveChart>
              </div>
            </ChartFrame>
          )}

          <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
            {/* Action distribution donut */}
            {actions && (
              <ChartFrame title="Action Distribution">
                <div className="h-[200px]">
                  <SafeResponsiveChart className="h-full">
                    <PieChart>
                      <Pie
                        data={actions.data}
                        dataKey="value"
                        nameKey="name"
                        cx="50%"
                        cy="50%"
                        innerRadius={42}
                        outerRadius={70}
                        paddingAngle={2}
                        label={({ name, percent }) => `${name} ${(percent * 100).toFixed(0)}%`}
                        labelLine={false}
                      >
                        {actions.data.map((d, i) => (
                          <Cell key={i} fill={actionColor(d.name)} />
                        ))}
                      </Pie>
                      <Tooltip contentStyle={TOOLTIP_STYLE} />
                    </PieChart>
                  </SafeResponsiveChart>
                </div>
              </ChartFrame>
            )}

            {/* Detected categories — horizontal bar */}
            {threats && (
              <ChartFrame title="Detected Categories">
                <div className="h-[200px]">
                  <SafeResponsiveChart className="h-full">
                    <BarChart data={threats.data} layout="vertical" margin={{ top: 4, right: 12, left: 8, bottom: 0 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#3f3f46" horizontal={false} />
                      <XAxis type="number" tick={AXIS_TICK} allowDecimals={false} />
                      <YAxis type="category" dataKey="name" tick={AXIS_TICK} width={92} />
                      <Tooltip contentStyle={TOOLTIP_STYLE} cursor={{ fill: "rgba(148,163,184,0.1)" }} />
                      <Bar dataKey="value" radius={[0, 4, 4, 0]}>
                        {threats.data.map((d, i) => (
                          <Cell key={i} fill={d.color || "#14b8a6"} />
                        ))}
                      </Bar>
                    </BarChart>
                  </SafeResponsiveChart>
                </div>
              </ChartFrame>
            )}

            {/* Risk score distribution — vertical bar */}
            {risk && (
              <ChartFrame title="Risk Score Spread">
                <div className="h-[200px]">
                  <SafeResponsiveChart className="h-full">
                    <BarChart data={risk.data} margin={{ top: 4, right: 8, left: -16, bottom: 0 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#3f3f46" vertical={false} />
                      <XAxis dataKey="range" tick={AXIS_TICK} />
                      <YAxis tick={AXIS_TICK} allowDecimals={false} width={32} />
                      <Tooltip contentStyle={TOOLTIP_STYLE} cursor={{ fill: "rgba(148,163,184,0.1)" }} />
                      <Bar dataKey="count" radius={[4, 4, 0, 0]}>
                        {risk.data.map((d, i) => {
                          const lo = parseInt(d.range, 10) || 0;
                          const fill = lo >= 80 ? "#ef4444" : lo >= 60 ? "#fb923c" : lo >= 40 ? "#f59e0b" : "#10b981";
                          return <Cell key={i} fill={fill} />;
                        })}
                      </Bar>
                    </BarChart>
                  </SafeResponsiveChart>
                </div>
              </ChartFrame>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export default OutputGuardrailCharts;
