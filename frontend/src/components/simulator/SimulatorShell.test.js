import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { deriveResultAction } from "./simulatorResultUtils.js";

describe("deriveResultAction", () => {
  it("returns block for threat_intel_blocked 403 without final_action", () => {
    const action = deriveResultAction({
      error: "Request blocked due to security policy",
      code: "threat_intel_blocked",
      status: 403,
    });
    assert.equal(action, "block");
  });

  it("returns allow for successful result", () => {
    assert.equal(deriveResultAction({ ok: true, content: "Paris" }), "allow");
  });

  it("prefers final_action when set", () => {
    assert.equal(deriveResultAction({ final_action: "block", status: 200 }), "block");
  });

  it("returns error for generic failures", () => {
    assert.equal(deriveResultAction({ error: "Network error", status: 500 }), "error");
  });
});
