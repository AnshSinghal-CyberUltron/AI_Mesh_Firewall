/**
 * Minimal node:test coverage for demo pipelineTrace helpers.
 * Run: node --test examples/zeroshield-openai-demo/frontend/lib/pipelineTrace.test.js
 */
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import {
  honestStageAction,
  normalizeStages,
  resolveRoutingDecision,
  resolveTotalLatencyMs,
  latencyMsWithinTolerance,
} from "./pipelineTrace.js";

describe("honestStageAction", () => {
  it("relabels redact+noop as flag", () => {
    assert.equal(honestStageAction({ action: "redact", redact_noop: true }), "flag");
  });
  it("keeps allow when analyzed", () => {
    assert.equal(honestStageAction({ action: "allow", scan_outcome: "analyzed" }), "allow");
  });
});

describe("normalizeStages", () => {
  it("drops nulls and maps stage→name", () => {
    const out = normalizeStages([null, { stage: "policy", action: "allow" }]);
    assert.equal(out.length, 1);
    assert.equal(out[0].name, "policy");
  });
});

describe("resolveRoutingDecision", () => {
  it("merges model_routing stage", () => {
    const r = resolveRoutingDecision({
      pipelineTrace: {
        stages: [{
          name: "model_routing",
          requested_model: "auto",
          selected_model: "gpt-4o-mini",
          routed_model: "gpt-4o-mini",
          routing_reason: "best",
          decision_source: "policy_adjudicator",
        }],
      },
    });
    assert.equal(r.selected_model, "gpt-4o-mini");
    assert.equal(r.decision_source, "policy_adjudicator");
  });
});

describe("latency parity", () => {
  it("sums stages when total missing", () => {
    const ms = resolveTotalLatencyMs({
      pipelineTrace: {
        stages: [
          { name: "a", latency_ms: 10 },
          { name: "b", latency_ms: 5.5 },
        ],
      },
    });
    assert.equal(ms, 15.5);
  });
  it("tolerance helper", () => {
    assert.equal(latencyMsWithinTolerance(10.0, 10.05), true);
    assert.equal(latencyMsWithinTolerance(10.0, 10.2), false);
  });
});
