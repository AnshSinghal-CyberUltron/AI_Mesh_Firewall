/**
 * Concurrent cross-org leakage probe — runs alongside org_worker hammering.
 * Writes per-org volume markers and verifies sibling org cannot read them via RPC/env.
 *
 * Env: MCP_BROKER_URL, MCP_BROKER_INTERNAL_KEY
 */
import fs from "node:fs";

const BROKER_URL = (process.env.MCP_BROKER_URL || "http://127.0.0.1:8311").replace(/\/$/, "");
const BROKER_KEY = process.env.MCP_BROKER_INTERNAL_KEY || "dev-mcp-broker-key-change-me";
const ORG_ALPHA = "adv-org-alpha";
const ORG_BETA = "adv-org-beta";
const OUT = process.env.LEAK_PROBE_REPORT || "runs/cross_org_leak_probe.json";
const STUB_PATH = "/data/mcp-auth/stdio_mcp_stub.py";
const HAMMER_ROUNDS = Number(process.env.LEAK_PROBE_HAMMER_ROUNDS || 12);

async function brokerFetch(pathname, opts = {}, attempt = 0) {
  const maxAttempts = 4;
  const backoffMs = 1000 * (attempt + 1);
  try {
    const res = await fetch(`${BROKER_URL}${pathname}`, {
      ...opts,
      headers: {
        "Content-Type": "application/json",
        "X-MCP-Broker-Key": BROKER_KEY,
        ...(opts.headers || {}),
      },
      signal: AbortSignal.timeout(60000),
    });
    if ((res.status === 429 || res.status === 503 || res.status === 502 || res.status === 500) && attempt < maxAttempts) {
      await new Promise((r) => setTimeout(r, backoffMs));
      return brokerFetch(pathname, opts, attempt + 1);
    }
    return res;
  } catch (e) {
    if (attempt < maxAttempts) {
      await new Promise((r) => setTimeout(r, backoffMs));
      return brokerFetch(pathname, opts, attempt + 1);
    }
    throw e;
  }
}

function serverConfig(org, slug, idx) {
  return {
    server_slug: slug,
    command: "python3",
    args: [STUB_PATH],
    env_vars: {
      ORG_SLUG: org,
      SERVER_IDX: String(idx),
      SERVER_TOKEN: `${org}:${slug}`,
      ORG_ONLY_MARKER: `marker-${org}`,
    },
  };
}

async function rpc(org, cfg, method, params, id) {
  const res = await brokerFetch(`/v1/sandbox/${org}/stdio/rpc`, {
    method: "POST",
    body: JSON.stringify({
      server_slug: cfg.server_slug,
      command: cfg.command,
      args: cfg.args || [],
      env: cfg.env_vars || {},
      method,
      params: params ?? null,
      jsonrpc_id: id,
    }),
  });
  if (!res.ok) throw new Error(`rpc ${org}/${method} failed: ${res.status} ${await res.text()}`);
  return res.json();
}

async function hammerOrg(org, cfg, round) {
  const list = await rpc(org, cfg, "tools/list", null, round * 100);
  const names = (list?.result?.tools || []).map((t) => t.name);
  if (!names.includes("echo")) throw new Error(`${org} round ${round}: tools/list missing echo`);
  const echo = await rpc(
    org,
    cfg,
    "tools/call",
    { name: "echo", arguments: { msg: `hammer-${org}-${round}` } },
    round * 100 + 1
  );
  const text = echo?.result?.content?.[0]?.text;
  if (text !== `hammer-${org}-${round}`) {
    throw new Error(`${org} round ${round}: echo mismatch ${text}`);
  }
}

async function probeForeignMarker(org, foreignOrg) {
  const cfg = serverConfig(org, "leak-probe", 99);
  const res = await rpc(org, cfg, "tools/call", {
    name: "get_env",
    arguments: { key: "ORG_ONLY_MARKER" },
  }, `marker-${org}`);
  const text = res?.result?.content?.[0]?.text ?? "";
  if (text.includes(`marker-${foreignOrg}`)) {
    throw new Error(`${org} sees foreign marker from ${foreignOrg}: ${text}`);
  }
  if (!text.includes(`marker-${org}`)) {
    throw new Error(`${org} missing own marker: ${text}`);
  }
}

async function probeSecretKeys(org) {
  const cfg = serverConfig(org, "leak-probe", 99);
  for (const key of ["GATEWAY_INTERNAL_API_KEY", "MCP_BROKER_INTERNAL_KEY", "DATABASE_URL"]) {
    const res = await rpc(org, cfg, "tools/call", { name: "get_env", arguments: { key } }, `sec-${org}-${key}`);
    const text = res?.result?.content?.[0]?.text ?? "";
    if (text && text.length > 0) {
      throw new Error(`${org} leaked secret ${key}`);
    }
  }
}

async function main() {
  fs.mkdirSync("runs", { recursive: true });
  const report = { ok: false, steps: [], hammer_rounds: HAMMER_ROUNDS, error: null };

  try {
    const alphaCfg = serverConfig(ORG_ALPHA, "stub-1", 1);
    const betaCfg = serverConfig(ORG_BETA, "stub-1", 1);

    for (let round = 0; round < HAMMER_ROUNDS; round++) {
      await Promise.all([
        hammerOrg(ORG_ALPHA, alphaCfg, round),
        hammerOrg(ORG_BETA, betaCfg, round),
      ]);
      await probeForeignMarker(ORG_ALPHA, ORG_BETA);
      await probeForeignMarker(ORG_BETA, ORG_ALPHA);
      await probeSecretKeys(ORG_ALPHA);
      await probeSecretKeys(ORG_BETA);
    }
    report.steps.push(`hammer-${HAMMER_ROUNDS}-rounds`);
    report.steps.push("cross-org-markers-ok");
    report.steps.push("secret-denylist-ok");
    report.ok = true;
    console.log(`OK cross_org_leak_probe ${report.steps.join(" → ")}`);
  } catch (e) {
    report.error = e.message;
    console.error("FAIL cross_org_leak_probe", e.message);
  } finally {
    fs.writeFileSync(OUT, JSON.stringify(report, null, 2));
    process.exit(report.ok ? 0 : 1);
  }
}

main();
