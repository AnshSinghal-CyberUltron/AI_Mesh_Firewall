import { describe, expect, it } from "vitest";
import { displayUebaScore, graduationPctComplete } from "./uebaScoring";

describe("uebaScoring", () => {
  it("prefers final_score over risk_score", () => {
    expect(displayUebaScore({ risk_score: 0.2, final_score: 0.7 })).toBe(0.7);
  });

  it("graduation progress respects cap", () => {
    expect(graduationPctComplete(null)).toBe(0);
    expect(graduationPctComplete({ pct_complete: 88 })).toBe(88);
  });
});
