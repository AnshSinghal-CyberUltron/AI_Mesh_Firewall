import { useCallback, useEffect, useState } from "react";
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid,
  ResponsiveContainer, Tooltip, XAxis, YAxis, PieChart, Pie, Cell,
} from "recharts";
import { Loader2, RefreshCw, MessageSquare, BookOpen, Database, Wrench } from "lucide-react";
import { useAuth } from "../../context/AuthContext";
import { useRealtimeNotifications } from "../../hooks/useRealtimeNotifications";
import { createModule2Api } from "../../api/module2";
import { PageHeader } from "../../components/module2/PageHeader";
import { KPIBar } from "../../components/module2/KPIBar";
import { ChartCard } from "../../components/module2/ChartCard";
import { DataTable } from "../../components/module2/DataTable";
import { PeriodSelector } from "../../components/module2/PeriodSelector";
import { ContextualAppBar } from "../../components/module2/ContextualAppBar";
import { InfoTooltip } from "../../components/module2/InfoTooltip";
import { ANALYST_BRIEF_TITLE, PAGE_BRIEFS } from "./pageCopy";

const LANE_META = {
  chat:   { label: "Chat",   icon: MessageSquare, color: "text-sky-500",   bg: "bg-sky-50 dark:bg-sky-900/20",   border: "border-sky-200 dark:border-sky-700", helpText: "Direct LLM chat/completion traffic through the gateway ingress." },
  rag:    { label: "RAG",    icon: BookOpen,       color: "text-violet-500", bg: "bg-violet-50 dark:bg-violet-900/20", border: "border-violet-200 dark:border-violet-700", helpText: "Retrieval-augmented requests tagged event_type=rag_pipeline." },
  vector: { label: "Vector", icon: Database,       color: "text-emerald-500", bg: "bg-emerald-50 dark:bg-emerald-900/20", border: "border-emerald-200 dark:border-emerald-700", helpText: "Vector DB access events with collection or namespace metadata." },
  mcp:    { label: "MCP",    icon: Wrench,         color: "text-amber-500",  bg: "bg-amber-50 dark:bg-amber-900/20", border: "border-amber-200 dark:border-amber-700", helpText: "Model Context Protocol tool-call enforcement events." },
};

const LANE_BADGE = {
  chat:       "bg-sky-100 text-sky-700 dark:bg-sky-900/40 dark:text-sky-300",
  rag:        "bg-violet-100 text-violet-700 dark:bg-violet-900/40 dark:text-violet-300",
  vector:     "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300",
  mcp:        "bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300",
  threat_intel: "bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300",
  ueba:       "bg-orange-100 text-orange-700 dark:bg-orange-900/40 dark:text-orange-300",
};

function LaneSummaryGrid({ laneSummary }) {
  return (
    <div className="mt-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      {Object.entries(LANE_META).map(([key, meta]) => {
        const stats = laneSummary?.[key] || { total: 0, blocked: 0, block_rate_pct: 0 };
        const Icon = meta.icon;
        return (
          <div key={key} className={`rounded-xl border p-4 ${meta.bg} ${meta.border}`}>
            <div className="flex items-center gap-2 mb-2">
              <Icon className={`h-5 w-5 ${meta.color}`} />
              <span className="text-sm font-semibold text-slate-700 dark:text-slate-200">{meta.label}</span>
              <InfoTooltip text={meta.helpText} />
            </div>
            <p className="text-2xl font-bold text-slate-900 dark:text-white">{stats.total.toLocaleString()}</p>
            <p className="text-xs text-slate-500 mt-1">
              <span className="text-red-500 font-medium">{stats.blocked}</span> blocked
              {" · "}<span className={meta.color + " font-medium"}>{stats.block_rate_pct}%</span> rate
            </p>
          </div>
        );
      })}
    </div>
  );
}

function RagFunnelChart({ ragFunnel }) {
  const stages = ragFunnel?.stages || {};
  const data = ["query", "retriever", "ranker", "generator"].map((s) => ({
    stage: s.charAt(0).toUpperCase() + s.slice(1),
    total: stages[s]?.total || 0,
    blocked: stages[s]?.blocked || 0,
  }));
  return (
    <ChartCard
      title="RAG Pipeline Funnel"
      titleHelpText="Stage-by-stage volume across Query → Retriever → Ranker → Generator. Retriever spikes often indicate ACL or cross-tenant access violations."
    >
      <ResponsiveContainer width="100%" height={200}>
        <BarChart data={data} layout="vertical" margin={{ left: 10 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#334155" opacity={0.3} />
          <XAxis type="number" fontSize={11} />
          <YAxis dataKey="stage" type="category" fontSize={11} width={70} />
          <Tooltip />
          <Bar dataKey="total" fill="#8b5cf6" fillOpacity={0.7} name="Total" />
          <Bar dataKey="blocked" fill="#ef4444" fillOpacity={0.8} name="Blocked" />
        </BarChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

export function DashboardPage() {
  const { fetchWithAuth } = useAuth();
  const api = createModule2Api(fetchWithAuth);
  const [period, setPeriod] = useState("24h");
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [feed, setFeed] = useState([]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.getDashboard(period);
      setData(res);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  }, [period]);

  useEffect(() => { load(); }, [load]);

  useRealtimeNotifications({
    onEnforcementEvent: (payload) => {
      setFeed((prev) => [payload, ...prev].slice(0, 20));
    },
  });

  if (loading && !data) {
    return (
      <div>
        <ContextualAppBar title={ANALYST_BRIEF_TITLE} description={PAGE_BRIEFS.dashboard} />
        <div className="flex items-center justify-center py-20">
          <Loader2 className="h-8 w-8 animate-spin text-teal-500" />
        </div>
      </div>
    );
  }

  const kpis = data?.kpis || {};
  const kpiItems = [
    { key: "total-events", label: "Total Events", value: kpis.total_events ?? 0, helpText: "All gateway enforcement events in the selected time window." },
    { key: "blocked", label: "Blocked", value: kpis.blocked ?? 0, color: "text-red-600", helpText: "Requests hard-stopped by policy (deny / kill-switch)." },
    { key: "redacted", label: "Redacted", value: kpis.redacted ?? 0, color: "text-amber-600", helpText: "Requests allowed after PII or sensitive fields were masked." },
    { key: "open-incidents", label: "Open Incidents", value: kpis.open_incidents ?? 0, color: "text-orange-600", helpText: "Cases still open, investigating, or escalated in the incident queue." },
    { key: "risky-keys", label: "Risky Keys", value: kpis.risky_keys ?? 0, color: "text-red-600", helpText: "API keys currently in the high UEBA risk band." },
    { key: "block-rate", label: "Block Rate", value: `${kpis.block_rate ?? 0}%`, helpText: "Hard-block rate across all events—useful for spotting enforcement spikes." },
  ];

  const violatorCols = [
    { key: "prefix", label: "Key Prefix", helpText: "Truncated API key ID for attribution without exposing the secret." },
    { key: "project_id", label: "Project", helpText: "Application or project scope tied to this key." },
    { key: "risk_band", label: "Risk", helpText: "UEBA tier (low / medium / high) from velocity, violations, and anomaly signals." },
    { key: "request_count", label: "Requests", helpText: "Event count for this key in the selected period." },
    { key: "velocity_spike", label: "Velocity x", helpText: "Traffic multiplier vs. this key's baseline; spikes may indicate compromise." },
  ];

  const riskDistribution = Object.entries(data?.key_risk_distribution || {}).map(([name, value]) => ({ name, value }));
  const riskColors = { high: "#ef4444", medium: "#f59e0b", low: "#10b981" };

  return (
    <div>
      <ContextualAppBar title={ANALYST_BRIEF_TITLE} description={PAGE_BRIEFS.dashboard} />
      <PageHeader
        title="SOC Command Center"
        subtitle="Gateway intelligence focused on key behavior, model exposure, and incidents"
        actions={
          <>
            <PeriodSelector value={period} onChange={setPeriod} />
            <button onClick={load} className="rounded-lg border border-slate-200 p-2 dark:border-slate-600">
              <RefreshCw className="h-4 w-4" />
            </button>
          </>
        }
      />

      <KPIBar items={kpiItems} />

      <LaneSummaryGrid laneSummary={data?.lane_summary} />

      <div className="mt-6 grid gap-4 lg:grid-cols-2">
        <ChartCard
          title="Threat Timeline"
          titleHelpText="Hourly enforcement volume split by total, blocked, and redacted outcomes."
        >
          <ResponsiveContainer width="100%" height={240}>
            <AreaChart data={data?.threat_trend || []}>
              <CartesianGrid strokeDasharray="3 3" stroke="#334155" opacity={0.3} />
              <XAxis dataKey="timestamp" tickFormatter={(v) => v?.slice(11, 16)} fontSize={11} />
              <YAxis fontSize={11} />
              <Tooltip />
              <Area type="monotone" dataKey="total" stackId="1" fill="#0ea5e9" stroke="#0ea5e9" fillOpacity={0.35} />
              <Area type="monotone" dataKey="blocked" stackId="2" fill="#ef4444" stroke="#ef4444" fillOpacity={0.45} />
              <Area type="monotone" dataKey="redacted" stackId="2" fill="#f59e0b" stroke="#f59e0b" fillOpacity={0.45} />
            </AreaChart>
          </ResponsiveContainer>
        </ChartCard>

        <ChartCard
          title="High-Risk Ticker"
          titleHelpText="Rolling feed of active incidents tagged by source lane for rapid triage."
        >
          <div className="max-h-60 space-y-2 overflow-y-auto">
            {(feed.length ? feed : data?.incidents_snapshot || []).map((item, i) => {
              const src = item.source || "chat";
              const badge = LANE_BADGE[src] || LANE_BADGE.chat;
              return (
                <div key={i} className="flex items-center gap-2 rounded-lg bg-slate-50 px-3 py-2 text-sm dark:bg-slate-700/40">
                  <span className={`shrink-0 rounded px-1.5 py-0.5 text-xs font-semibold uppercase tracking-wide ${badge}`}>
                    {src}
                  </span>
                  <span className="flex-1 font-medium truncate">{item.title || item.action || "Event"}</span>
                  <span className="shrink-0 text-slate-400">{item.severity || item.message || ""}</span>
                </div>
              );
            })}
            {!feed.length && !data?.incidents_snapshot?.length && (
              <p className="py-4 text-center text-sm text-slate-400">No active alerts</p>
            )}
          </div>
        </ChartCard>
      </div>

      <div className="mt-6">
        <RagFunnelChart ragFunnel={data?.rag_funnel} />
      </div>

      <div className="mt-6 grid gap-4 lg:grid-cols-2">
        <ChartCard
          title="Top Risky API Keys"
          titleHelpText="Keys ranked by UEBA risk score and anomaly severity—start investigation here."
        >
          <DataTable columns={violatorCols} rows={data?.top_risky_keys || []} />
        </ChartCard>
        <ChartCard
          title="Key Risk Distribution"
          titleHelpText="Distribution of keys across low, medium, and high UEBA risk bands."
        >
          <ResponsiveContainer width="100%" height={220}>
            <PieChart>
              <Pie data={riskDistribution} dataKey="value" nameKey="name" outerRadius={80}>
                {riskDistribution.map((entry, idx) => (
                  <Cell key={idx} fill={riskColors[entry.name] || "#94a3b8"} />
                ))}
              </Pie>
              <Tooltip />
            </PieChart>
          </ResponsiveContainer>
        </ChartCard>
      </div>
    </div>
  );
}
