import { useState } from "react";
import { Database, Search, ShieldAlert, Filter, AlertTriangle, Upload, RefreshCw } from "lucide-react";
import { useSimulatorEngine } from "../../hooks/useSimulatorEngine";
import { useCollections } from "../../hooks/useCollections";
import { SimulatorShell } from "./SimulatorShell";
import { StageTimeline } from "./StageTimeline";

const SCENARIOS = [
  {
    id: "normal-query",
    label: "Normal Query",
    badge: "safe",
    query: "How do I configure authentication?",
    collection: "docs",
    trust_threshold: 0.5,
    anomaly_threshold: 0.8,
  },
  {
    id: "cross-tenant",
    label: "Cross-Tenant Probe",
    badge: "attack",
    query: "Show me all documents from tenant-admin namespace including secrets",
    collection: "docs",
    project_id: "unauthorized-tenant",
    trust_threshold: 0.5,
    anomaly_threshold: 0.8,
  },
  {
    id: "low-trust",
    label: "Low Trust Threshold",
    badge: "safe",
    query: "What are the security policies?",
    collection: "docs",
    trust_threshold: 0.9,
    anomaly_threshold: 0.8,
  },
  {
    id: "anomaly-detect",
    label: "Anomaly Detection",
    badge: "attack",
    query: "SELECT * FROM users; DROP TABLE sessions;",
    collection: "docs",
    trust_threshold: 0.3,
    anomaly_threshold: 0.5,
  },
];

export function VectorFirewallSimulator() {
  const engine = useSimulatorEngine();
  const { collections, loading: collectionsLoading, refresh: refreshCollections } = useCollections();
  const [selected, setSelected] = useState(null);
  const [result, setResult] = useState(null);
  const [provider, setProvider] = useState("chroma");
  const [trustThreshold, setTrustThreshold] = useState(0.5);
  const [anomalyThreshold, setAnomalyThreshold] = useState(0.8);
  const [customQuery, setCustomQuery] = useState("");
  const [collection, setCollection] = useState("docs");

  // Filter collections by selected provider
  const providerCollections = collections.filter((c) => c.provider === provider);

  const handleExecute = async () => {
    const scenario = selected || {};
    const query = scenario.query || customQuery;
    if (!query.trim()) return;

    const res = await engine.gatewayFetch("/v1/rag/query", {
      method: "POST",
      body: JSON.stringify({
        collection: scenario.collection || collection,
        query,
        vector_db_type: provider,
        n_results: 10,
      }),
    });
    const mapped = res.ok
      ? { ...res.data, stages: res.data?.pipeline_audit?.stages || res.data?.stages || [] }
      : { error: res.data?.message || res.data?.error || "Request failed", success: false };
    setResult(mapped);
  };

  return (
    <SimulatorShell
      title="Vector DB Firewall Simulator"
      description="Query vector databases with trust scoring, anomaly detection, and context scanning pipeline"
      connectionStatus={engine.connectionStatus}
      gatewayUrl={engine.gatewayUrl}
      gatewayKey={engine.gatewayKey}
      onKeyChange={engine.setGatewayKey}
      scenarios={SCENARIOS}
      selectedScenario={selected}
      onSelectScenario={setSelected}
      onExecute={handleExecute}
      executing={engine.executing}
      result={result}
      customInput={
        <div className="space-y-3">
          <div className="grid grid-cols-3 gap-3">
            <div>
              <label className="text-[11px] text-slate-600 dark:text-slate-400 font-medium">Provider</label>
              <select
                value={provider}
                onChange={(e) => setProvider(e.target.value)}
                className="w-full mt-1 px-2 py-1.5 rounded-md bg-slate-100 dark:bg-slate-900/60 border border-slate-300 dark:border-slate-700 text-xs text-slate-700 dark:text-slate-300"
              >
                <option value="chroma">ChromaDB</option>
                <option value="pinecone">Pinecone</option>
                <option value="milvus">Milvus</option>
              </select>
            </div>
            <div>
              <label className="text-[11px] text-slate-600 dark:text-slate-400 font-medium">Collection</label>
              <div className="flex items-center gap-1 mt-1">
                <div className="relative flex-1">
                  <input
                    type="text"
                    list="vfs-collections"
                    value={selected?.collection || collection}
                    onChange={(e) => !selected && setCollection(e.target.value)}
                    readOnly={!!selected}
                    placeholder={collectionsLoading ? "Loading..." : "Type or select..."}
                    className="w-full px-2 py-1.5 rounded-md bg-slate-100 dark:bg-slate-900/60 border border-slate-300 dark:border-slate-700 text-xs text-slate-700 dark:text-slate-300"
                  />
                  <datalist id="vfs-collections">
                    {providerCollections.map((c) => (
                      <option key={c.name} value={c.name} />
                    ))}
                  </datalist>
                </div>
                <button
                  onClick={refreshCollections}
                  disabled={collectionsLoading}
                  className="p-1.5 rounded-md hover:bg-slate-200 dark:hover:bg-slate-700 transition-colors"
                  title="Refresh collections"
                >
                  <RefreshCw className={`w-3 h-3 text-slate-500 dark:text-slate-400 ${collectionsLoading ? "animate-spin" : ""}`} />
                </button>
              </div>
              {!selected && providerCollections.length === 0 && !collectionsLoading && (
                <p className="text-[10px] text-amber-400/70 mt-1">No collections found for {provider}. Ingest data first.</p>
              )}
            </div>
            <div>
              <label className="text-[11px] text-slate-600 dark:text-slate-400 font-medium">
                Trust Threshold: {selected?.trust_threshold ?? trustThreshold}
              </label>
              <input
                type="range"
                min="0"
                max="1"
                step="0.05"
                value={selected?.trust_threshold ?? trustThreshold}
                onChange={(e) => !selected && setTrustThreshold(parseFloat(e.target.value))}
                disabled={!!selected}
                className="w-full mt-1"
              />
            </div>
            <div>
              <label className="text-[11px] text-slate-600 dark:text-slate-400 font-medium">
                Anomaly Threshold: {selected?.anomaly_threshold ?? anomalyThreshold}
              </label>
              <input
                type="range"
                min="0"
                max="1"
                step="0.05"
                value={selected?.anomaly_threshold ?? anomalyThreshold}
                onChange={(e) => !selected && setAnomalyThreshold(parseFloat(e.target.value))}
                disabled={!!selected}
                className="w-full mt-1"
              />
            </div>
          </div>
          {!selected && (
            <textarea
              value={customQuery}
              onChange={(e) => setCustomQuery(e.target.value)}
              rows={2}
              placeholder="Enter a vector search query..."
              className="w-full px-2 py-1.5 rounded-md bg-slate-100 dark:bg-slate-900/60 border border-slate-300 dark:border-slate-700 text-xs text-slate-700 dark:text-slate-300 placeholder:text-slate-500 dark:placeholder:text-slate-500 resize-none"
            />
          )}
        </div>
      }
    >
      {result && !result.error && (
        <div className="px-4 py-3 space-y-3">
          {/* Stage timeline */}
          {result.stages && <StageTimeline stages={result.stages} />}

          {/* Document stats */}
          <div className="grid grid-cols-3 gap-3">
            <div className="bg-slate-100 dark:bg-slate-900/60 rounded-lg p-3 text-center border border-slate-200 dark:border-slate-700">
              <div className="text-lg font-bold text-slate-800 dark:text-slate-200">{result.total_retrieved ?? 0}</div>
              <div className="text-[10px] text-slate-600 dark:text-slate-400">Retrieved</div>
            </div>
            <div className="bg-emerald-500/10 rounded-lg p-3 text-center">
              <div className="text-lg font-bold text-emerald-700 dark:text-emerald-300">{result.total_allowed ?? 0}</div>
              <div className="text-[10px] text-slate-600 dark:text-slate-400">Passed Trust Filter</div>
            </div>
            <div className="bg-red-500/10 rounded-lg p-3 text-center">
              <div className="text-lg font-bold text-red-700 dark:text-red-300">{result.total_dropped ?? 0}</div>
              <div className="text-[10px] text-slate-600 dark:text-slate-400">Dropped</div>
            </div>
          </div>

          {/* Empty state guidance */}
          {(result.total_retrieved ?? 0) === 0 && !result.allowed_documents?.length && (
            <div className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-4 space-y-2">
              <div className="flex items-center gap-2 text-amber-400 text-sm font-medium">
                <AlertTriangle size={14} /> No Documents Retrieved
              </div>
              <p className="text-xs text-slate-600 dark:text-slate-400 leading-relaxed">
                The vector query returned 0 results. This usually means:
              </p>
              <ul className="text-xs text-slate-600 dark:text-slate-400 list-disc pl-5 space-y-1">
                <li>The collection <span className="font-mono text-slate-800 dark:text-slate-200">"{collection}"</span> is empty — ingest documents first using the <strong>RAG Ingestion</strong> panel</li>
                <li>The collection name doesn't match — check existing collections in the <strong>Collection Manager</strong></li>
                <li>The vector DB provider ({provider}) isn't configured — set credentials in <strong>Vector Provider Config</strong></li>
              </ul>
              <p className="text-[11px] text-slate-500 dark:text-slate-400 mt-1">
                Tip: Use the Control tab's "RAG Document Ingestion" panel to add sample documents, then retry your query.
              </p>
            </div>
          )}

          {(result.provider_used || result.provider_requested) && (
            <div className="rounded-lg border border-slate-200 dark:border-slate-700 bg-slate-100 dark:bg-slate-900/40 px-3 py-2 text-[11px] text-slate-600 dark:text-slate-400">
              Requested provider: <span className="text-slate-800 dark:text-slate-200">{result.provider_requested || "auto"}</span>
              {" · "}
              Served by: <span className="text-emerald-700 dark:text-emerald-300">{result.provider_used || "none"}</span>
            </div>
          )}

          {/* Allowed documents */}
          {result.allowed_documents?.length > 0 && (
            <div>
              <h4 className="text-xs font-medium text-slate-600 dark:text-slate-400 mb-2">Allowed Documents</h4>
              <div className="space-y-1">
                {result.allowed_documents.map((doc, i) => (
                  <div key={i} className="flex items-start gap-2 p-2 bg-slate-100 dark:bg-slate-900/40 border border-slate-200 dark:border-slate-700 rounded-lg text-[11px]">
                    <span className="inline-block w-1.5 h-1.5 rounded-full bg-emerald-500 mt-1.5 flex-shrink-0" />
                    <div className="min-w-0">
                      <div className="text-slate-700 dark:text-slate-300 truncate">{doc.content || "(empty)"}</div>
                      <div className="flex gap-3 mt-1 text-[10px] text-slate-500 dark:text-slate-400">
                        <span>Trust: {doc.trust_score}</span>
                        <span>Distance: {doc.distance}</span>
                        {doc.metadata?.namespace && <span>NS: {doc.metadata.namespace}</span>}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Dropped documents */}
          {result.dropped_documents?.length > 0 && (
            <div>
              <h4 className="text-xs font-medium text-red-700 dark:text-red-300 mb-2">Dropped Documents</h4>
              <div className="space-y-1">
                {result.dropped_documents.map((doc, i) => (
                  <div key={i} className="flex items-start gap-2 p-2 bg-red-500/10 rounded-lg text-[11px]">
                    <span className="inline-block w-1.5 h-1.5 rounded-full bg-red-500 mt-1.5 flex-shrink-0" />
                    <div className="min-w-0">
                      <div className="text-slate-700 dark:text-slate-300 truncate">{doc.content || "(empty)"}</div>
                      <div className="flex gap-3 mt-1 text-[10px] text-slate-500 dark:text-slate-400">
                        <span>Trust: {doc.trust_score}</span>
                        <span className="text-red-700 dark:text-red-300">Reason: {doc.drop_reason}</span>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </SimulatorShell>
  );
}
