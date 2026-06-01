import { useState, useEffect, useCallback } from "react";
import {
  Activity, AlertTriangle, MessageSquare, RotateCcw, ShieldOff, Zap,
} from "lucide-react";
import { useAuth } from "../../context/AuthContext";
import { useSimulatorEngine } from "../../hooks/useSimulatorEngine";
import { useSimulatorGatewayModels } from "../../hooks/useSimulatorGatewayModels";
import { chatCompletionBody } from "../../utils/liveGateway";
import { SimulatorShell } from "./SimulatorShell";
import { SimulatorModelSelector } from "./SimulatorModelSelector";

const TABS = [
  { id: "live", label: "Live gateway test", icon: MessageSquare },
  { id: "circuit", label: "Circuit breaker", icon: Zap },
  { id: "isolate", label: "Manual isolate", icon: ShieldOff },
];

export function IsolationOpsSimulator() {
  const { fetchWithAuth } = useAuth();
  const engine = useSimulatorEngine();
  const gatewayModels = useSimulatorGatewayModels();
  const [tab, setTab] = useState("live");

  const [prompt, setPrompt] = useState("What is the capital of France?");
  const [stream, setStream] = useState(false);
  const [liveResult, setLiveResult] = useState(null);
  const [liveLoading, setLiveLoading] = useState(false);

  const [cbState, setCbState] = useState(null);
  const [cbResult, setCbResult] = useState(null);
  const [errorCount, setErrorCount] = useState(10);
  const [polling, setPolling] = useState(false);

  const [isolateReason, setIsolateReason] = useState("Manual isolation test from simulator");
  const [isolateResult, setIsolateResult] = useState(null);
  const [isolating, setIsolating] = useState(false);

  const loadCbState = useCallback(async () => {
    const res = await fetchWithAuth("/api/admin/gateway/circuit-breaker/state/");
    if (!res.ok) return;
    try {
      const body = await res.json();
      if (body?.status === "ok") setCbState(body.data);
    } catch {
      /* ignore */
    }
  }, [fetchWithAuth]);

  useEffect(() => {
    if (engine.connectionStatus !== "disconnected") loadCbState();
  }, [engine.connectionStatus, loadCbState]);

  useEffect(() => {
    if (!polling) return;
    const id = setInterval(loadCbState, 3000);
    return () => clearInterval(id);
  }, [polling, loadCbState]);

  const handleLiveChat = async () => {
    if (!gatewayModels.selectedModel) {
      setLiveResult({ error: "Select a connected model with an API key (Model Connections on 1.5)." });
      return;
    }
    setLiveLoading(true);
    setLiveResult(null);
    try {
      const res = stream
        ? await engine.gatewayFetchStream("/v1/chat/completions", {
            method: "POST",
            body: JSON.stringify(
              chatCompletionBody({
                prompt,
                model: gatewayModels.selectedModel,
                stream: true,
              }),
            ),
          })
        : await engine.gatewayFetch("/v1/chat/completions", {
            method: "POST",
            body: JSON.stringify(
              chatCompletionBody({
                prompt,
                model: gatewayModels.selectedModel,
                stream: false,
              }),
            ),
          });

      if (!res.ok) {
        const msg = res.data?.error?.message || res.data?.message || res.data?.detail || `HTTP ${res.status}`;
        setLiveResult({
          error: msg,
          code: res.data?.error?.code || res.data?.code,
          status: res.status,
          model: gatewayModels.selectedModel,
        });
      } else if (res.isStream && res.sse) {
        setLiveResult({
          ok: true,
          stream: true,
          content: res.sse.aggregatedContent,
          events: res.sse.events?.length ?? 0,
          model: gatewayModels.selectedModel,
        });
      } else {
        const content = res.data?.choices?.[0]?.message?.content ?? JSON.stringify(res.data);
        setLiveResult({ ok: true, content, model: gatewayModels.selectedModel, status: res.status });
      }
    } catch (err) {
      setLiveResult({ error: String(err) });
    } finally {
      setLiveLoading(false);
    }
  };

  const handleCbTrigger = async () => {
    const model = gatewayModels.selectedModel || "gpt-4o-mini";
    let parsed = null;
    let httpOk = false;
    try {
      const httpRes = await fetchWithAuth("/api/admin/gateway/circuit-breaker/trigger/", {
        method: "POST",
        body: JSON.stringify({
          model,
          error_count: errorCount,
          error_type: "simulated_overload",
        }),
      });
      httpOk = httpRes.ok;
      parsed = await httpRes.json().catch(() => null);
    } catch (err) {
      parsed = { status: "error", data: { message: String(err) } };
    }
    setCbResult({ ok: httpOk && parsed?.status === "ok", data: parsed?.data ?? null, model });
    setPolling(true);
    await loadCbState();
  };

  const handleCbReset = async () => {
    const model = gatewayModels.selectedModel || "gpt-4o-mini";
    await fetchWithAuth("/api/admin/gateway/circuit-breaker/reset/", {
      method: "POST",
      body: JSON.stringify({ model }),
    });
    setCbResult(null);
    setPolling(false);
    await loadCbState();
  };

  const handleIsolate = async () => {
    if (!gatewayModels.selectedModel) {
      setIsolateResult({ error: "Select a model first." });
      return;
    }
    setIsolating(true);
    setIsolateResult(null);
    try {
      const res = await fetchWithAuth("/api/models/isolate/", {
        method: "POST",
        body: JSON.stringify({
          model_name: gatewayModels.selectedModel,
          action: "block",
          reason: isolateReason,
          cooldown_seconds: 300,
        }),
      });
      const data = await res.json().catch(() => ({}));
      setIsolateResult(
        res.ok
          ? { ok: true, ...data }
          : { error: data.error || data.detail || "Isolation failed" },
      );
    } catch {
      setIsolateResult({ error: "Network error" });
    } finally {
      setIsolating(false);
    }
  };

  const stateColor = (state) => {
    if (state === "open") return "text-red-700 dark:text-red-300 bg-red-500/10 border-red-500/30";
    if (state === "half_open" || state === "half-open") return "text-amber-700 dark:text-amber-300 bg-amber-500/10 border-amber-500/30";
    return "text-emerald-700 dark:text-emerald-300 bg-emerald-500/10 border-emerald-500/30";
  };

  return (
    <SimulatorShell
      title="Isolation Operations Simulator"
      description="Live gateway chat, circuit-breaker injection, and manual model isolation — one workspace for Module 1.6"
      connectionStatus={engine.connectionStatus}
      gatewayUrl={engine.gatewayUrl}
      gatewayKey={engine.gatewayKey}
      onKeyChange={engine.setGatewayKey}
      scenarios={[]}
      onExecute={tab === "live" ? handleLiveChat : tab === "circuit" ? handleCbTrigger : handleIsolate}
      executing={liveLoading || isolating || engine.executing}
      result={tab === "live" ? liveResult : tab === "circuit" ? cbResult : isolateResult}
      customInput={
        <div className="space-y-4">
          <div className="flex flex-wrap gap-2" role="tablist" aria-label="Isolation simulator modes">
            {TABS.map(({ id, label, icon: Icon }) => (
              <button
                key={id}
                type="button"
                role="tab"
                aria-selected={tab === id}
                onClick={() => setTab(id)}
                className={`flex min-h-[44px] items-center gap-1.5 rounded-xl px-3 py-2 text-xs font-medium transition-colors ${
                  tab === id
                    ? "bg-teal-600 text-white"
                    : "bg-slate-100 text-slate-700 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-300 dark:hover:bg-slate-700"
                }`}
              >
                <Icon className="h-3.5 w-3.5" aria-hidden />
                {label}
              </button>
            ))}
          </div>

          <SimulatorModelSelector
            eligibleModels={gatewayModels.eligibleModels}
            selectedModel={gatewayModels.selectedModel}
            onSelectModel={gatewayModels.setSelectedModel}
            loading={gatewayModels.loading}
            loadError={gatewayModels.loadError}
            firewallDefault={gatewayModels.firewallDefault}
            allowlistBlocksSimulator={gatewayModels.allowlistBlocksSimulator}
            allowedModels={gatewayModels.allowedModels}
            onSyncAllowlist={gatewayModels.syncSelectedToAllowlist}
            allowlistSyncing={gatewayModels.allowlistSyncing}
            allowlistSyncError={gatewayModels.allowlistSyncError}
            showModelPicker={gatewayModels.showModelPicker}
            isSingleModel={gatewayModels.isSingleModel}
          />

          {tab === "live" && (
            <div className="space-y-3">
              <label className="block text-[11px] font-medium text-slate-600 dark:text-slate-400">
                Prompt
                <textarea
                  value={prompt}
                  onChange={(e) => setPrompt(e.target.value)}
                  rows={2}
                  className="mt-1 w-full min-h-[44px] rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm text-slate-800 dark:border-slate-700 dark:bg-slate-900/60 dark:text-slate-200"
                />
              </label>
              <label className="flex min-h-[44px] cursor-pointer items-center gap-2 text-xs text-slate-700 dark:text-slate-300">
                <input
                  type="checkbox"
                  checked={stream}
                  onChange={(e) => setStream(e.target.checked)}
                  className="h-4 w-4 rounded border-slate-400"
                />
                Stream response (SSE)
              </label>
            </div>
          )}

          {tab === "circuit" && (
            <div className="flex flex-wrap items-end gap-3">
              <div className="flex-1 min-w-[140px]">
                <label className="text-[11px] font-medium text-slate-600 dark:text-slate-400">
                  Error count: {errorCount}
                </label>
                <input
                  type="range"
                  min="1"
                  max="50"
                  value={errorCount}
                  onChange={(e) => setErrorCount(parseInt(e.target.value, 10))}
                  className="mt-2 w-full"
                />
              </div>
              <button
                type="button"
                onClick={handleCbReset}
                className="flex min-h-[44px] items-center gap-1 rounded-xl bg-slate-200 px-3 py-2 text-xs font-medium text-slate-800 dark:bg-slate-700 dark:text-slate-200"
              >
                <RotateCcw className="h-3.5 w-3.5" /> Reset breaker
              </button>
              <button
                type="button"
                onClick={loadCbState}
                className="flex min-h-[44px] items-center gap-1 rounded-xl bg-slate-200 px-3 py-2 text-xs font-medium text-slate-800 dark:bg-slate-700 dark:text-slate-200"
              >
                <Activity className="h-3.5 w-3.5" /> Refresh
              </button>
            </div>
          )}

          {tab === "isolate" && (
            <label className="block text-[11px] font-medium text-slate-600 dark:text-slate-400">
              Isolation reason
              <input
                type="text"
                value={isolateReason}
                onChange={(e) => setIsolateReason(e.target.value)}
                className="mt-1 w-full min-h-[44px] rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-900/60 dark:text-slate-200"
              />
            </label>
          )}
        </div>
      }
      extraActions={null}
    >
      <div className="px-4 py-3 space-y-3" aria-live="polite">
        {tab === "live" && liveResult?.error && (
          <div className="rounded-xl border border-red-500/30 bg-red-500/10 p-3 text-sm text-red-800 dark:text-red-200">
            <p className="font-medium">{liveResult.error}</p>
            {liveResult.code && (
              <p className="mt-1 text-xs opacity-90">Code: {liveResult.code} · HTTP {liveResult.status}</p>
            )}
            {liveResult.code === "kill_switch_active" && (
              <p className="mt-2 text-xs">Deactivate the kill-switch for this model or repair Redis keys below.</p>
            )}
          </div>
        )}
        {tab === "live" && liveResult?.ok && (
          <div className="rounded-xl border border-emerald-500/30 bg-emerald-500/10 p-3 text-sm text-emerald-900 dark:text-emerald-100">
            <p className="text-xs font-semibold uppercase tracking-wide opacity-80">Model: {liveResult.model}</p>
            <p className="mt-2 whitespace-pre-wrap">{liveResult.content || "(empty)"}</p>
            {liveResult.stream && (
              <p className="mt-1 text-xs opacity-75">{liveResult.events} SSE events</p>
            )}
          </div>
        )}

        {tab === "circuit" && cbState?.models?.length > 0 && (
          <div className="space-y-2">
            <h4 className="text-xs font-medium text-slate-600 dark:text-slate-400">Circuit states</h4>
            {cbState.models.map((m, i) => (
              <div key={i} className={`rounded-lg border p-3 ${stateColor(m.state)}`}>
                <div className="flex items-center justify-between">
                  <span className="font-mono text-sm font-semibold">{m.model}</span>
                  <span className="text-[10px] font-bold uppercase">{m.state}</span>
                </div>
                {m.should_block && (
                  <p className="mt-1 flex items-center gap-1 text-[10px] font-semibold text-red-700 dark:text-red-300">
                    <AlertTriangle className="h-3 w-3" /> Blocking traffic
                  </p>
                )}
              </div>
            ))}
          </div>
        )}

        {tab === "isolate" && isolateResult?.ok && (
          <div className="rounded-xl border border-red-500/30 bg-red-500/10 p-3 text-xs text-red-800 dark:text-red-200">
            Model <strong>{isolateResult.model_name || gatewayModels.selectedModel}</strong> isolated (
            {isolateResult.status}).
          </div>
        )}
        {tab === "isolate" && isolateResult?.error && (
          <div className="rounded-xl border border-red-500/30 bg-red-500/10 p-3 text-xs text-red-700 dark:text-red-300">
            {isolateResult.error}
          </div>
        )}
      </div>
    </SimulatorShell>
  );
}
