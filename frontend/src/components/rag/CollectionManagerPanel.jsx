import { useState, useEffect, useCallback } from "react";
import { Database, Plus, Trash2, RefreshCw, Loader2, AlertTriangle, CheckCircle, List, FolderOpen } from "lucide-react";
import { InfoTooltip } from "../InfoTooltip";
import { useAuth } from "../../context/AuthContext";

// Phase 1 F-3.1: collection management now flows through the Django admin
// proxy (``/api/admin/gateway/rag/collections/``) instead of the per-tenant
// gateway Bearer key. Django enforces IsAdminOrSuperuser, stamps the
// caller's organization-derived project_id, and forwards to the gateway
// with the server-side internal key.

const PROVIDERS = [
  { value: "chroma", label: "ChromaDB" },
  { value: "pinecone", label: "Pinecone" },
  { value: "milvus", label: "Milvus" },
];

const PROXY_URL = "/api/admin/gateway/rag/collections/";

export function CollectionManagerPanel() {
  const { fetchWithAuth } = useAuth();
  const [collections, setCollections] = useState({});
  const [loading, setLoading] = useState(false);
  const [selectedProvider, setSelectedProvider] = useState("chroma");
  const [newName, setNewName] = useState("");
  const [creating, setCreating] = useState(false);
  const [deleting, setDeleting] = useState(null);
  const [error, setError] = useState(null);
  const [success, setSuccess] = useState(null);

  const unwrap = async (res) => {
    let body = null;
    try { body = await res.json(); } catch { body = null; }
    if (!res.ok || !body || body.status !== "ok") {
      const msg = body?.data?.message || body?.data?.error || `HTTP ${res.status}`;
      throw new Error(msg);
    }
    return body.data || {};
  };

  const fetchCollections = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetchWithAuth(PROXY_URL);
      const data = await unwrap(res);
      setCollections(data.collections || {});
    } catch (err) {
      setError(`Failed to list collections: ${err.message}`);
    } finally {
      setLoading(false);
    }
  }, [fetchWithAuth]);

  useEffect(() => { fetchCollections(); }, [fetchCollections]);

  const handleCreate = async () => {
    if (!newName.trim()) return;
    setCreating(true);
    setError(null);
    setSuccess(null);
    try {
      const res = await fetchWithAuth(PROXY_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ collection: newName.trim(), vector_db_type: selectedProvider }),
      });
      await unwrap(res);
      setSuccess(`Collection "${newName.trim()}" created in ${selectedProvider}.`);
      setNewName("");
      await fetchCollections();
    } catch (err) {
      setError(`Create failed: ${err.message}`);
    } finally {
      setCreating(false);
    }
  };

  const handleDelete = async (provider, name) => {
    // Bundle Z3 — destructive op requires explicit confirmation. The
    // delete button is one click away from the row and there is no
    // undo on the backend (collection drop is permanent), so a stray
    // click previously vaporised the collection. Native ``confirm`` is
    // sufficient here; we already use it for vector-policy deletion in
    // VectorPolicyPanel.jsx so the UX stays consistent across the RAG
    // surface.
    if (!window.confirm(
      `Delete collection "${name}" from ${provider}? ` +
      `All vectors and metadata in this collection will be permanently lost.`,
    )) {
      return;
    }
    setDeleting(`${provider}:${name}`);
    setError(null);
    setSuccess(null);
    try {
      const res = await fetchWithAuth(PROXY_URL, {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ collection: name, vector_db_type: provider }),
      });
      await unwrap(res);
      setSuccess(`Collection "${name}" deleted.`);
      await fetchCollections();
    } catch (err) {
      setError(`Delete failed: ${err.message}`);
    } finally {
      setDeleting(null);
    }
  };

  const allCollections = Object.entries(collections).flatMap(([provider, items]) =>
    (items || []).map((item) => ({
      provider,
      name: typeof item === "string" ? item : item.name || item,
    }))
  );

  return (
    <div className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl shadow-sm p-6 space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-base font-semibold text-slate-900 dark:text-slate-100 flex items-center gap-2">
            <FolderOpen className="w-4 h-4 text-indigo-600" />
            Collection Manager
          </h3>
          <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
            Create, browse, and delete vector DB collections across providers
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={fetchCollections} disabled={loading} className="p-1.5 rounded-lg hover:bg-slate-100 dark:hover:bg-slate-700 transition-colors" title="Refresh">
            <RefreshCw className={`w-4 h-4 text-slate-400 ${loading ? "animate-spin" : ""}`} />
          </button>
          <InfoTooltip text="Collections are namespaced by project ID for tenant isolation. Create indexes here and use them in ingestion and query panels." />
        </div>
      </div>

      {error && (
        <div className="p-3 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg text-xs text-red-700 dark:text-red-300 flex items-start gap-2">
          <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" /> <div>{error}</div>
        </div>
      )}
      {success && (
        <div className="p-3 bg-emerald-50 dark:bg-emerald-900/20 border border-emerald-200 dark:border-emerald-800 rounded-lg text-xs text-emerald-700 dark:text-emerald-300 flex items-start gap-2">
          <CheckCircle className="w-4 h-4 flex-shrink-0 mt-0.5" /> <div>{success}</div>
        </div>
      )}

      {/* Create collection */}
      <div className="p-4 border border-dashed border-slate-300 dark:border-slate-600 rounded-lg space-y-3">
        <div className="text-xs font-medium text-slate-600 dark:text-slate-400">Create New Collection</div>
        <div className="grid grid-cols-3 gap-3">
          <div>
            <label className="block text-[11px] text-slate-500 dark:text-slate-400 mb-1">Provider</label>
            <select
              value={selectedProvider}
              onChange={(e) => setSelectedProvider(e.target.value)}
              className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-1.5 border border-slate-200 dark:border-slate-700 rounded-lg text-xs focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
            >
              {PROVIDERS.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}
            </select>
          </div>
          <div className="col-span-2">
            <label className="block text-[11px] text-slate-500 dark:text-slate-400 mb-1">Collection Name</label>
            <div className="flex gap-2">
              <input
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                placeholder="my_documents"
                onKeyDown={(e) => e.key === "Enter" && handleCreate()}
                className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 flex-1 px-3 py-1.5 border border-slate-200 dark:border-slate-700 rounded-lg text-xs font-mono focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
              />
              <button
                onClick={handleCreate}
                disabled={creating || !newName.trim()}
                className="flex items-center gap-1 px-4 py-1.5 bg-indigo-600 hover:bg-indigo-700 disabled:bg-indigo-400 text-white text-xs font-medium rounded-lg transition-colors"
              >
                {creating ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Plus className="w-3.5 h-3.5" />}
                Create
              </button>
            </div>
          </div>
        </div>
      </div>

      {/* Existing collections */}
      <div>
        <div className="flex items-center gap-2 mb-2">
          <List className="w-4 h-4 text-slate-400" />
          <span className="text-xs font-medium text-slate-600 dark:text-slate-400">
            Existing Collections ({allCollections.length})
          </span>
        </div>
        {loading ? (
          <div className="flex items-center justify-center py-6"><Loader2 className="w-5 h-5 animate-spin text-slate-400" /></div>
        ) : allCollections.length === 0 ? (
          <div className="text-center py-6 text-xs text-slate-400">No collections found. Create one above or check your gateway connection.</div>
        ) : (
          <div className="space-y-1.5 max-h-64 overflow-y-auto">
            {allCollections.map(({ provider, name }) => {
              const isDeleting = deleting === `${provider}:${name}`;
              return (
                <div key={`${provider}:${name}`} className="flex items-center justify-between px-3 py-2 rounded-lg bg-slate-50 dark:bg-slate-700/30 border border-slate-200 dark:border-slate-700 group">
                  <div className="flex items-center gap-2">
                    <Database className="w-3.5 h-3.5 text-slate-400" />
                    <span className="text-xs font-mono text-slate-900 dark:text-slate-100">{name}</span>
                    <span className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-slate-200 dark:bg-slate-600 text-slate-600 dark:text-slate-300">{provider}</span>
                  </div>
                  <button
                    onClick={() => handleDelete(provider, name)}
                    disabled={isDeleting}
                    className="opacity-0 group-hover:opacity-100 flex items-center gap-1 px-2 py-1 text-[10px] text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20 rounded transition-all"
                  >
                    {isDeleting ? <Loader2 className="w-3 h-3 animate-spin" /> : <Trash2 className="w-3 h-3" />}
                    Delete
                  </button>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
