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
const BEHAVIOR_RELOAD_DELAYS_REASSESS_MS = [0, 1500, 3000, 6000, 12000, 20000, 30000, 45000, 60000];
const BEHAVIOR_TELEMETRY_DEBOUNCE_MS = 150;
const SCORE_SETTLE_TIMEOUT_MS = 90_000;
const SCORE_SETTLE_EPSILON = 0.001;

function behaviorSnapshotEqual(prev, next) {
  if (!prev || !next) return false;
  const prevRecent = Array.isArray(prev.recent_requests) ? prev.recent_requests : [];
  const nextRecent = Array.isArray(next.recent_requests) ? next.recent_requests : [];
  const prevRecentHead = prevRecent[0] || {};
  const nextRecentHead = nextRecent[0] || {};
  return (
    prev.request_count === next.request_count
    && prev.blocked_count === next.blocked_count
    && prev.risk_score === next.risk_score
    && prev.final_score === next.final_score
    && prev.risk_band === next.risk_band
    && prevRecent.length === nextRecent.length
    && prevRecentHead.timestamp === nextRecentHead.timestamp
    && prevRecentHead.action === nextRecentHead.action
    && prevRecentHead.prompt_snippet === nextRecentHead.prompt_snippet
  );
}

function readBehaviorScore(row) {
  if (!row) return null;
  const raw = row.final_score ?? row.risk_score;
  return raw == null ? null : Number(raw);
}

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
  onRiskCalcRestore,
  riskCalcSaving,
  riskCalcSaveError,
  riskCalcReassessSignal = 0,
  refreshSignal = 0,
  liveConnected = false,
}) {
  const module2Api = useMemo(() => createModule2Api(fetchWithAuth), [fetchWithAuth]);
  const keyId = rowSummary?.key_id;
  const [behavior, setBehavior] = useState(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState(null);
  const [riskCalcReassessing, setRiskCalcReassessing] = useState(false);
  const behaviorSeqRef = useRef(0);
  const reloadTimersRef = useRef([]);
  const telemetryDebounceRef = useRef(null);
  const hasLoadedRef = useRef(false);
  const rowSummaryRef = useRef(rowSummary);
  const riskCalculationRef = useRef(riskCalculation);
  const settleBaselineRef = useRef(null);
  const reassessComputedAtRef = useRef(null);
  const reassessBandRef = useRef(null);
  const lastReassessSignalRef = useRef(riskCalcReassessSignal);
  const riskCalcReassessingRef = useRef(false);

  useEffect(() => {
    rowSummaryRef.current = rowSummary;
    riskCalculationRef.current = riskCalculation;
  }, [rowSummary, riskCalculation]);

  useEffect(() => {
    riskCalcReassessingRef.current = riskCalcReassessing;
  }, [riskCalcReassessing]);

  const armRiskCalcReassess = useCallback(() => {
    settleBaselineRef.current = readBehaviorScore(behavior) ?? readBehaviorScore(rowSummaryRef.current);
    reassessComputedAtRef.current = behavior?.computed_at ?? null;
    reassessBandRef.current = behavior?.risk_band ?? rowSummaryRef.current?.risk_band ?? null;
    setRiskCalcReassessing(true);
  }, [behavior]);

  useEffect(() => {
    if (riskCalcSaving) {
      armRiskCalcReassess();
    }
  }, [riskCalcSaving, armRiskCalcReassess]);

  useEffect(() => {
    if (!riskCalcSaving && riskCalcSaveError) {
      setRiskCalcReassessing(false);
    }
  }, [riskCalcSaving, riskCalcSaveError]);

  useEffect(() => {
    if (!riskCalcReassessing) return undefined;
    const timer = setTimeout(() => setRiskCalcReassessing(false), SCORE_SETTLE_TIMEOUT_MS);
    return () => clearTimeout(timer);
  }, [riskCalcReassessing]);

  useEffect(() => {
    if (!riskCalcReassessing || !behavior) return;
    const current = readBehaviorScore(behavior);
    const baseline = settleBaselineRef.current;
    const computedAtChanged = behavior.computed_at != null
      && behavior.computed_at !== reassessComputedAtRef.current;
    const bandChanged = Boolean(
      reassessBandRef.current
      && behavior.risk_band
      && behavior.risk_band !== reassessBandRef.current,
    );
    if (computedAtChanged || bandChanged) {
      setRiskCalcReassessing(false);
      return;
    }
    if (current == null || baseline == null) return;
    if (Math.abs(current - baseline) >= SCORE_SETTLE_EPSILON) {
      setRiskCalcReassessing(false);
    }
  }, [behavior, riskCalcReassessing]);

  const mergeBehavior = useCallback((data) => {
    const summary = rowSummaryRef.current;
    const riskCalc = riskCalculationRef.current;
    if (!data || !summary) return data;
    return {
      ...data,
      key_id: data.key_id || summary.key_id,
      prefix: data.prefix || summary.prefix,
      name: data.name || summary.name,
      owner_email: data.owner_email || summary.owner_email,
      request_count: data.request_count ?? summary.request_count,
      blocked_count: data.blocked_count ?? summary.blocked_count,
      redacted_count: data.redacted_count ?? summary.redacted_count,
      block_rate_pct: data.block_rate_pct ?? summary.block_rate_pct,
      redact_rate_pct: data.redact_rate_pct ?? summary.redact_rate_pct,
      risk_score: data.final_score ?? data.risk_score ?? summary.final_score ?? summary.risk_score,
      final_score: data.final_score ?? data.risk_score ?? summary.final_score ?? summary.risk_score,
      traditional_score: data.traditional_score ?? summary.traditional_score,
      computed_at: data.computed_at ?? summary.computed_at,
      behavior_profile: data.behavior_profile ?? summary.behavior_profile,
      score_breakdown: data.score_breakdown ?? summary.score_breakdown,
      risk_calculation: data.risk_calculation ?? riskCalc,
      llm_reasoning: data.llm_reasoning ?? summary.llm_reasoning,
      llm_verdict: data.llm_verdict ?? summary.llm_verdict,
      llm_recommended_action: data.llm_recommended_action ?? summary.llm_recommended_action,
      risk_band: data.risk_band ?? summary.risk_band,
      velocity_spike: data.velocity_spike ?? summary.velocity_spike,
      anomaly_flags: data.anomaly_flags ?? summary.anomaly_flags,
      llm_observation: data.llm_observation ?? summary.llm_observation,
      is_active: data.is_active ?? summary.is_active,
    };
  }, []);

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
      const merged = mergeBehavior(data);
      setBehavior((prev) => {
        if (silent && prev && behaviorSnapshotEqual(prev, merged)) return prev;
        return merged;
      });
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

  const loadBehaviorRef = useRef(loadBehavior);
  useEffect(() => {
    loadBehaviorRef.current = loadBehavior;
  }, [loadBehavior]);

  const scheduleReload = useCallback((delays = null) => {
    if (!keyId) return;
    const resolvedDelays = delays
      ?? (riskCalcReassessingRef.current
        ? BEHAVIOR_RELOAD_DELAYS_REASSESS_MS
        : (liveConnected ? BEHAVIOR_RELOAD_DELAYS_LIVE_MS : BEHAVIOR_RELOAD_DELAYS_POLLING_MS));
    reloadTimersRef.current.forEach((id) => clearTimeout(id));
    reloadTimersRef.current = resolvedDelays.map((delay) =>
      setTimeout(() => loadBehaviorRef.current({ silent: true }), delay),
    );
  }, [keyId, liveConnected]);

  const scheduleReassessReload = useCallback(() => {
    scheduleReload(BEHAVIOR_RELOAD_DELAYS_REASSESS_MS);
  }, [scheduleReload]);

  useEffect(() => {
    if (!open || riskCalcReassessSignal === lastReassessSignalRef.current) return;
    lastReassessSignalRef.current = riskCalcReassessSignal;
    if (riskCalcReassessSignal > 0) {
      armRiskCalcReassess();
      if (keyId) scheduleReassessReload();
    }
  }, [riskCalcReassessSignal, open, keyId, armRiskCalcReassess, scheduleReassessReload]);

  useEffect(() => {
    if (!open || !keyId) {
      setBehavior(null);
      setLoadError(null);
      setRiskCalcReassessing(false);
      hasLoadedRef.current = false;
      settleBaselineRef.current = null;
      reassessComputedAtRef.current = null;
      reassessBandRef.current = null;
      return;
    }
    hasLoadedRef.current = false;
    loadBehaviorRef.current();
  }, [open, keyId, period]);

  useEffect(() => {
    if (!open || !keyId || refreshSignal === 0 || !hasLoadedRef.current) return;
    if (riskCalcReassessingRef.current) {
      scheduleReassessReload();
      return;
    }
    scheduleReload();
  }, [refreshSignal, open, keyId, scheduleReload, scheduleReassessReload]);

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
    const pollId = setInterval(() => loadBehaviorRef.current({ silent: true }), BEHAVIOR_POLL_MS);
    return () => {
      if (pollId) clearInterval(pollId);
      clearTimeout(telemetryDebounceRef.current);
      reloadTimersRef.current.forEach((id) => clearTimeout(id));
      window.removeEventListener(TELEMETRY_ACTIVITY_EVENT, onTelemetry);
      window.removeEventListener("storage", onStorage);
    };
  }, [open, keyId, scheduleReload]);

  useEffect(() => {
    if (!open) return undefined;
    const onKeyDown = (event) => {
      if (event.key === "Escape") onClose?.();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open, onClose]);

  if (!open) return null;

  const scoreUpdating = Boolean(riskCalcSaving || riskCalcReassessing);

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
                scoreUpdating={scoreUpdating}
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
                onRestoreDefaults={onRiskCalcRestore}
                saving={riskCalcSaving}
                saveError={riskCalcSaveError}
                defaultOpen={false}
              />
            </div>
          )}
        </div>

        {behavior && (
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
                {scoreUpdating && (
                  <span className="mb-1 inline-flex items-center gap-1 rounded-full bg-sky-100 px-2 py-0.5 text-[10px] font-semibold uppercase text-sky-800 dark:bg-sky-900/40 dark:text-sky-200">
                    <Loader2 className="h-3 w-3 animate-spin" />
                    Updating score
                  </span>
                )}
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
