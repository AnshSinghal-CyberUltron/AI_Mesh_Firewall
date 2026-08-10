import test from "node:test";
import assert from "node:assert/strict";
import {
  buildCredentialKillSwitchPayload,
  buildAnalystKillSwitchReason,
  createKillSwitchApi,
  deriveAllowedModelsForKey,
  describeContainmentSemantics,
  fetchActiveSimulatorContext,
  filterEnforcedKillSwitches,
  filterKillSwitchesForPrefix,
  findKillSwitchForPayload,
  isGatewayEnforcedKillModel,
  isSimulatorKeyRow,
  mergeKillSwitchModelCandidates,
  resolveTopModelName,
  validateKillSwitchTarget,
} from "./module2KillSwitch.js";

test("buildCredentialKillSwitchPayload requires model_name (no __credential__)", () => {
  const payload = buildCredentialKillSwitchPayload({
    modelName: "gpt-4o",
    apiKeyPrefix: "abc12345",
    reason: "UEBA high risk",
  });
  assert.equal(payload.model_name, "gpt-4o");
  assert.equal(payload.api_key_prefix, "abc12345");
  assert.equal(payload.action, "disable");
  assert.equal(payload.reason, "UEBA high risk");
});

test("buildCredentialKillSwitchPayload rejects legacy __credential__ scope", () => {
  assert.throws(
    () => buildCredentialKillSwitchPayload({
      modelName: "__credential__",
      apiKeyPrefix: "abc12345",
      reason: "legacy",
    }),
    /not enforced by the gateway/,
  );
});

test("isGatewayEnforcedKillModel rejects legacy credential scope", () => {
  assert.equal(isGatewayEnforcedKillModel("__credential__"), false);
  assert.equal(isGatewayEnforcedKillModel("gpt-4o"), true);
  assert.equal(filterEnforcedKillSwitches([
    { id: 1, model_name: "__credential__", is_active: true },
    { id: 2, model_name: "gpt-4o", is_active: true },
  ]).map((ks) => ks.id).join(","), "2");
});

test("mergeKillSwitchModelCandidates prefers simulator then catalog then telemetry", () => {
  const models = mergeKillSwitchModelCandidates({
    preferredModel: "north-mini",
    gatewayModelNames: ["gpt-4o", "north-mini"],
    telemetryRow: { top_models: [["served-model", 9]] },
  });
  assert.deepEqual(models, ["north-mini", "gpt-4o", "served-model"]);
});

test("buildCredentialKillSwitchPayload allows org-wide scope when prefix is empty", () => {
  const payload = buildCredentialKillSwitchPayload({
    modelName: "gpt-4o",
    apiKeyPrefix: "",
    reason: "org-wide containment",
  });
  assert.equal(payload.model_name, "gpt-4o");
  assert.equal(payload.api_key_prefix, undefined);
  assert.equal(payload.action, "disable");
});

test("buildCredentialKillSwitchPayload requires fallback_model for reroute", () => {
  assert.throws(
    () => buildCredentialKillSwitchPayload({ modelName: "gpt-4o", action: "reroute" }),
    /fallback_model is required/,
  );
});

test("buildCredentialKillSwitchPayload includes reroute fallback_model", () => {
  const payload = buildCredentialKillSwitchPayload({
    modelName: "gpt-4o",
    apiKeyPrefix: "abc12345",
    action: "reroute",
    fallbackModel: "gpt-4.1-mini",
    reason: "fallback test",
  });
  assert.equal(payload.action, "reroute");
  assert.equal(payload.fallback_model, "gpt-4.1-mini");
  assert.equal(payload.api_key_prefix, "abc12345");
});

test("buildCredentialKillSwitchPayload throws without model_name", () => {
  assert.throws(
    () => buildCredentialKillSwitchPayload({ apiKeyPrefix: "abc12345", reason: "x" }),
    /model_name is required/,
  );
});

test("resolveTopModelName prefers top_models tuples", () => {
  assert.equal(resolveTopModelName({ top_models: [["gpt-4o", 3], ["mini", 1]] }), "gpt-4o");
  assert.equal(resolveTopModelName({ models: ["z", "a"] }), "a");
});

test("deriveAllowedModelsForKey preserves key-scoped model ordering and uniqueness", () => {
  const models = deriveAllowedModelsForKey({
    top_models: [["gpt-4o", 3], ["gpt-4.1-mini", 1]],
    models: ["gpt-4.1-mini", "gpt-4o-mini"],
    recent_requests: [{ model: "gpt-4o" }, { model: "gpt-4o-mini" }],
    model: "gpt-5-mini",
  });
  assert.deepEqual(models, ["gpt-4o", "gpt-4.1-mini", "gpt-4o-mini", "gpt-5-mini"]);
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

test("createAndActivateKillSwitch reactivates existing per-model switch instead of creating duplicate", async () => {
  const calls = [];
  const payload = buildCredentialKillSwitchPayload({
    modelName: "gpt-4o",
    apiKeyPrefix: "abc12345",
    reason: "reactivate",
  });
  const fetchWithAuth = async (url, opts = {}) => {
    calls.push({ url, method: opts.method || "GET", body: opts.body });
    if (url === "/api/kill-switches/") {
      return { ok: true, json: async () => ([{ id: 7, model_name: "gpt-4o", api_key_prefix: "abc12345", is_active: false }]) };
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

test("findKillSwitchForPayload matches per-model scope", () => {
  const payload = buildCredentialKillSwitchPayload({
    modelName: "gpt-4o",
    apiKeyPrefix: "abc12345",
  });
  const match = findKillSwitchForPayload(
    [{ id: 1, model_name: "gpt-4o", api_key_prefix: "abc12345" }],
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

test("setGatewayKeyActive PATCHes is_active", async () => {
  const calls = [];
  const fetchWithAuth = async (url, opts = {}) => {
    calls.push({ url, method: opts.method || "GET", body: opts.body });
    return { ok: true, json: async () => ({ id: "k1", is_active: false }) };
  };
  const api = createKillSwitchApi(fetchWithAuth);
  const data = await api.setGatewayKeyActive("k1", false);
  assert.equal(data.is_active, false);
  assert.equal(calls[0].url, "/api/gateways/keys/k1/");
  assert.equal(calls[0].method, "PATCH");
  assert.equal(JSON.parse(calls[0].body).is_active, false);
});

test("deactivateKillSwitch posts deactivate endpoint", async () => {
  const calls = [];
  const fetchWithAuth = async (url, opts = {}) => {
    calls.push({ url, method: opts.method || "GET", body: opts.body });
    return { ok: true, json: async () => ({ id: 77, is_active: false }) };
  };
  const api = createKillSwitchApi(fetchWithAuth);
  await api.deactivateKillSwitch(77);
  assert.equal(calls[0].url, "/api/kill-switches/77/deactivate/");
  assert.equal(calls[0].method, "POST");
});

test("validateKillSwitchTarget refuses inactive and stale simulator prefixes", () => {
  const live = { prefix: "liveSim1", keyId: "42", name: "simulator" };
  assert.equal(isSimulatorKeyRow({ name: "simulator", prefix: "oldSim99" }, live), true);
  const inactive = validateKillSwitchTarget({
    row: { name: "simulator", prefix: "oldSim99", is_active: false },
    activeSimulator: live,
    requireActiveKey: true,
  });
  assert.equal(inactive.ok, false);
  assert.match(inactive.error, /liveSim1/);

  const staleActive = validateKillSwitchTarget({
    row: { name: "simulator", prefix: "oldSim99", is_active: true },
    activeSimulator: live,
    requireActiveKey: true,
  });
  assert.equal(staleActive.ok, false);
  assert.match(staleActive.error, /Stale simulator/);

  const okLive = validateKillSwitchTarget({
    row: { name: "simulator", prefix: "liveSim1", is_active: true },
    activeSimulator: live,
    requireActiveKey: true,
  });
  assert.equal(okLive.ok, true);

  const semantics = describeContainmentSemantics();
  assert.match(semantics.killSwitch, /Kill Switch pipeline stage/);
  assert.match(semantics.disableKey, /Auth \(HTTP 403\)/);
});

test("fetchActiveSimulatorContext reads Module 1 simulator-default", async () => {
  const fetchWithAuth = async (url) => {
    assert.equal(url, "/api/gateways/simulator-default/");
    return {
      ok: true,
      json: async () => ({
        has_gateway_key: true,
        prefix: "abcDEF12",
        key_id: "99",
        name: "simulator",
      }),
    };
  };
  const ctx = await fetchActiveSimulatorContext(fetchWithAuth);
  assert.equal(ctx.prefix, "abcDEF12");
  assert.equal(ctx.keyId, "99");
});
