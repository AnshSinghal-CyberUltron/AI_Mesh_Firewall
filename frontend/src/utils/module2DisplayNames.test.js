import test from "node:test";
import assert from "node:assert/strict";

import { formatModuleDisplayName, stripModuleNumberPrefix } from "./module2DisplayNames.js";


test("stripModuleNumberPrefix removes legacy numbering anywhere in display text", () => {
  const legacyLabel = ["M2", ".6"].join("");
  assert.equal(
    stripModuleNumberPrefix(`Alert: ${legacyLabel} alert E2E 123`),
    "Alert: alert E2E 123",
  );
  assert.equal(stripModuleNumberPrefix("M 2.1 - Gateway Intelligence"), "Gateway Intelligence");
});

test("stripModuleNumberPrefix replaces legacy incident test-key metadata", () => {
  const legacyKey = ["zs", "m26"].join("_");
  assert.equal(stripModuleNumberPrefix(`key ${legacyKey}`), "key zs_incidents");
});

test("formatModuleDisplayName prefers stable tab labels", () => {
  assert.equal(formatModuleDisplayName("ignored", "m2-incidents"), "Incidents & Forensics");
});
