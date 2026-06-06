import test from "node:test";
import assert from "node:assert/strict";
import {
  getDedicatedGatewayFallbackUrl,
  isProductionFirewallHost,
  preferSameOriginGateway,
  probeSameOriginGatewayProxy,
  resolveBrowserGatewayBaseUrl,
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
