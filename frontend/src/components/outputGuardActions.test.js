import assert from "node:assert/strict";
import test from "node:test";

import {
  OUTPUT_GUARD_ACTIONS,
  UNSUPPORTED_OUTPUT_ACTIONS,
  coerceOutputGuardAction,
  guardrailPayloadWithoutRewrite,
} from "./outputGuardActions.js";

test("T01 L01-2: Rewrite is not an operator action", () => {
  assert.equal(
    OUTPUT_GUARD_ACTIONS.some((o) => o.value === "rewrite"),
    false,
  );
  assert.ok(UNSUPPORTED_OUTPUT_ACTIONS.has("rewrite"));
  assert.deepEqual(
    OUTPUT_GUARD_ACTIONS.map((o) => o.value),
    ["block", "redact", "flag", "allow"],
  );
});

test("T01 L01-2: stored rewrite coerces to detector default", () => {
  assert.equal(coerceOutputGuardAction("rewrite", "redact"), "redact");
  assert.equal(coerceOutputGuardAction("rewrite", "block"), "block");
  assert.equal(coerceOutputGuardAction("REWrite", "flag"), "flag");
});

test("T01 L01-2: supported actions pass through", () => {
  assert.equal(coerceOutputGuardAction("block", "redact"), "block");
  assert.equal(coerceOutputGuardAction("redact", "block"), "redact");
  assert.equal(coerceOutputGuardAction("flag", "redact"), "flag");
  assert.equal(coerceOutputGuardAction("allow", "redact"), "allow");
});

test("T01 L01-2: save payload never emits rewrite", () => {
  const payload = guardrailPayloadWithoutRewrite(
    { output_pii_action: "rewrite", output_credential_action: "block" },
    ["output_pii_action", "output_credential_action"],
    { output_pii_action: "redact" },
  );
  assert.equal(payload.output_pii_action, "redact");
  assert.equal(payload.output_credential_action, "block");
});
