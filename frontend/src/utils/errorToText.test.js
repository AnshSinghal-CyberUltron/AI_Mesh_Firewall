import { describe, expect, it } from "vitest";
import { errorToText, formatRoutingError } from "./errorToText";

describe("errorToText", () => {
  it("returns strings unchanged", () => {
    expect(errorToText("boom")).toBe("boom");
  });

  it("extracts nested OpenAI error message", () => {
    expect(errorToText({ message: "blocked", type: "invalid_request_error" })).toBe("blocked");
  });
});

describe("formatRoutingError", () => {
  it("maps compliance routing unsatisfiable to operator guidance", () => {
    const msg = formatRoutingError({
      code: "compliance_routing_unsatisfiable",
      blocked_by: "compliance_routing",
    }, 403);
    expect(msg).toContain("compliance/sensitivity");
  });
});
