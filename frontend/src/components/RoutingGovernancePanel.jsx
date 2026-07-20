/**
 * RoutingGovernancePanel — configure global routing weights and sensitivity.
 *
 * Reads/writes to the existing FirewallConfig API at /api/firewall/config/.
 * On save the backend pushes the updated config to Redis and the gateway
 * hot-reloads routing weights without restart.
 */

import { useState, useEffect, useRef } from "react";
import { Save, RotateCcw, Info, Sliders, Power, Loader2 } from "lucide-react";
import { useAuth } from "../context/AuthContext";
import { useFirewallConfig } from "../hooks/useFirewallConfig";
import { PanelLoadingShell } from "./PanelLoadingShell";

const WEIGHT_KEYS = [
  { key: "routing_risk_weight", label: "Risk Avoidance", color: "rose", description: "Prefer lower-risk models" },
  { key: "routing_cost_weight", label: "Cost Efficiency", color: "emerald", description: "Prefer cheaper models" },
  { key: "routing_latency_weight", label: "Latency / Speed", color: "blue", description: "Prefer faster models" },
  { key: "routing_priority_weight", label: "Priority", color: "amber", description: "Prefer higher-priority models" },
];

const SENSITIVITY_OPTIONS = [
  { value: "public", label: "Public", color: "slate" },
  { value: "internal", label: "Internal", color: "blue" },
  { value: "confidential", label: "Confidential", color: "amber" },
  { value: "restricted", label: "Restricted", color: "rose" },
];

const PRESETS = [
  { name: "Balanced", weights: { routing_risk_weight: 0.30, routing_cost_weight: 0.20, routing_latency_weight: 0.20, routing_priority_weight: 0.30 } },
  { name: "Cost Optimized", weights: { routing_risk_weight: 0.15, routing_cost_weight: 0.50, routing_latency_weight: 0.20, routing_priority_weight: 0.15 } },
  { name: "Low Latency", weights: { routing_risk_weight: 0.15, routing_cost_weight: 0.15, routing_latency_weight: 0.55, routing_priority_weight: 0.15 } },
  { name: "Maximum Security", weights: { routing_risk_weight: 0.55, routing_cost_weight: 0.10, routing_latency_weight: 0.10, routing_priority_weight: 0.25 } },
  { name: "Quality First", weights: { routing_risk_weight: 0.20, routing_cost_weight: 0.10, routing_latency_weight: 0.10, routing_priority_weight: 0.60 } },
];

const WEIGHT_TEXT_STYLES = {
  rose: "text-rose-700 dark:text-rose-300",
  emerald: "text-emerald-700 dark:text-emerald-300",
  blue: "text-blue-700 dark:text-blue-300",
  amber: "text-amber-700 dark:text-amber-300",
};

const WEIGHT_ACCENT_STYLES = {
  rose: "accent-rose-500",
  emerald: "accent-emerald-500",
  blue: "accent-blue-500",
  amber: "accent-amber-500",
};

const SENSITIVITY_ACTIVE_STYLES = {
  slate: "bg-slate-500/20 text-slate-700 dark:text-slate-300 ring-1 ring-slate-500/50",
  blue: "bg-blue-500/20 text-blue-700 dark:text-blue-300 ring-1 ring-blue-500/50",
  amber: "bg-amber-500/20 text-amber-700 dark:text-amber-300 ring-1 ring-amber-500/50",
  rose: "bg-rose-500/20 text-rose-700 dark:text-rose-300 ring-1 ring-rose-500/50",
};

// Dynamic-routing toggle styles (emerald-on-emerald when on, slate-on-slate when
// off — kept as separate strings so text/bg colours never co-occur cross-state).
const ROUTING_TOGGLE_STYLES = {
  on: "bg-emerald-500/20 text-emerald-700 dark:text-emerald-300 ring-1 ring-emerald-500/50",
  off: "bg-slate-300 dark:bg-slate-700/70 text-slate-700 dark:text-slate-300 ring-1 ring-slate-400 dark:ring-slate-600",
};

function routingFromConfig(data) {
  const w = {
    routing_risk_weight: data.routing_risk_weight ?? 0.30,
    routing_cost_weight: data.routing_cost_weight ?? 0.20,
    routing_latency_weight: data.routing_latency_weight ?? 0.20,
    routing_priority_weight: data.routing_priority_weight ?? 0.30,
  };
  const sensitivity = data.default_data_sensitivity ?? "internal";
  const routingEnabled = typeof data.routing_enabled === "boolean" ? data.routing_enabled : true;
  return {
    weights: w,
    sensitivity,
    routingEnabled,
    defaultModel: data.default_model ?? "",
    serverState: {
      ...w,
      default_data_sensitivity: sensitivity,
      routing_enabled: routingEnabled,
    },
  };
}

export function RoutingGovernancePanel() {
  const { fetchWithAuth } = useAuth();
  const { config, loading, refreshing, error: configError, mergeConfig } = useFirewallConfig();
  const [weights, setWeights] = useState({
    routing_risk_weight: 0.30,
    routing_cost_weight: 0.20,
    routing_latency_weight: 0.20,
    routing_priority_weight: 0.30,
  });
  const [sensitivity, setSensitivity] = useState("internal");
  const [routingEnabled, setRoutingEnabled] = useState(true);
  const [defaultModelDisplay, setDefaultModelDisplay] = useState("");
  const [serverState, setServerState] = useState(null);
  const [saving, setSaving] = useState(false);
  const [saveOk, setSaveOk] = useState(false);
  const [error, setError] = useState(null);

  const isDirtyRef = useRef(false);

  useEffect(() => {
    if (!config) return;
    setDefaultModelDisplay(config.default_model ?? "");
    if (isDirtyRef.current) return;
    const next = routingFromConfig(config);
    setWeights(next.weights);
    setSensitivity(next.sensitivity);
    setRoutingEnabled(next.routingEnabled);
    setServerState(next.serverState);
  }, [config]);

  const total = Object.values(weights).reduce((s, v) => s + v, 0);
  const isDirty = serverState && (
    Object.keys(weights).some(k => Math.abs(weights[k] - serverState[k]) > 0.005) ||
    sensitivity !== serverState.default_data_sensitivity ||
    routingEnabled !== serverState.routing_enabled
  );

  isDirtyRef.current = Boolean(isDirty);

  const handleDiscard = () => {
    if (!serverState) return;
    setWeights({
      routing_risk_weight: serverState.routing_risk_weight,
      routing_cost_weight: serverState.routing_cost_weight,
      routing_latency_weight: serverState.routing_latency_weight,
      routing_priority_weight: serverState.routing_priority_weight,
    });
    setSensitivity(serverState.default_data_sensitivity);
    setRoutingEnabled(serverState.routing_enabled);
    setError(null);
  };

  const handleWeightChange = (key, raw) => {
    const val = Math.max(0, Math.min(1, parseFloat(raw) || 0));
    setWeights(prev => ({ ...prev, [key]: Math.round(val * 100) / 100 }));
  };

  const applyPreset = (preset) => {
    setWeights({ ...preset.weights });
  };

  const handleSave = async () => {
    setSaving(true);
    setSaveOk(false);
    setError(null);
    try {
      const payload = {
        ...weights,
        default_data_sensitivity: sensitivity,
        routing_enabled: routingEnabled,
      };
      const res = await fetchWithAuth("/api/firewall/config/", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (res.ok) {
        const data = await res.json();
        mergeConfig(data);
        const next = routingFromConfig(data);
        setWeights(next.weights);
        setSensitivity(next.sensitivity);
        setRoutingEnabled(next.routingEnabled);
        setDefaultModelDisplay(next.defaultModel);
        setServerState(next.serverState);
        setSaveOk(true);
        setTimeout(() => setSaveOk(false), 4000);
      } else {
        const body = await res.json().catch(() => null);
        const detail = body && typeof body === "object"
          ? Object.entries(body)
              .map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(", ") : v}`)
              .join("; ")
          : `Failed to save routing configuration (HTTP ${res.status}).`;
        setError(detail);
      }
    } catch {
      setError("Network error saving routing configuration.");
    } finally {
      setSaving(false);
    }
  };

  const displayError = error || configError;

  if (loading && !config) {
    return <PanelLoadingShell variant="routing" />;
  }

  return (
    <div className="relative bg-slate-100 dark:bg-slate-800/50 border border-slate-200 dark:border-slate-700 rounded-lg overflow-hidden">
      {refreshing && (
        <div
          className="absolute inset-0 z-10 bg-white/40 dark:bg-slate-900/40 pointer-events-none"
          aria-hidden
        />
      )}
      {/* Header */}
      <div className="px-5 py-4 border-b border-slate-200 dark:border-slate-700 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="p-2 bg-purple-500/10 rounded-lg">
            <Sliders className="w-5 h-5 text-purple-700 dark:text-purple-300" />
          </div>
          <div>
            <h3 className="text-base font-semibold text-slate-900 dark:text-white">Routing Governance</h3>
            <p className="text-xs text-slate-600 dark:text-slate-400">Configure routing strategy weights and default sensitivity</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {refreshing && (
            <Loader2 className="w-4 h-4 text-purple-500 animate-spin" aria-label="Refreshing" />
          )}
          {saveOk && <span className="text-xs text-emerald-700 dark:text-emerald-300">Saved &amp; synced to gateway</span>}
          {isDirty && (
            <button
              type="button"
              onClick={handleDiscard}
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
            {saving ? "Saving…" : "Save routing"}
          </button>
        </div>
      </div>

      {displayError && (
        <div className="mx-5 mt-3 p-2 bg-red-500/10 border border-red-500/30 text-red-700 dark:text-red-300 text-xs rounded" role="alert">
          {displayError}
        </div>
      )}

      <div className="p-5 space-y-5">
        {/* Presets */}
        <div className="bg-slate-200/50 dark:bg-slate-900/40 border border-slate-300 dark:border-slate-700/50 rounded-lg p-4">
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <Power className={`w-4 h-4 ${routingEnabled ? "text-emerald-700 dark:text-emerald-300" : "text-slate-500 dark:text-slate-400"}`} />
              <div>
                <p className="text-sm font-medium text-slate-800 dark:text-slate-200">Dynamic Routing</p>
                <p className="text-[11px] text-slate-600 dark:text-slate-400">
                  Controls whether ZeroShield policy adjudication runs for <span className="font-mono">/v1/chat/completions</span> requests.
                </p>
              </div>
            </div>
            <button
              type="button"
              onClick={() => setRoutingEnabled((v) => !v)}
              className={`min-h-[44px] px-4 py-2 rounded-lg text-xs font-semibold transition-colors ${routingEnabled ? ROUTING_TOGGLE_STYLES.on : ROUTING_TOGGLE_STYLES.off}`}
            >
              {routingEnabled ? "Enabled" : "Disabled"}
            </button>
          </div>
          <p className="mt-2 text-[10px] text-slate-600 dark:text-slate-400">
            API callers can override per request using <span className="font-mono">routing_preferences.enable_routing</span>.
            Set <span className="font-mono">enable_routing: false</span> in <span className="font-mono">extra_body</span> to pin a specific model; when omitted, org routing applies.
          </p>
        </div>

        {/* Presets */}
        <div>
          <label className="text-xs font-medium text-slate-600 dark:text-slate-400 uppercase tracking-wider mb-2 block">Strategy Presets</label>
          <div className="flex flex-wrap gap-2">
            {PRESETS.map(p => {
              const active = Object.keys(p.weights).every(k => Math.abs(weights[k] - p.weights[k]) < 0.005);
              return (
                <button
                  type="button"
                  key={p.name}
                  onClick={() => applyPreset(p)}
                  className={`min-h-[44px] px-4 py-2 rounded-full text-xs font-medium transition-all ${
                    active
                      ? "bg-purple-500/20 text-purple-700 dark:text-purple-300 ring-1 ring-purple-500/50"
                      : "bg-slate-300/60 dark:bg-slate-700/50 text-slate-700 dark:text-slate-300 hover:bg-slate-300 dark:hover:bg-slate-700"
                  }`}
                >
                  {p.name}
                </button>
              );
            })}
          </div>
        </div>

        {/* Weight sliders */}
        <div>
          <div className="flex items-center justify-between mb-2">
            <label className="text-xs font-medium text-slate-600 dark:text-slate-400 uppercase tracking-wider">Dimension Weights</label>
            <span className={`text-xs font-mono ${Math.abs(total - 1) < 0.005 ? "text-emerald-700 dark:text-emerald-300" : "text-amber-700 dark:text-amber-300"}`}>
              Σ = {total.toFixed(2)}
              {Math.abs(total - 1) > 0.005 && " (should be 1.00)"}
            </span>
          </div>
          <div className="space-y-3">
            {WEIGHT_KEYS.map(({ key, label, color, description }) => (
              <div key={key} className="space-y-1">
                <div className="flex items-center justify-between">
                  <span className="text-sm text-slate-800 dark:text-slate-200">{label}</span>
                  <span className={`text-xs font-mono ${WEIGHT_TEXT_STYLES[color]}`}>{(weights[key] * 100).toFixed(0)}%</span>
                </div>
                <div className="flex items-center gap-3">
                  <input
                    type="range"
                    min="0"
                    max="1"
                    step="0.05"
                    value={weights[key]}
                    onChange={e => handleWeightChange(key, e.target.value)}
                    aria-label={`${label} weight`}
                    className={`flex-1 h-1.5 rounded-full appearance-none cursor-pointer ${WEIGHT_ACCENT_STYLES[color]} bg-slate-300 dark:bg-slate-700`}
                  />
                  <input
                    type="number"
                    min="0"
                    max="1"
                    step="0.05"
                    value={weights[key]}
                    onChange={e => handleWeightChange(key, e.target.value)}
                    aria-label={`${label} weight value`}
                    className="w-16 bg-white dark:bg-slate-900/50 border border-slate-300 dark:border-slate-600 rounded px-2 py-0.5 text-xs text-slate-900 dark:text-white text-center font-mono"
                  />
                </div>
                <p className="text-[10px] text-slate-600 dark:text-slate-400">{description}</p>
              </div>
            ))}
          </div>
        </div>

        {/* Default Sensitivity */}
        <div>
          <label className="text-xs font-medium text-slate-600 dark:text-slate-400 uppercase tracking-wider mb-2 block">
            Default Data Sensitivity
          </label>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            {SENSITIVITY_OPTIONS.map(opt => (
              <button
                type="button"
                key={opt.value}
                onClick={() => setSensitivity(opt.value)}
                className={`min-h-[44px] px-3 py-2 rounded-lg text-xs font-medium transition-all text-center ${
                  sensitivity === opt.value
                    ? SENSITIVITY_ACTIVE_STYLES[opt.color]
                    : "bg-slate-300/60 dark:bg-slate-700/40 text-slate-700 dark:text-slate-400 hover:bg-slate-300 dark:hover:bg-slate-700/70"
                }`}
              >
                {opt.label}
              </button>
            ))}
          </div>
          <p className="mt-1.5 text-[10px] text-slate-600 dark:text-slate-400">
            Applied when requests don't specify a sensitivity level. Models below this level are excluded from routing.
          </p>
        </div>

        {/* Default model (read-only — edited in Model allowlist panel above) */}
        <div className="bg-slate-200/40 dark:bg-slate-900/30 border border-slate-300 dark:border-slate-700/50 rounded-lg px-4 py-3">
          <p className="text-xs font-medium text-slate-600 dark:text-slate-400 uppercase tracking-wider mb-1">
            Default fallback model
          </p>
          <p className="text-sm font-mono text-slate-900 dark:text-white">
            {defaultModelDisplay || "— Not set —"}
          </p>
          <p className="mt-1.5 text-[10px] text-slate-600 dark:text-slate-400">
            Configure in the <strong>Model allowlist &amp; default</strong> panel above. Used when routing is off or no model is specified.
          </p>
        </div>

        {/* How it works */}
        <div className="bg-slate-200/50 dark:bg-slate-900/40 border border-slate-300 dark:border-slate-700/50 rounded-lg p-4">
          <div className="flex items-center gap-2 mb-2">
            <Info className="w-4 h-4 text-blue-700 dark:text-blue-300" />
            <span className="text-xs font-semibold text-blue-700 dark:text-blue-300">How Model Routing Works</span>
          </div>
          <ol className="text-[11px] text-slate-600 dark:text-slate-400 space-y-1.5 list-decimal list-inside">
            <li><b className="text-slate-800 dark:text-slate-300">Hard filters</b> — models that don't meet compliance tags, sensitivity level, tenant allowlists, or active-state checks are removed.</li>
            <li><b className="text-slate-800 dark:text-slate-300">Weighted scoring</b> — each remaining model is scored from the same governance settings you configure here:
              <code className="block mt-0.5 ml-4 text-[10px] text-purple-700/80 dark:text-purple-300/80 font-mono">
                score = w_risk × (1 − model_risk)(1 − request_risk) + w_cost × 1/(1 + cost×tokens/1000) + w_latency × min(1, budget/latency) + w_priority × priority/max_priority
              </code>
            </li>
            <li><b className="text-slate-800 dark:text-slate-300">ZeroShield adjudication</b> — when Dynamic Routing is enabled, the ZeroShield Policy Adjudicator analyzes candidate scores plus request context (data sensitivity, compliance requirements, token budget/cost pressure, latency SLA, and risk) and decides whether to keep the preferred model or reroute.</li>
            <li><b className="text-slate-800 dark:text-slate-300">API output + audit</b> — every chat-completion response includes routing metadata and headers (`selected_model`, `original_model`, `rerouted`, `routing_reason`, `decision_source`, `policy_summary`, `decision_factors`) and emits the same decision as telemetry.</li>
          </ol>
        </div>
      </div>
    </div>
  );
}
