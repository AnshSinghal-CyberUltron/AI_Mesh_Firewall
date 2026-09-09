import { describe, it } from "node:test";
import assert from "node:assert/strict";
import {
  resolveTier2SegmentValue,
  buildTier2PutPayload,
  TIER2_SEGMENTS,
} from "./tier2ScanControl.js";

// policy-driven-detection Req 3.6 — the Tier-2 toggle reflects the backend
// value AND toggling builds the partial PUT payload the backend expects.
// Contract: null->inherit, true->enabled, false->disabled.

describe("resolveTier2SegmentValue (backend value -> displayed segment)", () => {
  it("null reflects to inherit", () => {
    assert.equal(resolveTier2SegmentValue(null), "inherit");
  });

  it("undefined (absent key) reflects to inherit", () => {
    assert.equal(resolveTier2SegmentValue(undefined), "inherit");
  });

  it("true reflects to enabled", () => {
    assert.equal(resolveTier2SegmentValue(true), "enabled");
  });

  it("false reflects to disabled (not inherit)", () => {
    assert.equal(resolveTier2SegmentValue(false), "disabled");
  });
});

describe("buildTier2PutPayload (segment -> partial PUT payload)", () => {
  it("inherit builds { tier2_enabled: null }", () => {
    assert.deepEqual(buildTier2PutPayload("inherit"), { tier2_enabled: null });
  });

  it("enabled builds { tier2_enabled: true }", () => {
    assert.deepEqual(buildTier2PutPayload("enabled"), { tier2_enabled: true });
  });

  it("disabled builds { tier2_enabled: false }", () => {
    assert.deepEqual(buildTier2PutPayload("disabled"), { tier2_enabled: false });
  });

  it("only ever sends the single tier2_enabled field (partial PUT)", () => {
    for (const seg of TIER2_SEGMENTS) {
      assert.deepEqual(Object.keys(buildTier2PutPayload(seg)), ["tier2_enabled"]);
    }
  });
});

describe("round-trip: toggle payload reflects back to the same segment", () => {
  it("every segment survives PUT-payload -> reflect", () => {
    for (const seg of TIER2_SEGMENTS) {
      const { tier2_enabled } = buildTier2PutPayload(seg);
      assert.equal(
        resolveTier2SegmentValue(tier2_enabled),
        seg,
        `segment ${seg} did not round-trip`,
      );
    }
  });

  it("backend value reflects to a segment whose PUT payload matches the backend contract", () => {
    // (a) reflect direction then (b) PUT direction agree with the null/true/false contract.
    const cases = [
      { backend: null, segment: "inherit" },
      { backend: true, segment: "enabled" },
      { backend: false, segment: "disabled" },
    ];
    for (const { backend, segment } of cases) {
      assert.equal(resolveTier2SegmentValue(backend), segment);
      assert.deepEqual(buildTier2PutPayload(segment), { tier2_enabled: backend });
    }
  });
});
