import test from "node:test";
import assert from "node:assert/strict";
import { formatFleetListingSummary } from "./fleetListingSummary.js";

test("formatFleetListingSummary explains idle keys vs registered total", () => {
  const text = formatFleetListingSummary({
    shownCount: 1,
    listedCount: 1,
    registeredTotal: 6,
    filterId: "all",
    periodLabel: "1 hour",
  });
  assert.match(text, /Showing 1 of 6 registered keys/);
  assert.match(text, /5 idle in 1 hour/);
});

test("formatFleetListingSummary when all registered keys are listed", () => {
  const text = formatFleetListingSummary({
    shownCount: 3,
    listedCount: 3,
    registeredTotal: 3,
    filterId: "all",
    periodLabel: "24 hours",
  });
  assert.equal(text, "Showing 3 of 3 registered keys.");
});

test("formatFleetListingSummary for a secondary filter", () => {
  const text = formatFleetListingSummary({
    shownCount: 0,
    listedCount: 1,
    registeredTotal: 6,
    filterId: "disabled",
    periodLabel: "1 hour",
  });
  assert.match(text, /Showing 0 matching this filter/);
  assert.match(text, /1 listed for 1 hour of 6 registered/);
});
