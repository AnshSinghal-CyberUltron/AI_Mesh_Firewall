/** Platform defaults — must match control OrgUebaSettings / migration 0006. */
export const DEFAULT_UEBA_RISK_CALC_SETTINGS = {
  behavior_profile_prompt_target: 50,
  llm_triage_enabled: true,
  llm_triage_min_traditional_score: 0.45,
  weights: {
    block_rate: 0.5,
    threat_severity: 0.25,
    velocity: 0.15,
    policy_escalation: 0.1,
    baseline_deviation: 0.2,
  },
};

export const LLM_BLEND_WEIGHT = 0.45;

function fmtWeight(value, fallback = 0) {
  const n = Number(value);
  if (!Number.isFinite(n)) return String(fallback);
  return n.toFixed(2).replace(/\.?0+$/, (m) => (m === "." ? "" : m)) || "0";
}

/** Live formula lines reflecting the current draft (or saved settings). */
export function buildRiskCalcFormulaLines(draft) {
  const w = draft?.weights || {};
  const wBlock = fmtWeight(w.block_rate, DEFAULT_UEBA_RISK_CALC_SETTINGS.weights.block_rate);
  const wThreat = fmtWeight(w.threat_severity, DEFAULT_UEBA_RISK_CALC_SETTINGS.weights.threat_severity);
  const wVelocity = fmtWeight(w.velocity, DEFAULT_UEBA_RISK_CALC_SETTINGS.weights.velocity);
  const wPolicy = fmtWeight(w.policy_escalation, DEFAULT_UEBA_RISK_CALC_SETTINGS.weights.policy_escalation);
  const wBaseline = fmtWeight(w.baseline_deviation, DEFAULT_UEBA_RISK_CALC_SETTINGS.weights.baseline_deviation);
  const triage = draft?.llm_triage_min_traditional_score ?? DEFAULT_UEBA_RISK_CALC_SETTINGS.llm_triage_min_traditional_score;
  const promptTarget = draft?.behavior_profile_prompt_target ?? DEFAULT_UEBA_RISK_CALC_SETTINGS.behavior_profile_prompt_target;
  const llmOn = draft?.llm_triage_enabled !== false;

  return {
    traditional: `score = ${wBlock}×block_rate + ${wThreat}×threat_idx + ${wVelocity}×velocity + ${wPolicy}×policy_esc + redact_bonus`,
    post_profile: `traditional += ${wBaseline} × behavior_deviation_factor (after ${promptTarget} prompts)`,
    final: llmOn
      ? `final = traditional + ${LLM_BLEND_WEIGHT} × llm_adjustment (when score ≥ ${triage} or anomaly flags)`
      : `final = traditional (LLM triage disabled)`,
    llm_blend_weight: LLM_BLEND_WEIGHT,
  };
}

export function traditionalWeightSum(draft) {
  const w = draft?.weights || {};
  const keys = ["block_rate", "threat_severity", "velocity", "policy_escalation"];
  return keys.reduce((sum, key) => sum + (Number(w[key]) || 0), 0);
}

export function cloneDefaultRiskCalcSettings() {
  return structuredClone(DEFAULT_UEBA_RISK_CALC_SETTINGS);
}
