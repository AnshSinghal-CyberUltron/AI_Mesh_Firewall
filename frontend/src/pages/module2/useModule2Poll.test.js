import { describe, expect, it } from "vitest";
import { MODULE2_HOT_POLL_MS, MODULE2_POLL_MS, MODULE2_ENFORCEMENT_NUDGE_EVENT } from "./useModule2Poll";

describe("useModule2Poll constants", () => {
  it("exposes a 5s hot poll for traffic lanes", () => {
    expect(MODULE2_HOT_POLL_MS).toBe(5_000);
  });

  it("keeps quieter pages on 30s default poll", () => {
    expect(MODULE2_POLL_MS).toBe(30_000);
  });

  it("exports the enforcement nudge event name", () => {
    expect(MODULE2_ENFORCEMENT_NUDGE_EVENT).toBe("module2:enforcement-nudge");
  });
});
