import test from "node:test";
import assert from "node:assert/strict";
import {
  chatGatewayBases,
  isModelNotConfiguredResponse,
  rewriteChatBodyModelAuto,
  fetchGatewayPath,
} from "./gatewayChatFetch.js";

test("isModelNotConfiguredResponse matches OpenAI-shaped 404", () => {
  assert.equal(
    isModelNotConfiguredResponse(404, {
      error: { message: "Model 'gpt-4o-mini' is not configured", code: "model_not_configured" },
    }),
    true,
  );
  assert.equal(isModelNotConfiguredResponse(404, { code: "model_not_configured" }), true);
  assert.equal(isModelNotConfiguredResponse(401, { error: { code: "unauthorized" } }), false);
  assert.equal(isModelNotConfiguredResponse(404, { error: { code: "not_found" } }), false);
});

test("rewriteChatBodyModelAuto pins model auto but keeps the dropdown preferred_model", () => {
  const out = JSON.parse(
    rewriteChatBodyModelAuto(
      JSON.stringify({
        model: "Haiku",
        messages: [{ role: "user", content: "hi" }],
        routing_preferences: { enable_routing: false, preferred_model: "Haiku" },
      }),
    ),
  );
  assert.equal(out.model, "auto");
  assert.equal(out.routing_preferences.enable_routing, true);
  assert.equal(out.routing_preferences.preferred_model, "Haiku");
});

test("chatGatewayBases skips Django control-plane URLs", () => {
  const original = globalThis.window;
  globalThis.window = {
    location: {
      hostname: "127.0.0.1",
      origin: "http://127.0.0.1:8180",
      protocol: "http:",
      port: "8180",
    },
  };
  try {
    const bases = chatGatewayBases("https://aimeshbackend.zeroshield.ai");
    assert.equal(bases.includes("https://aimeshbackend.zeroshield.ai"), false);
    assert.equal(bases[0], "");
    assert.equal(bases.includes("http://127.0.0.1:8180"), true);
  } finally {
    globalThis.window = original;
  }
});

test("fetchGatewayPath retries model_not_configured with model=auto", async () => {
  const calls = [];
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url, opts) => {
    const body = JSON.parse(opts.body);
    calls.push({
      url,
      model: body.model,
      preferred: body.routing_preferences?.preferred_model,
    });
    if (body.model !== "auto") {
      return {
        ok: false,
        status: 404,
        headers: new Headers({ "content-type": "application/json" }),
        text: async () =>
          JSON.stringify({
            error: { message: "not configured", code: "model_not_configured" },
          }),
      };
    }
    return {
      ok: true,
      status: 200,
      headers: new Headers({ "content-type": "application/json" }),
      text: async () => JSON.stringify({ id: "ok", model: "auto" }),
    };
  };
  try {
    const result = await fetchGatewayPath({
      gatewayUrl: "http://127.0.0.1:8300",
      gatewayKey: "k",
      path: "/v1/chat/completions",
      opts: {
        method: "POST",
        body: JSON.stringify({
          model: "gpt-4o-mini",
          messages: [],
          routing_preferences: { enable_routing: true, preferred_model: "gpt-4o-mini" },
        }),
      },
    });
    assert.equal(result.ok, true);
    assert.equal(result.status, 200);
    assert.equal(calls.length >= 2, true);
    assert.equal(calls[0].model, "gpt-4o-mini");
    assert.equal(calls[calls.length - 1].model, "auto");
    assert.equal(calls[calls.length - 1].preferred, "gpt-4o-mini");
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("fetchGatewayPath hops off HTML 404 onto the next gateway base", async () => {
  const calls = [];
  const originalFetch = globalThis.fetch;
  const originalWindow = globalThis.window;
  globalThis.window = {
    location: {
      hostname: "demo.example.com",
      origin: "https://demo.example.com",
      protocol: "https:",
      port: "",
    },
  };
  globalThis.fetch = async (url) => {
    calls.push(String(url));
    if (String(url).includes("stale.example")) {
      return {
        ok: false,
        status: 404,
        headers: new Headers({ "content-type": "text/html" }),
        text: async () => "<html><title>Page not found</title></html>",
      };
    }
    return {
      ok: true,
      status: 200,
      headers: new Headers({ "content-type": "application/json" }),
      text: async () => JSON.stringify({ id: "ok" }),
    };
  };
  try {
    const result = await fetchGatewayPath({
      gatewayUrl: "http://stale.example:9999",
      gatewayKey: "k",
      path: "/v1/chat/completions",
      opts: { method: "POST", body: JSON.stringify({ model: "gpt-5.2", messages: [] }) },
    });
    assert.equal(result.ok, true);
    assert.equal(calls[0], "http://stale.example:9999/v1/chat/completions");
    assert.equal(calls.some((u) => u.startsWith("https://demo.example.com/")), true);
  } finally {
    globalThis.fetch = originalFetch;
    globalThis.window = originalWindow;
  }
});

test("fetchGatewayPath does not hop a JSON LiteLLM 404 onto the next gateway host", async () => {
  const calls = [];
  const originalFetch = globalThis.fetch;
  const originalWindow = globalThis.window;
  globalThis.window = {
    location: {
      hostname: "aimeshfirewall.zeroshield.ai",
      origin: "https://aimeshfirewall.zeroshield.ai",
      protocol: "https:",
      port: "",
    },
  };
  globalThis.fetch = async (url) => {
    calls.push(String(url));
    return {
      ok: false,
      status: 404,
      headers: new Headers({ "content-type": "application/json" }),
      text: async () =>
        JSON.stringify({
          error: {
            message: "The inference provider is temporarily unavailable. Please try again.",
            type: "upstream_error",
            code: 404,
          },
        }),
    };
  };
  try {
    const result = await fetchGatewayPath({
      gatewayUrl: "https://aimeshgateway.zeroshield.ai",
      gatewayKey: "k",
      path: "/v1/chat/completions",
      opts: {
        method: "POST",
        body: JSON.stringify({ model: "Haiku", messages: [] }),
      },
    });
    assert.equal(result.status, 404);
    assert.equal(calls.length, 1, `expected one host, got ${calls.join(" | ")}`);
  } finally {
    globalThis.fetch = originalFetch;
    globalThis.window = originalWindow;
  }
});

test("fetchGatewayPath does not rewrite isolation 503 onto model=auto", async () => {
  const calls = [];
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url, opts) => {
    const body = JSON.parse(opts.body);
    calls.push({ url, model: body.model });
    return {
      ok: false,
      status: 503,
      headers: new Headers({ "content-type": "application/json" }),
      text: async () =>
        JSON.stringify({
          error: "service_unavailable",
          code: "isolation_target_uncallable",
          message: "Isolation fallback is not callable.",
        }),
    };
  };
  try {
    const result = await fetchGatewayPath({
      gatewayUrl: "http://127.0.0.1:8300",
      gatewayKey: "k",
      path: "/v1/chat/completions",
      opts: {
        method: "POST",
        body: JSON.stringify({ model: "mistral-nemo-cheap", messages: [] }),
      },
    });
    assert.equal(result.status, 503);
    assert.equal(result.data?.code, "isolation_target_uncallable");
    assert.equal(calls.length, 1);
    assert.equal(calls[0].model, "mistral-nemo-cheap");
  } finally {
    globalThis.fetch = originalFetch;
  }
});
