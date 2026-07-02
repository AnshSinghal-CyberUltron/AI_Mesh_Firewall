import { useState, useEffect, useCallback, useMemo } from "react";
import { createPortal } from "react-dom";
import {
  Plus, Power, PowerOff, Pencil, Trash2, X, Loader2,
  AlertTriangle, CheckCircle, ArrowRightLeft, Server,
} from "lucide-react";
import { useAuth } from "../context/AuthContext";
import { InfoTooltip } from "./InfoTooltip";
import { KillSwitchModelCombobox } from "./KillSwitchModelCombobox";
import {
  KILL_SWITCH_GLOBAL_SCOPE,
  buildKillSwitchFallbackOptions,
  buildKillSwitchTargetOptions,
} from "../utils/killSwitchModelOptions";

// Extract a human-readable message from a kill-switch API error response.
// DRF returns field-keyed validation errors (e.g. {"fallback_model": ["..."]})
// for serializer failures and {"detail": "..."} for permission/other errors.
function extractKillSwitchError(data, fallback) {
  if (!data || typeof data !== "object") return fallback;
  if (typeof data.error === "string" && data.error) return data.error;
  if (typeof data.detail === "string" && data.detail) return data.detail;
  for (const value of Object.values(data)) {
    if (typeof value === "string" && value) return value;
    if (Array.isArray(value) && typeof value[0] === "string" && value[0]) return value[0];
  }
  return fallback;
}

export function KillSwitchPanel() {
  const { fetchWithAuth, user } = useAuth();
  const orgSlug = user?.organization?.slug || "";
  const [killSwitches, setKillSwitches] = useState([]);
  const [loading, setLoading] = useState(true);
  const [modalOpen, setModalOpen] = useState(false);
  const [editingId, setEditingId] = useState(null);
  const [formData, setFormData] = useState({
    model_name: "",
    api_key_prefix: "",
    action: "disable",
    fallback_model: "",
    reason: "",
  });
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState(null);
  const [actionLoading, setActionLoading] = useState(null);
  const [actionError, setActionError] = useState(null);
  const [loadError, setLoadError] = useState(null);
  const [redisScanning, setRedisScanning] = useState(false);
  const [redisResult, setRedisResult] = useState(null);
  const [redisError, setRedisError] = useState(null);
  const [connectedModels, setConnectedModels] = useState([]);
  const [modelsLoading, setModelsLoading] = useState(false);
  const [modelsLoadError, setModelsLoadError] = useState(null);

  const fetchKillSwitches = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetchWithAuth("/api/kill-switches/");
      if (res.ok) {
        const data = await res.json();
        setKillSwitches(Array.isArray(data) ? data : data.results || []);
        setLoadError(null);
      } else {
        // Safety surface: a failed load must NOT collapse into the benign
        // "No kill-switches configured" state and hide ACTIVE switches.
        setLoadError(`Failed to load kill-switches (HTTP ${res.status}).`);
      }
    } catch {
      setLoadError("Failed to load kill-switches.");
    } finally {
      setLoading(false);
    }
  }, [fetchWithAuth]);

  useEffect(() => {
    fetchKillSwitches();
  }, [fetchKillSwitches]);

  const fetchConnectedModels = useCallback(async () => {
    setModelsLoading(true);
    setModelsLoadError(null);
    try {
      const res = await fetchWithAuth("/api/firewall/models/");
      if (!res.ok) {
        setConnectedModels([]);
        setModelsLoadError("Could not load connected models.");
        return;
      }
      const data = await res.json();
      setConnectedModels(Array.isArray(data) ? data : data.results || []);
    } catch {
      setConnectedModels([]);
      setModelsLoadError("Network error loading connected models.");
    } finally {
      setModelsLoading(false);
    }
  }, [fetchWithAuth]);

  useEffect(() => {
    if (modalOpen) {
      fetchConnectedModels();
    }
  }, [modalOpen, fetchConnectedModels]);

  const isCreate = !editingId;

  const targetOptions = useMemo(
    () =>
      buildKillSwitchTargetOptions({
        connectedModels,
        killSwitches,
        apiKeyPrefix: formData.api_key_prefix,
        editingId,
        currentModelName: formData.model_name,
        isCreate,
      }),
    [
      connectedModels,
      killSwitches,
      formData.api_key_prefix,
      formData.model_name,
      editingId,
      isCreate,
    ],
  );

  const fallbackOptions = useMemo(
    () =>
      buildKillSwitchFallbackOptions({
        connectedModels,
        killSwitches,
        apiKeyPrefix: formData.api_key_prefix,
        targetModelName: formData.model_name,
        editingId,
        currentFallbackModel: formData.fallback_model,
        isCreate,
      }),
    [
      connectedModels,
      killSwitches,
      formData.api_key_prefix,
      formData.model_name,
      formData.fallback_model,
      editingId,
      isCreate,
    ],
  );

  const selectedTargetMeta = useMemo(
    () => targetOptions.find((o) => o.value === formData.model_name),
    [targetOptions, formData.model_name],
  );

  const isGlobalTarget = formData.model_name === KILL_SWITCH_GLOBAL_SCOPE;

  useEffect(() => {
    if (isGlobalTarget && formData.action === "reroute") {
      setFormData((prev) => ({ ...prev, action: "disable", fallback_model: "" }));
    }
  }, [isGlobalTarget, formData.action]);

  const handleRedisValidate = async (repair = false) => {
    if (repair && !window.confirm("Delete malformed Redis kill-switch keys for your org and resync from the database?")) {
      return;
    }
    setRedisScanning(true);
    setRedisError(null);
    setRedisResult(null);
    try {
      const res = await fetchWithAuth("/api/admin/redis/kill-switches/validate/", {
        method: "POST",
        body: JSON.stringify({ repair }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setRedisError(data.error || data.detail || "Redis validation failed.");
        return;
      }
      setRedisResult(data);
    } catch {
      setRedisError("Network error during Redis validation.");
    } finally {
      setRedisScanning(false);
    }
  };

  const openCreateModal = () => {
    setEditingId(null);
    setSubmitError(null);
    setFormData({ model_name: "", api_key_prefix: "", action: "disable", fallback_model: "", reason: "" });
    setModalOpen(true);
  };

  const openEditModal = (ks) => {
    setEditingId(ks.id);
    setSubmitError(null);
    setFormData({
      model_name: ks.model_name || "",
      api_key_prefix: ks.api_key_prefix || "",
      action: ks.action || "disable",
      fallback_model: ks.fallback_model || "",
      reason: ks.reason || "",
    });
    setModalOpen(true);
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setSubmitting(true);
    setSubmitError(null);
    try {
      const payload = { ...formData };
      if (payload.action !== "reroute") {
        payload.fallback_model = "";
      }

      const res = editingId
        ? await fetchWithAuth(`/api/kill-switches/${editingId}/`, {
            method: "PATCH",
            body: JSON.stringify(payload),
          })
        : await fetchWithAuth("/api/kill-switches/", {
            method: "POST",
            body: JSON.stringify(payload),
          });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setSubmitError(
          extractKillSwitchError(
            data,
            editingId ? "Could not update kill-switch." : "Could not create kill-switch.",
          ),
        );
        return;
      }
      setModalOpen(false);
      await fetchKillSwitches();
    } catch {
      setSubmitError("Network error saving kill-switch.");
    } finally {
      setSubmitting(false);
    }
  };

  const handleActivate = async (id) => {
    if (!window.confirm("Activate this kill-switch? It will immediately block or reroute traffic per its configuration.")) return;
    setActionLoading(id);
    setActionError(null);
    try {
      const res = await fetchWithAuth(`/api/kill-switches/${id}/activate/`, { method: "POST" });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setActionError(extractKillSwitchError(data, "Could not activate kill-switch."));
        return;
      }
      await fetchKillSwitches();
    } catch {
      setActionError("Network error activating kill-switch.");
    } finally {
      setActionLoading(null);
    }
  };

  const handleDeactivate = async (id) => {
    if (!window.confirm("Deactivate this kill-switch? Traffic to the affected model(s) will resume.")) return;
    setActionLoading(id);
    setActionError(null);
    try {
      const res = await fetchWithAuth(`/api/kill-switches/${id}/deactivate/`, { method: "POST" });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setActionError(extractKillSwitchError(data, "Could not deactivate kill-switch."));
        return;
      }
      await fetchKillSwitches();
    } catch {
      setActionError("Network error deactivating kill-switch.");
    } finally {
      setActionLoading(null);
    }
  };

  const handleDelete = async (id) => {
    if (!window.confirm("Delete this kill-switch? This action cannot be undone.")) return;
    setActionLoading(id);
    setActionError(null);
    try {
      const res = await fetchWithAuth(`/api/kill-switches/${id}/`, { method: "DELETE" });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setActionError(extractKillSwitchError(data, "Could not delete kill-switch."));
        return;
      }
      await fetchKillSwitches();
    } catch {
      setActionError("Network error deleting kill-switch.");
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
                <button onClick={() => setModalOpen(false)} className="p-1 hover:bg-slate-100 dark:hover:bg-slate-700 rounded transition-colors" aria-label="Close dialog" title="Close">
                  <X className="w-4 h-4 text-slate-500 dark:text-slate-400" />
                </button>
              </div>
              <form onSubmit={handleSubmit} className="space-y-4">
                {submitError && (
                  <div className="rounded-xl border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-800 dark:text-red-200" role="alert">
                    {submitError}
                  </div>
                )}
                {orgSlug && (
                  <div>
                    <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">
                      Organization Slug
                    </label>
                    <input
                      type="text"
                      readOnly
                      value={orgSlug}
                      className="text-slate-600 dark:text-slate-400 bg-slate-50 dark:bg-slate-800/80 w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm font-mono"
                      aria-label="Organization slug"
                    />
                    <p className="text-[11px] text-slate-500 dark:text-slate-400 mt-1">
                      Redis keys use this slug — verify it matches your gateway API key org.
                    </p>
                  </div>
                )}
                <div>
                  <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1 flex items-center gap-1">
                    <Server className="w-3 h-3" />
                    Target model *
                    <InfoTooltip title="Connected models only">
                      Pick a model registered under Multi-Model Governance with an API key. Models that already have a kill-switch at the same credential scope are hidden when creating a new switch.
                    </InfoTooltip>
                  </label>
                  {modelsLoading ? (
                    <div className="flex items-center gap-2 py-2 text-xs text-slate-500 dark:text-slate-400">
                      <Loader2 className="w-3.5 h-3.5 animate-spin" />
                      Loading connected models…
                    </div>
                  ) : modelsLoadError ? (
                    <p className="text-xs text-amber-700 dark:text-amber-300" role="alert">
                      {modelsLoadError}
                    </p>
                  ) : targetOptions.length === 0 ? (
                    <div className="rounded-lg border border-violet-200 dark:border-violet-800 bg-violet-50/80 dark:bg-violet-900/20 px-3 py-2.5 text-xs text-violet-900 dark:text-violet-100">
                      No models available for a new kill-switch at this scope.{" "}
                      <a href="?tab=firewall-1-5" className="font-medium underline">
                        Add or connect a model
                      </a>{" "}
                      under Multi-Model Governance, or change the API key prefix.
                    </div>
                  ) : (
                    <>
                      <KillSwitchModelCombobox
                        label="Target model"
                        options={targetOptions}
                        value={formData.model_name}
                        onChange={(model_name) => {
                          setFormData((prev) => ({
                            ...prev,
                            model_name,
                            fallback_model:
                              prev.fallback_model === model_name ? "" : prev.fallback_model,
                          }));
                        }}
                        required
                        loading={modelsLoading}
                        disabled={modelsLoading}
                        placeholder="Search connected models…"
                        hintId="kill-switch-target-hint"
                        hint={
                          selectedTargetMeta?.modelId
                            ? `LiteLLM id: ${selectedTargetMeta.modelId}`
                            : "Uses registered model_name for Redis and gateway routing."
                        }
                        emptyMessage="No models match your search"
                      />
                    </>
                  )}
                </div>
                <div>
                  <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">
                    API Key Prefix (optional)
                    <InfoTooltip title="Credential scope">
                      When set, this kill-switch applies only to requests using an API key with this prefix.
                    </InfoTooltip>
                  </label>
                  <input
                    type="text"
                    value={formData.api_key_prefix}
                    onChange={(e) =>
                      setFormData((prev) => ({
                        ...prev,
                        api_key_prefix: e.target.value,
                        model_name: "",
                        fallback_model: "",
                      }))
                    }
                    placeholder="e.g. zs_a1b2"
                    className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm focus:ring-2 focus:ring-teal-500 focus:border-transparent font-mono"
                  />
                  <p className="text-[11px] text-slate-500 dark:text-slate-400 mt-1">
                    Changing the prefix refreshes which models can be selected (per credential scope).
                  </p>
                </div>
                <div>
                  <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">Action *</label>
                  <select
                    value={formData.action}
                    onChange={(e) => setFormData({ ...formData, action: e.target.value })}
                    className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full min-h-[44px] px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm focus:ring-2 focus:ring-teal-500 focus:border-transparent"
                    aria-label="Kill-switch action"
                  >
                    <option value="disable">Disable (reject all requests)</option>
                    <option value="reroute" disabled={isGlobalTarget}>
                      Reroute (redirect to fallback model)
                    </option>
                  </select>
                  {isGlobalTarget && (
                    <p className="text-[11px] text-amber-700 dark:text-amber-300 mt-1" role="status">
                      Org-wide emergency scope only supports disable at the gateway.
                    </p>
                  )}
                </div>
                {formData.action === "reroute" && (
                  <div>
                    <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1 flex items-center gap-1">
                      Fallback model *
                      <InfoTooltip title="Reroute destination">
                        Must be a different connected model. Models already under kill-switch at this credential scope are not offered.
                      </InfoTooltip>
                    </label>
                    {!formData.model_name ? (
                      <p className="text-xs text-slate-500 dark:text-slate-400">
                        Select a target model first.
                      </p>
                    ) : fallbackOptions.length === 0 ? (
                      <p className="text-xs text-amber-700 dark:text-amber-300" role="alert">
                        No other connected models available for reroute (others may be under an active kill-switch).{" "}
                        <a href="?tab=firewall-1-5" className="font-medium underline">
                          Connect another model
                        </a>
                        .
                      </p>
                    ) : (
                      <KillSwitchModelCombobox
                        label="Fallback model"
                        options={fallbackOptions}
                        value={formData.fallback_model}
                        onChange={(fallback_model) => setFormData({ ...formData, fallback_model })}
                        required={formData.action === "reroute"}
                        loading={modelsLoading}
                        disabled={modelsLoading || !formData.model_name}
                        placeholder="Search fallback models…"
                        hint="Must differ from the target and must not be under an active kill-switch for this scope."
                        emptyMessage="No eligible fallback models"
                      />
                    )}
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
                    aria-label="Kill-switch reason"
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
                    disabled={
                      submitting
                      || modelsLoading
                      || (isCreate && targetOptions.length === 0)
                      || (formData.action === "reroute"
                        && formData.model_name
                        && fallbackOptions.length > 0
                        && !formData.fallback_model)
                    }
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
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => handleRedisValidate(false)}
            disabled={redisScanning}
            className="flex min-h-[44px] items-center gap-1.5 rounded-xl border border-slate-300 bg-white px-3 py-2 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-60 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700"
            aria-label="Validate Redis kill-switch keys"
          >
            {redisScanning ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <AlertTriangle className="w-3.5 h-3.5" />}
            Validate Redis
          </button>
          <button
            onClick={openCreateModal}
            className="flex min-h-[44px] items-center gap-1.5 rounded-xl bg-teal-600 px-3 py-2 text-xs font-medium text-white transition-colors hover:bg-teal-700 dark:bg-teal-500 dark:hover:bg-teal-400"
          >
            <Plus className="w-3.5 h-3.5" />
            Create Kill-Switch
          </button>
        </div>
      </div>

      {(redisError || redisResult) && (
        <div
          className={`mb-4 rounded-xl border px-3 py-2 text-xs ${
            redisResult?.malformed_count > 0
              ? "border-amber-500/40 bg-amber-500/10 text-amber-900 dark:text-amber-100"
              : "border-emerald-500/30 bg-emerald-500/10 text-emerald-900 dark:text-emerald-100"
          }`}
          role="status"
          aria-live="polite"
        >
          {redisError && <p>{redisError}</p>}
          {redisResult && (
            <>
              <p>
                Scanned {redisResult.scanned} key(s) for <span className="font-mono">{redisResult.org_slug}</span>
                {" — "}
                {redisResult.malformed_count} malformed.
              </p>
              {redisResult.malformed_count > 0 && (
                <button
                  type="button"
                  onClick={() => handleRedisValidate(true)}
                  disabled={redisScanning}
                  className="mt-2 min-h-[44px] rounded-lg bg-amber-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-amber-700 disabled:opacity-60"
                >
                  Repair malformed keys
                </button>
              )}
            </>
          )}
        </div>
      )}

      {actionError && (
        <div className="mb-4 rounded-xl border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-800 dark:text-red-200" role="alert">
          {actionError}
        </div>
      )}

      {loadError && (
        <div className="mb-4 flex items-center justify-between gap-3 rounded-xl border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-800 dark:text-red-200" role="alert">
          <span>{loadError}</span>
          <button
            type="button"
            onClick={fetchKillSwitches}
            className="rounded-lg border border-red-300 px-2 py-1 text-xs font-medium text-red-700 transition-colors hover:bg-red-500/10 dark:border-red-700 dark:text-red-300"
          >
            Retry
          </button>
        </div>
      )}

      {loading ? (
        <div className="flex items-center justify-center py-8">
          <Loader2 className="w-5 h-5 text-teal-500 animate-spin" />
          <span className="ml-2 text-sm text-slate-500 dark:text-slate-400">Loading kill-switches...</span>
        </div>
      ) : killSwitches.length === 0 ? (
        loadError ? null : (
          <div className="text-center py-8 text-sm text-slate-500 dark:text-slate-400">
            No kill-switches configured. Create one to enable emergency model isolation.
          </div>
        )
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-slate-50/90 dark:bg-slate-900/50 border-b border-slate-200 dark:border-slate-700">
                <th className="px-3 py-2.5 text-left text-xs font-semibold text-slate-600 dark:text-slate-300 uppercase">Model</th>
                <th className="px-3 py-2.5 text-left text-xs font-semibold text-slate-600 dark:text-slate-300 uppercase">Key Prefix</th>
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
                <tr key={ks.id ?? `ms:${ks.model_name}`} className="transition-colors hover:bg-slate-50 dark:hover:bg-slate-800/50">
                  <td className="px-3 py-2.5 font-mono text-xs text-slate-800 dark:text-slate-200">{ks.model_name}</td>
                  <td className="px-3 py-2.5 font-mono text-xs text-slate-600 dark:text-slate-400">{ks.api_key_prefix || "—"}</td>
                  <td className="px-3 py-2.5">
                    {ks.is_active ? (
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-red-100 dark:bg-red-800/30 text-red-700 dark:text-red-300">
                        <AlertTriangle className="w-3 h-3" /> Active
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-300">
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
                      {ks.source === "model_state" ? (
                        <span
                          className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-medium bg-slate-100 dark:bg-slate-700 text-slate-500 dark:text-slate-400"
                          title="Isolation set in Model State & Risk Monitor — manage it there."
                        >
                          Risk-monitor
                        </span>
                      ) : actionLoading === ks.id ? (
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
