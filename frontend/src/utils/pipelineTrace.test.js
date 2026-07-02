import test from "node:test";
import assert from "node:assert/strict";
import {
  honestStageAction,
  buildHonestTraceStages,
  extractRealStages,
  extractFinalAction,
} from "./pipelineTrace.js";

// TRACE_UI_CONTRACT.md: each stage must render its OWN action; a redact whose
// scrubber was a no-op is displayed as flag (never a phantom redaction).

test("honestStageAction returns the stage's own action", () => {
  assert.equal(honestStageAction({ action: "block" }), "block");
  assert.equal(honestStageAction({ action: "redact" }), "redact");
  assert.equal(honestStageAction({}), "allow");
});

test("honestStageAction downgrades a no-op redact to flag", () => {
  assert.equal(honestStageAction({ action: "redact", redact_noop: true }), "flag");
  assert.equal(honestStageAction({ action: "redact", metadata: { redact_noop: true } }), "flag");
});

test("buildHonestTraceStages gives EACH stage its own badge (no global smear)", () => {
  const realStages = [
    { stage: "policy", action: "redact", detection_tier: "policy", matched_policy_names: ["PII Detection & Redaction"], latency_ms: 1.2 },
    { stage: "input_scan", action: "allow", detection_tier: "tier_1", latency_ms: 4.5 },
    { stage: "output_guard", action: "block", threat_type: "prompt_injection", latency_ms: 3.1 },
  ];
  const nodes = buildHonestTraceStages(realStages, "block", { promptSnippet: "hi" });
  const byId = Object.fromEntries(nodes.map((n) => [n.id, n]));
  // honest per-stage: three DIFFERENT badges, not one repeated
  assert.equal(byId.policy.badge, "redacted");
  assert.equal(byId.policy.highlightAction, "redact");
  assert.equal(byId.input_scan.badge, "allowed");
  assert.equal(byId.input_scan.highlight, false); // allow is not highlighted
  assert.equal(byId.output_guard.badge, "blocked");
  assert.equal(byId.output_guard.highlightAction, "block");
  // attribution surfaced as tags (rule/policy names, not raw PII)
  assert.ok(byId.policy.tags.some((t) => t.includes("PII Detection & Redaction")));
  // latency metric present
  assert.ok(byId.policy.metrics && byId.policy.metrics[0].value.includes("ms"));
  // final node reflects the parent final_action
  assert.equal(nodes[nodes.length - 1].id, "final_output");
  assert.equal(nodes[nodes.length - 1].badge, "blocked");
});

test("buildHonestTraceStages applies the redact->flag honesty rule end to end", () => {
  const nodes = buildHonestTraceStages(
    [{ stage: "policy", action: "redact", metadata: { redact_noop: true } }],
    "flag",
    {},
  );
  const policy = nodes.find((n) => n.id === "policy");
  assert.equal(policy.badge, "flagged");
  assert.equal(policy.highlightAction, "flag");
});

test("extractRealStages finds the trace under the zeroshield envelope", () => {
  const event = { zeroshield: { pipeline_trace: { stages: [{ stage: "policy", action: "allow" }], final_action: "allow" } } };
  assert.equal(extractRealStages(event).length, 1);
  assert.equal(extractFinalAction(event, "x"), "allow");
  // no trace -> empty, and final action falls back to event.action
  assert.deepEqual(extractRealStages({ action: "block" }), []);
  assert.equal(extractFinalAction({ action: "block" }, "x"), "block");
});
