import { Loader2, Server, AlertTriangle } from "lucide-react";

/**
 * Simulator inference target — org models with API keys synced to the gateway.
 * Avoids sending `auto`, which fails global allowlists when model isolation is on.
 */
export function SimulatorModelSelector({
  eligibleModels = [],
  selectedModel = "",
  onSelectModel,
  loading = false,
  loadError = null,
  firewallDefault = "",
  allowlistBlocksSimulator = false,
  allowedModels = [],
  onSyncAllowlist = null,
  allowlistSyncing = false,
  allowlistSyncError = null,
  showModelPicker = false,
  isSingleModel = false,
}) {
  if (loading) {
    return (
      <div className="flex items-center gap-2 text-xs text-slate-500 dark:text-slate-400">
        <Loader2 className="w-3.5 h-3.5 animate-spin" />
        Loading gateway-connected models…
      </div>
    );
  }

  if (loadError) {
    return (
      <p className="text-xs text-amber-700 dark:text-amber-300 flex items-center gap-1.5">
        <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
        {loadError}
      </p>
    );
  }

  if (eligibleModels.length === 0) {
    return (
      <div className="rounded-lg border border-violet-200 dark:border-violet-800 bg-violet-50/80 dark:bg-violet-900/20 px-3 py-2.5 text-xs text-violet-900 dark:text-violet-100">
        <span className="font-medium">No inference model connected.</span>{" "}
        Add a model under <strong>Model Connection</strong> with an API key, then return here.
      </div>
    );
  }

  if (isSingleModel && selectedModel) {
    const entry = eligibleModels.find((m) => m.model_name === selectedModel);
    return (
      <div className="flex items-center gap-2 text-xs text-slate-600 dark:text-slate-300">
        <Server className="w-3.5 h-3.5 text-teal-600" />
        <span>
          Simulator model:{" "}
          <span className="font-semibold text-slate-900 dark:text-slate-100">{selectedModel}</span>
          {entry?.provider_display ? (
            <span className="text-slate-500 dark:text-slate-400"> ({entry.provider_display})</span>
          ) : null}
        </span>
      </div>
    );
  }

  if (!showModelPicker) return null;

  return (
    <div>
      <label className="text-[11px] font-medium text-slate-600 dark:text-slate-400 flex items-center gap-1 mb-1.5">
        <Server className="w-3 h-3" />
        Simulator model
      </label>
      <select
        value={selectedModel}
        onChange={(e) => onSelectModel(e.target.value)}
        className="w-full max-w-md px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm bg-white dark:bg-slate-800 text-slate-900 dark:text-slate-100 focus:ring-2 focus:ring-teal-500 focus:border-transparent"
      >
        {eligibleModels.map((m) => (
          <option key={m.id ?? m.model_name} value={m.model_name}>
            {m.model_name}
            {m.provider_display ? ` — ${m.provider_display}` : ""}
          </option>
        ))}
      </select>
      {firewallDefault && firewallDefault !== selectedModel ? (
        <p className="mt-1 text-[10px] text-slate-500 dark:text-slate-400">
          Routing default in governance: {firewallDefault}
        </p>
      ) : null}
      {allowlistBlocksSimulator ? (
        <div className="mt-2 text-[11px] text-amber-800 dark:text-amber-200 rounded-md border border-amber-200 dark:border-amber-800 bg-amber-50/90 dark:bg-amber-900/25 px-2.5 py-2 space-y-2">
          <p>
            <strong>{selectedModel}</strong> is connected to the gateway but missing from the firewall{" "}
            <strong>Allowed Models</strong> list
            {allowedModels.length ? ` (${allowedModels.join(", ")})` : ""}. That causes
            &quot;not in the global allowlist&quot; even when the simulator sends the correct model name.
          </p>
          {onSyncAllowlist ? (
            <button
              type="button"
              disabled={allowlistSyncing}
              onClick={() => onSyncAllowlist()}
              className="rounded-lg bg-amber-600 hover:bg-amber-700 disabled:opacity-60 text-white text-xs font-medium px-3 py-1.5 transition-colors"
            >
              {allowlistSyncing ? "Updating allowlist…" : `Add ${selectedModel} to allowed models`}
            </button>
          ) : null}
          {allowlistSyncError ? (
            <p className="text-red-700 dark:text-red-300">{allowlistSyncError}</p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
