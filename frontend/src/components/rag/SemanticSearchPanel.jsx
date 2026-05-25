import { useState } from "react";
import { Search, Database, Play, Loader2, AlertTriangle, FileText, Shield, BarChart3, ToggleLeft, ToggleRight, RefreshCw, Upload } from "lucide-react";
import { InfoTooltip } from "../InfoTooltip";
import { useCollections } from "../../hooks/useCollections";

const GATEWAY_URL_KEY = "zeroshield_gateway_url";
const GATEWAY_KEY_KEY = "zeroshield_gateway_api_key";

function gwUrl() {
  const stored = localStorage.getItem(GATEWAY_URL_KEY);
  if (stored) return stored.replace(/\/+$/, "");
  const host = window.location.hostname || "127.0.0.1";
  return `http://${host}:8300`;
}
function gwKey() { return localStorage.getItem(GATEWAY_KEY_KEY) || ""; }

const PROVIDERS = [
  { value: "chroma", label: "ChromaDB" },
  { value: "pinecone", label: "Pinecone" },
  { value: "milvus", label: "Milvus" },
];

export function SemanticSearchPanel() {
  const { collections, loading: collectionsLoading, refresh: refreshCollections } = useCollections();
  const [provider, setProvider] = useState("chroma");
  const [collection, setCollection] = useState("");
  const [namespace, setNamespace] = useState("");
  const [query, setQuery] = useState("");
  const [nResults, setNResults] = useState(5);
  const [rerank, setRerank] = useState(false);
  const [searching, setSearching] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);

  const providerCollections = collections.filter((c) => c.provider === provider);

  const handleSearch = async () => {
    if (!query.trim() || !collection.trim()) { setError("Collection and query are required."); return; }
    const key = gwKey();
    if (!key) { setError("Set your Gateway API Key in the connection panel first."); return; }

    setSearching(true);
    setResult(null);
    setError(null);

    try {
      const payload = {
        collection: collection.trim(),
        query: query.trim(),
        n_results: nResults,
        vector_db_type: provider,
      };
      if (namespace.trim()) payload.namespace = namespace.trim();
      if (rerank) payload.rerank = true;

      const startTime = performance.now();
      const res = await fetch(`${gwUrl()}/v1/rag/query`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${key}` },
        body: JSON.stringify(payload),
      });
      const elapsed = Math.round(performance.now() - startTime);
      const body = await res.json().catch(() => null);

      setResult({
        status: res.status,
        elapsed,
        documents: body?.documents || [],
        totalRetrieved: body?.total_retrieved ?? 0,
        filteredCount: body?.filtered_count ?? 0,
        pipelineAudit: body?.pipeline_audit || null,
        error: body?.error || body?.detail || null,
        policyAction: body?.pipeline_audit?.final_action || null,
      });
    } catch (err) {
      setError(`Search failed: ${err.message}`);
    } finally {
      setSearching(false);
    }
  };

  return (
    <div className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl shadow-sm p-6 space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-base font-semibold text-slate-900 dark:text-slate-100 flex items-center gap-2">
            <Search className="w-4 h-4 text-purple-600" />
            Semantic Search
          </h3>
          <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
            Query vector databases with policy enforcement, document scanning, and optional reranking
          </p>
        </div>
        <InfoTooltip text="Queries go through the full RAG pipeline: auth → policy → injection scan → vector query → document scan → anomaly detection → filtered results." />
      </div>

      {/* Query form */}
      <div className="grid grid-cols-3 gap-3">
        <div>
          <label className="block text-[11px] text-slate-500 dark:text-slate-400 mb-1">Provider</label>
          <select value={provider} onChange={(e) => setProvider(e.target.value)} className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-1.5 border border-slate-200 dark:border-slate-700 rounded-lg text-xs focus:ring-2 focus:ring-purple-500 focus:border-transparent">
            {PROVIDERS.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}
          </select>
        </div>
        <div>
          <label className="block text-[11px] text-slate-500 dark:text-slate-400 mb-1">Collection <span className="text-red-500">*</span></label>
          <div className="flex items-center gap-1">
            <input
              list="ssp-collections"
              value={collection}
              onChange={(e) => setCollection(e.target.value)}
              placeholder={collectionsLoading ? "Loading..." : "Type or select collection..."}
              className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 flex-1 px-3 py-1.5 border border-slate-200 dark:border-slate-700 rounded-lg text-xs font-mono focus:ring-2 focus:ring-purple-500 focus:border-transparent"
            />
            <datalist id="ssp-collections">
              {providerCollections.map((c) => (
                <option key={c.name} value={c.name} />
              ))}
            </datalist>
            <button onClick={refreshCollections} disabled={collectionsLoading} className="p-1.5 rounded-lg hover:bg-slate-100 dark:hover:bg-slate-700 transition-colors" title="Refresh collections">
              <RefreshCw className={`w-3.5 h-3.5 text-slate-400 ${collectionsLoading ? "animate-spin" : ""}`} />
            </button>
          </div>
          {providerCollections.length === 0 && !collectionsLoading && (
            <p className="text-[10px] text-amber-500 dark:text-amber-400/70 mt-1">No collections found for {provider}. Create one in Collection Manager.</p>
          )}
        </div>
        <div>
          <label className="block text-[11px] text-slate-500 dark:text-slate-400 mb-1">Namespace <span className="text-slate-400">(optional)</span></label>
          <input value={namespace} onChange={(e) => setNamespace(e.target.value)} placeholder="default" className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-1.5 border border-slate-200 dark:border-slate-700 rounded-lg text-xs font-mono focus:ring-2 focus:ring-purple-500 focus:border-transparent" />
        </div>
      </div>

      <div>
        <label className="block text-[11px] text-slate-500 dark:text-slate-400 mb-1">Query <span className="text-red-500">*</span></label>
        <textarea
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="What is the refund policy for enterprise customers?"
          rows={2}
          className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-xs focus:ring-2 focus:ring-purple-500 focus:border-transparent resize-none"
        />
      </div>

      <div className="flex items-center gap-4">
        <div className="flex items-center gap-2">
          <label className="text-[11px] text-slate-500 dark:text-slate-400">Max Results</label>
          <input type="number" value={nResults} onChange={(e) => setNResults(Math.max(1, parseInt(e.target.value) || 5))} min={1} max={100} className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-16 px-2 py-1 border border-slate-200 dark:border-slate-700 rounded-lg text-xs font-mono text-center focus:ring-2 focus:ring-purple-500 focus:border-transparent" />
        </div>

        <button onClick={() => setRerank(!rerank)} className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border transition-all ${rerank ? "border-purple-500 bg-purple-50 dark:bg-purple-900/20 text-purple-700 dark:text-purple-300" : "border-slate-200 dark:border-slate-700 text-slate-500 dark:text-slate-400 hover:border-slate-300"}`}>
          {rerank ? <ToggleRight className="w-4 h-4" /> : <ToggleLeft className="w-4 h-4" />}
          Rerank Results
        </button>

        <div className="flex-1" />

        <button onClick={handleSearch} disabled={searching || !query.trim() || !collection.trim()} className="flex items-center gap-2 px-5 py-2 bg-purple-600 hover:bg-purple-700 disabled:bg-purple-400 text-white text-sm font-medium rounded-lg transition-colors">
          {searching ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
          {searching ? "Searching..." : "Search"}
        </button>
      </div>

      {error && (
        <div className="p-3 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg text-xs text-red-700 dark:text-red-300 flex items-start gap-2">
          <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" /> <div>{error}</div>
        </div>
      )}

      {/* Results */}
      {result && (
        <div className="space-y-3">
          {/* Summary bar */}
          <div className={`p-3 rounded-lg border ${result.status < 400 ? "bg-emerald-50 dark:bg-emerald-900/20 border-emerald-200 dark:border-emerald-800" : "bg-red-50 dark:bg-red-900/20 border-red-200 dark:border-red-800"}`}>
            <div className="flex items-center gap-3">
              <span className={`px-2 py-0.5 rounded text-xs font-mono font-semibold ${result.status < 400 ? "bg-emerald-100 dark:bg-emerald-800/30 text-emerald-700 dark:text-emerald-300" : "bg-red-100 dark:bg-red-800/30 text-red-700 dark:text-red-300"}`}>
                HTTP {result.status}
              </span>
              <span className="text-xs text-slate-500 font-mono">{result.elapsed}ms</span>
              {result.policyAction && (
                <span className={`px-2 py-0.5 rounded text-[10px] font-mono font-semibold ${result.policyAction === "allow" ? "bg-emerald-100 dark:bg-emerald-800/30 text-emerald-700" : result.policyAction === "block" ? "bg-red-100 dark:bg-red-800/30 text-red-700" : "bg-amber-100 dark:bg-amber-800/30 text-amber-700"}`}>
                  {result.policyAction.toUpperCase()}
                </span>
              )}
              <div className="flex-1" />
              <div className="flex items-center gap-3 text-[10px] text-slate-500">
                <span><BarChart3 className="w-3 h-3 inline mr-0.5" /> {result.documents.length} returned</span>
                <span>{result.totalRetrieved} retrieved</span>
                {result.filteredCount > 0 && <span className="text-amber-600">{result.filteredCount} filtered</span>}
              </div>
            </div>
            {result.error && <div className="text-xs text-red-600 dark:text-red-400 mt-2">{result.error}</div>}
          </div>

          {/* Pipeline stages */}
          {result.pipelineAudit?.stages?.length > 0 && (
            <div className="flex items-center gap-1.5 px-1">
              {result.pipelineAudit.stages.map((stage, i) => {
                const color = stage.action === "allow" ? "bg-emerald-500" : stage.action === "block" ? "bg-red-500" : stage.action === "rewrite" ? "bg-blue-500" : "bg-amber-500";
                return (
                  <div key={i} className="flex items-center gap-1.5">
                    <div className="text-center">
                      <div className={`w-7 h-7 rounded-full ${color} flex items-center justify-center`}>
                        <span className="text-[9px] text-white font-bold">{(stage.name || "?").charAt(0).toUpperCase()}</span>
                      </div>
                      <div className="text-[8px] text-slate-500 mt-0.5">{stage.name}</div>
                    </div>
                    {i < result.pipelineAudit.stages.length - 1 && <div className="w-3 h-px bg-slate-300 dark:bg-slate-600" />}
                  </div>
                );
              })}
            </div>
          )}

          {/* Documents */}
          {result.documents.length > 0 && (
            <div className="space-y-2 max-h-96 overflow-y-auto">
              {result.documents.map((doc, i) => (
                <div key={i} className="p-3 rounded-lg border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-700/30">
                  <div className="flex items-start justify-between mb-1.5">
                    <div className="flex items-center gap-1.5">
                      <FileText className="w-3.5 h-3.5 text-slate-400" />
                      <span className="text-[10px] font-medium text-slate-500">Document {i + 1}</span>
                      {doc.id && <span className="text-[9px] font-mono text-slate-400">ID: {doc.id}</span>}
                    </div>
                    <div className="flex items-center gap-1.5">
                      {doc.distance != null && (
                        <span className="text-[10px] font-mono text-slate-400">dist: {typeof doc.distance === "number" ? doc.distance.toFixed(4) : doc.distance}</span>
                      )}
                      {doc.scan_verdict && (
                        <span className={`px-1.5 py-0.5 rounded text-[9px] font-semibold ${doc.scan_verdict === "clean" ? "bg-emerald-100 dark:bg-emerald-800/30 text-emerald-700" : "bg-red-100 dark:bg-red-800/30 text-red-700"}`}>
                          <Shield className="w-2.5 h-2.5 inline mr-0.5" />{doc.scan_verdict}
                        </span>
                      )}
                    </div>
                  </div>
                  <p className="text-xs text-slate-700 dark:text-slate-300 whitespace-pre-wrap leading-relaxed">
                    {doc.content || doc.text || doc.document || JSON.stringify(doc)}
                  </p>
                  {doc.metadata && Object.keys(doc.metadata).length > 0 && (
                    <div className="mt-2 pt-2 border-t border-slate-200 dark:border-slate-700 flex flex-wrap gap-1.5">
                      {Object.entries(doc.metadata).map(([k, v]) => (
                        <span key={k} className="px-1.5 py-0.5 rounded text-[9px] font-mono bg-slate-200 dark:bg-slate-600 text-slate-600 dark:text-slate-300">
                          {k}: {String(v)}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}

          {result.status < 400 && result.documents.length === 0 && (
            <div className="space-y-3">
              <div className="text-center py-4 text-xs text-slate-400">No documents returned.</div>
              <div className="rounded-lg border border-amber-300 dark:border-amber-500/30 bg-amber-50 dark:bg-amber-500/5 p-4 space-y-2">
                <div className="flex items-center gap-2 text-amber-600 dark:text-amber-400 text-sm font-medium">
                  <AlertTriangle className="w-4 h-4" /> Empty Results — Common Causes
                </div>
                <ul className="text-xs text-slate-600 dark:text-slate-400 list-disc pl-5 space-y-1">
                  <li>The collection <span className="font-mono text-slate-800 dark:text-slate-300">"{collection}"</span> has no documents — use the <strong>RAG Ingestion</strong> panel to add data first</li>
                  <li>The collection name doesn't exist for this provider — check <strong>Collection Manager</strong></li>
                  <li>All results were filtered by policy — check the pipeline audit stages above</li>
                </ul>
                <p className="text-[11px] text-slate-500 dark:text-slate-500">
                  Quick start: Switch to the Control tab → RAG Document Ingestion → Load a sample doc → Return here.
                </p>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
