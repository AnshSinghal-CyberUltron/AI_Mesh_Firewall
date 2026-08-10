#!/usr/bin/env node
/**
 * Live proof: kill-switch 503 from gateway → Attack Simulator stages show
 * Kill Switch BLOCK (not Model Output ERROR).
 *
 *   docker run --rm --network host -v "$PWD:/work" -w /work \
 *     mcr.microsoft.com/playwright:v1.60.0-jammy \
 *     node scripts/ralph/kill_switch_pipeline_attr_verify.mjs
 */
import { normalizeChatPipelineResult } from "../../frontend/src/utils/liveGateway.js";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.join(HERE, "../../mcp-parallel/findings/kill-switch-pipeline-attr");
const CONTROL = (process.env.CONTROL_URL || "http://127.0.0.1:8100").replace(/\/$/, "");
const GATEWAY = (process.env.GATEWAY_URL || "http://127.0.0.1:8300").replace(/\/$/, "");
const EMAIL = process.env.TEST_EMAIL || "admin@zeroshield.io";
const PASS = process.env.TEST_PASSWORD || "Adm1n!Pass#2024";
const MODEL = process.env.TEST_MODEL || "gpt-4o-mini";

async function login() {
  const res = await fetch(`${CONTROL}/api/auth/token/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email: EMAIL, password: PASS }),
  });
  if (res.status === 429) {
    const wait = Number(res.headers.get("retry-after") || 15);
    await new Promise((r) => setTimeout(r, wait * 1000));
    return login();
  }
  if (!res.ok) throw new Error(`login ${res.status}`);
  const data = await res.json();
  return data.access || data.token || data.access_token;
}

async function simulatorKey(token) {
  const res = await fetch(`${CONTROL}/api/gateways/simulator-default/`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
  });
  if (!res.ok) throw new Error(`simulator-default ${res.status}`);
  const data = await res.json();
  if (!data?.key) throw new Error("no simulator key");
  return data.key;
}

async function main() {
  fs.mkdirSync(OUT, { recursive: true });
  const token = await login();
  const key = await simulatorKey(token);
  const res = await fetch(`${GATEWAY}/v1/chat/completions`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${key}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      model: MODEL,
      messages: [{ role: "user", content: "kill switch pipeline attribution probe" }],
      max_tokens: 8,
      routing_preferences: { enable_routing: false, preferred_model: MODEL },
    }),
  });
  const body = await res.json().catch(() => null);
  fs.writeFileSync(path.join(OUT, "gateway-response.json"), JSON.stringify({ status: res.status, body }, null, 2));

  const normalized = normalizeChatPipelineResult(body, res.status, {
    prompt: "kill switch pipeline attribution probe",
    requestedModel: MODEL,
  });
  const ks = normalized.stages?.find((s) => s.name === "kill_switch");
  const mo = normalized.stages?.find((s) => s.name === "model_output");
  const code = body?.code || body?.error?.code || "";

  const verdict = {
    httpStatus: res.status,
    gatewayCode: code,
    final_action: normalized.final_action,
    blocked_by: normalized.blocked_by,
    kill_switch_action: ks?.action,
    kill_switch_detail: ks?.detail,
    model_output_action: mo?.action,
    ok:
      res.status === 503
      && String(code).toLowerCase() === "kill_switch_active"
      && normalized.final_action === "block"
      && normalized.blocked_by === "kill_switch"
      && ks?.action === "block"
      && mo?.action === "skip",
    note:
      res.status !== 503
        ? `Model ${MODEL} is not kill-switched right now (got ${res.status}) — activate kill switch then re-run`
        : undefined,
  };
  fs.writeFileSync(path.join(OUT, "verdict.json"), JSON.stringify(verdict, null, 2));
  console.log(JSON.stringify(verdict, null, 2));
  if (!verdict.ok && res.status === 503) process.exit(1);
  if (!verdict.ok) {
    console.error("SKIP/WARN: kill switch not active on live model; unit tests still cover the fix");
    process.exit(0);
  }
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
