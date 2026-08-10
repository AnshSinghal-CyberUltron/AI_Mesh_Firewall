import test from "node:test";
import assert from "node:assert/strict";
import {
  buildRiskCalcFormulaLines,
  cloneDefaultRiskCalcSettings,
  DEFAULT_UEBA_RISK_CALC_SETTINGS,
  traditionalWeightSum,
} from "./uebaRiskCalcDefaults.js";

test("buildRiskCalcFormulaLines substitutes draft weights", () => {
  const lines = buildRiskCalcFormulaLines({
    behavior_profile_prompt_target: 50,
    llm_triage_min_traditional_score: 0.45,
    llm_triage_enabled: true,
    weights: {
      block_rate: 0.5,
      threat_severity: 0.25,
      velocity: 0.15,
      policy_escalation: 0.1,
      baseline_deviation: 0.2,
    },
  });
  assert.match(lines.traditional, /0\.5×block_rate/);
  assert.match(lines.post_profile, /0\.2 × behavior_deviation_factor \(after 50 prompts\)/);
  assert.match(lines.final, /0\.45 × llm_adjustment/);
});

test("buildRiskCalcFormulaLines reflects user prompt-target override", () => {
  const lines = buildRiskCalcFormulaLines({
    behavior_profile_prompt_target: 75,
    weights: { baseline_deviation: 0.2 },
  });
  assert.match(lines.post_profile, /after 75 prompts/);
});

test("buildRiskCalcFormulaLines updates when weights change", () => {
  const lines = buildRiskCalcFormulaLines({
    llm_triage_enabled: true,
    weights: { block_rate: 0.6, threat_severity: 0.2, velocity: 0.1, policy_escalation: 0.1, baseline_deviation: 0.3 },
  });
  assert.match(lines.traditional, /0\.6×block_rate/);
  assert.match(lines.post_profile, /0\.3 × behavior_deviation_factor/);
});

test("cloneDefaultRiskCalcSettings matches platform defaults", () => {
  const clone = cloneDefaultRiskCalcSettings();
  assert.deepEqual(clone, DEFAULT_UEBA_RISK_CALC_SETTINGS);
  assert.equal(traditionalWeightSum(clone), 1);
  assert.equal(clone.high_risk_threshold, 0.7);
  assert.equal(clone.medium_risk_threshold, 0.35);
});
