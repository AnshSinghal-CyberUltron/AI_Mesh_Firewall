import { useState, useEffect, useCallback, useRef } from "react";
import {
  BarChart3, RefreshCw, TrendingUp, Shield, Clock, Activity,
  ArrowRight, ShieldAlert, AlertTriangle, ChevronDown, Zap,
} from "lucide-react";
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
const STAGE_LABELS = { query: "Query Stage", retriever: "Retriever Stage", ranker: "Ranker Stage", generator: "Generator Stage" };
const STAGE_ICONS = { query: "Q", retriever: "R", ranker: "K", generator: "G" };
const STAGE_DESCRIPTIONS = {
  query: "Scans incoming queries for prompt injection, applies rewrite or block decisions",
  retriever: "Manages document retrieval with chain-of-custody tracking and circuit breaker protection",
  ranker: "Filters and scores documents using trust scoring, anomaly detection, and policy-based rules",
  generator: "Verifies approved context integrity, applies field-level redaction, and grounds responses",
};
const FUNNEL_COLORS = ["#8b5cf6", "#14b8a6", "#f59e0b"];

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

function StageCard({ name, data, isLast, isExpanded, onToggle }) {
  const label = STAGE_LABELS[name] || name;
  const color = STAGE_COLORS[name] || "#6b7280";
  const icon = STAGE_ICONS[name] || "?";
  const desc = STAGE_DESCRIPTIONS[name] || "";
  const total = data?.total ?? 0;
  const blocked = data?.blocked ?? 0;
  const flagged = data?.flagged ?? 0;
  const rewritten = data?.rewritten ?? 0;
  const allowed = data?.allowed ?? 0;
  const avgLatency = data?.avg_latency_ms ?? 0;

  let healthStatus = "healthy";
  if (blocked > 0) { healthStatus = "critical"; }
  else if (flagged > 0 || rewritten > 0) { healthStatus = "warning"; }

  const statusStyles = {
    healthy: "from-emerald-500/10 to-emerald-500/5 border-emerald-500/30",
    warning: "from-amber-500/10 to-amber-500/5 border-amber-500/30",
    critical: "from-red-500/10 to-red-500/5 border-red-500/30",
  };
  const statusShadows = {
    healthy: "hover:shadow-emerald-500/10",
    warning: "hover:shadow-amber-500/10",
    critical: "hover:shadow-red-500/10",
  };

  return (
    <div className="flex items-start">
      <div className="flex flex-col items-center w-full">
        {/* Main card */}
        <button
          onClick={onToggle}
          className={`relative w-full rounded-xl border bg-gradient-to-b ${statusStyles[healthStatus]} p-4 text-left transition-all hover:shadow-lg ${statusShadows[healthStatus]}`}
        >
          {/* Pulse indicator */}
          <div className="absolute -top-1.5 -right-1.5">
            <div className={`w-3 h-3 rounded-full ${healthStatus === "critical" ? "bg-red-500" : healthStatus === "warning" ? "bg-amber-500" : "bg-emerald-500"}`}>
              <div className={`w-3 h-3 rounded-full ${healthStatus === "critical" ? "bg-red-500" : healthStatus === "warning" ? "bg-amber-500" : "bg-emerald-500"} animate-ping absolute`} />
            </div>
          </div>

          {/* Header */}
          <div className="flex items-center gap-2 mb-3">
            <div className="w-7 h-7 rounded-lg flex items-center justify-center text-white text-xs font-bold" style={{ backgroundColor: color }}>
              {icon}
            </div>
            <div className="flex-1">
              <span className="text-sm font-semibold text-slate-900 dark:text-slate-100">{label}</span>
              <p className="text-[10px] text-slate-400 dark:text-slate-500 leading-tight">{desc}</p>
            </div>
            <ChevronDown size={14} className={`text-slate-400 dark:text-slate-500 transition-transform ${isExpanded ? "rotate-180" : ""}`} />
          </div>

          {/* Quick stats */}
          <div className="grid grid-cols-2 gap-2">
            <div className="bg-slate-50 dark:bg-slate-800/50 rounded-lg p-2 text-center">
              <div className="text-[10px] text-slate-400 dark:text-slate-500">Total</div>
              <div className="text-sm font-mono font-bold text-slate-900 dark:text-slate-100">{total.toLocaleString()}</div>
            </div>
            <div className="bg-slate-50 dark:bg-slate-800/50 rounded-lg p-2 text-center">
              <div className="text-[10px] text-slate-400 dark:text-slate-500">Blocked</div>
              <div className="text-sm font-mono font-bold text-red-400">{blocked.toLocaleString()}</div>
            </div>
            <div className="bg-slate-50 dark:bg-slate-800/50 rounded-lg p-2 text-center">
              <div className="text-[10px] text-slate-400 dark:text-slate-500">Latency</div>
              <div className="text-sm font-mono font-bold text-slate-700 dark:text-slate-300">{avgLatency.toFixed(1)}ms</div>
            </div>
            <div className="bg-slate-50 dark:bg-slate-800/50 rounded-lg p-2 text-center">
              <div className="text-[10px] text-slate-400 dark:text-slate-500">
                {rewritten > 0 ? "Rewritten" : flagged > 0 ? "Flagged" : "Allowed"}
              </div>
              <div className={`text-sm font-mono font-bold ${rewritten > 0 ? "text-blue-400" : flagged > 0 ? "text-amber-400" : "text-emerald-400"}`}>
                {rewritten > 0 ? rewritten : flagged > 0 ? flagged : allowed}
              </div>
            </div>
          </div>

          {/* Health bar */}
          {total > 0 && (
            <div className="mt-3 h-1.5 rounded-full bg-slate-200 dark:bg-slate-700 overflow-hidden flex">
              {allowed > 0 && <div className="h-full bg-emerald-500 transition-all" style={{ width: `${(allowed / total) * 100}%` }} />}
              {blocked > 0 && <div className="h-full bg-red-500 transition-all" style={{ width: `${(blocked / total) * 100}%` }} />}
              {flagged > 0 && <div className="h-full bg-amber-500 transition-all" style={{ width: `${(flagged / total) * 100}%` }} />}
              {rewritten > 0 && <div className="h-full bg-blue-500 transition-all" style={{ width: `${(rewritten / total) * 100}%` }} />}
            </div>
          )}
        </button>

        {/* Expanded detail */}
        {isExpanded && (
          <div className="w-full mt-2 bg-slate-50 dark:bg-slate-800/30 rounded-lg border border-slate-200 dark:border-slate-700/50 p-3 space-y-2">
            <div className="grid grid-cols-2 gap-2 text-xs">
              <div className="flex justify-between"><span className="text-slate-400 dark:text-slate-500">Allowed</span><span className="text-emerald-400 font-mono">{allowed}</span></div>
              <div className="flex justify-between"><span className="text-slate-400 dark:text-slate-500">Blocked</span><span className="text-red-400 font-mono">{blocked}</span></div>
              <div className="flex justify-between"><span className="text-slate-400 dark:text-slate-500">Flagged</span><span className="text-amber-400 font-mono">{flagged}</span></div>
              <div className="flex justify-between"><span className="text-slate-400 dark:text-slate-500">Rewritten</span><span className="text-blue-400 font-mono">{rewritten}</span></div>
            </div>
            {total > 0 && (
              <div className="text-[10px] text-slate-400 dark:text-slate-500 mt-1">
                Block rate: <span className="text-slate-900 dark:text-slate-100 font-mono">{((blocked / total) * 100).toFixed(1)}%</span>
                {rewritten > 0 && <> | Rewrite rate: <span className="text-slate-900 dark:text-slate-100 font-mono">{((rewritten / total) * 100).toFixed(1)}%</span></>}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Arrow connector */}
      {!isLast && (
        <div className="flex items-center px-2 mt-10 flex-shrink-0">
          <div className="w-6 h-px bg-gradient-to-r from-slate-300 dark:from-slate-600 to-slate-400 dark:to-slate-500" />
          <ArrowRight size={12} className="text-slate-400 dark:text-slate-500 -ml-0.5" />
        </div>
      )}
    </div>
  );
}

export function RAGPipelineTelemetry({ ragPipelineKpis: externalKpis }) {
  const { fetchWithAuth } = useAuth();
  const [timeRange, setTimeRange] = useState("24h");
  const [internalKpis, setInternalKpis] = useState(null);
  const [loading, setLoading] = useState(!externalKpis);
  const [fetchError, setFetchError] = useState(null);
  const [activeTab, setActiveTab] = useState("flow");
  const [expandedStage, setExpandedStage] = useState(null);

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
  const funnel = kpis?.document_funnel || {};

  // --- Pipeline Flow tab data ---
  const funnelData = [
    { name: "Retrieved", value: funnel.retrieved || 0 },
    { name: "Post-Ranker", value: funnel.post_ranker || 0 },
    { name: "Post-Generator", value: funnel.post_generator || 0 },
  ];

  const totalEscalation = (escalation.normal || 0) + (escalation.elevated || 0) + (escalation.strict || 0);

  // Overall summary stats (sum across stages)
  const totalEvents = STAGE_NAMES.reduce((sum, s) => sum + (stages[s]?.total || 0), 0);
  const totalBlocked = STAGE_NAMES.reduce((sum, s) => sum + (stages[s]?.blocked || 0), 0);
  const avgLatency = STAGE_NAMES.reduce((sum, s) => sum + (stages[s]?.avg_latency_ms || 0), 0);

  // --- Analytics tab data ---
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

  const TABS = [
    { id: "flow", label: "Pipeline Flow" },
    { id: "analytics", label: "Analytics" },
  ];

  return (
    <div className="ai-mesh-card ai-mesh-grid-bg rounded-3xl p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-3">
          <div className="p-2 rounded-lg bg-purple-500/10 border border-purple-500/20">
            <BarChart3 className="text-purple-400" size={20} />
          </div>
          <div>
            <h3 className="text-slate-900 dark:text-slate-100 font-semibold text-base">RAG Pipeline Monitor &amp; Telemetry</h3>
            <p className="text-slate-500 dark:text-slate-400 text-xs">Interactive stage flow + stage-level analytics</p>
          </div>
          <InfoTooltip text="Unified view of the 4-stage RAG firewall pipeline (Query → Retriever → Ranker → Generator). The interactive Pipeline Flow tab shows per-stage enforcement with click-to-expand cards, the document filtering funnel, and escalation levels. The Analytics tab adds the stage health heatmap, action distribution, escalation breakdown, health scores, and latency analytics." />
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          {/* Mode toggle pills */}
          <div className="flex items-center gap-1 p-0.5 rounded-lg bg-slate-100 dark:bg-slate-800/70 border border-slate-200 dark:border-slate-700">
            {TABS.map((tab) => (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={`px-3 py-1 text-xs rounded-md font-medium transition-all ${
                  activeTab === tab.id
                    ? "bg-purple-500/20 text-purple-600 dark:text-purple-400 shadow-sm"
                    : "text-slate-400 dark:text-slate-500 hover:text-slate-900 dark:hover:text-white"
                }`}
              >
                {tab.label}
              </button>
            ))}
          </div>
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

      {/* ===================== Pipeline Flow tab ===================== */}
      {activeTab === "flow" && (
        <div className="space-y-6">
          {/* Summary KPIs */}
          <div className="grid grid-cols-3 gap-3">
            <div className="bg-slate-50 dark:bg-slate-800/50 rounded-xl border border-slate-200 dark:border-slate-700 p-3 text-center">
              <Zap size={14} className="text-teal-400 mx-auto mb-1" />
              <div className="text-lg font-bold text-slate-900 dark:text-slate-100 font-mono">{totalEvents.toLocaleString()}</div>
              <div className="text-[10px] text-slate-400 dark:text-slate-500">Total Pipeline Events</div>
            </div>
            <div className="bg-slate-50 dark:bg-slate-800/50 rounded-xl border border-slate-200 dark:border-slate-700 p-3 text-center">
              <ShieldAlert size={14} className="text-red-400 mx-auto mb-1" />
              <div className="text-lg font-bold text-red-400 font-mono">{totalBlocked.toLocaleString()}</div>
              <div className="text-[10px] text-slate-400 dark:text-slate-500">Total Blocked</div>
            </div>
            <div className="bg-slate-50 dark:bg-slate-800/50 rounded-xl border border-slate-200 dark:border-slate-700 p-3 text-center">
              <Clock size={14} className="text-blue-400 mx-auto mb-1" />
              <div className="text-lg font-bold text-slate-900 dark:text-slate-100 font-mono">{avgLatency.toFixed(0)}ms</div>
              <div className="text-[10px] text-slate-400 dark:text-slate-500">Total Avg Latency</div>
            </div>
          </div>

          {/* Pipeline Flow - Interactive stage cards */}
          <div className="flex items-start justify-center gap-0 overflow-x-auto py-2">
            {STAGE_NAMES.map((name, i) => (
              <StageCard
                key={name}
                name={name}
                data={stages[name]}
                isLast={i === STAGE_NAMES.length - 1}
                isExpanded={expandedStage === name}
                onToggle={() => setExpandedStage(expandedStage === name ? null : name)}
              />
            ))}
          </div>

          {/* Bottom row: Funnel + Escalation */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {/* Document Funnel */}
            <div className="bg-slate-50 dark:bg-slate-800/50 rounded-xl border border-slate-200 dark:border-slate-700 p-4">
              <div className="flex items-center gap-2 mb-3">
                <TrendingUp size={14} className="text-purple-400" />
                <h4 className="text-sm font-medium text-slate-700 dark:text-slate-300">Document Filtering Funnel</h4>
              </div>
              <div className="h-40">
                <SafeResponsiveChart className="h-full">
                  <BarChart data={funnelData} layout="vertical" margin={{ left: 20, right: 20 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#3f3f46" />
                    <XAxis type="number" tick={{ fill: "#a1a1aa", fontSize: 11 }} />
                    <YAxis type="category" dataKey="name" tick={{ fill: "#a1a1aa", fontSize: 11 }} width={90} />
                    <Tooltip contentStyle={{ backgroundColor: "#18181b", borderColor: "#3f3f46", borderRadius: 8, color: "#e2e8f0" }} />
                    <Bar dataKey="value" radius={[0, 4, 4, 0]}>
                      {funnelData.map((_, i) => <Cell key={i} fill={FUNNEL_COLORS[i]} />)}
                    </Bar>
                  </BarChart>
                </SafeResponsiveChart>
              </div>
              {funnel.retrieved > 0 && (
                <div className="text-[10px] text-slate-400 dark:text-slate-500 mt-2 text-center">
                  Filtering rate: <span className="text-slate-900 dark:text-slate-100 font-mono">{((1 - (funnel.post_generator || 0) / funnel.retrieved) * 100).toFixed(1)}%</span> of documents filtered across pipeline
                </div>
              )}
            </div>

            {/* Escalation Distribution */}
            <div className="bg-slate-50 dark:bg-slate-800/50 rounded-xl border border-slate-200 dark:border-slate-700 p-4">
              <div className="flex items-center gap-2 mb-3">
                <AlertTriangle size={14} className="text-amber-400" />
                <h4 className="text-sm font-medium text-slate-700 dark:text-slate-300">Escalation Level Distribution</h4>
              </div>
              <div className="space-y-3 mt-4">
                {[
                  { label: "Normal (Level 0)", desc: "Standard enforcement", value: escalation.normal || 0, color: "bg-emerald-500", textColor: "text-emerald-400" },
                  { label: "Elevated (Level 1)", desc: "Stricter filtering active", value: escalation.elevated || 0, color: "bg-amber-500", textColor: "text-amber-400" },
                  { label: "Strict (Level 2)", desc: "Maximum enforcement", value: escalation.strict || 0, color: "bg-red-500", textColor: "text-red-400" },
                ].map((item) => {
                  const pct = totalEscalation > 0 ? ((item.value / totalEscalation) * 100).toFixed(1) : 0;
                  return (
                    <div key={item.label} className="space-y-1">
                      <div className="flex justify-between text-xs">
                        <div>
                          <span className="text-slate-700 dark:text-slate-300">{item.label}</span>
                          <span className="text-slate-500 dark:text-slate-500 text-[10px] ml-2">{item.desc}</span>
                        </div>
                        <span className={`${item.textColor} font-mono`}>{item.value} ({pct}%)</span>
                      </div>
                      <div className="h-2 rounded-full bg-slate-200 dark:bg-slate-700 overflow-hidden">
                        <div className={`h-full rounded-full ${item.color} transition-all duration-500`} style={{ width: `${pct}%` }} />
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ===================== Analytics tab ===================== */}
      {activeTab === "analytics" && (
        <div className="space-y-6">
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
      )}
    </div>
  );
}
