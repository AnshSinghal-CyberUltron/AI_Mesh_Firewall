import { Calculator, ChevronDown, ChevronRight } from "lucide-react";
import { useState } from "react";

const DEFAULT_REFERENCE = {
  traditional:
    "score = w_block*block_rate + w_threat*threat_idx + w_velocity*velocity + w_policy*policy_esc + redact_bonus",
  post_profile: "traditional += weight_baseline_deviation * behavior_deviation_factor",
  final: "final = traditional + 0.45 * llm_adjustment (when LLM runs)",
  llm_blend_weight: 0.45,
};

function fmt(value, digits = 3) {
  if (value == null || Number.isNaN(Number(value))) return "—";
  return Number(value).toFixed(digits);
}

function pct(value) {
  if (value == null || Number.isNaN(Number(value))) return "—";
  return `${(Number(value) * 100).toFixed(1)}%`;
}

function buildTraditionalRows(breakdown, weights) {
  const w = breakdown?.weights || weights || {};
  const components = [
    {
      key: "block_rate",
      label: "Block rate",
      raw: breakdown?.block_rate,
      weight: w.block_rate,
      rawLabel: pct(breakdown?.block_rate),
    },
    {
      key: "threat_severity",
      label: "Threat severity index",
      raw: breakdown?.threat_severity_index,
      weight: w.threat_severity,
      rawLabel: fmt(breakdown?.threat_severity_index, 3),
    },
    {
      key: "velocity",
      label: "Velocity factor",
      raw: breakdown?.velocity_factor,
      weight: w.velocity,
      rawLabel: fmt(breakdown?.velocity_factor, 3),
    },
    {
      key: "policy_escalation",
      label: "Policy escalation",
      raw: breakdown?.policy_escalation,
      weight: w.policy_escalation,
      rawLabel: fmt(breakdown?.policy_escalation, 3),
    },
  ];

  const rows = components.map((item) => ({
    ...item,
    contribution:
      item.raw != null && item.weight != null
        ? Number(item.raw) * Number(item.weight)
        : null,
  }));

  if (breakdown?.redact_contribution != null) {
    rows.push({
      key: "redact_bonus",
      label: "Redact learning bonus",
      raw: breakdown.redact_contribution,
      weight: null,
      rawLabel: fmt(breakdown.redact_contribution, 3),
      contribution: Number(breakdown.redact_contribution),
    });
  }

  return rows;
}

function StepRow({ label, formula, value, detail }) {
  return (
    <div className="rounded-md border border-slate-100 bg-white/70 px-3 py-2 dark:border-slate-700 dark:bg-slate-800/50">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">{label}</p>
        <p className="font-mono text-sm font-semibold text-slate-900 dark:text-white">{value}</p>
      </div>
      <p className="mt-1 font-mono text-[11px] text-slate-600 dark:text-slate-300">{formula}</p>
      {detail && <p className="mt-1 text-[11px] text-slate-500 dark:text-slate-400">{detail}</p>}
    </div>
  );
}

export function RiskScoreCalculationGuide({
  behavior,
  riskCalculation,
  collapsible = false,
  defaultOpen = false,
}) {
  const [open, setOpen] = useState(defaultOpen);
  if (!behavior) return null;

  const breakdown = behavior.score_breakdown || {};
  const reference = riskCalculation?.formula_reference || DEFAULT_REFERENCE;
  const settings = riskCalculation?.settings || {};
  const weights = settings.weights || breakdown.weights || {};
  const guardrails = settings.weight_guardrails;
  const profileReady = behavior.behavior_profile?.status === "ready";
  const llmBlend = reference.llm_blend_weight ?? DEFAULT_REFERENCE.llm_blend_weight;

  const traditionalRows = buildTraditionalRows(breakdown, weights);
  const traditionalSum = traditionalRows.reduce(
    (sum, row) => sum + (row.contribution ?? 0),
    0,
  );
  const baselineDelta = breakdown.baseline_deviation_delta;
  const baselineFactor = breakdown.baseline_deviation_factor;
  const baselineWeight = breakdown.baseline_deviation_weight ?? weights.baseline_deviation;
  const llmDelta = breakdown.llm_weighted_delta;
  const traditionalScore = behavior.traditional_score;
  const finalScore = behavior.final_score ?? behavior.risk_score;
  const preBaselineTraditional =
    baselineDelta != null && traditionalScore != null
      ? Number(traditionalScore) - Number(baselineDelta)
      : traditionalSum;

  const body = (
    <>
      <div className="mb-3 space-y-1 rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-[11px] text-slate-600 dark:border-slate-700 dark:bg-slate-900/30 dark:text-slate-300">
        <p><strong>Traditional:</strong> {reference.traditional}</p>
        <p><strong>Post-profile:</strong> {reference.post_profile}</p>
        <p><strong>Final:</strong> {reference.final}</p>
        {guardrails && (
          <p className="text-slate-500">
            Weights normalized at score time when traditional sum ≠ 1.
            Baseline deviation max {guardrails.baseline_deviation_weight_max}.
            Risk bands: high ≥ {reference.bands?.high ?? settings.high_risk_threshold ?? 0.7},
            medium ≥ {reference.bands?.medium ?? settings.medium_risk_threshold ?? 0.35}.
          </p>
        )}
      </div>

      <p className="mb-2 text-[11px] font-semibold uppercase text-slate-500">Step 1 — Traditional components</p>
      <div className="overflow-x-auto">
        <table className="mb-3 w-full min-w-[520px] text-xs">
          <thead>
            <tr className="border-b border-slate-200 text-left dark:border-slate-700">
              <th className="px-2 py-1 font-medium text-slate-500">Component</th>
              <th className="px-2 py-1 font-medium text-slate-500">Raw value</th>
              <th className="px-2 py-1 font-medium text-slate-500">Weight</th>
              <th className="px-2 py-1 font-medium text-slate-500">Contribution</th>
            </tr>
          </thead>
          <tbody>
            {traditionalRows.map((row) => (
              <tr key={row.key} className="border-b border-slate-100 dark:border-slate-800">
                <td className="px-2 py-1 text-slate-700 dark:text-slate-200">{row.label}</td>
                <td className="px-2 py-1 font-mono">{row.rawLabel}</td>
                <td className="px-2 py-1 font-mono">{row.weight != null ? fmt(row.weight, 3) : "—"}</td>
                <td className="px-2 py-1 font-mono font-semibold">{fmt(row.contribution, 4)}</td>
              </tr>
            ))}
            <tr className="bg-slate-50/80 dark:bg-slate-900/40">
              <td colSpan={3} className="px-2 py-1.5 font-semibold text-slate-700 dark:text-slate-200">
                Traditional subtotal (pre-profile)
              </td>
              <td className="px-2 py-1.5 font-mono font-bold text-slate-900 dark:text-white">
                {fmt(preBaselineTraditional, 3)}
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <div className="space-y-2">
        {profileReady && baselineDelta != null ? (
          <StepRow
            label="Step 2 — Baseline deviation (profile ready)"
            formula={`${fmt(baselineWeight, 2)} × ${fmt(baselineFactor, 3)} = ${fmt(baselineDelta, 4)}`}
            value={fmt(traditionalScore, 3)}
            detail="Adds deviation from LLM-learned behavior baseline after profile is ready."
          />
        ) : (
          <StepRow
            label="Step 2 — Baseline deviation"
            formula={reference.post_profile}
            value={fmt(traditionalScore, 3)}
            detail={
              profileReady
                ? "Profile ready but no deviation delta recorded for this window."
                : `Collecting prompts (${behavior.behavior_profile?.prompt_samples_collected ?? 0}/${behavior.behavior_profile?.prompt_samples_target ?? 50}) — baseline term not applied yet.`
            }
          />
        )}

        {llmDelta != null && behavior.llm_verdict && behavior.llm_verdict !== "skipped" ? (
          <StepRow
            label="Step 3 — LLM analyst adjustment"
            formula={`traditional + ${llmBlend} × adjustment = ${fmt(finalScore, 3)} (Δ ${fmt(llmDelta, 4)})`}
            value={fmt(finalScore, 3)}
            detail={`Verdict: ${behavior.llm_verdict}${behavior.llm_reasoning ? ` — ${behavior.llm_reasoning.slice(0, 160)}${behavior.llm_reasoning.length > 160 ? "…" : ""}` : ""}`}
          />
        ) : (
          <StepRow
            label="Step 3 — LLM analyst adjustment"
            formula={`final = traditional + ${llmBlend} × llm_adjustment (when triage runs)`}
            value={fmt(finalScore, 3)}
            detail={
              settings.llm_triage_enabled === false
                ? "LLM triage disabled for this org."
                : behavior.llm_observation?.requests_below_prompt_target
                  ? `Profile building — need ≥ ${behavior.llm_observation.prompt_target ?? settings.behavior_profile_prompt_target ?? 50} requests (currently ${behavior.llm_observation.request_count ?? behavior.request_count ?? 0}).`
                  : !behavior.llm_observation?.profile_ready
                    ? `Collecting prompts (${behavior.llm_observation?.prompt_samples_collected ?? behavior.behavior_profile?.prompt_samples_collected ?? 0}/${behavior.llm_observation?.prompt_target ?? settings.behavior_profile_prompt_target ?? 50}) before LLM triage.`
                    : behavior.llm_verdict === "skipped" || !behavior.llm_observation?.triage_score_gate_met
                      ? `Skipped — traditional score below triage threshold (${behavior.llm_observation?.triage_threshold ?? settings.llm_triage_min_traditional_score ?? 0.45}) unless anomaly flags fire.`
                      : "No LLM adjustment applied for this assessment."
            }
          />
        )}

        <StepRow
          label="Final behavioral risk score"
          formula={`Band: ${(behavior.risk_band || "low").toUpperCase()} · ${behavior.request_count ?? 0} requests in window`}
          value={fmt(finalScore, 3)}
        />
      </div>

      {(breakdown.anomaly_flags || []).length > 0 && (
        <p className="mt-3 text-[11px] text-amber-700 dark:text-amber-300">
          Anomaly flags influencing review: {(breakdown.anomaly_flags || []).join(", ")}
        </p>
      )}
    </>
  );

  if (collapsible) {
    return (
      <div className="rounded-lg border border-slate-200 dark:border-slate-700">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="flex w-full items-center justify-between gap-2 px-3 py-2.5 text-left hover:bg-slate-50 dark:hover:bg-slate-800/50"
          aria-expanded={open}
        >
          <span className="inline-flex items-center gap-2 text-xs font-semibold uppercase text-slate-600 dark:text-slate-300">
            <Calculator className="h-3.5 w-3.5 text-teal-600 dark:text-teal-400" />
            How default score is calculated
          </span>
          {open ? <ChevronDown className="h-4 w-4 text-slate-400" /> : <ChevronRight className="h-4 w-4 text-slate-400" />}
        </button>
        {open && (
          <div className="border-t border-slate-200 px-3 py-3 dark:border-slate-700">
            <p className="mb-3 text-[11px] text-slate-500 dark:text-slate-400">
              Default org formula and weights applied to this key unless customized below.
            </p>
            {body}
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-teal-200/80 bg-teal-50/40 p-3 dark:border-teal-900/50 dark:bg-teal-950/20">
      <div className="mb-3 flex items-start gap-2">
        <Calculator className="mt-0.5 h-4 w-4 shrink-0 text-teal-600 dark:text-teal-400" />
        <div>
          <p className="text-xs font-semibold uppercase text-teal-800 dark:text-teal-200">
            Risk score calculation
          </p>
          <p className="text-[11px] text-slate-600 dark:text-slate-300">
            Step-by-step breakdown for this key using your org&apos;s current formula and weights.
          </p>
        </div>
      </div>
      {body}
    </div>
  );
}
