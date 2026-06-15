import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Bar, BarChart, CartesianGrid, Cell, Legend, Pie, PieChart,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import {
  BookOpen, Cpu, Database, Filter, RefreshCw, Search, Sparkles,
} from "lucide-react";
import { useAuth } from "../../context/AuthContext";
import { createModule2Api } from "../../api/module2";
import { PageHeader } from "../../components/module2/PageHeader";
import { KPIBar } from "../../components/module2/KPIBar";
import { ChartCard } from "../../components/module2/ChartCard";
import { DataTable } from "../../components/module2/DataTable";
import { PeriodSelector } from "../../components/module2/PeriodSelector";
import { Module2EmptyState, Module2ErrorState, Module2PageSkeleton } from "../../components/module2/PageStates";
import { ContextualAppBar } from "../../components/module2/ContextualAppBar";
import {
  buildExposureKpis,
  buildRagKpis,
  exposureBandClass,
  formatExposureChartData,
  formatRagCollectionChartData,
  formatRagDocumentFunnel,
  formatRagEscalationChartData,
  formatRagStageChartData,
} from "./pageData";
import { ANALYST_BRIEF_TITLE, PAGE_BRIEFS } from "./pageCopy";

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

function RagPipelineGuide() {
  return (
    <div className="mb-6 rounded-xl border border-violet-200 bg-violet-50/60 p-4 dark:border-violet-800/60 dark:bg-violet-950/20">
      <p className="mb-3 text-sm font-semibold text-violet-900 dark:text-violet-200">
        How to read this page
      </p>
      <p className="mb-4 text-xs leading-relaxed text-violet-800/90 dark:text-violet-300/90">
        Every RAG request passes through four gates. A <strong>block</strong> means the firewall stopped the
        request at that stage. <strong>100% block rate</strong> on a stage with few events means every request
        was denied there — not that your whole fleet is down. Compare volume (bars) with block rate together.
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
    </div>
  );
}

function RagStageTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  const row = payload[0]?.payload;
  if (!row) return null;
  return (
    <div className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs shadow-lg dark:border-slate-600 dark:bg-slate-800">
      <p className="font-semibold text-slate-800 dark:text-slate-100">{label}</p>
      <p className="text-slate-600 dark:text-slate-300">Total checks: {row.total}</p>
      <p className="text-red-600">Blocked: {row.blocked} ({row.block_rate}%)</p>
      <p className="text-emerald-600">Allowed: {row.allowed}</p>
      {row.flagged > 0 && <p className="text-amber-600">Flagged: {row.flagged}</p>}
      {row.avg_latency_ms > 0 && (
        <p className="text-slate-500">Avg latency: {row.avg_latency_ms}ms</p>
      )}
    </div>
  );
}

function RagHealthTab({ ragData, loading, error, onRetry }) {
  if (loading && !ragData) return <Module2PageSkeleton />;
  if (error && !ragData) return <Module2ErrorState message={error} onRetry={onRetry} />;
  if (!ragData) return null;

  const kpis = ragData.rag_pipeline_kpis || {};
  const stageData = formatRagStageChartData(kpis.stages);
  const funnelSteps = formatRagDocumentFunnel(kpis.document_funnel);
  const collections = ragData.vector_exposure?.collections || [];
  const collectionChart = formatRagCollectionChartData(collections);
  const escalationData = formatRagEscalationChartData(kpis.escalation_distribution);
  const totalPipelineEvents = stageData.reduce((s, r) => s + r.total, 0);

  if (totalPipelineEvents === 0 && collections.length === 0) {
    return (
      <>
        <RagPipelineGuide />
        <Module2EmptyState
          title="No RAG pipeline activity in this period"
          message="Run RAG traffic through Module 1.3 (RAG & Vector DB Firewall) with event_type=rag_pipeline metadata so stage-level metrics appear here."
          hint="Tip: Blocked retriever events show collection names and stage block rates quickly."
        />
      </>
    );
  }

  return (
    <>
      <RagPipelineGuide />
      <KPIBar items={buildRagKpis(kpis, ragData.vector_exposure)} />

      <div className="mt-6 grid gap-4 lg:grid-cols-2">
        <ChartCard
          title="Pipeline Stage Volume"
          titleHelpText="Stacked counts per stage — red = blocked, green = allowed. Shows WHERE enforcement fires, not just the percentage."
        >
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
        </ChartCard>

        <ChartCard
          title="Stage Block Rate"
          titleHelpText="Percentage of checks blocked at each gate. High rates at Retriever often mean vector ACL or collection poisoning."
        >
          <ResponsiveContainer width="100%" height={260}>
            <BarChart data={stageData}>
              <CartesianGrid strokeDasharray="3 3" opacity={0.25} />
              <XAxis dataKey="stage" fontSize={11} />
              <YAxis fontSize={11} unit="%" domain={[0, 100]} />
              <Tooltip content={<RagStageTooltip />} />
              <Bar dataKey="block_rate" fill="#8b5cf6" name="Block %" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>
      </div>

      <div className="mt-6 grid gap-4 lg:grid-cols-2">
        <ChartCard
          title="Document Survival Funnel"
          titleHelpText="How many retrieval paths survive each gate. Large drops after Retriever or Ranker indicate chunk filtering or policy kills."
        >
          <ResponsiveContainer width="100%" height={240}>
            <BarChart data={funnelSteps}>
              <CartesianGrid strokeDasharray="3 3" opacity={0.25} />
              <XAxis dataKey="step" fontSize={10} interval={0} angle={-12} textAnchor="end" height={50} />
              <YAxis fontSize={11} allowDecimals={false} />
              <Tooltip
                formatter={(value, name, props) => {
                  if (name === "Documents") {
                    return [`${value} (${props.payload.pct}% of retrieved)`, name];
                  }
                  return [value, name];
                }}
              />
              <Bar dataKey="value" name="Documents" fill="#6366f1" radius={[4, 4, 0, 0]} />
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
        </ChartCard>

        <ChartCard
          title="Policy Escalation Mix"
          titleHelpText="How often RAG events triggered elevated or strict policy tiers — spikes may follow repeated violations."
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
                <Tooltip />
                <Legend wrapperStyle={{ fontSize: 11 }} />
              </PieChart>
            </ResponsiveContainer>
          ) : (
            <p className="py-16 text-center text-sm text-slate-400">
              No escalation-tier events in this period — all traffic ran at normal policy level.
            </p>
          )}
        </ChartCard>
      </div>

      <div className="mt-6 grid gap-4 lg:grid-cols-2">
        <ChartCard
          title="Collection Block Rate"
          titleHelpText="Vector collections ranked by block rate — investigate collections above 50% for poisoned embeddings or ACL misconfiguration."
        >
          {collectionChart.length > 0 ? (
            <ResponsiveContainer width="100%" height={Math.max(180, collectionChart.length * 36)}>
              <BarChart data={collectionChart} layout="vertical" margin={{ left: 10 }}>
                <CartesianGrid strokeDasharray="3 3" opacity={0.25} />
                <XAxis type="number" unit="%" domain={[0, 100]} fontSize={11} />
                <YAxis dataKey="name" type="category" fontSize={10} width={90} />
                <Tooltip
                  formatter={(v, name) => (name === "Block %" ? `${v}%` : v)}
                  labelFormatter={(label) => `Collection: ${label}`}
                />
                <Bar dataKey="blockRate" fill="#ef4444" name="Block %" radius={[0, 4, 4, 0]} />
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <p className="py-12 text-center text-sm text-slate-400">
              No vector collection metadata on events — ensure retriever telemetry includes collection name.
            </p>
          )}
        </ChartCard>

        <ChartCard
          title="Stage Latency (avg ms)"
          titleHelpText="Mean processing time per pipeline stage — latency spikes at Retriever may indicate vector DB or scanner load."
        >
          <ResponsiveContainer width="100%" height={220}>
            <BarChart data={stageData.filter((s) => s.avg_latency_ms > 0)}>
              <CartesianGrid strokeDasharray="3 3" opacity={0.25} />
              <XAxis dataKey="stage" fontSize={11} />
              <YAxis fontSize={11} unit="ms" />
              <Tooltip formatter={(v) => [`${v} ms`, "Avg latency"]} />
              <Bar dataKey="avg_latency_ms" fill="#0ea5e9" name="Avg latency" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
          {!stageData.some((s) => s.avg_latency_ms > 0) && (
            <p className="-mt-16 text-center text-sm text-slate-400">No latency metadata on RAG events yet.</p>
          )}
        </ChartCard>
      </div>

      <div className="mt-6">
        <ChartCard
          title="Vector Collection Registry"
          titleHelpText="Full breakdown by collection — total access attempts, blocks, and redactions from vector DB firewall telemetry."
        >
          <DataTable
            columns={[
              { key: "collection", label: "Collection", helpText: "Vector store collection or namespace queried during retrieval." },
              { key: "total", label: "Total", helpText: "All enforcement events tied to this collection." },
              { key: "blocked", label: "Blocked", helpText: "Retrieval or chunk access denied by policy." },
              { key: "redacted", label: "Redacted", helpText: "Sensitive fields masked before the request continued." },
              {
                key: "block_rate_pct",
                label: "Block %",
                helpText: "blocked ÷ total — primary signal for collection health.",
                render: (r) => (
                  <span className={r.block_rate_pct >= 50 ? "font-semibold text-red-600" : ""}>
                    {r.block_rate_pct}%
                  </span>
                ),
              },
            ]}
            rows={collections}
            emptyMessage="No vector access events in this period."
          />
        </ChartCard>
      </div>
    </>
  );
}

export function ModelExposurePage() {
  const { fetchWithAuth } = useAuth();
  const api = useMemo(() => createModule2Api(fetchWithAuth), [fetchWithAuth]);
  const [period, setPeriod] = useState("30d");
  const [activeTab, setActiveTab] = useState("model");

  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const [ragData, setRagData] = useState(null);
  const [ragLoading, setRagLoading] = useState(false);
  const [ragError, setRagError] = useState(null);

  const loadModel = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await api.getModelExposure(period));
    } catch (e) {
      setError(e.message || "Failed to load model exposure data.");
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [api, period]);

  const loadRag = useCallback(async () => {
    setRagLoading(true);
    setRagError(null);
    try {
      setRagData(await api.getRagHealth(period));
    } catch (e) {
      setRagError(e.message || "Failed to load RAG health data.");
      setRagData(null);
    } finally {
      setRagLoading(false);
    }
  }, [api, period]);

  useEffect(() => { loadModel(); }, [loadModel]);
  useEffect(() => { if (activeTab === "rag") loadRag(); }, [activeTab, loadRag]);

  const chartData = formatExposureChartData(data?.exposure_by_model || []);

  return (
    <div>
      <ContextualAppBar title={ANALYST_BRIEF_TITLE} description={PAGE_BRIEFS.modelRag} />
      <PageHeader
        title="Model & RAG Health"
        subtitle="Model exposure scores and RAG pipeline health metrics from live enforcement data"
        actions={
          <>
            <PeriodSelector value={period} onChange={setPeriod} />
            <button
              type="button"
              onClick={activeTab === "model" ? loadModel : loadRag}
              className="rounded-lg border border-slate-200 p-2 dark:border-slate-600"
              aria-label="Refresh"
            >
              <RefreshCw className="h-4 w-4" />
            </button>
          </>
        }
      />

      <TabBar active={activeTab} onChange={setActiveTab} />

      {activeTab === "model" && (
        <>
          {error && !data && <Module2ErrorState message={error} onRetry={loadModel} />}
          {loading && !data && !error && <Module2PageSkeleton />}
          {data && (
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
                      <Tooltip formatter={(value) => [`${(value * 100).toFixed(1)}%`, "Exposure Score"]} />
                      <Bar dataKey="score" radius={[0, 4, 4, 0]} fill="#0ea5e9" />
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
                      <YAxis fontSize={11} unit="%" />
                      <Tooltip />
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
                      helpText: "Risk band derived from exposure score: low, medium, or high.",
                      render: (r) => (
                        <span className={`rounded px-2 py-0.5 text-xs font-medium ${exposureBandClass(r.exposure_band)}`}>
                          {r.exposure_band}
                        </span>
                      ),
                    },
                    { key: "exposure_score", label: "Score", helpText: "Normalized exposure index (0–1). Pair with block % for triage priority." },
                  ]}
                  rows={data?.models || []}
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
