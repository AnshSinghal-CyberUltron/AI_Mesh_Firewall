#!/usr/bin/env node
/**
 * Ralph iteration gate — spawns 2 parallel org workers (adv-org-alpha, adv-org-beta).
 * Each worker ensures sandbox + 6 stdio MCP servers + adversarial env probes.
 *
 * Usage:
 *   node scripts/ralph/run_parallel_org_agents.mjs              # default parallel workers
 *   node scripts/ralph/run_parallel_org_agents.mjs --bootstrap-only
 *   node scripts/ralph/run_parallel_org_agents.mjs --full     # workers + pytest leakage subset
 */
import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, "../..");
const WORKER = path.join(REPO_ROOT, "tests/e2e/mcp_sandbox_adversarial/org_worker.mjs");
const LEAK_PROBE = path.join(REPO_ROOT, "tests/e2e/mcp_sandbox_adversarial/cross_org_leak_probe.mjs");
const STUB_HOST = path.join(
  REPO_ROOT,
  "services/mcp-broker/tests/fixtures/stdio_mcp_stub.py"
);
const STUB_CONTAINER = "/data/mcp-auth/stdio_mcp_stub.py";

const BROKER_URL = process.env.MCP_BROKER_URL || "http://127.0.0.1:8311";
const BROKER_KEY = process.env.MCP_BROKER_INTERNAL_KEY || "dev-mcp-broker-key-change-me";

const ORGS = ["adv-org-alpha", "adv-org-beta"];
const args = new Set(process.argv.slice(2));
const bootstrapOnly = args.has("--bootstrap-only");
const full = args.has("--full");

function run(cmd, cmdArgs, env = {}, cwd = REPO_ROOT) {
  return new Promise((resolve, reject) => {
    const child = spawn(cmd, cmdArgs, {
      cwd,
      env: { ...process.env, ...env },
      stdio: ["ignore", "pipe", "pipe"],
    });
    let out = "";
    child.stdout.on("data", (d) => {
      out += d;
      process.stdout.write(d);
    });
    child.stderr.on("data", (d) => {
      out += d;
      process.stderr.write(d);
    });
    child.on("close", (code) => {
      if (code === 0) resolve(out);
      else reject(new Error(`${cmd} ${cmdArgs.join(" ")} exited ${code}\n${out}`));
    });
  });
}

async function curlOk(url) {
  const res = await fetch(url, { signal: AbortSignal.timeout(30000) });
  return res.ok ? res.json().catch(() => ({})) : null;
}

async function brokerFetch(pathname, opts = {}, attempt = 0) {
  const maxAttempts = 5;
  const backoffMs = 1000 * (attempt + 1);
  try {
    const res = await fetch(`${BROKER_URL.replace(/\/$/, "")}${pathname}`, {
      ...opts,
      headers: {
        "Content-Type": "application/json",
        "X-MCP-Broker-Key": BROKER_KEY,
        ...(opts.headers || {}),
      },
      signal: AbortSignal.timeout(60000),
    });
    if (
      (res.status === 429 || res.status === 500 || res.status === 502 || res.status === 503) &&
      attempt < maxAttempts
    ) {
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

async function waitForAgent(containerId, maxWaitMs = 120000) {
  const { execFile } = await import("node:child_process");
  const probe = [
    "docker",
    "exec",
    containerId,
    "python",
    "-c",
    "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9320/health', timeout=3)",
  ];
  const deadline = Date.now() + maxWaitMs;
  while (Date.now() < deadline) {
    const ok = await new Promise((resolve) => {
      execFile(probe[0], probe.slice(1), (err) => resolve(!err));
    });
    if (ok) return;
    await new Promise((r) => setTimeout(r, 2000));
  }
  throw new Error(`sandbox-agent not healthy for ${containerId.slice(0, 12)} within ${maxWaitMs}ms`);
}

async function waitBrokerDockerOk(maxWaitMs = 60000) {
  const base = BROKER_URL.replace(/\/$/, "");
  const deadline = Date.now() + maxWaitMs;
  while (Date.now() < deadline) {
    const brokerHealth = await curlOk(`${base}/health`);
    if (brokerHealth?.docker_ok) return brokerHealth;
    await new Promise((r) => setTimeout(r, 2000));
  }
  throw new Error(
    `mcp-broker not healthy at ${BROKER_URL} (docker_ok=false after ${maxWaitMs}ms). ` +
      "Run: docker compose --profile services up -d"
  );
}

async function bootstrap() {
  console.log("=== bootstrap: broker + gateway health ===");
  const brokerHealth = await waitBrokerDockerOk();
  console.log("broker docker_ok=true");

  const gatewayHealth = await curlOk(
    (process.env.GATEWAY_URL || "http://127.0.0.1:8300").replace(/\/$/, "") + "/health"
  );
  if (gatewayHealth) {
    console.log("gateway health ok");
  } else {
    console.warn("gateway /health not reachable — workers use broker API directly");
  }

  // Copy stdio stub into org sandboxes after ensure (workers call ensure first)
  for (const org of ORGS) {
    let ensureRes = null;
    for (let attempt = 0; attempt < 4; attempt++) {
      ensureRes = await fetch(`${BROKER_URL}/v1/sandbox/${org}/ensure`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-MCP-Broker-Key": BROKER_KEY,
        },
        body: JSON.stringify({ warm: true }),
      });
      if (ensureRes.ok) break;
      if (![429, 500, 502, 503].includes(ensureRes.status)) break;
      await new Promise((r) => setTimeout(r, 2000 * (attempt + 1)));
    }
    if (!ensureRes?.ok) {
      throw new Error(`ensure ${org} failed: ${ensureRes?.status}`);
    }
    const body = await ensureRes.json();
    const cid = body.container_id;
    if (!cid) throw new Error(`no container_id for ${org}`);
    await waitForAgent(cid);
    await run("docker", ["cp", STUB_HOST, `${cid}:${STUB_CONTAINER}`]);
    console.log(`stub copied into ${org} (${cid.slice(0, 12)})`);
  }
  console.log("=== bootstrap OK ===");
}

async function parallelWorkers() {
  console.log("=== parallel org workers (2 × 6 servers) + cross-org leak probe ===");
  const workers = ORGS.map((org) =>
    run("node", [WORKER], {
      ORG_SLUG: org,
      MCP_BROKER_URL: BROKER_URL,
      MCP_BROKER_INTERNAL_KEY: BROKER_KEY,
      WORKER_REPORT: `runs/org_worker_${org}.json`,
    })
  );
  await Promise.all(workers);
  await run("node", [LEAK_PROBE], {
    MCP_BROKER_URL: BROKER_URL,
    MCP_BROKER_INTERNAL_KEY: BROKER_KEY,
    LEAK_PROBE_REPORT: "runs/cross_org_leak_probe.json",
  });
  console.log("=== parallel workers OK ===");
}

async function resetSandboxes() {
  console.log("=== reset org sandboxes (clear stdio server slots) ===");
  for (const org of ORGS) {
    const res = await brokerFetch(`/v1/sandbox/${org}`, { method: "DELETE" });
    if (!res.ok && res.status !== 404) {
      throw new Error(`destroy ${org} failed: ${res.status}`);
    }
  }
  const deadline = Date.now() + 60000;
  for (const org of ORGS) {
    while (Date.now() < deadline) {
      const st = await brokerFetch(`/v1/sandbox/${org}/status`);
      if (!st.ok) {
        break;
      }
      const body = await st.json().catch(() => ({}));
      if (body.status === "missing") {
        break;
      }
      await new Promise((r) => setTimeout(r, 500));
    }
  }
  await waitBrokerDockerOk(30000);
}

async function pytestLeakage() {
  console.log("=== pytest adversarial leakage subset ===");
  const gatewayDir = path.join(REPO_ROOT, "gateway");
  const venvPy = path.join(gatewayDir, ".venv/bin/python");
  const py = fs.existsSync(venvPy) ? venvPy : "python3";
    await run(
    py,
    [
      "-m",
      "pytest",
      "ai_mesh_gateway/tests/test_mcp_sandbox_adversarial.py",
      "-q",
      "-k",
      "volume or foreign_org or npm_cache or denylist_secrets",
      "--tb=short",
    ],
    {
      MCP_BROKER_URL: BROKER_URL,
      MCP_BROKER_INTERNAL_KEY: BROKER_KEY,
      MCP_ADVERSARIAL_USE_LIVE_BROKER: "true",
    },
    gatewayDir
  );
}

async function main() {
  fs.mkdirSync(path.join(REPO_ROOT, "runs"), { recursive: true });
  try {
    await bootstrap();
    if (bootstrapOnly) {
      console.log("bootstrap-only complete");
      return;
    }
    await parallelWorkers();
    await waitBrokerDockerOk(90000);
    if (full) {
      await resetSandboxes();
      await pytestLeakage();
    }
    console.log("run_parallel_org_agents OK");
  } catch (e) {
    console.error("run_parallel_org_agents FAIL:", e.message);
    process.exit(1);
  }
}

main();
