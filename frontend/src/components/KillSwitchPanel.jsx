import { useState, useEffect, useCallback } from "react";
import { createPortal } from "react-dom";
import {
  Plus, Power, PowerOff, Pencil, Trash2, X, Loader2,
  AlertTriangle, CheckCircle, ArrowRightLeft,
} from "lucide-react";
import { useAuth } from "../context/AuthContext";
import { InfoTooltip } from "./InfoTooltip";

export function KillSwitchPanel() {
  const { fetchWithAuth } = useAuth();
  const [killSwitches, setKillSwitches] = useState([]);
  const [loading, setLoading] = useState(true);
  const [modalOpen, setModalOpen] = useState(false);
  const [editingId, setEditingId] = useState(null);
  const [formData, setFormData] = useState({
    model_name: "",
    action: "disable",
    fallback_model: "",
    reason: "",
  });
  const [submitting, setSubmitting] = useState(false);
  const [actionLoading, setActionLoading] = useState(null);

  const fetchKillSwitches = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetchWithAuth("/api/kill-switches/");
      if (res.ok) {
        const data = await res.json();
        setKillSwitches(Array.isArray(data) ? data : data.results || []);
      }
    } finally {
      setLoading(false);
    }
  }, [fetchWithAuth]);

  useEffect(() => {
    fetchKillSwitches();
  }, [fetchKillSwitches]);

  const openCreateModal = () => {
    setEditingId(null);
    setFormData({ model_name: "", action: "disable", fallback_model: "", reason: "" });
    setModalOpen(true);
  };

  const openEditModal = (ks) => {
    setEditingId(ks.id);
    setFormData({
      model_name: ks.model_name || "",
      action: ks.action || "disable",
      fallback_model: ks.fallback_model || "",
      reason: ks.reason || "",
    });
    setModalOpen(true);
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setSubmitting(true);
    try {
      const payload = { ...formData };
      if (payload.action !== "reroute") {
        payload.fallback_model = "";
      }

      if (editingId) {
        await fetchWithAuth(`/api/kill-switches/${editingId}/`, {
          method: "PATCH",
          body: JSON.stringify(payload),
        });
      } else {
        await fetchWithAuth("/api/kill-switches/", {
          method: "POST",
          body: JSON.stringify(payload),
        });
      }
      setModalOpen(false);
      await fetchKillSwitches();
    } finally {
      setSubmitting(false);
    }
  };

  const handleActivate = async (id) => {
    setActionLoading(id);
    try {
      await fetchWithAuth(`/api/kill-switches/${id}/activate/`, { method: "POST" });
      await fetchKillSwitches();
    } finally {
      setActionLoading(null);
    }
  };

  const handleDeactivate = async (id) => {
    setActionLoading(id);
    try {
      await fetchWithAuth(`/api/kill-switches/${id}/deactivate/`, { method: "POST" });
      await fetchKillSwitches();
    } finally {
      setActionLoading(null);
    }
  };

  const handleDelete = async (id) => {
    if (!window.confirm("Delete this kill-switch? This action cannot be undone.")) return;
    setActionLoading(id);
    try {
      await fetchWithAuth(`/api/kill-switches/${id}/`, { method: "DELETE" });
      await fetchKillSwitches();
    } finally {
      setActionLoading(null);
    }
  };

  const modal =
    modalOpen && typeof document !== "undefined"
      ? createPortal(
          <div className="fixed inset-0 z-[120] flex items-center justify-center p-4 sm:p-6">
            <div className="absolute inset-0 bg-black/45 backdrop-blur-[1px]" onClick={() => setModalOpen(false)} />
            <div className="relative z-[121] w-full max-w-md max-h-[90vh] overflow-y-auto rounded-3xl border border-slate-200 bg-white/95 p-6 shadow-2xl dark:border-slate-700 dark:bg-slate-900/95">
              <div className="flex items-center justify-between mb-4">
                <h3 className="text-base font-semibold text-slate-900 dark:text-slate-100">
                  {editingId ? "Edit Kill-Switch" : "Create Kill-Switch"}
                </h3>
                <button onClick={() => setModalOpen(false)} className="p-1 hover:bg-slate-100 dark:hover:bg-slate-700 rounded transition-colors">
                  <X className="w-4 h-4 text-slate-500 dark:text-slate-400" />
                </button>
              </div>
              <form onSubmit={handleSubmit} className="space-y-4">
                <div>
                  <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">Model Name *</label>
                  <input
                    type="text"
                    required
                    value={formData.model_name}
                    onChange={(e) => setFormData({ ...formData, model_name: e.target.value })}
                    placeholder="e.g. gpt-4, claude-3-opus"
                    className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm focus:ring-2 focus:ring-teal-500 focus:border-transparent"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">Action *</label>
                  <select
                    value={formData.action}
                    onChange={(e) => setFormData({ ...formData, action: e.target.value })}
                    className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm focus:ring-2 focus:ring-teal-500 focus:border-transparent"
                  >
                    <option value="disable">Disable (reject all requests)</option>
                    <option value="reroute">Reroute (redirect to fallback model)</option>
                  </select>
                </div>
                {formData.action === "reroute" && (
                  <div>
                    <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">Fallback Model *</label>
                    <input
                      type="text"
                      required={formData.action === "reroute"}
                      value={formData.fallback_model}
                      onChange={(e) => setFormData({ ...formData, fallback_model: e.target.value })}
                      placeholder="e.g. gpt-3.5-turbo"
                      className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm focus:ring-2 focus:ring-teal-500 focus:border-transparent"
                    />
                  </div>
                )}
                <div>
                  <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">Reason</label>
                  <textarea
                    value={formData.reason}
                    onChange={(e) => setFormData({ ...formData, reason: e.target.value })}
                    placeholder="Optional: reason for this kill-switch"
                    rows={2}
                    className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm focus:ring-2 focus:ring-teal-500 focus:border-transparent resize-none"
                  />
                </div>
                <div className="flex items-center justify-end gap-2 pt-2">
                  <button
                    type="button"
                    onClick={() => setModalOpen(false)}
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
                    {editingId ? "Update" : "Create"}
                  </button>
                </div>
              </form>
            </div>
          </div>,
          document.body,
        )
      : null;

  return (
    <div className="ai-mesh-card ai-mesh-grid-bg rounded-3xl p-6">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h3 className="text-base font-semibold text-slate-900 dark:text-slate-100 flex items-center">Kill-Switch Management<InfoTooltip title="How to Use">{"Emergency model controls. 'Disable' blocks all traffic to a model immediately. 'Reroute' transparently redirects requests to a fallback model. Changes take effect in real-time via Redis."}</InfoTooltip></h3>
          <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
            Create and manage model kill-switches for emergency isolation
          </p>
        </div>
        <button
          onClick={openCreateModal}
          className="flex items-center gap-1.5 rounded-xl bg-teal-600 px-3 py-2 text-xs font-medium text-white transition-colors hover:bg-teal-700 dark:bg-teal-500 dark:hover:bg-teal-400"
        >
          <Plus className="w-3.5 h-3.5" />
          Create Kill-Switch
        </button>
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-8">
          <Loader2 className="w-5 h-5 text-teal-500 animate-spin" />
          <span className="ml-2 text-sm text-slate-500 dark:text-slate-400">Loading kill-switches...</span>
        </div>
      ) : killSwitches.length === 0 ? (
        <div className="text-center py-8 text-sm text-slate-500 dark:text-slate-400">
          No kill-switches configured. Create one to enable emergency model isolation.
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-slate-50/90 dark:bg-slate-900/50 border-b border-slate-200 dark:border-slate-700">
                <th className="px-3 py-2.5 text-left text-xs font-semibold text-slate-600 dark:text-slate-300 uppercase">Model</th>
                <th className="px-3 py-2.5 text-left text-xs font-semibold text-slate-600 dark:text-slate-300 uppercase">Status</th>
                <th className="px-3 py-2.5 text-left text-xs font-semibold text-slate-600 dark:text-slate-300 uppercase">Action</th>
                <th className="px-3 py-2.5 text-left text-xs font-semibold text-slate-600 dark:text-slate-300 uppercase">Fallback</th>
                <th className="px-3 py-2.5 text-left text-xs font-semibold text-slate-600 dark:text-slate-300 uppercase">Reason</th>
                <th className="px-3 py-2.5 text-left text-xs font-semibold text-slate-600 dark:text-slate-300 uppercase">Activated By</th>
                <th className="px-3 py-2.5 text-right text-xs font-semibold text-slate-600 dark:text-slate-300 uppercase">Controls</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-700">
              {killSwitches.map((ks) => (
                <tr key={ks.id} className="transition-colors hover:bg-slate-50 dark:hover:bg-slate-800/50">
                  <td className="px-3 py-2.5 font-mono text-xs text-slate-800 dark:text-slate-200">{ks.model_name}</td>
                  <td className="px-3 py-2.5">
                    {ks.is_active ? (
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-red-100 dark:bg-red-800/30 text-red-700 dark:text-red-300">
                        <AlertTriangle className="w-3 h-3" /> Active
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-400">
                        <CheckCircle className="w-3 h-3" /> Inactive
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2.5">
                    <span
                      className={`inline-flex items-center gap-1 text-xs font-medium ${
                        ks.action === "reroute"
                          ? "text-amber-700 dark:text-amber-300"
                          : "text-red-700 dark:text-red-300"
                      }`}
                    >
                      {ks.action === "reroute" ? <ArrowRightLeft className="w-3 h-3" /> : <PowerOff className="w-3 h-3" />}
                      {ks.action}
                    </span>
                  </td>
                  <td className="px-3 py-2.5 font-mono text-xs text-slate-600 dark:text-slate-400">{ks.fallback_model || "--"}</td>
                  <td className="px-3 py-2.5 text-xs text-slate-600 dark:text-slate-400 max-w-[200px] truncate">{ks.reason || "--"}</td>
                  <td className="px-3 py-2.5 text-xs text-slate-600 dark:text-slate-400">{ks.activated_by_username || "--"}</td>
                  <td className="px-3 py-2.5">
                    <div className="flex items-center justify-end gap-1">
                      {actionLoading === ks.id ? (
                        <Loader2 className="w-4 h-4 text-teal-500 animate-spin" />
                      ) : (
                        <>
                          {ks.is_active ? (
                            <button
                              onClick={() => handleDeactivate(ks.id)}
                              className="p-1.5 hover:bg-emerald-50 dark:hover:bg-emerald-900/20 rounded text-emerald-600 transition-colors"
                              title="Deactivate"
                              aria-label={`Deactivate kill switch for ${ks.model_name}`}
                            >
                              <Power className="w-3.5 h-3.5" />
                            </button>
                          ) : (
                            <button
                              onClick={() => handleActivate(ks.id)}
                              className="p-1.5 hover:bg-red-50 dark:hover:bg-red-900/20 rounded text-red-600 transition-colors"
                              title="Activate"
                              aria-label={`Activate kill switch for ${ks.model_name}`}
                            >
                              <PowerOff className="w-3.5 h-3.5" />
                            </button>
                          )}
                          <button
                            onClick={() => openEditModal(ks)}
                            className="p-1.5 hover:bg-slate-100 dark:hover:bg-slate-700 rounded text-slate-500 dark:text-slate-400 transition-colors"
                            title="Edit"
                            aria-label={`Edit kill switch for ${ks.model_name}`}
                          >
                            <Pencil className="w-3.5 h-3.5" />
                          </button>
                          <button
                            onClick={() => handleDelete(ks.id)}
                            className="p-1.5 hover:bg-red-50 dark:hover:bg-red-900/20 rounded text-red-500 transition-colors"
                            title="Delete"
                            aria-label={`Delete kill switch for ${ks.model_name}`}
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

      {modal}
    </div>
  );
}
