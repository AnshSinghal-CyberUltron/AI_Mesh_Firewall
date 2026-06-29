/**
 * Single-org adversarial worker — 5+ stdio MCP servers via gateway broker API.
 * Used by run_parallel_org_agents.mjs (2 workers in parallel).
 *
 * Env:
 *   ORG_SLUG=adv-org-alpha|adv-org-beta
 *   GATEWAY_URL=http://127.0.0.1:8300
 *   MCP_BROKER_URL=http://127.0.0.1:8311
 *   MCP_BROKER_INTERNAL_KEY=...
 *   CONTROL_TOKEN=... (optional; uses login if missing)
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ORG = process.env.ORG_SLUG || "adv-org-alpha";
const BROKER_URL = (process.env.MCP_BROKER_URL || "http://127.0.0.1:8311").replace(/\/$/, "");
const BROKER_KEY = process.env.MCP_BROKER_INTERNAL_KEY || "dev-mcp-broker-key-change-me";
const GATEWAY_URL = (process.env.GATEWAY_URL || "http://127.0.0.1:8300").replace(/\/$/, "");
const OUT = process.env.WORKER_REPORT || `runs/org_worker_${ORG}.json`;

const SERVERS = [
  { slug: "stub-1", idx: 1 },
  { slug: "stub-2", idx: 2 },
  { slug: "stub-3", idx: 3 },
  { slug: "stub-4", idx: 4 },
  { slug: "stub-5", idx: 5 },
  { slug: "stub-6", idx: 6 },
];

const STUB_PATH = "/data/mcp-auth/stdio_mcp_stub.py";

async function brokerFetch(pathname, opts = {}) {
  const res = await fetch(`${BROKER_URL}${pathname}`, {
    ...opts,
    headers: {
      "Content-Type": "application/json",
      "X-MCP-Broker-Key": BROKER_KEY,
      ...(opts.headers || {}),
    },
  });
  return res;
}

async function ensureSandbox() {
  const res = await brokerFetch(`/v1/sandbox/${ORG}/ensure`, {
    method: "POST",
    body: JSON.stringify({ warm: true }),
  });
  if (!res.ok) throw new Error(`ensure failed: ${res.status} ${await res.text()}`);
  return res.json();
}

async function rpc(serverConfig, method, params, id) {
  const res = await brokerFetch(`/v1/sandbox/${ORG}/stdio/rpc`, {
    method: "POST",
    body: JSON.stringify({
      server_slug: serverConfig.server_slug,
      command: serverConfig.command,
      args: serverConfig.args || [],
      env: serverConfig.env_vars || {},
      method,
      params: params ?? null,
      jsonrpc_id: id,
    }),
  });
  if (!res.ok) throw new Error(`rpc ${method} failed: ${res.status} ${await res.text()}`);
  return res.json();
}

function serverConfig(slug, idx) {
  return {
    server_slug: slug,
    command: "python3",
    args: [STUB_PATH],
    env_vars: {
      ORG_SLUG: ORG,
      SERVER_IDX: String(idx),
      SERVER_TOKEN: `${ORG}:${slug}`,
      ORG_ONLY_MARKER: `marker-${ORG}`,
    },
  };
}

async function adversarialProbes(cfg) {
  const probes = [];
  for (const key of ["GATEWAY_INTERNAL_API_KEY", "MCP_BROKER_INTERNAL_KEY", "DATABASE_URL"]) {
    const rpcRes = await rpc(cfg, "tools/call", { name: "get_env", arguments: { key } }, `env-${key}`);
    const text = rpcRes?.result?.content?.[0]?.text ?? rpcRes?.result?.content?.[0]?.text;
    probes.push({ key, leaked: Boolean(text && text.length > 0) });
  }
  return probes;
}

async function main() {
  fs.mkdirSync("runs", { recursive: true });
  const report = { org: ORG, ok: false, steps: [], servers: [], probes: [], error: null };

  try {
    const health = await brokerFetch("/health");
    if (!health.ok) throw new Error(`broker health ${health.status}`);
    const healthBody = await health.json();
    if (!healthBody.docker_ok) throw new Error("broker docker_ok=false");
    report.steps.push("broker-health");

    const ensure = await ensureSandbox();
    report.steps.push(`sandbox-${ensure.status}`);
    report.container_id = ensure.container_id?.slice(0, 12);

    for (const { slug, idx } of SERVERS) {
      const cfg = serverConfig(slug, idx);
      const list = await rpc(cfg, "tools/list", null, idx * 10);
      const names = (list?.result?.tools || []).map((t) => t.name);
      if (!names.includes("echo") || !names.includes("get_env")) {
        throw new Error(`${slug} tools/list missing echo/get_env: ${names.join(",")}`);
      }
      const echo = await rpc(
        cfg,
        "tools/call",
        { name: "echo", arguments: { msg: `${ORG}:${slug}` } },
        idx * 10 + 1
      );
      const echoed = echo?.result?.content?.[0]?.text;
      if (echoed !== `${ORG}:${slug}`) {
        throw new Error(`${slug} echo mismatch: ${echoed}`);
      }
      report.servers.push({ slug, tools: names.length, echo: echoed });
    }
    report.steps.push(`servers-${SERVERS.length}`);

    const probes = await adversarialProbes(serverConfig("stub-1", 1));
    const leaked = probes.filter((p) => p.leaked);
    if (leaked.length) throw new Error(`secret leak: ${JSON.stringify(leaked)}`);
    report.probes = probes;
    report.steps.push("adversarial-probes-ok");

    report.ok = true;
    console.log(`OK org_worker ${ORG}`, report.steps.join(" → "));
  } catch (e) {
    report.error = e.message;
    console.error(`FAIL org_worker ${ORG}`, e.message);
  } finally {
    fs.writeFileSync(OUT, JSON.stringify(report, null, 2));
    process.exit(report.ok ? 0 : 1);
  }
}

main();
