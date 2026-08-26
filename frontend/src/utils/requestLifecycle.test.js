import { describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  composeAbortSignal,
  DEFAULT_FETCH_TIMEOUT_MS,
  isDocumentHidden,
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
