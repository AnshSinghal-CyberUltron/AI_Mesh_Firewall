import { describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  composeAbortSignal,
  DEFAULT_FETCH_TIMEOUT_MS,
  HEAVY_ANALYTICS_CONCURRENCY,
  isDocumentHidden,
  mapWithConcurrency,
} from "./requestLifecycle.js";
import { startVisibleInterval } from "./visiblePoll.js";

describe("requestLifecycle (Phase 0a F-b)", () => {
  it("defaults fetch timeout to 30s well below nginx 300s", () => {
    assert.equal(DEFAULT_FETCH_TIMEOUT_MS, 30_000);
    assert.ok(DEFAULT_FETCH_TIMEOUT_MS < 300_000);
  });

  it("composeAbortSignal returns a signal that aborts", () => {
    const signal = composeAbortSignal(undefined, 30_000);
    assert.equal(signal.aborted, false);
    assert.ok(typeof signal.addEventListener === "function");
  });

  it("composeAbortSignal aborts when the caller signal aborts", () => {
    const caller = new AbortController();
    const signal = composeAbortSignal(caller.signal, 60_000);
    caller.abort();
    assert.equal(signal.aborted, true);
  });
});

describe("visiblePoll (Phase 0a F-a)", () => {
  it("isDocumentHidden is true when document.hidden is true", () => {
    globalThis.document = { hidden: true };
    assert.equal(isDocumentHidden(), true);
    globalThis.document = { hidden: false };
    assert.equal(isDocumentHidden(), false);
    delete globalThis.document;
  });

  it("startVisibleInterval does not fire the callback while hidden", async () => {
    globalThis.document = { hidden: true };
    let n = 0;
    const stop = startVisibleInterval(() => {
      n += 1;
    }, 20);
    await new Promise((r) => setTimeout(r, 70));
    stop();
    delete globalThis.document;
    assert.equal(n, 0);
  });

  it("startVisibleInterval fires while visible", async () => {
    globalThis.document = { hidden: false };
    let n = 0;
    const stop = startVisibleInterval(() => {
      n += 1;
    }, 20);
    await new Promise((r) => setTimeout(r, 70));
    stop();
    delete globalThis.document;
    assert.ok(n >= 1);
  });
});

describe("requestLifecycle (Phase 0b F-c)", () => {
  it("caps heavy analytics concurrency at 2", () => {
    assert.equal(HEAVY_ANALYTICS_CONCURRENCY, 2);
  });

  it("mapWithConcurrency never runs more than the limit at once", async () => {
    let inflight = 0;
    let peak = 0;
    const items = [1, 2, 3, 4];
    const out = await mapWithConcurrency(items, 2, async (n) => {
      inflight += 1;
      peak = Math.max(peak, inflight);
      await new Promise((r) => setTimeout(r, 30));
      inflight -= 1;
      return n * 10;
    });
    assert.equal(peak, 2);
    assert.deepEqual(out, [10, 20, 30, 40]);
  });

  it("mapWithConcurrency stops starting work after abort", async () => {
    const ac = new AbortController();
    let started = 0;
    const pending = mapWithConcurrency([1, 2, 3, 4], 2, async (n, _i, signal) => {
      started += 1;
      if (n === 1) ac.abort();
      await new Promise((r) => setTimeout(r, 20));
      if (signal?.aborted) {
        const err = new Error("aborted");
        err.name = "AbortError";
        throw err;
      }
      return n;
    }, ac.signal);
    await pending.catch(() => {});
    assert.ok(started <= 3);
  });

  it("isLiveGeneration rejects aborted and superseded generations", async () => {
    const { isLiveGeneration } = await import("./requestLifecycle.js");
    const live = new AbortController();
    const dead = new AbortController();
    dead.abort();
    assert.equal(isLiveGeneration(live.signal, 2, 2), true);
    assert.equal(isLiveGeneration(dead.signal, 2, 2), false);
    assert.equal(isLiveGeneration(live.signal, 1, 2), false);
  });
});
