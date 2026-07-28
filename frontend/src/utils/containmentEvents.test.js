import test from "node:test";
import assert from "node:assert/strict";
import {
  clearSimulatorKeyReprovisionSuppress,
  isSimulatorKeyReprovisionSuppressed,
  readSimulatorKeyReprovisionSuppress,
  suppressSimulatorKeyReprovision,
} from "../utils/containmentEvents.js";

test("suppress blocks reprovision for matching disabled prefix", () => {
  clearSimulatorKeyReprovisionSuppress();
  suppressSimulatorKeyReprovision({ prefix: "Ngz8_Rnz", keyId: "k1" });
  assert.equal(isSimulatorKeyReprovisionSuppressed("Ngz8_Rnz_secret_value"), true);
  assert.equal(isSimulatorKeyReprovisionSuppressed("OtherKey"), false);
  assert.deepEqual(readSimulatorKeyReprovisionSuppress()?.prefix, "Ngz8_Rnz");
  clearSimulatorKeyReprovisionSuppress();
  assert.equal(isSimulatorKeyReprovisionSuppressed("Ngz8_Rnz_secret_value"), false);
});

test("suppress without prefix still blocks heal (disable containment)", () => {
  clearSimulatorKeyReprovisionSuppress();
  suppressSimulatorKeyReprovision({ keyId: "k2" });
  assert.equal(isSimulatorKeyReprovisionSuppressed(""), true);
  assert.equal(isSimulatorKeyReprovisionSuppressed("any-key"), true);
  clearSimulatorKeyReprovisionSuppress();
});
