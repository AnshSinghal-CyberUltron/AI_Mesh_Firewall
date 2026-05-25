import { useState } from "react";
import {
  Database, CheckCircle, AlertTriangle, Loader2, Zap, RefreshCw, List, Play, ChevronDown, ChevronRight,
} from "lucide-react";
import { InfoTooltip } from "./InfoTooltip";
import { resolveGatewayBaseUrl } from "../utils/environmentUrls";

const GATEWAY_URL_KEY = "zeroshield_gateway_url";
const GATEWAY_KEY_KEY = "zeroshield_gateway_api_key";

const DB_PROVIDERS = [
  {
    value: "chromadb",
    label: "ChromaDB",
    description: "Open-source, self-hosted embedding database",
    fields: [
      { key: "connection_url", label: "ChromaDB URL", placeholder: "http://localhost:8000", required: true },
    ],
    simulationFields: [
      { key: "collection", label: "Collection Name", placeholder: "docs", required: true },
      { key: "query", label: "Test Query", placeholder: "What is the refund policy?", required: true },
      { key: "n_results", label: "Max Results", placeholder: "5", type: "number" },
    ],
  },
  {
    value: "pinecone",
    label: "Pinecone",
    description: "Managed serverless vector database — only API key needed",
    fields: [
      { key: "api_key", label: "Pinecone API Key", placeholder: "pcsk_... or pc-...", required: true, type: "password" },
    ],
    simulationFields: [
      { key: "index_name", label: "Index Name", placeholder: "my-index", required: true, helpText: "Select from the list after connecting, or type a name" },
      { key: "namespace", label: "Namespace (optional)", placeholder: "default" },
      { key: "query", label: "Test Query", placeholder: "What is the refund policy?", required: true },
      { key: "n_results", label: "Max Results", placeholder: "5", type: "number" },
    ],
  },
  {
    value: "milvus",
    label: "Milvus",
    description: "High-performance open-source vector database",
    fields: [
      { key: "connection_url", label: "Milvus URI", placeholder: "http://localhost:19530", required: true },
      { key: "api_key", label: "Token (optional)", placeholder: "", type: "password" },
    ],
    simulationFields: [
      { key: "collection", label: "Collection Name", placeholder: "docs", required: true },
      { key: "query", label: "Test Query", placeholder: "What is the refund policy?", required: true },
      { key: "n_results", label: "Max Results", placeholder: "5", type: "number" },
    ],
  },
];

function ConnectionStatusBadge({ status }) {
  if (!status) return null;
  const config = {
    connected: { icon: CheckCircle, text: "Connected", cls: "bg-emerald-50 dark:bg-emerald-900/20 text-emerald-700 dark:text-emerald-300 border-emerald-200 dark:border-emerald-800" },
    error: { icon: AlertTriangle, text: "Failed", cls: "bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-300 border-red-200 dark:border-red-800" },
    testing: { icon: Loader2, text: "Testing...", cls: "bg-blue-50 dark:bg-blue-900/20 text-blue-700 dark:text-blue-300 border-blue-200 dark:border-blue-800" },
  };
  const c = config[status] || config.error;
  const Icon = c.icon;
  return (
    <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-medium border ${c.cls}`}>
      <Icon className={`w-3.5 h-3.5 ${status === "testing" ? "animate-spin" : ""}`} />
      {c.text}
    </span>
  );
}

export function DatabaseConnectionPanel() {
  const [gatewayUrl, setGatewayUrl] = useState(() => {
    const stored = localStorage.getItem(GATEWAY_URL_KEY);
    if (stored) return stored;
    return resolveGatewayBaseUrl() || "http://127.0.0.1:8300";
  });
  const [apiKey, setApiKey] = useState(() => localStorage.getItem(GATEWAY_KEY_KEY) || "");
  const [selectedProvider, setSelectedProvider] = useState("chromadb");
  const [fieldValues, setFieldValues] = useState({});
  const [simFieldValues, setSimFieldValues] = useState({});
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [showSimulation, setShowSimulation] = useState(false);
  const [simulating, setSimulating] = useState(false);
  const [simResult, setSimResult] = useState(null);

  const currentProvider = DB_PROVIDERS.find((p) => p.value === selectedProvider);

  const handleProviderSelect = (providerValue) => {
    setSelectedProvider(providerValue);
    setFieldValues({});
    setSimFieldValues({});
    setResult(null);
    setError(null);
    setSimResult(null);
  };

  const handleFieldChange = (key, value) => {
    setFieldValues((prev) => ({ ...prev, [key]: value }));
  };

  const handleSimFieldChange = (key, value) => {
    setSimFieldValues((prev) => ({ ...prev, [key]: value }));
  };

  const handleTestConnection = async () => {
    if (!apiKey.trim()) {
      setError("Gateway API key is required.");
      return;
    }

    localStorage.setItem(GATEWAY_URL_KEY, gatewayUrl);
    localStorage.setItem(GATEWAY_KEY_KEY, apiKey);

    setTesting(true);
    setResult(null);
    setError(null);

    try {
      const url = `${gatewayUrl.replace(/\/+$/, "")}/v1/admin/db-test`;
      const startTime = performance.now();

      const payload = { provider: selectedProvider, ...fieldValues };
      const res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${apiKey}` },
        body: JSON.stringify(payload),
      });

      const elapsed = Math.round(performance.now() - startTime);
      let body = {};
      try { body = await res.json(); } catch { body = { detail: await res.text() }; }

      if (res.status === 401 || res.status === 403) {
        setError("Authentication failed. Your Gateway API Key is invalid or expired.");
      } else if (res.ok && body.status !== "error") {
        setResult({
          success: true,
          status: body.status || "connected",
          details: body.details || "Connection successful",
          latency: elapsed,
          indexes: body.indexes || [],
          collections: body.collections || [],
        });
      } else {
        setResult({
          success: false,
          status: body.status || "failed",
          details: body.error || body.detail || body.message || `HTTP ${res.status}`,
          latency: elapsed,
        });
      }
    } catch (err) {
      setError(
        err.message === "Failed to fetch"
          ? `Cannot reach gateway at ${gatewayUrl}. Ensure the gateway is running.`
          : err.message
      );
    } finally {
      setTesting(false);
    }
  };

  const handleSimulate = async () => {
    if (!apiKey.trim()) { setError("Gateway API key is required."); return; }

    const collection = simFieldValues.collection || simFieldValues.index_name;
    const query = simFieldValues.query;
    if (!collection || !query) { setError("Collection/Index name and query are required for simulation."); return; }

    localStorage.setItem(GATEWAY_URL_KEY, gatewayUrl);
    localStorage.setItem(GATEWAY_KEY_KEY, apiKey);

    setSimulating(true);
    setSimResult(null);
    setError(null);

    try {
      const url = `${gatewayUrl.replace(/\/+$/, "")}/v1/rag/query`;
      const startTime = performance.now();

      const payload = {
        collection: collection,
        query: query,
        n_results: parseInt(simFieldValues.n_results) || 5,
        vector_db_type: selectedProvider === "chromadb" ? "chroma" : selectedProvider,
      };
      if (simFieldValues.namespace) payload.namespace = simFieldValues.namespace;

      const res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${apiKey}` },
        body: JSON.stringify(payload),
      });

      const elapsed = Math.round(performance.now() - startTime);
      let body = null;
      try { body = await res.json(); } catch { body = null; }

      setSimResult({
        status: res.status,
        elapsed,
        body,
        pipelineAudit: body?.pipeline_audit || null,
        docsReturned: body?.documents?.length ?? 0,
        totalRetrieved: body?.total_retrieved ?? 0,
        filteredCount: body?.filtered_count ?? 0,
        error: body?.error || null,
      });
    } catch (err) {
      setError(`Simulation failed: ${err.message}`);
    } finally {
      setSimulating(false);
    }
  };

  // Items available from connection test
  const availableItems = result?.success
    ? (result.indexes?.length > 0 ? result.indexes : result.collections || [])
    : [];

  return (
    <div className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl shadow-sm p-6 space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-base font-semibold text-slate-900 dark:text-slate-100 flex items-center gap-2">
            <Database className="w-4 h-4 text-teal-600" />
            Vector Database Connection
          </h3>
          <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
            Connect, test, and simulate queries against vector databases
          </p>
        </div>
        <InfoTooltip text="Test connectivity to ChromaDB, Pinecone, or Milvus through the gateway. After connecting, run simulation queries through the RAG pipeline to verify end-to-end data flow." />
      </div>

      {/* Gateway config row */}
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">Gateway URL</label>
          <input
            type="text"
            value={gatewayUrl}
            onChange={(e) => setGatewayUrl(e.target.value)}
            placeholder="http://127.0.0.1:8300"
            className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-xs font-mono focus:ring-2 focus:ring-teal-500 focus:border-transparent"
          />
        </div>
        <div>
          <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">Gateway API Key *</label>
          <input
            type="password"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder="Paste your gateway API key"
            className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-xs font-mono focus:ring-2 focus:ring-teal-500 focus:border-transparent"
          />
        </div>
      </div>

      {/* Provider selection */}
      <div>
        <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-2">Database Provider</label>
        <div className="grid grid-cols-3 gap-2">
          {DB_PROVIDERS.map((provider) => {
            const isSelected = selectedProvider === provider.value;
            return (
              <button
                key={provider.value}
                onClick={() => handleProviderSelect(provider.value)}
                className={`text-left p-3 rounded-lg border transition-all ${
                  isSelected
                    ? "border-teal-500 bg-teal-50 dark:bg-teal-900/20 ring-1 ring-teal-500"
                    : "border-slate-200 dark:border-slate-700 hover:border-slate-300 dark:hover:border-slate-600"
                }`}
              >
                <div className="flex items-center gap-2">
                  <Database className={`w-4 h-4 ${isSelected ? "text-teal-600" : "text-slate-400"}`} />
                  <span className={`text-sm font-medium ${isSelected ? "text-teal-800 dark:text-teal-200" : "text-slate-700 dark:text-slate-300"}`}>
                    {provider.label}
                  </span>
                </div>
                <p className="text-[10px] text-slate-500 dark:text-slate-400 mt-1">{provider.description}</p>
              </button>
            );
          })}
        </div>
      </div>

      {/* Provider-specific connection fields */}
      {currentProvider && (
        <div className="space-y-3">
          <div className="text-xs font-medium text-slate-600 dark:text-slate-400">Connection Settings</div>
          {currentProvider.fields.map((field) => (
            <div key={field.key}>
              <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">
                {field.label} {field.required && <span className="text-red-500">*</span>}
              </label>
              <input
                type={field.type || "text"}
                value={fieldValues[field.key] || ""}
                onChange={(e) => handleFieldChange(field.key, e.target.value)}
                placeholder={field.placeholder}
                className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm font-mono focus:ring-2 focus:ring-teal-500 focus:border-transparent"
              />
            </div>
          ))}
        </div>
      )}

      {/* Test Connection button */}
      <div className="flex items-center gap-3">
        <button
          onClick={handleTestConnection}
          disabled={testing}
          className="flex items-center gap-2 px-4 py-2.5 bg-teal-600 hover:bg-teal-700 disabled:bg-teal-400 text-white text-sm font-medium rounded-lg transition-colors"
        >
          {testing ? <Loader2 className="w-4 h-4 animate-spin" /> : <Zap className="w-4 h-4" />}
          {testing ? "Testing..." : "Test Connection"}
        </button>
        {result && <ConnectionStatusBadge status={result.success ? "connected" : "error"} />}
      </div>

      {/* Error */}
      {error && (
        <div className="p-3 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg text-xs text-red-700 dark:text-red-300 flex items-start gap-2">
          <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" />
          <div>{error}</div>
        </div>
      )}

      {/* Connection result */}
      {result && (
        <div className={`p-4 rounded-lg border ${
          result.success
            ? "bg-emerald-50 dark:bg-emerald-900/20 border-emerald-200 dark:border-emerald-800"
            : "bg-red-50 dark:bg-red-900/20 border-red-200 dark:border-red-800"
        }`}>
          <div className="flex items-center gap-2 mb-2">
            {result.success ? <CheckCircle className="w-5 h-5 text-emerald-600" /> : <AlertTriangle className="w-5 h-5 text-red-600" />}
            <span className={`text-sm font-bold ${result.success ? "text-emerald-700 dark:text-emerald-300" : "text-red-700 dark:text-red-300"}`}>
              {result.success ? "Connection Successful" : "Connection Failed"}
            </span>
            <span className="text-[10px] text-slate-400">{result.latency}ms</span>
          </div>
          <div className="text-sm text-slate-700 dark:text-slate-300 mb-2">{result.details}</div>

          {/* Show available indexes/collections */}
          {availableItems.length > 0 && (
            <div className="mt-3 pt-3 border-t border-emerald-200 dark:border-emerald-700">
              <div className="flex items-center gap-1.5 mb-2">
                <List className="w-3.5 h-3.5 text-emerald-600 dark:text-emerald-400" />
                <span className="text-xs font-medium text-emerald-700 dark:text-emerald-300">
                  Available {selectedProvider === "pinecone" ? "Indexes" : "Collections"} ({availableItems.length})
                </span>
              </div>
              <div className="flex flex-wrap gap-1.5">
                {availableItems.map((item, i) => {
                  const name = typeof item === "string" ? item : (item.name || item);
                  return (
                    <button
                      key={i}
                      onClick={() => {
                        const fieldKey = selectedProvider === "pinecone" ? "index_name" : "collection";
                        setSimFieldValues((prev) => ({ ...prev, [fieldKey]: name }));
                        setShowSimulation(true);
                      }}
                      className="px-2.5 py-1 text-xs font-mono bg-emerald-100 dark:bg-emerald-800/30 text-emerald-700 dark:text-emerald-300 rounded-lg hover:bg-emerald-200 dark:hover:bg-emerald-800/50 transition-colors border border-emerald-200 dark:border-emerald-700"
                    >
                      {name}
                    </button>
                  );
                })}
              </div>
              <p className="text-[10px] text-emerald-600 dark:text-emerald-400 mt-1.5">Click an item to auto-fill the simulation below</p>
            </div>
          )}
        </div>
      )}

      {/* Simulation section */}
      {result?.success && (
        <div className="border border-slate-200 dark:border-slate-700 rounded-lg overflow-hidden">
          <button
            onClick={() => setShowSimulation(!showSimulation)}
            className="w-full flex items-center justify-between p-3 bg-slate-50 dark:bg-slate-700/50 hover:bg-slate-100 dark:hover:bg-slate-700 transition-colors"
          >
            <div className="flex items-center gap-2">
              <Play className="w-4 h-4 text-teal-600 dark:text-teal-400" />
              <span className="text-sm font-medium text-slate-700 dark:text-slate-300">Run RAG Pipeline Simulation</span>
            </div>
            {showSimulation ? <ChevronDown className="w-4 h-4 text-slate-400" /> : <ChevronRight className="w-4 h-4 text-slate-400" />}
          </button>

          {showSimulation && (
            <div className="p-4 space-y-3">
              <p className="text-xs text-slate-500 dark:text-slate-400">
                Send a real query through the RAG pipeline to test document retrieval, policy enforcement, and stage-by-stage processing.
              </p>

              {currentProvider.simulationFields.map((field) => (
                <div key={field.key}>
                  <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">
                    {field.label} {field.required && <span className="text-red-500">*</span>}
                  </label>
                  <input
                    type={field.type || "text"}
                    value={simFieldValues[field.key] || ""}
                    onChange={(e) => handleSimFieldChange(field.key, e.target.value)}
                    placeholder={field.placeholder}
                    className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm font-mono focus:ring-2 focus:ring-teal-500 focus:border-transparent"
                  />
                  {field.helpText && <p className="text-[10px] text-slate-400 mt-0.5">{field.helpText}</p>}
                </div>
              ))}

              <button
                onClick={handleSimulate}
                disabled={simulating}
                className="flex items-center gap-2 px-4 py-2 bg-purple-600 hover:bg-purple-700 disabled:bg-purple-400 text-white text-sm font-medium rounded-lg transition-colors"
              >
                {simulating ? <RefreshCw className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
                {simulating ? "Running..." : "Run Simulation"}
              </button>

              {/* Simulation result */}
              {simResult && (
                <div className={`p-4 rounded-lg border ${
                  simResult.status < 400
                    ? "bg-emerald-50 dark:bg-emerald-900/20 border-emerald-200 dark:border-emerald-800"
                    : "bg-red-50 dark:bg-red-900/20 border-red-200 dark:border-red-800"
                }`}>
                  <div className="flex items-center gap-3 mb-3">
                    <span className={`px-2.5 py-1 rounded-lg text-xs font-mono font-semibold ${
                      simResult.status < 400 ? "bg-emerald-100 dark:bg-emerald-800/30 text-emerald-700 dark:text-emerald-300" : "bg-red-100 dark:bg-red-800/30 text-red-700 dark:text-red-300"
                    }`}>
                      HTTP {simResult.status}
                    </span>
                    <span className="text-xs text-slate-500 font-mono">{simResult.elapsed}ms</span>
                    {simResult.pipelineAudit?.final_action && (
                      <span className={`px-2 py-0.5 rounded text-[10px] font-mono font-semibold ${
                        simResult.pipelineAudit.final_action === "allow" ? "bg-emerald-100 dark:bg-emerald-800/30 text-emerald-700" :
                        simResult.pipelineAudit.final_action === "block" ? "bg-red-100 dark:bg-red-800/30 text-red-700" :
                        "bg-amber-100 dark:bg-amber-800/30 text-amber-700"
                      }`}>
                        {simResult.pipelineAudit.final_action.toUpperCase()}
                      </span>
                    )}
                  </div>

                  {simResult.error && (
                    <div className="text-xs text-red-600 dark:text-red-400 mb-2 font-mono">{simResult.error}</div>
                  )}

                  <div className="grid grid-cols-3 gap-2 text-center">
                    <div className="bg-white dark:bg-slate-800 rounded-lg p-2 border border-slate-200 dark:border-slate-700">
                      <div className="text-[10px] text-slate-500">Docs Returned</div>
                      <div className="text-sm font-mono font-bold text-slate-900 dark:text-slate-100">{simResult.docsReturned}</div>
                    </div>
                    <div className="bg-white dark:bg-slate-800 rounded-lg p-2 border border-slate-200 dark:border-slate-700">
                      <div className="text-[10px] text-slate-500">Total Retrieved</div>
                      <div className="text-sm font-mono font-bold text-slate-900 dark:text-slate-100">{simResult.totalRetrieved}</div>
                    </div>
                    <div className="bg-white dark:bg-slate-800 rounded-lg p-2 border border-slate-200 dark:border-slate-700">
                      <div className="text-[10px] text-slate-500">Filtered Out</div>
                      <div className="text-sm font-mono font-bold text-amber-600">{simResult.filteredCount}</div>
                    </div>
                  </div>

                  {/* Pipeline audit stages */}
                  {simResult.pipelineAudit?.stages?.length > 0 && (
                    <div className="mt-3 pt-3 border-t border-slate-200 dark:border-slate-700">
                      <div className="text-xs font-medium text-slate-600 dark:text-slate-400 mb-2">Pipeline Stage Trace</div>
                      <div className="flex items-center gap-1">
                        {simResult.pipelineAudit.stages.map((stage, i) => {
                          const actionColor = stage.action === "allow" ? "bg-emerald-500" : stage.action === "block" ? "bg-red-500" : stage.action === "rewrite" ? "bg-blue-500" : "bg-amber-500";
                          return (
                            <div key={i} className="flex items-center gap-1">
                              <div className="text-center">
                                <div className={`w-8 h-8 rounded-full ${actionColor} flex items-center justify-center`}>
                                  <span className="text-[9px] text-white font-bold">{(stage.name || "?").charAt(0).toUpperCase()}</span>
                                </div>
                                <div className="text-[8px] text-slate-500 mt-0.5">{stage.name}</div>
                                <div className="text-[8px] text-slate-400">{stage.latency_ms?.toFixed(0) || 0}ms</div>
                              </div>
                              {i < simResult.pipelineAudit.stages.length - 1 && (
                                <div className="w-3 h-px bg-slate-300 dark:bg-slate-600" />
                              )}
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
