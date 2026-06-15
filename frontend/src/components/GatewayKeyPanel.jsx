import { useState, useEffect, useCallback } from "react";
import {
  Plus, Trash2, X, Loader2, Key, Copy, CheckCircle, AlertTriangle, Building2,
} from "lucide-react";
import { useAuth } from "../context/AuthContext";
import { copyToClipboard } from "../lib/clipboard";
import { InfoTooltip } from "./InfoTooltip";
import { createPortal } from "react-dom";

export function GatewayKeyPanel() {
  const { fetchWithAuth, user } = useAuth();
  const userOrg = user?.organization;
  const [keys, setKeys] = useState([]);
  const [loading, setLoading] = useState(true);
  const [modalOpen, setModalOpen] = useState(false);
  const [newKeyValue, setNewKeyValue] = useState(null);
  const [formData, setFormData] = useState({
    name: "",
    project_id: "",
    allowed_models: "",
    rate_limit_tokens_per_minute: 100000,
    risk_score: 0,
    expires_at: "",
  });
  const [submitting, setSubmitting] = useState(false);
  const [actionLoading, setActionLoading] = useState(null);
  const [copied, setCopied] = useState(false);
  const [formError, setFormError] = useState("");

  const fetchKeys = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetchWithAuth("/api/gateways/keys/");
      if (res.ok) {
        const data = await res.json();
        setKeys(Array.isArray(data) ? data : data.results || []);
      }
    } catch {
      setKeys([]);
    } finally {
      setLoading(false);
    }
  }, [fetchWithAuth]);

  useEffect(() => {
    fetchKeys();
  }, [fetchKeys]);

  const openCreateModal = () => {
    setNewKeyValue(null);
    setFormError("");
    setFormData({
      name: "",
      project_id: "",
      allowed_models: "",
      rate_limit_tokens_per_minute: 100000,
      risk_score: 0,
      expires_at: "",
    });
    setModalOpen(true);
  };

  const handleCreate = async (e) => {
    e.preventDefault();
    setSubmitting(true);
    setFormError("");
    try {
      const payload = {
        name: formData.name,
        project_id: formData.project_id,
        rate_limit_tokens_per_minute: Number(formData.rate_limit_tokens_per_minute) || 100000,
        risk_score: Number(formData.risk_score) || 0,
      };
      if (formData.allowed_models.trim()) {
        payload.allowed_models = formData.allowed_models.split(",").map((m) => m.trim()).filter(Boolean);
      }
      if (formData.expires_at) {
        payload.expires_at = formData.expires_at;
      }

      const res = await fetchWithAuth("/api/gateways/keys/", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      if (res.ok) {
        const data = await res.json();
        setNewKeyValue(data.key || null);
        await fetchKeys();
      } else {
        const errorText = await res.text();
        setFormError(errorText || `Failed to create key (${res.status})`);
      }
    } catch (error) {
      setFormError(error.message || "Failed to create key.");
    } finally {
      setSubmitting(false);
    }
  };

  const handleRevoke = async (id) => {
    if (!window.confirm("Revoke this API key? It will immediately stop working.")) return;
    setActionLoading(id);
    try {
      await fetchWithAuth(`/api/gateways/keys/${id}/`, { method: "DELETE" });
      await fetchKeys();
    } finally {
      setActionLoading(null);
    }
  };

  const handleCopyKey = () => {
    if (newKeyValue) {
      copyToClipboard(newKeyValue);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  const formatDate = (iso) => {
    if (!iso) return "--";
    return new Intl.DateTimeFormat(undefined, {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    }).format(new Date(iso));
  };

  return (
    <div className="ai-mesh-card rounded-[28px] p-6">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h3 className="flex items-center text-lg font-semibold text-slate-900 dark:text-slate-100">Gateway API Keys<InfoTooltip title="How to Use">{"Gateway API keys authenticate requests to the AI Gateway. Create a key, copy it, and use it as the Authorization: Bearer <key> header in your API calls to the gateway at /v1/chat/completions."}</InfoTooltip></h3>
          <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
            Manage authentication, project scoping, and rate limits for gateway access.
          </p>
          {userOrg && (
            <div className="mt-2 inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-indigo-50 dark:bg-indigo-900/20 border border-indigo-200 dark:border-indigo-800">
              <Building2 className="w-3 h-3 text-indigo-600 dark:text-indigo-400" />
              <span className="text-xs font-medium text-indigo-700 dark:text-indigo-300">Organization: {userOrg.name}</span>
            </div>
          )}
        </div>
        <button
          onClick={openCreateModal}
          className="flex items-center gap-1.5 rounded-2xl bg-teal-600 px-4 py-2.5 text-xs font-medium text-white transition-colors hover:bg-teal-700 dark:bg-teal-500 dark:hover:bg-teal-400"
        >
          <Plus className="w-3.5 h-3.5" />
          Create API Key
        </button>
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-8">
          <Loader2 className="w-5 h-5 text-teal-500 animate-spin" />
          <span className="ml-2 text-sm text-slate-500 dark:text-slate-400">Loading API keys...</span>
        </div>
      ) : keys.length === 0 ? (
        <div className="text-center py-8 text-sm text-slate-500 dark:text-slate-400">
          No API keys created. Create one to authenticate gateway requests.
        </div>
      ) : (
        <div className="overflow-x-auto rounded-[24px] border border-slate-200/80 dark:border-slate-700">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-slate-50 dark:bg-slate-800/50 border-b border-slate-200 dark:border-slate-700">
                <th className="px-3 py-2.5 text-left text-xs font-semibold text-slate-600 dark:text-slate-300 uppercase">Name</th>
                <th className="px-3 py-2.5 text-left text-xs font-semibold text-slate-600 dark:text-slate-300 uppercase">Prefix</th>
                <th className="px-3 py-2.5 text-left text-xs font-semibold text-slate-600 dark:text-slate-300 uppercase">Organization</th>
                <th className="px-3 py-2.5 text-left text-xs font-semibold text-slate-600 dark:text-slate-300 uppercase">Project</th>
                <th className="px-3 py-2.5 text-left text-xs font-semibold text-slate-600 dark:text-slate-300 uppercase">Models</th>
                <th className="px-3 py-2.5 text-left text-xs font-semibold text-slate-600 dark:text-slate-300 uppercase">Rate Limit</th>
                <th className="px-3 py-2.5 text-left text-xs font-semibold text-slate-600 dark:text-slate-300 uppercase">Status</th>
                <th className="px-3 py-2.5 text-left text-xs font-semibold text-slate-600 dark:text-slate-300 uppercase">Last Used</th>
                <th className="px-3 py-2.5 text-right text-xs font-semibold text-slate-600 dark:text-slate-300 uppercase">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-700">
              {keys.map((k) => (
                <tr key={k.id} className="transition-colors hover:bg-slate-50 dark:hover:bg-slate-900/50">
                  <td className="px-3 py-2.5 text-xs font-medium text-slate-800 dark:text-slate-200">{k.name}</td>
                  <td className="px-3 py-2.5 font-mono text-xs text-slate-600 dark:text-slate-400">{k.prefix || "--"}...</td>
                  <td className="px-3 py-2.5">
                    {k.organization_name ? (
                      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-indigo-50 dark:bg-indigo-900/20 text-indigo-700 dark:text-indigo-300 text-[10px] font-medium">
                        <Building2 className="w-2.5 h-2.5" />
                        {k.organization_name}
                      </span>
                    ) : (
                      <span className="text-xs text-slate-400">--</span>
                    )}
                  </td>
                  <td className="px-3 py-2.5 text-xs text-slate-600 dark:text-slate-400">{k.project_id || "--"}</td>
                  <td className="px-3 py-2.5">
                    <div className="flex flex-wrap gap-1">
                      {(k.allowed_models || []).length > 0
                        ? k.allowed_models.map((m, i) => (
                            <span key={i} className="px-1.5 py-0.5 bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-400 text-[10px] rounded font-mono">
                              {m}
                            </span>
                          ))
                        : <span className="text-xs text-slate-400">all</span>}
                    </div>
                  </td>
                  <td className="px-3 py-2.5 text-xs text-slate-600 dark:text-slate-400">
                    {k.rate_limit_tokens_per_minute ? `${(k.rate_limit_tokens_per_minute / 1000).toFixed(0)}K TPM` : "--"}
                  </td>
                  <td className="px-3 py-2.5">
                    {k.is_active ? (
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-emerald-100 dark:bg-emerald-800/30 text-emerald-700">
                        <CheckCircle className="w-3 h-3" /> Active
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-red-100 dark:bg-red-800/30 text-red-700">
                        <AlertTriangle className="w-3 h-3" /> Revoked
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2.5 text-xs text-slate-500 dark:text-slate-400">{formatDate(k.last_used_at)}</td>
                  <td className="px-3 py-2.5">
                    <div className="flex items-center justify-end gap-1">
                      {actionLoading === k.id ? (
                        <Loader2 className="w-4 h-4 text-teal-500 animate-spin" />
                      ) : (
                        k.is_active && (
                          <button
                            onClick={() => handleRevoke(k.id)}
                            className="rounded-lg p-1.5 text-red-500 transition-colors hover:bg-red-50 dark:hover:bg-red-900/20"
                            aria-label={`Revoke gateway key ${k.name || k.prefix || ""}`.trim()}
                            title="Revoke Key"
                          >
                            <Trash2 className="w-3.5 h-3.5" />
                          </button>
                        )
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Create Modal */}
      {modalOpen && typeof document !== "undefined" && createPortal(
        <div className="fixed inset-0 z-50 flex items-start justify-center px-4 py-10 overflow-y-auto">
          <div className="fixed inset-0 z-40 bg-black/45 backdrop-blur-[2px]" onClick={() => { if (!newKeyValue) setModalOpen(false); }} />
          <div className="relative z-50 my-6 w-full max-w-4xl overflow-hidden rounded-[28px] border border-slate-200/90 bg-white shadow-2xl dark:border-slate-700 dark:bg-slate-800 flex flex-col">
            {/* Header */}
            <div className="flex-shrink-0 px-6 py-4 md:px-8 md:py-5 border-b border-slate-200 dark:border-slate-700">
              {newKeyValue ? (
                <div className="flex items-center justify-between">
                  <h3 className="text-base font-semibold text-slate-900 dark:text-slate-100">API Key Created</h3>
                  <button onClick={() => { setNewKeyValue(null); setModalOpen(false); }} className="p-1 hover:bg-slate-100 dark:hover:bg-slate-700 rounded transition-colors">
                    <X className="w-4 h-4 text-slate-500 dark:text-slate-400" />
                  </button>
                </div>
              ) : (
                <div className="flex items-center justify-between">
                  <h3 className="text-base font-semibold text-slate-900 dark:text-slate-100">Create API Key</h3>
                  <button onClick={() => setModalOpen(false)} className="p-0.5 hover:bg-slate-100 dark:hover:bg-slate-700 rounded transition-colors">
                    <X className="w-4 h-4 text-slate-500 dark:text-slate-400" />
                  </button>
                </div>
              )}
            </div>

            {/* Content */}
            {newKeyValue ? (
              <>
                <div className="px-6 py-4 md:px-8 md:py-5">
                  <div className="bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 rounded-lg p-3 mb-4">
                    <div className="flex items-start gap-2">
                      <AlertTriangle className="w-4 h-4 text-amber-600 mt-0.5 flex-shrink-0" />
                      <p className="text-xs text-amber-800 dark:text-amber-200">
                        Store this key securely. It will not be shown again.
                      </p>
                    </div>
                  </div>
                  <div className="flex items-center gap-2 bg-slate-50 dark:bg-slate-800/50 border border-slate-200 dark:border-slate-700 rounded-lg p-3">
                    <Key className="w-4 h-4 text-teal-600 flex-shrink-0" />
                    <code className="text-xs text-slate-800 dark:text-slate-200 font-mono break-all flex-1">{newKeyValue}</code>
                    <button
                      onClick={handleCopyKey}
                      className="p-1.5 hover:bg-slate-200 rounded transition-colors flex-shrink-0"
                      title="Copy"
                    >
                      {copied ? <CheckCircle className="w-4 h-4 text-emerald-600" /> : <Copy className="w-4 h-4 text-slate-500 dark:text-slate-400" />}
                    </button>
                  </div>
                </div>

                {/* Footer for success state */}
                <div className="flex-shrink-0 px-6 py-4 md:px-8 md:py-5 border-t border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800/50">
                  <div className="flex justify-end">
                    <button
                      onClick={() => { setNewKeyValue(null); setModalOpen(false); }}
                      className="px-4 py-2 bg-teal-600 hover:bg-teal-700 text-white text-sm font-medium rounded-lg transition-colors"
                    >
                      Done
                    </button>
                  </div>
                </div>
              </>
            ) : (
              <form onSubmit={handleCreate} className="flex flex-col">
                {/* Scrollable form content */}
                <div className="px-6 py-4 md:px-8 md:py-5">
                  {formError ? (
                    <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700 dark:border-red-800 dark:bg-red-900/20 dark:text-red-300 mb-3">
                      {formError}
                    </div>
                  ) : null}
                  <div className="space-y-3">
                    <div>
                      <label htmlFor="gateway-key-name" className="mb-1.5 block text-xs font-semibold text-slate-700 dark:text-slate-300">Name *</label>
                      <input
                        id="gateway-key-name"
                        type="text"
                        required
                        value={formData.name}
                        onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                        placeholder="e.g. Production Key"
                        className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-xs focus:ring-2 focus:ring-teal-500 focus:border-transparent"
                      />
                    </div>
                    <div>
                      <label htmlFor="gateway-key-project-id" className="mb-1.5 block text-xs font-semibold text-slate-700 dark:text-slate-300">Project ID *</label>
                      <input
                        id="gateway-key-project-id"
                        type="text"
                        required
                        value={formData.project_id}
                        onChange={(e) => setFormData({ ...formData, project_id: e.target.value })}
                        placeholder="e.g. 1"
                        className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-xs focus:ring-2 focus:ring-teal-500 focus:border-transparent"
                      />
                    </div>
                    <div>
                      <label htmlFor="gateway-key-models" className="mb-1.5 block text-xs font-semibold text-slate-700 dark:text-slate-300">Allowed Models</label>
                      <input
                        id="gateway-key-models"
                        type="text"
                        value={formData.allowed_models}
                        onChange={(e) => setFormData({ ...formData, allowed_models: e.target.value })}
                        placeholder="gpt-4, gpt-3.5-turbo"
                        className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-xs focus:ring-2 focus:ring-teal-500 focus:border-transparent"
                      />
                    </div>
                    <div className="grid grid-cols-2 gap-3">
                      <div>
                        <label htmlFor="gateway-key-rate-limit" className="mb-1.5 block text-xs font-semibold text-slate-700 dark:text-slate-300">Rate Limit (TPM)</label>
                        <input
                          id="gateway-key-rate-limit"
                          type="number"
                          min="0"
                          value={formData.rate_limit_tokens_per_minute}
                          onChange={(e) => setFormData({ ...formData, rate_limit_tokens_per_minute: e.target.value })}
                          className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-xs focus:ring-2 focus:ring-teal-500 focus:border-transparent"
                        />
                      </div>
                      <div>
                        <label htmlFor="gateway-key-risk-score" className="mb-1.5 block text-xs font-semibold text-slate-700 dark:text-slate-300">Risk Score (0-1)</label>
                        <input
                          id="gateway-key-risk-score"
                          type="number"
                          step="0.1"
                          min="0"
                          max="1"
                          value={formData.risk_score}
                          onChange={(e) => setFormData({ ...formData, risk_score: e.target.value })}
                          className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-xs focus:ring-2 focus:ring-teal-500 focus:border-transparent"
                        />
                      </div>
                    </div>
                    <div>
                      <label htmlFor="gateway-key-expires-at" className="mb-1.5 block text-xs font-semibold text-slate-700 dark:text-slate-300">Expires At (optional)</label>
                      <input
                        id="gateway-key-expires-at"
                        type="datetime-local"
                        value={formData.expires_at}
                        onChange={(e) => setFormData({ ...formData, expires_at: e.target.value })}
                        className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-xs focus:ring-2 focus:ring-teal-500 focus:border-transparent"
                      />
                    </div>
                  </div>
                </div>

                {/* Footer with buttons */}
                <div className="flex-shrink-0 px-6 py-4 md:px-8 md:py-5 border-t border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800/50">
                  <div className="flex items-center justify-end gap-2">
                    <button
                      type="button"
                      onClick={() => setModalOpen(false)}
                      className="px-4 py-1.5 text-xs font-medium text-slate-600 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-700 rounded-lg transition-colors"
                    >
                      Cancel
                    </button>
                    <button
                      type="submit"
                      disabled={submitting}
                      className="flex items-center gap-1.5 px-4 py-1.5 bg-teal-600 hover:bg-teal-700 disabled:bg-teal-400 text-white text-xs font-semibold rounded-lg transition-colors"
                    >
                      {submitting && <Loader2 className="w-3 h-3 animate-spin" />}
                      Create Key
                    </button>
                  </div>
                </div>
              </form>
            )}
          </div>
        </div>,
        document.body,
      )}
    </div>
  );
}
