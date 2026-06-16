import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
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
import { AlertTriangle, BookOpen, Loader2, Plus, Radio, RefreshCw, Shield, Trash2 } from "lucide-react";
import { useAuth } from "../../context/AuthContext";
import { clearModule2Cache, createModule2Api } from "../../api/module2";
import { useRealtimeNotifications } from "../../hooks/useRealtimeNotifications";
import { TELEMETRY_ACTIVITY_EVENT } from "../../utils/telemetryEvents";
import { PageHeader } from "../../components/module2/PageHeader";
import { KPIBar } from "../../components/module2/KPIBar";
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
import {
  buildIocFleetStats,
  buildTelemetryKpis,
  formatAttackVectors,
  formatConfidencePercent,
  formatTelemetryTimeline,
  iocMatchRatePct,
  normalizeThreatIntelRows,
  telemetrySeriesColor,
  telemetrySeriesKeys,
} from "./pageData";

const PERIOD_LABELS = { "1h": "1 hour", "24h": "24 hours", "7d": "7 days", "30d": "30 days" };
const REFRESH_DEBOUNCE_MS = 300;
const ATTACK_SIMULATOR_PATH = "/?tab=firewall-1-1";
const INCIDENTS_THREAT_INTEL_PATH = "/incidents?source=threat_intel";

const IOC_EXAMPLE = {
  threat_type: "jailbreak_probe",
  indicator: "ignore previous instructions",
  owasp_code: "LLM01",
  confidence: 0.8,
  auto_block: true,
};

const VALIDATION_STEPS = [
  {
    title: "Add Entry (this page)",
    body: "Saves an IOC to the control-plane database — a regex or plain-text fingerprint plus metadata. The gateway cannot see it until you sync.",
  },
  {
    title: "Sync to Gateway",
    body: "Pushes every org indicator to Redis (firewall:threat_intel:{org}). Required after add, edit, delete, or auto-block changes.",
  },
  {
    title: "Open M1.1 Attack Simulator",
    body: "Use Live enforcement mode (not Dry-Run). Dry-Run does not emit enforcement events, so KPIs here stay at zero.",
  },
  {
    title: "Send a matching prompt",
    body: "Paste your indicator text verbatim in the simulator prompt — e.g. “ignore previous instructions”. With Auto-Block ON, expect a block response.",
  },
  {
    title: "Validate on this page",
    body: "IOC Matches KPI should rise (try 24h period). Refresh or wait for Live updates. The indicator row does not auto-fill from traffic — only telemetry changes.",
  },
];

const SERIES_LABELS = {
  injection_attempts: "Injection",
  pii_leaks: "PII",
  behavior_scoring: "API Keys",
  threat_intel_matches: "IOC Matches",
};

const STAGE_LABELS = {
  ingress: "Ingress (prompt)",
  query: "RAG Query",
  retriever: "RAG Retriever",
  ranker: "RAG Ranker",
  generator: "RAG Generator",
};

function formatStageLabel(stage) {
  return STAGE_LABELS[stage] || stage;
}

function IocEntryGuide({ onUseExample, iocMatchCount, entryCount, syncQueued }) {
  return (
    <aside className="rounded-xl border border-teal-200 bg-teal-50/50 p-4 text-xs leading-relaxed text-teal-950 dark:border-teal-800/50 dark:bg-teal-950/20 dark:text-teal-100">
      <div className="mb-3 flex items-center gap-2 font-semibold text-teal-800 dark:text-teal-200">
        <BookOpen className="h-4 w-4 shrink-0" />
        What does Add Entry do?
      </div>
      <p className="text-teal-900/90 dark:text-teal-100/90">
        <strong>Add Entry</strong> registers a threat indicator (IOC) in your org library. The gateway matches user
        prompts against these patterns at <strong>tier-0</strong> (before policy). Matches can hard-block when{" "}
        <strong>Auto-Block</strong> is enabled, or monitor-only when it is off.
      </p>

      <p className="mt-3 font-semibold text-teal-800 dark:text-teal-200">Validate end-to-end (simulator)</p>
      <ol className="mt-2 list-decimal space-y-2 pl-4">
        {VALIDATION_STEPS.map((step) => (
          <li key={step.title}>
            <span className="font-medium">{step.title}</span>
            {" — "}
            {step.body}
          </li>
        ))}
      </ol>

      <div className="mt-4 rounded-lg border border-teal-200/80 bg-white/70 px-3 py-2 dark:border-teal-800/40 dark:bg-slate-900/40">
        <p className="font-semibold text-slate-700 dark:text-slate-200">Is it working?</p>
        <ul className="mt-2 space-y-1 text-slate-600 dark:text-slate-300">
          <li>{entryCount > 0 ? "✓" : "○"} Indicator row appears after Save</li>
          <li>{syncQueued ? "✓" : "○"} Sync to Gateway completed (banner above)</li>
          <li>{iocMatchCount > 0 ? "✓" : "○"} IOC Matches KPI &gt; 0 after simulator test</li>
        </ul>
      </div>

      <div className="mt-4 flex flex-col gap-2 sm:flex-row">
        <button
          type="button"
          onClick={onUseExample}
          className="rounded-lg border border-teal-300 bg-white px-3 py-2 text-xs font-medium text-teal-800 hover:bg-teal-50 dark:border-teal-700 dark:bg-slate-900 dark:text-teal-200 dark:hover:bg-teal-950/40"
        >
          Fill example IOC
        </button>
        <Link
          to={ATTACK_SIMULATOR_PATH}
          className="inline-flex items-center justify-center rounded-lg bg-teal-600 px-3 py-2 text-xs font-semibold text-white hover:bg-teal-700"
        >
          Open M1.1 Simulator
        </Link>
      </div>
    </aside>
  );
}

function TimelineTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  const total = payload.reduce((sum, row) => sum + (Number(row.value) || 0), 0);
  return (
    <div className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs shadow-md dark:border-slate-600 dark:bg-slate-800">
      <p className="font-semibold text-slate-700 dark:text-slate-200">{label}</p>
      <p className="mt-1 text-slate-500">Category total: {total}</p>
      <ul className="mt-2 space-y-1">
        {payload.map((row) => (
          <li key={row.dataKey} className="flex justify-between gap-4">
            <span style={{ color: row.color }}>{row.name}</span>
            <span className="font-mono">{row.value}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function ThreatIntelSocPanel({ summary, iocLibrary, fleetStats, topVector, period }) {
  const matchRate = iocMatchRatePct(summary);
  const lib = iocLibrary || {};
  const fleet = fleetStats || {};
  const periodLabel = PERIOD_LABELS[period] || period;

  return (
    <div className="mb-4 grid gap-3 lg:grid-cols-3">
      <div className="rounded-xl border border-violet-200 bg-violet-50/70 px-4 py-3 text-xs leading-relaxed text-violet-950 dark:border-violet-800/60 dark:bg-violet-950/25 dark:text-violet-100">
        <div className="mb-2 flex items-center gap-2 font-semibold">
          <Shield className="h-4 w-4" />
          IOC library posture
        </div>
        <p>
          <strong>{lib.total ?? fleet.total ?? 0}</strong> indicators configured
          {" · "}
          <strong>{lib.auto_block_enabled ?? fleet.autoBlock ?? 0}</strong> armed for auto-block
          {(lib.expired ?? fleet.expired ?? 0) > 0 && (
            <>
              {" · "}
              <span className="text-red-600 dark:text-red-400">
                <strong>{lib.expired ?? fleet.expired}</strong> expired
              </span>
            </>
          )}
          {(fleet.expiringSoon ?? 0) > 0 && (
            <>
              {" · "}
              <span className="text-amber-700 dark:text-amber-300">
                <strong>{fleet.expiringSoon}</strong> expiring within 7 days
              </span>
            </>
          )}
        </p>
        <p className="mt-2 text-violet-800/80 dark:text-violet-200/80">
          Sources: manual {fleet.bySource?.manual ?? 0}, feed {fleet.bySource?.feed ?? 0}, auto{" "}
          {fleet.bySource?.auto ?? 0}. After edits, use <strong>Sync to Gateway</strong> so Redis picks up changes.
        </p>
      </div>

      <div className="rounded-xl border border-slate-200 bg-slate-50/80 px-4 py-3 text-xs leading-relaxed text-slate-700 dark:border-slate-600 dark:bg-slate-900/40 dark:text-slate-200">
        <p className="font-semibold">IOC effectiveness ({periodLabel} window)</p>
        <p className="mt-2">
          <strong className="text-2xl text-violet-600 dark:text-violet-400">{matchRate}%</strong>
          {" "}of enforcement events were IOC matches
          {" "}({summary?.threat_intel_matches ?? 0} / {summary?.total_events ?? 0}).
        </p>
        <p className="mt-2 text-slate-500 dark:text-slate-400">
          Injection blocks from M1.1 usually count under <strong>Injection</strong>, not IOC Matches, unless metadata
          is tagged as threat intel.
        </p>
      </div>

      <div className="rounded-xl border border-slate-200 bg-slate-50/80 px-4 py-3 text-xs leading-relaxed text-slate-700 dark:border-slate-600 dark:bg-slate-900/40 dark:text-slate-200">
        <p className="font-semibold">Top label this period</p>
        <p className="mt-2 text-lg font-bold text-slate-900 dark:text-white">
          {topVector?.name || "—"}
        </p>
        {topVector?.count > 0 && (
          <p className="mt-1 text-slate-500">
            {topVector.count.toLocaleString()} enforcement events with this OWASP / threat_type label.
          </p>
        )}
        <Link
          to={INCIDENTS_THREAT_INTEL_PATH}
          className="mt-3 inline-flex text-teal-600 hover:underline dark:text-teal-400"
        >
          Open M2.6 incidents (threat intel lane) →
        </Link>
      </div>
    </div>
  );
}

function ThreatIntelTelemetryDashboard({ telemetry, period }) {
  const timeline = formatTelemetryTimeline(telemetry?.timeline);
  const vectors = formatAttackVectors(telemetry?.top_attack_vectors);
  const seriesKeys = telemetrySeriesKeys();
  const summary = telemetry?.summary || {};
  const stageRows = (Array.isArray(telemetry?.stage_hit_distribution)
    ? telemetry.stage_hit_distribution
    : []
  ).map((row) => ({
    ...row,
    stageLabel: formatStageLabel(row.stage),
  }));

  return (
    <>
      <div className="mb-4 rounded-xl border border-amber-200 bg-amber-50/60 px-4 py-3 text-xs leading-relaxed text-amber-950 dark:border-amber-800/60 dark:bg-amber-950/20 dark:text-amber-100">
        <strong>Telemetry</strong> below is live enforcement data for{" "}
        <strong>{PERIOD_LABELS[period] || period}</strong>. The <strong>IOC library</strong> table at the bottom is your
        configuration — it does not auto-populate from traffic.
      </div>

      <KPIBar items={buildTelemetryKpis(summary)} />

      <div className="mt-6 grid gap-4 lg:grid-cols-2">
        <ChartCard
          title="Attack Categories Over Time"
          titleHelpText="Stacked timeline of injection, PII, API-key, and IOC-match events. Category totals may be lower than Total Events when events fall outside these buckets."
        >
          <ResponsiveContainer width="100%" height={280}>
            <AreaChart data={timeline}>
              <CartesianGrid strokeDasharray="3 3" stroke="#334155" opacity={0.2} />
              <XAxis dataKey="label" fontSize={10} />
              <YAxis fontSize={11} allowDecimals={false} />
              <Tooltip content={<TimelineTooltip />} />
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
          title="Top OWASP / Threat Labels"
          titleHelpText="Most common owasp_code or threat_type values on enforcement events — includes policy blocks and scanner hits, not only your IOC table."
        >
          {vectors.length > 0 ? (
            <ResponsiveContainer width="100%" height={280}>
              <BarChart data={vectors}>
                <CartesianGrid strokeDasharray="3 3" stroke="#334155" opacity={0.2} />
                <XAxis dataKey="name" fontSize={10} interval={0} angle={-15} textAnchor="end" height={55} />
                <YAxis fontSize={11} allowDecimals={false} />
                <Tooltip
                  formatter={(value) => [value, "Events"]}
                  labelFormatter={(label) => `Label: ${label}`}
                />
                <Bar dataKey="count" fill="#8b5cf6" radius={[4, 4, 0, 0]} name="Events" />
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <p className="py-8 text-center text-sm text-slate-400">
              No OWASP or threat_type labels on events in this window.
            </p>
          )}
        </ChartCard>
      </div>

      {stageRows.length > 0 && (
        <div className="mt-4">
          <ChartCard
            title="Where IOC Matches Fired (by pipeline stage)"
            titleHelpText="Only events classified as threat-intel lane. Ingress = prompt scan; RAG stages show retrieval-time poison or context injection."
          >
            <ResponsiveContainer width="100%" height={180}>
              <BarChart data={stageRows} layout="vertical" margin={{ left: 10 }}>
                <CartesianGrid strokeDasharray="3 3" opacity={0.25} />
                <XAxis type="number" fontSize={11} allowDecimals={false} />
                <YAxis dataKey="stageLabel" type="category" fontSize={11} width={120} />
                <Tooltip
                  formatter={(value) => [value, "IOC matches"]}
                  labelFormatter={(_, payload) => payload?.[0]?.payload?.stage || ""}
                />
                <Bar dataKey="count" fill="#ef4444" radius={[0, 4, 4, 0]} name="IOC matches" />
              </BarChart>
            </ResponsiveContainer>
          </ChartCard>
        </div>
      )}
    </>
  );
}

export function ThreatIntelPage() {
  return (
    <Module2PageErrorBoundary title="Threat Intelligence Ops failed to render">
      <ThreatIntelPageInner />
    </Module2PageErrorBoundary>
  );
}

function ThreatIntelPageInner() {
  const { fetchWithAuth, user } = useAuth();
  const api = useMemo(() => createModule2Api(fetchWithAuth), [fetchWithAuth]);
  const isAdmin =
    !!user?.is_superuser
    || (Array.isArray(user?.roles) && user.roles.includes("platform_admin"));

  const [period, setPeriod] = useState("7d");
  const [telemetry, setTelemetry] = useState(null);
  const [entries, setEntries] = useState([]);
  const [loading, setLoading] = useState(true);
  const [entriesLoading, setEntriesLoading] = useState(false);
  const [error, setError] = useState(null);
  const [entriesError, setEntriesError] = useState(null);
  const [syncStatus, setSyncStatus] = useState(null);
  const [syncing, setSyncing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [deletingId, setDeletingId] = useState(null);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({
    threat_type: "",
    indicator: "",
    owasp_code: "",
    confidence: 0.8,
    auto_block: false,
  });

  const loadSeqRef = useRef(0);
  const entriesSeqRef = useRef(0);
  const refreshTimerRef = useRef(null);

  const loadTelemetry = useCallback(async ({ silent = false } = {}) => {
    const seq = ++loadSeqRef.current;
    if (!silent) {
      setLoading(true);
      setError(null);
    }
    try {
      clearModule2Cache();
      const data = await api.getThreatTelemetry(period, { useCache: false });
      if (seq !== loadSeqRef.current) return;
      setTelemetry(data);
      setError(null);
    } catch (e) {
      if (seq !== loadSeqRef.current) return;
      setError(e.message || "Failed to load threat telemetry.");
      if (!silent) setTelemetry(null);
    } finally {
      if (seq === loadSeqRef.current && !silent) setLoading(false);
    }
  }, [api, period]);

  const loadEntries = useCallback(async ({ silent = false } = {}) => {
    const seq = ++entriesSeqRef.current;
    if (!silent) setEntriesLoading(true);
    try {
      const data = await api.listThreatIntel();
      if (seq !== entriesSeqRef.current) return;
      setEntries(normalizeThreatIntelRows(data));
      setEntriesError(null);
    } catch (e) {
      if (seq !== entriesSeqRef.current) return;
      setEntriesError(e.message || "Failed to load threat intel entries.");
    } finally {
      if (seq === entriesSeqRef.current && !silent) setEntriesLoading(false);
    }
  }, [api]);

  const refreshLive = useCallback(() => {
    clearTimeout(refreshTimerRef.current);
    refreshTimerRef.current = setTimeout(() => {
      loadTelemetry({ silent: true });
      loadEntries({ silent: true });
    }, REFRESH_DEBOUNCE_MS);
  }, [loadTelemetry, loadEntries]);

  useEffect(() => () => clearTimeout(refreshTimerRef.current), []);

  const { connected: wsConnected } = useRealtimeNotifications({
    onEnforcementEvent: refreshLive,
  });

  useEffect(() => {
    const onTelemetry = () => refreshLive();
    window.addEventListener(TELEMETRY_ACTIVITY_EVENT, onTelemetry);
    return () => window.removeEventListener(TELEMETRY_ACTIVITY_EVENT, onTelemetry);
  }, [refreshLive]);

  useEffect(() => {
    loadTelemetry();
  }, [loadTelemetry]);

  useEffect(() => {
    loadEntries();
  }, [loadEntries]);

  const handleCreate = async () => {
    if (!form.threat_type.trim() || !form.indicator.trim()) {
      setEntriesError("Threat type and indicator are required.");
      return;
    }
    setSaving(true);
    setEntriesError(null);
    try {
      await api.createThreatIntel({ ...form, source: "manual" });
      setShowForm(false);
      setForm({
        threat_type: "",
        indicator: "",
        owasp_code: "",
        confidence: 0.8,
        auto_block: false,
      });
      await loadEntries();
    } catch (e) {
      setEntriesError(e.message || "Failed to create indicator.");
    } finally {
      setSaving(false);
    }
  };

  const handleSync = async () => {
    setSyncing(true);
    setEntriesError(null);
    try {
      const status = await api.syncThreatIntel();
      setSyncStatus(status);
      await loadEntries({ silent: true });
    } catch (e) {
      const msg = e.message || "Gateway sync failed.";
      setEntriesError(
        isAdmin ? msg : `${msg} Sync requires platform admin or superuser.`,
      );
    } finally {
      setSyncing(false);
    }
  };

  const toggleAutoBlock = async (entry) => {
    setEntriesError(null);
    try {
      await api.updateThreatIntel(entry.id, { auto_block: !entry.auto_block });
      await loadEntries({ silent: true });
    } catch (e) {
      setEntriesError(e.message || "Failed to update auto-block setting.");
    }
  };

  const handleDelete = async (entry) => {
    const label = entry.threat_type || entry.indicator?.slice(0, 40) || "this indicator";
    if (!window.confirm(`Delete IOC "${label}"? Sync to Gateway after delete so Redis stops matching it.`)) {
      return;
    }
    setDeletingId(entry.id);
    setEntriesError(null);
    try {
      await api.deleteThreatIntel(entry.id);
      await loadEntries();
      setSyncStatus(null);
    } catch (e) {
      setEntriesError(e.message || "Failed to delete indicator.");
    } finally {
      setDeletingId(null);
    }
  };

  const fillExampleIoc = () => {
    setForm({ ...IOC_EXAMPLE });
    setShowForm(true);
    setEntriesError(null);
  };

  const summary = telemetry?.summary || {};
  const hasTelemetryActivity = (summary.total_events ?? 0) > 0;
  const fleetStats = useMemo(() => buildIocFleetStats(entries), [entries]);
  const topVector = formatAttackVectors(telemetry?.top_attack_vectors)[0] || null;

  if (loading && !telemetry) {
    return (
      <div>
        <ContextualAppBar title={ANALYST_BRIEF_TITLE} description={PAGE_BRIEFS.threatIntel} />
        {error ? <Module2ErrorState message={error} onRetry={() => loadTelemetry()} /> : <Module2PageSkeleton />}
      </div>
    );
  }

  return (
    <div>
      <ContextualAppBar title={ANALYST_BRIEF_TITLE} description={PAGE_BRIEFS.threatIntel} />
      <PageHeader
        title="Threat Intelligence Ops"
        subtitle={`IOC library, gateway sync, and live attack telemetry · ${PERIOD_LABELS[period] || period} window (server UTC)`}
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
            <PeriodSelector value={period} onChange={setPeriod} />
            <button
              type="button"
              onClick={() => {
                loadTelemetry();
                loadEntries();
              }}
              className="rounded-lg border border-slate-200 p-2 dark:border-slate-600"
              aria-label="Refresh threat intel data"
            >
              <RefreshCw className="h-4 w-4" />
            </button>
            <button
              type="button"
              onClick={handleSync}
              disabled={syncing}
              title={isAdmin ? "Push IOC table to gateway Redis cache" : "Requires platform admin or superuser"}
              className="flex items-center gap-1 rounded-lg border px-3 py-1.5 text-sm disabled:opacity-50 dark:border-slate-600"
            >
              <RefreshCw className={`h-4 w-4 ${syncing ? "animate-spin" : ""}`} />
              Sync to Gateway
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

      {syncStatus && (
        <div
          className={`mb-4 flex flex-wrap items-center justify-between gap-2 rounded-xl border px-4 py-3 text-sm ${
            syncStatus.entry_count > 0
              ? "border-teal-200 bg-teal-50 text-teal-800 dark:border-teal-800 dark:bg-teal-900/30 dark:text-teal-200"
              : "border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-800 dark:bg-amber-900/30 dark:text-amber-200"
          }`}
        >
          <span>
            Gateway sync queued for <strong>{syncStatus.org_slug}</strong> —{" "}
            <strong>{syncStatus.entry_count}</strong> indicators in org DB
            {syncStatus.redis_key && (
              <>
                {" "}
                (<code className="font-mono text-xs">{syncStatus.redis_key}</code>)
              </>
            )}
          </span>
          <span className="text-xs opacity-70">
            {syncStatus.last_sync_at ? new Date(syncStatus.last_sync_at).toLocaleString() : "just now"}
          </span>
        </div>
      )}

      {error && telemetry && (
        <div className="mb-4 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-amber-200 bg-amber-50 px-4 py-2 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200">
          <span>{error}</span>
          <button type="button" onClick={() => loadTelemetry({ silent: true })} className="font-medium underline">
            Retry telemetry
          </button>
        </div>
      )}

      {error && !telemetry && <Module2ErrorState message={error} onRetry={() => loadTelemetry()} />}

      {(telemetry || fleetStats.total > 0) && (
        <ThreatIntelSocPanel
          summary={summary}
          iocLibrary={telemetry?.ioc_library}
          fleetStats={fleetStats}
          topVector={topVector}
          period={period}
        />
      )}

      {!telemetry && !loading && !error && (
        <Module2EmptyState
          title="Telemetry unavailable"
          message="Attack category charts could not be loaded, but you can still manage indicators below."
        />
      )}

      {telemetry && !hasTelemetryActivity && (
        <Module2EmptyState
          title="No attack telemetry in this period"
          message="Charts summarize enforcement events from your gateway — prompt blocks, PII redactions, API-key activity, and IOC matches."
          hint="Use M1.1 Attack Simulator for injection signals. Add an indicator below, Sync to Gateway, then send matching traffic for IOC Matches."
          action={(
            <Link
              to={ATTACK_SIMULATOR_PATH}
              className="inline-flex rounded-lg bg-teal-600 px-4 py-2 text-sm font-semibold text-white hover:bg-teal-700"
            >
              Open M1.1 Attack Simulator
            </Link>
          )}
        />
      )}

      {telemetry && hasTelemetryActivity && (
        <ThreatIntelTelemetryDashboard telemetry={telemetry} period={period} />
      )}

      {/* IOC library — always visible */}
      <div className="mt-6 grid gap-4 xl:grid-cols-3">
        <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-700 dark:bg-slate-800/60 xl:col-span-2">
        <div className="mb-3 flex flex-wrap items-start justify-between gap-2">
          <div>
            <h3 className="text-sm font-semibold text-slate-700 dark:text-slate-200">Threat Indicators (IOC library)</h3>
            <p className="mt-1 max-w-3xl text-xs text-slate-500 dark:text-slate-400">
              <strong>Add Entry</strong> stores a pattern here. <strong>Sync to Gateway</strong> publishes it to Redis.
              Matching traffic shows under <strong>IOC Matches</strong> above — not as new table rows.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {entriesLoading && <Loader2 className="h-4 w-4 animate-spin text-teal-500" />}
            <button
              type="button"
              onClick={() => setShowForm(true)}
              className="flex items-center gap-1 rounded-lg bg-teal-600 px-3 py-1.5 text-sm text-white"
            >
              <Plus className="h-4 w-4" /> Add Entry
            </button>
          </div>
        </div>

        {entries.length === 0 && !entriesLoading && (
          <div className="mb-4 flex items-start gap-2 rounded-lg border border-dashed border-slate-300 bg-slate-50 px-4 py-3 text-xs text-slate-600 dark:border-slate-600 dark:bg-slate-900/30 dark:text-slate-300">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-500" />
            <div>
              <p className="font-medium">No indicators configured yet</p>
              <p className="mt-1">
                Example: threat type <code className="font-mono">jailbreak_probe</code>, indicator{" "}
                <code className="font-mono">ignore previous instructions</code>, enable auto-block, then Sync.
              </p>
            </div>
          </div>
        )}

        {entriesError && (
          <div className="mb-4 rounded-lg border border-red-200 bg-red-50 px-4 py-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950/40 dark:text-red-300">
            {entriesError}
          </div>
        )}

        {showForm && (
          <div className="mb-4 rounded-xl border p-4 dark:border-slate-700">
            <div className="grid gap-3 sm:grid-cols-2">
              <input
                placeholder="Threat Type (e.g. jailbreak_probe)"
                value={form.threat_type}
                onChange={(e) => setForm({ ...form, threat_type: e.target.value })}
                className="rounded-lg border px-3 py-2 text-sm dark:border-slate-600 dark:bg-slate-800"
              />
              <input
                placeholder="OWASP Code (optional, e.g. LLM01)"
                value={form.owasp_code}
                onChange={(e) => setForm({ ...form, owasp_code: e.target.value })}
                className="rounded-lg border px-3 py-2 text-sm dark:border-slate-600 dark:bg-slate-800"
              />
              <input
                placeholder="Indicator (regex or substring fingerprint)"
                value={form.indicator}
                onChange={(e) => setForm({ ...form, indicator: e.target.value })}
                className="sm:col-span-2 rounded-lg border px-3 py-2 font-mono text-sm dark:border-slate-600 dark:bg-slate-800"
              />
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={form.auto_block}
                  onChange={(e) => setForm({ ...form, auto_block: e.target.checked })}
                />
                Auto-block on match (otherwise monitor-only at scanner tier)
              </label>
            </div>
            <div className="mt-3 flex gap-2">
              <button
                type="button"
                onClick={handleCreate}
                disabled={saving}
                className="rounded-lg bg-teal-600 px-4 py-2 text-sm text-white disabled:opacity-50"
              >
                {saving ? "Saving…" : "Save"}
              </button>
              <button type="button" onClick={() => setShowForm(false)} className="rounded-lg border px-4 py-2 text-sm">
                Cancel
              </button>
            </div>
          </div>
        )}

        <DataTable
          columns={[
            {
              key: "threat_type",
              label: "Type",
              helpText: "Threat family label used in enforcement metadata when this IOC matches.",
            },
            {
              key: "indicator",
              label: "Indicator",
              helpText: "Regex (case-insensitive) or substring matched at gateway tier-0 before policy scan.",
              render: (r) => (
                <code className="text-xs" title={r.indicator}>
                  {r.indicator?.length > 60 ? `${r.indicator.slice(0, 60)}…` : r.indicator}
                </code>
              ),
            },
            {
              key: "owasp_code",
              label: "OWASP",
              helpText: "Optional OWASP AI Top 10 mapping for reporting and incident triage.",
            },
            {
              key: "confidence",
              label: "Confidence",
              helpText: "Match confidence (0–1) passed to the gateway scorer on hit.",
              render: (r) => formatConfidencePercent(r.confidence),
            },
            {
              key: "auto_block",
              label: "Auto-Block",
              helpText: "ON = hard block at tier-0. OFF = monitor verdict only (may still block downstream via policy).",
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
            {
              key: "source",
              label: "Source",
              helpText: "manual = analyst entry, feed = imported feed, auto = system-generated.",
            },
            {
              key: "expires_at",
              label: "Expires",
              helpText: "Expired indicators should be removed or renewed — gateway may still cache until next sync.",
              render: (r) => {
                if (!r.expires_at) return "—";
                const exp = new Date(r.expires_at);
                const expired = exp.getTime() < Date.now();
                return (
                  <span className={expired ? "text-red-600" : ""}>
                    {exp.toLocaleDateString()}
                    {expired ? " (expired)" : ""}
                  </span>
                );
              },
            },
            {
              key: "created_at",
              label: "Added",
              helpText: "When this IOC was first recorded in the control plane.",
              render: (r) => (r.created_at ? new Date(r.created_at).toLocaleDateString() : "—"),
            },
            {
              key: "actions",
              label: "",
              helpText: "Delete removes the IOC from the database. Sync to Gateway afterward to clear it from Redis.",
              render: (r) => (
                <button
                  type="button"
                  onClick={() => handleDelete(r)}
                  disabled={deletingId === r.id}
                  className="inline-flex items-center gap-1 rounded px-2 py-1 text-xs text-red-600 hover:bg-red-50 disabled:opacity-50 dark:hover:bg-red-950/30"
                  title="Delete indicator"
                >
                  {deletingId === r.id ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Trash2 className="h-3.5 w-3.5" />
                  )}
                  Delete
                </button>
              ),
            },
          ]}
          rows={entries}
          emptyMessage="No threat intel entries configured."
        />
        </div>

        <IocEntryGuide
          onUseExample={fillExampleIoc}
          iocMatchCount={summary.threat_intel_matches ?? 0}
          entryCount={entries.length}
          syncQueued={!!syncStatus}
        />
      </div>
    </div>
  );
}
