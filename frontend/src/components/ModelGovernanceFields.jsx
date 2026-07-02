/**
 * Model governance controls — allowlist + default model scoped to org-connected models only.
 */

import { useMemo } from "react";
import { AlertCircle, CheckSquare, Link2, Square } from "lucide-react";
import { parseAllowedModels, formatAllowedModelsForApi } from "../utils/firewallAllowlist";

function modelLabel(m) {
  if (m.model_id && m.model_id !== m.model_name) {
    return `${m.model_name} → ${m.model_id}`;
  }
  return m.model_name;
}

export function ModelGovernanceFields({
  connectedModels = [],
  staleModels = [],
  allowedModelsValue,
  defaultModel,
  modelIsolationEnabled,
  onAllowedChange,
  onDefaultChange,
  disabled = false,
}) {
  const connected = useMemo(
    () => (connectedModels || []).filter((m) => m?.model_name),
    [connectedModels],
  );

  const allowedSet = useMemo(
    () => new Set(parseAllowedModels(allowedModelsValue)),
    [allowedModelsValue],
  );

  const allowedNames = useMemo(() => [...allowedSet], [allowedSet]);

  const defaultOptions = useMemo(() => {
    if (modelIsolationEnabled && allowedNames.length > 0) {
      return connected.filter((m) => allowedSet.has(m.model_name));
    }
    return connected;
  }, [connected, allowedSet, allowedNames.length, modelIsolationEnabled]);

  const toggleModel = (modelName) => {
    const next = new Set(allowedSet);
    if (next.has(modelName)) {
      next.delete(modelName);
    } else {
      next.add(modelName);
    }
    const names = [...next];
    onAllowedChange(formatAllowedModelsForApi(names));
    if (defaultModel && !names.includes(defaultModel)) {
      onDefaultChange(names[0] || "");
    }
  };

  const selectAll = () => {
    const names = connected.map((m) => m.model_name);
    onAllowedChange(formatAllowedModelsForApi(names));
    if (!defaultModel && names[0]) {
      onDefaultChange(names[0]);
    }
  };

  const clearAll = () => {
    onAllowedChange("");
    onDefaultChange("");
  };

  if (!connected.length) {
    return (
      <div
        className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-4"
        role="status"
      >
        <div className="flex gap-2">
          <Link2 className="w-4 h-4 text-amber-700 dark:text-amber-300 shrink-0 mt-0.5" aria-hidden />
          <div>
            <p className="text-sm font-medium text-amber-900 dark:text-amber-100">
              No models connected yet
            </p>
            <p className="mt-1 text-xs text-amber-800/90 dark:text-amber-200/80">
              Add provider credentials under <strong>Model Connection</strong> below. Allowed and
              default models are limited to models your organization has connected.
            </p>
          </div>
        </div>
      </div>
    );
  }

  const stale = Array.isArray(staleModels) ? staleModels.filter(Boolean) : [];

  return (
    <div className="space-y-4">
      {stale.length > 0 && (
        <div
          className="rounded-lg border border-blue-500/30 bg-blue-500/10 p-4"
          role="status"
        >
          <div className="flex gap-2">
            <AlertCircle className="w-4 h-4 text-blue-700 dark:text-blue-300 shrink-0 mt-0.5" aria-hidden />
            <div>
              <p className="text-sm font-medium text-blue-900 dark:text-blue-100">
                Allowlist includes names not in your connections
              </p>
              <p className="mt-1 text-xs text-blue-800/90 dark:text-blue-200/80">
                The gateway enforces connected models only. Review or save governance to align:{" "}
                <span className="font-mono">{stale.join(", ")}</span>
              </p>
            </div>
          </div>
        </div>
      )}

      <div>
        <div className="flex flex-wrap items-center justify-between gap-2 mb-2">
          <label className="text-xs font-medium text-slate-600 dark:text-slate-400 uppercase tracking-wider">
            Allowed models
          </label>
          <div className="flex gap-2">
            <button
              type="button"
              disabled={disabled}
              onClick={selectAll}
              className="text-[11px] font-medium text-purple-700 dark:text-purple-300 hover:underline disabled:opacity-40"
            >
              Select all connected
            </button>
            <button
              type="button"
              disabled={disabled || !allowedNames.length}
              onClick={clearAll}
              className="text-[11px] font-medium text-slate-600 dark:text-slate-400 hover:underline disabled:opacity-40"
            >
              Clear
            </button>
          </div>
        </div>
        <p className="text-[11px] text-slate-600 dark:text-slate-400 mb-2">
          Only models from Model Connection appear here. When isolation is on, requests must use an
          allowed model name.
        </p>
        <ul
          className="space-y-1.5 max-h-56 overflow-y-auto rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900/40 p-2"
          aria-label="Allowed models"
        >
          {connected.map((m) => {
            const checked = allowedSet.has(m.model_name);
            const inactive = m.is_active === false;
            return (
              <li key={m.id ?? m.model_name}>
                <button
                  type="button"
                  disabled={disabled}
                  onClick={() => toggleModel(m.model_name)}
                  className={`w-full flex items-start gap-2.5 rounded-md px-2.5 py-2 text-left transition-colors min-h-[44px] ${
                    checked
                      ? "bg-purple-500/15 ring-1 ring-purple-500/40"
                      : "hover:bg-slate-100 dark:hover:bg-slate-800/60"
                  } ${disabled ? "opacity-50 cursor-not-allowed" : "cursor-pointer"}`}
                  aria-pressed={checked}
                >
                  {checked ? (
                    <CheckSquare className="w-4 h-4 text-purple-600 dark:text-purple-400 shrink-0 mt-0.5" />
                  ) : (
                    <Square className="w-4 h-4 text-slate-400 shrink-0 mt-0.5" />
                  )}
                  <span className="flex-1 min-w-0">
                    <span className="block text-sm font-mono text-slate-900 dark:text-white truncate">
                      {m.model_name}
                    </span>
                    <span className="block text-[10px] text-slate-500 dark:text-slate-400 truncate">
                      {m.provider_display || m.provider}
                      {m.model_id && m.model_id !== m.model_name ? ` · ${m.model_id}` : ""}
                      {m.api_key_set ? "" : m.api_key_env_var ? " · Env-var key" : " · API key missing"}
                      {inactive ? " · inactive" : ""}
                    </span>
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      </div>

      <div>
        <label
          htmlFor="governance-default-model"
          className="text-xs font-medium text-slate-600 dark:text-slate-400 uppercase tracking-wider mb-2 block"
        >
          Default model
        </label>
        <select
          id="governance-default-model"
          value={defaultModel || ""}
          disabled={disabled || !defaultOptions.length}
          onChange={(e) => onDefaultChange(e.target.value)}
          aria-label="Default model"
          className="w-full min-h-[44px] bg-white dark:bg-slate-900/50 border border-slate-300 dark:border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-900 dark:text-white font-mono appearance-none cursor-pointer hover:border-slate-400 dark:hover:border-slate-500 transition-colors disabled:opacity-50"
        >
          <option value="">— Select default —</option>
          {defaultOptions.map((m) => (
            <option key={m.id ?? m.model_name} value={m.model_name}>
              {modelLabel(m)}
            </option>
          ))}
        </select>
        <p className="mt-1.5 text-[10px] text-slate-600 dark:text-slate-400">
          Used when no model is specified, when routing is disabled, or as last-resort fallback.
        </p>
      </div>
    </div>
  );
}
