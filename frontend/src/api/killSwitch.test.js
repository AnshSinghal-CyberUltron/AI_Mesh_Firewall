import test from "node:test";
import assert from "node:assert/strict";
import {
  buildCredentialKillSwitchPayload,
  buildUebaKillSwitchReason,
  createKillSwitchApi,
  filterKillSwitchesForPrefix,
} from "./killSwitch.js";

test("buildCredentialKillSwitchPayload shapes disable request", () => {
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

test("buildUebaKillSwitchReason includes band and metrics", () => {
  const reason = buildUebaKillSwitchReason({
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
  assert.equal(calls.length, 2);
  assert.equal(calls[1].method, "POST");
});
