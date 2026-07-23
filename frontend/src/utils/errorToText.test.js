import test from "node:test";
import assert from "node:assert/strict";

import { errorToText, formatRoutingError } from "./errorToText.js";

test("errorToText returns strings unchanged", () => {
  assert.equal(errorToText("boom"), "boom");
});

test("errorToText extracts nested OpenAI error message", () => {
  assert.equal(
    errorToText({ message: "blocked", type: "invalid_request_error" }),
    "blocked",
  );
});

test("formatRoutingError maps compliance routing unsatisfiable to operator guidance", () => {
  const msg = formatRoutingError({
    code: "compliance_routing_unsatisfiable",
    blocked_by: "compliance_routing",
  }, 403);
  assert.match(msg, /compliance\/sensitivity/);
});
