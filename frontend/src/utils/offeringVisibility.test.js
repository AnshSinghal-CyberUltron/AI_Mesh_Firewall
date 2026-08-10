import test from "node:test";
import assert from "node:assert/strict";
import { resolveOfferingVisibility } from "./offeringVisibility.js";

test("authenticated staff sees nav", () => {
  assert.equal(
    resolveOfferingVisibility({ email: "admin@zeroshield.io", roles: ["staff", "user"] }).hasPlatform,
    true,
  );
});

test("authenticated plain user sees nav", () => {
  assert.equal(resolveOfferingVisibility({ roles: ["user"] }).hasPlatform, true);
});

test("logged-out user has empty nav", () => {
  assert.equal(resolveOfferingVisibility(null).hasPlatform, false);
  assert.equal(resolveOfferingVisibility(undefined).hasPlatform, false);
});
