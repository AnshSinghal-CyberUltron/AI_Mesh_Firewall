import { useMemo, useState } from "react";
import { ChevronDown, ChevronRight, Settings2 } from "lucide-react";
import {
  buildRiskCalcFormulaLines,
  applyDefaultRiskCalcSettings,
  traditionalWeightSum,
} from "./uebaRiskCalcDefaults";

function NumberInput({ label, value, step = "0.01", min = "0", max = "1", onChange, disabled = false }) {
  return (
    <label className="text-xs text-slate-600 dark:text-slate-300">
      <span className="mb-1 block font-medium">{label}</span>
      <input
        type="number"
        className="w-full rounded-md border border-slate-200 bg-white px-2 py-1.5 text-sm dark:border-slate-600 dark:bg-slate-800"
        value={value ?? ""}
        step={step}
        min={min}
        max={max}
        disabled={disabled}
        onChange={(e) => onChange(Number(e.target.value))}
      />
    </label>
  );
}

export function RiskCalculationSettingsPanel({
  draft,
  guardrails,
  canEdit,
  onChange,
  onSave,
  onRestoreDefaults,
  saving,
  saveError,
  defaultOpen = false,
}) {
  const [open, setOpen] = useState(defaultOpen);
  const [restoring, setRestoring] = useState(false);
  const liveFormula = useMemo(() => buildRiskCalcFormulaLines(draft), [draft]);
  const liveWeightSum = useMemo(() => traditionalWeightSum(draft), [draft]);
  if (!draft) return null;

  const handleRestoreDefaults = async () => {
    if (!canEdit || restoring) return;
    if (onRestoreDefaults) {
      setRestoring(true);
      try {
        await onRestoreDefaults();
      } finally {
        setRestoring(false);
      }
      return;
    }
    const defaults = applyDefaultRiskCalcSettings(draft);
    onChange((next) => {
      Object.keys(next).forEach((key) => delete next[key]);
      Object.assign(next, defaults);
      next.weights = { ...defaults.weights };
    });
  };

  return (
    <div className="rounded-lg border border-slate-200 dark:border-slate-700">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between gap-2 px-3 py-2.5 text-left hover:bg-slate-50 dark:hover:bg-slate-800/50"
        aria-expanded={open}
      >
        <span className="inline-flex items-center gap-2 text-xs font-semibold uppercase text-slate-600 dark:text-slate-300">
          <Settings2 className="h-3.5 w-3.5 text-teal-600 dark:text-teal-400" />
          Configure score calculation
        </span>
        {open ? <ChevronDown className="h-4 w-4 text-slate-400" /> : <ChevronRight className="h-4 w-4 text-slate-400" />}
      </button>

      {open && (
        <div className="border-t border-slate-200 px-3 py-3 dark:border-slate-700">
          <p className="mb-3 text-[11px] text-slate-500 dark:text-slate-400">
            Org-wide observation weights applied to every API key unless you change them here.
            Defaults remain in effect until saved.
          </p>
          {!canEdit && (
            <p className="mb-3 text-xs text-amber-700 dark:text-amber-300">
              Read-only: only platform admins can change calculation settings.
            </p>
          )}
          {saveError && <p className="mb-3 text-xs text-red-600">{saveError}</p>}
          <div className="grid gap-3 sm:grid-cols-2">
            <NumberInput
              label="Prompt target for behavior profile"
              value={draft.behavior_profile_prompt_target}
              step="1"
              min="10"
              max="500"
              disabled={!canEdit}
              onChange={(v) => onChange((next) => { next.behavior_profile_prompt_target = v; })}
            />
            <NumberInput
              label="LLM triage threshold"
              value={draft.llm_triage_min_traditional_score}
              disabled={!canEdit}
              onChange={(v) => onChange((next) => { next.llm_triage_min_traditional_score = v; })}
            />
            <NumberInput
              label="Baseline deviation weight"
              value={draft.weights?.baseline_deviation}
              disabled={!canEdit}
              onChange={(v) => onChange((next) => {
                next.weights = next.weights || {};
                next.weights.baseline_deviation = v;
              })}
            />
            <NumberInput
              label="Weight: block rate"
              value={draft.weights?.block_rate}
              disabled={!canEdit}
              onChange={(v) => onChange((next) => {
                next.weights = next.weights || {};
                next.weights.block_rate = v;
              })}
            />
            <NumberInput
              label="Weight: threat severity"
              value={draft.weights?.threat_severity}
              disabled={!canEdit}
              onChange={(v) => onChange((next) => {
                next.weights = next.weights || {};
                next.weights.threat_severity = v;
              })}
            />
            <NumberInput
              label="Weight: velocity"
              value={draft.weights?.velocity}
              disabled={!canEdit}
              onChange={(v) => onChange((next) => {
                next.weights = next.weights || {};
                next.weights.velocity = v;
              })}
            />
            <NumberInput
              label="Weight: policy escalation"
              value={draft.weights?.policy_escalation}
              disabled={!canEdit}
              onChange={(v) => onChange((next) => {
                next.weights = next.weights || {};
                next.weights.policy_escalation = v;
              })}
            />
            <NumberInput
              label="Medium risk band threshold"
              value={draft.medium_risk_threshold}
              disabled={!canEdit}
              onChange={(v) => onChange((next) => { next.medium_risk_threshold = v; })}
            />
            <NumberInput
              label="High risk band threshold"
              value={draft.high_risk_threshold}
              disabled={!canEdit}
              onChange={(v) => onChange((next) => { next.high_risk_threshold = v; })}
            />
            <label className="flex items-end gap-2 text-xs text-slate-600 dark:text-slate-300">
              <input
                type="checkbox"
                checked={!!draft.llm_triage_enabled}
                disabled={!canEdit}
                onChange={(e) => onChange((next) => { next.llm_triage_enabled = e.target.checked; })}
              />
              Enable LLM triage
            </label>
          </div>
          {guardrails && (
            <p className="mt-2 text-[11px] text-slate-500">
              Traditional weights: each 0–{guardrails.traditional_weight_max}, sum ≥ {guardrails.traditional_weight_min_sum}
              {" "}(current {liveWeightSum.toFixed(2)}).
              Prompt target {guardrails.prompt_target_min}–{guardrails.prompt_target_max}.
            </p>
          )}
          <div className="mt-2 rounded-md border border-slate-100 bg-slate-50 px-2 py-1.5 text-[11px] text-slate-600 dark:border-slate-700 dark:bg-slate-900/30 dark:text-slate-300">
            <p>{liveFormula.traditional}</p>
            <p>{liveFormula.post_profile}</p>
            <p>{liveFormula.final}</p>
          </div>
          <div className="mt-3 flex flex-col gap-2 sm:flex-row">
            <button
              type="button"
              disabled={!canEdit || saving || restoring}
              onClick={handleRestoreDefaults}
              className="flex-1 rounded-md border border-slate-200 bg-white px-3 py-2 text-xs font-semibold text-slate-700 hover:bg-slate-50 disabled:opacity-50 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700"
            >
              {restoring ? "Restoring…" : "Restore defaults"}
            </button>
            <button
              type="button"
              disabled={!canEdit || saving || restoring}
              onClick={onSave}
              className="flex-1 rounded-md bg-teal-600 px-3 py-2 text-xs font-semibold text-white disabled:opacity-50"
            >
              {saving ? "Saving…" : "Save & reassess keys"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
