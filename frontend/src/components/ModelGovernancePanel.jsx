/**
 * Model allowlist + default model — scoped to org-connected models.
 * Subscribes to FirewallConfigProvider (Module 1.5).
 */

import { useState, useEffect, useRef } from "react";
import { Database, Save, RotateCcw, Loader2 } from "lucide-react";
import { useAuth } from "../context/AuthContext";
import { useFirewallConfig } from "../hooks/useFirewallConfig";
import { ModelGovernanceFields } from "./ModelGovernanceFields";
import { PanelLoadingShell } from "./PanelLoadingShell";
import { formatAllowedModelsForApi, parseAllowedModels } from "../utils/firewallAllowlist";
import { filterUserManagedModels } from "../constants/zeroshieldBrand";

function governanceFromConfig(data) {
  const allowed = formatAllowedModelsForApi(
    data.allowed_models_list ?? parseAllowedModels(data.allowed_models),
  );
  return {
    allowed,
    defaultM: data.default_model ?? "",
    isolation: data.model_isolation_enabled ?? true,
  };
}

export function ModelGovernancePanel() {
  const { fetchWithAuth } = useAuth();
  const {
    config,
    loading,
    refreshing,
    error: configError,
    mergeConfig,
    governanceStaleModels,
  } = useFirewallConfig();

  const [allowedModels, setAllowedModels] = useState("");
  const [defaultModel, setDefaultModel] = useState("");
  const [modelIsolationEnabled, setModelIsolationEnabled] = useState(true);
  const [serverSnapshot, setServerSnapshot] = useState(null);
  const [saving, setSaving] = useState(false);
  const [saveOk, setSaveOk] = useState(false);
  const [error, setError] = useState(null);

  const isDirtyRef = useRef(false);

  const isDirty =
    serverSnapshot &&
    (allowedModels !== serverSnapshot.allowed ||
      defaultModel !== serverSnapshot.defaultM ||
      modelIsolationEnabled !== serverSnapshot.isolation);

  isDirtyRef.current = Boolean(isDirty);

  useEffect(() => {
    if (!config || isDirtyRef.current) return;
    const { allowed, defaultM, isolation } = governanceFromConfig(config);
    setAllowedModels(allowed);
    setDefaultModel(defaultM);
    setModelIsolationEnabled(isolation);
    setServerSnapshot({ allowed, defaultM, isolation });
  }, [config]);

  const handleSave = async () => {
    setSaving(true);
    setSaveOk(false);
    setError(null);
    try {
      const res = await fetchWithAuth("/api/firewall/config/", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          allowed_models: allowedModels,
          default_model: defaultModel,
          model_isolation_enabled: modelIsolationEnabled,
        }),
      });
      if (res.ok) {
        const data = await res.json();
        mergeConfig(data);
        const { allowed, defaultM, isolation } = governanceFromConfig(data);
        setAllowedModels(allowed);
        setDefaultModel(defaultM);
        setModelIsolationEnabled(isolation);
        setServerSnapshot({ allowed, defaultM, isolation });
        setSaveOk(true);
        setTimeout(() => setSaveOk(false), 4000);
      } else if (res.status === 400) {
        const body = await res.json().catch(() => null);
        const detail = body
          ? Object.entries(body)
              .map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(", ") : v}`)
              .join("; ")
          : "Invalid model governance values.";
        setError(detail);
      } else {
        setError("Failed to save model governance settings.");
      }
    } catch {
      setError("Network error saving model governance settings.");
    } finally {
      setSaving(false);
    }
  };

  const handleReset = () => {
    if (serverSnapshot) {
      setAllowedModels(serverSnapshot.allowed);
      setDefaultModel(serverSnapshot.defaultM);
      setModelIsolationEnabled(serverSnapshot.isolation);
    }
    setError(null);
  };

  const displayError = error || configError;
  // Strip platform/guard (ZeroShield) entries so the reserved guard model and
  // its raw upstream id never surface in the org governance allowlist UI —
  // mirrors ModelStatePanel / ModelConnectionPanel / kill-switch.
  const connectedModels = filterUserManagedModels(
    Array.isArray(config?.connected_models) ? config.connected_models : [],
  );

  if (loading && !config) {
    return <PanelLoadingShell variant="governance" rows={5} />;
  }

  return (
    <div className="relative bg-slate-100 dark:bg-slate-800/50 border border-slate-200 dark:border-slate-700 rounded-lg overflow-hidden">
      {refreshing && (
        <div
          className="absolute inset-0 z-10 bg-white/40 dark:bg-slate-900/40 pointer-events-none"
          aria-hidden
        />
      )}
      <div className="px-5 py-4 border-b border-slate-200 dark:border-slate-700 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <div className="p-2 bg-purple-500/10 rounded-lg">
            <Database className="w-5 h-5 text-purple-700 dark:text-purple-300" aria-hidden />
          </div>
          <div>
            <h3 className="text-base font-semibold text-slate-900 dark:text-white">Model allowlist &amp; default</h3>
            <p className="text-xs text-slate-600 dark:text-slate-400">
              Only models connected under Model Connection can be allowed or set as default.
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {refreshing && (
            <Loader2 className="w-4 h-4 text-purple-500 animate-spin" aria-label="Refreshing" />
          )}
          {saveOk && (
            <span className="text-xs text-emerald-700 dark:text-emerald-300" aria-live="polite">
              Saved
            </span>
          )}
          {isDirty && (
            <button
              type="button"
              onClick={handleReset}
              disabled={saving}
              className="p-2 min-h-[44px] min-w-[44px] flex items-center justify-center text-slate-500 dark:text-slate-400 hover:text-slate-800 dark:hover:text-white rounded transition-colors"
              title="Discard changes"
              aria-label="Discard changes"
            >
              <RotateCcw className="w-4 h-4" />
            </button>
          )}
          <button
            type="button"
            onClick={handleSave}
            disabled={saving || !isDirty}
            className="flex items-center gap-1.5 min-h-[44px] px-4 py-2 rounded-lg text-xs font-medium transition-colors disabled:opacity-40 disabled:cursor-not-allowed bg-purple-600 hover:bg-purple-500 text-white"
          >
            <Save className="w-3.5 h-3.5" aria-hidden />
            {saving ? "Saving…" : "Save governance"}
          </button>
        </div>
      </div>

      {displayError && (
        <div
          className="mx-5 mt-3 p-3 bg-red-500/10 border border-red-500/30 text-red-700 dark:text-red-300 text-xs rounded-lg"
          role="alert"
        >
          {displayError}
        </div>
      )}

      <div className="p-5 space-y-4">
        <label className="flex items-center gap-3 min-h-[44px] cursor-pointer">
          <input
            type="checkbox"
            checked={modelIsolationEnabled}
            onChange={(e) => setModelIsolationEnabled(e.target.checked)}
            disabled={saving}
            className="w-4 h-4 rounded border-slate-300 text-purple-600 focus:ring-purple-500"
          />
          <span className="text-sm text-slate-800 dark:text-slate-200">
            Model isolation — requests must use an allowed model name
          </span>
        </label>

        <ModelGovernanceFields
          connectedModels={connectedModels}
          staleModels={governanceStaleModels}
          allowedModelsValue={allowedModels}
          defaultModel={defaultModel}
          modelIsolationEnabled={modelIsolationEnabled}
          disabled={saving}
          onAllowedChange={setAllowedModels}
          onDefaultChange={setDefaultModel}
        />
      </div>
    </div>
  );
}
