import { useState, useEffect, useCallback, useRef } from "react";
import { BarChart3, RefreshCw, TrendingUp, Shield, Clock, Activity } from "lucide-react";
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell, Legend,
  PieChart, Pie, RadarChart, Radar, PolarGrid, PolarAngleAxis, PolarRadiusAxis,
} from "recharts";
import { InfoTooltip } from "./InfoTooltip";
import { useAuth } from "../context/AuthContext";

const TIME_RANGES = ["1h", "6h", "24h", "7d", "30d"];
const STAGE_COLORS = { query: "#14b8a6", retriever: "#8b5cf6", ranker: "#f59e0b", generator: "#ef4444" };
const ACTION_COLORS = { allowed: "#22c55e", blocked: "#ef4444", flagged: "#f59e0b", rewritten: "#3b82f6" };
const ESCALATION_COLORS = { normal: "#22c55e", elevated: "#f59e0b", strict: "#ef4444" };
const STAGE_NAMES = ["query", "retriever", "ranker", "generator"];

function SafeResponsiveChart({ className, children }) {
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
    <div ref={containerRef} className={className}>
      {isReady ? (
        <ResponsiveContainer width="100%" height="100%">
          {children}
        </ResponsiveContainer>
      ) : (
        <div className="h-full flex items-center justify-center text-xs text-slate-400 dark:text-slate-500">Preparing chart...</div>
      )}
    </div>
  );
}

function StageHealthHeatmap({ stages }) {
  const metrics = ["total", "blocked", "flagged", "rewritten", "allowed"];
  const metricLabels = { total: "Total", blocked: "Blocked", flagged: "Flagged", rewritten: "Rewritten", allowed: "Allowed" };

  const getIntensity = (value, metric) => {
    if (!value) return "bg-slate-100 dark:bg-slate-800";
    if (metric === "blocked") {
      if (value > 50) return "bg-red-600";
      if (value > 10) return "bg-red-500/60";
      if (value > 0) return "bg-red-500/30";
      return "bg-slate-100 dark:bg-slate-800";
    }
    if (metric === "flagged" || metric === "rewritten") {
      if (value > 20) return "bg-amber-500/60";
      if (value > 0) return "bg-amber-500/30";
      return "bg-slate-100 dark:bg-slate-800";
    }
    if (metric === "allowed") {
      if (value > 100) return "bg-emerald-600";
      if (value > 10) return "bg-emerald-500/60";
      if (value > 0) return "bg-emerald-500/30";
      return "bg-slate-100 dark:bg-slate-800";
    }
    // total
    if (value > 100) return "bg-teal-600";
    if (value > 10) return "bg-teal-500/60";
    if (value > 0) return "bg-teal-500/30";
    return "bg-slate-100 dark:bg-slate-800";
  };

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr>
            <th className="text-left text-slate-400 dark:text-slate-500 p-2">Stage</th>
            {metrics.map((m) => (
              <th key={m} className="text-center text-slate-400 dark:text-slate-500 p-2">{metricLabels[m]}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {STAGE_NAMES.map((stage) => (
            <tr key={stage}>
              <td className="p-2">
                <div className="flex items-center gap-2">
                  <div className="w-2 h-2 rounded-full" style={{ backgroundColor: STAGE_COLORS[stage] }} />
                  <span className="text-slate-700 dark:text-slate-300 capitalize font-medium">{stage}</span>
                </div>
              </td>
              {metrics.map((metric) => {
                const value = stages[stage]?.[metric] || 0;
                return (
                  <td key={metric} className="p-1.5">
                    <div className={`${getIntensity(value, metric)} rounded-lg p-2 text-center transition-colors`}>
                      <span className="text-slate-900 dark:text-slate-100 font-mono font-bold">{value.toLocaleString()}</span>
                    </div>
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function RAGPipelineTelemetry({ ragPipelineKpis: externalKpis }) {
  const { fetchWithAuth } = useAuth();
  const [timeRange, setTimeRange] = useState("24h");
  const [internalKpis, setInternalKpis] = useState(null);
  const [loading, setLoading] = useState(!externalKpis);
  const [fetchError, setFetchError] = useState(null);

  const fetchKpis = useCallback(async () => {
    if (externalKpis) return;
    setLoading(true);
    setFetchError(null);
    try {
      const res = await fetchWithAuth(`/api/security/rag-pipeline-kpis/?period=${timeRange}`);
      if (res.ok) {
        setInternalKpis(await res.json());
      } else {
        console.error("[RAGPipelineTelemetry] KPI fetch failed:", res.status);
        setFetchError(`Failed to load telemetry (HTTP ${res.status})`);
      }
    } catch (err) {
      console.error("[RAGPipelineTelemetry] KPI fetch error:", err);
      setFetchError(`Failed to load telemetry: ${err.message}`);
    } finally {
      setLoading(false);
    }
  }, [timeRange, externalKpis, fetchWithAuth]);

  useEffect(() => { fetchKpis(); }, [fetchKpis]);

  const kpis = externalKpis || internalKpis;
  const stages = kpis?.stages || {};
  const escalation = kpis?.escalation_distribution || {};

  // Stacked bar data: per-stage action breakdown
  const stageActionData = STAGE_NAMES.map((s) => ({
    stage: s.charAt(0).toUpperCase() + s.slice(1),
    Allowed: stages[s]?.allowed || 0,
    Blocked: stages[s]?.blocked || 0,
    Flagged: stages[s]?.flagged || 0,
    Rewritten: stages[s]?.rewritten || 0,
  }));

  // Pie chart for escalation
  const escalationPieData = [
    { name: "Normal", value: escalation.normal || 0, fill: ESCALATION_COLORS.normal },
    { name: "Elevated", value: escalation.elevated || 0, fill: ESCALATION_COLORS.elevated },
    { name: "Strict", value: escalation.strict || 0, fill: ESCALATION_COLORS.strict },
  ].filter((d) => d.value > 0);

  // Latency comparison bar
  const latencyData = STAGE_NAMES.map((s) => ({
    stage: s.charAt(0).toUpperCase() + s.slice(1),
    latency: stages[s]?.avg_latency_ms || 0,
    fill: STAGE_COLORS[s],
  }));

  // Radar: stage health scores
  const radarData = STAGE_NAMES.map((s) => {
    const total = stages[s]?.total || 0;
    const blocked = stages[s]?.blocked || 0;
    const successRate = total > 0 ? ((total - blocked) / total) * 100 : 100;
    return { stage: s.charAt(0).toUpperCase() + s.slice(1), score: Math.round(successRate) };
  });

  return (
    <div className="ai-mesh-card ai-mesh-grid-bg rounded-3xl p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-3">
          <div className="p-2 rounded-lg bg-purple-500/10 border border-purple-500/20">
            <BarChart3 className="text-purple-400" size={20} />
          </div>
          <div>
            <h3 className="text-slate-900 dark:text-slate-100 font-semibold text-base">RAG Pipeline Telemetry</h3>
            <p className="text-slate-500 dark:text-slate-400 text-xs">Stage-level analytics, health heatmap, and distributions</p>
          </div>
          <InfoTooltip text="Aggregated telemetry from all RAG pipeline executions showing per-stage action distribution, escalation patterns, latency breakdown, and stage health scores." />
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          {TIME_RANGES.map((r) => (
            <button
              key={r}
              onClick={() => setTimeRange(r)}
              className={`px-3 py-1 text-xs rounded-lg border transition-all ${
                timeRange === r
                  ? "bg-purple-500/20 border-purple-500/40 text-purple-600 dark:text-purple-400"
                  : "border-slate-200 dark:border-slate-700 text-slate-400 dark:text-slate-500 hover:text-slate-900 dark:hover:text-white"
              }`}
            >
              {r}
            </button>
          ))}
          <button onClick={fetchKpis} className="p-1.5 rounded-lg border border-slate-200 dark:border-slate-700 text-slate-400 dark:text-slate-500 hover:text-slate-900 dark:hover:text-white transition-all">
            <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
          </button>
        </div>
      </div>

      {fetchError && (
        <div className="p-3 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg text-xs text-red-700 dark:text-red-300 flex items-start gap-2">
          <Shield className="w-4 h-4 flex-shrink-0 mt-0.5" /> <div>{fetchError}</div>
        </div>
      )}

      {/* Stage Health Heatmap */}
      <div className="bg-slate-50 dark:bg-slate-800/50 rounded-xl border border-slate-200 dark:border-slate-700 p-4">
        <div className="flex items-center gap-2 mb-3">
          <Activity size={14} className="text-teal-400" />
          <h4 className="text-sm font-medium text-slate-700 dark:text-slate-300">Stage Health Heatmap</h4>
        </div>
        <StageHealthHeatmap stages={stages} />
      </div>

      {/* Charts row: Stacked bar + Escalation pie + Radar */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* Stacked Bar: Action distribution per stage */}
        <div className="bg-slate-50 dark:bg-slate-800/50 rounded-xl border border-slate-200 dark:border-slate-700 p-4">
          <div className="flex items-center gap-2 mb-3">
            <TrendingUp size={14} className="text-blue-400" />
            <h4 className="text-sm font-medium text-slate-700 dark:text-slate-300">Action Distribution</h4>
          </div>
          <div className="h-52">
            <SafeResponsiveChart className="h-full">
              <BarChart data={stageActionData} margin={{ left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#3f3f46" />
                <XAxis dataKey="stage" tick={{ fill: "#a1a1aa", fontSize: 11 }} />
                <YAxis tick={{ fill: "#a1a1aa", fontSize: 11 }} />
                <Tooltip contentStyle={{ backgroundColor: "#18181b", borderColor: "#3f3f46", borderRadius: 8, fontSize: 12, color: "#e2e8f0" }} />
                <Legend wrapperStyle={{ fontSize: 11 }} />
                <Bar dataKey="Allowed" stackId="a" fill={ACTION_COLORS.allowed} radius={[0, 0, 0, 0]} />
                <Bar dataKey="Blocked" stackId="a" fill={ACTION_COLORS.blocked} />
                <Bar dataKey="Flagged" stackId="a" fill={ACTION_COLORS.flagged} />
                <Bar dataKey="Rewritten" stackId="a" fill={ACTION_COLORS.rewritten} radius={[4, 4, 0, 0]} />
              </BarChart>
            </SafeResponsiveChart>
          </div>
        </div>

        {/* Pie: Escalation Distribution */}
        <div className="bg-slate-50 dark:bg-slate-800/50 rounded-xl border border-slate-200 dark:border-slate-700 p-4">
          <div className="flex items-center gap-2 mb-3">
            <Shield size={14} className="text-amber-400" />
            <h4 className="text-sm font-medium text-slate-700 dark:text-slate-300">Escalation Distribution</h4>
          </div>
          <div className="h-52">
            {escalationPieData.length > 0 ? (
              <SafeResponsiveChart className="h-full">
                <PieChart>
                  <Pie
                    data={escalationPieData}
                    cx="50%"
                    cy="50%"
                    innerRadius={40}
                    outerRadius={70}
                    paddingAngle={3}
                    dataKey="value"
                    label={({ name, percent }) => `${name} ${(percent * 100).toFixed(0)}%`}
                    labelLine={{ stroke: "#71717a", strokeWidth: 1 }}
                  >
                    {escalationPieData.map((entry, i) => <Cell key={i} fill={entry.fill} />)}
                  </Pie>
                  <Tooltip contentStyle={{ backgroundColor: "#18181b", borderColor: "#3f3f46", borderRadius: 8, fontSize: 12, color: "#e2e8f0" }} />
                </PieChart>
              </SafeResponsiveChart>
            ) : (
              <div className="h-full flex items-center justify-center text-slate-400 dark:text-slate-500 text-sm">No data for period</div>
            )}
          </div>
        </div>

        {/* Radar: Stage Health */}
        <div className="bg-slate-50 dark:bg-slate-800/50 rounded-xl border border-slate-200 dark:border-slate-700 p-4">
          <div className="flex items-center gap-2 mb-3">
            <Shield size={14} className="text-emerald-400" />
            <h4 className="text-sm font-medium text-slate-700 dark:text-slate-300">Stage Health Score</h4>
          </div>
          <div className="h-52">
            <SafeResponsiveChart className="h-full">
              <RadarChart data={radarData}>
                <PolarGrid stroke="#3f3f46" />
                <PolarAngleAxis dataKey="stage" tick={{ fill: "#a1a1aa", fontSize: 11 }} />
                <PolarRadiusAxis domain={[0, 100]} tick={{ fill: "#71717a", fontSize: 9 }} />
                <Radar name="Health" dataKey="score" stroke="#14b8a6" fill="#14b8a6" fillOpacity={0.3} />
                <Tooltip contentStyle={{ backgroundColor: "#18181b", borderColor: "#3f3f46", borderRadius: 8, fontSize: 12, color: "#e2e8f0" }} />
              </RadarChart>
            </SafeResponsiveChart>
          </div>
        </div>
      </div>

      {/* Latency comparison */}
      <div className="bg-slate-50 dark:bg-slate-800/50 rounded-xl border border-slate-200 dark:border-slate-700 p-4">
        <div className="flex items-center gap-2 mb-3">
          <Clock size={14} className="text-blue-400" />
          <h4 className="text-sm font-medium text-slate-700 dark:text-slate-300">Average Latency by Stage (ms)</h4>
        </div>
        <div className="h-36">
          <SafeResponsiveChart className="h-full">
            <BarChart data={latencyData} margin={{ left: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#3f3f46" />
              <XAxis dataKey="stage" tick={{ fill: "#a1a1aa", fontSize: 11 }} />
              <YAxis tick={{ fill: "#a1a1aa", fontSize: 11 }} />
              <Tooltip contentStyle={{ backgroundColor: "#18181b", borderColor: "#3f3f46", borderRadius: 8, fontSize: 12, color: "#e2e8f0" }} />
              <Bar dataKey="latency" radius={[4, 4, 0, 0]}>
                {latencyData.map((entry, i) => <Cell key={i} fill={entry.fill} />)}
              </Bar>
            </BarChart>
          </SafeResponsiveChart>
        </div>
      </div>
    </div>
  );
}
