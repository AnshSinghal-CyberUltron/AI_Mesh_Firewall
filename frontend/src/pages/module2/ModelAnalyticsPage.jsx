import { useCallback, useEffect, useMemo, useState } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { RefreshCw } from "lucide-react";
import { useAuth } from "../../context/AuthContext";
import { createModule2Api } from "../../api/module2";
import { PageHeader } from "../../components/module2/PageHeader";
import { KPIBar } from "../../components/module2/KPIBar";
import { ChartCard } from "../../components/module2/ChartCard";
import { DataTable } from "../../components/module2/DataTable";
import { PeriodSelector } from "../../components/module2/PeriodSelector";
import { Module2ErrorState, Module2PageSkeleton } from "../../components/module2/PageStates";
import { ContextualAppBar } from "../../components/module2/ContextualAppBar";
import { buildExposureKpis, exposureBandClass, formatExposureChartData } from "./pageData";
import { ANALYST_BRIEF_TITLE, PAGE_BRIEFS } from "./pageCopy";

const TABS = [
  { id: "model", label: "Model Exposure" },
  { id: "rag", label: "RAG & Retrieval" },
];

function TabBar({ active, onChange }) {
  return (
    <div className="flex gap-1 rounded-lg bg-slate-100 p-1 dark:bg-slate-700/50 w-fit mb-6">
      {TABS.map((t) => (
        <button
          key={t.id}
          onClick={() => onChange(t.id)}
          className={`rounded-md px-4 py-1.5 text-sm font-medium transition-colors ${
            active === t.id
              ? "bg-white shadow text-slate-900 dark:bg-slate-800 dark:text-white"
              : "text-slate-500 hover:text-slate-700 dark:text-slate-400 dark:hover:text-slate-200"
          }`}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}

function RagHealthTab({ ragData, loading, error, onRetry }) {
  if (loading && !ragData) return <Module2PageSkeleton />;
  if (error && !ragData) return <Module2ErrorState message={error} onRetry={onRetry} />;
  if (!ragData) return null;

  const stages = ragData.rag_pipeline_kpis?.stages || {};
  const stageData = ["query", "retriever", "ranker", "generator"].map((s) => ({
    stage: s.charAt(0).toUpperCase() + s.slice(1),
    total: stages[s]?.total || 0,
    blocked: stages[s]?.blocked || 0,
    block_rate: stages[s]?.total
      ? Math.round((stages[s].blocked / stages[s].total) * 100)
      : 0,
  }));

  const collections = ragData.vector_exposure?.collections || [];

  return (
    <>
      <div className="grid gap-4 lg:grid-cols-2">
        <ChartCard
          title="Stage Block Rates"
          titleHelpText="Block rate per pipeline stage—identifies where retrieval or generation controls are firing most."
        >
          <ResponsiveContainer width="100%" height={220}>
            <BarChart data={stageData} layout="vertical" margin={{ left: 10 }}>
              <CartesianGrid strokeDasharray="3 3" opacity={0.25} />
              <XAxis type="number" unit="%" fontSize={11} />
              <YAxis dataKey="stage" type="category" fontSize={11} width={75} />
              <Tooltip formatter={(v) => `${v}%`} />
              <Bar dataKey="block_rate" fill="#8b5cf6" radius={[0, 4, 4, 0]} name="Block %" />
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>

        <ChartCard
          title="Document Funnel"
          titleHelpText="Document survival through ranker and generator gates—drop-offs signal chunk filtering or policy kills."
        >
          {(() => {
            const funnel = ragData.rag_pipeline_kpis?.document_funnel || {};
            const rows = [
              { label: "Retrieved", value: funnel.retrieved ?? 0 },
              { label: "Post-Ranker", value: funnel.post_ranker ?? 0 },
              { label: "Post-Generator", value: funnel.post_generator ?? 0 },
            ];
            return (
              <ul className="mt-2 space-y-3">
                {rows.map((r) => (
                  <li key={r.label} className="flex items-center justify-between text-sm">
                    <span className="text-slate-600 dark:text-slate-300">{r.label}</span>
                    <span className="font-semibold text-slate-900 dark:text-white">{r.value.toLocaleString()}</span>
                  </li>
                ))}
              </ul>
            );
          })()}
        </ChartCard>
      </div>

      <div className="mt-6">
        <ChartCard
          title="Top Collections by Block Rate"
          titleHelpText="Collections ranked by block rate—high values may indicate poisoned chunks or ACL misconfiguration."
        >
          <DataTable
            columns={[
              { key: "collection", label: "Collection", helpText: "Vector store collection or namespace queried during retrieval." },
              { key: "total", label: "Total" },
              { key: "blocked", label: "Blocked" },
              { key: "redacted", label: "Redacted" },
              { key: "block_rate_pct", label: "Block %", render: (r) => `${r.block_rate_pct}%` },
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
