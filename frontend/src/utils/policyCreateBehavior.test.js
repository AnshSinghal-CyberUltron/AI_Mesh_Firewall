import test from "node:test";
import assert from "node:assert/strict";
import {
  resolvePolicyCreateBehavior,
  getPolicyDomainUi,
  POLICY_CREATE_DOMAINS,
} from "./policyCreateBehavior.js";

const MATRIX = [
  { active: "pipeline", source: "header", target: "pipeline", scope: "pipeline", switchSection: false },
  { active: "rag", source: "header", target: "rag", scope: "rag", switchSection: false },
  { active: "mcp", source: "header", target: "mcp", scope: "mcp", switchSection: false },
  { active: "analytics", source: "header", target: "pipeline", scope: "pipeline", switchSection: true },
  { active: "vector", source: "header", target: "pipeline", scope: "pipeline", switchSection: true },
  { active: "pipeline", source: "panel", target: "pipeline", scope: "pipeline", switchSection: false },
  { active: "rag", source: "panel", target: "rag", scope: "rag", switchSection: false },
  { active: "mcp", source: "panel", target: "mcp", scope: "mcp", switchSection: false },
  { active: "all", source: "panel", target: "pipeline", scope: "pipeline", switchSection: false },
];

test("should follow strict policy create behavior matrix", () => {
  for (const row of MATRIX) {
    const result = resolvePolicyCreateBehavior(row.active, row.source);
    assert.equal(result.targetSection, row.target, `${row.source}/${row.active} target section`);
    assert.equal(result.scope, row.scope, `${row.source}/${row.active} modal scope`);
    assert.equal(result.shouldSwitchSection, row.switchSection, `${row.source}/${row.active} section switch`);
    assert.equal(result.shouldOpenCreateModal, true, `${row.source}/${row.active} should open modal`);
    const expectsLock = ["pipeline", "rag", "mcp"].includes(row.target);
    assert.equal(result.lockScope, expectsLock, `${row.source}/${row.active} lock scope`);
    assert.equal(result.usesVectorModal, row.active === "vector", `${row.source}/${row.active} vector modal`);
  }
});

test("should default unknown tabs to pipeline behavior", () => {
  const result = resolvePolicyCreateBehavior("unknown-tab", "header");
  assert.equal(result.targetSection, "pipeline");
  assert.equal(result.scope, "pipeline");
  assert.equal(result.shouldSwitchSection, false);
});

test("should default unknown create source to header semantics", () => {
  const result = resolvePolicyCreateBehavior("analytics", "hotkey");
  assert.equal(result.targetSection, "pipeline");
  assert.equal(result.scope, "pipeline");
  assert.equal(result.createSource, "header");
  assert.equal(result.shouldSwitchSection, true);
});

test("legacy global tab normalizes to pipeline", () => {
  const result = resolvePolicyCreateBehavior("global", "header");
  assert.equal(result.targetSection, "pipeline");
  assert.equal(result.scope, "pipeline");
});

test("in-panel domain switcher exposes four domains in order", () => {
  assert.deepEqual(
    POLICY_CREATE_DOMAINS.map((d) => d.key),
    ["pipeline", "rag", "mcp", "vector"],
  );
});

test("getPolicyDomainUi returns the Vector domain's own metadata (not pipeline)", () => {
  assert.equal(getPolicyDomainUi("vector").label, "Vector");
  assert.equal(getPolicyDomainUi("mcp").label, "MCP");
  assert.equal(getPolicyDomainUi("analytics").label, "Pipeline");
  assert.equal(getPolicyDomainUi("nonsense").label, "Pipeline");
});
