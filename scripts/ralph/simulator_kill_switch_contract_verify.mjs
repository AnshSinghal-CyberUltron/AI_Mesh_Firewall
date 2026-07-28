#!/usr/bin/env node
/**
 * Contract gate for Module 2 simulator kill-switch alignment (no Module 1 edits).
 *
 * Usage (host or frontend container):
 *   node scripts/ralph/simulator_kill_switch_contract_verify.mjs
 *   docker exec ai_mesh_firewall-frontend-1 node --test src/api/killSwitch.test.js
 *
 * This script prefers the frontend-mounted module path when present.
 */
import assert from "node:assert/strict";
import { access } from "node:fs/promises";
import { pathToFileURL } from "node:url";
import path from "node:path";

async function loadKillSwitchApi() {
  const candidates = [
    path.resolve("frontend/src/api/killSwitch.js"),
    path.resolve("src/api/killSwitch.js"),
    path.resolve("/app/src/api/killSwitch.js"),
  ];
  for (const candidate of candidates) {
    try {
      await access(candidate);
      return import(pathToFileURL(candidate).href);
    } catch {
      // try next
    }
  }
  throw new Error("Could not locate killSwitch.js");
}

const {
  buildCredentialKillSwitchPayload,
  describeContainmentSemantics,
  fetchActiveSimulatorContext,
  isGatewayEnforcedKillModel,
  validateKillSwitchTarget,
} = await loadKillSwitchApi();

const results = [];

function check(name, fn) {
  try {
    fn();
    results.push({ name, ok: true });
  } catch (err) {
    results.push({ name, ok: false, error: String(err?.message || err) });
  }
}

check("rejects legacy __credential__ payload", () => {
  assert.throws(
    () => buildCredentialKillSwitchPayload({
      modelName: "__credential__",
      apiKeyPrefix: "liveSim1",
    }),
    /not enforced/,
  );
});

check("builds exact model+prefix payload", () => {
  const p = buildCredentialKillSwitchPayload({
    modelName: "gpt-4o-mini",
    apiKeyPrefix: "liveSim1",
    reason: "contract",
  });
  assert.equal(p.model_name, "gpt-4o-mini");
  assert.equal(p.api_key_prefix, "liveSim1");
  assert.equal(isGatewayEnforcedKillModel(p.model_name), true);
});

check("refuses stale/inactive simulator targets", () => {
  const live = { prefix: "liveSim1", keyId: "1", name: "simulator" };
  assert.equal(
    validateKillSwitchTarget({
      row: { name: "simulator", prefix: "oldSim", is_active: false },
      activeSimulator: live,
    }).ok,
    false,
  );
  assert.equal(
    validateKillSwitchTarget({
      row: { name: "simulator", prefix: "liveSim1", is_active: true },
      activeSimulator: live,
    }).ok,
    true,
  );
});

check("Auth disable vs Kill Switch semantics", () => {
  const s = describeContainmentSemantics();
  assert.match(s.disableKey, /Auth \(HTTP 403\)/);
  assert.match(s.disableKey, /never reach Input Scan or Kill Switch/);
  assert.match(s.killSwitch, /Kill Switch pipeline stage \(HTTP 503\)/);
});

async function maybeLive() {
  if (process.env.LIVE !== "1" || !process.env.CONTROL_TOKEN) {
    results.push({ name: "live simulator-default (skipped)", ok: true, skipped: true });
    return;
  }
  const base = process.env.CONTROL_URL || "http://127.0.0.1:8100";
  const fetchWithAuth = async (pathName) => fetch(`${base}${pathName}`, {
    headers: { Authorization: `Bearer ${process.env.CONTROL_TOKEN}` },
  });
  const ctx = await fetchActiveSimulatorContext(fetchWithAuth);
  assert.ok(ctx?.prefix, "active simulator prefix required");
  results.push({
    name: "live simulator-default",
    ok: true,
    prefix: ctx.prefix,
    keyId: ctx.keyId,
  });
}

await maybeLive();

const failed = results.filter((r) => !r.ok);
console.log(JSON.stringify({
  contractPass: failed.length === 0,
  results,
}, null, 2));
process.exit(failed.length === 0 ? 0 : 1);
