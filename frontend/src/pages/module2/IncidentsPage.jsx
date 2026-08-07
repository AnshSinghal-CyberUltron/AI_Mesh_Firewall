import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Info,
  Radio,
  Search,
  Sparkles,
  X,
} from "lucide-react";
import { Link, useSearchParams } from "react-router-dom";
import { useAuth } from "../../context/AuthContext";
import { clearModule2Cache, createModule2Api, INCIDENT_QUEUE_MUTATED_EVENT } from "../../api/module2";
import { useRealtimeNotifications } from "../../hooks/useRealtimeNotifications";
import { useContainmentPolling } from "../../hooks/useContainmentPolling";
import { TELEMETRY_ACTIVITY_EVENT } from "../../utils/telemetryEvents";
import { formatRiskBandLabel } from "../../utils/riskLabels";
import { PageHeader } from "../../components/module2/PageHeader";
import { Module2RefreshButton } from "../../components/module2/Module2RefreshButton";
import { KPIBar } from "../../components/module2/KPIBar";
import { ChartCard } from "../../components/module2/ChartCard";
import { DataTable } from "../../components/module2/DataTable";
import {
  Module2EmptyState,
  Module2ErrorState,
  Module2PageErrorBoundary,
  Module2PageSkeleton,
} from "../../components/module2/PageStates";
import { ContextualAppBar } from "../../components/module2/ContextualAppBar";
import { PeriodSelector } from "../../components/module2/PeriodSelector";
import { module2TooltipProps } from "../../components/module2/module2Chart";
import {
  applyIncidentListMutation,
  buildIncidentKpiItems,
  formatIncidentAge,
  formatIncidentsBySourceChart,
  INCIDENT_SOURCE_CHART_HELP,
  incidentLaneDrillDown,
  formatLaneDisplayLabel,
  sourceBadgeClass,
} from "./pageData";
import { ANALYST_BRIEF_TITLE, INCIDENTS_GUIDE, PAGE_BRIEFS } from "./pageCopy";
import { stripModuleNumberPrefix } from "../../utils/module2DisplayNames";

const REFRESH_DEBOUNCE_MS = 300;
const PAGE_SIZE = 25;
const DEFAULT_PERIOD = "7d";
const SOURCE_CHIPS = [
  { value: "", label: "All lanes" },
  { value: "chat", label: "Chat" },
  { value: "rag", label: "RAG & retrieval" },
  { value: "mcp", label: "MCP" },
  { value: "threat_intel", label: "Threat Intel" },
  { value: "generic", label: "Generic" },
];

const SEVERITY_CLASS = {
  critical: "bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300",
  high: "bg-orange-100 text-orange-700 dark:bg-orange-900/40 dark:text-orange-300",
  medium: "bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300",
  low: "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400",
};

const STATUS_CLASS = {
  open: "bg-sky-100 text-sky-700 dark:bg-sky-900/40 dark:text-sky-300",
  investigating: "bg-violet-100 text-violet-700 dark:bg-violet-900/40 dark:text-violet-300",
  escalated: "bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300",
  resolved: "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300",
};

const SOURCE_LABELS = {
  chat: "Chat",
  rag: "RAG & retrieval",
  vector: "RAG & retrieval",
  mcp: "MCP",
  threat_intel: "Threat Intel",
  generic: "Generic",
};

function filtersToSearchParams({ status, severity, source, queue, search }) {
  const next = new URLSearchParams();
  if (status) next.set("status", status);
  if (severity) next.set("severity", severity);
  if (source) next.set("source", source);
  if (queue) next.set("queue", queue);
  if (search) next.set("search", search);
  return next;
}

function readIncidentFilters(searchParams) {
  return {
    status: searchParams.get("status") || "",
    queue: searchParams.get("queue") || "",
    severity: searchParams.get("severity") || "",
    source: searchParams.get("source") || "",
    search: searchParams.get("search") || "",
  };
}

function formatStatusLabel(status) {
  if (!status) return "";
  return status
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function buildActiveFilterChips({ statusFilter, severityFilter, sourceFilter, queueFilter, search }) {
  const chips = [];
  if (queueFilter === "active") {
    chips.push({ key: "queue", label: "Active workload" });
  }
  if (statusFilter) {
    chips.push({ key: "status", label: `Status: ${formatStatusLabel(statusFilter)}` });
  }
  if (severityFilter) {
    chips.push({ key: "severity", label: `Severity: ${formatStatusLabel(severityFilter)}` });
  }
  if (sourceFilter) {
    chips.push({ key: "source", label: `Lane: ${SOURCE_LABELS[sourceFilter] || sourceFilter}` });
  }
  if (search) {
    chips.push({ key: "search", label: `Search: “${search}”` });
  }
  return chips;
}

function IncidentsGuideContent() {
  return (
    <div className="space-y-6 px-6 py-5 text-sm leading-relaxed text-slate-600 dark:text-slate-300">
      <section>
        <h3 className="mb-2 flex items-center gap-2 font-semibold text-slate-800 dark:text-slate-100">
          <Info className="h-4 w-4 text-teal-600" />
          Page objective
        </h3>
        <p>{INCIDENTS_GUIDE.objective}</p>
      </section>

      <section className="rounded-xl border border-slate-200 bg-slate-50/80 p-4 dark:border-slate-700 dark:bg-slate-800/40">
        <h3 className="mb-2 font-semibold text-slate-800 dark:text-slate-100">What is a security incident?</h3>
        <p>{INCIDENTS_GUIDE.incidentDefinition}</p>
      </section>

      <section className="grid gap-3 sm:grid-cols-2">
        <div className="rounded-xl border border-amber-200 bg-amber-50/70 p-4 dark:border-amber-800/50 dark:bg-amber-950/20">
          <h3 className="mb-2 flex items-center gap-2 font-semibold text-amber-900 dark:text-amber-100">
            <AlertTriangle className="h-4 w-4" />
            Escalate
          </h3>
          <p className="text-amber-950/90 dark:text-amber-100/90">{INCIDENTS_GUIDE.escalationDefinition}</p>
        </div>
        <div className="rounded-xl border border-emerald-200 bg-emerald-50/70 p-4 dark:border-emerald-800/50 dark:bg-emerald-950/20">
          <h3 className="mb-2 flex items-center gap-2 font-semibold text-emerald-900 dark:text-emerald-100">
            <CheckCircle2 className="h-4 w-4" />
            Resolve
          </h3>
          <p className="text-emerald-950/90 dark:text-emerald-100/90">{INCIDENTS_GUIDE.resolveDefinition}</p>
        </div>
      </section>

      <section>
        <h3 className="mb-2 font-semibold text-slate-800 dark:text-slate-100">What the table shows</h3>
        <p>{INCIDENTS_GUIDE.tableSummary}</p>
      </section>

      <section>
        <h3 className="mb-3 font-semibold text-slate-800 dark:text-slate-100">What filters do</h3>
        <ul className="space-y-2">
          <li>
            <strong className="text-slate-700 dark:text-slate-200">KPI cards —</strong> {INCIDENTS_GUIDE.kpiHelp}
          </li>
          <li>
            <strong className="text-slate-700 dark:text-slate-200">Search —</strong> {INCIDENTS_GUIDE.filterHelp.search}
          </li>
          <li>
            <strong className="text-slate-700 dark:text-slate-200">Status —</strong> {INCIDENTS_GUIDE.filterHelp.status}
          </li>
          <li>
            <strong className="text-slate-700 dark:text-slate-200">Severity —</strong> {INCIDENTS_GUIDE.filterHelp.severity}
          </li>
          <li>
            <strong className="text-slate-700 dark:text-slate-200">Lane chips —</strong> {INCIDENTS_GUIDE.filterHelp.source}
          </li>
        </ul>
      </section>
    </div>
  );
}

function IncidentsGuideModal({ open, onClose }) {
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-[80] flex items-center justify-center bg-black/50 p-4 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-labelledby="incidents-guide-title"
      onClick={onClose}
    >
      <div
        className="max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-2xl border border-slate-200 bg-white shadow-2xl dark:border-slate-600 dark:bg-slate-900"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="sticky top-0 flex items-start justify-between gap-4 border-b border-slate-100 bg-white px-6 py-5 dark:border-slate-700 dark:bg-slate-900">
          <div>
            <p className="text-xs font-semibold uppercase tracking-wide text-teal-600 dark:text-teal-400">
              Incidents &amp; Forensics
            </p>
            <h2 id="incidents-guide-title" className="mt-1 text-lg font-bold text-slate-900 dark:text-slate-100">
              How this page works
            </h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg border border-slate-200 p-2 text-slate-500 hover:bg-slate-50 dark:border-slate-600 dark:hover:bg-slate-800"
            aria-label="Close guide"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <IncidentsGuideContent />
      </div>
    </div>
  );
}

export function IncidentsPage() {
  return (
    <Module2PageErrorBoundary title="Incident and Forensics failed to render">
      <IncidentsPageInner />
    </Module2PageErrorBoundary>
  );
}

function IncidentsPageInner() {
  const { fetchWithAuth } = useAuth();
  const api = useMemo(() => createModule2Api(fetchWithAuth), [fetchWithAuth]);
  const [searchParams, setSearchParams] = useSearchParams();

  const { status: statusFilter, queue: queueFilter, severity: severityFilter, source: sourceFilter, search } =
    readIncidentFilters(searchParams);

  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [searchInput, setSearchInput] = useState(search);
  const [page, setPage] = useState(1);
  const [period, setPeriod] = useState(DEFAULT_PERIOD);
  const [selectedIds, setSelectedIds] = useState(() => new Set());
  const [bulkResolving, setBulkResolving] = useState(false);
  const [guideOpen, setGuideOpen] = useState(false);
  const [guideAttention, setGuideAttention] = useState(true);
  const [rowActionId, setRowActionId] = useState(null);
  const [actionNotice, setActionNotice] = useState(null);

  const loadSeqRef = useRef(0);
  const refreshTimerRef = useRef(null);
  const filterKeyRef = useRef("");
  const tableSectionRef = useRef(null);

  const scrollToTable = useCallback(() => {
    requestAnimationFrame(() => {
      tableSectionRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }, []);

  const replaceFilters = useCallback(
    (patch) => {
      const current = readIncidentFilters(searchParams);
      setSearchParams(filtersToSearchParams({ ...current, ...patch }), { replace: true });
    },
    [searchParams, setSearchParams],
  );

  const load = useCallback(async ({ silent = false, manual = false } = {}) => {
    const seq = ++loadSeqRef.current;
    if (!silent) {
      setLoading(true);
      setError(null);
    }
    try {
      clearModule2Cache();
      const res = await api.listIncidents(
        {
          status: statusFilter,
          queue: queueFilter,
          severity: severityFilter,
          source: sourceFilter,
          search,
          period,
          page,
          page_size: PAGE_SIZE,
        },
        { useCache: false },
      );
      // Manual refresh always wins over a racing background poll started earlier.
      if (!manual && seq !== loadSeqRef.current) return;
      if (manual) loadSeqRef.current = Math.max(loadSeqRef.current, seq);
      setData(res);
      setSelectedIds(new Set());
      setError(null);
    } catch (e) {
      if (!manual && seq !== loadSeqRef.current) return;
      setError(e.message || "Failed to load incidents.");
      if (!silent) setData(null);
    } finally {
      if ((manual || seq === loadSeqRef.current) && !silent) setLoading(false);
    }
  }, [api, statusFilter, queueFilter, severityFilter, sourceFilter, search, period, page]);

  const refreshLive = useCallback(() => {
    clearTimeout(refreshTimerRef.current);
    refreshTimerRef.current = setTimeout(() => {
      load({ silent: true });
    }, REFRESH_DEBOUNCE_MS);
  }, [load]);

  useEffect(() => () => clearTimeout(refreshTimerRef.current), []);

  useEffect(() => {
    if (!guideAttention) return undefined;
    const timer = window.setTimeout(() => setGuideAttention(false), 12000);
    return () => window.clearTimeout(timer);
  }, [guideAttention]);

  const { connected: wsConnected } = useRealtimeNotifications({
    onEnforcementEvent: refreshLive,
    onEscalationEvent: refreshLive,
    onResolutionEvent: refreshLive,
  });
  useContainmentPolling(refreshLive, { enabled: !!data });

  useEffect(() => {
    const onMutated = () => refreshLive();
    window.addEventListener(INCIDENT_QUEUE_MUTATED_EVENT, onMutated);
    return () => window.removeEventListener(INCIDENT_QUEUE_MUTATED_EVENT, onMutated);
  }, [refreshLive]);

  useEffect(() => {
    const onTelemetry = () => refreshLive();
    window.addEventListener(TELEMETRY_ACTIVITY_EVENT, onTelemetry);
    return () => window.removeEventListener(TELEMETRY_ACTIVITY_EVENT, onTelemetry);
  }, [refreshLive]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    setSearchInput(search);
  }, [search]);

  useEffect(() => {
    const filterKey = `${statusFilter}|${queueFilter}|${severityFilter}|${sourceFilter}|${search}|${period}`;
    if (filterKeyRef.current && filterKeyRef.current !== filterKey) {
      setPage(1);
    }
    filterKeyRef.current = filterKey;
  }, [statusFilter, queueFilter, severityFilter, sourceFilter, search, period]);

  const handleStatusFilter = useCallback(
    (value) => {
      const current = readIncidentFilters(searchParams);
      // Status KPI/chip clears queue + severity so cards don't stack into
      // e.g. status=resolved&severity=critical_high (Critical KPI stays 0).
      replaceFilters({
        status: value,
        queue: value ? "" : current.queue,
        severity: value ? "" : current.severity,
      });
      if (value) scrollToTable();
    },
    [searchParams, replaceFilters, scrollToTable],
  );

  const handleQueueFilter = useCallback(
    (value) => {
      const current = readIncidentFilters(searchParams);
      replaceFilters({
        queue: value,
        status: value ? "" : current.status,
        severity: value ? "" : current.severity,
      });
      if (value) scrollToTable();
    },
    [searchParams, replaceFilters, scrollToTable],
  );

  const handleSeverityFilter = useCallback(
    (value) => {
      if (value === "critical_high") {
        // Match Critical/High KPI (active open work only) — don't stack on Resolved.
        replaceFilters({ severity: "critical_high", status: "", queue: "active" });
      } else if (value) {
        replaceFilters({ severity: value });
      } else {
        replaceFilters({ severity: "", queue: "" });
      }
      if (value) scrollToTable();
    },
    [replaceFilters, scrollToTable],
  );

  const handleSourceFilter = useCallback(
    (value) => {
      replaceFilters({ source: value });
    },
    [replaceFilters],
  );

  const clearAllFilters = useCallback(() => {
    setSearchInput("");
    setSearchParams(new URLSearchParams(), { replace: true });
  }, [setSearchParams]);

  const handleRowAction = useCallback(
    async (row, action) => {
      const incidentId = row.id;
      const previousStatus = row.status;
      if (action === "resolve") {
        const confirmed = window.confirm(
          `Resolve incident #${incidentId}? This closes the case and moves it to resolved history.`,
        );
        if (!confirmed) return;
      }
      setRowActionId(incidentId);
      setActionNotice(null);
      setData((prev) =>
        applyIncidentListMutation(
          prev,
          { incidentId, action, previousStatus, severity: row.severity },
          { status: statusFilter, queue: queueFilter, severity: severityFilter },
        ),
      );
      try {
        if (action === "escalate") {
          await api.escalateIncident(incidentId);
          setActionNotice({ type: "success", text: `Incident #${incidentId} escalated.` });
        } else {
          await api.resolveIncident(incidentId);
          setActionNotice({ type: "success", text: `Incident #${incidentId} resolved.` });
        }
        await load({ silent: true });
      } catch (e) {
        setActionNotice({ type: "error", text: e.message || "Action failed." });
        await load({ silent: true });
      } finally {
        setRowActionId(null);
      }
    },
    [api, load, statusFilter, queueFilter, severityFilter],
  );

  const handleSearchSubmit = (e) => {
    e.preventDefault();
    replaceFilters({ search: searchInput.trim() });
  };

  const summary = data?.summary || {};
  const incidents = data?.results || [];
  const totalPages = data?.total_pages || 1;

  const toggleRowSelected = useCallback((id) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const selectableOnPage = useMemo(
    () => incidents.filter((row) => row.status !== "resolved"),
    [incidents],
  );

  const toggleSelectAllOnPage = useCallback(() => {
    setSelectedIds((prev) => {
      const pageIds = selectableOnPage.map((row) => row.id);
      const allSelected = pageIds.length > 0 && pageIds.every((id) => prev.has(id));
      if (allSelected) return new Set();
      return new Set(pageIds);
    });
  }, [selectableOnPage]);

  const handleBulkResolve = useCallback(async () => {
    const ids = [...selectedIds];
    if (!ids.length) return;
    const confirmed = window.confirm(
      `Resolve ${ids.length} selected incident${ids.length === 1 ? "" : "s"}? `
      + "This closes the cases and moves them to resolved history.",
    );
    if (!confirmed) return;
    setBulkResolving(true);
    setActionNotice(null);
    try {
      const result = await api.bulkResolveIncidents(ids);
      setActionNotice({
        type: "success",
        text: `Resolved ${result.resolved_count ?? ids.length} incident(s).`,
      });
      setSelectedIds(new Set());
      await load({ silent: true });
    } catch (e) {
      setActionNotice({ type: "error", text: e.message || "Bulk resolve failed." });
      await load({ silent: true });
    } finally {
      setBulkResolving(false);
    }
  }, [api, load, selectedIds]);

  const hasSummary = data?.summary != null && typeof data.summary.total === "number";
  const filteredCount = data?.count ?? 0;
  const hasFilters = Boolean(statusFilter || queueFilter || severityFilter || sourceFilter || search);
  const orgTotal = hasSummary ? summary.total : !hasFilters ? filteredCount : null;
  const effectiveSummary = hasSummary
    ? summary
    : {
        total: hasFilters ? 0 : filteredCount,
        active: 0,
        open: 0,
        investigating: 0,
        escalated: 0,
        resolved: 0,
        critical_high: 0,
        by_source: {},
      };
  const sourceChart = formatIncidentsBySourceChart(effectiveSummary.by_source);
  const filterChips = buildActiveFilterChips({
    statusFilter,
    severityFilter,
    sourceFilter,
    queueFilter,
    search,
  });
  const kpiItems = buildIncidentKpiItems(
    effectiveSummary,
    {
      statusFilter,
      severityFilter,
      queueFilter,
      onStatusFilter: handleStatusFilter,
      onSeverityFilter: handleSeverityFilter,
      onQueueFilter: handleQueueFilter,
    },
    data?.data_provenance,
  );

  if (loading && !data) {
    return (
      <div>
        <ContextualAppBar title={ANALYST_BRIEF_TITLE} description={PAGE_BRIEFS.incidents} />
        {error ? <Module2ErrorState message={error} onRetry={() => load()} /> : <Module2PageSkeleton rows={8} />}
      </div>
    );
  }

  if (error && !data) {
    return (
      <div>
        <ContextualAppBar title={ANALYST_BRIEF_TITLE} description={PAGE_BRIEFS.incidents} />
        <Module2ErrorState message={error} onRetry={() => load()} />
      </div>
    );
  }

  return (
    <div>
      <IncidentsGuideModal open={guideOpen} onClose={() => setGuideOpen(false)} />
      <ContextualAppBar title={ANALYST_BRIEF_TITLE} description={PAGE_BRIEFS.incidents} />
      <PageHeader
        title="Incident and Forensics"
        subtitle="Triage, escalate, and resolve formal security cases raised by alert rules and anomaly detection"
        actions={
          <>
            <PeriodSelector value={period} onChange={setPeriod} />
            <span
              className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ${
                wsConnected
                  ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300"
                  : "bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400"
              }`}
              title={wsConnected ? "Live enforcement feed connected" : "Refreshes when cases are created or updated"}
            >
              <Radio className={`h-3 w-3 ${wsConnected ? "text-emerald-500" : ""}`} />
              {wsConnected ? "Live" : "On activity"}
            </span>
            <Module2RefreshButton
              label="Refresh"
              onRefresh={async () => {
                clearTimeout(refreshTimerRef.current);
                await load({ silent: true, manual: true });
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

      {actionNotice && (
        <div
          className={`mb-4 rounded-lg border px-4 py-2 text-sm ${
            actionNotice.type === "error"
              ? "border-red-200 bg-red-50 text-red-800 dark:border-red-800 dark:bg-red-950/40 dark:text-red-200"
              : "border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-800 dark:bg-emerald-950/40 dark:text-emerald-200"
          }`}
        >
          {actionNotice.text}
        </div>
      )}

      {import.meta.env.DEV && !hasSummary && orgTotal != null && orgTotal > 0 && (
        <div className="mb-4 rounded-lg border border-sky-200 bg-sky-50 px-4 py-2 text-sm text-sky-900 dark:border-sky-800 dark:bg-sky-950/30 dark:text-sky-100">
          Showing <strong>{orgTotal}</strong> incidents from the API. For full KPI breakdown and lane chart metrics, rebuild and restart the{" "}
          <strong>control</strong> Docker service so the latest Incidents API is active.
        </div>
      )}

      <KPIBar items={kpiItems} />
      <div className="mt-3 flex justify-start">
        <button
          type="button"
          onClick={() => {
            setGuideAttention(false);
            setGuideOpen(true);
          }}
          aria-label="Open analyst guide"
          className="group relative inline-flex items-center gap-2.5 overflow-hidden rounded-xl border border-cyan-300/70 bg-gradient-to-r from-cyan-600 via-teal-500 to-emerald-500 bg-[length:200%_100%] px-5 py-2.5 text-sm font-bold text-white shadow-lg shadow-cyan-700/30 transition-all duration-300 hover:-translate-y-0.5 hover:from-cyan-500 hover:via-teal-400 hover:to-emerald-400 hover:shadow-xl hover:shadow-cyan-600/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400/80 focus-visible:ring-offset-2 motion-safe:animate-[gradient-pan_4s_ease-in-out_infinite] dark:focus-visible:ring-offset-slate-900"
        >
          <span className="pointer-events-none absolute inset-0 -translate-x-full bg-gradient-to-r from-transparent via-white/40 to-transparent motion-safe:animate-[guide-shine_2.6s_ease-in-out_infinite]" />
          {guideAttention && (
            <span className="absolute inset-0 rounded-xl ring-2 ring-cyan-300/70 ring-offset-1 ring-offset-transparent motion-safe:animate-ping motion-reduce:animate-none" />
          )}
          <Sparkles className="relative h-4 w-4 motion-safe:animate-pulse motion-reduce:animate-none" />
          <span className="relative tracking-wide">Open analyst guide</span>
          <span className="relative rounded-full bg-white/25 px-2 py-0.5 text-[10px] font-extrabold uppercase tracking-widest shadow-sm">
            New
          </span>
          <ChevronRight className="relative h-4 w-4 transition-transform duration-300 group-hover:translate-x-1" />
        </button>
      </div>

      {sourceChart.length > 0 && (
        <div className="mt-4 space-y-2">
          <ChartCard
            title="Incidents by Enforcement Lane"
            titleHelpText={INCIDENT_SOURCE_CHART_HELP}
          >
            <ResponsiveContainer width="100%" height={220}>
              <BarChart data={sourceChart} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" className="stroke-slate-200 dark:stroke-slate-700" />
                <XAxis dataKey="label" tick={{ fontSize: 11 }} />
                <YAxis allowDecimals={false} tick={{ fontSize: 11 }} />
                <Tooltip {...module2TooltipProps} />
                <Bar dataKey="count" fill="#0d9488" radius={[4, 4, 0, 0]} name="Incidents" />
              </BarChart>
            </ResponsiveContainer>
          </ChartCard>
          <p
            className="rounded-lg border border-slate-200 bg-slate-50/80 px-3 py-2 text-[11px] leading-relaxed text-slate-600 dark:border-slate-700 dark:bg-slate-900/30 dark:text-slate-300"
            data-testid="incidents-rag-lane-legend"
          >
            <span className="font-semibold text-slate-800 dark:text-slate-100">RAG &amp; retrieval</span>{" "}
            counts cases from knowledge-base pipeline events and standalone document-library lookups.
            That is not the same number as Model &amp; RAG Health → Pipeline Stage Events. Health shows
            early access denials under <span className="font-medium">Policy / Access Denials</span> and
            library risk under collections charts.
          </p>
        </div>
      )}

      <div
        ref={tableSectionRef}
        id="incident-cases-table"
        className="mt-6 scroll-mt-24 rounded-xl border border-slate-200 bg-white shadow-sm dark:border-slate-700 dark:bg-slate-800/60"
      >
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 px-4 py-3 dark:border-slate-700">
          <div className="flex items-center gap-2">
            <h2 className="text-sm font-semibold text-slate-800 dark:text-slate-100">Incident cases</h2>
          </div>
          {hasFilters && (
            <button
              type="button"
              onClick={clearAllFilters}
              className="text-xs font-medium text-teal-600 hover:underline dark:text-teal-400"
            >
              Clear all filters
            </button>
          )}
        </div>

        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 px-4 py-3 dark:border-slate-700">
          <div className="flex flex-wrap items-center gap-2">
            {selectedIds.size > 0 && (
              <button
                type="button"
                disabled={bulkResolving}
                onClick={handleBulkResolve}
                className="inline-flex items-center gap-1.5 rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-emerald-700 disabled:opacity-60"
              >
                <CheckCircle2 className="h-3.5 w-3.5" />
                {bulkResolving ? "Resolving…" : `Resolve selected (${selectedIds.size})`}
              </button>
            )}
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-3 border-b border-slate-100 px-4 py-3 dark:border-slate-700">
          <form onSubmit={handleSearchSubmit} className="flex min-w-[200px] flex-1 items-center gap-2">
            <Search className="h-4 w-4 shrink-0 text-slate-400" />
            <input
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              placeholder="Search title or notes..."
              className="w-full rounded-lg border px-3 py-1.5 text-sm dark:border-slate-600 dark:bg-slate-900"
            />
          </form>
          <select
            value={statusFilter}
            onChange={(e) => handleStatusFilter(e.target.value)}
            className="rounded-lg border px-3 py-1.5 text-sm dark:border-slate-600 dark:bg-slate-800"
            title={INCIDENTS_GUIDE.filterHelp.status}
          >
            <option value="">All statuses</option>
            <option value="open">Open</option>
            <option value="investigating">Investigating</option>
            <option value="escalated">Escalated</option>
            <option value="resolved">Resolved</option>
          </select>
          <select
            value={severityFilter}
            onChange={(e) => handleSeverityFilter(e.target.value)}
            className="rounded-lg border px-3 py-1.5 text-sm dark:border-slate-600 dark:bg-slate-800"
            title={INCIDENTS_GUIDE.filterHelp.severity}
          >
            <option value="">All severities</option>
            <option value="critical_high">Critical + High</option>
            <option value="critical">Critical</option>
            <option value="high">High</option>
            <option value="medium">Medium</option>
            <option value="low">Low</option>
          </select>
        </div>

        <div className="flex flex-wrap items-center gap-2 border-b border-slate-100 px-4 py-3 dark:border-slate-700">
          <span className="text-xs font-medium text-slate-500 dark:text-slate-400">Enforcement lane</span>
          {SOURCE_CHIPS.map((chip) => (
            <button
              key={chip.value || "all"}
              type="button"
              onClick={() => handleSourceFilter(chip.value)}
              className={`rounded-full px-3 py-1 text-xs font-medium transition ${
                sourceFilter === chip.value
                  ? "bg-teal-600 text-white shadow-sm"
                  : "border border-slate-200 bg-white text-slate-600 hover:border-teal-300 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-300"
              }`}
            >
              {chip.label}
            </button>
          ))}
        </div>

        <div className="border-b border-slate-100 bg-slate-50/60 px-4 py-2 text-[11px] text-slate-600 dark:border-slate-700 dark:bg-slate-900/20 dark:text-slate-300">
          Actions: <span className="font-medium">Escalate</span> = senior review, <span className="font-medium">Resolve</span> = close case, <span className="font-medium">View</span> = full forensics.
        </div>

        {filterChips.length > 0 && (
          <div className="flex flex-wrap items-center gap-2 border-b border-slate-100 bg-slate-50/80 px-4 py-2.5 dark:border-slate-700 dark:bg-slate-900/30">
            <span className="text-xs font-medium text-slate-500 dark:text-slate-400">Table showing</span>
            {filterChips.map((chip) => (
              <span
                key={chip.key}
                className="inline-flex items-center rounded-full bg-teal-100 px-2.5 py-0.5 text-xs font-medium text-teal-800 dark:bg-teal-900/40 dark:text-teal-200"
              >
                {chip.label}
              </span>
            ))}
          </div>
        )}

        {(orgTotal === 0 || orgTotal === null) && incidents.length === 0 && !hasFilters ? (
          <div className="p-4">
            <Module2EmptyState
              title="No security incidents yet"
              message="Your org queue is empty. Incidents are opened when alert rules or anomaly jobs match enforcement patterns."
              hint="A gateway block alone does not create a row — a matching alert rule must promote the event."
              action={(
                <Link
                  to="/threat-intel"
                  className="inline-flex items-center rounded-lg bg-teal-600 px-4 py-2 text-sm font-semibold text-white hover:bg-teal-700"
                >
                  Review Threat Intel
                </Link>
              )}
            />
          </div>
        ) : (
          <div className="p-4">
            {incidents.length === 0 && hasFilters && (
              <p className="mb-3 text-sm text-slate-500">No incidents match the current filters.</p>
            )}
            <DataTable
              columns={[
                {
                  key: "select",
                  label: (
                    <input
                      type="checkbox"
                      aria-label="Select all resolvable incidents on this page"
                      checked={
                        selectableOnPage.length > 0
                        && selectableOnPage.every((row) => selectedIds.has(row.id))
                      }
                      onChange={toggleSelectAllOnPage}
                      className="rounded border-slate-300"
                    />
                  ),
                  render: (r) => (
                    r.status === "resolved" ? (
                      <span className="inline-block w-4" />
                    ) : (
                      <input
                        type="checkbox"
                        aria-label={`Select incident ${r.id}`}
                        checked={selectedIds.has(r.id)}
                        onChange={() => toggleRowSelected(r.id)}
                        className="rounded border-slate-300"
                      />
                    )
                  ),
                },
                {
                  key: "id",
                  label: "Case",
                  helpText: "Unique incident ID — open for full timeline and evidence.",
                  render: (r) => (
                    <Link to={`/incidents/${r.id}`} className="font-medium text-teal-600 hover:underline">
                      #{r.id}
                    </Link>
                  ),
                },
                { key: "title", label: "Title", helpText: "Short description from the alert rule or anomaly job.", render: (r) => stripModuleNumberPrefix(r.title) || r.title || "—" },
                {
                  key: "source",
                  label: "Lane",
                  helpText: "Which enforcement path raised this case (chat, RAG, MCP, UEBA, threat intel, etc.).",
                  render: (r) => {
                    const drill = incidentLaneDrillDown(r.source);
                    return (
                      <div className="flex flex-col gap-1">
                        <span className={`inline-flex w-fit rounded px-2 py-0.5 text-xs font-medium ${sourceBadgeClass(r.source)}`}>
                          {formatLaneDisplayLabel(r.source)}
                        </span>
                        {drill && (
                          <Link to={drill.to} className="text-xs text-teal-600 hover:underline">
                            {drill.label} →
                          </Link>
                        )}
                      </div>
                    );
                  },
                },
                {
                  key: "severity",
                  label: "Severity",
                  helpText: "Business impact tier assigned when the case was opened.",
                  render: (r) => (
                    <span className={`rounded px-2 py-0.5 text-xs font-medium ${SEVERITY_CLASS[r.severity] || SEVERITY_CLASS.low}`}>
                      {formatRiskBandLabel("severity", r.severity)}
                    </span>
                  ),
                },
                {
                  key: "status",
                  label: "Status",
                  helpText: "Workflow state: Open → Escalated or Resolved.",
                  render: (r) => (
                    <span className={`rounded px-2 py-0.5 text-xs font-medium capitalize ${STATUS_CLASS[r.status] || STATUS_CLASS.open}`}>
                      {r.status}
                    </span>
                  ),
                },
                {
                  key: "threat_type",
                  label: "Threat",
                  helpText: "Threat category from the triggering enforcement event.",
                  render: (r) => r.threat_type || "—",
                },
                {
                  key: "model",
                  label: "Model",
                  render: (r) => r.model || "—",
                },
                {
                  key: "key_prefix",
                  label: "API key",
                  helpText: "Truncated key prefix for identity correlation.",
                  render: (r) => r.key_prefix || "—",
                },
                {
                  key: "age",
                  label: "Age",
                  helpText: "How long the case has been open.",
                  render: (r) => formatIncidentAge(r.created_at),
                },
                {
                  key: "actions",
                  label: "Actions",
                  helpText: "Escalate for senior review, Resolve when investigation is complete, or open the case for forensics.",
                  render: (r) => {
                    const busy = rowActionId === r.id;
                    const canEscalate = r.status !== "escalated" && r.status !== "resolved";
                    const canResolve = r.status !== "resolved";
                    return (
                      <div className="flex min-w-[9rem] flex-wrap gap-1">
                        {canEscalate && (
                          <button
                            type="button"
                            disabled={busy}
                            onClick={() => handleRowAction(r, "escalate")}
                            className="rounded-md border border-amber-300 bg-amber-50 px-2 py-1 text-xs font-medium text-amber-800 hover:bg-amber-100 disabled:opacity-50 dark:border-amber-700 dark:bg-amber-950/30 dark:text-amber-200"
                            title="Mark for senior review or IR handoff"
                          >
                            {busy ? "…" : "Escalate"}
                          </button>
                        )}
                        {canResolve && (
                          <button
                            type="button"
                            disabled={busy}
                            onClick={() => handleRowAction(r, "resolve")}
                            className="rounded-md border border-emerald-300 bg-emerald-50 px-2 py-1 text-xs font-medium text-emerald-800 hover:bg-emerald-100 disabled:opacity-50 dark:border-emerald-700 dark:bg-emerald-950/30 dark:text-emerald-200"
                            title="Close the case when investigation is complete"
                          >
                            {busy ? "…" : "Resolve"}
                          </button>
                        )}
                        <Link
                          to={`/incidents/${r.id}`}
                          className="rounded-md border border-slate-200 px-2 py-1 text-xs font-medium text-slate-600 hover:border-teal-300 hover:text-teal-700 dark:border-slate-600 dark:text-slate-300"
                        >
                          View
                        </Link>
                      </div>
                    );
                  },
                },
              ]}
              rows={incidents}
              emptyMessage="No incidents match the current filters."
            />

            <div className="mt-4 flex items-center justify-between border-t border-slate-100 pt-4 text-sm dark:border-slate-700">
              <p className="text-slate-500">
                {incidents.length} row{incidents.length !== 1 ? "s" : ""} on this page
                {" · "}
                {data?.count ?? 0} matching filters
                {orgTotal != null && orgTotal !== (data?.count ?? 0) ? ` · ${orgTotal} total in org` : ""}
              </p>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  disabled={page <= 1 || loading}
                  onClick={() => setPage((p) => Math.max(1, p - 1))}
                  className="rounded-lg border px-2 py-1 disabled:opacity-40 dark:border-slate-600"
                >
                  <ChevronLeft className="h-4 w-4" />
                </button>
                <span>
                  Page {page} / {totalPages}
                </span>
                <button
                  type="button"
                  disabled={page >= totalPages || loading}
                  onClick={() => setPage((p) => p + 1)}
                  className="rounded-lg border px-2 py-1 disabled:opacity-40 dark:border-slate-600"
                >
                  <ChevronRight className="h-4 w-4" />
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
