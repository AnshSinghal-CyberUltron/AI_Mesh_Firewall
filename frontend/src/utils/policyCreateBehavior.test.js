import test from "node:test";
import assert from "node:assert/strict";
import { resolvePolicyCreateBehavior } from "./policyCreateBehavior.js";

const MATRIX = [
  { active: "global", source: "header", target: "global", scope: "global", switchSection: false },
  { active: "pipeline", source: "header", target: "pipeline", scope: "pipeline", switchSection: false },
  { active: "rag", source: "header", target: "rag", scope: "rag", switchSection: false },
  { active: "mcp", source: "header", target: "mcp", scope: "mcp", switchSection: false },
  { active: "analytics", source: "header", target: "global", scope: "global", switchSection: true },
  { active: "vector", source: "header", target: "global", scope: "global", switchSection: true },
  { active: "global", source: "panel", target: "global", scope: "global", switchSection: false },
  { active: "pipeline", source: "panel", target: "pipeline", scope: "pipeline", switchSection: false },
  { active: "rag", source: "panel", target: "rag", scope: "rag", switchSection: false },
  { active: "mcp", source: "panel", target: "mcp", scope: "mcp", switchSection: false },
  { active: "all", source: "panel", target: "global", scope: "global", switchSection: false },
];

test("should follow strict policy create behavior matrix", () => {
  for (const row of MATRIX) {
    const result = resolvePolicyCreateBehavior(row.active, row.source);
    assert.equal(result.targetSection, row.target, `${row.source}/${row.active} target section`);
    assert.equal(result.scope, row.scope, `${row.source}/${row.active} modal scope`);
    assert.equal(result.shouldSwitchSection, row.switchSection, `${row.source}/${row.active} section switch`);
    assert.equal(result.shouldOpenCreateModal, true, `${row.source}/${row.active} should open modal`);
  }
});

test("should default unknown tabs to global behavior", () => {
  const result = resolvePolicyCreateBehavior("unknown-tab", "header");
  assert.equal(result.targetSection, "global");
  assert.equal(result.scope, "global");
  assert.equal(result.shouldSwitchSection, false);
});

test("should default unknown create source to header semantics", () => {
  const result = resolvePolicyCreateBehavior("analytics", "hotkey");
  assert.equal(result.targetSection, "global");
  assert.equal(result.scope, "global");
  assert.equal(result.createSource, "header");
  assert.equal(result.shouldSwitchSection, true);
});

