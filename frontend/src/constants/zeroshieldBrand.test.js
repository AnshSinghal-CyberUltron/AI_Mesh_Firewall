import test from "node:test";
import assert from "node:assert/strict";
import {
  formatRoutingReason,
  formatZeroshieldScanSummary,
  ZEROSHIELD_ADJUDICATOR_LABEL,
} from "./zeroshieldBrand.js";

test("formatZeroshieldScanSummary maps tier-2 clean pass to operator labels", () => {
  const summary = formatZeroshieldScanSummary({
    detection_tier: "tier_2",
    threat_type: "clean",
    scan_outcome: "clean",
    action: "allow",
    confidence: 0.99,
    risk_score: 0.01,
    detail: "Guard Model scan completed",
  });
  assert.match(summary.tierLabel, /ZeroShield Guard Model/);
  assert.equal(summary.threatLabel, "No threat detected");
  assert.equal(summary.scoreLabel, "Risk score");
  assert.equal(summary.scoreValue, "1%");
  assert.equal(summary.action, "ALLOW");
  assert.equal(summary.clean, true);
});

test("formatZeroshieldScanSummary keeps confidence label for flagged threats", () => {
  const summary = formatZeroshieldScanSummary({
    detection_tier: "tier_2",
    threat_type: "prompt_injection",
    action: "flag",
    confidence: 0.72,
  });
  assert.equal(summary.threatLabel, "prompt injection");
  assert.equal(summary.scoreLabel, "Confidence");
  assert.equal(summary.scoreValue, "72%");
  assert.equal(summary.clean, false);
});

test("formatRoutingReason replaces Bedrock adjudicator branding", () => {
  const formatted = formatRoutingReason(
    "Bedrock GPT OSS 120B adjudicator selected 'Haiku'",
    { decisionSource: "policy_adjudicator" },
  );
  assert.match(formatted, /ZeroShield Policy Adjudicator/);
  assert.doesNotMatch(formatted, /Bedrock/);
});
