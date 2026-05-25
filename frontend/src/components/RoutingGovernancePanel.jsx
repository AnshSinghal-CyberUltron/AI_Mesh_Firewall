/**
 * RoutingGovernancePanel — configure global routing weights and sensitivity.
 *
 * Reads/writes to the existing FirewallConfig API at /api/firewall/config/.
 * On save the backend pushes the updated config to Redis and the gateway
 * hot-reloads routing weights without restart.
 */

import { useState, useEffect, useCallback } from "react";
import { Settings, Save, RotateCcw, Info, Sliders, Power } from "lucide-react";
import { useAuth } from "../context/AuthContext";

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

export function RoutingGovernancePanel() {
  const { fetchWithAuth } = useAuth();
  const [weights, setWeights] = useState({
    routing_risk_weight: 0.30,
    routing_cost_weight: 0.20,
    routing_latency_weight: 0.20,
    routing_priority_weight: 0.30,
  });
  const [sensitivity, setSensitivity] = useState("internal");
  const [routingEnabled, setRoutingEnabled] = useState(true);
  const [defaultModel, setDefaultModel] = useState("gpt-4");
  const [registeredModels, setRegisteredModels] = useState([]);
  const [serverState, setServerState] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saveOk, setSaveOk] = useState(false);
  const [error, setError] = useState(null);

  const fetchModels = useCallback(async () => {
    try {
      const res = await fetchWithAuth("/api/firewall/models/");
      if (res.ok) {
        const data = await res.json();
        const list = Array.isArray(data) ? data : data.results ?? [];
        setRegisteredModels(list.filter(m => m.is_active !== false));
      }
    } catch {
      setRegisteredModels([]);
    }
  }, [fetchWithAuth]);

  const fetchConfig = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetchWithAuth("/api/firewall/config/");
      if (res.ok) {
        const data = await res.json();
        const w = {
          routing_risk_weight: data.routing_risk_weight ?? 0.30,
          routing_cost_weight: data.routing_cost_weight ?? 0.20,
          routing_latency_weight: data.routing_latency_weight ?? 0.20,
          routing_priority_weight: data.routing_priority_weight ?? 0.30,
        };
        setWeights(w);
        setSensitivity(data.default_data_sensitivity ?? "internal");
        const enabled = typeof data.routing_enabled === "boolean" ? data.routing_enabled : true;
        setRoutingEnabled(enabled);
        setDefaultModel(data.default_model ?? "gpt-4");
        setServerState({ ...w, default_data_sensitivity: data.default_data_sensitivity ?? "internal", routing_enabled: enabled, default_model: data.default_model ?? "gpt-4" });
      } else {
        setError("Failed to load routing configuration.");
      }
    } catch {
      setError("Network error loading routing configuration.");
    } finally {
      setLoading(false);
    }
  }, [fetchWithAuth]);

  useEffect(() => { fetchConfig(); fetchModels(); }, [fetchConfig, fetchModels]);

  const total = Object.values(weights).reduce((s, v) => s + v, 0);
  const isDirty = serverState && (
    Object.keys(weights).some(k => Math.abs(weights[k] - serverState[k]) > 0.005) ||
    sensitivity !== serverState.default_data_sensitivity ||
    routingEnabled !== serverState.routing_enabled ||
    defaultModel !== serverState.default_model
  );

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
      const payload = { ...weights, default_data_sensitivity: sensitivity, routing_enabled: routingEnabled, default_model: defaultModel };
      const res = await fetchWithAuth("/api/firewall/config/", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (res.ok) {
        const data = await res.json();
        const w = {
          routing_risk_weight: data.routing_risk_weight ?? weights.routing_risk_weight,
          routing_cost_weight: data.routing_cost_weight ?? weights.routing_cost_weight,
          routing_latency_weight: data.routing_latency_weight ?? weights.routing_latency_weight,
          routing_priority_weight: data.routing_priority_weight ?? weights.routing_priority_weight,
        };
        setWeights(w);
        setSensitivity(data.default_data_sensitivity ?? sensitivity);
        const enabled = typeof data.routing_enabled === "boolean" ? data.routing_enabled : routingEnabled;
        setRoutingEnabled(enabled);
        const dm = data.default_model ?? defaultModel;
        setDefaultModel(dm);
        setServerState({ ...w, default_data_sensitivity: data.default_data_sensitivity ?? sensitivity, routing_enabled: enabled, default_model: dm });
        setSaveOk(true);
        setTimeout(() => setSaveOk(false), 4000);
      } else {
        const body = await res.json().catch(() => null);
        setError(body ? JSON.stringify(body) : "Failed to save routing configuration.");
      }
    } catch {
      setError("Network error saving routing configuration.");
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div className="bg-slate-100 dark:bg-slate-800/50 border border-slate-200 dark:border-slate-700 rounded-lg p-6 animate-pulse">
        <div className="h-6 bg-slate-300 dark:bg-slate-700 rounded w-48 mb-4" />
        <div className="space-y-3">
          {[1, 2, 3, 4].map(i => <div key={i} className="h-10 bg-slate-300/70 dark:bg-slate-700/50 rounded" />)}
        </div>
      </div>
    );
  }

  return (
    <div className="bg-slate-100 dark:bg-slate-800/50 border border-slate-200 dark:border-slate-700 rounded-lg overflow-hidden">
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
          {saveOk && <span className="text-xs text-emerald-700 dark:text-emerald-300">Saved &amp; synced to gateway</span>}
          {isDirty && (
            <button
              onClick={fetchConfig}
              className="p-1.5 text-slate-500 dark:text-slate-400 hover:text-slate-800 dark:hover:text-white rounded transition-colors"
              title="Discard changes"
            >
              <RotateCcw className="w-4 h-4" />
            </button>
          )}
          <button
            onClick={handleSave}
            disabled={saving || !isDirty}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-medium transition-colors disabled:opacity-40 disabled:cursor-not-allowed bg-purple-600 hover:bg-purple-500 text-white"
          >
            <Save className="w-3.5 h-3.5" />
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
      </div>

      {error && (
        <div className="mx-5 mt-3 p-2 bg-red-500/10 border border-red-500/30 text-red-700 dark:text-red-300 text-xs rounded">
          {error}
        </div>
      )}

      <div className="p-5 space-y-5">
        {/* Presets */}
        <div className="bg-slate-200/50 dark:bg-slate-900/40 border border-slate-300 dark:border-slate-700/50 rounded-lg p-4">
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <Power className={`w-4 h-4 ${routingEnabled ? "text-emerald-700 dark:text-emerald-300" : "text-slate-500 dark:text-slate-500"}`} />
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
              className={`px-3 py-1.5 rounded text-xs font-semibold transition-colors ${routingEnabled ? "bg-emerald-500/20 text-emerald-700 dark:text-emerald-300 ring-1 ring-emerald-500/50" : "bg-slate-300 dark:bg-slate-700/70 text-slate-700 dark:text-slate-300 ring-1 ring-slate-400 dark:ring-slate-600"}`}
            >
              {routingEnabled ? "Enabled" : "Disabled"}
            </button>
          </div>
          <p className="mt-2 text-[10px] text-slate-600 dark:text-slate-500">
            API callers can still override per request using <span className="font-mono">routing_preferences.enable_routing</span>.
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
                  key={p.name}
                  onClick={() => applyPreset(p)}
                  className={`px-3 py-1.5 rounded-full text-xs font-medium transition-all ${
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
                    className={`flex-1 h-1.5 rounded-full appearance-none cursor-pointer ${WEIGHT_ACCENT_STYLES[color]} bg-slate-300 dark:bg-slate-700`}
                  />
                  <input
                    type="number"
                    min="0"
                    max="1"
                    step="0.05"
                    value={weights[key]}
                    onChange={e => handleWeightChange(key, e.target.value)}
                    className="w-16 bg-white dark:bg-slate-900/50 border border-slate-300 dark:border-slate-600 rounded px-2 py-0.5 text-xs text-slate-900 dark:text-white text-center font-mono"
                  />
                </div>
                <p className="text-[10px] text-slate-600 dark:text-slate-500">{description}</p>
              </div>
            ))}
          </div>
        </div>

        {/* Default Sensitivity */}
        <div>
          <label className="text-xs font-medium text-slate-600 dark:text-slate-400 uppercase tracking-wider mb-2 block">
            Default Data Sensitivity
          </label>
          <div className="grid grid-cols-4 gap-2">
            {SENSITIVITY_OPTIONS.map(opt => (
              <button
                key={opt.value}
                onClick={() => setSensitivity(opt.value)}
                className={`px-3 py-2 rounded text-xs font-medium transition-all text-center ${
                  sensitivity === opt.value
                    ? SENSITIVITY_ACTIVE_STYLES[opt.color]
                    : "bg-slate-300/60 dark:bg-slate-700/40 text-slate-700 dark:text-slate-400 hover:bg-slate-300 dark:hover:bg-slate-700/70"
                }`}
              >
                {opt.label}
              </button>
            ))}
          </div>
          <p className="mt-1.5 text-[10px] text-slate-600 dark:text-slate-500">
            Applied when requests don't specify a sensitivity level. Models below this level are excluded from routing.
          </p>
        </div>

        {/* Default Fallback Model */}
        <div>
          <label className="text-xs font-medium text-slate-600 dark:text-slate-400 uppercase tracking-wider mb-2 block">
            Default Fallback Model
          </label>
          <select
            value={defaultModel}
            onChange={e => setDefaultModel(e.target.value)}
            className="w-full bg-white dark:bg-slate-900/50 border border-slate-300 dark:border-slate-600 rounded px-3 py-2 text-sm text-slate-900 dark:text-white font-mono appearance-none cursor-pointer hover:border-slate-400 dark:hover:border-slate-500 transition-colors"
          >
            <option value={defaultModel}>{defaultModel}</option>
            {registeredModels
              .filter(m => m.model_name !== defaultModel)
              .map(m => (
                <option key={m.id || m.model_name} value={m.model_name}>{m.model_name}</option>
              ))
            }
          </select>
          <p className="mt-1.5 text-[10px] text-slate-600 dark:text-slate-500">
            Used when no model is specified in a request, when routing is disabled, or as last-resort fallback if all candidates are excluded.
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
