import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Line, LineChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Loader2, Radio, RefreshCw } from "lucide-react";
import { useAuth } from "../../context/AuthContext";
import { clearModule2Cache, createModule2Api } from "../../api/module2";
import {
  fetchSimulatorDefaultContext,
  readOrgScopedGatewayKey,
  readStoredGatewayKeyContext,
  resolveGatewayKeyContext,
  SIMULATOR_KEY_CHANGED_EVENT,
} from "../../api/gatewayContext";
import { useContainmentPolling } from "../../hooks/useContainmentPolling";
import { useRealtimeNotifications } from "../../hooks/useRealtimeNotifications";
import { TELEMETRY_ACTIVITY_EVENT, TELEMETRY_STORAGE_KEY } from "../../utils/telemetryEvents";
import { CONTAINMENT_CHANGED_EVENT, CONTAINMENT_STORAGE_KEY } from "../../utils/containmentEvents";
import { PageHeader } from "../../components/module2/PageHeader";
import { KPIBar } from "../../components/module2/KPIBar";
import { module2TooltipProps } from "../../components/module2/module2Chart";
import { ChartCard } from "../../components/module2/ChartCard";
import { DataTable } from "../../components/module2/DataTable";
import { PeriodSelector } from "../../components/module2/PeriodSelector";
import { ContextualAppBar } from "../../components/module2/ContextualAppBar";
import { ApiKeyFleetTable } from "../../components/module2/ApiKeyFleetTable";
import { ApiKeyProfileSidebar } from "../../components/module2/ApiKeyProfileSidebar";
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
  const { fetchWithAuth, user } = useAuth();
  const orgId = user?.organization?.id;
  const [searchParams] = useSearchParams();
  const api = useMemo(() => createModule2Api(fetchWithAuth), [fetchWithAuth]);
  const [period, setPeriod] = useState("24h");
  const [summary, setSummary] = useState(null);
  const [timeline, setTimeline] = useState(null);
  const [registry, setRegistry] = useState(null);
  const [riskCalc, setRiskCalc] = useState(null);
  const [riskCalcDraft, setRiskCalcDraft] = useState(null);
  const [riskCalcSaveError, setRiskCalcSaveError] = useState(null);
  const [riskCalcSaving, setRiskCalcSaving] = useState(false);
  const [selectedKey, setSelectedKey] = useState(null);
  const [profileRow, setProfileRow] = useState(null);
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

  const openProfile = useCallback((row) => {
    if (!row?.key_id) return;
    initialSelectDone.current = true;
    setSelectedKey(row.key_id);
    setProfileRow(row);
  }, []);

  const closeProfile = useCallback(() => {
    setProfileRow(null);
  }, []);

  const findRegistryRow = useCallback((keyId) => {
    return (registry?.results || []).find((row) => row.key_id === keyId) || null;
  }, [registry]);

  const load = useCallback(async ({ silent = false } = {}) => {
    const seq = ++loadSeqRef.current;
    if (!silent) {
      setLoading(true);
      setLoadError(null);
    }
    try {
      clearModule2Cache();
      const [bundle, calc] = await Promise.all([
        api.getUebaBundle(period, { useCache: false }),
        api.getUebaRiskCalculation(period, { useCache: false }),
      ]);
      if (seq !== loadSeqRef.current) return;
      setSummary(bundle.summary);
      setTimeline(bundle.timeline);
      setRegistry(bundle.registry);
      setRiskCalc(calc);
      setRiskCalcDraft(calc.settings || null);
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

  const refreshSimulatorCtx = useCallback(async () => {
    const storedKey = readOrgScopedGatewayKey(orgId);
    const ctx = storedKey
      ? await resolveGatewayKeyContext(fetchWithAuth, storedKey)
      : await fetchSimulatorDefaultContext(fetchWithAuth);
    if (ctx) setSimulatorCtx(ctx);
  }, [fetchWithAuth, orgId]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const storedKey = readOrgScopedGatewayKey(orgId);
      const ctx = storedKey
        ? await resolveGatewayKeyContext(fetchWithAuth, storedKey)
        : await fetchSimulatorDefaultContext(fetchWithAuth);
      if (!cancelled && ctx) setSimulatorCtx(ctx);
    })();
    return () => {
      cancelled = true;
    };
  }, [fetchWithAuth, orgId]);

  useEffect(() => {
    const onKeyOrContainment = () => {
      refreshSimulatorCtx();
    };
    const onSimulatorKeyChanged = (event) => {
      const detail = event?.detail || {};
      if (detail.prefix || detail.keyId) {
        setSimulatorCtx((prev) => ({
          ...prev,
          prefix: detail.prefix || prev.prefix,
          keyId: detail.keyId || prev.keyId,
          name: detail.name || prev.name,
        }));
      } else {
        refreshSimulatorCtx();
      }
    };
    const onStorage = (event) => {
      if (
        event.key === TELEMETRY_STORAGE_KEY
        || event.key === CONTAINMENT_STORAGE_KEY
        || (event.key && event.key.startsWith("zeroshield_gateway_key"))
      ) {
        onKeyOrContainment();
      }
    };
    window.addEventListener(TELEMETRY_ACTIVITY_EVENT, onKeyOrContainment);
    window.addEventListener(CONTAINMENT_CHANGED_EVENT, onKeyOrContainment);
    window.addEventListener(SIMULATOR_KEY_CHANGED_EVENT, onSimulatorKeyChanged);
    window.addEventListener("storage", onStorage);
    return () => {
      window.removeEventListener(TELEMETRY_ACTIVITY_EVENT, onKeyOrContainment);
      window.removeEventListener(CONTAINMENT_CHANGED_EVENT, onKeyOrContainment);
      window.removeEventListener(SIMULATOR_KEY_CHANGED_EVENT, onSimulatorKeyChanged);
      window.removeEventListener("storage", onStorage);
    };
  }, [refreshSimulatorCtx]);

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

  const canEditRiskCalc = Boolean(user?.is_superuser || (user?.roles || []).includes("platform_admin"));
  const updateRiskCalcDraft = useCallback((mutator) => {
    setRiskCalcDraft((prev) => {
      const next = structuredClone(prev || {});
      mutator(next);
      return next;
    });
  }, []);

  const saveRiskCalculation = useCallback(async () => {
    if (!canEditRiskCalc || !riskCalcDraft) return;
    setRiskCalcSaving(true);
    setRiskCalcSaveError(null);
    try {
      const data = await api.updateUebaRiskCalculation(riskCalcDraft);
      setRiskCalc((prev) => ({ ...(prev || {}), settings: data.settings }));
      setRiskCalcDraft(data.settings);
      await load({ silent: true });
    } catch (err) {
      setRiskCalcSaveError(err.message || "Failed to save risk calculation settings.");
    } finally {
      setRiskCalcSaving(false);
    }
  }, [api, canEditRiskCalc, load, riskCalcDraft]);

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

  useContainmentPolling(refreshLive, { enabled: !!summary, intervalMs: CONTAINMENT_POLL_MS });

  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === "visible") refreshLive();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [refreshLive]);

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
          {" "}Expand the row or click <strong>Profile</strong> for the full behavior sidebar.
        </div>
      )}

      <KPIBar items={kpiItems} loading={kpiLoading} />

      <ApiKeyProfileSidebar
        open={!!profileRow}
        rowSummary={profileRow ? (findRegistryRow(profileRow.key_id) || profileRow) : null}
        period={period}
        fetchWithAuth={fetchWithAuth}
        onClose={closeProfile}
        onActionComplete={handleActionComplete}
        simulatorKeyPrefix={simulatorCtx.prefix}
        riskCalculation={
          riskCalc
            ? { settings: riskCalc.settings, formula_reference: riskCalc.formula_reference }
            : null
        }
        riskCalcDraft={riskCalcDraft}
        riskCalcGuardrails={riskCalcDraft?.weight_guardrails}
        canEditRiskCalc={canEditRiskCalc}
        onRiskCalcChange={updateRiskCalcDraft}
        onRiskCalcSave={saveRiskCalculation}
        riskCalcSaving={riskCalcSaving}
        riskCalcSaveError={riskCalcSaveError}
        refreshSignal={refreshSignal}
        liveConnected={wsConnected}
      />

      <ApiKeyContainmentDetailPanel
        panel={containmentPanel}
        containment={containment}
        onClose={() => setContainmentPanel(null)}
        onSelectKey={(keyId) => {
          const row = findRegistryRow(keyId);
          if (row) openProfile(row);
          else selectKey(keyId);
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
                <Tooltip {...module2TooltipProps} />
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
          titleHelpText="Click a key prefix to open its profile sidebar with behavior timeline and safety rates."
        >
          <DataTable
            columns={[
              {
                key: "prefix",
                label: "Key",
                render: (r) => (
                  <button
                    type="button"
                    onClick={() => openProfile(findRegistryRow(r.key_id) || r)}
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

      <div className="mt-6" id="ueba-key-fleet-registry">
        <ApiKeyFleetTable
          rows={registry?.results || []}
          selectedKeyId={selectedKey}
          activeProfileKeyId={profileRow?.key_id}
          onSelectKey={selectKey}
          onOpenProfile={openProfile}
          fetchWithAuth={fetchWithAuth}
          period={period}
          refreshSignal={refreshSignal}
          simulatorKeyId={simulatorCtx.keyId}
          simulatorKeyPrefix={simulatorCtx.prefix}
          orgId={orgId}
          riskCalculation={
            riskCalc
              ? { settings: riskCalc.settings, formula_reference: riskCalc.formula_reference }
              : null
          }
          onSimulatorKeyAdopted={(ctx) => {
            setSimulatorCtx(ctx);
            if (ctx?.keyId) {
              const row = findRegistryRow(ctx.keyId);
              if (row) openProfile(row);
              else selectKey(ctx.keyId);
            }
          }}
          onActionComplete={handleActionComplete}
          loading={loading}
          liveConnected={wsConnected}
        />
      </div>
    </div>
  );
}
