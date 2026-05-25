import { useState, useEffect, useCallback } from "react";
import { Key, Database, CheckCircle, AlertTriangle, Loader2, Save, Trash2, RefreshCw } from "lucide-react";
import { InfoTooltip } from "../InfoTooltip";
import { useAuth } from "../../context/AuthContext";

const PROVIDERS = [
  { value: "chroma", label: "ChromaDB", description: "Open-source embedding database", fields: [
    { key: "connection_url", label: "ChromaDB URL", placeholder: "http://localhost:8000", required: true },
    { key: "api_key", label: "Auth Token (optional)", placeholder: "", type: "password" },
  ]},
  { value: "pinecone", label: "Pinecone", description: "Managed serverless vector DB", fields: [
    { key: "api_key", label: "API Key", placeholder: "pcsk_...", required: true, type: "password" },
    { key: "environment", label: "Environment", placeholder: "us-east-1" },
  ]},
  { value: "milvus", label: "Milvus", description: "High-performance vector database", fields: [
    { key: "connection_url", label: "Milvus URI", placeholder: "http://localhost:19530", required: true },
    { key: "api_key", label: "Token (optional)", placeholder: "", type: "password" },
  ]},
];

// Use relative URLs so the Vite dev-server proxy forwards /api → backend correctly
// (absolute URLs like http://localhost:8100 fail inside Docker containers).

export function VectorProviderConfigPanel() {
  const { fetchWithAuth } = useAuth();
  const [configs, setConfigs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(null);
  const [error, setError] = useState(null);
  const [success, setSuccess] = useState(null);
  const [editForms, setEditForms] = useState({});

  const fetchConfigs = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetchWithAuth(`/api/vector-providers/`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const responseData = await res.json();
      // Defensive parsing: ensure data is always an array
      // Handle cases where API returns: array, {results: [...]}, or {<provider_type>: {...}}
      let data;
      if (Array.isArray(responseData)) {
        data = responseData;
      } else if (responseData && typeof responseData === "object") {
        if (Array.isArray(responseData.results)) {
          data = responseData.results;
        } else if (Array.isArray(responseData.data)) {
          data = responseData.data;
        } else {
          // Try to convert object with provider types as keys into an array
          data = Object.values(responseData).filter(item => item && typeof item === "object" && item.provider_type);
        }
      } else {
        data = [];
      }
      setConfigs(data);
      // Initialize edit forms for each provider (existing + new)
      const forms = {};
      PROVIDERS.forEach((p) => {
        const existing = data.find((c) => c.provider_type === p.value);
        if (existing) {
          forms[p.value] = { id: existing.id, connection_url: existing.connection_url || "", api_key: "", environment: existing.environment || "", embedding_model: existing.embedding_model || "text-embedding-3-small", is_active: existing.is_active, api_key_set: existing.api_key_set, display_name: existing.display_name || "" };
        } else {
          forms[p.value] = { id: null, connection_url: "", api_key: "", environment: "", embedding_model: "text-embedding-3-small", is_active: true, api_key_set: false, display_name: "" };
        }
      });
      setEditForms(forms);
    } catch (err) {
      setError(`Failed to load provider configs: ${err.message}`);
    } finally {
      setLoading(false);
    }
  }, [fetchWithAuth]);

  useEffect(() => { fetchConfigs(); }, [fetchConfigs]);

  const handleFieldChange = (provider, field, value) => {
    setEditForms((prev) => ({ ...prev, [provider]: { ...prev[provider], [field]: value } }));
  };

  const handleSave = async (providerType) => {
    const form = editForms[providerType];
    if (!form) return;
    setSaving(providerType);
    setError(null);
    setSuccess(null);
    try {
      const payload = {
        provider_type: providerType,
        display_name: form.display_name || PROVIDERS.find((p) => p.value === providerType)?.label || providerType,
        connection_url: form.connection_url,
        environment: form.environment,
        embedding_model: form.embedding_model,
        is_active: form.is_active,
      };
      if (form.api_key) payload.api_key = form.api_key;

      let res;
      if (form.id) {
        res = await fetchWithAuth(`/api/vector-providers/${form.id}/`, {
          method: "PATCH",
          body: JSON.stringify(payload),
        });
      } else {
        res = await fetchWithAuth(`/api/vector-providers/`, {
          method: "POST",
          body: JSON.stringify(payload),
        });
      }
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || body.provider_type?.[0] || `HTTP ${res.status}`);
      }
      setSuccess(`${PROVIDERS.find((p) => p.value === providerType)?.label} configuration saved.`);
      await fetchConfigs();
    } catch (err) {
      setError(`Save failed: ${err.message}`);
    } finally {
      setSaving(null);
    }
  };

  const handleDelete = async (providerType) => {
    const form = editForms[providerType];
    if (!form?.id) return;
    setSaving(providerType);
    setError(null);
    try {
      const res = await fetchWithAuth(`/api/vector-providers/${form.id}/`, { method: "DELETE" });
      if (!res.ok && res.status !== 204) throw new Error(`HTTP ${res.status}`);
      setSuccess(`${PROVIDERS.find((p) => p.value === providerType)?.label} configuration removed.`);
      await fetchConfigs();
    } catch (err) {
      setError(`Delete failed: ${err.message}`);
    } finally {
      setSaving(null);
    }
  };

  return (
    <div className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl shadow-sm p-6 space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-base font-semibold text-slate-900 dark:text-slate-100 flex items-center gap-2">
            <Key className="w-4 h-4 text-amber-600" />
            Organisation Provider Keys
          </h3>
          <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
            Set org-level API keys for vector providers. The gateway uses these automatically when users don't supply their own.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={fetchConfigs} className="p-1.5 rounded-lg hover:bg-slate-100 dark:hover:bg-slate-700 transition-colors" title="Refresh">
            <RefreshCw className={`w-4 h-4 text-slate-400 ${loading ? "animate-spin" : ""}`} />
          </button>
          <InfoTooltip text="Configure provider API keys at the org level. Gateway resolves credentials: user-supplied → org config → env defaults." />
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

      {loading ? (
        <div className="flex items-center justify-center py-8"><Loader2 className="w-5 h-5 animate-spin text-slate-400" /></div>
      ) : (
        <div className="space-y-4">
          {PROVIDERS.map((provider) => {
            const form = editForms[provider.value] || {};
            const isConfigured = !!form.id;
            const isSaving = saving === provider.value;
            return (
              <div key={provider.value} className={`p-4 rounded-lg border transition-all ${isConfigured ? "border-emerald-300 dark:border-emerald-700 bg-emerald-50/50 dark:bg-emerald-900/10" : "border-slate-200 dark:border-slate-700"}`}>
                <div className="flex items-center justify-between mb-3">
                  <div className="flex items-center gap-2">
                    <Database className={`w-4 h-4 ${isConfigured ? "text-emerald-600" : "text-slate-400"}`} />
                    <span className="text-sm font-semibold text-slate-900 dark:text-slate-100">{provider.label}</span>
                    <span className="text-[10px] text-slate-400">{provider.description}</span>
                  </div>
                  <div className="flex items-center gap-2">
                    {isConfigured && (
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium bg-emerald-100 dark:bg-emerald-800/30 text-emerald-700 dark:text-emerald-300">
                        <CheckCircle className="w-3 h-3" /> Configured
                      </span>
                    )}
                    {form.api_key_set && (
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium bg-amber-100 dark:bg-amber-800/30 text-amber-700 dark:text-amber-300">
                        <Key className="w-3 h-3" /> Key Set
                      </span>
                    )}
                  </div>
                </div>

                <div className="grid grid-cols-2 gap-3">
                  {provider.fields.map((field) => (
                    <div key={field.key}>
                      <label className="block text-[11px] font-medium text-slate-600 dark:text-slate-400 mb-1">
                        {field.label} {field.required && <span className="text-red-500">*</span>}
                      </label>
                      <input
                        type={field.type || "text"}
                        value={form[field.key] || ""}
                        onChange={(e) => handleFieldChange(provider.value, field.key, e.target.value)}
                        placeholder={field.key === "api_key" && form.api_key_set ? "••••••• (key set, enter new to update)" : field.placeholder}
                        className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-1.5 border border-slate-200 dark:border-slate-700 rounded-lg text-xs font-mono focus:ring-2 focus:ring-teal-500 focus:border-transparent"
                      />
                    </div>
                  ))}
                  <div>
                    <label className="block text-[11px] font-medium text-slate-600 dark:text-slate-400 mb-1">Embedding Model</label>
                    <input
                      type="text"
                      value={form.embedding_model || ""}
                      onChange={(e) => handleFieldChange(provider.value, "embedding_model", e.target.value)}
                      placeholder="text-embedding-3-small"
                      className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-1.5 border border-slate-200 dark:border-slate-700 rounded-lg text-xs font-mono focus:ring-2 focus:ring-teal-500 focus:border-transparent"
                    />
                  </div>
                </div>

                <div className="flex items-center justify-between mt-3 pt-3 border-t border-slate-200 dark:border-slate-700">
                  <label className="inline-flex items-center gap-2 text-xs text-slate-600 dark:text-slate-400">
                    <input type="checkbox" checked={form.is_active ?? true} onChange={(e) => handleFieldChange(provider.value, "is_active", e.target.checked)} className="rounded border-slate-300 dark:border-slate-600 text-teal-600 focus:ring-teal-500" />
                    Active
                  </label>
                  <div className="flex items-center gap-2">
                    {isConfigured && (
                      <button onClick={() => handleDelete(provider.value)} disabled={isSaving} className="flex items-center gap-1 px-3 py-1.5 text-xs text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20 rounded-lg transition-colors">
                        <Trash2 className="w-3.5 h-3.5" /> Remove
                      </button>
                    )}
                    <button onClick={() => handleSave(provider.value)} disabled={isSaving} className="flex items-center gap-1 px-4 py-1.5 bg-teal-600 hover:bg-teal-700 disabled:bg-teal-400 text-white text-xs font-medium rounded-lg transition-colors">
                      {isSaving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
                      {isSaving ? "Saving..." : "Save"}
                    </button>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
