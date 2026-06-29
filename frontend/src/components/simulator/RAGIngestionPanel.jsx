import { useState, useRef, useEffect } from "react";
import { Upload, Database, CheckCircle, AlertTriangle, FileUp, Plus, X, Loader2, RefreshCw } from "lucide-react";
import { useSimulatorEngine } from "../../hooks/useSimulatorEngine";
import { useCollections } from "../../hooks/useCollections";
import { useVectorProviders } from "../../hooks/useVectorProviders";
import { DEFAULT_VECTOR_PROVIDER, VECTOR_PROVIDERS } from "../../constants/vectorProviders";

/**
 * RAG document ingestion panel for Module 1.3.
 * Ingests single or bulk documents into vector DB collections.
 * Supports sample docs, manual entry, and multi-document batch upload.
 */
export function RAGIngestionPanel() {
  const { gatewayFetch, connectionStatus, gatewayKey } = useSimulatorEngine();
  const { hasConfiguredProvider, primaryProvider } = useVectorProviders();
  const { collections, loading: collectionsLoading, refresh: refreshCollections } = useCollections({
    enabled: hasConfiguredProvider,
  });
  const [collection, setCollection] = useState("test_collection");
  const [namespace, setNamespace] = useState("default");
  const [sensitivity, setSensitivity] = useState("unclassified");
  const [provider, setProvider] = useState(DEFAULT_VECTOR_PROVIDER);
  const [ingesting, setIngesting] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);

  // Single document
  const [content, setContent] = useState("");

  // Bulk documents
  const [bulkMode, setBulkMode] = useState(false);
  const [bulkDocs, setBulkDocs] = useState([]);
  const fileInputRef = useRef(null);

  useEffect(() => {
    if (primaryProvider) setProvider(primaryProvider);
  }, [primaryProvider]);

  const SAMPLE_DOCS = [
    { label: "Public Policy", content: "Employees must use strong passwords with at least 12 characters including uppercase, lowercase, numbers, and symbols.", namespace: "public", sensitivity: "unclassified" },
    { label: "Confidential HR", content: "Employee salaries for Q3 2024: John Smith $150,000, Jane Doe $165,000. Performance reviews attached.", namespace: "hr-confidential", sensitivity: "confidential" },
    { label: "API Documentation", content: "The /v1/chat/completions endpoint accepts POST requests with a JSON body containing model, messages array, and optional max_tokens parameter.", namespace: "docs", sensitivity: "unclassified" },
    { label: "Internal Security", content: "Internal network range 10.0.0.0/8 is used for production services. VPN gateway at vpn.internal.company.com. AWS account ID: 123456789012.", namespace: "security", sensitivity: "restricted" },
  ];

  const handleIngest = async () => {
    setIngesting(true);
    setResult(null);
    setError(null);

    try {
      if (bulkMode && bulkDocs.length > 0) {
        // Bulk ingestion
        const payload = {
          collection,
          documents: bulkDocs.map((doc) => doc.content),
          ids: bulkDocs.map((doc, i) => `doc_${Date.now()}_${i}`),
          metadatas: bulkDocs.map((doc, i) => ({
            namespace,
            sensitivity,
            source: doc.source || `doc_${i}`,
            ingested_at: new Date().toISOString(),
          })),
          vector_db_type: provider,
        };
        const res = await gatewayFetch("/v1/rag/ingest", {
          method: "POST",
          body: JSON.stringify(payload),
        });
        if (res.ok) {
          setResult({ ...res.data, count: bulkDocs.length });
        } else {
          setError(res.data?.error || res.data?.message || `HTTP ${res.status}`);
        }
      } else if (content.trim()) {
        // Single document
        const res = await gatewayFetch("/v1/rag/ingest", {
          method: "POST",
          body: JSON.stringify({
            collection,
            content,
            vector_db_type: provider,
            metadata: { namespace, sensitivity, ingested_at: new Date().toISOString() },
          }),
        });
        if (res.ok) {
          setResult(res.data);
        } else {
          setError(res.data?.error || `HTTP ${res.status}`);
        }
      } else {
        setError("No content to ingest.");
        setIngesting(false);
        return;
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setIngesting(false);
    }
  };

  const handleFileUpload = (e) => {
    const files = Array.from(e.target.files || []);
    files.forEach((file) => {
      const reader = new FileReader();
      reader.onload = (ev) => {
        const text = ev.target.result;
        setBulkDocs((prev) => [...prev, { content: text, source: file.name }]);
      };
      reader.readAsText(file);
    });
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  const removeBulkDoc = (index) => {
    setBulkDocs((prev) => prev.filter((_, i) => i !== index));
  };

  const handleSample = (doc) => {
    setContent(doc.content);
    setNamespace(doc.namespace);
    setSensitivity(doc.sensitivity);
  };

  return (
    <div className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl shadow-sm p-4">
      <div className="flex items-center gap-2 mb-3">
        <Upload className="w-4 h-4 text-teal-500" />
        <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-100">RAG Document Ingestion</h3>
        <span className={`ml-auto inline-block w-2 h-2 rounded-full ${
          connectionStatus === "connected" ? "bg-emerald-500" :
          connectionStatus === "degraded" ? "bg-amber-500" : "bg-red-500"
        }`} />
      </div>

      {/* Mode toggle */}
      <div className="flex items-center gap-2 mb-3">
        <button onClick={() => setBulkMode(false)} className={`px-3 py-1 rounded-lg text-[11px] font-medium transition-colors ${!bulkMode ? "bg-teal-100 dark:bg-teal-900/30 text-teal-700 dark:text-teal-300 border border-teal-300 dark:border-teal-700" : "text-slate-500 dark:text-slate-400 border border-slate-200 dark:border-slate-700 hover:bg-slate-50 dark:hover:bg-slate-700"}`}>
          Single Document
        </button>
        <button onClick={() => setBulkMode(true)} className={`px-3 py-1 rounded-lg text-[11px] font-medium transition-colors ${bulkMode ? "bg-teal-100 dark:bg-teal-900/30 text-teal-700 dark:text-teal-300 border border-teal-300 dark:border-teal-700" : "text-slate-500 dark:text-slate-400 border border-slate-200 dark:border-slate-700 hover:bg-slate-50 dark:hover:bg-slate-700"}`}>
          <FileUp className="w-3 h-3 inline mr-1" />Bulk Upload
        </button>
      </div>

      {/* Sample docs */}
      {!bulkMode && (
        <div className="mb-3">
          <label className="text-[11px] text-slate-500 dark:text-slate-400 font-medium mb-1.5 block">Quick Load Sample Document</label>
          <div className="flex flex-wrap gap-1.5">
            {SAMPLE_DOCS.map((doc) => (
              <button
                key={doc.label}
                onClick={() => handleSample(doc)}
                className="px-2 py-1 rounded-md text-[11px] font-medium bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-300 hover:bg-slate-200 dark:hover:bg-slate-600 border border-slate-200 dark:border-slate-600 transition-colors"
              >
                {doc.label}
              </button>
            ))}
          </div>
        </div>
      )}

      <div className="grid grid-cols-4 gap-3 mb-3">
        <div>
          <label className="text-[11px] text-slate-500 dark:text-slate-400 font-medium">Provider</label>
          <select value={provider} onChange={(e) => setProvider(e.target.value)} className="w-full mt-1 px-2 py-1.5 rounded-md bg-white dark:bg-slate-700/50 border border-slate-200 dark:border-slate-600 text-xs text-slate-800 dark:text-slate-200">
            {VECTOR_PROVIDERS.map((p) => (
              <option key={p.value} value={p.value}>{p.label}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="text-[11px] text-slate-500 dark:text-slate-400 font-medium">Collection</label>
          <div className="flex items-center gap-1 mt-1">
            <input
              type="text"
              list="rip-collections"
              value={collection}
              onChange={(e) => setCollection(e.target.value)}
              placeholder={collectionsLoading ? "Loading..." : "Type or select..."}
              className="flex-1 px-2 py-1.5 rounded-md bg-white dark:bg-slate-700/50 border border-slate-200 dark:border-slate-600 text-xs text-slate-800 dark:text-slate-200"
            />
            <datalist id="rip-collections">
              {collections
                .filter((c) => c.provider === provider)
                .map((c) => (
                  <option key={c.name} value={c.name} />
                ))}
            </datalist>
            <button onClick={refreshCollections} disabled={collectionsLoading} className="p-1 rounded hover:bg-slate-100 dark:hover:bg-slate-600 transition-colors" aria-label="Refresh collections" title="Refresh collections">
              <RefreshCw className={`w-3 h-3 text-slate-400 ${collectionsLoading ? "animate-spin" : ""}`} />
            </button>
          </div>
        </div>
        <div>
          <label className="text-[11px] text-slate-500 dark:text-slate-400 font-medium">Namespace</label>
          <input type="text" value={namespace} onChange={(e) => setNamespace(e.target.value)} className="w-full mt-1 px-2 py-1.5 rounded-md bg-white dark:bg-slate-700/50 border border-slate-200 dark:border-slate-600 text-xs text-slate-800 dark:text-slate-200" />
        </div>
        <div>
          <label className="text-[11px] text-slate-500 dark:text-slate-400 font-medium">Sensitivity</label>
          <select value={sensitivity} onChange={(e) => setSensitivity(e.target.value)} className="w-full mt-1 px-2 py-1.5 rounded-md bg-white dark:bg-slate-700/50 border border-slate-200 dark:border-slate-600 text-xs text-slate-800 dark:text-slate-200">
            <option value="unclassified">Unclassified</option>
            <option value="confidential">Confidential</option>
            <option value="restricted">Restricted</option>
          </select>
        </div>
      </div>

      {/* Single mode */}
      {!bulkMode && (
        <textarea
          value={content}
          onChange={(e) => setContent(e.target.value)}
          rows={3}
          placeholder="Enter document content to ingest into the vector DB..."
          className="w-full mb-3 px-3 py-2 rounded-lg bg-white dark:bg-slate-700/50 border border-slate-200 dark:border-slate-600 text-xs text-slate-800 dark:text-slate-200 placeholder:text-slate-400 resize-none"
        />
      )}

      {/* Bulk mode */}
      {bulkMode && (
        <div className="mb-3 space-y-2">
          <input type="file" ref={fileInputRef} multiple accept=".txt,.md,.csv,.json" onChange={handleFileUpload} className="hidden" />
          <button onClick={() => fileInputRef.current?.click()} className="flex items-center gap-1.5 px-3 py-1.5 border border-dashed border-slate-300 dark:border-slate-600 rounded-lg text-xs text-slate-500 hover:border-teal-400 hover:text-teal-600 transition-colors w-full justify-center">
            <FileUp className="w-3.5 h-3.5" /> Upload Files (.txt, .md, .csv, .json)
          </button>
          {bulkDocs.length > 0 && (
            <div className="space-y-1 max-h-32 overflow-y-auto">
              {bulkDocs.map((doc, i) => (
                <div key={i} className="flex items-center justify-between px-2 py-1 rounded bg-slate-50 dark:bg-slate-700/30 border border-slate-200 dark:border-slate-700 text-[11px]">
                  <span className="text-slate-600 dark:text-slate-300 truncate flex-1">{doc.source || `Document ${i + 1}`} — {doc.content.length} chars</span>
                  <button onClick={() => removeBulkDoc(i)} className="ml-2 text-red-400 hover:text-red-600"><X className="w-3 h-3" /></button>
                </div>
              ))}
            </div>
          )}
          <p className="text-[10px] text-slate-400">{bulkDocs.length} document(s) queued for ingestion</p>
        </div>
      )}

      <button
        onClick={handleIngest}
        disabled={ingesting || (!bulkMode && !content.trim()) || (bulkMode && bulkDocs.length === 0) || connectionStatus === "disconnected"}
        className="flex items-center gap-1.5 px-4 py-2 bg-teal-600 hover:bg-teal-700 disabled:bg-teal-400 text-white text-xs font-medium rounded-lg transition-colors"
      >
        {ingesting ? (
          <><Loader2 className="w-3.5 h-3.5 animate-spin" /> Ingesting...</>
        ) : (
          <><Database className="w-3.5 h-3.5" /> {bulkMode ? `Ingest ${bulkDocs.length} Documents` : "Ingest Document"}</>
        )}
      </button>

      {error && (
        <div className="mt-3 p-2 rounded-lg bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 text-[11px] text-red-700 dark:text-red-400 flex items-center gap-2">
          <AlertTriangle className="w-3.5 h-3.5" /> {error}
        </div>
      )}

      {result && (
        <div className="mt-3 p-3 rounded-lg bg-emerald-50 dark:bg-emerald-900/20 border border-emerald-200 dark:border-emerald-800">
          <div className="flex items-center gap-2 mb-1">
            <CheckCircle className="w-4 h-4 text-emerald-600 dark:text-emerald-400" />
            <span className="text-xs font-semibold text-emerald-700 dark:text-emerald-400">Document Ingested</span>
          </div>
          <div className="grid grid-cols-3 gap-2 text-[11px] text-slate-600 dark:text-slate-400">
            <div><span className="text-slate-400">Client:</span> {result.client}</div>
            <div><span className="text-slate-400">Collection:</span> {result.collection}</div>
            <div><span className="text-slate-400">Doc ID:</span> <span className="font-mono">{result.doc_id}</span></div>
          </div>
        </div>
      )}
    </div>
  );
}
