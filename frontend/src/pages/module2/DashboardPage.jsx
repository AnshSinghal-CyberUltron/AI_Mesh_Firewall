import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  Area, AreaChart, CartesianGrid,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { MessageSquare, BookOpen, Wrench, Shield, Radio, ArrowRight } from "lucide-react";
import { Module2RefreshButton } from "../../components/module2/Module2RefreshButton";
import { useAuth } from "../../context/AuthContext";
import { useContainmentPolling } from "../../hooks/useContainmentPolling";
import { useRealtimeNotifications } from "../../hooks/useRealtimeNotifications";
import { clearModule2Cache, createModule2Api } from "../../api/module2";
import { TELEMETRY_ACTIVITY_EVENT } from "../../utils/telemetryEvents";
import { PageHeader } from "../../components/module2/PageHeader";
import { KPIBar } from "../../components/module2/KPIBar";
import { ChartCard } from "../../components/module2/ChartCard";
import { module2TooltipProps } from "../../components/module2/module2Chart";
import { RiskBandBadge } from "../../components/module2/RiskBandBadge";
import { DataTable } from "../../components/module2/DataTable";
import { PeriodSelector } from "../../components/module2/PeriodSelector";
import { ContextualAppBar } from "../../components/module2/ContextualAppBar";
import { InfoTooltip } from "../../components/module2/InfoTooltip";
import { Module2EmptyState, Module2ErrorState, Module2PageSkeleton } from "../../components/module2/PageStates";
import {
  buildTickerAnalystFields,
  formatTickerAnalystSummary,
  formatTickerDetail,
  formatTickerHeadline,
  mergeRagVectorLaneStats,
  mergeTickerFeed,
  formatLaneDisplayLabel,
  resolveEventLane,
} from "./pageData";
import { ANALYST_BRIEF_TITLE, PAGE_BRIEFS } from "./pageCopy";
import { GATEWAY_KPI_SOURCE } from "../../utils/kpiDataSourceCopy";

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
    helpText: "Chat and completion prompts sent through your AI gateway.",
    drillDown: { to: "/models/exposure?tab=model", label: "Model exposure" },
  },
  rag: {
    label: "RAG",
    icon: BookOpen,
    color: "text-violet-500",
    bg: "bg-violet-50 dark:bg-violet-900/20",
    border: "border-violet-200 dark:border-violet-700",
    helpText:
      "Knowledge-base retrieval through the gateway — pipeline searches and document-library lookups. Open RAG health for stage KPIs and collection risk.",
    drillDown: { to: "/models/exposure?tab=rag", label: "RAG health" },
  },
  mcp: {
    label: "MCP",
    icon: Wrench,
    color: "text-amber-500",
    bg: "bg-amber-50 dark:bg-amber-900/20",
    border: "border-amber-200 dark:border-amber-700",
    helpText: "Tool and context calls checked by the gateway before they reach the model.",
    drillDown: { to: "/mcp/risk", label: "MCP risk" },
  },
  threat_intel: {
    label: "Threat Intel",
    icon: Shield,
    color: "text-red-500",
    bg: "bg-red-50 dark:bg-red-900/20",
    border: "border-red-200 dark:border-red-700",
    helpText: "Prompts and requests that matched your threat indicators at the gateway.",
    drillDown: { to: "/threat-intel", label: "Threat intel" },
  },
};

const PERIOD_LABELS = { "1h": "1 hour", "24h": "24 hours", "7d": "7 days", "30d": "30 days" };

const LANE_BADGE = {
  chat:       "bg-sky-100 text-sky-700 dark:bg-sky-900/40 dark:text-sky-300",
  rag:        "bg-violet-100 text-violet-700 dark:bg-violet-900/40 dark:text-violet-300",
  vector:     "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300",
  mcp:        "bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300",
  threat_intel: "bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300",
};

function tickerRowKey(item, lane, index) {
  if (item.id) return item._fromFeed ? `ev-${item.id}` : `inc-${item.id}`;
  return `row-${lane}-${item.timestamp || index}`;
}

function HighRiskTickerRow({ item, index }) {
  const [expanded, setExpanded] = useState(false);
  const lane = resolveEventLane(item);
  const displayLane = lane === "vector" ? "rag" : lane;
  const badge = LANE_BADGE[displayLane] || LANE_BADGE.chat;
  const headline = formatTickerHeadline(item);
  const detail = formatTickerDetail(item);
  const summary = formatTickerAnalystSummary(item);
  const analystFields = buildTickerAnalystFields(item);
  const rowKey = tickerRowKey(item, lane, index);

  return (
    <div className="rounded-lg border border-slate-200 bg-slate-50 dark:border-slate-600 dark:bg-slate-700/40">
      <button
        type="button"
        onClick={() => setExpanded((open) => !open)}
        className="group flex w-full items-start gap-2 px-3 py-2 text-left text-sm"
        aria-expanded={expanded}
        aria-controls={`ticker-detail-${rowKey}`}
        title={summary}
      >
        <span className={`shrink-0 rounded px-1.5 py-0.5 text-xs font-semibold uppercase tracking-wide ${badge}`}>
          {formatLaneDisplayLabel(lane)}
        </span>
        <div className="min-w-0 flex-1">
          <p className="font-medium truncate">{headline}</p>
          {detail ? <p className="text-xs text-slate-500 truncate">{detail}</p> : null}
          <p className="mt-1 hidden text-xs leading-relaxed text-slate-600 group-hover:block dark:text-slate-300">
            {summary}
          </p>
        </div>
        {item._fromFeed ? (
          <span className="shrink-0 text-[10px] uppercase tracking-wide text-emerald-600 dark:text-emerald-400">live</span>
        ) : null}
        <span className="shrink-0 text-[10px] uppercase tracking-wide text-slate-400">
          {expanded ? "Hide" : "Details"}
        </span>
      </button>
      {expanded && analystFields.length > 0 ? (
        <dl
          id={`ticker-detail-${rowKey}`}
          className="border-t border-slate-200 px-3 py-2 text-xs dark:border-slate-600"
        >
          {analystFields.map(({ label, value }) => (
            <div key={label} className="grid grid-cols-[7rem_1fr] gap-2 py-1">
              <dt className="font-medium text-slate-500 dark:text-slate-400">{label}</dt>
              <dd className="break-words text-slate-800 dark:text-slate-100">{value}</dd>
            </div>
          ))}
        </dl>
      ) : null}
    </div>
  );
}

function LaneSummaryGrid({ laneSummary, period = "24h" }) {
  return (
    <div className="mt-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
      {Object.entries(LANE_META).map(([key, meta]) => {
        const stats =
          key === "rag"
            ? mergeRagVectorLaneStats(laneSummary)
            : laneSummary?.[key] || { total: 0, blocked: 0, block_rate_pct: 0 };
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
  const loadSeqRef = useRef(0);
  const refreshTimerRef = useRef(null);

  const load = useCallback(async ({ silent = false } = {}) => {
    const seq = ++loadSeqRef.current;
    if (!silent) setLoading(true);
    if (!silent) setError(null);
    try {
      clearModule2Cache();
      const res = await api.getDashboard(period);
      if (seq !== loadSeqRef.current) return;
      setData(res);
    } catch (e) {
      if (seq !== loadSeqRef.current) return;
      setError(e.message || "Failed to load dashboard.");
      if (!silent) setData(null);
    } finally {
      if (seq === loadSeqRef.current && !silent) setLoading(false);
    }
  }, [api, period]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => { setFeed([]); }, [period]);

  const refreshLive = useCallback(() => {
    clearTimeout(refreshTimerRef.current);
    refreshTimerRef.current = setTimeout(async () => {
      const seq = ++loadSeqRef.current;
      clearModule2Cache();
      try {
        const res = await api.getDashboard(period, { useCache: false });
        if (seq !== loadSeqRef.current) return;
        setData(res);
        setError(null);
        setLoading(false);
      } catch (e) {
        if (seq !== loadSeqRef.current) return;
        setError(e.message || "Failed to refresh dashboard.");
        setLoading(false);
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

  useEffect(() => {
    const onTelemetry = () => refreshLive();
    window.addEventListener(TELEMETRY_ACTIVITY_EVENT, onTelemetry);
    return () => window.removeEventListener(TELEMETRY_ACTIVITY_EVENT, onTelemetry);
  }, [refreshLive]);

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
  const kpiItems = [
    { key: "total-events", label: "Gateway Requests", value: kpis.total_events ?? kpis.requests_inspected ?? 0, dataSource: GATEWAY_KPI_SOURCE, helpText: "Distinct gateway requests in the selected window (one count per request, not per pipeline stage)." },
    { key: "blocked", label: "Blocked", value: kpis.blocked ?? 0, color: "text-red-600", dataSource: GATEWAY_KPI_SOURCE, helpText: "Requests hard-stopped by policy (deny / kill-switch)." },
    { key: "redacted", label: "Redacted", value: kpis.redacted ?? 0, color: "text-amber-600", dataSource: GATEWAY_KPI_SOURCE, helpText: "Requests allowed after PII or sensitive fields were masked." },
    {
      key: "monitored",
      label: "Monitored",
      value: kpis.monitored ?? 0,
      color: "text-sky-600",
      dataSource: GATEWAY_KPI_SOURCE,
      helpText: "Distinct gateway requests with a monitor/flag verdict — allowed through but marked for analyst review in this window.",
    },
    {
      key: "rerouted",
      label: "Rerouted",
      value: kpis.rerouted ?? 0,
      color: "text-violet-600",
      dataSource: GATEWAY_KPI_SOURCE,
      helpText: "Distinct gateway requests where model routing sent traffic to a different model than the caller requested.",
    },
    { key: "block-rate", label: "Block Rate", value: `${kpis.block_rate ?? 0}%`, dataSource: GATEWAY_KPI_SOURCE, helpText: "Block rate across gateway requests in this window." },
  ];

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
            <Module2RefreshButton
              label="Refresh"
              onRefresh={async () => {
                clearTimeout(refreshTimerRef.current);
                await load({ silent: true });
              }}
            />
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

      <div className="mt-6">
        <ChartCard
          title="API Key Activity"
          titleHelpText="Gateway credentials with enforcement traffic in this window — same fleet data as API Key & Identity Risk."
        >
          <div className="mb-3 flex justify-end">
            <Link
              to={`/ueba/api-keys?period=${period}`}
              className="inline-flex items-center gap-1 text-xs font-medium text-teal-700 hover:text-teal-900 dark:text-teal-400 dark:hover:text-teal-300"
            >
              Open API Key & Identity Risk
              <ArrowRight className="h-3 w-3" />
            </Link>
          </div>
          <DataTable
            columns={[
              { key: "prefix", label: "Key", render: (r) => <span className="font-mono text-xs">{r.prefix}</span> },
              { key: "name", label: "Name" },
              { key: "request_count", label: "Requests" },
              {
                key: "risk_band",
                label: "Risk",
                render: (r) => <RiskBandBadge type="behavioral" band={r.risk_band} score={r.risk_score} />,
              },
            ]}
            rows={data?.top_risky_keys || []}
            emptyMessage="No API key traffic in this period — send demo or simulator requests through the gateway to populate UEBA."
          />
        </ChartCard>
      </div>

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
                <Tooltip
                  {...module2TooltipProps}
                  labelFormatter={(v) => `${String(v).replace("T", " ").slice(0, 19)} UTC`}
                />
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
          titleHelpText="Live enforcement events merged with cases opened in the selected window that are still open. Hover a row for a plain-language summary; click Details to expand forensics."
        >
          <div className="max-h-72 space-y-2 overflow-y-auto">
            {tickerItems.map((item, i) => (
              <HighRiskTickerRow key={tickerRowKey(item, resolveEventLane(item), i)} item={item} index={i} />
            ))}
            {!tickerItems.length && (
              <Module2EmptyState
                title="No active alerts"
                message="Cases opened in the selected window that are still open, plus live enforcement events, appear here during active shifts."
              />
            )}
          </div>
        </ChartCard>
      </div>
    </div>
  );
}
