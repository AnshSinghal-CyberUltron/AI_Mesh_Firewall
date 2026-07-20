#!/usr/bin/env node
/**
 * Dedicated-broker MCP sandbox isolation gate (Ralph loop: scripts/ralph/mcp_sandbox).
 *
 * Runs ENTIRELY against the ISOLATED broker on :8312 (own base net mcp_sandbox_bridge_2,
 * orgs epsilon/zeta) so it NEVER collides with the parallel session's :8311 broker
 * (alpha/beta). Per iteration it:
 *   1. bootstraps both org sandboxes (ensure -> wait sandbox-agent -> docker cp stdio stub)
 *   2. runs 2 parallel adversarial org workers (org_worker.mjs, 6 stdio MCP servers each,
 *      env-exfil probes against GATEWAY_INTERNAL_API_KEY / MCP_BROKER_INTERNAL_KEY / DATABASE_URL)
 *   3. hammers a concurrent cross-org leak probe (epsilon must never see zeta's ORG_ONLY_MARKER
 *      and vice-versa; neither may read broker secrets) — the sandbox-isolation assertion
 *
 * Exit 0 only if BOTH workers ok AND the cross-org leak probe is clean. Any leak / breakout
 * => exit 1 with a machine-readable runs/iso_gate.json the loop iteration can act on.
 *
 * Usage:  MCP_BROKER_URL=http://127.0.0.1:8312 node scripts/ralph/mcp_sandbox/run_iso_orgs.mjs
 *         node scripts/ralph/mcp_sandbox/run_iso_orgs.mjs --bootstrap-only
 */
import { spawn, execFile } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, "../../..");
const WORKER = path.join(REPO_ROOT, "tests/e2e/mcp_sandbox_adversarial/org_worker.mjs");
const STUB_HOST = path.join(REPO_ROOT, "services/mcp-broker/tests/fixtures/stdio_mcp_stub.py");
const STUB_CONTAINER = "/data/mcp-auth/stdio_mcp_stub.py";

const BROKER_URL = (process.env.MCP_BROKER_URL || "http://127.0.0.1:8312").replace(/\/$/, "");
const BROKER_KEY = process.env.MCP_BROKER_INTERNAL_KEY || "dev-mcp-broker-key-change-me";
const ORGS = (process.env.ISO_ORGS || "adv-org-epsilon,adv-org-zeta").split(",");
const HAMMER_ROUNDS = Number(process.env.LEAK_PROBE_HAMMER_ROUNDS || 12);
const STUB_PATH = STUB_CONTAINER;
const args = new Set(process.argv.slice(2));

function run(cmd, cmdArgs, env = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(cmd, cmdArgs, { cwd: REPO_ROOT, env: { ...process.env, ...env }, stdio: ["ignore", "pipe", "pipe"] });
    let out = "";
    child.stdout.on("data", (d) => { out += d; process.stdout.write(d); });
    child.stderr.on("data", (d) => { out += d; process.stderr.write(d); });
    child.on("close", (code) => (code === 0 ? resolve(out) : reject(new Error(`${cmd} exited ${code}\n${out}`))));
  });
}

async function brokerFetch(pathname, opts = {}, attempt = 0) {
  const maxAttempts = 5;
  try {
    const res = await fetch(`${BROKER_URL}${pathname}`, {
      ...opts,
      headers: { "Content-Type": "application/json", "X-MCP-Broker-Key": BROKER_KEY, ...(opts.headers || {}) },
      signal: AbortSignal.timeout(60000),
    });
    if ([429, 500, 502, 503].includes(res.status) && attempt < maxAttempts) {
      await new Promise((r) => setTimeout(r, 1000 * (attempt + 1)));
      return brokerFetch(pathname, opts, attempt + 1);
    }
    return res;
  } catch (e) {
    if (attempt < maxAttempts) {
      await new Promise((r) => setTimeout(r, 1000 * (attempt + 1)));
      return brokerFetch(pathname, opts, attempt + 1);
    }
    throw e;
  }
}

async function waitForAgent(cid, maxWaitMs = 120000) {
  const probe = ["exec", cid, "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9320/health', timeout=3)"];
  const deadline = Date.now() + maxWaitMs;
  while (Date.now() < deadline) {
    const ok = await new Promise((resolve) => execFile("docker", probe, (err) => resolve(!err)));
    if (ok) return;
    await new Promise((r) => setTimeout(r, 2000));
  }
  throw new Error(`sandbox-agent not healthy for ${cid.slice(0, 12)} within ${maxWaitMs}ms`);
}

async function ensureAndStub(org) {
  const res = await brokerFetch(`/v1/sandbox/${org}/ensure`, { method: "POST", body: JSON.stringify({ warm: true }) });
  if (!res.ok) throw new Error(`ensure ${org} failed: ${res.status} ${await res.text()}`);
  const body = await res.json();
  const cid = body.container_id;
  if (!cid) throw new Error(`no container_id for ${org}`);
  await waitForAgent(cid);
  await run("docker", ["cp", STUB_HOST, `${cid}:${STUB_CONTAINER}`]);
  console.log(`bootstrap ${org} -> ${cid.slice(0, 12)} (stub copied)`);
  return cid;
}

async function bootstrap() {
  const health = await brokerFetch("/health");
  const hb = await health.json();
  if (!hb.docker_ok) throw new Error("broker docker_ok=false");
  for (const org of ORGS) await ensureAndStub(org);
}

// ---- cross-org isolation probe (epsilon <-> zeta) ----
function serverConfig(org, slug, idx) {
  return { server_slug: slug, command: "python3", args: [STUB_PATH], env_vars: { ORG_SLUG: org, SERVER_IDX: String(idx), SERVER_TOKEN: `${org}:${slug}`, ORG_ONLY_MARKER: `marker-${org}` } };
}
async function rpc(org, cfg, method, params, id) {
  const res = await brokerFetch(`/v1/sandbox/${org}/stdio/rpc`, {
    method: "POST",
    body: JSON.stringify({ server_slug: cfg.server_slug, command: cfg.command, args: cfg.args || [], env: cfg.env_vars || {}, method, params: params ?? null, jsonrpc_id: id }),
  });
  if (!res.ok) throw new Error(`rpc ${org}/${method} failed: ${res.status} ${await res.text()}`);
  return res.json();
}
async function hammerOrg(org, round) {
  const cfg = serverConfig(org, "stub-1", 1);
  const list = await rpc(org, cfg, "tools/list", null, round * 100);
  const names = (list?.result?.tools || []).map((t) => t.name);
  if (!names.includes("echo")) throw new Error(`${org} round ${round}: tools/list missing echo`);
  const echo = await rpc(org, cfg, "tools/call", { name: "echo", arguments: { msg: `hammer-${org}-${round}` } }, round * 100 + 1);
  if (echo?.result?.content?.[0]?.text !== `hammer-${org}-${round}`) throw new Error(`${org} round ${round}: echo mismatch`);
}
async function probeForeignMarker(org, foreign) {
  const cfg = serverConfig(org, "leak-probe", 99);
  const res = await rpc(org, cfg, "tools/call", { name: "get_env", arguments: { key: "ORG_ONLY_MARKER" } }, `marker-${org}`);
  const text = res?.result?.content?.[0]?.text ?? "";
  if (text.includes(`marker-${foreign}`)) throw new Error(`ISOLATION BREACH: ${org} sees foreign marker from ${foreign}: ${text}`);
  if (!text.includes(`marker-${org}`)) throw new Error(`${org} missing own marker: ${text}`);
}
async function probeSecretKeys(org) {
  const cfg = serverConfig(org, "leak-probe", 99);
  for (const key of ["GATEWAY_INTERNAL_API_KEY", "MCP_BROKER_INTERNAL_KEY", "DATABASE_URL", "AWS_SECRET_ACCESS_KEY", "DJANGO_SECRET_KEY"]) {
    const res = await rpc(org, cfg, "tools/call", { name: "get_env", arguments: { key } }, `sec-${org}-${key}`);
    const text = res?.result?.content?.[0]?.text ?? "";
    if (text && text.length > 0) throw new Error(`SECRET LEAK: ${org} read ${key}=${text.slice(0, 12)}…`);
  }
}
async function crossOrgLeakProbe() {
  const [a, b] = ORGS;
  for (let round = 0; round < HAMMER_ROUNDS; round++) {
    await Promise.all([hammerOrg(a, round), hammerOrg(b, round)]);
    await probeForeignMarker(a, b);
    await probeForeignMarker(b, a);
    await probeSecretKeys(a);
    await probeSecretKeys(b);
  }
}

async function main() {
  fs.mkdirSync(path.join(REPO_ROOT, "runs"), { recursive: true });
  const report = { broker: BROKER_URL, orgs: ORGS, ok: false, steps: [], workers: {}, leak_probe: null, error: null };
  try {
    await bootstrap();
    report.steps.push("bootstrap");
    if (args.has("--bootstrap-only")) { report.ok = true; finish(report); return; }

    const results = await Promise.all(
      ORGS.map((org) =>
        run("node", [WORKER], { ORG_SLUG: org, MCP_BROKER_URL: BROKER_URL, MCP_BROKER_INTERNAL_KEY: BROKER_KEY, WORKER_REPORT: `runs/iso_worker_${org}.json` })
          .then(() => ({ org, ok: true }))
          .catch((e) => ({ org, ok: false, error: e.message }))
      )
    );
    for (const r of results) report.workers[r.org] = r;
    if (results.some((r) => !r.ok)) throw new Error(`worker failed: ${JSON.stringify(results.filter((r) => !r.ok))}`);
    report.steps.push("parallel-workers");

    await crossOrgLeakProbe();
    report.leak_probe = `clean-${HAMMER_ROUNDS}-rounds`;
    report.steps.push("cross-org-leak-clean");

    report.ok = true;
    console.log(`OK iso-gate ${ORGS.join("+")} ${report.steps.join(" → ")}`);
  } catch (e) {
    report.error = e.message;
    console.error(`FAIL iso-gate`, e.message);
  } finally {
    finish(report);
  }
}
function finish(report) {
  fs.writeFileSync(path.join(REPO_ROOT, "runs/iso_gate.json"), JSON.stringify(report, null, 2));
  process.exit(report.ok ? 0 : 1);
}
main();
