import test from "node:test";
import assert from "node:assert/strict";
import { EventTarget } from "node:events";
import {
  syncModule2AfterTelemetryChange,
  syncModule2AfterContainmentChange,
  syncModule2AfterGatewayKeyChange,
} from "./crossModuleSync.js";
import { TELEMETRY_ACTIVITY_EVENT } from "./telemetryEvents.js";
import { CONTAINMENT_CHANGED_EVENT } from "./containmentEvents.js";

function ensureBrowserGlobals() {
  if (typeof globalThis.window === "undefined") {
    const target = new EventTarget();
    globalThis.window = {
      dispatchEvent(event) {
        target.dispatchEvent(event);
        return true;
      },
      addEventListener: (...args) => target.addEventListener(...args),
      removeEventListener: (...args) => target.removeEventListener(...args),
    };
    globalThis.CustomEvent = class CustomEvent extends Event {
      constructor(type, init = {}) {
        super(type);
        this.detail = init.detail;
      }
    };
    globalThis.localStorage = {
      store: new Map(),
      setItem(k, v) { this.store.set(k, String(v)); },
      getItem(k) { return this.store.get(k) ?? null; },
    };
  }
}

test("syncModule2AfterTelemetryChange clears cache and emits telemetry event", async () => {
  ensureBrowserGlobals();
  const events = [];
  const handler = (e) => events.push(e);
  window.addEventListener(TELEMETRY_ACTIVITY_EVENT, handler);

  let cacheCleared = false;
  const mod = await import("../api/module2.js");
  const original = mod.clearModule2Cache;
  mod.clearModule2Cache = () => {
    cacheCleared = true;
  };

  try {
    syncModule2AfterTelemetryChange("test-source", { action: "save" });
    assert.equal(cacheCleared, true);
    assert.equal(events.length, 1);
    assert.equal(events[0].detail.source, "test-source");
    assert.equal(events[0].detail.action, "save");
  } finally {
    mod.clearModule2Cache = original;
    window.removeEventListener(TELEMETRY_ACTIVITY_EVENT, handler);
  }
});

test("syncModule2AfterContainmentChange clears cache and emits containment event", async () => {
  ensureBrowserGlobals();
  const events = [];
  const handler = (e) => events.push(e);
  window.addEventListener(CONTAINMENT_CHANGED_EVENT, handler);

  let cacheCleared = false;
  const mod = await import("../api/module2.js");
  const original = mod.clearModule2Cache;
  mod.clearModule2Cache = () => {
    cacheCleared = true;
  };

  try {
    syncModule2AfterContainmentChange("kill-switch-save");
    assert.equal(cacheCleared, true);
    assert.equal(events.length, 1);
    assert.equal(events[0].detail.source, "kill-switch-save");
  } finally {
    mod.clearModule2Cache = original;
    window.removeEventListener(CONTAINMENT_CHANGED_EVENT, handler);
  }
});

test("syncModule2AfterGatewayKeyChange emits containment and telemetry events", async () => {
  ensureBrowserGlobals();
  const telemetry = [];
  const containment = [];
  const onTelemetry = (e) => telemetry.push(e);
  const onContainment = (e) => containment.push(e);
  window.addEventListener(TELEMETRY_ACTIVITY_EVENT, onTelemetry);
  window.addEventListener(CONTAINMENT_CHANGED_EVENT, onContainment);

  let cacheCleared = false;
  const mod = await import("../api/module2.js");
  const original = mod.clearModule2Cache;
  mod.clearModule2Cache = () => {
    cacheCleared = true;
  };

  try {
    syncModule2AfterGatewayKeyChange("create", { prefix: "zs_test" });
    assert.equal(cacheCleared, true);
    assert.equal(containment.length, 1);
    assert.equal(telemetry.length, 1);
    assert.equal(containment[0].detail.source, "gateway-key-create");
    assert.equal(telemetry[0].detail.source, "gateway-key-create");
    assert.equal(telemetry[0].detail.prefix, "zs_test");
  } finally {
    mod.clearModule2Cache = original;
    window.removeEventListener(TELEMETRY_ACTIVITY_EVENT, onTelemetry);
    window.removeEventListener(CONTAINMENT_CHANGED_EVENT, onContainment);
  }
});
