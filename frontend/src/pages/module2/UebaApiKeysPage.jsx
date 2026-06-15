import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Loader2, RefreshCw } from "lucide-react";
import { useAuth } from "../../context/AuthContext";
import { clearModule2Cache, createModule2Api } from "../../api/module2";
import { useContainmentPolling } from "../../hooks/useContainmentPolling";
import { useRealtimeNotifications } from "../../hooks/useRealtimeNotifications";
import { PageHeader } from "../../components/module2/PageHeader";
import { KPIBar } from "../../components/module2/KPIBar";
import { ChartCard } from "../../components/module2/ChartCard";
import { DataTable } from "../../components/module2/DataTable";
import { PeriodSelector } from "../../components/module2/PeriodSelector";
import { ContextualAppBar } from "../../components/module2/ContextualAppBar";
import { ApiKeyFleetTable } from "../../components/module2/ApiKeyFleetTable";
import { ApiKeyContainmentDetailPanel } from "../../components/module2/ApiKeyContainmentDetailPanel";
import { Module2EmptyState, Module2ErrorState } from "../../components/module2/PageStates";
import { buildContainmentKpiItems } from "./pageData";
import { ANALYST_BRIEF_TITLE, PAGE_BRIEFS } from "./pageCopy";

export function UebaApiKeysPage() {
  const { fetchWithAuth } = useAuth();
  const api = useMemo(() => createModule2Api(fetchWithAuth), [fetchWithAuth]);
  const [period, setPeriod] = useState("24h");
  const [summary, setSummary] = useState(null);
  const [timeline, setTimeline] = useState(null);
  const [registry, setRegistry] = useState(null);
  const [selectedKey, setSelectedKey] = useState(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(null);
  const [containmentPanel, setContainmentPanel] = useState(null);
  const initialSelectDone = useRef(false);

  const load = useCallback(async ({ silent = false } = {}) => {
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
      setSummary(sum);
      setTimeline(tl);
      setRegistry(reg);
      if (!initialSelectDone.current && sum?.top_risky_keys?.length) {
        setSelectedKey(sum.top_risky_keys[0].key_id);
        initialSelectDone.current = true;
      }
    } catch (err) {
      if (!silent) {
        setLoadError(err.message || "Failed to load UEBA analytics.");
      }
    } finally {
      if (!silent) setLoading(false);
    }
  }, [api, period]);

  useEffect(() => {
    load();
  }, [load]);

  const refreshContainment = useCallback(async () => {
    clearModule2Cache();
    try {
      const [sum, reg] = await Promise.all([
        api.getUebaSummary(period, { useCache: false }),
        api.getUebaRegistry(period, { useCache: false }),
      ]);
      setSummary((prev) => (
        prev
          ? { ...prev, summary: sum.summary, containment: sum.containment, top_risky_keys: sum.top_risky_keys }
          : sum
      ));
      setRegistry(reg);
    } catch {
      /* silent refresh */
    }
  }, [api, period]);

  const refreshAll = useCallback(async () => {
    clearModule2Cache();
    await load({ silent: true });
  }, [load]);

  useContainmentPolling(refreshContainment, { enabled: !!summary });
  useRealtimeNotifications({
    onEnforcementEvent: refreshContainment,
  });

  const handleActionComplete = useCallback(async () => {
    await refreshAll();
  }, [refreshAll]);

  if (loading && !summary) {
    return (
      <div>
        <ContextualAppBar title={ANALYST_BRIEF_TITLE} description={PAGE_BRIEFS.ueba} />
        <div className="flex justify-center py-20">
          <Loader2 className="h-8 w-8 animate-spin text-teal-500" />
        </div>
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
  const kpiItems = [
    { key: "total-keys", label: "Total Keys", value: s.total_keys ?? 0, helpText: "All API keys provisioned for this organization." },
    { key: "active-keys", label: "Active Keys", value: s.active_keys ?? 0, color: "text-teal-600", helpText: "Keys currently enabled and able to pass ingress auth." },
    ...buildContainmentKpiItems({
      disabledKeys: s.disabled_keys ?? containment.disabled_keys ?? 0,
      activeKillSwitches: s.active_kill_switches ?? containment.active_kill_switches ?? 0,
      clickable: true,
      activePanel: containmentPanel,
      onDisabledClick: () => setContainmentPanel((p) => (p === "disabled" ? null : "disabled")),
      onKillSwitchClick: () => setContainmentPanel((p) => (p === "kill-switch" ? null : "kill-switch")),
    }),
    { key: "keys-with-activity", label: "Keys With Activity", value: s.keys_with_activity ?? 0, helpText: "Keys with at least one enforcement event in this window." },
    { key: "high-risk-keys", label: "High Risk Keys", value: s.high_risk_keys ?? 0, color: "text-red-600", helpText: "Keys exceeding UEBA high-risk thresholds—investigate first." },
  ];

  const noKeyActivity = (s.keys_with_activity ?? 0) === 0;

  return (
    <div>
      <ContextualAppBar title={ANALYST_BRIEF_TITLE} description={PAGE_BRIEFS.ueba} />
      <PageHeader
        title="API Key Behavior Analytics (UEBA)"
        subtitle="Behavior baselining, anomaly flags, and risk-scored key registry"
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

      <ApiKeyContainmentDetailPanel
        panel={containmentPanel}
        containment={containment}
        onClose={() => setContainmentPanel(null)}
        onSelectKey={(keyId) => {
          setSelectedKey(keyId);
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
          <ResponsiveContainer width="100%" height={260}>
            <LineChart data={timeline?.timeline || []}>
              <CartesianGrid strokeDasharray="3 3" opacity={0.25} />
              <XAxis dataKey="timestamp" tickFormatter={(v) => v?.slice(11, 16)} fontSize={10} />
              <YAxis fontSize={11} />
              <Tooltip />
              <Line type="monotone" dataKey="total_events" stroke="#0ea5e9" strokeWidth={2} dot={false} />
              <Line type="monotone" dataKey="blocked" stroke="#ef4444" strokeWidth={2} dot={false} />
              <Line type="monotone" dataKey="redacted" stroke="#f59e0b" strokeWidth={2} dot={false} />
            </LineChart>
          </ResponsiveContainer>
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
                    onClick={() => setSelectedKey(r.key_id)}
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
                label: "Risk",
                helpText: "Current UEBA band: low, medium, or high.",
                render: (r) => (
                  <span className={
                    r.risk_band === "high"
                      ? "font-semibold text-red-600"
                      : r.risk_band === "medium"
                        ? "font-semibold text-amber-600"
                        : "text-slate-600"
                  }>
                    {r.risk_band} ({r.risk_score})
                  </span>
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
          onSelectKey={setSelectedKey}
          fetchWithAuth={fetchWithAuth}
          period={period}
          onActionComplete={handleActionComplete}
          loading={loading}
        />
      </div>
    </div>
  );
}
