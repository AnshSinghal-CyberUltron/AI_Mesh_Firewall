/**
 * Frontend parallel orgs — MCPConnectorPanel stdio presets (5+ register + sync + tools/call).
 * Requires full stack on :8180 with MCP_STDIO_IN_PROCESS=false.
 *
 * Run: BASE_URL=http://127.0.0.1:8180 node tests/e2e/mcp_sandbox_adversarial/frontend_parallel_orgs.mjs
 */
import { chromium } from "playwright";
import fs from "node:fs";

const BASE = (process.env.BASE_URL || "http://127.0.0.1:8180").replace(/\/$/, "");
const BROKER_URL = (process.env.MCP_BROKER_URL || "http://127.0.0.1:8311").replace(/\/$/, "");
const BROKER_KEY = process.env.MCP_BROKER_INTERNAL_KEY || "dev-mcp-broker-key-change-me";
const EMAIL = process.env.TEST_EMAIL || "admin@zeroshield.io";
const PASS = process.env.TEST_PASSWORD || "Adm1n!Pass#2024";
const OUT = process.env.E2E_REPORT || "runs/frontend_parallel_orgs.json";

/** Stdio presets from MCPConnectorPanel — 5+ per org registration pass */
const STDIO_PRESETS = [
  "Playwright MCP",
  "Semgrep MCP",
  "Memory MCP",
  "Everything MCP",
  "Vibe Check MCP",
];

/** Preset label → server card heading (legacy names differ from preset buttons) */
const SERVER_CARD_PATTERN = {
  "Playwright MCP": /Playwright MCP|Playwright/i,
  "Semgrep MCP": /Semgrep MCP/i,
  "Memory MCP": /Memory MCP/i,
  "Everything MCP": /Everything MCP/i,
  "Vibe Check MCP": /Vibe Check/i,
};

async function withRetry(label, fn, { retries = 3, backoffMs = 1500 } = {}) {
  let lastErr;
  for (let i = 0; i <= retries; i++) {
    try {
      return await fn();
    } catch (e) {
      lastErr = e;
      if (i < retries && /429|503|502|timeout|ETIMEDOUT|ECONNRESET/i.test(String(e.message))) {
        await new Promise((r) => setTimeout(r, backoffMs * (i + 1)));
        continue;
      }
      throw e;
    }
  }
  throw lastErr;
}

async function waitForControlPlane(maxWaitMs = 180000) {
  const deadline = Date.now() + maxWaitMs;
  const urls = [
    `${BASE.replace(":8180", ":8100")}/api/health/`,
    `${BASE.replace(":8180", ":8300")}/health`,
  ];
  while (Date.now() < deadline) {
    const results = await Promise.all(
      urls.map(async (url) => {
        try {
          const res = await fetch(url, { signal: AbortSignal.timeout(8000) });
          return res.ok;
        } catch {
          return false;
        }
      })
    );
    if (results.every(Boolean)) return;
    await new Promise((r) => setTimeout(r, 3000));
  }
  throw new Error("Control plane not healthy (control :8100 / gateway :8300)");
}

async function login(page) {
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle", timeout: 120000 });
  await page.locator("#email").fill(EMAIL);
  await page.locator("#password").fill(PASS);
  const [res] = await Promise.all([
    page.waitForResponse((r) => r.url().includes("/api/auth/token/") && r.request().method() === "POST", { timeout: 120000 }),
    page.getByRole("button", { name: /^sign in$/i }).click(),
  ]);
  if (!res.ok()) throw new Error(`Login failed: ${res.status()}`);
}

async function openMcpPanel(page) {
  await page.goto(`${BASE}/?tab=firewall-1-4`, { waitUntil: "domcontentloaded", timeout: 120000 });
  await page
    .waitForResponse(
      (r) => r.url().includes("/api/mcp-connector/servers/") && r.request().method() === "GET",
      { timeout: 120000 }
    )
    .catch(() => null);
  await page.locator('[role="status"], .animate-spin').first().waitFor({ state: "hidden", timeout: 180000 }).catch(() => {});
  const presetProbe = page.getByRole("button", { name: /Semgrep MCP|Playwright MCP|Memory MCP/i }).first();
  await presetProbe.waitFor({ state: "visible", timeout: 180000 });
}

function serverCardLocator(page, presetName) {
  const pattern = SERVER_CARD_PATTERN[presetName] || new RegExp(`^${presetName}$`, "i");
  return page.locator("h4").filter({ hasText: pattern }).first();
}

async function touchSandboxActivity(orgSlug) {
  const res = await fetch(`${BROKER_URL}/v1/sandbox/${orgSlug}/ensure`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-MCP-Broker-Key": BROKER_KEY,
    },
    body: JSON.stringify({ warm: true }),
    signal: AbortSignal.timeout(30000),
  });
  if (!res.ok) throw new Error(`touch ${orgSlug} failed: ${res.status}`);
}

async function registerPreset(page, presetName) {
  const existing = serverCardLocator(page, presetName);
  if (await existing.isVisible().catch(() => false)) {
    return "already-listed";
  }
  const escaped = presetName.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const presetBtn = page.getByRole("button", { name: new RegExp(`^${escaped}`, "i") });
  await presetBtn.scrollIntoViewIfNeeded();
  await presetBtn.waitFor({ state: "visible", timeout: 60000 });
  const [createRes] = await Promise.all([
    page.waitForResponse(
      (r) => r.url().includes("/api/mcp-connector/servers/") && r.request().method() === "POST",
      { timeout: 300000 }
    ),
    presetBtn.click(),
  ]);
  if (!createRes.ok() && createRes.status() !== 409) {
    const body = await createRes.text().catch(() => "");
    throw new Error(`Register ${presetName} failed: ${createRes.status()} ${body.slice(0, 200)}`);
  }
  if (createRes.status() === 409) {
    await page.getByRole("button", { name: /^refresh$/i }).first().click().catch(() => {});
    await page.waitForTimeout(1500);
  }
  return createRes.status() === 409 ? "exists" : "registered";
}

async function syncTools(page, presetName) {
  const serverCard = serverCardLocator(page, presetName);
  await serverCard.scrollIntoViewIfNeeded();
  await serverCard.waitFor({ state: "visible", timeout: 120000 });
  const card = serverCard.locator("xpath=ancestor::div[contains(@class,'rounded')][1]");
  const toolsLabel = card.locator("text=/\\d+ tools/").first();
  if (await toolsLabel.isVisible().catch(() => false)) {
    const match = (await toolsLabel.innerText()).match(/(\d+)\s+tools/);
    if (match && Number(match[1]) > 0) {
      return Number(match[1]);
    }
  }
  const syncBtn = card.getByRole("button", { name: /sync tools from server/i });
  const [syncRes] = await Promise.all([
    page.waitForResponse(
      (r) => /\/api\/mcp-connector\/servers\/[^/]+\/tools\/?$/.test(r.url()) && r.request().method() === "POST",
      { timeout: 600000 }
    ),
    syncBtn.click(),
  ]);
  const body = await syncRes.json().catch(() => ({}));
  if (!syncRes.ok() || body.error || body.connection_status === "failed") {
    throw new Error(`tools/list ${presetName}: ${body.error || syncRes.status()}`);
  }
  return body.synced ?? 0;
}

async function callFirstTool(page, presetName) {
  await page.getByRole("tab", { name: /tool execution/i }).click();
  await page
    .waitForResponse(
      (r) => r.url().includes("/api/mcp-connector/tools/") && r.request().method() === "GET",
      { timeout: 120000 }
    )
    .catch(() => null);
  await page.locator('[role="status"], .animate-spin').first().waitFor({ state: "hidden", timeout: 120000 }).catch(() => {});
  await page.waitForSelector('[aria-label="Execution server"]', { timeout: 120000 });

  const pattern = SERVER_CARD_PATTERN[presetName] || new RegExp(presetName, "i");
  const serverSelect = page.getByLabel("Execution server");
  const serverOpts = await serverSelect.locator("option").evaluateAll((els) =>
    els.map((o) => ({ value: o.value, text: (o.textContent || "").trim() }))
  );
  const serverMatch = serverOpts.find((o) => o.value && pattern.test(o.text));
  if (!serverMatch?.value) throw new Error(`Server ${presetName} not in execution dropdown`);

  await serverSelect.selectOption(serverMatch.value);
  const toolSelect = page.getByLabel("Tool to execute");
  await toolSelect.waitFor({ state: "visible", timeout: 30000 });
  const toolOpts = await toolSelect.locator("option").evaluateAll((els) =>
    els.map((o) => ({ value: o.value, text: (o.textContent || "").trim() }))
  );
  const toolMatch = toolOpts.find((o) => o.value && !/select tool|choose a server/i.test(o.text));
  if (!toolMatch?.value) throw new Error(`No tools for ${presetName} after sync`);

  const preferred = toolOpts.find((o) => o.value && /echo|vibe_check|get_time|ping|health/i.test(o.text));
  await toolSelect.selectOption((preferred || toolMatch).value);

  const toolName = (preferred || toolMatch).text.toLowerCase();
  let argsJson = "{}";
  if (/echo/.test(toolName)) {
    argsJson = JSON.stringify({ message: "adversarial-frontend-probe" });
  } else if (/vibe_check/.test(toolName)) {
    argsJson = JSON.stringify({
      goal: "MCP frontend adversarial probe",
      plan: "Register 5+ stdio servers, sync tools/list, invoke tools/call",
    });
  }
  await page.getByLabel("Tool arguments (JSON)").fill(argsJson);

  const [callRes] = await Promise.all([
    page.waitForResponse(
      (r) => r.url().includes("/api/mcp-connector/tools/call/") && r.request().method() === "POST",
      { timeout: 300000 }
    ),
    page.getByRole("button", { name: /execute tool/i }).click(),
  ]);
  if (!callRes.ok()) {
    const errBody = await callRes.text().catch(() => "");
    throw new Error(`tools/call ${presetName}: ${callRes.status()} ${errBody.slice(0, 200)}`);
  }
  const callBody = await callRes.json().catch(() => ({}));
  if (callBody.error && !callBody.result) {
    throw new Error(`tools/call ${presetName}: ${callBody.detail || callBody.error}`);
  }
  return true;
}

async function main() {
  fs.mkdirSync("runs", { recursive: true });
  const report = { base: BASE, ok: false, orgs: [], steps: [], error: null };
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();

  try {
    await waitForControlPlane();
    report.steps.push("control-plane-ready");
    await login(page);
    report.steps.push("login");
    await openMcpPanel(page);
    report.steps.push("mcp-panel");

    for (const preset of STDIO_PRESETS) {
      await touchSandboxActivity("adv-org-alpha").catch(() => touchSandboxActivity("adv-org-beta"));
      const reg = await withRetry(`register-${preset}`, () => registerPreset(page, preset));
      report.steps.push(`${preset}:${reg}`);
      const synced = await withRetry(`sync-${preset}`, () => syncTools(page, preset), {
        retries: 4,
        backoffMs: 2000,
      });
      report.steps.push(`${preset}:synced-${synced}`);
      report.orgs.push({ preset, reg, synced });
      await page.waitForTimeout(500);
    }

    if (report.orgs.length < 5) {
      throw new Error(`Need 5+ stdio presets registered (got ${report.orgs.length})`);
    }

    await callFirstTool(page, "Everything MCP");
    report.steps.push("tools-call-ok");

    report.ok = true;
    console.log("OK frontend_parallel_orgs", report.steps.join(" → "));
  } catch (e) {
    report.error = e.message;
    console.error("FAIL frontend_parallel_orgs", e.message);
  } finally {
    await browser.close();
    fs.writeFileSync(OUT, JSON.stringify(report, null, 2));
    process.exit(report.ok ? 0 : 1);
  }
}

main();
