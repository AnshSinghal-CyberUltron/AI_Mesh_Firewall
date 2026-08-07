import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  Bar, BarChart, CartesianGrid, Cell, Legend, Pie, PieChart,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import {
  BookOpen, CircleHelp, Cpu, Database, Filter, Info, Search, Sparkles, X,
} from "lucide-react";
import { useAuth } from "../../context/AuthContext";
import { clearModule2Cache, createModule2Api } from "../../api/module2";
import { useRealtimeNotifications } from "../../hooks/useRealtimeNotifications";
import { useContainmentPolling } from "../../hooks/useContainmentPolling";
import { TELEMETRY_ACTIVITY_EVENT } from "../../utils/telemetryEvents";
import { PageHeader } from "../../components/module2/PageHeader";
import { Module2RefreshButton } from "../../components/module2/Module2RefreshButton";
import { KPIBar } from "../../components/module2/KPIBar";
import { module2TooltipPanelClass, module2TooltipProps } from "../../components/module2/module2Chart";
import { ChartCard } from "../../components/module2/ChartCard";
import { DataTable } from "../../components/module2/DataTable";
import { PeriodSelector } from "../../components/module2/PeriodSelector";
import { RiskBandBadge } from "../../components/module2/RiskBandBadge";
import { Module2EmptyState, Module2ErrorState, Module2PageErrorBoundary, Module2PageSkeleton } from "../../components/module2/PageStates";
import { ContextualAppBar } from "../../components/module2/ContextualAppBar";
import {
  buildExposureKpis,
  buildRagKpis,
  buildRagModule2Extras,
  formatExposureChartData,
  formatRagCollectionChartData,
  formatRagDenialTypeRows,
  formatRagDocumentFunnel,
  formatRagEscalationChartData,
  formatRagStageChartData,
} from "./pageData";
import { ANALYST_BRIEF_TITLE, PAGE_BRIEFS } from "./pageCopy";

const PERIOD_LABELS = { "1h": "1 hour", "24h": "24 hours", "7d": "7 days", "30d": "30 days" };
const REFRESH_DEBOUNCE_MS = 300;
const RAG_STAGE_EMPTY_MSG =
  "No RAG search-step activity in this window yet (ask → find docs → rank → answer).";
const RAG_DENIAL_EMPTY_MSG =
  "No early access denials before search started in this window.";

const TABS = [
  {
    id: "model",
    label: "Model Exposure",
    description: "LLM attack surface & block posture",
    icon: Cpu,
  },
  {
    id: "rag",
    label: "RAG & Retrieval",
    description: "Pipeline stages & vector collections",
    icon: BookOpen,
  },
];

const RAG_STAGE_GUIDE = [
  {
    icon: Search,
    title: "Query",
    text: "User question scanned before retrieval — blocks injection or policy violations in the prompt.",
  },
  {
    icon: Database,
    title: "Retriever",
    text: "Vector DB lookup — enforces collection ACLs, cross-tenant access, and poisoned-chunk detection.",
  },
  {
    icon: Filter,
    title: "Ranker",
    text: "Re-ranking step — flags or blocks chunks that fail relevance or sensitivity checks.",
  },
  {
    icon: Sparkles,
    title: "Generator",
    text: "Final LLM answer — output guardrails and grounding checks run here.",
  },
];

function RagChartEmpty({ message }) {
  return <p className="py-12 text-center text-sm text-slate-400">{message}</p>;
}

function TabBar({ active, onChange }) {
  return (
    <div
      className="mb-6 flex flex-wrap gap-3"
      role="tablist"
      aria-label="Model and RAG views"
    >
      {TABS.map((t) => {
        const Icon = t.icon;
        const isActive = active === t.id;
        return (
          <button
            key={t.id}
            type="button"
            role="tab"
            aria-selected={isActive}
            onClick={() => onChange(t.id)}
            className={[
              "flex min-w-[200px] flex-1 flex-col items-start rounded-xl border-2 px-5 py-3 text-left transition-all sm:max-w-xs",
              isActive
                ? "border-teal-500 bg-teal-600 text-white shadow-lg shadow-teal-600/25"
                : "border-slate-200 bg-white text-slate-700 hover:border-teal-300 hover:bg-teal-50/50 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-200 dark:hover:border-teal-700 dark:hover:bg-slate-800/80",
            ].join(" ")}
          >
            <span className="flex items-center gap-2 text-sm font-bold">
              <Icon className={`h-4 w-4 ${isActive ? "text-teal-100" : "text-teal-600 dark:text-teal-400"}`} />
              {t.label}
            </span>
            <span className={`mt-0.5 text-xs ${isActive ? "text-teal-100/90" : "text-slate-500 dark:text-slate-400"}`}>
              {t.description}
            </span>
          </button>
        );
      })}
    </div>
  );
}

function ModelRagGuideModal({ open, onClose }) {
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
      aria-labelledby="model-rag-guide-title"
      onClick={onClose}
    >
      <div
        className="max-h-[90vh] w-full max-w-3xl overflow-y-auto rounded-2xl border border-slate-200 bg-white shadow-2xl dark:border-slate-600 dark:bg-slate-900"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="sticky top-0 flex items-start justify-between gap-4 border-b border-slate-100 bg-white px-6 py-5 dark:border-slate-700 dark:bg-slate-900">
          <div>
            <p className="text-xs font-semibold uppercase tracking-wide text-teal-600 dark:text-teal-400">
              Model &amp; RAG Health Guide
            </p>
            <h2 id="model-rag-guide-title" className="mt-1 text-lg font-bold text-slate-900 dark:text-slate-100">
              What this page shows
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

        <div className="space-y-6 px-6 py-5 text-sm leading-relaxed text-slate-600 dark:text-slate-300">
          <section>
            <h3 className="mb-2 flex items-center gap-2 font-semibold text-slate-800 dark:text-slate-100">
              <Info className="h-4 w-4 text-teal-600" />
              Page objective
            </h3>
            <p>{PAGE_BRIEFS.modelRag}</p>
          </section>

          <section className="rounded-xl border border-slate-200 bg-slate-50/80 p-4 dark:border-slate-700 dark:bg-slate-800/40">
            <h3 className="mb-2 font-semibold text-slate-800 dark:text-slate-100">Model Exposure tab</h3>
            <p>
              Shows per-model risk posture from real enforcement telemetry (requests, block %, redact %, latency,
              exposure score). Use it to identify which models create the most policy friction.
            </p>
          </section>

          <section className="rounded-xl border border-violet-200 bg-violet-50/70 p-4 dark:border-violet-800/50 dark:bg-violet-950/20">
            <h3 className="mb-2 font-semibold text-violet-900 dark:text-violet-200">RAG & Retrieval tab</h3>
            <p className="mb-3 text-violet-900/90 dark:text-violet-100/90">
              Shows where retrieval pipeline enforcement happens (query, retriever, ranker, generator), plus vector
              collection risk. A high stage block rate with low volume means strict filtering on that stage, not a full outage.
            </p>
            <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
              {RAG_STAGE_GUIDE.map(({ icon: Icon, title, text }) => (
                <div
                  key={title}
                  className="rounded-lg border border-violet-200/80 bg-white/80 p-3 dark:border-violet-800/40 dark:bg-slate-900/40"
                >
                  <p className="mb-1 flex items-center gap-1.5 text-xs font-bold text-violet-700 dark:text-violet-300">
                    <Icon className="h-3.5 w-3.5" />
                    {title}
                  </p>
                  <p className="text-[11px] leading-relaxed text-slate-600 dark:text-slate-400">{text}</p>
                </div>
              ))}
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}

function RagStageTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  const row = payload[0]?.payload;
  if (!row) return null;
  return (
    <div className={module2TooltipPanelClass}>
      <p className="font-semibold text-slate-100">{label}</p>
      <p className="text-slate-300">Total checks: {row.total}</p>
      <p className="text-red-400">Blocked: {row.blocked} ({row.block_rate}%)</p>
      <p className="text-emerald-400">Allowed: {row.allowed}</p>
      {row.flagged > 0 && <p className="text-amber-400">Flagged: {row.flagged}</p>}
      {row.avg_latency_ms > 0 && (
        <p className="text-slate-400">Avg latency: {row.avg_latency_ms}ms</p>
      )}
    </div>
  );
}

function RagHealthTab({ ragData, loading, error, onRetry }) {
  if (loading && !ragData) return <Module2PageSkeleton />;
  if (error && !ragData) return <Module2ErrorState message={error} onRetry={onRetry} />;
  if (!ragData) return null;

  const kpis = ragData.rag_pipeline_kpis || {};
  const alignedStages =
    ragData.module1_aligned?.stages || kpis.module1_aligned?.stages || kpis.stages || {};
  const extras = buildRagModule2Extras(ragData);
  const denials = extras.denials;
  const denialTotal = denials.total;
  const denialTypeRows = formatRagDenialTypeRows({
    by_event_type: denials.byEventType,
  });
  const stageData = formatRagStageChartData(alignedStages);
  const funnelSteps = formatRagDocumentFunnel(
    kpis.document_funnel || {
      retrieved: alignedStages.retriever?.total || 0,
      post_ranker: (alignedStages.ranker?.total || 0) - (alignedStages.ranker?.blocked || 0),
      post_generator:
        (alignedStages.generator?.total || 0) - (alignedStages.generator?.blocked || 0),
    },
  );
  const collections = ragData.vector_exposure?.collections || [];
  const collectionChart = formatRagCollectionChartData(collections);
  const escalationData = formatRagEscalationChartData(kpis.escalation_distribution);
  const totalPipelineEvents = stageData.reduce((s, r) => s + r.total, 0);
  const hasStageVolume = totalPipelineEvents > 0;
  const hasFunnelData = funnelSteps.some((step) => step.value > 0);
  const hasLatency = stageData.some((s) => s.avg_latency_ms > 0);
  const latencyData = stageData.filter((s) => s.avg_latency_ms > 0);
  const hasAnyRagSignal =
    totalPipelineEvents > 0 || collections.length > 0 || extras.hasExtras;

  if (!hasAnyRagSignal) {
    return (
      <>
        <Module2EmptyState
          title="No RAG activity in this period"
          message="No RAG searches or early access denials were recorded in this window."
          hint="Charts appear after knowledge-base searches run through the gateway. Early access denials show in the card on the left when present."
        />
      </>
    );
  }

  return (
    <>
      <KPIBar items={buildRagKpis(kpis, ragData.vector_exposure)} />

      <div className="mt-6 grid gap-4 lg:grid-cols-2">
        <ChartCard
          title="Policy / Access Denials"
          titleHelpText="Requests stopped before a RAG search even started (for example, no permission to use a document library). These are not counted in Pipeline Stage Events above."
        >
          {denialTotal > 0 ? (
            <div className="space-y-3" data-testid="rag-pre-pipeline-denials-card">
              <div className="flex items-end justify-between gap-3">
                <div>
                  <p className="text-3xl font-semibold tabular-nums text-red-600 dark:text-red-400">
                    {denialTotal}
                  </p>
                  <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                    Pre-pipeline policy / access denials
                  </p>
                </div>
              </div>
              {denialTypeRows.length > 0 && (
                <ul className="divide-y divide-slate-100 rounded-lg border border-slate-100 dark:divide-slate-700 dark:border-slate-700">
                  {denialTypeRows.map((row) => (
                    <li
                      key={row.eventType}
                      className="flex items-center justify-between gap-3 px-3 py-2 text-xs"
                    >
                      <span className="font-mono text-[11px] text-slate-700 dark:text-slate-200">
                        {row.eventType}
                      </span>
                      <span className="tabular-nums font-semibold text-slate-900 dark:text-slate-50">
                        {row.count}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
              {denials.byStage && Object.keys(denials.byStage).length > 0 && (
                <p className="text-[11px] text-slate-500 dark:text-slate-400">
                  Stages:{" "}
                  {Object.entries(denials.byStage)
                    .map(([stage, count]) => `${stage}=${count}`)
                    .join(" · ")}
                </p>
              )}
            </div>
          ) : (
            <RagChartEmpty message={RAG_DENIAL_EMPTY_MSG} />
          )}
        </ChartCard>

        <ChartCard
          title="Pipeline Stage Volume"
          titleHelpText="How many RAG checks ran at each step. Green = allowed, red = blocked. Early access denials are in the card on the left, not here."
        >
          {hasStageVolume ? (
            <ResponsiveContainer width="100%" height={260}>
              <BarChart data={stageData} layout="vertical" margin={{ left: 10, right: 20 }}>
                <CartesianGrid strokeDasharray="3 3" opacity={0.25} />
                <XAxis type="number" fontSize={11} allowDecimals={false} />
                <YAxis dataKey="stage" type="category" fontSize={11} width={80} />
                <Tooltip content={<RagStageTooltip />} />
                <Legend wrapperStyle={{ fontSize: 11 }} />
                <Bar dataKey="allowed" stackId="a" fill="#10b981" name="Allowed" radius={[0, 0, 0, 0]} />
                <Bar dataKey="flagged" stackId="a" fill="#f59e0b" name="Flagged" radius={[0, 0, 0, 0]} />
                <Bar dataKey="blocked" stackId="a" fill="#ef4444" name="Blocked" radius={[0, 4, 4, 0]} />
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <RagChartEmpty message={RAG_STAGE_EMPTY_MSG} />
          )}
        </ChartCard>
      </div>

      <div className="mt-6 grid gap-4 lg:grid-cols-2">
        <ChartCard
          title="Stage Block Rate"
          titleHelpText="What share of checks were blocked at each search step. A high rate at document lookup often means overshared or risky content in that library."
        >
          {hasStageVolume ? (
            <ResponsiveContainer width="100%" height={260}>
              <BarChart data={stageData}>
                <CartesianGrid strokeDasharray="3 3" opacity={0.25} />
                <XAxis dataKey="stage" fontSize={11} />
                <YAxis fontSize={11} unit="%" domain={[0, 100]} />
                <Tooltip content={<RagStageTooltip />} />
                <Bar dataKey="block_rate" fill="#ef4444" name="Block %" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <RagChartEmpty message={RAG_STAGE_EMPTY_MSG} />
          )}
        </ChartCard>

        <ChartCard
          title="Stage Throughput Snapshot"
          titleHelpText="How busy each search step was in this window. Use it to compare ask, find docs, rank, and answer — not as a single-request step-by-step funnel."
        >
          {hasFunnelData ? (
            <>
              <ResponsiveContainer width="100%" height={240}>
                <BarChart data={funnelSteps}>
                  <CartesianGrid strokeDasharray="3 3" opacity={0.25} />
                  <XAxis dataKey="step" fontSize={10} interval={0} angle={-12} textAnchor="end" height={50} />
                  <YAxis fontSize={11} allowDecimals={false} />
                  <Tooltip
                    {...module2TooltipProps}
                    formatter={(value, name, props) => {
                      if (name === "Documents") {
                        return [`${value} (${props.payload.pct}% of retrieved)`, name];
                      }
                      return [value, name];
                    }}
                  />
                  <Bar dataKey="value" fill="#0ea5e9" name="Documents" radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
              <div className="mt-3 grid grid-cols-3 gap-2 text-center text-xs">
                {funnelSteps.map((step) => (
                  <div key={step.step} className="rounded-lg bg-slate-50 px-2 py-2 dark:bg-slate-800/60">
                    <p className="font-semibold text-slate-800 dark:text-slate-100">{step.value}</p>
                    <p className="text-slate-500">{step.pct}% of retrieved</p>
                  </div>
                ))}
              </div>
            </>
          ) : (
            <RagChartEmpty message={RAG_STAGE_EMPTY_MSG} />
          )}
        </ChartCard>
      </div>

      <div className="mt-6 grid gap-4 lg:grid-cols-2">
        <ChartCard
          title="Policy Escalation Mix"
          titleHelpText="How often RAG activity triggered stricter policy levels. Spikes can follow repeated violations. Early access denials are counted in Policy / Access Denials above."
        >
          {escalationData.some((d) => d.count > 0) ? (
            <ResponsiveContainer width="100%" height={240}>
              <PieChart>
                <Pie
                  data={escalationData}
                  dataKey="count"
                  nameKey="level"
                  cx="50%"
                  cy="50%"
                  outerRadius={85}
                  label={({ level, count }) => (count > 0 ? `${level}: ${count}` : "")}
                >
                  {escalationData.map((entry) => (
                    <Cell key={entry.level} fill={entry.fill} />
                  ))}
                </Pie>
                <Tooltip {...module2TooltipProps} />
                <Legend wrapperStyle={{ fontSize: 11 }} />
              </PieChart>
            </ResponsiveContainer>
          ) : (
            <p className="py-16 text-center text-sm text-slate-400">
              No escalation-tier events in this period — all traffic ran at normal policy level.
            </p>
          )}
        </ChartCard>

        <ChartCard
          title="Collection Block Rate"
          titleHelpText="Document libraries ranked by how often searches were blocked. Libraries above 50% usually need a closer look for risky or overshared content. This is the deep dive for Hub RAG (including former Vector-only lookups)."
        >
          {collectionChart.length > 0 ? (
            <ResponsiveContainer width="100%" height={Math.max(180, collectionChart.length * 36)}>
              <BarChart data={collectionChart} layout="vertical" margin={{ left: 10 }}>
                <CartesianGrid strokeDasharray="3 3" opacity={0.25} />
                <XAxis type="number" unit="%" domain={[0, 100]} fontSize={11} />
                <YAxis dataKey="name" type="category" fontSize={10} width={90} />
                <Tooltip
                  {...module2TooltipProps}
                  formatter={(v, name) => (name === "Block %" ? `${v}%` : v)}
                  labelFormatter={(label) => `Collection: ${label}`}
                />
                <Bar dataKey="blockRate" fill="#f97316" name="Block %" radius={[0, 4, 4, 0]} />
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <p className="py-12 text-center text-sm text-slate-400">
              No document-library names were recorded on these events yet.
            </p>
          )}
        </ChartCard>

        <ChartCard
          title="Stage Latency (avg ms)"
          titleHelpText="Average time spent at each search step. Slow document lookup can mean the knowledge base or scanners are under load."
        >
          {hasLatency ? (
            <ResponsiveContainer width="100%" height={220}>
              <BarChart data={latencyData}>
                <CartesianGrid strokeDasharray="3 3" opacity={0.25} />
                <XAxis dataKey="stage" fontSize={11} />
                <YAxis fontSize={11} unit="ms" />
                <Tooltip {...module2TooltipProps} formatter={(v) => [`${v} ms`, "Avg latency"]} />
                <Bar dataKey="avg_latency_ms" fill="#6366f1" name="Avg latency (ms)" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <RagChartEmpty message="No latency metadata on RAG events yet." />
          )}
        </ChartCard>
      </div>

      <div className="mt-6">
        <ChartCard
          title="Vector Collection Registry"
          titleHelpText="Document-library exposure for Hub RAG traffic — how often each library was searched, blocked, or had sensitive content hidden."
        >
          <DataTable
            columns={[
              { key: "collection", label: "Collection", helpText: "The document library that was searched." },
              { key: "total", label: "Total", helpText: "How many times this library was involved in a RAG check." },
              { key: "blocked", label: "Blocked", helpText: "Searches stopped because of policy." },
              { key: "redacted", label: "Redacted", helpText: "Searches that continued after sensitive details were hidden." },
              {
                key: "block_rate_pct",
                label: "Block %",
                helpText: "Share of searches for this library that were blocked — a quick health signal.",
                render: (r) => (
                  <span className={r.block_rate_pct >= 50 ? "font-semibold text-red-600" : ""}>
                    {r.block_rate_pct}%
                  </span>
                ),
              },
            ]}
            rows={collections}
            emptyMessage="No document-library access events in this period."
          />
        </ChartCard>
      </div>
    </>
  );
}

export function ModelExposurePage() {
  return (
    <Module2PageErrorBoundary title="Model & RAG Health failed to render">
      <ModelExposurePageInner />
    </Module2PageErrorBoundary>
  );
}

function ModelExposurePageInner() {
  const { fetchWithAuth } = useAuth();
  const api = useMemo(() => createModule2Api(fetchWithAuth), [fetchWithAuth]);
  const [searchParams, setSearchParams] = useSearchParams();
  const tabParam = searchParams.get("tab");
  const periodParam = searchParams.get("period");
  const initialTab = tabParam === "rag" ? "rag" : "model";
  const [period, setPeriod] = useState(
    periodParam && PERIOD_LABELS[periodParam] ? periodParam : "24h",
  );
  const [activeTab, setActiveTab] = useState(initialTab);
  const [guideOpen, setGuideOpen] = useState(false);

  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const [ragData, setRagData] = useState(null);
  const [ragLoading, setRagLoading] = useState(false);
  const [ragError, setRagError] = useState(null);

  const loadSeqRef = useRef(0);
  const ragLoadSeqRef = useRef(0);
  const refreshTimerRef = useRef(null);

  const loadModel = useCallback(async () => {
    const seq = ++loadSeqRef.current;
    setLoading(true);
    setError(null);
    try {
      clearModule2Cache();
      const result = await api.getModelExposure(period, { useCache: false });
      if (seq !== loadSeqRef.current) return;
      setData(result);
    } catch (e) {
      if (seq !== loadSeqRef.current) return;
      setError(e.message || "Failed to load model exposure data.");
      setData(null);
    } finally {
      if (seq === loadSeqRef.current) setLoading(false);
    }
  }, [api, period]);

  const loadRag = useCallback(async () => {
    const seq = ++ragLoadSeqRef.current;
    setRagLoading(true);
    setRagError(null);
    try {
      clearModule2Cache();
      const result = await api.getRagHealth(period, { useCache: false });
      if (seq !== ragLoadSeqRef.current) return;
      setRagData(result);
    } catch (e) {
      if (seq !== ragLoadSeqRef.current) return;
      setRagError(e.message || "Failed to load RAG health data.");
      setRagData(null);
    } finally {
      if (seq === ragLoadSeqRef.current) setRagLoading(false);
    }
  }, [api, period]);

  const refreshActiveTab = useCallback(() => {
    clearTimeout(refreshTimerRef.current);
    refreshTimerRef.current = setTimeout(() => {
      if (activeTab === "model") loadModel();
      else loadRag();
    }, REFRESH_DEBOUNCE_MS);
  }, [activeTab, loadModel, loadRag]);

  useEffect(() => () => clearTimeout(refreshTimerRef.current), []);

  useRealtimeNotifications({ onEnforcementEvent: refreshActiveTab });
  useContainmentPolling(refreshActiveTab, { enabled: !!(data || ragData) });

  useEffect(() => {
    const onTelemetry = () => refreshActiveTab();
    window.addEventListener(TELEMETRY_ACTIVITY_EVENT, onTelemetry);
    return () => window.removeEventListener(TELEMETRY_ACTIVITY_EVENT, onTelemetry);
  }, [refreshActiveTab]);

  useEffect(() => { loadModel(); }, [loadModel]);
  useEffect(() => {
    if (activeTab === "rag") loadRag();
  }, [activeTab, loadRag]);

  useEffect(() => {
    setRagData(null);
  }, [period]);

  useEffect(() => {
    if (periodParam && PERIOD_LABELS[periodParam]) {
      setPeriod(periodParam);
    }
  }, [periodParam]);

  useEffect(() => {
    if (tabParam === "rag" || tabParam === "model") {
      setActiveTab(tabParam);
    }
  }, [tabParam]);

  const handleTabChange = useCallback((tab) => {
    setActiveTab(tab);
    setSearchParams({ tab, period }, { replace: true });
  }, [period, setSearchParams]);

  const handlePeriodChange = useCallback((next) => {
    setPeriod(next);
    setSearchParams({ tab: activeTab, period: next }, { replace: true });
  }, [activeTab, setSearchParams]);

  const chartData = formatExposureChartData(data?.exposure_by_model || []);
  const modelRows = data?.models || [];
  const hasModelActivity = modelRows.length > 0;

  return (
    <div>
      <ModelRagGuideModal open={guideOpen} onClose={() => setGuideOpen(false)} />
      <ContextualAppBar title={ANALYST_BRIEF_TITLE} description={PAGE_BRIEFS.modelRag} />
      <PageHeader
        title="Model & RAG Health"
        subtitle={`Model exposure and RAG pipeline health · ${PERIOD_LABELS[period] || period} window (server UTC)`}
        actions={
          <>
            <button
              type="button"
              onClick={() => setGuideOpen(true)}
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-600 hover:border-teal-300 hover:text-teal-700 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-300 dark:hover:border-teal-600 dark:hover:text-teal-300"
            >
              <CircleHelp className="h-3.5 w-3.5" />
              Guide
            </button>
            <PeriodSelector value={period} onChange={handlePeriodChange} />
            <Module2RefreshButton
              label="Refresh"
              onRefresh={async () => {
                clearTimeout(refreshTimerRef.current);
                if (activeTab === "model") await loadModel();
                else await loadRag();
              }}
            />
          </>
        }
      />

      <TabBar active={activeTab} onChange={handleTabChange} />

      {activeTab === "model" && (
        <>
          {error && !data && <Module2ErrorState message={error} onRetry={loadModel} />}
          {loading && !data && !error && <Module2PageSkeleton />}
          {data && !hasModelActivity && (
            <Module2EmptyState
              title="No model traffic in this period"
              message="Routed LLM requests with model metadata will populate exposure scores, block rates, and the active-models table."
              hint="Tip: Run Attack Simulator under AI Gateway & Traffic Ingress to generate model-attributed enforcement events."
            />
          )}
          {data && hasModelActivity && (
            <>
              <div className="mb-4 rounded-xl border border-sky-200 bg-sky-50/60 px-4 py-3 text-xs leading-relaxed text-sky-900 dark:border-sky-800/60 dark:bg-sky-950/20 dark:text-sky-200">
                <strong>Model Exposure</strong> ranks each LLM by a composite score (block rate, redact rate,
                latency). <strong>High exposure</strong> means more enforcement friction — review policy and
                routing for those models first.
              </div>
              <KPIBar items={buildExposureKpis(data?.summary)} />

              <div className="mt-6 grid gap-4 lg:grid-cols-2">
                <ChartCard
                  title="Vulnerability Exposure by Model"
                  titleHelpText="Composite 0–1 score from block rate, redact rate, and latency stress—higher means more enforcement friction."
                >
                  <ResponsiveContainer width="100%" height={280}>
                    <BarChart data={chartData} layout="vertical" margin={{ left: 20, right: 20 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#334155" opacity={0.2} />
                      <XAxis type="number" domain={[0, 1]} tickFormatter={(v) => `${Math.round(v * 100)}%`} fontSize={11} />
                      <YAxis type="category" dataKey="name" width={100} fontSize={11} />
                      <Tooltip
                        {...module2TooltipProps}
                        formatter={(value) => [`${(value * 100).toFixed(1)}%`, "Exposure Score"]}
                      />
                      <Bar dataKey="score" radius={[0, 4, 4, 0]}>
                        {chartData.map((entry) => (
                          <Cell key={entry.name} fill={entry.fill} />
                        ))}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                </ChartCard>

                <ChartCard
                  title="Block Rate by Model"
                  titleHelpText="Per-model hard-block percentage—compare providers for jailbreak or policy bypass patterns."
                >
                  <ResponsiveContainer width="100%" height={280}>
                    <BarChart data={chartData}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#334155" opacity={0.2} />
                      <XAxis dataKey="name" fontSize={10} interval={0} angle={-20} textAnchor="end" height={60} />
                      <YAxis fontSize={11} unit="%" domain={[0, 100]} />
                      <Tooltip {...module2TooltipProps} />
                      <Bar dataKey="blockRate" fill="#ef4444" radius={[4, 4, 0, 0]} />
                    </BarChart>
                  </ResponsiveContainer>
                </ChartCard>
              </div>

              <div className="mt-6 rounded-xl border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-700 dark:bg-slate-800/60">
                <h3 className="mb-3 text-sm font-semibold text-slate-700 dark:text-slate-200">Active Models</h3>
                <DataTable
                  columns={[
                    { key: "model", label: "Model" },
                    { key: "provider", label: "Provider" },
                    { key: "requests", label: "Requests", helpText: "Traffic volume routed to this model via the gateway." },
                    { key: "block_rate_pct", label: "Block %", helpText: "Requests terminated by policy before completion.", render: (r) => `${r.block_rate_pct}%` },
                    { key: "redact_rate_pct", label: "Redact %", helpText: "Requests completed after output or context redaction.", render: (r) => `${r.redact_rate_pct}%` },
                    { key: "avg_latency_ms", label: "Avg Latency", helpText: "Mean end-to-end latency—spikes can correlate with scanner load.", render: (r) => `${r.avg_latency_ms}ms` },
                    { key: "associated_keys", label: "Keys" },
                    {
                      key: "exposure_band",
                      label: "Exposure",
                      helpText: "Risk band derived from exposure score.",
                      render: (r) => (
                        <RiskBandBadge type="exposure" band={r.exposure_band} score={r.exposure_score} />
                      ),
                    },
                    { key: "exposure_score", label: "Score", helpText: "Normalized exposure index (0–1). Pair with block % for triage priority." },
                  ]}
                  rows={modelRows}
                  emptyMessage="No model enforcement events in this period."
                />
              </div>
            </>
          )}
        </>
      )}

      {activeTab === "rag" && (
        <RagHealthTab ragData={ragData} loading={ragLoading} error={ragError} onRetry={loadRag} />
      )}
    </div>
  );
}
