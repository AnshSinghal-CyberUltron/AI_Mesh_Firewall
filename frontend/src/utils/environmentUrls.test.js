import test from "node:test";
import assert from "node:assert/strict";
import {
  getDedicatedGatewayFallbackUrl,
  isProductionFirewallHost,
  preferSameOriginGateway,
  probeSameOriginGatewayProxy,
  resolveBrowserGatewayBaseUrl,
  resolveWebSocketBaseUrl,
} from "./environmentUrls.js";

test("probeSameOriginGatewayProxy returns true when GET /gw-health returns status ok", async () => {
  const fetchFn = async () => ({
    ok: true,
    json: async () => ({ status: "ok" }),
  });
  const ok = await probeSameOriginGatewayProxy("https://aimeshfirewall.zeroshield.ai", fetchFn);
  assert.equal(ok, true);
});

test("probeSameOriginGatewayProxy returns false when GET /gw-health is not ok", async () => {
  const fetchFn = async () => ({
    ok: false,
    json: async () => ({ status: "error" }),
  });
  const ok = await probeSameOriginGatewayProxy("https://aimeshfirewall.zeroshield.ai", fetchFn);
  assert.equal(ok, false);
});

test("resolveBrowserGatewayBaseUrl uses same-origin on prod firewall host", () => {
  const original = globalThis.window;
  globalThis.window = {
    location: {
      hostname: "aimeshfirewall.zeroshield.ai",
      origin: "https://aimeshfirewall.zeroshield.ai",
      protocol: "https:",
    },
    localStorage: { getItem: () => "", setItem: () => {}, removeItem: () => {} },
  };
  try {
    assert.equal(isProductionFirewallHost(), true);
    assert.equal(preferSameOriginGateway(), true);
    assert.equal(resolveBrowserGatewayBaseUrl(), "https://aimeshfirewall.zeroshield.ai");
  } finally {
    globalThis.window = original;
  }
});

test("getDedicatedGatewayFallbackUrl resolves aimeshgateway on prod firewall host", () => {
  const original = globalThis.window;
  globalThis.window = {
    location: {
      hostname: "aimeshfirewall.zeroshield.ai",
      origin: "https://aimeshfirewall.zeroshield.ai",
      protocol: "https:",
    },
    localStorage: { getItem: () => "", setItem: () => {}, removeItem: () => {} },
  };
  try {
    assert.equal(isProductionFirewallHost(), true);
    assert.equal(getDedicatedGatewayFallbackUrl(), "https://aimeshgateway.zeroshield.ai");
  } finally {
    globalThis.window = original;
  }
});

test("probeSameOriginGatewayProxy returns false on network error", async () => {
  const fetchFn = async () => {
    throw new Error("network");
  };
  const ok = await probeSameOriginGatewayProxy("https://aimeshfirewall.zeroshield.ai", fetchFn);
  assert.equal(ok, false);
});

test("resolveWebSocketBaseUrl uses same-origin on localhost even when prod backend URL is baked in", () => {
  const original = globalThis.window;
  const originalEnv = import.meta.env;
  globalThis.window = {
    location: {
      hostname: "127.0.0.1",
      host: "127.0.0.1:8180",
      origin: "http://127.0.0.1:8180",
      protocol: "http:",
      port: "8180",
    },
    localStorage: { getItem: () => "", setItem: () => {}, removeItem: () => {} },
  };
  import.meta.env = {
    ...originalEnv,
    VITE_BACKEND_BASE_URL: "https://aimeshbackend.zeroshield.ai",
    VITE_WS_BASE_URL: "",
  };
  try {
    assert.equal(resolveWebSocketBaseUrl(), "ws://127.0.0.1:8180");
  } finally {
    globalThis.window = original;
    import.meta.env = originalEnv;
  }
});

test("resolveWebSocketBaseUrl uses same-origin wss on prod firewall host", () => {
  const original = globalThis.window;
  globalThis.window = {
    location: {
      hostname: "aimeshfirewall.zeroshield.ai",
      host: "aimeshfirewall.zeroshield.ai",
      origin: "https://aimeshfirewall.zeroshield.ai",
      protocol: "https:",
      port: "",
    },
    localStorage: { getItem: () => "", setItem: () => {}, removeItem: () => {} },
  };
  try {
    assert.equal(resolveWebSocketBaseUrl(), "wss://aimeshfirewall.zeroshield.ai");
  } finally {
    globalThis.window = original;
  }
});
