import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Line, LineChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Loader2, Radio, RefreshCw } from "lucide-react";
import { useAuth } from "../../context/AuthContext";
import { clearModule2Cache, createModule2Api } from "../../api/module2";
import {
  fetchSimulatorDefaultContext,
  readStoredGatewayKeyContext,
  resolveGatewayKeyContext,
} from "../../api/gatewayContext";
import { getGatewayApiKey } from "../../utils/gatewayStorage";
import { useContainmentPolling } from "../../hooks/useContainmentPolling";
import { useRealtimeNotifications } from "../../hooks/useRealtimeNotifications";
import { TELEMETRY_ACTIVITY_EVENT, TELEMETRY_STORAGE_KEY } from "../../utils/telemetryEvents";
import { PageHeader } from "../../components/module2/PageHeader";
import { KPIBar } from "../../components/module2/KPIBar";
import { ChartCard } from "../../components/module2/ChartCard";
import { DataTable } from "../../components/module2/DataTable";
import { PeriodSelector } from "../../components/module2/PeriodSelector";
import { ContextualAppBar } from "../../components/module2/ContextualAppBar";
import { ApiKeyFleetTable } from "../../components/module2/ApiKeyFleetTable";
import { ApiKeyContainmentDetailPanel } from "../../components/module2/ApiKeyContainmentDetailPanel";
import { RiskBandBadge } from "../../components/module2/RiskBandBadge";
import { Module2EmptyState, Module2ErrorState, Module2PageErrorBoundary, Module2PageSkeleton } from "../../components/module2/PageStates";
import { buildUebaKpiItems } from "./pageData";
import { ANALYST_BRIEF_TITLE, PAGE_BRIEFS } from "./pageCopy";

const REFRESH_DEBOUNCE_MS = 300;
const CONTAINMENT_POLL_MS = 10_000;
const PERIOD_LABELS = { "1h": "1 hour", "24h": "24 hours", "7d": "7 days", "30d": "30 days" };

export function UebaApiKeysPage() {
  return (
    <Module2PageErrorBoundary title="API Key Behavior Analytics (UEBA) failed to render">
      <UebaApiKeysPageInner />
    </Module2PageErrorBoundary>
  );
}

function UebaApiKeysPageInner() {
  const { fetchWithAuth } = useAuth();
  const [searchParams] = useSearchParams();
  const api = useMemo(() => createModule2Api(fetchWithAuth), [fetchWithAuth]);
  const [period, setPeriod] = useState("24h");
  const [summary, setSummary] = useState(null);
  const [timeline, setTimeline] = useState(null);
  const [registry, setRegistry] = useState(null);
  const [selectedKey, setSelectedKey] = useState(null);
  const [simulatorCtx, setSimulatorCtx] = useState(() => readStoredGatewayKeyContext());
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(null);
  const [refreshError, setRefreshError] = useState(null);
  const [containmentPanel, setContainmentPanel] = useState(null);
  const [refreshSignal, setRefreshSignal] = useState(0);
  const initialSelectDone = useRef(false);
  const refreshTimerRef = useRef(null);
  const loadSeqRef = useRef(0);

  const selectKey = useCallback((keyId) => {
    initialSelectDone.current = true;
    setSelectedKey(keyId);
  }, []);

  const load = useCallback(async ({ silent = false } = {}) => {
    const seq = ++loadSeqRef.current;
    if (!silent) {
      setLoading(true);
      setLoadError(null);
    }
    try {
      clearModule2Cache();
      const [sum, tl, reg] = await Promise.all([
        api.getUebaSummary(period),
        api.getUebaTimeline(period),
        api.getUebaRegistry(period),
      ]);
      if (seq !== loadSeqRef.current) return;
      setSummary(sum);
      setTimeline(tl);
      setRegistry(reg);
      setRefreshError(null);
      setRefreshSignal((n) => n + 1);
    } catch (err) {
      if (seq !== loadSeqRef.current) return;
      const message = err.message || "Failed to load UEBA analytics.";
      if (silent) {
        setRefreshError(message);
      } else {
        setLoadError(message);
      }
    } finally {
      if (seq !== loadSeqRef.current) return;
      if (!silent) setLoading(false);
    }
  }, [api, period]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const storedKey = getGatewayApiKey();
      const ctx = storedKey
        ? await resolveGatewayKeyContext(fetchWithAuth, storedKey)
        : await fetchSimulatorDefaultContext(fetchWithAuth);
      if (!cancelled && ctx) setSimulatorCtx(ctx);
    })();
    return () => {
      cancelled = true;
    };
  }, [fetchWithAuth]);

  useEffect(() => {
    if (initialSelectDone.current) return;
    const urlKeyId = searchParams.get("key_id");
    if (urlKeyId) {
      selectKey(urlKeyId);
      return;
    }
    if (simulatorCtx.keyId) {
      selectKey(simulatorCtx.keyId);
    }
  }, [searchParams, simulatorCtx.keyId, selectKey]);

  useEffect(() => {
    if (initialSelectDone.current || selectedKey) return;
    if (summary?.top_risky_keys?.length) {
      selectKey(summary.top_risky_keys[0].key_id);
    }
  }, [summary, selectedKey, selectKey]);

  const refreshLive = useCallback(() => {
    clearTimeout(refreshTimerRef.current);
    refreshTimerRef.current = setTimeout(() => {
      load({ silent: true });
    }, REFRESH_DEBOUNCE_MS);
  }, [load]);

  useEffect(() => () => clearTimeout(refreshTimerRef.current), []);

  const handleActionComplete = useCallback(async () => {
    clearModule2Cache();
    await load({ silent: true });
  }, [load]);

  useContainmentPolling(refreshLive, { enabled: !!summary, intervalMs: CONTAINMENT_POLL_MS });

  useEffect(() => {
    const onTelemetry = () => {
      if (!initialSelectDone.current && simulatorCtx.keyId) {
        selectKey(simulatorCtx.keyId);
      }
      refreshLive();
    };
    const onStorage = (event) => {
      if (event.key === TELEMETRY_STORAGE_KEY) onTelemetry();
    };
    window.addEventListener(TELEMETRY_ACTIVITY_EVENT, onTelemetry);
    window.addEventListener("storage", onStorage);
    return () => {
      window.removeEventListener(TELEMETRY_ACTIVITY_EVENT, onTelemetry);
      window.removeEventListener("storage", onStorage);
    };
  }, [refreshLive, simulatorCtx.keyId, selectKey]);

  const { connected: wsConnected } = useRealtimeNotifications({
    onEnforcementEvent: refreshLive,
  });

  if (loading && !summary) {
    return (
      <div>
        <ContextualAppBar title={ANALYST_BRIEF_TITLE} description={PAGE_BRIEFS.ueba} />
        {loadError ? <Module2ErrorState message={loadError} onRetry={load} /> : <Module2PageSkeleton />}
      </div>
    );
  }

  if (loadError && !summary) {
    return (
      <div>
        <ContextualAppBar title={ANALYST_BRIEF_TITLE} description={PAGE_BRIEFS.ueba} />
        <Module2ErrorState message={loadError} onRetry={load} />
      </div>
    );
  }

  const s = summary?.summary || {};
  const containment = summary?.containment || {};
  const timelineData = timeline?.timeline || [];
  const hasTimeline = timelineData.some((p) => (p.total_events ?? 0) > 0);
  const periodLabel = PERIOD_LABELS[period] || period;
  const kpiPeriodStale = Boolean(summary?.period && summary.period !== period);
  const kpiLoading = loading || kpiPeriodStale;

  const kpiItems = buildUebaKpiItems({
    summary: s,
    containment,
    periodLabel,
    containmentPanel,
    onDisabledClick: () => setContainmentPanel((p) => (p === "disabled" ? null : "disabled")),
    onKillSwitchClick: () => setContainmentPanel((p) => (p === "kill-switch" ? null : "kill-switch")),
  });

  const noKeyActivity = !kpiLoading && (s.keys_with_activity ?? 0) === 0;

  return (
    <div>
      <ContextualAppBar title={ANALYST_BRIEF_TITLE} description={PAGE_BRIEFS.ueba} />
      <PageHeader
        title="API Key Behavior Analytics (UEBA)"
        subtitle={`Behavior baselining and risk-scored key registry · ${PERIOD_LABELS[period] || period} window (server UTC)`}
        actions={
          <>
            <span
              className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ${
                wsConnected
                  ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300"
                  : "bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400"
              }`}
              title={
                wsConnected
                  ? "Live enforcement feed connected"
                  : `Polling every ${CONTAINMENT_POLL_MS / 1000}s and on telemetry events`
              }
            >
              <Radio className={`h-3 w-3 ${wsConnected ? "text-emerald-500" : ""}`} />
              {wsConnected ? "Live" : "Polling"}
            </span>
            <PeriodSelector value={period} onChange={setPeriod} />
            <button
              type="button"
              onClick={() => load()}
              disabled={loading}
              className="rounded-lg border border-slate-200 p-2 disabled:opacity-50 dark:border-slate-600"
              aria-label="Refresh UEBA data"
            >
              <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
            </button>
          </>
        }
      />

      {refreshError && summary && (
        <div className="mb-4 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-amber-200 bg-amber-50 px-4 py-2 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200">
          <span>{refreshError}</span>
          <button type="button" onClick={() => load({ silent: true })} className="font-medium underline">
            Retry
          </button>
        </div>
      )}

      {simulatorCtx.prefix && (
        <div className="mb-4 rounded-lg border border-teal-200 bg-teal-50 px-4 py-2.5 text-sm text-teal-900 dark:border-teal-800 dark:bg-teal-950/30 dark:text-teal-100">
          Attack Simulator traffic is recorded under API key{" "}
          <span className="font-mono font-semibold">{simulatorCtx.prefix}</span>
          {simulatorCtx.name ? ` (${simulatorCtx.name})` : ""}.
          {" "}Expand that row (marked <strong>Simulator</strong>) for live prompts and counts.
        </div>
      )}

      <KPIBar items={kpiItems} loading={kpiLoading} />

      <ApiKeyContainmentDetailPanel
        panel={containmentPanel}
        containment={containment}
        onClose={() => setContainmentPanel(null)}
        onSelectKey={(keyId) => {
          selectKey(keyId);
          setContainmentPanel(null);
        }}
      />

      {noKeyActivity && (
        <div className="mt-6">
          <Module2EmptyState
            title="No API key activity in this period"
            message="Run Module 1 simulators (Attack Simulator, MCP live mode) with your org gateway key so events include key_prefix metadata."
            hint="Tip: Prompt injection blocks raise block rate quickly and surface keys in Top Risky Keys."
          />
        </div>
      )}

      <div className="mt-6 grid gap-4 lg:grid-cols-2">
        <ChartCard
          title="Behavior Timeline"
          titleHelpText="Hourly enforcement trend (total, blocked, redacted) for your organization."
        >
          {hasTimeline ? (
            <ResponsiveContainer width="100%" height={260}>
              <LineChart data={timelineData}>
                <CartesianGrid strokeDasharray="3 3" opacity={0.25} />
                <XAxis dataKey="timestamp" tickFormatter={(v) => `${v?.slice(11, 16)} UTC`} fontSize={10} />
                <YAxis fontSize={11} allowDecimals={false} />
                <Tooltip labelFormatter={(v) => `${String(v).replace("T", " ").slice(0, 19)} UTC`} />
                <Line type="monotone" dataKey="total_events" stroke="#0ea5e9" strokeWidth={2} dot={false} name="Total" />
                <Line type="monotone" dataKey="blocked" stroke="#ef4444" strokeWidth={2} dot={false} name="Blocked" />
                <Line type="monotone" dataKey="redacted" stroke="#f59e0b" strokeWidth={2} dot={false} name="Redacted" />
              </LineChart>
            </ResponsiveContainer>
          ) : (
            <p className="py-16 text-center text-sm text-slate-400">No enforcement events in this period.</p>
          )}
        </ChartCard>

        <ChartCard
          title="Top Risky Keys"
          titleHelpText="Click a key prefix to load its drilldown profile and response controls."
        >
          <DataTable
            columns={[
              {
                key: "prefix",
                label: "Key",
                render: (r) => (
                  <button
                    type="button"
                    onClick={() => selectKey(r.key_id)}
                    className={`font-mono text-xs ${selectedKey === r.key_id ? "text-teal-600" : "text-slate-700 dark:text-slate-300"}`}
                  >
                    {r.prefix}
                  </button>
                ),
              },
              { key: "project_id", label: "Project", helpText: "Owning app or project for this credential." },
              { key: "request_count", label: "Requests", helpText: "Enforcement events attributed to this key." },
              {
                key: "risk_band",
                label: "Behavioral risk",
                helpText: "UEBA behavioral tier from velocity, violations, and anomaly signals.",
                render: (r) => (
                  <RiskBandBadge type="behavioral" band={r.risk_band} score={r.risk_score} />
                ),
              },
              { key: "velocity_spike", label: "Velocity x", helpText: "Burst factor vs. baseline; values above 3x warrant review." },
            ]}
            rows={summary?.top_risky_keys || []}
            emptyMessage="No key activity in selected period"
          />
        </ChartCard>
      </div>

      <div className="mt-6">
        <ApiKeyFleetTable
          rows={registry?.results || []}
          selectedKeyId={selectedKey}
          onSelectKey={selectKey}
          fetchWithAuth={fetchWithAuth}
          period={period}
          refreshSignal={refreshSignal}
          simulatorKeyId={simulatorCtx.keyId}
          simulatorKeyPrefix={simulatorCtx.prefix}
          onActionComplete={handleActionComplete}
          loading={loading}
          liveConnected={wsConnected}
        />
      </div>
    </div>
  );
}
