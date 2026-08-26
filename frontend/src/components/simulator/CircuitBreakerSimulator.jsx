import { useState, useEffect, useCallback } from "react";
import { AlertTriangle, RotateCcw, Zap, Activity, ShieldOff, TrendingUp } from "lucide-react";
import { useAuth } from "../../context/AuthContext";
import { startVisibleInterval } from "../../utils/visiblePoll.js";
import { useSimulatorEngine } from "../../hooks/useSimulatorEngine";
import { SimulatorShell } from "./SimulatorShell";

export function CircuitBreakerSimulator() {
  const { fetchWithAuth } = useAuth();
  const engine = useSimulatorEngine();
  const [result, setResult] = useState(null);
  const [cbState, setCbState] = useState(null);
  const [targetModel, setTargetModel] = useState("gpt-4o-mini");
  const [errorCount, setErrorCount] = useState(10);
  const [polling, setPolling] = useState(false);
  const [riskModel, setRiskModel] = useState("gpt-4o");
  const [riskSeverity, setRiskSeverity] = useState(0.85);
  const [riskType, setRiskType] = useState("output_guard");
  const [riskResult, setRiskResult] = useState(null);
  const [riskInjecting, setRiskInjecting] = useState(false);
  const [triggering, setTriggering] = useState(false);

  // Load circuit breaker state
  // Phase 1 Fx-3: route through Django admin proxy (IsAdminOrSuperuser)
  // instead of calling /v1/admin/* directly with a per-org gateway key.
  // Response envelope: { status: "ok" | "error", data: <gateway_json> }.
  const loadState = useCallback(async () => {
    const res = await fetchWithAuth("/api/admin/gateway/circuit-breaker/state/");
    if (!res.ok) return;
    let body = null;
    try {
      body = await res.json();
    } catch {
      return;
    }
    if (body && body.status === "ok") setCbState(body.data);
  }, [fetchWithAuth]);

  useEffect(() => {
    if (engine.connectionStatus !== "disconnected") {
      loadState();
    }
  }, [engine.connectionStatus, loadState]);

  // Auto-poll when circuit is open
  useEffect(() => {
    if (!polling) return;
    return startVisibleInterval(loadState, 3000);
  }, [polling, loadState]);

  const handleTrigger = async () => {
    // Phase 1 Fx-3: proxy trigger through Django admin RBAC instead of
    // engine.executeScenario which uses the per-org gateway key (non-admin).
    // handleTrigger bypasses the hook's executeScenario, so engine.executing
    // never flips — drive the Execute button's in-flight disable off a local
    // `triggering` flag to prevent double-submit / concurrent injections.
    if (triggering) return;
    setTriggering(true);
    let parsed = null;
    let httpOk = false;
    try {
      const httpRes = await fetchWithAuth(
        "/api/admin/gateway/circuit-breaker/trigger/",
        {
          method: "POST",
          body: JSON.stringify({
            model: targetModel,
            error_count: errorCount,
            error_type: "simulated_overload",
          }),
        },
      );
      httpOk = httpRes.ok;
      try {
        parsed = await httpRes.json();
      } catch {
        parsed = null;
      }
    } catch (err) {
      parsed = { status: "error", data: { message: String(err) } };
    }
    // Normalise to the {ok, data} shape the rest of the panel expects.
    setResult({
      ok: httpOk && parsed?.status === "ok",
      data: parsed?.data ?? null,
    });
    setPolling(true);
    await loadState();
    setTriggering(false);
  };

  const handleReset = async () => {
    // Phase 1 Fx-3: proxy reset through Django admin RBAC.
    await fetchWithAuth("/api/admin/gateway/circuit-breaker/reset/", {
      method: "POST",
      body: JSON.stringify({ model: targetModel }),
    });
    setResult(null);
    setPolling(false);
    await loadState();
  };

  const handleRiskInject = async () => {
    setRiskInjecting(true);
    try {
      const res = await fetchWithAuth("/api/models/isolate/", {
        method: "POST",
        body: JSON.stringify({
          model_name: riskModel,
          action: "block",
          reason: `Manual isolation test (${riskType}, severity ${riskSeverity})`,
          cooldown_seconds: 300,
        }),
      });
      let data = null;
      try {
        data = await res.json();
      } catch {
        data = null;
      }
      setRiskResult(
        res.ok
          ? {
              auto_isolated: data?.status === "isolated",
              composite_score: data?.risk_score,
              threshold: data?.threshold,
            }
          : { error: data?.error || data?.detail || "Failed to isolate model" },
      );
    } catch {
      setRiskResult({ error: "Network error" });
    }
    setRiskInjecting(false);
  };

  const stateColor = (state) => {
    if (state === "open") return "text-red-700 dark:text-red-300 bg-red-500/10 border-red-500/30";
    if (state === "half_open" || state === "half-open") return "text-amber-700 dark:text-amber-300 bg-amber-500/10 border-amber-500/30";
    return "text-emerald-700 dark:text-emerald-300 bg-emerald-500/10 border-emerald-500/30";
  };

  return (
    <SimulatorShell
      title="Circuit Breaker Simulator"
      description="Inject errors to trigger circuit breaker protection, observe state transitions, and test recovery"
      connectionStatus={engine.connectionStatus}
      gatewayUrl={engine.gatewayUrl}
      gatewayKey={engine.gatewayKey}
      onKeyChange={engine.setGatewayKey}
      scenarios={[]}
      onExecute={handleTrigger}
      executing={engine.executing || triggering}
      result={result}
      customInput={
        <div className="space-y-3">
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <div>
              <label className="text-[11px] text-slate-600 dark:text-slate-400 font-medium">Target Model</label>
              <input
                type="text"
                value={targetModel}
                onChange={(e) => setTargetModel(e.target.value)}
                className="w-full mt-1 px-2 py-1.5 rounded-md bg-slate-100 dark:bg-slate-900/60 border border-slate-300 dark:border-slate-700 text-xs text-slate-700 dark:text-slate-300"
              />
            </div>
            <div>
              <label className="text-[11px] text-slate-600 dark:text-slate-400 font-medium">Error Count: {errorCount}</label>
              <input
                type="range"
                min="1"
                max="50"
                value={errorCount}
                onChange={(e) => setErrorCount(parseInt(e.target.value))}
                className="w-full mt-2"
              />
            </div>
            <div className="flex items-end gap-2">
              <button
                onClick={handleReset}
                className="flex items-center gap-1 px-3 py-1.5 rounded-md text-xs font-medium bg-slate-200 hover:bg-slate-300 text-slate-700 dark:bg-slate-700 dark:hover:bg-slate-600 dark:text-slate-300 transition-colors"
              >
                <RotateCcw className="w-3 h-3" /> Reset Breaker
              </button>
            </div>
          </div>
        </div>
      }
      extraActions={
        <button
          onClick={loadState}
          className="flex items-center gap-1 px-3 py-1.5 rounded-md text-xs font-medium bg-slate-200 hover:bg-slate-300 text-slate-700 dark:bg-slate-700 dark:hover:bg-slate-600 dark:text-slate-300 transition-colors"
        >
          <Activity className="w-3 h-3" /> Refresh State
        </button>
      }
    >
      {/* Circuit breaker state panel */}
      <div className="px-4 py-3 space-y-3">
        {cbState?.enabled === false && (
          <div className="p-3 rounded-lg bg-slate-100 dark:bg-slate-900/60 border border-slate-300 dark:border-slate-700 text-xs text-slate-600 dark:text-slate-400">
            Circuit breaker is not enabled. Configure it in gateway settings.
          </div>
        )}

        {cbState?.models?.length > 0 && (
          <div className="space-y-2">
            <h4 className="text-xs font-medium text-slate-600 dark:text-slate-400">Model Circuit States</h4>
            {cbState.models.map((m, i) => (
              <div key={i} className={`p-3 rounded-lg border ${stateColor(m.state)}`}>
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-semibold">{m.model}</span>
                    <span className={`px-2 py-0.5 rounded-md text-[10px] font-bold uppercase border ${stateColor(m.state)}`}>
                      {m.state}
                    </span>
                  </div>
                  {m.should_block && (
                    <span className="text-[10px] font-semibold text-red-700 dark:text-red-300 flex items-center gap-1">
                      <AlertTriangle className="w-3 h-3" /> BLOCKING
                    </span>
                  )}
                </div>
                <div className="grid grid-cols-4 gap-3 text-[11px]">
                  <div>
                    <span className="text-slate-600 dark:text-slate-400">Error Rate</span>
                    <div className="font-semibold text-slate-800 dark:text-slate-200">{(m.error_rate * 100).toFixed(1)}%</div>
                  </div>
                  <div>
                    <span className="text-slate-600 dark:text-slate-400">Errors</span>
                    <div className="font-semibold text-slate-800 dark:text-slate-200">{m.error_count}</div>
                  </div>
                  <div>
                    <span className="text-slate-600 dark:text-slate-400">Total Requests</span>
                    <div className="font-semibold text-slate-800 dark:text-slate-200">{m.total_requests}</div>
                  </div>
                  <div>
                    <span className="text-slate-600 dark:text-slate-400">Opened At</span>
                    <div className="font-semibold text-slate-800 dark:text-slate-200 text-[10px]">{m.opened_at || "—"}</div>
                  </div>
                </div>
                {/* Error rate visual bar */}
                <div className="mt-2 h-2 bg-slate-200 dark:bg-slate-900 rounded-full overflow-hidden">
                  <div
                    className={`h-full rounded-full transition-all ${
                      m.error_rate > 0.5 ? "bg-red-500" :
                      m.error_rate > 0.2 ? "bg-amber-500" : "bg-emerald-500"
                    }`}
                    style={{ width: `${Math.min(m.error_rate * 100, 100)}%` }}
                  />
                </div>
              </div>
            ))}
          </div>
        )}

        {cbState?.models?.length === 0 && cbState?.enabled && (
          <div className="p-3 rounded-lg bg-slate-100 dark:bg-slate-900/60 border border-slate-300 dark:border-slate-700 text-xs text-slate-600 dark:text-slate-400 text-center">
            No circuit breaker keys in Redis. Send some requests through the gateway first, or click "Execute" to inject errors.
          </div>
        )}

        {/* Trigger result — the injection response is normalised to {ok, data}.
            A failed trigger (ok:false) must NOT render a green success card:
            read Model/Errors from the operator's own request inputs and take
            the authoritative post-injection state from the freshly-loaded
            cbState (falling back to the response's own state field). */}
        {result && (
          result.ok ? (
            <div className="p-3 rounded-lg bg-amber-500/10 border border-amber-500/30">
              <h4 className="text-xs font-semibold text-amber-700 dark:text-amber-300 mb-1">Error Injection Result</h4>
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 text-[11px]">
                <div>
                  <span className="text-slate-600 dark:text-slate-400">Model</span>
                  <div className="font-medium text-slate-800 dark:text-slate-200 break-all">{targetModel}</div>
                </div>
                <div>
                  <span className="text-slate-600 dark:text-slate-400">Errors Injected</span>
                  <div className="font-medium text-red-700 dark:text-red-300">{errorCount}</div>
                </div>
                <div>
                  <span className="text-slate-600 dark:text-slate-400">New State</span>
                  {(() => {
                    const st = cbState?.models?.find((m) => m.model === targetModel)?.state ?? result.data?.state;
                    return (
                      <div className={`font-bold uppercase ${
                        st === "open" ? "text-red-700 dark:text-red-300" :
                        st === "half_open" || st === "half-open" ? "text-amber-700 dark:text-amber-300" :
                        st ? "text-emerald-700 dark:text-emerald-300" : "text-slate-500 dark:text-slate-400"
                      }`}>
                        {st || "—"}
                      </div>
                    );
                  })()}
                </div>
              </div>
            </div>
          ) : (
            <div className="p-3 rounded-lg bg-red-500/10 border border-red-500/30">
              <h4 className="text-xs font-semibold text-red-700 dark:text-red-300 mb-1">Error Injection Failed</h4>
              <p className="text-[11px] text-slate-600 dark:text-slate-400 break-words">
                {result.data?.message || "The circuit-breaker trigger did not complete. Check the target model and gateway connection."}
              </p>
            </div>
          )
        )}

        {/* Model Risk Injection Section */}
        <div className="mt-4 pt-4 border-t border-slate-200 dark:border-slate-700/60">
          <h4 className="text-xs font-semibold text-slate-700 dark:text-slate-300 flex items-center gap-1.5 mb-3">
            <TrendingUp className="w-3.5 h-3.5 text-red-600 dark:text-red-400" />
            Model Risk Injection (Auto-Isolation Test)
          </h4>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-3">
            <div>
              <label className="text-[11px] text-slate-600 dark:text-slate-400 font-medium">Model</label>
              <input
                type="text"
                value={riskModel}
                onChange={(e) => setRiskModel(e.target.value)}
                className="w-full mt-1 px-2 py-1.5 rounded-md bg-slate-100 dark:bg-slate-900/60 border border-slate-300 dark:border-slate-700 text-xs text-slate-700 dark:text-slate-300"
              />
            </div>
            <div>
              <label className="text-[11px] text-slate-600 dark:text-slate-400 font-medium">Type</label>
              <select
                value={riskType}
                onChange={(e) => setRiskType(e.target.value)}
                className="w-full mt-1 px-2 py-1.5 rounded-md bg-slate-100 dark:bg-slate-900/60 border border-slate-300 dark:border-slate-700 text-xs text-slate-700 dark:text-slate-300"
              >
                <option value="output_guard">Output Guard (0.40)</option>
                <option value="circuit_breaker">Circuit Breaker (0.35)</option>
                <option value="input_scan">Input Scan (0.30)</option>
                <option value="guardrail">Guardrail (0.25)</option>
                <option value="anomaly">Anomaly (0.20)</option>
                <option value="policy">Policy (0.10)</option>
              </select>
            </div>
            <div>
              <label className="text-[11px] text-slate-600 dark:text-slate-400 font-medium">Severity: {riskSeverity.toFixed(2)}</label>
              <input
                type="range"
                min="0.1"
                max="1.0"
                step="0.05"
                value={riskSeverity}
                onChange={(e) => setRiskSeverity(parseFloat(e.target.value))}
                className="w-full mt-2"
              />
            </div>
          </div>
          <button
            onClick={handleRiskInject}
            disabled={riskInjecting || engine.connectionStatus === "disconnected"}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${
              riskInjecting || engine.connectionStatus === "disconnected"
                ? "bg-slate-300 text-slate-500 dark:bg-slate-700 dark:text-slate-400 cursor-not-allowed"
                : "bg-red-600 hover:bg-red-500 text-white"
            }`}
          >
            <ShieldOff className="w-3 h-3" />
            {riskInjecting ? "Injecting..." : "Inject Risk Events"}
          </button>
          {riskResult && !riskResult.error && (
            <div className={`mt-3 p-3 rounded-lg border ${riskResult.auto_isolated ? "bg-red-500/10 border-red-500/30" : "bg-amber-500/10 border-amber-500/30"}`}>
              <h4 className={`text-xs font-semibold mb-1 ${riskResult.auto_isolated ? "text-red-700 dark:text-red-400" : "text-amber-700 dark:text-amber-400"}`}>
                {riskResult.auto_isolated ? "AUTO-ISOLATED" : "Risk Score Updated"}
              </h4>
              <div className="grid grid-cols-2 gap-3 text-[11px]">
                <div><span className="text-slate-600 dark:text-slate-400">Composite Score</span><div className="font-semibold text-slate-800 dark:text-slate-200">{riskResult.composite_score?.toFixed(1)}</div></div>
                <div><span className="text-slate-600 dark:text-slate-400">Threshold</span><div className="font-semibold text-slate-800 dark:text-slate-200">{riskResult.threshold}</div></div>
              </div>
            </div>
          )}
          {riskResult?.error && (
            <div className="mt-3 p-2 rounded-lg bg-red-500/10 border border-red-500/30 text-xs text-red-700 dark:text-red-400">{riskResult.error}</div>
          )}
        </div>
      </div>
    </SimulatorShell>
  );
}
