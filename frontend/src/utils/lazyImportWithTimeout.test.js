import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { lazyImportWithTimeout } from "./lazyImportWithTimeout.js";

function delay(ms, value) {
  return new Promise((resolve) => {
    setTimeout(() => resolve(value), ms);
  });
}

describe("lazyImportWithTimeout", () => {
  it("resolves when the importer finishes before timeout", async () => {
    const load = lazyImportWithTimeout(
      async () => ({ default: "ok" }),
      { timeoutMs: 1000, retries: 0, label: "fast" },
    );
    const mod = await load();
    assert.equal(mod.default, "ok");
  });

  it("rejects when the importer never settles in time", async () => {
    const late = delay(80, { default: "late" });
    const load = lazyImportWithTimeout(
      () => late,
      { timeoutMs: 20, retries: 0, label: "hung" },
    );
    await assert.rejects(load, /Timed out loading hung/);
    await late;
  });

  it("retries once after a timeout then succeeds", async () => {
    let calls = 0;
    let firstLate;
    const load = lazyImportWithTimeout(
      async () => {
        calls += 1;
        if (calls === 1) {
          firstLate = delay(80, { default: "late" });
          return firstLate;
        }
        return { default: "recovered" };
      },
      { timeoutMs: 20, retries: 1, label: "retry" },
    );
    const mod = await load();
    assert.equal(mod.default, "recovered");
    assert.equal(calls, 2);
    if (firstLate) await firstLate;
  });
});
