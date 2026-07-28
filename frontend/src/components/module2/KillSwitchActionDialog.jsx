import { useEffect, useMemo, useState } from "react";
import { Loader2 } from "lucide-react";
import { describeContainmentSemantics } from "../../api/killSwitch";

export function KillSwitchActionDialog({
  open,
  title = "Activate kill switch",
  targetLabel = "",
  allowedModels = [],
  preferredModel = "",
  initialApiKeyPrefix = "",
  defaultReason = "",
  keyIsActive = true,
  isSimulatorKey = false,
  activeSimulatorPrefix = "",
  loading = false,
  onClose,
  onSubmit,
}) {
  const [modelName, setModelName] = useState("");
  const [customModel, setCustomModel] = useState("");
  const [apiKeyPrefix, setApiKeyPrefix] = useState("");
  const [action, setAction] = useState("disable");
  const [fallbackModel, setFallbackModel] = useState("");
  const [reason, setReason] = useState("");
  const [validationError, setValidationError] = useState("");
  const semantics = useMemo(() => describeContainmentSemantics(), []);

  useEffect(() => {
    if (!open) return;
    const preferred = String(preferredModel || "").trim();
    const initial = (preferred && allowedModels.includes(preferred))
      ? preferred
      : (allowedModels[0] || preferred || "");
    setModelName(initial && allowedModels.includes(initial) ? initial : (allowedModels[0] || ""));
    setCustomModel(initial && !allowedModels.includes(initial) ? initial : "");
    setApiKeyPrefix(String(initialApiKeyPrefix || "").trim());
    setAction("disable");
    setFallbackModel("");
    setReason(String(defaultReason || "SOC API key containment (analyst)").trim());
    setValidationError("");
  }, [open, allowedModels, preferredModel, initialApiKeyPrefix, defaultReason]);

  const resolvedModel = useMemo(() => {
    const custom = String(customModel || "").trim();
    if (custom) return custom;
    return String(modelName || "").trim();
  }, [customModel, modelName]);

  const fallbackOptions = useMemo(
    () => allowedModels.filter((name) => name !== resolvedModel),
    [allowedModels, resolvedModel],
  );

  if (!open) return null;

  const submit = () => {
    if (!resolvedModel) {
      setValidationError("Select or enter the exact client-requested model to kill.");
      return;
    }
    if (action === "reroute" && !fallbackModel) {
      setValidationError("Select a fallback model for reroute.");
      return;
    }
    if (action === "reroute" && fallbackModel === resolvedModel) {
      setValidationError("Fallback model must be different from the blocked model.");
      return;
    }
    setValidationError("");
    onSubmit?.({
      modelName: resolvedModel,
      apiKeyPrefix: apiKeyPrefix.trim(),
      action,
      fallbackModel: action === "reroute" ? fallbackModel : "",
      reason: reason.trim(),
    });
  };

  const hasModels = allowedModels.length > 0 || Boolean(String(customModel || "").trim());
  const livePrefix = String(activeSimulatorPrefix || "").trim();
  const targetPrefix = String(targetLabel || initialApiKeyPrefix || "").trim();
  const staleSimulator = Boolean(
    isSimulatorKey && livePrefix && targetPrefix && livePrefix !== targetPrefix,
  );

  return (
    <div className="fixed inset-0 z-[120] flex items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-lg rounded-2xl border border-slate-200 bg-white p-5 shadow-xl dark:border-slate-700 dark:bg-slate-900">
        <h4 className="text-sm font-semibold text-slate-900 dark:text-white">{title}</h4>
        {targetLabel && (
          <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
            Target: <span className="font-mono">{targetLabel}</span>
          </p>
        )}
        <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
          Gateway matches the <strong>client-requested</strong>{" "}
          <span className="font-mono">body.model</span> + this key prefix before routing.
          Killing a post-routing served model will not stop Attack Simulator traffic.
        </p>
        <div className="mt-2 space-y-1 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-600 dark:border-slate-600 dark:bg-slate-800/60 dark:text-slate-300">
          <p><strong>Kill switch:</strong> {semantics.killSwitch}</p>
          <p><strong>Disable API key:</strong> {semantics.disableKey}</p>
        </div>
        {!keyIsActive && (
          <p className="mt-2 text-xs text-amber-700 dark:text-amber-300">
            This key is currently disabled. Attack Simulator may mint a <strong>new</strong> active
            simulator key — activate the kill switch on the live simulator prefix after the next
            simulator run, or re-enable this key first.
          </p>
        )}
        {staleSimulator && (
          <p className="mt-2 text-xs text-red-700 dark:text-red-300">
            Stale simulator target. Module 1 active simulator prefix is{" "}
            <span className="font-mono font-semibold">{livePrefix}</span>, not{" "}
            <span className="font-mono">{targetPrefix}</span>. Activation will be blocked until you
            select the live key.
          </p>
        )}
        {isSimulatorKey && preferredModel && (
          <p className="mt-2 text-xs text-teal-700 dark:text-teal-300">
            Attack Simulator currently selects{" "}
            <span className="font-mono font-semibold">{preferredModel}</span> — prefer killing that
            exact name.
          </p>
        )}

        <div className="mt-4 space-y-3">
          <label className="block text-xs font-semibold text-slate-600 dark:text-slate-300">
            Model (client-requested)
            <select
              className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm dark:border-slate-600 dark:bg-slate-800"
              value={modelName}
              onChange={(event) => {
                setModelName(event.target.value);
                setCustomModel("");
              }}
              disabled={loading || !allowedModels.length}
            >
              {!allowedModels.length && <option value="">Enter model below</option>}
              {allowedModels.map((name) => (
                <option key={name} value={name}>{name}</option>
              ))}
            </select>
          </label>

          <label className="block text-xs font-semibold text-slate-600 dark:text-slate-300">
            Or type exact model name
            <input
              className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 font-mono text-sm dark:border-slate-600 dark:bg-slate-800"
              value={customModel}
              onChange={(event) => setCustomModel(event.target.value)}
              placeholder="e.g. gpt-4o-mini"
              disabled={loading}
            />
          </label>

          <label className="block text-xs font-semibold text-slate-600 dark:text-slate-300">
            API key prefix
            <input
              className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 font-mono text-sm dark:border-slate-600 dark:bg-slate-800"
              value={apiKeyPrefix}
              onChange={(event) => setApiKeyPrefix(event.target.value)}
              disabled={loading}
            />
          </label>

          <label className="block text-xs font-semibold text-slate-600 dark:text-slate-300">
            Action
            <select
              className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm dark:border-slate-600 dark:bg-slate-800"
              value={action}
              onChange={(event) => setAction(event.target.value)}
              disabled={loading}
            >
              <option value="disable">Disable (block at Kill Switch · 503)</option>
              <option value="reroute">Reroute to fallback model</option>
            </select>
          </label>

          {action === "reroute" && (
            <label className="block text-xs font-semibold text-slate-600 dark:text-slate-300">
              Fallback model
              <select
                className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm dark:border-slate-600 dark:bg-slate-800"
                value={fallbackModel}
                onChange={(event) => setFallbackModel(event.target.value)}
                disabled={loading}
              >
                <option value="">Select fallback</option>
                {fallbackOptions.map((name) => (
                  <option key={name} value={name}>{name}</option>
                ))}
              </select>
            </label>
          )}

          <label className="block text-xs font-semibold text-slate-600 dark:text-slate-300">
            Reason
            <textarea
              className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm dark:border-slate-600 dark:bg-slate-800"
              rows={2}
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              disabled={loading}
            />
          </label>
        </div>

        {(validationError || !hasModels) && (
          <p className="mt-3 text-xs text-red-600 dark:text-red-400">
            {validationError || "No models available — type the exact client-requested model."}
          </p>
        )}

        <div className="mt-5 flex justify-end gap-2">
          <button
            type="button"
            className="rounded-lg border border-slate-300 px-3 py-2 text-xs font-semibold text-slate-700 hover:bg-slate-100 dark:border-slate-600 dark:text-slate-200 dark:hover:bg-slate-800"
            onClick={onClose}
            disabled={loading}
          >
            Cancel
          </button>
          <button
            type="button"
            className="inline-flex items-center gap-1.5 rounded-lg bg-red-600 px-3 py-2 text-xs font-semibold text-white hover:bg-red-700 disabled:opacity-60"
            onClick={submit}
            disabled={loading || staleSimulator}
          >
            {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
            Activate
          </button>
        </div>
      </div>
    </div>
  );
}
