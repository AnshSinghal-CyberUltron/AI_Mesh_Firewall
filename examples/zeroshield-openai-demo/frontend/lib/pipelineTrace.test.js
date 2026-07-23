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
  extractLastUserPromptSegment,
  resolveRedactedChatUserText,
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

describe("chat-plane input redaction", () => {
  it("extracts the last [user] segment", () => {
    assert.equal(
      extractLastUserPromptSegment(
        "[user]: hello\n[assistant]: hi\n[user]: my aadhar card is [REDACTED_AADHAAR]",
      ),
      "my aadhar card is [REDACTED_AADHAAR]",
    );
  });

  it("updates display from sync input_was_redacted fields", () => {
    const r = resolveRedactedChatUserText({
      pipelineTrace: {
        input_was_redacted: true,
        input_text: "[user]: my aadhar card is 123412341234",
        prompt_submitted: "[user]: my aadhar card is [REDACTED_AADHAAR]",
        input_text_before: "[user]: my aadhar card is 123412341234",
        input_text_after: "[user]: my aadhar card is [REDACTED_AADHAAR]",
      },
    }, "my aadhar card is 123412341234");
    assert.equal(r.redacted, true);
    assert.equal(r.display, "my aadhar card is [REDACTED_AADHAAR]");
    assert.equal(r.display.includes("123412341234"), false);
  });

  it("detects stream traces that omit input_was_redacted but carry redacted prompt_submitted", () => {
    const r = resolveRedactedChatUserText({
      pipelineTrace: {
        input_was_redacted: false,
        input_text: "",
        prompt_submitted: "[user]: my aadhar card is [REDACTED_AADHAAR]",
        input_text_after: "",
      },
    }, "my aadhar card is 123412341234");
    assert.equal(r.redacted, true);
    assert.equal(r.display, "my aadhar card is [REDACTED_AADHAAR]");
  });

  it("leaves clean prompts unchanged", () => {
    const r = resolveRedactedChatUserText({
      pipelineTrace: {
        input_was_redacted: false,
        prompt_submitted: "[user]: hello",
        input_text: "[user]: hello",
      },
    }, "hello");
    assert.equal(r.redacted, false);
    assert.equal(r.display, "hello");
  });
});
