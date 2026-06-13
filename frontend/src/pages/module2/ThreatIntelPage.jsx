import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Loader2, Plus, RefreshCw } from "lucide-react";
import { useAuth } from "../../context/AuthContext";
import { createModule2Api } from "../../api/module2";
import { PageHeader } from "../../components/module2/PageHeader";
import { KPIBar } from "../../components/module2/KPIBar";
import { ChartCard } from "../../components/module2/ChartCard";
import { DataTable } from "../../components/module2/DataTable";
import { PeriodSelector } from "../../components/module2/PeriodSelector";
import { Module2ErrorState, Module2PageSkeleton } from "../../components/module2/PageStates";
import { ContextualAppBar } from "../../components/module2/ContextualAppBar";
import { ANALYST_BRIEF_TITLE, PAGE_BRIEFS } from "./pageCopy";
import {
  buildTelemetryKpis,
  formatAttackVectors,
  formatTelemetryTimeline,
  telemetrySeriesColor,
  telemetrySeriesKeys,
} from "./pageData";

const SERIES_LABELS = {
  injection_attempts: "Injection",
  pii_leaks: "PII Leaks",
  behavior_scoring: "Behavior",
  threat_intel_matches: "Threat Intel",
};

export function ThreatIntelPage() {
  const { fetchWithAuth } = useAuth();
  const api = useMemo(() => createModule2Api(fetchWithAuth), [fetchWithAuth]);
  const [period, setPeriod] = useState("7d");
  const [telemetry, setTelemetry] = useState(null);
  const [entries, setEntries] = useState([]);
  const [loading, setLoading] = useState(true);
  const [entriesLoading, setEntriesLoading] = useState(false);
  const [error, setError] = useState(null);
  const [syncStatus, setSyncStatus] = useState(null);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({
    threat_type: "",
    indicator: "",
    owasp_code: "",
    confidence: 0.8,
    auto_block: false,
  });

  const loadTelemetry = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setTelemetry(await api.getThreatTelemetry(period));
    } catch (e) {
      setError(e.message || "Failed to load threat telemetry.");
      setTelemetry(null);
    } finally {
      setLoading(false);
    }
  }, [api, period]);

  const loadEntries = useCallback(async () => {
    setEntriesLoading(true);
    try {
      const data = await api.listThreatIntel();
      setEntries(data.results || data);
    } catch (e) {
      if (!error) setError(e.message || "Failed to load threat intel entries.");
    } finally {
      setEntriesLoading(false);
    }
  }, [api, error]);

  useEffect(() => {
    loadTelemetry();
    loadEntries();
  }, [loadTelemetry, loadEntries]);

  const handleCreate = async () => {
    await api.createThreatIntel({ ...form, source: "manual" });
    setShowForm(false);
    loadEntries();
  };

  const handleSync = async () => {
    const status = await api.syncThreatIntel();
    setSyncStatus(status);
  };

  const toggleAutoBlock = async (entry) => {
    await api.updateThreatIntel(entry.id, { auto_block: !entry.auto_block });
    loadEntries();
  };

  const timeline = formatTelemetryTimeline(telemetry?.timeline || []);
  const vectors = formatAttackVectors(telemetry?.top_attack_vectors || []);
  const seriesKeys = telemetrySeriesKeys();

  return (
    <div>
      <ContextualAppBar title={ANALYST_BRIEF_TITLE} description={PAGE_BRIEFS.threatIntel} />
      <PageHeader
        title="Threat Intelligence"
        subtitle="Live attack telemetry, indicator management, and gateway sync"
        actions={
          <>
            <PeriodSelector value={period} onChange={setPeriod} />
            <button
              type="button"
              onClick={loadTelemetry}
              className="rounded-lg border border-slate-200 p-2 dark:border-slate-600"
              aria-label="Refresh telemetry"
            >
              <RefreshCw className="h-4 w-4" />
            </button>
            <button
              type="button"
              onClick={handleSync}
              className="flex items-center gap-1 rounded-lg border px-3 py-1.5 text-sm dark:border-slate-600"
            >
              <RefreshCw className="h-4 w-4" /> Sync to Gateway
            </button>
            <button
              type="button"
              onClick={() => setShowForm(true)}
              className="flex items-center gap-1 rounded-lg bg-teal-600 px-4 py-2 text-sm text-white"
            >
              <Plus className="h-4 w-4" /> Add Entry
            </button>
          </>
        }
      />

      {/* Persistent Redis Sync Banner */}
      {syncStatus && (
        <div
          className={`mb-4 flex items-center justify-between rounded-xl border px-4 py-3 text-sm ${
            syncStatus.entry_count > 0
              ? "border-teal-200 bg-teal-50 text-teal-800 dark:border-teal-800 dark:bg-teal-900/30 dark:text-teal-200"
              : "border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-800 dark:bg-amber-900/30 dark:text-amber-200"
          }`}
        >
          <span>
            Gateway sync live for <strong>{syncStatus.org_slug}</strong> —{" "}
            <strong>{syncStatus.entry_count}</strong> indicators active in Redis cache
            {" "}(<code className="font-mono text-xs">{syncStatus.redis_key || `firewall:threat_intel:${syncStatus.org_slug}`}</code>)
          </span>
          <span className="text-xs opacity-70">{syncStatus.last_sync_at ? new Date(syncStatus.last_sync_at).toLocaleString() : "just now"}</span>
        </div>
      )}

      {error && !telemetry && <Module2ErrorState message={error} onRetry={loadTelemetry} />}
      {loading && !telemetry && !error && <Module2PageSkeleton />}
      {telemetry && (
      <>
      <KPIBar items={buildTelemetryKpis(telemetry?.summary)} />

      <div className="mt-6 grid gap-4 lg:grid-cols-2">
        <ChartCard
          title="Attack Volume Over Time"
          titleHelpText="Stacked timeline of attack categories—use to correlate IOC updates with match volume."
        >
          <ResponsiveContainer width="100%" height={280}>
            <AreaChart data={timeline}>
              <CartesianGrid strokeDasharray="3 3" stroke="#334155" opacity={0.2} />
              <XAxis dataKey="label" fontSize={10} />
              <YAxis fontSize={11} />
              <Tooltip />
              <Legend />
              {seriesKeys.map((key) => (
                <Area
                  key={key}
                  type="monotone"
                  dataKey={key}
                  name={SERIES_LABELS[key]}
                  stackId="1"
                  stroke={telemetrySeriesColor(key)}
                  fill={telemetrySeriesColor(key)}
                  fillOpacity={0.35}
                />
              ))}
            </AreaChart>
          </ResponsiveContainer>
        </ChartCard>

        <ChartCard
          title="Top Attack Vectors"
          titleHelpText="Top OWASP AI Top 10 and custom threat vectors by hit count."
        >
          <ResponsiveContainer width="100%" height={280}>
            <BarChart data={vectors}>
              <CartesianGrid strokeDasharray="3 3" stroke="#334155" opacity={0.2} />
              <XAxis dataKey="name" fontSize={10} interval={0} angle={-15} textAnchor="end" height={55} />
              <YAxis fontSize={11} />
              <Tooltip />
              <Bar dataKey="count" fill="#8b5cf6" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>
      </div>

      {/* Stage Hit Distribution — threat intel hits per pipeline stage */}
      {(telemetry?.stage_hit_distribution || []).length > 0 && (
        <div className="mt-4">
          <ChartCard
            title="Stage Hit Attribution — Where Threat Intel Matches Fire"
            titleHelpText="Pipeline stage where IOCs matched—tells you if feeds catch ingress vs. retrieval-stage poison."
          >
            <ResponsiveContainer width="100%" height={180}>
              <BarChart data={telemetry.stage_hit_distribution} layout="vertical" margin={{ left: 10 }}>
                <CartesianGrid strokeDasharray="3 3" opacity={0.25} />
                <XAxis type="number" fontSize={11} />
                <YAxis dataKey="stage" type="category" fontSize={11} width={80} />
                <Tooltip />
                <Bar dataKey="count" fill="#ef4444" radius={[0, 4, 4, 0]} name="Matches" />
              </BarChart>
            </ResponsiveContainer>
          </ChartCard>
        </div>
      )}

      {showForm && (
        <div className="mt-4 rounded-xl border p-4 dark:border-slate-700">
          <div className="grid gap-3 sm:grid-cols-2">
            <input
              placeholder="Threat Type"
              value={form.threat_type}
              onChange={(e) => setForm({ ...form, threat_type: e.target.value })}
              className="rounded-lg border px-3 py-2 text-sm dark:border-slate-600 dark:bg-slate-800"
            />
            <input
              placeholder="OWASP Code"
              value={form.owasp_code}
              onChange={(e) => setForm({ ...form, owasp_code: e.target.value })}
              className="rounded-lg border px-3 py-2 text-sm dark:border-slate-600 dark:bg-slate-800"
            />
            <input
              placeholder="Indicator (regex or fingerprint)"
              value={form.indicator}
              onChange={(e) => setForm({ ...form, indicator: e.target.value })}
              className="sm:col-span-2 rounded-lg border px-3 py-2 text-sm dark:border-slate-600 dark:bg-slate-800"
            />
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={form.auto_block}
                onChange={(e) => setForm({ ...form, auto_block: e.target.checked })}
              />
              Auto-block on match
            </label>
          </div>
          <div className="mt-3 flex gap-2">
            <button type="button" onClick={handleCreate} className="rounded-lg bg-teal-600 px-4 py-2 text-sm text-white">
              Save
            </button>
            <button type="button" onClick={() => setShowForm(false)} className="rounded-lg border px-4 py-2 text-sm">
              Cancel
            </button>
          </div>
        </div>
      )}

      <div className="mt-6 rounded-xl border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-700 dark:bg-slate-800/60">
        <div className="mb-3 flex items-center justify-between">
          <h3 className="text-sm font-semibold text-slate-700 dark:text-slate-200">Threat Indicators</h3>
          {entriesLoading && <Loader2 className="h-4 w-4 animate-spin text-teal-500" />}
        </div>
        <DataTable
          columns={[
            { key: "threat_type", label: "Type", helpText: "Threat family label (e.g., jailbreak, toxic prompt, malicious hash)." },
            {
              key: "indicator",
              label: "Indicator",
              helpText: "Regex, fingerprint, or signature matched at the gateway.",
              render: (r) => <code className="text-xs">{r.indicator?.slice(0, 60)}</code>,
            },
            { key: "owasp_code", label: "OWASP", helpText: "Mapped OWASP AI Top 10 code when applicable." },
            { key: "confidence", label: "Confidence", helpText: "Match confidence threshold used by the gateway scorer.", render: (r) => `${Math.round(r.confidence * 100)}%` },
            {
              key: "auto_block",
              label: "Auto-Block",
              helpText: "When ON, a match triggers an immediate hard block at the gateway.",
              render: (r) => (
                <button
                  type="button"
                  onClick={() => toggleAutoBlock(r)}
                  className={`text-xs ${r.auto_block ? "font-medium text-red-600" : "text-slate-400"}`}
                >
                  {r.auto_block ? "ON" : "OFF"}
                </button>
              ),
            },
            { key: "source", label: "Source" },
          ]}
          rows={entries}
          emptyMessage="No threat intel entries configured."
        />
      </div>
      </>
      )}
    </div>
  );
}
