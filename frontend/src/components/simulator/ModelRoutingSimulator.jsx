import { useState, useMemo } from "react";
import { GitBranch, Sliders } from "lucide-react";
import { useSimulatorEngine } from "../../hooks/useSimulatorEngine";
import { chatCompletionBody, normalizeRoutingResult } from "../../utils/liveGateway";
import { SimulatorShell } from "./SimulatorShell";

const SCENARIOS = [
  {
    id: "balanced",
    label: "Balanced Routing",
    badge: "safe",
    preferences: { cost_weight: 0.25, latency_weight: 0.25, quality_weight: 0.25, risk_weight: 0.25 },
    disabled_models: [],
    compliance: [],
  },
  {
    id: "cost-optimized",
    label: "Cost Optimized",
    badge: "safe",
    preferences: { cost_weight: 0.6, latency_weight: 0.1, quality_weight: 0.2, risk_weight: 0.1 },
    disabled_models: [],
    compliance: [],
  },
  {
    id: "quality-first",
    label: "Quality First",
    badge: "safe",
    preferences: { cost_weight: 0.05, latency_weight: 0.1, quality_weight: 0.7, risk_weight: 0.15 },
    disabled_models: [],
    compliance: [],
  },
  {
    id: "hipaa",
    label: "HIPAA Compliance",
    badge: "safe",
    preferences: { cost_weight: 0.1, latency_weight: 0.1, quality_weight: 0.3, risk_weight: 0.5 },
    disabled_models: [],
    compliance: ["HIPAA"],
  },
  {
    id: "failover",
    label: "Model Disabled",
    badge: "attack",
    preferences: { cost_weight: 0.25, latency_weight: 0.25, quality_weight: 0.25, risk_weight: 0.25 },
    disabled_models: ["gpt-4o-mini"],
    compliance: [],
  },
];

const WEIGHT_LABELS = { cost_weight: "Cost", latency_weight: "Latency", quality_weight: "Quality", risk_weight: "Risk" };
const WEIGHT_STYLES = {
  cost_weight: "text-emerald-700 dark:text-emerald-300",
  latency_weight: "text-blue-700 dark:text-blue-300",
  quality_weight: "text-purple-700 dark:text-purple-300",
  risk_weight: "text-amber-700 dark:text-amber-300",
};

export function ModelRoutingSimulator() {
  const engine = useSimulatorEngine();
  const [selected, setSelected] = useState(null);
  const [result, setResult] = useState(null);
  const [weights, setWeights] = useState({ cost_weight: 0.25, latency_weight: 0.25, quality_weight: 0.25, risk_weight: 0.25 });

  const activeWeights = selected?.preferences || weights;

  const handleExecute = async () => {
    const prefs = selected?.preferences || weights;
    const res = await engine.gatewayFetch("/v1/chat/completions", {
      method: "POST",
      body: JSON.stringify(
        chatCompletionBody({
          prompt: "Evaluate this prompt for model routing",
          model: "auto",
          runInference: false,
          routingPreferences: {
            cost_weight: prefs.cost_weight,
            latency_weight: prefs.latency_weight,
            priority_weight: prefs.quality_weight,
            risk_weight: prefs.risk_weight,
            compliance_requirements: selected?.compliance || [],
          },
        }),
      ),
    });
    setResult(
      res.ok
        ? normalizeRoutingResult(res.data)
        : { error: res.data?.message || res.data?.error || "Request failed" },
    );
  };

  // Bar chart rendering
  const maxScore = useMemo(() => {
    if (!result?.scored_models) return 1;
    return Math.max(...result.scored_models.map(m => m.score), 0.01);
  }, [result]);

  return (
    <SimulatorShell
      title="Model Routing Simulator"
      description="Exercise live /v1/chat/completions routing (same path as production) with priority weights and compliance filters"
      connectionStatus={engine.connectionStatus}
      gatewayUrl={engine.gatewayUrl}
      gatewayKey={engine.gatewayKey}
      onKeyChange={engine.setGatewayKey}
      scenarios={SCENARIOS}
      selectedScenario={selected}
      onSelectScenario={setSelected}
      onExecute={handleExecute}
      executing={engine.executing}
      result={result}
      customInput={
        <div className="space-y-3">
          <label className="text-[11px] text-slate-600 dark:text-slate-400 font-medium flex items-center gap-1">
            <Sliders className="w-3 h-3" /> Priority Weights
          </label>
          <div className="grid grid-cols-4 gap-3">
            {Object.entries(WEIGHT_LABELS).map(([key, label]) => {
              return (
                <div key={key}>
                  <div className="flex items-center justify-between mb-1">
                    <span className={`text-[10px] font-medium ${WEIGHT_STYLES[key]}`}>{label}</span>
                    <span className="text-[10px] text-slate-600 dark:text-slate-400">{(activeWeights[key] * 100).toFixed(0)}%</span>
                  </div>
                  <input
                    type="range"
                    min="0"
                    max="1"
                    step="0.05"
                    value={activeWeights[key]}
                    onChange={(e) => !selected && setWeights(w => ({ ...w, [key]: parseFloat(e.target.value) }))}
                    disabled={!!selected}
                    className="w-full h-1.5"
                  />
                </div>
              );
            })}
          </div>
        </div>
      }
    >
      {result && !result.error && (
        <div className="px-4 py-3 space-y-4">
          {/* Chosen model + fallback chain */}
          <div className="flex items-center gap-2 flex-wrap">
            {result.chosen_model && (
              <div className="px-3 py-1.5 rounded-lg bg-indigo-500/20 border border-indigo-500/30 text-sm font-semibold text-indigo-700 dark:text-indigo-300">
                ✦ {result.chosen_model}
              </div>
            )}
            {result.fallback_chain?.length > 0 && (
              <>
                <span className="text-slate-600 dark:text-slate-400 text-xs">→ fallback:</span>
                {result.fallback_chain.map((m, i) => (
                  <span key={i} className="px-2 py-1 rounded-md bg-slate-100 dark:bg-slate-900/60 border border-slate-300 dark:border-slate-700 text-[11px] text-slate-700 dark:text-slate-300">
                    {m}
                  </span>
                ))}
              </>
            )}
          </div>

          {result.scored_models?.length === 0 && result.routing_reason && (
            <p className="text-xs text-slate-500 dark:text-slate-400">
              Full per-model score breakdown is server-side only; this panel shows the live routed model from the completion response.
            </p>
          )}

          {/* Scored models (when provided by API) */}
          <div className="space-y-2">
            {result.scored_models?.map((model, i) => (
              <div key={i} className={`p-3 rounded-lg border ${
                model.model === result.chosen_model
                  ? "bg-indigo-500/10 border-indigo-500/30"
                  : model.status === "disabled" || model.status === "excluded"
                  ? "bg-slate-100 dark:bg-slate-900/60 border-slate-300 dark:border-slate-700 opacity-70"
                  : "bg-slate-100 dark:bg-slate-900/40 border-slate-300 dark:border-slate-700"
              }`}>
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-slate-800 dark:text-slate-200">{model.model}</span>
                    {model.status !== "available" && (
                      <span className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-red-500/20 text-red-700 dark:text-red-300 border border-red-500/30">
                        {model.status}: {model.excluded_reason}
                      </span>
                    )}
                  </div>
                  <span className="text-sm font-bold text-slate-700 dark:text-slate-300">{(model.score * 100).toFixed(1)}%</span>
                </div>
                {/* Score bar */}
                <div className="h-2 bg-slate-200 dark:bg-slate-900 rounded-full overflow-hidden mb-2">
                  <div
                    className="h-full bg-indigo-500 rounded-full transition-all"
                    style={{ width: `${(model.score / maxScore) * 100}%` }}
                  />
                </div>
                {/* Per-dimension breakdown */}
                <div className="grid grid-cols-4 gap-2 text-[10px]">
                  <div>
                    <span className="text-emerald-700 dark:text-emerald-300">Cost</span>
                    <span className="text-slate-600 dark:text-slate-400 ml-1">{(model.cost_score * 100).toFixed(0)}%</span>
                  </div>
                  <div>
                    <span className="text-blue-700 dark:text-blue-300">Latency</span>
                    <span className="text-slate-600 dark:text-slate-400 ml-1">{(model.latency_score * 100).toFixed(0)}%</span>
                  </div>
                  <div>
                    <span className="text-purple-700 dark:text-purple-300">Quality</span>
                    <span className="text-slate-600 dark:text-slate-400 ml-1">{(model.quality_score * 100).toFixed(0)}%</span>
                  </div>
                  <div>
                    <span className="text-amber-700 dark:text-amber-300">Risk</span>
                    <span className="text-slate-600 dark:text-slate-400 ml-1">{(model.risk_score * 100).toFixed(0)}%</span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </SimulatorShell>
  );
}
