import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Loader2, X } from "lucide-react";
import { createModule2Api } from "../../api/module2";
import { TELEMETRY_ACTIVITY_EVENT, TELEMETRY_STORAGE_KEY } from "../../utils/telemetryEvents";
import { ApiKeyRiskProfile } from "./ApiKeyRiskProfile";
import { RiskCalculationSettingsPanel } from "./RiskCalculationSettingsPanel";
import { RiskScoreCalculationGuide } from "./RiskScoreCalculationGuide";
import { RiskBandBadge } from "./RiskBandBadge";

const BEHAVIOR_POLL_MS = 10_000;
const BEHAVIOR_RELOAD_DELAYS_POLLING_MS = [0, 2000, 4000];
const BEHAVIOR_RELOAD_DELAYS_LIVE_MS = [0, 2000, 4000];
const BEHAVIOR_TELEMETRY_DEBOUNCE_MS = 150;

export function ApiKeyProfileSidebar({
  open,
  rowSummary,
  period,
  fetchWithAuth,
  onClose,
  onActionComplete,
  simulatorKeyPrefix = "",
  riskCalculation = null,
  riskCalcDraft,
  riskCalcGuardrails,
  canEditRiskCalc,
  onRiskCalcChange,
  onRiskCalcSave,
  riskCalcSaving,
  riskCalcSaveError,
  refreshSignal = 0,
  liveConnected = false,
}) {
  const module2Api = useMemo(() => createModule2Api(fetchWithAuth), [fetchWithAuth]);
  const keyId = rowSummary?.key_id;
  const [behavior, setBehavior] = useState(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState(null);
  const behaviorSeqRef = useRef(0);
  const reloadTimersRef = useRef([]);
  const telemetryDebounceRef = useRef(null);
  const hasLoadedRef = useRef(false);

  const mergeBehavior = useCallback((data) => {
    if (!data || !rowSummary) return data;
    return {
      ...data,
      key_id: data.key_id || rowSummary.key_id,
      prefix: data.prefix || rowSummary.prefix,
      name: data.name || rowSummary.name,
      owner_email: data.owner_email || rowSummary.owner_email,
      request_count: rowSummary.request_count ?? data.request_count,
      blocked_count: rowSummary.blocked_count ?? data.blocked_count,
      redacted_count: rowSummary.redacted_count ?? data.redacted_count,
      block_rate_pct: rowSummary.block_rate_pct ?? data.block_rate_pct,
      redact_rate_pct: rowSummary.redact_rate_pct ?? data.redact_rate_pct,
      risk_score: rowSummary.final_score ?? rowSummary.risk_score ?? data.risk_score,
      final_score: rowSummary.final_score ?? rowSummary.risk_score ?? data.final_score,
      traditional_score: rowSummary.traditional_score ?? data.traditional_score,
      behavior_profile: rowSummary.behavior_profile ?? data.behavior_profile,
      score_breakdown: rowSummary.score_breakdown ?? data.score_breakdown,
      risk_calculation: data.risk_calculation ?? riskCalculation,
      llm_reasoning: rowSummary.llm_reasoning ?? data.llm_reasoning,
      llm_verdict: rowSummary.llm_verdict ?? data.llm_verdict,
      risk_band: rowSummary.risk_band ?? data.risk_band,
      velocity_spike: rowSummary.velocity_spike ?? data.velocity_spike,
      anomaly_flags: rowSummary.anomaly_flags ?? data.anomaly_flags,
      llm_observation: rowSummary.llm_observation ?? data.llm_observation,
      is_active: rowSummary.is_active ?? data.is_active,
    };
  }, [rowSummary, riskCalculation]);

  const loadBehavior = useCallback(async ({ silent = false } = {}) => {
    if (!keyId) return;
    const seq = ++behaviorSeqRef.current;
    if (!silent) {
      setLoading(true);
      setLoadError(null);
    }
    try {
      const data = await module2Api.getUebaBehavior(keyId, period, { useCache: false });
      if (seq !== behaviorSeqRef.current) return;
      hasLoadedRef.current = true;
      setBehavior(mergeBehavior(data));
    } catch (err) {
      if (seq !== behaviorSeqRef.current) return;
      if (!silent) {
        setBehavior(null);
        setLoadError(err.message || "Failed to load key profile.");
      }
    } finally {
      if (seq !== behaviorSeqRef.current) return;
      if (!silent) setLoading(false);
    }
  }, [keyId, mergeBehavior, module2Api, period]);

  const scheduleReload = useCallback(() => {
    if (!keyId) return;
    const delays = liveConnected ? BEHAVIOR_RELOAD_DELAYS_LIVE_MS : BEHAVIOR_RELOAD_DELAYS_POLLING_MS;
    reloadTimersRef.current.forEach((id) => clearTimeout(id));
    reloadTimersRef.current = delays.map((delay) =>
      setTimeout(() => loadBehavior({ silent: true }), delay),
    );
  }, [keyId, liveConnected, loadBehavior]);

  useEffect(() => {
    if (!open || !keyId) {
      setBehavior(null);
      setLoadError(null);
      hasLoadedRef.current = false;
      return;
    }
    hasLoadedRef.current = false;
    loadBehavior();
  }, [open, keyId, period, loadBehavior]);

  useEffect(() => {
    if (!open || !keyId || refreshSignal === 0 || loading || !hasLoadedRef.current) return;
    scheduleReload();
  }, [refreshSignal, open, keyId, loading, scheduleReload]);

  useEffect(() => {
    if (!open || !keyId) return undefined;
    const onTelemetry = () => {
      clearTimeout(telemetryDebounceRef.current);
      telemetryDebounceRef.current = setTimeout(scheduleReload, BEHAVIOR_TELEMETRY_DEBOUNCE_MS);
    };
    const onStorage = (event) => {
      if (event.key === TELEMETRY_STORAGE_KEY) onTelemetry();
    };
    window.addEventListener(TELEMETRY_ACTIVITY_EVENT, onTelemetry);
    window.addEventListener("storage", onStorage);
    const pollId = setInterval(() => loadBehavior({ silent: true }), BEHAVIOR_POLL_MS);
    return () => {
      if (pollId) clearInterval(pollId);
      clearTimeout(telemetryDebounceRef.current);
      reloadTimersRef.current.forEach((id) => clearTimeout(id));
      window.removeEventListener(TELEMETRY_ACTIVITY_EVENT, onTelemetry);
      window.removeEventListener("storage", onStorage);
    };
  }, [open, keyId, liveConnected, loadBehavior, scheduleReload]);

  useEffect(() => {
    if (!open) return undefined;
    const onKeyDown = (event) => {
      if (event.key === "Escape") onClose?.();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <>
      <button
        type="button"
        aria-label="Close profile"
        className="fixed inset-0 z-[85] bg-black/40"
        onClick={onClose}
      />
      <aside
        className="fixed inset-y-0 right-0 z-[90] flex w-full max-w-md flex-col border-l border-slate-200 bg-white shadow-2xl dark:border-slate-700 dark:bg-slate-900"
        role="dialog"
        aria-modal="true"
        aria-label={`API key profile ${rowSummary?.prefix || ""}`}
      >
        <header className="flex shrink-0 items-start justify-between gap-3 border-b border-slate-200 px-4 py-3 dark:border-slate-700">
          <div className="min-w-0">
            <p className="text-[10px] font-semibold uppercase tracking-wide text-teal-600 dark:text-teal-400">
              API key profile
            </p>
            <h2 className="truncate font-mono text-base font-semibold text-slate-900 dark:text-white">
              {rowSummary?.prefix || "—"}
            </h2>
            <p className="truncate text-xs text-slate-500 dark:text-slate-400">
              {rowSummary?.name || "—"}
              {rowSummary?.owner_email ? ` · ${rowSummary.owner_email}` : ""}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-2 text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800"
            aria-label="Close"
          >
            <X className="h-5 w-5" />
          </button>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
          {loading && !behavior ? (
            <div className="flex justify-center py-16">
              <Loader2 className="h-7 w-7 animate-spin text-teal-500" />
            </div>
          ) : loadError && !behavior ? (
            <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-4 text-xs text-red-700 dark:border-red-900 dark:bg-red-950/40 dark:text-red-300">
              <p>{loadError}</p>
              <button type="button" onClick={() => loadBehavior()} className="mt-2 font-semibold underline">
                Retry
              </button>
            </div>
          ) : (
            <div className="space-y-4">
              <ApiKeyRiskProfile
                behavior={behavior}
                fetchWithAuth={fetchWithAuth}
                onActionComplete={onActionComplete}
                showActions
                simulatorKeyPrefix={simulatorKeyPrefix}
                riskCalculation={behavior?.risk_calculation || riskCalculation}
                variant="sidebar"
                showScoreGuide={false}
              />
              <RiskScoreCalculationGuide
                behavior={behavior}
                riskCalculation={behavior?.risk_calculation || riskCalculation}
                collapsible
                defaultOpen={false}
              />
              <RiskCalculationSettingsPanel
                draft={riskCalcDraft}
                guardrails={riskCalcGuardrails}
                canEdit={canEditRiskCalc}
                onChange={onRiskCalcChange}
                onSave={onRiskCalcSave}
                saving={riskCalcSaving}
                saveError={riskCalcSaveError}
                defaultOpen={false}
              />
            </div>
          )}
        </div>

        {behavior && !loading && (
          <footer className="shrink-0 border-t border-slate-200 bg-slate-50 px-4 py-3 dark:border-slate-700 dark:bg-slate-900/80">
            <div className="flex items-center justify-between gap-3">
              <div>
                <p className="text-[10px] font-semibold uppercase tracking-wide text-slate-500">
                  Behavioral risk score
                </p>
                <p className="text-xs text-slate-500 dark:text-slate-400">
                  {behavior.request_count ?? 0} requests · block {behavior.block_rate_pct ?? 0}%
                </p>
              </div>
              <div className="text-right">
                <RiskBandBadge
                  type="behavioral"
                  band={behavior.risk_band || "low"}
                  score={behavior.final_score ?? behavior.risk_score}
                />
                <p className="mt-1 font-mono text-lg font-bold text-slate-900 dark:text-white">
                  {Number(behavior.final_score ?? behavior.risk_score ?? 0).toFixed(3)}
                </p>
              </div>
            </div>
          </footer>
        )}
      </aside>
    </>
  );
}
