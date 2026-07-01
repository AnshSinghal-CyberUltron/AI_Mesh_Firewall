import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { RefreshCw, ArrowDown, ArrowUp, HelpCircle, Radio } from "lucide-react";
import { useAuth } from "../../context/AuthContext";
import { clearModule2Cache, createModule2Api } from "../../api/module2";
import { useRealtimeNotifications } from "../../hooks/useRealtimeNotifications";
import { useContainmentPolling } from "../../hooks/useContainmentPolling";
import { TELEMETRY_ACTIVITY_EVENT } from "../../utils/telemetryEvents";
import { PageHeader } from "../../components/module2/PageHeader";
import { KPIBar } from "../../components/module2/KPIBar";
import { module2TooltipProps } from "../../components/module2/module2Chart";
import { ChartCard } from "../../components/module2/ChartCard";
import { DataTable } from "../../components/module2/DataTable";
import { PeriodSelector } from "../../components/module2/PeriodSelector";
import {
  Module2EmptyState,
  Module2ErrorState,
  Module2PageErrorBoundary,
  Module2PageSkeleton,
} from "../../components/module2/PageStates";
import { ContextualAppBar } from "../../components/module2/ContextualAppBar";
import { ANALYST_BRIEF_TITLE, PAGE_BRIEFS } from "./pageCopy";

const PERIOD_LABELS = { "1h": "1 hour", "24h": "24 hours", "7d": "7 days", "30d": "30 days" };
const REFRESH_DEBOUNCE_MS = 300;
const TOOL_CHART_HEIGHT = 260;
const MCP_SIMULATOR_PATH = "/?tab=firewall-1-4";

const DIRECTION_META = {
  inbound: {
    label: "Inbound",
    subtitle: "Tool arguments",
    help: "Scans tool call arguments before they are added to the model context. Blocks and redactions here stop risky input early.",
    Icon: ArrowDown,
    color: "text-sky-500",
    bg: "bg-sky-50 dark:bg-sky-900/20 border-sky-200 dark:border-sky-700",
  },
  outbound: {
    label: "Outbound",
    subtitle: "Tool responses",
    help: "Scans data coming back from tools before it reaches the model. Used to drop or redact secrets and overshared context.",
    Icon: ArrowUp,
    color: "text-emerald-500",
    bg: "bg-emerald-50 dark:bg-emerald-900/20 border-emerald-200 dark:border-emerald-700",
  },
  unknown: {
    label: "Unknown",
    subtitle: "Direction missing",
    help: "Events missing scan direction metadata. These are excluded from inbound/outbound hit-rate calculations until telemetry is normalized.",
    Icon: HelpCircle,
    color: "text-slate-500",
    bg: "bg-slate-50 dark:bg-slate-800/40 border-slate-200 dark:border-slate-700",
  },
};

function buildMcpKpiItems(summary = {}) {
  const totalEvents = summary.total_events ?? 0;
  const blocked = summary.blocked_tool_calls ?? 0;
  const redacted = summary.redacted_calls ?? summary.redacted_arguments ?? 0;
  const violationRate = totalEvents
    ? Math.min(100, Math.round(((blocked + redacted) / totalEvents) * 100))
    : 0;

  return [
    {
      key: "mcp-events",
      label: "MCP Events",
      value: totalEvents,
      helpText: "Every MCP tool-call the firewall evaluated in this time window (live calls and policy tests that emit telemetry).",
    },
    {
      key: "blocked-tools",
      label: "Blocked Calls",
      value: blocked,
      color: blocked > 0 ? "text-red-600" : undefined,
      helpText: "Tool calls stopped completely — for example dangerous SQL, path traversal, or arguments that break your MCP policy.",
    },
    {
      key: "redacted-args",
      label: "Redacted Calls",
      value: redacted,
      color: redacted > 0 ? "text-amber-600" : undefined,
      helpText: "Calls that were allowed only after sensitive values in tool arguments or responses were masked.",
    },
    {
      key: "unique-tools",
      label: "Unique Tools",
      value: summary.unique_tools ?? 0,
      color: "text-violet-600",
      helpText: "How many different tool names appeared. A sudden jump can mean an agent is reaching for tools you have not reviewed.",
    },
    {
      key: "violation-rate",
      label: "Policy Hit Rate",
      value: `${violationRate}%`,
      color: violationRate >= 30 ? "text-red-600" : violationRate >= 10 ? "text-amber-600" : "text-emerald-600",
      helpText: "Share of MCP events that ended in a block or redaction (blocked + redacted ÷ total events).",
    },
  ];
}

function directionViolationRate(stats) {
  if (!stats?.total) return "—";
  return `${Math.min(100, Math.round((stats.blocked / stats.total) * 100))}%`;
}

function McpRiskDashboard({ data }) {
  const summary = data?.summary || {};
  const toolLedger = data?.tool_ledger || [];
  const dirSplit = data?.direction_split || {
    inbound: { total: 0, blocked: 0 },
    outbound: { total: 0, blocked: 0 },
    unknown: { total: 0, blocked: 0 },
  };
  const hasToolActivity = toolLedger.length > 0;

  return (
    <>
      <div className="mb-4 rounded-xl border border-amber-200 bg-amber-50/60 px-4 py-3 text-xs leading-relaxed text-amber-950 dark:border-amber-800/60 dark:bg-amber-950/20 dark:text-amber-100">
        <strong>MCP risk</strong> summarizes tool-call enforcement from Module 1.4.
        {" "}Use the <strong>violations</strong> chart to prioritize which tools to restrict, and the{" "}
        <strong>inbound / outbound</strong> cards to see whether problems happen on arguments going in or results coming back.
      </div>

      <KPIBar items={buildMcpKpiItems(summary)} />

      <div className="mt-6 grid gap-4 lg:grid-cols-2">
        <ChartCard
          title="Tool Activity vs Policy Hits"
          titleHelpText="Compares total calls against policy-hit calls (block + redact) for each tool. Use this to spot noisy high-volume tools and high-risk tools."
        >
          {hasToolActivity ? (
            <ResponsiveContainer width="100%" height={TOOL_CHART_HEIGHT}>
              <BarChart data={toolLedger} layout="vertical" margin={{ left: 10, right: 10 }}>
                <CartesianGrid strokeDasharray="3 3" opacity={0.25} />
                <XAxis type="number" fontSize={11} allowDecimals={false} />
                <YAxis dataKey="tool" type="category" fontSize={10} width={130} />
                <Tooltip {...module2TooltipProps} />
                <Bar dataKey="total_calls" fill="#0ea5e9" radius={[0, 4, 4, 0]} name="Total calls" />
                <Bar dataKey="violations" fill="#f59e0b" radius={[0, 4, 4, 0]} name="Policy hits" />
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <p className="py-8 text-center text-sm text-slate-400">
              MCP events were recorded, but telemetry did not include tool names.
              Ensure events set <span className="font-mono">tools_invoked</span>.
            </p>
          )}
        </ChartCard>

        <ChartCard
          title="Inbound vs Outbound Scans"
          titleHelpText="Inbound counts argument scans on the way into the model. Outbound counts response scans on the way back. Violations include both hard blocks and redactions."
        >
          <div className="mt-2 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {["inbound", "outbound", "unknown"].map((dir) => {
              const stats = dirSplit[dir] || { total: 0, blocked: 0 };
              const meta = DIRECTION_META[dir];
              const Icon = meta.Icon;
              return (
                <div key={dir} className={`rounded-xl border p-4 ${meta.bg}`} title={meta.help}>
                  <div className="mb-3 flex items-center gap-2">
                    <Icon className={`h-5 w-5 ${meta.color}`} />
                    <div>
                      <span className="text-sm font-semibold text-slate-700 dark:text-slate-200">{meta.label}</span>
                      <p className="text-[10px] text-slate-500 dark:text-slate-400">{meta.subtitle}</p>
                    </div>
                  </div>
                  <p className="text-2xl font-bold text-slate-900 dark:text-white">{stats.total.toLocaleString()}</p>
                  <p className="mt-1 text-xs text-slate-500">
                    <span className="font-medium text-red-500">{stats.blocked}</span> policy hits
                    {" · "}
                    <span className={`${meta.color} font-medium`}>{directionViolationRate(stats)}</span> hit rate
                  </p>
                </div>
              );
            })}
          </div>
          <p className="mt-4 text-xs text-slate-400">
            If inbound hit rate is high, review tool argument policies. If outbound is high, tighten what tool results may return to the model. Unknown should stay near zero after telemetry normalization.
          </p>
        </ChartCard>
      </div>

      <div className="mt-6">
        <ChartCard
          title="Busiest MCP Servers"
          titleHelpText="Servers or connector slugs ranked by how many MCP events they generated in this period (all outcomes — allow, block, and redact)."
        >
          <DataTable
            columns={[
              {
                key: "server",
                label: "Server",
                helpText: "The MCP server slug or connector name reported in event metadata.",
              },
              {
                key: "total",
                label: "Events",
                helpText: "Total MCP enforcement events tied to this server in the selected period.",
              },
            ]}
            rows={data?.top_servers || []}
            emptyMessage="No MCP server metadata on events yet — connect a server in Module 1.4."
          />
        </ChartCard>
      </div>
    </>
  );
}

export function McpRiskPage() {
  return (
    <Module2PageErrorBoundary title="MCP & Context Risk failed to render">
      <McpRiskPageInner />
    </Module2PageErrorBoundary>
  );
}

function McpRiskPageInner() {
  const { fetchWithAuth } = useAuth();
  const api = useMemo(() => createModule2Api(fetchWithAuth), [fetchWithAuth]);
  const [searchParams, setSearchParams] = useSearchParams();
  const periodParam = searchParams.get("period");
  const [period, setPeriod] = useState(
    periodParam && PERIOD_LABELS[periodParam] ? periodParam : "24h",
  );
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const loadSeqRef = useRef(0);
  const refreshTimerRef = useRef(null);

  const load = useCallback(async ({ silent = false } = {}) => {
    const seq = ++loadSeqRef.current;
    if (!silent) {
      setLoading(true);
      setError(null);
    }
    try {
      clearModule2Cache();
      const result = await api.getMcpRisk(period, { useCache: false });
      if (seq !== loadSeqRef.current) return;
      setData(result);
      setError(null);
    } catch (e) {
      if (seq !== loadSeqRef.current) return;
      setError(e.message || "Failed to load MCP risk data.");
      if (!silent) setData(null);
    } finally {
      if (seq === loadSeqRef.current && !silent) setLoading(false);
    }
  }, [api, period]);

  const refreshLive = useCallback(() => {
    clearTimeout(refreshTimerRef.current);
    refreshTimerRef.current = setTimeout(() => {
      load({ silent: true });
    }, REFRESH_DEBOUNCE_MS);
  }, [load]);

  useEffect(() => () => clearTimeout(refreshTimerRef.current), []);

  const { connected: wsConnected } = useRealtimeNotifications({
    onEnforcementEvent: refreshLive,
  });
  useContainmentPolling(refreshLive, { enabled: !!data });

  useEffect(() => {
    const onTelemetry = () => refreshLive();
    window.addEventListener(TELEMETRY_ACTIVITY_EVENT, onTelemetry);
    return () => window.removeEventListener(TELEMETRY_ACTIVITY_EVENT, onTelemetry);
  }, [refreshLive]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (periodParam && PERIOD_LABELS[periodParam]) {
      setPeriod(periodParam);
    }
  }, [periodParam]);

  const handlePeriodChange = useCallback((next) => {
    setPeriod(next);
    setSearchParams({ period: next }, { replace: true });
  }, [setSearchParams]);

  const totalEvents = data?.summary?.total_events ?? 0;
  const hasActivity = totalEvents > 0;

  if (loading && !data) {
    return (
      <div>
        <ContextualAppBar title={ANALYST_BRIEF_TITLE} description={PAGE_BRIEFS.mcp} />
        {error ? <Module2ErrorState message={error} onRetry={() => load()} /> : <Module2PageSkeleton />}
      </div>
    );
  }

  if (error && !data) {
    return (
      <div>
        <ContextualAppBar title={ANALYST_BRIEF_TITLE} description={PAGE_BRIEFS.mcp} />
        <Module2ErrorState message={error} onRetry={() => load()} />
      </div>
    );
  }

  return (
    <div>
      <ContextualAppBar title={ANALYST_BRIEF_TITLE} description={PAGE_BRIEFS.mcp} />
      <PageHeader
        title="MCP & Context Risk"
        subtitle={`Tool-call policy hits and server activity · ${PERIOD_LABELS[period] || period} window (server UTC)`}
        actions={
          <>
            <span
              className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ${
                wsConnected
                  ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300"
                  : "bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400"
              }`}
              title={wsConnected ? "Live enforcement feed connected" : "Refreshes on simulator activity and enforcement events"}
            >
              <Radio className={`h-3 w-3 ${wsConnected ? "text-emerald-500" : ""}`} />
              {wsConnected ? "Live" : "On activity"}
            </span>
            <PeriodSelector value={period} onChange={handlePeriodChange} />
            <button
              type="button"
              onClick={() => load()}
              className="rounded-lg border border-slate-200 p-2 dark:border-slate-600"
              aria-label="Refresh MCP risk data"
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

      {data && !hasActivity && (
        <Module2EmptyState
          title="No MCP tool activity in this period"
          message="This page fills when the firewall records MCP tool calls — typically from Live mode in the Module 1.4 MCP Guardrail Simulator or from production agents using your MCP connectors."
          hint="Use Live mode (not Dry-Run) so enforcement events include tool names and server metadata."
          action={(
            <Link
              to={MCP_SIMULATOR_PATH}
              className="inline-flex items-center rounded-lg bg-teal-600 px-4 py-2 text-sm font-semibold text-white hover:bg-teal-700"
            >
              Open M1.4 MCP Guardrail Simulator
            </Link>
          )}
        />
      )}

      {data && hasActivity && <McpRiskDashboard data={data} />}
    </div>
  );
}
