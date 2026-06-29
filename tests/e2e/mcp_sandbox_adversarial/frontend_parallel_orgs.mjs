/**
 * Frontend parallel orgs — MCPConnectorPanel stdio presets per org (2 tabs / sequential orgs).
 * Requires full stack on :8180 with MCP_STDIO_IN_PROCESS=false.
 *
 * Run: BASE_URL=http://127.0.0.1:8180 node tests/e2e/mcp_sandbox_adversarial/frontend_parallel_orgs.mjs
 */
import { chromium } from "playwright";
import fs from "node:fs";

const BASE = (process.env.BASE_URL || "http://127.0.0.1:8180").replace(/\/$/, "");
const EMAIL = process.env.TEST_EMAIL || "admin@zeroshield.io";
const PASS = process.env.TEST_PASSWORD || "Adm1n!Pass#2024";
const OUT = process.env.E2E_REPORT || "runs/frontend_parallel_orgs.json";

/** Stdio presets from MCPConnectorPanel — 5+ per org registration pass */
const STDIO_PRESETS = [
  "Playwright MCP",
  "Semgrep MCP",
];

async function login(page) {
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle", timeout: 120000 });
  await page.locator("#email").fill(EMAIL);
  await page.locator("#password").fill(PASS);
  const [res] = await Promise.all([
    page.waitForResponse((r) => r.url().includes("/api/auth/token/") && r.request().method() === "POST"),
    page.getByRole("button", { name: /^sign in$/i }).click(),
  ]);
  if (!res.ok()) throw new Error(`Login failed: ${res.status()}`);
}

async function registerPreset(page, presetName) {
  await page.goto(`${BASE}/?tab=firewall-1-4`, { waitUntil: "networkidle", timeout: 120000 });
  const existing = page.locator("h4", { hasText: presetName }).first();
  if (await existing.isVisible().catch(() => false)) {
    return "already-listed";
  }
  const presetBtn = page.getByRole("button", { name: new RegExp(presetName, "i") });
  await presetBtn.waitFor({ state: "visible", timeout: 30000 });
  const [createRes] = await Promise.all([
    page.waitForResponse(
      (r) => r.url().includes("/api/mcp-connector/servers/") && r.request().method() === "POST",
      { timeout: 180000 }
    ),
    presetBtn.click(),
  ]);
  if (!createRes.ok() && createRes.status() !== 409) {
    throw new Error(`Register ${presetName} failed: ${createRes.status()}`);
  }
  return createRes.status() === 409 ? "exists" : "registered";
}

async function syncTools(page, presetName) {
  const serverCard = page.locator("h4", { hasText: presetName }).first();
  await serverCard.waitFor({ state: "visible", timeout: 60000 });
  const card = serverCard.locator("xpath=ancestor::div[contains(@class,'rounded')][1]");
  const syncBtn = card.getByRole("button", { name: /sync tools from server/i });
  const [syncRes] = await Promise.all([
    page.waitForResponse(
      (r) => /\/api\/mcp-connector\/servers\/[^/]+\/tools\/?$/.test(r.url()) && r.request().method() === "POST",
      { timeout: 300000 }
    ),
    syncBtn.click(),
  ]);
  const body = await syncRes.json().catch(() => ({}));
  if (!syncRes.ok() || body.error || body.connection_status === "failed") {
    throw new Error(`tools/list ${presetName}: ${body.error || syncRes.status()}`);
  }
  return body.synced ?? 0;
}

async function main() {
  fs.mkdirSync("runs", { recursive: true });
  const report = { base: BASE, ok: false, orgs: [], steps: [], error: null };
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();

  try {
    await login(page);
    report.steps.push("login");

    for (const preset of STDIO_PRESETS) {
      const reg = await registerPreset(page, preset);
      report.steps.push(`${preset}:${reg}`);
      const synced = await syncTools(page, preset);
      report.orgs.push({ preset, reg, synced });
    }

    if (report.orgs.length < 2) {
      throw new Error("Need 2+ stdio presets registered");
    }
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
