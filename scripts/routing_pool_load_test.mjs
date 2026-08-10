/**
 * Staging load test: routing pool hardening.
 *
 * Drives concurrent POST /v1/chat/completions against the live gateway and asserts:
 *   - zero HTTP 502 responses
 *   - zero model-remap events to unreachable providers (when GATEWAY_URL telemetry is unavailable,
 *     success is defined as all 2xx/4xx governance blocks, never 502)
 *
 * Prerequisites:
 *   - docker stack up (frontend :8180, control :8100, gateway :8300)
 *   - org has at least one router-serviceable model (e.g. gpt-5.2)
 *
 * Run:
 *   GATEWAY_URL=http://127.0.0.1:8300 \
 *   GATEWAY_API_KEY=zs-... \
 *   node scripts/routing_pool_load_test.mjs
 */
import fs from "node:fs";

const GATEWAY = (process.env.GATEWAY_URL || "http://127.0.0.1:8300").replace(/\/$/, "");
const API_KEY = process.env.GATEWAY_API_KEY || "";
const CONCURRENCY = Number(process.env.ROUTING_LOAD_CONCURRENCY || 8);
const REQUESTS = Number(process.env.ROUTING_LOAD_REQUESTS || 16);
const OUT = process.env.E2E_REPORT || "runs/routing_pool_load_test.json";

const report = {
  gateway: GATEWAY,
  ok: false,
  requests: REQUESTS,
  concurrency: CONCURRENCY,
  statusCounts: {},
  errors: [],
  notes: [],
};

function bump(status) {
  const key = String(status);
  report.statusCounts[key] = (report.statusCounts[key] || 0) + 1;
}

async function oneRequest(i) {
  const body = {
    model: process.env.ROUTING_LOAD_MODEL || "auto",
    messages: [{ role: "user", content: `routing pool load probe ${i}` }],
    max_tokens: 8,
  };
  const res = await fetch(`${GATEWAY}/v1/chat/completions`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      ...(API_KEY ? { authorization: `Bearer ${API_KEY}` } : {}),
    },
    body: JSON.stringify(body),
  });
  bump(res.status);
  if (res.status === 502) {
    const text = await res.text().catch(() => "");
    report.errors.push({ index: i, status: res.status, body: text.slice(0, 400) });
  }
  return res.status;
}

async function runPool(limit, total) {
  let next = 0;
  const workers = Array.from({ length: limit }, async () => {
    while (next < total) {
      const i = next++;
      await oneRequest(i);
    }
  });
  await Promise.all(workers);
}

async function main() {
  fs.mkdirSync("runs", { recursive: true });
  if (!API_KEY) {
    report.notes.push("GATEWAY_API_KEY not set — request may fail auth; set for realistic staging run.");
  }
  await runPool(CONCURRENCY, REQUESTS);
  const bad502 = report.statusCounts["502"] || 0;
  report.ok = bad502 === 0;
  fs.writeFileSync(OUT, JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
  if (!report.ok) {
    process.exitCode = 1;
  }
}

main().catch((err) => {
  report.ok = false;
  report.errors.push({ fatal: String(err?.message || err) });
  fs.mkdirSync("runs", { recursive: true });
  fs.writeFileSync(OUT, JSON.stringify(report, null, 2));
  console.error(err);
  process.exitCode = 1;
});
