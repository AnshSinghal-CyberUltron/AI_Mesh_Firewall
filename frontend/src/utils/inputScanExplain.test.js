import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { summarizeInputScanStage } from "./inputScanExplain.js";

describe("summarizeInputScanStage", () => {
  it("smart-mask after policy — analysis only", () => {
    const { summary } = summarizeInputScanStage({
      action: "allow",
      scan_outcome: "analyzed",
      redact_noop: true,
      threat_type: "pii",
      matched_patterns: ["email_smart_masked"],
    });
    assert.match(summary, /Policy already masked/i);
    assert.match(summary, /no further masking/i);
    assert.doesNotMatch(summary, /input scan masked/i);
  });

  it("tier-2 recommends block but allow — audit note", () => {
    const { summary } = summarizeInputScanStage({
      action: "allow",
      scan_outcome: "analyzed",
      redact_noop: true,
      threat_type: "pii",
      recommended_action: "block",
    });
    assert.match(summary, /recommended BLOCK/i);
  });

  it("injection block after policy", () => {
    const { summary } = summarizeInputScanStage({
      action: "block",
      threat_type: "prompt_injection",
    });
    assert.match(summary, /injection|jailbreak/i);
    assert.match(summary, /blocked/i);
  });

  it("scanner-owned redact", () => {
    const { summary } = summarizeInputScanStage({
      action: "redact",
      threat_type: "pii",
    });
    assert.match(summary, /masked|redacted/i);
  });
});
