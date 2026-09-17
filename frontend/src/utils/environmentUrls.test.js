import test from "node:test";
import assert from "node:assert/strict";
import {
  getDedicatedGatewayFallbackUrl,
  isControlPlaneGatewayUrl,
  isProductionFirewallHost,
  preferSameOriginGateway,
  probeSameOriginGatewayProxy,
  resolveBrowserGatewayBaseUrl,
  resolveGatewayBaseUrl,
  resolveMcpGatewayBaseUrl,
  toAbsoluteGatewayUrl,
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

test("resolveMcpGatewayBaseUrl uses the local gateway port on a local dev host", () => {
  const original = globalThis.window;
  globalThis.window = {
    location: {
      hostname: "localhost",
      origin: "http://localhost:8180",
      protocol: "http:",
    },
    localStorage: { getItem: () => "", setItem: () => {}, removeItem: () => {} },
  };
  try {
    // Regression guard for the MCP gateway URL mismatch bug: a local dev
    // browser must NEVER resolve to the production dedicated gateway host,
    // even if VITE_GATEWAY_BASE_URL is baked in as a prod value from a
    // shared .env (not settable here since import.meta.env is unavailable
    // under node:test, which conveniently also proves the local branch wins
    // unconditionally over any explicit env value).
    assert.equal(resolveMcpGatewayBaseUrl(), "http://localhost:8300");
  } finally {
    globalThis.window = original;
  }
});

test("resolveMcpGatewayBaseUrl never prefers same-origin on the prod firewall host (unlike resolveBrowserGatewayBaseUrl)", () => {
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
    // MCP config URLs must point at the real gateway host, not the firewall
    // UI's same-origin proxy — a pasted mcp.json entry has to work from an
    // external tool (VS Code / Cursor), not just from inside the browser.
    assert.notEqual(resolveMcpGatewayBaseUrl(), "https://aimeshfirewall.zeroshield.ai");
    assert.equal(resolveMcpGatewayBaseUrl(), resolveGatewayBaseUrl());
  } finally {
    globalThis.window = original;
  }
});

test("toAbsoluteGatewayUrl resolves a relative gateway_endpoint path against the local gateway on a dev host", () => {
  const original = globalThis.window;
  globalThis.window = {
    location: {
      hostname: "127.0.0.1",
      origin: "http://127.0.0.1:8180",
      protocol: "http:",
    },
    localStorage: { getItem: () => "", setItem: () => {}, removeItem: () => {} },
  };
  try {
    assert.equal(
      toAbsoluteGatewayUrl("/gateway/zeroshield/mcp/my-server"),
      "http://127.0.0.1:8300/gateway/zeroshield/mcp/my-server",
    );
  } finally {
    globalThis.window = original;
  }
});

test("toAbsoluteGatewayUrl passes an already-absolute URL through unchanged", () => {
  assert.equal(
    toAbsoluteGatewayUrl("https://aimeshgateway.zeroshield.ai/gateway/zeroshield/mcp/my-server"),
    "https://aimeshgateway.zeroshield.ai/gateway/zeroshield/mcp/my-server",
  );
});

test("toAbsoluteGatewayUrl returns empty string for an empty/undefined path", () => {
  assert.equal(toAbsoluteGatewayUrl(""), "");
  assert.equal(toAbsoluteGatewayUrl(undefined), "");
});

test("isControlPlaneGatewayUrl flags Django/backend hosts that 404 /v1", () => {
  assert.equal(isControlPlaneGatewayUrl("https://aimeshbackend.zeroshield.ai"), true);
  assert.equal(isControlPlaneGatewayUrl("http://127.0.0.1:8100"), true);
  assert.equal(isControlPlaneGatewayUrl("https://aimeshfirewall.zeroshield.ai"), false);
  assert.equal(isControlPlaneGatewayUrl("https://aimeshgateway.zeroshield.ai"), false);
});

test("resolveBrowserGatewayBaseUrl uses same-origin on Vite :8180", () => {
  const original = globalThis.window;
  globalThis.window = {
    location: {
      hostname: "127.0.0.1",
      origin: "http://127.0.0.1:8180",
      protocol: "http:",
      port: "8180",
    },
    localStorage: { getItem: () => "", setItem: () => {}, removeItem: () => {} },
  };
  try {
    assert.equal(preferSameOriginGateway(), true);
    assert.equal(resolveBrowserGatewayBaseUrl(), "http://127.0.0.1:8180");
  } finally {
    globalThis.window = original;
  }
});

test("resolveBrowserGatewayBaseUrl uses same-origin on GCP *.internal Vite UI", () => {
  const original = globalThis.window;
  globalThis.window = {
    location: {
      hostname: "ai-mesh-firewall.asia-south1-c.c.ai-mesh-firewall.internal",
      origin: "http://ai-mesh-firewall.asia-south1-c.c.ai-mesh-firewall.internal:8180",
      protocol: "http:",
      port: "8180",
    },
    localStorage: { getItem: () => "", setItem: () => {}, removeItem: () => {} },
  };
  try {
    assert.equal(preferSameOriginGateway(), true);
    assert.equal(
      resolveBrowserGatewayBaseUrl(),
      "http://ai-mesh-firewall.asia-south1-c.c.ai-mesh-firewall.internal:8180",
    );
  } finally {
    globalThis.window = original;
  }
});
