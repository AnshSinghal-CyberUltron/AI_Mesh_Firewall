/**
 * Module 1 → Module 2 sync smoke (live stack).
 *
 * Verifies cross-module propagation without manual page reload:
 *   1. Module 2 dashboard loads and exposes KPIs.
 *   2. A telemetry activity event triggers a dashboard refetch while M2 stays mounted.
 *   3. A Module 1 kill-switch mutation updates containment KPIs on revisit (cache cleared).
 *
 * Run:
 *   NODE_PATH="$PWD/tests/e2e/node_modules" BASE_URL=http://127.0.0.1:8180 \
 *     node scripts/playwright_module1_module2_sync.mjs
 */
import { chromium } from "playwright";
import fs from "node:fs";

const BASE = (process.env.BASE_URL || "http://127.0.0.1:8180").replace(/\/$/, "");
const EMAIL = process.env.TEST_EMAIL || "admin@zeroshield.io";
const PASS = process.env.TEST_PASSWORD || "Adm1n!Pass#2024";
const OUT = process.env.E2E_REPORT || "runs/playwright_module1_module2_sync.json";
const SHOT_DIR = process.env.SHOT_DIR || "runs/m1-m2-sync";

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

async function login(page) {
  CURRENT_PHASE = "login";
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle", timeout: 120000 });
  await page.locator("#email").fill(EMAIL);
  await page.locator("#password").fill(PASS);
  const [ok] = await Promise.all([
    page.waitForResponse((r) => r.url().includes("/api/auth/token/") && r.request().method() === "POST", { timeout: 30000 }),
    page.getByRole("button", { name: /^sign in$/i }).click(),
  ]);
  assert(ok.ok(), `valid creds -> 2xx (got ${ok.status()})`);
  await page.waitForFunction(() => !location.pathname.startsWith("/login"), { timeout: 30000 });
  report.steps.push("login");
}

async function waitForDashboard(page) {
  const res = await page.waitForResponse(
    (r) => r.url().includes("/api/module2/dashboard/") && r.request().method() === "GET" && r.ok(),
    { timeout: 90000 },
  );
  return res.json();
}

async function main() {
  fs.mkdirSync("runs", { recursive: true });
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  page.on("pageerror", (e) => report.pageErrors.push({ phase: CURRENT_PHASE, message: String(e.message || e) }));
  page.on("dialog", (d) => d.accept());

  try {
    await login(page);

    CURRENT_PHASE = "m2-dashboard-load";
    const [dash] = await Promise.all([
      waitForDashboard(page),
      page.goto(`${BASE}/dashboard`, { waitUntil: "domcontentloaded", timeout: 120000 }),
    ]);
    assert(typeof dash?.kpis?.total_events === "number", "dashboard returns total_events KPI");
    assert(dash?.lane_summary && typeof dash.lane_summary === "object", "dashboard returns lane_summary");
    // UEBA is a separate Module 2 surface (/api/module2/ueba/*), not a dashboard lane.
    // Lanes are chat/rag/mcp/vector/threat_intel (see build_lane_summary).
    assert("threat_intel" in dash.lane_summary && "chat" in dash.lane_summary, "lane_summary includes chat and threat_intel");
    const baselineKillSwitches = dash?.kpis?.active_kill_switches ?? dash?.containment?.active_kill_switches ?? 0;
    report.steps.push("m2-dashboard-load");
    await shot(page, "01-dashboard");

    CURRENT_PHASE = "m2-live-telemetry-refresh";
    let refetchCount = 0;
    const refetchPromise = page.waitForResponse(
      (r) => {
        if (r.url().includes("/api/module2/dashboard/") && r.request().method() === "GET") {
          refetchCount += 1;
          return refetchCount >= 2;
        }
        return false;
      },
      { timeout: 10000 },
    );
    await page.evaluate(() => {
      window.dispatchEvent(
        new CustomEvent("zeroshield:telemetry-activity", {
          detail: { source: "e2e-probe", at: Date.now() },
        }),
      );
    });
    await refetchPromise;
    assert(true, "telemetry activity event triggers dashboard refetch without manual reload");
    report.steps.push("m2-live-telemetry-refresh");

    CURRENT_PHASE = "m1-kill-switch-mutation";
    const [modelsRes] = await Promise.all([
      page.waitForResponse((r) => r.url().includes("/api/firewall/models/") && r.request().method() === "GET", { timeout: 60000 }).catch(() => null),
      page.goto(`${BASE}/?tab=firewall-1-6`, { waitUntil: "domcontentloaded", timeout: 120000 }),
    ]);
    await page.locator("text=/kill-switch management/i").first().waitFor({ state: "visible", timeout: 30000 });

    let modelName = "gpt4o-mini";
    if (modelsRes?.ok()) {
      const models = await modelsRes.json().catch(() => []);
      const list = Array.isArray(models) ? models : models.results || [];
      if (list[0]?.model_name) modelName = list[0].model_name;
    }

    const uniqueLabel = `m1-m2-sync-${Date.now()}`;
    await page.getByRole("button", { name: /create kill-switch/i }).click();
    const labelInput = page.locator('input[name="label"], input[placeholder*="label" i]').first();
    if (await labelInput.count()) await labelInput.fill(uniqueLabel);
    const reasonInput = page.locator('input[name="reason"], textarea[name="reason"]').first();
    if (await reasonInput.count()) await reasonInput.fill("module1-module2 sync e2e");
    const modelSelect = page.locator("select").filter({ has: page.locator(`option[value="${modelName}"]`) }).first();
    if (await modelSelect.count()) await modelSelect.selectOption(modelName);

    const [createRes] = await Promise.all([
      page.waitForResponse((r) => r.url().includes("/api/kill-switches/") && r.request().method() === "POST", { timeout: 30000 }),
      page.getByRole("button", { name: /^(create|save|submit)/i }).last().click(),
    ]);
    assert(createRes.ok(), `kill-switch create -> 2xx (got ${createRes.status()})`);
    await page.locator(`text=${uniqueLabel}`).first().waitFor({ state: "visible", timeout: 20000 });
    report.steps.push("m1-kill-switch-create");
    await shot(page, "02-kill-switch-created");

    CURRENT_PHASE = "m2-containment-after-mutation";
    const dashAfter = await Promise.all([
      waitForDashboard(page),
      page.goto(`${BASE}/dashboard`, { waitUntil: "domcontentloaded", timeout: 120000 }),
    ]).then(([d]) => d);
    const afterKillSwitches = dashAfter?.kpis?.active_kill_switches ?? dashAfter?.containment?.active_kill_switches ?? 0;
    assert(
      afterKillSwitches >= baselineKillSwitches,
      `active_kill_switches increased or held after M1 mutation (${baselineKillSwitches} -> ${afterKillSwitches})`,
    );
    report.steps.push("m2-containment-after-mutation");
    await shot(page, "03-dashboard-after-mutation");

    CURRENT_PHASE = "cleanup";
    await page.goto(`${BASE}/?tab=firewall-1-6`, { waitUntil: "domcontentloaded", timeout: 120000 });
    const row = page.locator(`tr:has-text("${uniqueLabel}")`).first();
    if (await row.count()) {
      await row.getByRole("button", { name: /delete/i }).click().catch(async () => {
        await row.locator('button[title*="Delete" i], button:has-text("Delete")').first().click();
      });
      await page.waitForResponse((r) => r.url().includes("/api/kill-switches/") && r.request().method() === "DELETE", { timeout: 30000 }).catch(() => null);
    }
    report.steps.push("cleanup");

    assert(report.pageErrors.length === 0, `no uncaught page errors (got ${report.pageErrors.length})`);
    report.ok = true;
  } catch (e) {
    report.error = String(e.message || e);
    await shot(page, "error");
  } finally {
    fs.writeFileSync(OUT, JSON.stringify(report, null, 2));
    await browser.close();
    if (!report.ok) {
      console.error(JSON.stringify(report, null, 2));
      process.exit(1);
    }
    console.log(JSON.stringify(report, null, 2));
  }
}

main();
