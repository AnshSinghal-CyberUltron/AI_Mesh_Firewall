import { useState, useEffect, useCallback, useRef } from "react";
import {
  Plus, Pencil, Trash2, X, Loader2, Database, CheckCircle, AlertTriangle,
  RefreshCw, Upload, Shield, Lock,
} from "lucide-react";
import { useAuth } from "../context/AuthContext";
import { InfoTooltip } from "./InfoTooltip";
import { PolicyDomainSwitcher } from "./PolicyDomainSwitcher";
import { DEFAULT_VECTOR_PROVIDER, VECTOR_PROVIDERS } from "../constants/vectorProviders";

const DB_TYPE_LABELS = {
  chroma: "Chroma (BYOK)",
  pinecone: "Pinecone",
  milvus: "Milvus",
  custom: "Custom",
};

const ACTION_CONFIG = {
  allow: { bg: "bg-emerald-100 dark:bg-emerald-800/30", text: "text-emerald-700" },
  deny: { bg: "bg-red-100 dark:bg-red-800/30", text: "text-red-700" },
  monitor: { bg: "bg-blue-100 dark:bg-blue-800/30", text: "text-blue-700" },
};

const EMPTY_FORM = {
  name: "",
  project_id: "",
  collection_name: "",
  vector_db_type: DEFAULT_VECTOR_PROVIDER,
  namespace: "",
  default_action: "deny",
  allowed_operations: ["query"],
  max_results_per_query: 10,
  max_query_length: 2000,
  sensitive_fields: [],
  require_context_scan: true,
  block_sensitive_documents: true,
  embedding_model: "",
  embedding_dimension: "",
  anomaly_distance_threshold: 0.85,
  enabled: true,
};

const VALID_OPS = ["query", "insert", "update", "delete"];

function normalizeNextUrl(nextUrl) {
  if (!nextUrl) return null;
  if (nextUrl.startsWith("http://") || nextUrl.startsWith("https://")) {
    try {
      const parsed = new URL(nextUrl);
      return `${parsed.pathname}${parsed.search}`;
    } catch {
      return null;
    }
  }
  return nextUrl;
}

async function readErrorResponse(res) {
  const text = await res.text();
  if (!text) return `HTTP ${res.status}`;
  try {
    const body = JSON.parse(text);
    return body.detail || body.non_field_errors?.[0] || text;
  } catch {
    return text;
  }
}

function ActionBadge({ action }) {
  const cfg = ACTION_CONFIG[action] || ACTION_CONFIG.monitor;
  return (
    <span className={`inline-flex px-2 py-0.5 rounded-full text-[10px] font-semibold uppercase ${cfg.bg} ${cfg.text}`}>
      {action}
    </span>
  );
}

function VectorPolicyModal({ title, form, setForm, onSubmit, onClose, submitting, error, isEdit, onRequestGenericCreate }) {
  const toggleOp = (op) => {
    const ops = form.allowed_operations || [];
    if (ops.includes(op)) {
      setForm({ ...form, allowed_operations: ops.filter((o) => o !== op) });
    } else {
      setForm({ ...form, allowed_operations: [...ops, op] });
    }
  };

  // Bundle Z2 — close-while-submitting guard. The form's Create/Update
  // request is already in flight; if the user backdrop-clicks or hits the
  // X mid-flight we previously unmounted the modal and lost the success
  // toast / error surface, leaving the panel in an inconsistent state and
  // potentially producing a duplicate POST on the next click. Lock both
  // dismissal paths while ``submitting`` is true; the Cancel button stays
  // active so the user can still bail out before submission starts.
  const guardedClose = submitting ? () => {} : onClose;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="fixed inset-0 bg-black/40" onClick={guardedClose} />
      <div className="relative bg-white dark:bg-slate-800 rounded-xl shadow-xl border border-slate-200 dark:border-slate-700 w-full max-w-xl p-6 max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-base font-semibold text-slate-900 dark:text-slate-100">{title}</h3>
          <button
            onClick={guardedClose}
            disabled={submitting}
            className="p-1 hover:bg-slate-100 dark:hover:bg-slate-700 rounded transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
            aria-label="Close"
            title="Close"
          >
            <X className="w-4 h-4 text-slate-500 dark:text-slate-400" />
          </button>
        </div>
        {error && (
          <div className="mb-4 p-3 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg text-xs text-red-700">
            {error}
          </div>
        )}
        <form onSubmit={onSubmit} className="space-y-4">
          {!isEdit && onRequestGenericCreate ? (
            <PolicyDomainSwitcher
              value="vector"
              onChange={(domain) => {
                // Switching away from Vector hands off to the generic policy
                // modal (Vector is a separate resource and stays its own form).
                if (domain !== "vector") onRequestGenericCreate(domain);
              }}
            />
          ) : null}
          <div>
            <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">Policy Name *</label>
            <input
              type="text"
              required
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder="e.g. Customer Knowledge Base Policy"
              className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm focus:ring-2 focus:ring-teal-500 focus:border-transparent"
            />
          </div>

          <div className="grid grid-cols-3 gap-3">
            <div>
              <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">Project ID *</label>
              <input
                type="text"
                required
                disabled={isEdit}
                value={form.project_id}
                onChange={(e) => setForm({ ...form, project_id: e.target.value })}
                placeholder="e.g. proj-1"
                className="w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm focus:ring-2 focus:ring-teal-500 focus:border-transparent disabled:bg-slate-50 dark:bg-slate-800/50 disabled:text-slate-400"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">Collection Name *</label>
              <input
                type="text"
                required
                disabled={isEdit}
                value={form.collection_name}
                onChange={(e) => setForm({ ...form, collection_name: e.target.value })}
                placeholder="e.g. docs_v2"
                className="w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm focus:ring-2 focus:ring-teal-500 focus:border-transparent disabled:bg-slate-50 dark:bg-slate-800/50 disabled:text-slate-400"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">Vector DB Type</label>
              <select
                value={form.vector_db_type}
                disabled={isEdit}
                onChange={(e) => setForm({ ...form, vector_db_type: e.target.value })}
                aria-label="Vector DB type"
                className="w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm focus:ring-2 focus:ring-teal-500 disabled:bg-slate-50 dark:bg-slate-800/50 disabled:text-slate-400"
              >
                {VECTOR_PROVIDERS.map((p) => (
                  <option key={p.value} value={p.value}>{p.label}</option>
                ))}
              </select>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">Default Action</label>
              <select
                value={form.default_action}
                onChange={(e) => setForm({ ...form, default_action: e.target.value })}
                aria-label="Default action"
                className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm focus:ring-2 focus:ring-teal-500"
              >
                <option value="deny">Deny</option>
                <option value="allow">Allow</option>
                <option value="monitor">Monitor</option>
              </select>
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">Namespace</label>
              <input
                type="text"
                value={form.namespace}
                onChange={(e) => setForm({ ...form, namespace: e.target.value })}
                placeholder="Sub-namespace (optional)"
                className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm focus:ring-2 focus:ring-teal-500 focus:border-transparent"
              />
            </div>
          </div>

          <div>
            <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">Allowed Operations</label>
            <div className="flex items-center gap-3">
              {VALID_OPS.map((op) => (
                <label key={op} className="flex items-center gap-1.5 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={(form.allowed_operations || []).includes(op)}
                    onChange={() => toggleOp(op)}
                    className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-3.5 h-3.5 rounded border-slate-300 dark:border-slate-600 text-teal-600 focus:ring-teal-500"
                  />
                  <span className="text-xs text-slate-700 dark:text-slate-300 capitalize">{op}</span>
                </label>
              ))}
            </div>
          </div>

          <div className="grid grid-cols-3 gap-3">
            <div>
              <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">Max Results/Query</label>
              <input
                type="number"
                min="1"
                max="100"
                value={form.max_results_per_query}
                onChange={(e) => setForm({ ...form, max_results_per_query: parseInt(e.target.value, 10) || 10 })}
                className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm focus:ring-2 focus:ring-teal-500 focus:border-transparent"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">Max Query Length</label>
              <input
                type="number"
                min="100"
                max="10000"
                value={form.max_query_length}
                onChange={(e) => setForm({ ...form, max_query_length: parseInt(e.target.value, 10) || 2000 })}
                className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm focus:ring-2 focus:ring-teal-500 focus:border-transparent"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">Anomaly Threshold</label>
              <input
                type="number"
                step="0.05"
                min="0"
                max="1"
                value={form.anomaly_distance_threshold}
                onChange={(e) => setForm({ ...form, anomaly_distance_threshold: parseFloat(e.target.value) || 0.85 })}
                className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm focus:ring-2 focus:ring-teal-500 focus:border-transparent"
              />
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">Embedding Model</label>
              <input
                type="text"
                value={form.embedding_model}
                onChange={(e) => setForm({ ...form, embedding_model: e.target.value })}
                placeholder="e.g. text-embedding-ada-002"
                className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm focus:ring-2 focus:ring-teal-500 focus:border-transparent"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">Embedding Dimension</label>
              <input
                type="number"
                value={form.embedding_dimension}
                onChange={(e) => setForm({ ...form, embedding_dimension: e.target.value ? parseInt(e.target.value, 10) : "" })}
                placeholder="e.g. 1536"
                className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm focus:ring-2 focus:ring-teal-500 focus:border-transparent"
              />
            </div>
          </div>

          <div>
            <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">Sensitive Fields (comma-separated)</label>
            <input
              type="text"
              value={Array.isArray(form.sensitive_fields) ? form.sensitive_fields.join(", ") : ""}
              onChange={(e) =>
                setForm({
                  ...form,
                  sensitive_fields: e.target.value.split(",").map((f) => f.trim()).filter(Boolean),
                })
              }
              placeholder="e.g. ssn, email, phone"
              className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm focus:ring-2 focus:ring-teal-500 focus:border-transparent"
            />
          </div>

          <div className="flex items-center gap-6">
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={form.require_context_scan}
                onChange={(e) => setForm({ ...form, require_context_scan: e.target.checked })}
                className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-4 h-4 rounded border-slate-300 dark:border-slate-600 text-teal-600 focus:ring-teal-500"
              />
              <span className="text-xs font-medium text-slate-700 dark:text-slate-300">Require Context Scan</span>
            </label>
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={form.block_sensitive_documents}
                onChange={(e) => setForm({ ...form, block_sensitive_documents: e.target.checked })}
                className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-4 h-4 rounded border-slate-300 dark:border-slate-600 text-teal-600 focus:ring-teal-500"
              />
              <span className="text-xs font-medium text-slate-700 dark:text-slate-300">Block Sensitive Documents</span>
            </label>
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={form.enabled}
                onChange={(e) => setForm({ ...form, enabled: e.target.checked })}
                className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-4 h-4 rounded border-slate-300 dark:border-slate-600 text-teal-600 focus:ring-teal-500"
              />
              <span className="text-xs font-medium text-slate-700 dark:text-slate-300">Enabled</span>
            </label>
          </div>

          <div className="flex items-center justify-end gap-2 pt-2">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 text-sm text-slate-600 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-700 rounded-lg transition-colors"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={submitting}
              className="flex items-center gap-1.5 px-4 py-2 bg-teal-600 hover:bg-teal-700 disabled:bg-teal-400 text-white text-sm font-medium rounded-lg transition-colors"
            >
              {submitting && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
              {isEdit ? "Update Policy" : "Create Policy"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

export function VectorPolicyPanel({
  title = "Vector Collection Policies",
  description = "Manage access policies for vector database collections",
  externalCreateSignal = 0,
  // Invoked when the user picks a non-Vector domain in the in-panel switcher.
  // The parent closes this Vector modal and opens the generic policy modal for
  // the chosen domain (global/pipeline/rag/mcp -> /api/policies/).
  onRequestGenericCreate,
}) {
  const { fetchWithAuth, user } = useAuth();
  // Bundle Z4 — admin gate on the Compile button. The backend already
  // returns 403 for non-admins (IsAdminUser on VectorPolicyCompileView)
  // but rendering the affordance for users who cannot use it produced a
  // confusing "Compilation failed: <html>" toast. Hide unless the
  // authenticated user is a platform_admin or Django superuser — same
  // role gate used by Sidebar.jsx for admin-only nav items.
  const isAdminUser = !!user && (user.is_superuser || (user.roles || []).includes("platform_admin"));
  const [policies, setPolicies] = useState([]);
  const [loading, setLoading] = useState(true);
  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [editModalOpen, setEditModalOpen] = useState(false);
  const [editPolicyId, setEditPolicyId] = useState(null);
  const [form, setForm] = useState({ ...EMPTY_FORM });
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState(null);
  const [actionLoading, setActionLoading] = useState(null);
  const [compileStatus, setCompileStatus] = useState(null);
  const [compiling, setCompiling] = useState(false);
  const [loadError, setLoadError] = useState(null);
  const lastHandledExternalCreateSignal = useRef(0);

  const fetchPolicies = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      let nextUrl = "/api/vector-policies/";
      const allPolicies = [];
      let pageCount = 0;

      while (nextUrl && pageCount < 20) {
        const res = await fetchWithAuth(nextUrl);
        if (!res.ok) {
          throw new Error(await readErrorResponse(res));
        }
        const data = await res.json();
        if (Array.isArray(data)) {
          allPolicies.push(...data);
          nextUrl = null;
        } else {
          allPolicies.push(...(data.results || []));
          nextUrl = normalizeNextUrl(data.next || null);
        }
        pageCount += 1;
      }

      setPolicies(allPolicies);
    } catch (err) {
      setPolicies([]);
      setLoadError(err.message || "Failed to load vector policies");
    } finally {
      setLoading(false);
    }
  }, [fetchWithAuth]);

  useEffect(() => {
    fetchPolicies();
  }, [fetchPolicies]);

  useEffect(() => {
    if (!externalCreateSignal) return;
    if (externalCreateSignal <= lastHandledExternalCreateSignal.current) return;
    lastHandledExternalCreateSignal.current = externalCreateSignal;
    setForm({ ...EMPTY_FORM });
    setFormError(null);
    setCreateModalOpen(true);
  }, [externalCreateSignal]);

  const handleCreate = async (e) => {
    e.preventDefault();
    setFormError(null);
    setSubmitting(true);
    try {
      const payload = { ...form };
      if (payload.embedding_dimension === "" || payload.embedding_dimension === null) {
        delete payload.embedding_dimension;
      }
      const res = await fetchWithAuth("/api/vector-policies/", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        const text = await res.text();
        let errBody = {};
        try { errBody = JSON.parse(text); } catch {}
        const msg = errBody.detail || errBody.collection_name?.[0] || errBody.name?.[0] || text || `HTTP ${res.status}`;
        throw new Error(msg);
      }
      setCreateModalOpen(false);
      setForm({ ...EMPTY_FORM });
      await fetchPolicies();
    } catch (err) {
      setFormError(err.message || "Failed to create policy");
    } finally {
      setSubmitting(false);
    }
  };

  const handleEdit = async (e) => {
    e.preventDefault();
    setFormError(null);
    setSubmitting(true);
    try {
      const payload = {
        name: form.name,
        namespace: form.namespace,
        default_action: form.default_action,
        allowed_operations: form.allowed_operations,
        max_results_per_query: form.max_results_per_query,
        max_query_length: form.max_query_length,
        sensitive_fields: form.sensitive_fields,
        require_context_scan: form.require_context_scan,
        block_sensitive_documents: form.block_sensitive_documents,
        embedding_model: form.embedding_model,
        anomaly_distance_threshold: form.anomaly_distance_threshold,
        enabled: form.enabled,
      };
      if (form.embedding_dimension !== "" && form.embedding_dimension !== null) {
        payload.embedding_dimension = form.embedding_dimension;
      }
      const res = await fetchWithAuth(`/api/vector-policies/${editPolicyId}/`, {
        method: "PATCH",
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        const text = await res.text();
        let errBody = {};
        try { errBody = JSON.parse(text); } catch {}
        throw new Error(errBody.detail || text || `HTTP ${res.status}`);
      }
      setEditModalOpen(false);
      setEditPolicyId(null);
      setForm({ ...EMPTY_FORM });
      await fetchPolicies();
    } catch (err) {
      setFormError(err.message || "Failed to update policy");
    } finally {
      setSubmitting(false);
    }
  };

  const openEditModal = (policy) => {
    setForm({
      name: policy.name || "",
      project_id: policy.project_id || "",
      collection_name: policy.collection_name || "",
      vector_db_type: policy.vector_db_type || DEFAULT_VECTOR_PROVIDER,
      namespace: policy.namespace || "",
      default_action: policy.default_action || "deny",
      allowed_operations: policy.allowed_operations || ["query"],
      max_results_per_query: policy.max_results_per_query || 10,
      max_query_length: policy.max_query_length || 2000,
      sensitive_fields: policy.sensitive_fields || [],
      require_context_scan: policy.require_context_scan !== false,
      block_sensitive_documents: policy.block_sensitive_documents !== false,
      embedding_model: policy.embedding_model || "",
      embedding_dimension: policy.embedding_dimension || "",
      anomaly_distance_threshold: policy.anomaly_distance_threshold || 0.85,
      enabled: policy.enabled !== false,
    });
    setEditPolicyId(policy.id);
    setFormError(null);
    setEditModalOpen(true);
  };

  const handleDelete = async (id) => {
    if (!window.confirm("Delete this vector policy? This cannot be undone.")) return;
    setActionLoading(id);
    // Bundle Z1 — surface delete failures. Previously the await on a
    // failing DELETE silently fell through to fetchPolicies(), so the
    // user saw the row reappear with no explanation. Treat any non-2xx
    // as an error, parse the JSON body for ``detail`` (DRF's standard
    // error shape), and route it through the same banner the load path
    // uses so the panel has a single error-display surface.
    try {
      const res = await fetchWithAuth(`/api/vector-policies/${id}/`, { method: "DELETE" });
      if (!res.ok) {
        throw new Error(await readErrorResponse(res));
      }
      setLoadError(null);
      await fetchPolicies();
    } catch (err) {
      setLoadError(err.message || "Failed to delete policy");
    } finally {
      setActionLoading(null);
    }
  };

  const handleCompile = async () => {
    setCompiling(true);
    setCompileStatus(null);
    try {
      const res = await fetchWithAuth("/api/vector-policies/compile/", { method: "POST" });
      if (res.ok) {
        setCompileStatus({ success: true });
      } else {
        // Bundle Z1 — parse JSON body for ``detail`` instead of dumping
        // raw HTML (auth/throttle responses) into the toast. Falls back
        // to status-code message if the response is empty or non-JSON.
        setCompileStatus({
          success: false,
          error: await readErrorResponse(res),
        });
      }
    } catch (err) {
      setCompileStatus({ success: false, error: err.message });
    } finally {
      setCompiling(false);
    }
  };

  const formatDate = (iso) => {
    if (!iso) return "--";
    return new Date(iso).toISOString().substring(0, 16).replace("T", " ");
  };

  return (
    <div className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl shadow-sm p-6">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h3 className="text-base font-semibold text-slate-900 dark:text-slate-100 flex items-center">{title}<InfoTooltip title="How to Use">{"Controls access to vector database collections used in RAG pipelines. Set namespace isolation, embedding dimension limits, and anomaly detection thresholds per collection."}</InfoTooltip></h3>
          <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
            {description}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {isAdminUser && (
            <button
              onClick={handleCompile}
              disabled={compiling}
              className="flex items-center gap-1.5 px-3 py-2 bg-slate-100 dark:bg-slate-700 hover:bg-slate-200 text-slate-700 dark:text-slate-300 text-xs font-medium rounded-lg transition-colors disabled:opacity-50"
              title="Compile and push vector policies to gateway"
            >
              {compiling ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Upload className="w-3.5 h-3.5" />}
              Compile
            </button>
          )}
          <button
            onClick={() => {
              setForm({ ...EMPTY_FORM });
              setFormError(null);
              setCreateModalOpen(true);
            }}
            className="flex items-center gap-1.5 px-3 py-2 bg-teal-600 hover:bg-teal-700 text-white text-xs font-medium rounded-lg transition-colors"
          >
            <Plus className="w-3.5 h-3.5" />
            Create Policy
          </button>
        </div>
      </div>

      {compileStatus && (
        <div className={`mb-4 p-3 rounded-lg text-xs flex items-start gap-2 ${compileStatus.success ? "bg-emerald-50 dark:bg-emerald-900/20 border border-emerald-200 dark:border-emerald-800 text-emerald-700" : "bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 text-red-700"}`}>
          {compileStatus.success ? <CheckCircle className="w-4 h-4 flex-shrink-0 mt-0.5" /> : <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" />}
          <div>
            {compileStatus.success ? "Vector policies compiled and pushed to gateway." : `Compilation failed: ${compileStatus.error}`}
          </div>
          <button onClick={() => setCompileStatus(null)} className="ml-auto p-0.5 hover:bg-slate-100 dark:hover:bg-slate-700/60 rounded transition-colors" aria-label="Dismiss compile status" title="Dismiss">
            <X className="w-3 h-3" />
          </button>
        </div>
      )}

      {loadError ? (
        <div className="mb-4 p-3 rounded-lg text-xs flex items-start gap-2 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 text-red-700">
          <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" />
          <span>{loadError}</span>
        </div>
      ) : null}

      {loading ? (
        <div className="flex items-center justify-center py-8">
          <Loader2 className="w-5 h-5 text-teal-500 animate-spin" />
          <span className="ml-2 text-sm text-slate-500 dark:text-slate-400">Loading vector policies...</span>
        </div>
      ) : policies.length === 0 ? (
        <div className="text-center py-8 text-sm text-slate-500 dark:text-slate-400">
          No vector policies created. Create one to control access to vector database collections.
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-slate-50 dark:bg-slate-800/50 border-b border-slate-200 dark:border-slate-700">
                <th className="px-3 py-2.5 text-left text-xs font-semibold text-slate-600 dark:text-slate-300 uppercase">Name</th>
                <th className="px-3 py-2.5 text-left text-xs font-semibold text-slate-600 dark:text-slate-300 uppercase">Collection</th>
                <th className="px-3 py-2.5 text-left text-xs font-semibold text-slate-600 dark:text-slate-300 uppercase">DB Type</th>
                <th className="px-3 py-2.5 text-left text-xs font-semibold text-slate-600 dark:text-slate-300 uppercase">Action</th>
                <th className="px-3 py-2.5 text-left text-xs font-semibold text-slate-600 dark:text-slate-300 uppercase">Ops</th>
                <th className="px-3 py-2.5 text-left text-xs font-semibold text-slate-600 dark:text-slate-300 uppercase">Max Results</th>
                <th className="px-3 py-2.5 text-left text-xs font-semibold text-slate-600 dark:text-slate-300 uppercase">Guards</th>
                <th className="px-3 py-2.5 text-left text-xs font-semibold text-slate-600 dark:text-slate-300 uppercase">Status</th>
                <th className="px-3 py-2.5 text-right text-xs font-semibold text-slate-600 dark:text-slate-300 uppercase">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-700">
              {policies.map((p) => (
                <tr key={p.id} className="hover:bg-slate-50 dark:hover:bg-slate-700 transition-colors">
                  <td className="px-3 py-2.5 text-xs font-medium text-slate-800 dark:text-slate-200">{p.name}</td>
                  <td className="px-3 py-2.5">
                    <div className="text-xs font-mono text-slate-600 dark:text-slate-400">{p.collection_name}</div>
                    {p.namespace && <div className="text-[10px] text-slate-400">{p.namespace}</div>}
                  </td>
                  <td className="px-3 py-2.5 text-xs text-slate-600 dark:text-slate-400">
                    {DB_TYPE_LABELS[p.vector_db_type] || p.vector_db_type}
                  </td>
                  <td className="px-3 py-2.5"><ActionBadge action={p.default_action} /></td>
                  <td className="px-3 py-2.5">
                    <div className="flex flex-wrap gap-0.5">
                      {(p.allowed_operations || []).map((op, i) => (
                        <span key={i} className="px-1 py-0.5 bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-400 text-[10px] rounded font-mono capitalize">
                          {op}
                        </span>
                      ))}
                    </div>
                  </td>
                  <td className="px-3 py-2.5 text-xs text-slate-600 dark:text-slate-400">{p.max_results_per_query}</td>
                  <td className="px-3 py-2.5">
                    <div className="flex items-center gap-1">
                      {p.require_context_scan && (
                        <span className="px-1 py-0.5 bg-blue-50 dark:bg-blue-900/20 text-blue-600 text-[10px] rounded" title="Context Scan">
                          <Shield className="w-3 h-3 inline" />
                        </span>
                      )}
                      {p.block_sensitive_documents && (
                        <span className="px-1 py-0.5 bg-red-50 dark:bg-red-900/20 text-red-600 text-[10px] rounded" title="Block Sensitive">
                          <Lock className="w-3 h-3 inline" />
                        </span>
                      )}
                    </div>
                  </td>
                  <td className="px-3 py-2.5">
                    <span className={`inline-flex px-2 py-0.5 rounded-full text-[10px] font-medium ${p.enabled ? "bg-emerald-100 dark:bg-emerald-800/30 text-emerald-700" : "bg-slate-100 dark:bg-slate-700 text-slate-500 dark:text-slate-400"}`}>
                      {p.enabled ? "Active" : "Disabled"}
                    </span>
                  </td>
                  <td className="px-3 py-2.5">
                    <div className="flex items-center justify-end gap-1">
                      {actionLoading === p.id ? (
                        <Loader2 className="w-4 h-4 text-teal-500 animate-spin" />
                      ) : (
                        <>
                          <button
                            onClick={() => openEditModal(p)}
                            className="p-1.5 hover:bg-slate-200 rounded text-slate-500 dark:text-slate-400 transition-colors"
                            aria-label="Edit vector policy"
                            title="Edit Policy"
                          >
                            <Pencil className="w-3.5 h-3.5" />
                          </button>
                          <button
                            onClick={() => handleDelete(p.id)}
                            className="p-1.5 hover:bg-red-50 dark:hover:bg-red-900/20 rounded text-red-400 hover:text-red-600 transition-colors"
                            aria-label="Delete vector policy"
                            title="Delete Policy"
                          >
                            <Trash2 className="w-3.5 h-3.5" />
                          </button>
                        </>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {createModalOpen && (
        <VectorPolicyModal
          title="Create Vector Policy"
          form={form}
          setForm={setForm}
          onSubmit={handleCreate}
          onClose={() => setCreateModalOpen(false)}
          submitting={submitting}
          error={formError}
          isEdit={false}
          onRequestGenericCreate={
            onRequestGenericCreate
              ? (domain) => {
                  setCreateModalOpen(false);
                  onRequestGenericCreate(domain);
                }
              : undefined
          }
        />
      )}

      {editModalOpen && (
        <VectorPolicyModal
          title="Edit Vector Policy"
          form={form}
          setForm={setForm}
          onSubmit={handleEdit}
          onClose={() => { setEditModalOpen(false); setEditPolicyId(null); }}
          submitting={submitting}
          error={formError}
          isEdit={true}
        />
      )}
    </div>
  );
}
