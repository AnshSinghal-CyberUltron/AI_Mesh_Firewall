import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  Area, AreaChart, CartesianGrid,
  ResponsiveContainer, Tooltip, XAxis, YAxis, PieChart, Pie, Cell, Legend,
} from "recharts";
import { RefreshCw, MessageSquare, BookOpen, Database, Wrench, Radio, ArrowRight } from "lucide-react";
import { useAuth } from "../../context/AuthContext";
import { useContainmentPolling } from "../../hooks/useContainmentPolling";
import { useRealtimeNotifications } from "../../hooks/useRealtimeNotifications";
import { clearModule2Cache, createModule2Api } from "../../api/module2";
import { PageHeader } from "../../components/module2/PageHeader";
import { KPIBar } from "../../components/module2/KPIBar";
import { ChartCard } from "../../components/module2/ChartCard";
import { DataTable } from "../../components/module2/DataTable";
import { PeriodSelector } from "../../components/module2/PeriodSelector";
import { ContextualAppBar } from "../../components/module2/ContextualAppBar";
import { InfoTooltip } from "../../components/module2/InfoTooltip";
import { Module2EmptyState, Module2ErrorState, Module2PageSkeleton } from "../../components/module2/PageStates";
import {
  buildContainmentKpiItems,
  formatRiskDistributionChart,
  formatTickerDetail,
  formatTickerHeadline,
  mergeTickerFeed,
  resolveEventLane,
} from "./pageData";
import { ANALYST_BRIEF_TITLE, PAGE_BRIEFS } from "./pageCopy";

const TICKER_FEED_LIMIT = 20;
const TICKER_DISPLAY_LIMIT = 12;
const REFRESH_DEBOUNCE_MS = 300;

const LANE_META = {
  chat: {
    label: "Chat",
    icon: MessageSquare,
    color: "text-sky-500",
    bg: "bg-sky-50 dark:bg-sky-900/20",
    border: "border-sky-200 dark:border-sky-700",
    helpText: "Direct LLM chat/completion traffic through the gateway ingress.",
    drillDown: { to: "/models/exposure?tab=model", label: "M2.3 Model exposure" },
  },
  rag: {
    label: "RAG",
    icon: BookOpen,
    color: "text-violet-500",
    bg: "bg-violet-50 dark:bg-violet-900/20",
    border: "border-violet-200 dark:border-violet-700",
    helpText: "Retrieval-augmented requests tagged event_type=rag_pipeline.",
    drillDown: { to: "/models/exposure?tab=rag", label: "M2.3 RAG health" },
  },
  vector: {
    label: "Vector",
    icon: Database,
    color: "text-emerald-500",
    bg: "bg-emerald-50 dark:bg-emerald-900/20",
    border: "border-emerald-200 dark:border-emerald-700",
    helpText: "Vector DB access events with collection or namespace metadata.",
    drillDown: { to: "/models/exposure?tab=rag", label: "M2.3 Vector collections" },
  },
  mcp: {
    label: "MCP",
    icon: Wrench,
    color: "text-amber-500",
    bg: "bg-amber-50 dark:bg-amber-900/20",
    border: "border-amber-200 dark:border-amber-700",
    helpText: "Model Context Protocol tool-call enforcement events.",
    drillDown: { to: "/mcp/risk", label: "M2.4 MCP risk" },
  },
};

const RISK_COLORS = { low: "#10b981", medium: "#f59e0b", high: "#ef4444" };

const PERIOD_LABELS = { "1h": "1 hour", "24h": "24 hours", "7d": "7 days", "30d": "30 days" };

const LANE_BADGE = {
  chat:       "bg-sky-100 text-sky-700 dark:bg-sky-900/40 dark:text-sky-300",
  rag:        "bg-violet-100 text-violet-700 dark:bg-violet-900/40 dark:text-violet-300",
  vector:     "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300",
  mcp:        "bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300",
  threat_intel: "bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300",
  ueba:       "bg-orange-100 text-orange-700 dark:bg-orange-900/40 dark:text-orange-300",
};

function LaneSummaryGrid({ laneSummary, period = "24h" }) {
  return (
    <div className="mt-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      {Object.entries(LANE_META).map(([key, meta]) => {
        const stats = laneSummary?.[key] || { total: 0, blocked: 0, block_rate_pct: 0 };
        const Icon = meta.icon;
        const rateLabel = stats.total > 0 ? `${stats.block_rate_pct}%` : "—";
        const drillTo = meta.drillDown
          ? `${meta.drillDown.to}${meta.drillDown.to.includes("?") ? "&" : "?"}period=${period}`
          : null;
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
              {" · "}<span className={meta.color + " font-medium"}>{rateLabel}</span> rate
            </p>
            {meta.drillDown && drillTo && (
              <Link
                to={drillTo}
                className="mt-3 inline-flex items-center gap-1 text-xs font-medium text-teal-700 hover:text-teal-900 dark:text-teal-400 dark:hover:text-teal-300"
              >
                {meta.drillDown.label}
                <ArrowRight className="h-3 w-3" />
              </Link>
            )}
          </div>
        );
      })}
    </div>
  );
}

export function DashboardPage() {
  const { fetchWithAuth } = useAuth();
  const api = useMemo(() => createModule2Api(fetchWithAuth), [fetchWithAuth]);
  const [period, setPeriod] = useState("24h");
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [feed, setFeed] = useState([]);
  const refreshTimerRef = useRef(null);

  const load = useCallback(async ({ silent = false } = {}) => {
    if (!silent) setLoading(true);
    setError(null);
    try {
      clearModule2Cache();
      const res = await api.getDashboard(period);
      setData(res);
    } catch (e) {
      setError(e.message || "Failed to load dashboard.");
      if (!silent) setData(null);
    } finally {
      if (!silent) setLoading(false);
    }
  }, [api, period]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => { setFeed([]); }, [period]);

  const refreshLive = useCallback(() => {
    clearTimeout(refreshTimerRef.current);
    refreshTimerRef.current = setTimeout(async () => {
      clearModule2Cache();
      try {
        const res = await api.getDashboard(period, { useCache: false });
        setData(res);
        setError(null);
      } catch (e) {
        setError(e.message || "Failed to refresh dashboard.");
      }
    }, REFRESH_DEBOUNCE_MS);
  }, [api, period]);

  useEffect(() => () => clearTimeout(refreshTimerRef.current), []);

  useContainmentPolling(refreshLive, { enabled: !!data });

  const { connected: wsConnected } = useRealtimeNotifications({
    onEnforcementEvent: (payload) => {
      setFeed((prev) => [payload, ...prev].slice(0, TICKER_FEED_LIMIT));
      refreshLive();
    },
  });

  const riskDistribution = useMemo(
    () => formatRiskDistributionChart(data?.key_risk_distribution),
    [data?.key_risk_distribution],
  );

  const tickerItems = useMemo(
    () => mergeTickerFeed(feed, data?.incidents_snapshot || [], TICKER_DISPLAY_LIMIT),
    [feed, data?.incidents_snapshot],
  );

  const threatTrend = data?.threat_trend || [];
  const hasThreatTrend = threatTrend.some((p) => (p.total ?? 0) > 0);

  if (loading && !data) {
    return (
      <div>
        <ContextualAppBar title={ANALYST_BRIEF_TITLE} description={PAGE_BRIEFS.dashboard} />
        {error ? <Module2ErrorState message={error} onRetry={load} /> : <Module2PageSkeleton />}
      </div>
    );
  }

  if (error && !data) {
    return (
      <div>
        <ContextualAppBar title={ANALYST_BRIEF_TITLE} description={PAGE_BRIEFS.dashboard} />
        <Module2ErrorState message={error} onRetry={load} />
      </div>
    );
  }

  const kpis = data?.kpis || {};
  const containment = data?.containment || {};
  const kpiItems = [
    { key: "total-events", label: "Total Events", value: kpis.total_events ?? 0, helpText: "All gateway enforcement events in the selected time window." },
    { key: "blocked", label: "Blocked", value: kpis.blocked ?? 0, color: "text-red-600", helpText: "Requests hard-stopped by policy (deny / kill-switch)." },
    { key: "redacted", label: "Redacted", value: kpis.redacted ?? 0, color: "text-amber-600", helpText: "Requests allowed after PII or sensitive fields were masked." },
    { key: "open-incidents", label: "Open Incidents", value: kpis.open_incidents ?? 0, color: "text-orange-600", helpText: "Cases still open, investigating, or escalated in the incident queue." },
    { key: "risky-keys", label: "High-Risk Keys", value: kpis.risky_keys ?? 0, color: "text-red-600", helpText: "Registered API keys in the high UEBA risk band (fleet-wide)." },
    ...buildContainmentKpiItems({
      disabledKeys: kpis.disabled_keys ?? containment.disabled_keys ?? 0,
      activeKillSwitches: kpis.active_kill_switches ?? containment.active_kill_switches ?? 0,
      clickable: false,
    }),
    { key: "block-rate", label: "Block Rate", value: `${kpis.block_rate ?? 0}%`, helpText: "Hard-block rate across all events—useful for spotting enforcement spikes." },
  ];

  const violatorCols = [
    { key: "prefix", label: "Key Prefix", helpText: "Truncated API key ID for attribution without exposing the secret." },
    { key: "project_id", label: "Project", helpText: "Application or project scope tied to this key." },
    { key: "risk_band", label: "Risk", helpText: "UEBA tier (low / medium / high) from velocity, violations, and anomaly signals." },
    { key: "request_count", label: "Requests", helpText: "Event count for this key in the selected period." },
    { key: "velocity_spike", label: "Velocity x", helpText: "Traffic multiplier vs. this key's baseline; spikes may indicate compromise." },
  ];

  const topRiskyKeys = data?.top_risky_keys || [];

  return (
    <div>
      <ContextualAppBar title={ANALYST_BRIEF_TITLE} description={PAGE_BRIEFS.dashboard} />
      <PageHeader
        title="SOC Command Center"
        subtitle={`Gateway intelligence · ${PERIOD_LABELS[period] || period} window (server UTC)`}
        actions={
          <>
            <span
              className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ${
                wsConnected
                  ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300"
                  : "bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400"
              }`}
              title={wsConnected ? "Live enforcement feed connected" : "Live feed offline — showing incidents and polled data"}
            >
              <Radio className={`h-3 w-3 ${wsConnected ? "text-emerald-500" : ""}`} />
              {wsConnected ? "Live" : "Polling"}
            </span>
            <PeriodSelector value={period} onChange={setPeriod} />
            <button
              type="button"
              onClick={() => load()}
              className="rounded-lg border border-slate-200 p-2 dark:border-slate-600"
              aria-label="Refresh dashboard"
            >
              <RefreshCw className="h-4 w-4" />
            </button>
          </>
        }
      />

      {error && data && (
        <div className="mb-4 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-amber-200 bg-amber-50 px-4 py-2 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200">
          <span>{error}</span>
          <button type="button" onClick={() => load({ silent: true })} className="font-medium underline">
            Retry
          </button>
        </div>
      )}

      <KPIBar items={kpiItems} />

      <LaneSummaryGrid laneSummary={data?.lane_summary} period={period} />

      <div className="mt-6 grid gap-4 lg:grid-cols-2">
        <ChartCard
          title="Threat Timeline"
          titleHelpText="Hourly enforcement volume split by total, blocked, and redacted outcomes."
        >
          {hasThreatTrend ? (
            <ResponsiveContainer width="100%" height={240}>
              <AreaChart data={threatTrend}>
                <CartesianGrid strokeDasharray="3 3" stroke="#334155" opacity={0.3} />
                <XAxis dataKey="timestamp" tickFormatter={(v) => `${v?.slice(11, 16)} UTC`} fontSize={11} />
                <YAxis fontSize={11} allowDecimals={false} />
                <Tooltip labelFormatter={(v) => `${String(v).replace("T", " ").slice(0, 19)} UTC`} />
                <Area type="monotone" dataKey="total" stackId="1" fill="#0ea5e9" stroke="#0ea5e9" fillOpacity={0.35} name="Total" />
                <Area type="monotone" dataKey="blocked" stackId="2" fill="#ef4444" stroke="#ef4444" fillOpacity={0.45} name="Blocked" />
                <Area type="monotone" dataKey="redacted" stackId="2" fill="#f59e0b" stroke="#f59e0b" fillOpacity={0.45} name="Redacted" />
              </AreaChart>
            </ResponsiveContainer>
          ) : (
            <p className="py-16 text-center text-sm text-slate-400">
              No enforcement events in this period.
            </p>
          )}
        </ChartCard>

        <ChartCard
          title="High-Risk Ticker"
          titleHelpText="Live enforcement events merged with open incidents across all lanes."
        >
          <div className="max-h-60 space-y-2 overflow-y-auto">
            {tickerItems.map((item, i) => {
              const lane = resolveEventLane(item);
              const badge = LANE_BADGE[lane] || LANE_BADGE.chat;
              const headline = formatTickerHeadline(item);
              const detail = formatTickerDetail(item);
              return (
                <div key={item.id || `${lane}-${item.timestamp || i}`} className="flex items-start gap-2 rounded-lg bg-slate-50 px-3 py-2 text-sm dark:bg-slate-700/40">
                  <span className={`shrink-0 rounded px-1.5 py-0.5 text-xs font-semibold uppercase tracking-wide ${badge}`}>
                    {lane.replace("_", " ")}
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="font-medium truncate">{headline}</p>
                    {detail ? <p className="text-xs text-slate-500 truncate">{detail}</p> : null}
                  </div>
                  {item._fromFeed && (
                    <span className="shrink-0 text-[10px] uppercase tracking-wide text-emerald-600 dark:text-emerald-400">live</span>
                  )}
                </div>
              );
            })}
            {!tickerItems.length && (
              <Module2EmptyState
                title="No active alerts"
                message="Open incidents and live enforcement events will appear here during active shifts."
              />
            )}
          </div>
        </ChartCard>
      </div>

      <div className="mt-6 grid gap-4 lg:grid-cols-2">
        <ChartCard
          title="Top Risky API Keys"
          titleHelpText="Keys with activity in this period, ranked by UEBA risk score."
        >
          {topRiskyKeys.length > 0 ? (
            <DataTable columns={violatorCols} rows={topRiskyKeys} />
          ) : (
            <p className="py-12 text-center text-sm text-slate-400">
              No key-attributed traffic in this period — events need key_prefix metadata.
            </p>
          )}
        </ChartCard>
        <ChartCard
          title="Key Risk Distribution"
          titleHelpText="Fleet-wide UEBA risk bands for all registered API keys (includes keys with zero activity)."
        >
          {riskDistribution.length > 0 ? (
            <ResponsiveContainer width="100%" height={220}>
              <PieChart>
                <Pie
                  data={riskDistribution}
                  dataKey="value"
                  nameKey="name"
                  cx="50%"
                  cy="50%"
                  outerRadius={80}
                >
                  {riskDistribution.map((entry) => (
                    <Cell key={entry.band} fill={RISK_COLORS[entry.band] || "#94a3b8"} />
                  ))}
                </Pie>
                <Tooltip formatter={(value, name) => [`${value} keys`, name]} />
                <Legend wrapperStyle={{ fontSize: 11 }} />
              </PieChart>
            </ResponsiveContainer>
          ) : (
            <p className="py-16 text-center text-sm text-slate-400">
              No API keys registered for this organization.
            </p>
          )}
        </ChartCard>
      </div>
    </div>
  );
}
