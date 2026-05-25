import { useState } from "react";
import { Wifi, WifiOff, Activity, RefreshCw, ChevronDown, ChevronUp, Copy, Check } from "lucide-react";

const STATUS_STYLES = {
  connected: { color: "text-emerald-400", bg: "bg-emerald-500/10", label: "Connected" },
  degraded: { color: "text-amber-400", bg: "bg-amber-500/10", label: "Degraded" },
  disconnected: { color: "text-red-400", bg: "bg-red-500/10", label: "Disconnected" },
};

/**
 * Shared simulator layout wrapper.
 * Provides: connection banner, scenario selector, execute button,
 * result pane with raw/JSON toggle, and trace ID display.
 */
export function SimulatorShell({
  title,
  description,
  connectionStatus = "disconnected",
  gatewayUrl = "",
  gatewayKey = "",
  onKeyChange,
  scenarios = [],
  selectedScenario,
  onSelectScenario,
  onExecute,
  executing = false,
  result = null,
  children,
  className = "",
  customInput,
  extraActions,
}) {
  const [showConfig, setShowConfig] = useState(!gatewayKey);
  const [showRawJson, setShowRawJson] = useState(false);
  const [copied, setCopied] = useState(false);

  const status = STATUS_STYLES[connectionStatus] || STATUS_STYLES.disconnected;

  const handleCopyTrace = () => {
    if (result?.request_id) {
      navigator.clipboard.writeText(result.request_id);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  return (
    <div className={`ai-mesh-card ai-mesh-grid-bg overflow-hidden rounded-[26px] ${className}`}>
      {/* Header */}
      <div className="border-b border-slate-200 px-5 py-4 dark:border-slate-700">
        <div className="flex items-center justify-between">
          <div>
            <h3 className="text-base font-semibold text-slate-900 dark:text-slate-100">{title}</h3>
            {description && <p className="mt-0.5 text-sm text-slate-500 dark:text-slate-400">{description}</p>}
          </div>
          <div className="flex items-center gap-2">
            <div className={`flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-semibold ${status.bg} ${status.color}`}>
              {connectionStatus === "connected" ? <Wifi className="w-3 h-3" /> : <WifiOff className="w-3 h-3" />}
              {status.label}
            </div>
            <button
              onClick={() => setShowConfig(!showConfig)}
              className="rounded-lg p-1 text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-700 dark:text-slate-400 dark:hover:bg-slate-800 dark:hover:text-slate-200"
            >
              {showConfig ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
            </button>
          </div>
        </div>
      </div>

      {/* Connection config (collapsible) */}
      {showConfig && (
        <div className="border-b border-slate-200 bg-slate-50/70 px-5 py-4 dark:border-slate-700 dark:bg-slate-900/35">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <div>
              <label className="text-xs font-medium text-slate-600 dark:text-slate-300">Gateway URL</label>
              <input
                type="text"
                value={gatewayUrl}
                readOnly
                className="mt-1 w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs text-slate-700 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300"
              />
            </div>
            <div>
              <label className="text-xs font-medium text-slate-600 dark:text-slate-300">Gateway API Key</label>
              <input
                type="password"
                value={gatewayKey}
                onChange={(e) => onKeyChange?.(e.target.value)}
                placeholder="Enter gateway API key..."
                className="mt-1 w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs text-slate-700 placeholder:text-slate-400 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300 dark:placeholder:text-slate-500"
              />
            </div>
          </div>
        </div>
      )}

      {/* Scenario selector */}
      {scenarios.length > 0 && (
        <div className="border-b border-slate-200 px-5 py-4 dark:border-slate-700">
          <label className="mb-2 block text-xs font-medium text-slate-600 dark:text-slate-300">Test Scenario</label>
          <div className="flex flex-wrap gap-1.5">
            {scenarios.map((s) => (
              <button
                key={s.id}
                onClick={() => onSelectScenario?.(s)}
                className={`rounded-xl border px-3 py-1.5 text-xs font-medium transition-all
                  ${selectedScenario?.id === s.id
                    ? "border-teal-300 bg-teal-50 text-teal-700 dark:border-teal-700 dark:bg-teal-900/30 dark:text-teal-300"
                    : "border-slate-200 bg-white text-slate-600 hover:border-slate-300 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900/50 dark:text-slate-300 dark:hover:bg-slate-800"
                  }`}
              >
                {s.badge && (
                  <span className={`inline-block w-1.5 h-1.5 rounded-full mr-1.5 ${
                    s.badge === "safe" ? "bg-emerald-500" :
                    s.badge === "attack" ? "bg-red-500" : "bg-amber-500"
                  }`} />
                )}
                {s.label}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Custom input area */}
      {customInput && (
        <div className="border-b border-slate-200 px-5 py-4 dark:border-slate-700">
          {customInput}
        </div>
      )}

      {/* Execute button row */}
      <div className="flex items-center gap-2 border-b border-slate-200 px-5 py-4 dark:border-slate-700">
        <button
          onClick={onExecute}
          disabled={executing || connectionStatus === "disconnected"}
          className={`flex items-center gap-1.5 rounded-xl px-4 py-2 text-sm font-semibold transition-all
            ${executing
              ? "bg-slate-300 text-slate-500 cursor-wait dark:bg-slate-700 dark:text-slate-400"
              : connectionStatus === "disconnected"
              ? "bg-slate-200 text-slate-500 cursor-not-allowed dark:bg-slate-700 dark:text-slate-500"
              : "bg-teal-600 hover:bg-teal-700 text-white dark:bg-teal-500 dark:hover:bg-teal-400"
            }`}
        >
          {executing ? (
            <><RefreshCw className="w-3.5 h-3.5 animate-spin" /> Running...</>
          ) : (
            <><Activity className="w-3.5 h-3.5" /> Execute</>
          )}
        </button>
        {extraActions}
        {result?.request_id && (
          <div className="ml-auto flex items-center gap-1.5 text-xs text-slate-500 dark:text-slate-400">
            <span>Trace:</span>
            <code className="font-mono text-slate-700 dark:text-slate-300">{result.request_id}</code>
            <button onClick={handleCopyTrace} className="text-slate-500 hover:text-slate-700 dark:text-slate-400 dark:hover:text-slate-200">
              {copied ? <Check className="w-3 h-3 text-emerald-400" /> : <Copy className="w-3 h-3" />}
            </button>
          </div>
        )}
      </div>

      {/* Result pane */}
      {result && (
        <div className="px-5 py-4">
          <div className="flex items-center gap-2 mb-2">
            <ResultBadge action={result.final_action || result.action || (result.success === false ? "error" : "allow")} />
            {result.total_latency_ms !== undefined && (
              <span className="text-[10px] text-slate-500 dark:text-slate-400">{result.total_latency_ms}ms</span>
            )}
            {result.latency_ms !== undefined && (
              <span className="text-[10px] text-slate-500 dark:text-slate-400">{result.latency_ms}ms</span>
            )}
            <button
              onClick={() => setShowRawJson(!showRawJson)}
              className="ml-auto text-[10px] text-slate-500 hover:text-slate-700 dark:text-slate-400 dark:hover:text-slate-200"
            >
              {showRawJson ? "Hide JSON" : "Show JSON"}
            </button>
          </div>
          {showRawJson && (
            <pre className="max-h-64 overflow-auto rounded-xl border border-slate-200 bg-slate-950 p-3 font-mono text-[11px] text-slate-200 dark:border-slate-700">
              {JSON.stringify(result, null, 2)}
            </pre>
          )}
        </div>
      )}

      {/* Module-specific content */}
      {children}
    </div>
  );
}

function ResultBadge({ action }) {
  const styles = {
    allow: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300 border-emerald-500/30",
    block: "bg-red-500/15 text-red-700 dark:text-red-300 border-red-500/30",
    flag: "bg-amber-500/15 text-amber-700 dark:text-amber-300 border-amber-500/30",
    redact: "bg-blue-500/15 text-blue-700 dark:text-blue-300 border-blue-500/30",
    error: "bg-red-500/15 text-red-700 dark:text-red-300 border-red-500/30",
  };
  return (
    <span className={`px-2 py-0.5 rounded-md text-[11px] font-bold uppercase border ${styles[action] || styles.error}`}>
      {action}
    </span>
  );
}
