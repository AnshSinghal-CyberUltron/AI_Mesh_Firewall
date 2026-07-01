import test from "node:test";
import assert from "node:assert/strict";
import {
  buildCredentialKillSwitchPayload,
  buildAnalystKillSwitchReason,
  createKillSwitchApi,
  filterKillSwitchesForPrefix,
  findKillSwitchForPayload,
} from "./killSwitch.js";

test("buildCredentialKillSwitchPayload defaults to credential-wide scope", () => {
  const payload = buildCredentialKillSwitchPayload({
    modelName: "gpt-4o",
    apiKeyPrefix: "abc12345",
    reason: "UEBA high risk",
  });
  assert.equal(payload.model_name, "__credential__");
  assert.equal(payload.api_key_prefix, "abc12345");
  assert.equal(payload.action, "disable");
  assert.equal(payload.reason, "UEBA high risk");
});

test("buildCredentialKillSwitchPayload can target a single model", () => {
  const payload = buildCredentialKillSwitchPayload({
    modelName: "gpt-4o",
    apiKeyPrefix: "abc12345",
    reason: "UEBA high risk",
    credentialWide: false,
  });
  assert.equal(payload.model_name, "gpt-4o");
});

test("buildAnalystKillSwitchReason includes band and metrics", () => {
  const reason = buildAnalystKillSwitchReason({
    risk_band: "high",
    risk_score: 0.82,
    block_rate_pct: 75,
    velocity_spike: 3.2,
  });
  assert.match(reason, /high/);
  assert.match(reason, /0\.82/);
  assert.match(reason, /75%/);
});

test("filterKillSwitchesForPrefix matches credential scope only", () => {
  const rows = filterKillSwitchesForPrefix(
    [
      { id: 1, api_key_prefix: "abc", model_name: "gpt-4o", is_active: true },
      { id: 2, api_key_prefix: "", model_name: "gpt-4o", is_active: true },
      { id: 3, api_key_prefix: "abc", model_name: "gpt-3.5", is_active: false },
    ],
    "abc",
  );
  assert.equal(rows.length, 2);
});

test("createKillSwitchApi createAndActivateKillSwitch posts then activates", async () => {
  const calls = [];
  const fetchWithAuth = async (url, opts = {}) => {
    calls.push({ url, method: opts.method || "GET", body: opts.body });
    if (url === "/api/kill-switches/") {
      if ((opts.method || "GET") === "GET" || !opts.method) {
        return { ok: true, json: async () => ([]) };
      }
      return { ok: true, json: async () => ({ id: 99, model_name: "gpt-4o" }) };
    }
    if (url === "/api/kill-switches/99/activate/") {
      return { ok: true, json: async () => ({ id: 99, is_active: true }) };
    }
    return { ok: false, json: async () => ({ detail: "unexpected" }) };
  };
  const api = createKillSwitchApi(fetchWithAuth);
  const created = await api.createAndActivateKillSwitch(
    buildCredentialKillSwitchPayload({
      modelName: "gpt-4o",
      apiKeyPrefix: "abc12345",
      reason: "test",
    }),
  );
  assert.equal(created.id, 99);
  assert.equal(calls.length, 3);
  assert.equal(calls[0].url, "/api/kill-switches/");
  assert.equal(calls[1].method, "POST");
  assert.equal(calls[2].method, "POST");
});

test("createAndActivateKillSwitch reactivates existing credential scope instead of creating duplicate", async () => {
  const calls = [];
  const payload = buildCredentialKillSwitchPayload({
    apiKeyPrefix: "abc12345",
    reason: "reactivate",
  });
  const fetchWithAuth = async (url, opts = {}) => {
    calls.push({ url, method: opts.method || "GET", body: opts.body });
    if (url === "/api/kill-switches/") {
      return { ok: true, json: async () => ([{ id: 7, model_name: "__credential__", api_key_prefix: "abc12345", is_active: false }]) };
    }
    if (url === "/api/kill-switches/7/activate/") {
      return { ok: true, json: async () => ({ id: 7, is_active: true }) };
    }
    return { ok: false, json: async () => ({ detail: "unexpected" }) };
  };
  const api = createKillSwitchApi(fetchWithAuth);
  const activated = await api.createAndActivateKillSwitch(payload);
  assert.equal(activated.id, 7);
  assert.equal(calls.length, 2);
  assert.equal(calls[0].url, "/api/kill-switches/");
  assert.equal(calls[1].url, "/api/kill-switches/7/activate/");
});

test("findKillSwitchForPayload matches credential scope", () => {
  const payload = buildCredentialKillSwitchPayload({ apiKeyPrefix: "abc12345" });
  const match = findKillSwitchForPayload(
    [{ id: 1, model_name: "__credential__", api_key_prefix: "abc12345" }],
    payload,
  );
  assert.equal(match?.id, 1);
});

test("createAndActivateKillSwitch rolls back created switch when activate fails", async () => {
  const calls = [];
  const fetchWithAuth = async (url, opts = {}) => {
    calls.push({ url, method: opts.method || "GET", body: opts.body });
    if (url === "/api/kill-switches/") {
      if ((opts.method || "GET") === "GET" || !opts.method) {
        return { ok: true, json: async () => ([]) };
      }
      return { ok: true, json: async () => ({ id: 42, model_name: "gpt-4o" }) };
    }
    if (url === "/api/kill-switches/42/activate/") {
      return { ok: false, status: 500, json: async () => ({ detail: "activation failed" }) };
    }
    if (url === "/api/kill-switches/42/") {
      return { ok: true, status: 204, json: async () => ({}) };
    }
    return { ok: false, json: async () => ({ detail: "unexpected" }) };
  };

  const api = createKillSwitchApi(fetchWithAuth);
  await assert.rejects(
    () => api.createAndActivateKillSwitch(buildCredentialKillSwitchPayload({
      modelName: "gpt-4o",
      apiKeyPrefix: "abc12345",
      reason: "test",
    })),
    /activation failed/,
  );
  assert.equal(calls.length, 4);
  assert.deepEqual(
    calls.map((c) => `${c.method} ${c.url}`),
    [
      "GET /api/kill-switches/",
      "POST /api/kill-switches/",
      "POST /api/kill-switches/42/activate/",
      "DELETE /api/kill-switches/42/",
    ],
  );
});
