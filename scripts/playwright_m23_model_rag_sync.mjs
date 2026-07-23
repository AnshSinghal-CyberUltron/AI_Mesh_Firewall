/**
 * M2.4 Model & RAG Health — Attack Simulator + RAG Attack → M2.4 tab sync gate.
 *
 * Run:
 *   NODE_PATH="$PWD/tests/e2e/node_modules" BASE_URL=http://127.0.0.1:8180 \
 *     node scripts/playwright_m23_model_rag_sync.mjs
 */
import { chromium } from "playwright";
import fs from "node:fs";

const BASE = (process.env.BASE_URL || "http://127.0.0.1:8180").replace(/\/$/, "");
const EMAIL = process.env.TEST_EMAIL || "admin@zeroshield.io";
const PASS = process.env.TEST_PASSWORD || "Adm1n!Pass#2024";
const OUT = process.env.E2E_REPORT || "runs/playwright_m23_model_rag_sync.json";
const SHOT_DIR = process.env.SHOT_DIR || "runs/m23-model-rag-sync";

const report = { base: BASE, ok: false, steps: [], asserts: [], pageErrors: [], notes: [], error: null };
let CURRENT_PHASE = "init";

function assert(cond, label) {
  report.asserts.push({ label, pass: !!cond, phase: CURRENT_PHASE });
  if (!cond) throw new Error(`ASSERT FAILED [${CURRENT_PHASE}]: ${label}`);
  return cond;
}

async function shot(page, name) {
  try {
    fs.mkdirSync(SHOT_DIR, { recursive: true });
    await page.screenshot({ path: `${SHOT_DIR}/${name}.png`, fullPage: false });
  } catch {}
}

async function waitForControlPlane(page) {
  for (let i = 0; i < 30; i++) {
    const ok = await page.evaluate(async () => {
      try {
        const c = await fetch("/api/health/", { signal: AbortSignal.timeout(4000) });
        return c.ok;
      } catch {
        return false;
      }
    });
    if (ok) return true;
    await page.waitForTimeout(2000);
  }
  return false;
}

async function installGatewayProxy(context) {
  for (const origin of ["https://aimeshgateway.zeroshield.ai", "http://aimeshgateway.zeroshield.ai"]) {
    await context.route(`${origin}/**`, async (route) => {
      const incoming = new URL(route.request().url());
      const proxied = `${BASE}${incoming.pathname}${incoming.search}`;
      const response = await route.fetch({
        url: proxied,
        method: route.request().method(),
        headers: route.request().headers(),
        postData: route.request().postData(),
      });
      await route.fulfill({ response });
    });
  }
}

async function login(page) {
  CURRENT_PHASE = "login";
  await page.goto(`${BASE}/login`, { waitUntil: "domcontentloaded", timeout: 120000 });
  await waitForControlPlane(page);
  await page.locator("#email").fill(EMAIL);
  await page.locator("#password").fill(PASS);
  const [tokenResp] = await Promise.all([
    page.waitForResponse((r) => r.url().includes("/api/auth/token/") && r.request().method() === "POST", { timeout: 45000 }),
    page.getByRole("button", { name: /^sign in$/i }).click(),
  ]);
  assert(tokenResp.ok(), `valid creds -> 2xx (got ${tokenResp.status()})`);
  await page.waitForFunction(() => !location.pathname.startsWith("/login"), { timeout: 45000 });
  report.steps.push("login");
}

async function gotoTab(page, tab, anchorText) {
  await page.goto(`${BASE}/?tab=${tab}`, { waitUntil: "domcontentloaded", timeout: 120000 });
  await page.locator(`text=${anchorText}`).first().waitFor({ state: "visible", timeout: 60000 });
}

async function runAttackInjection(page) {
  CURRENT_PHASE = "m1-attack-simulator";
  await gotoTab(page, "firewall-1-1", "/Attack Simulator/i");
  const attackPanel = page.locator("div.ai-mesh-card").filter({ has: page.getByRole("heading", { name: /Attack Simulator/i }) }).first();
  await attackPanel.waitFor({ state: "visible", timeout: 60000 });
  await page.waitForResponse((r) => r.url().includes("/api/firewall/models/"), { timeout: 90000 }).catch(() => {});
  await page.waitForTimeout(800);

  await attackPanel.getByRole("button", { name: /Prompt Injection/i }).first().click();
  await page.waitForTimeout(250);
  const [resp] = await Promise.all([
    page.waitForResponse((r) => r.url().includes("/v1/chat/completions") && r.request().method() === "POST", { timeout: 120000 }),
    attackPanel.getByRole("button", { name: /Run Pipeline/i }).click(),
  ]);
  report.notes.push(`attack injection HTTP ${resp.status()}`);
  assert(resp.status() >= 400 && resp.status() < 500, `injection blocked (HTTP ${resp.status()} 4xx)`);
  report.steps.push("m1-attack-simulator");
  await shot(page, "01-attack-blocked");
}

async function runRagInjection(page) {
  CURRENT_PHASE = "m1-rag-simulator";
  await gotoTab(page, "firewall-1-3", "/RAG Attack & Trust/i");
  const ragPanel = page.locator("div.ai-mesh-card").filter({ has: page.getByRole("heading", { name: /RAG Attack & Trust/i }) }).first();
  await ragPanel.waitFor({ state: "visible", timeout: 60000 }).catch(() => {});
  await page.getByRole("button", { name: /RAG Query Injection/i }).first().click();
  await page.waitForTimeout(250);
  const [resp] = await Promise.all([
    page.waitForResponse((r) => r.url().includes("/v1/rag/query") && r.request().method() === "POST", { timeout: 120000 }),
    page.getByRole("button", { name: /Execute RAG Query/i }).first().click(),
  ]);
  report.notes.push(`rag injection HTTP ${resp.status()}`);
  assert(resp.status() === 403, `RAG injection blocked (HTTP ${resp.status()})`);
  report.steps.push("m1-rag-simulator");
  await shot(page, "02-rag-blocked");
}

async function assertModelExposureTab(page) {
  CURRENT_PHASE = "m2-model-exposure";
  const [expResp] = await Promise.all([
    page.waitForResponse(
      (r) => r.url().includes("/api/module2/models/exposure/") && r.request().method() === "GET" && r.ok(),
      { timeout: 90000 },
    ),
    page.goto(`${BASE}/models/exposure?tab=model&period=24h`, { waitUntil: "domcontentloaded", timeout: 120000 }),
  ]);
  const exposure = await expResp.json().catch(() => ({}));
  report.notes.push(`exposure models=${(exposure.models || []).length}`);

  await page.getByRole("heading", { name: /Model & RAG Health/i }).waitFor({ state: "visible", timeout: 30000 });
  await page.getByRole("tab", { name: /Model Exposure/i }).waitFor({ state: "visible", timeout: 15000 });
  assert((exposure.models || []).length > 0, "exposure API returns models");

  await page.getByText(/Active Models/i).first().waitFor({ state: "visible", timeout: 30000 });
  const tableText = await page.locator("body").innerText();
  const hasModelRow = (exposure.models || []).some((m) => m.model && tableText.includes(String(m.model)));
  assert(hasModelRow, "Active Models table contains a model from exposure payload");

  await page.getByText(/Vulnerability Exposure by Model/i).first().waitFor({ state: "visible", timeout: 20000 });
  const chartBars = page.locator(".recharts-bar-rectangle");
  assert(await chartBars.count() > 0, "exposure chart renders bar elements");
  report.steps.push("m2-model-exposure");
  await shot(page, "03-model-exposure");
}

async function assertRagHealthTab(page) {
  CURRENT_PHASE = "m2-rag-health";
  let ragResp;
  await page.getByRole("tab", { name: /RAG & Retrieval/i }).click();
  ragResp = await page.waitForResponse(
    (r) => r.url().includes("/api/module2/rag/health/") && r.request().method() === "GET" && r.ok(),
    { timeout: 90000 },
  );
  const rag = await ragResp.json().catch(() => ({}));
  report.notes.push(`rag stages query=${rag?.rag_pipeline_kpis?.stages?.query?.total ?? 0}`);

  await page.waitForURL(/tab=rag/, { timeout: 15000 });
  await page.getByText(/Vector Collection Registry/i).first().waitFor({ state: "visible", timeout: 30000 });

  const stageTotal = Object.values(rag?.rag_pipeline_kpis?.stages || {}).reduce(
    (sum, s) => sum + (s?.total || 0),
    0,
  );
  assert(stageTotal > 0, "rag_pipeline_kpis stages have volume");

  await page.getByText(/Pipeline Stage Volume/i).first().waitFor({ state: "visible", timeout: 20000 });
  const stageBars = page.locator(".recharts-bar-rectangle");
  assert(await stageBars.count() > 0, "RAG stage charts render bar elements");
  report.steps.push("m2-rag-health");
  await shot(page, "04-rag-health");
}

async function main() {
  fs.mkdirSync("runs", { recursive: true });
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1100 } });
  await installGatewayProxy(context);
  const page = await context.newPage();
  page.on("pageerror", (e) => report.pageErrors.push({ phase: CURRENT_PHASE, message: String(e.message || e) }));

  try {
    await login(page);
    await runAttackInjection(page);
    await page.waitForTimeout(4000);
    await assertModelExposureTab(page);
    await runRagInjection(page);
    await page.waitForTimeout(4000);
    await page.goto(`${BASE}/models/exposure?tab=rag&period=24h`, { waitUntil: "domcontentloaded", timeout: 120000 });
    await assertRagHealthTab(page);
    report.ok = true;
  } catch (err) {
    report.error = String(err?.message || err);
    await shot(page, "error");
  } finally {
    fs.writeFileSync(OUT, JSON.stringify(report, null, 2));
    await browser.close();
  }

  const failed = report.asserts.filter((a) => !a.pass);
  console.log(JSON.stringify({ ok: report.ok, steps: report.steps, failed: failed.map((f) => f.label), error: report.error }, null, 2));
  process.exit(report.ok ? 0 : 1);
}

main();
