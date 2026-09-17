/**
 * V3 A02 frontend save/reload on synthetic org v3a02-block.
 * Real login form (not localStorage token injection).
 *
 *   NODE_PATH="$PWD/tests/e2e/node_modules" BASE_URL=http://127.0.0.1:8180 \\
 *     TEST_EMAIL=... TEST_PASSWORD=... node scripts/v3_a02_frontend_save_reload.mjs
 */
import { createRequire } from "node:module";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);
const { chromium } = require(path.join(__dirname, "../tests/e2e/node_modules/playwright"));

const BASE = (process.env.BASE_URL || "http://127.0.0.1:8180").replace(/\/$/, "");
const EMAIL = process.env.TEST_EMAIL;
const PASS = process.env.TEST_PASSWORD;
const OUT = process.env.E2E_REPORT || "docs/plans/evidence/2026-09-16-v3-a00-a01-a02/A02/frontend_save_reload.json";
const SHOT_DIR = process.env.SHOT_DIR || "docs/plans/evidence/2026-09-16-v3-a00-a01-a02/A02/shots";

const report = {
  base: BASE,
  email: EMAIL,
  ok: false,
  steps: [],
  asserts: [],
  pageErrors: [],
  network: [],
  error: null,
};

function assert(cond, label) {
  report.asserts.push({ label, pass: !!cond });
  if (!cond) throw new Error(`ASSERT FAILED: ${label}`);
}

async function shot(page, name) {
  fs.mkdirSync(SHOT_DIR, { recursive: true });
  await page.screenshot({ path: `${SHOT_DIR}/${name}.png`, fullPage: true });
}

async function main() {
  if (!EMAIL || !PASS) throw new Error("TEST_EMAIL and TEST_PASSWORD required");
  fs.mkdirSync(SHOT_DIR, { recursive: true });
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  page.on("pageerror", (e) => report.pageErrors.push(String(e.message || e)));
  page.on("response", async (r) => {
    const url = r.url();
    if (url.includes("/api/firewall/config/") || url.includes("/api/auth/")) {
      report.network.push({
        url: url.replace(BASE, ""),
        method: r.request().method(),
        status: r.status(),
        requestId: r.headers()["x-request-id"] || r.headers()["x-correlation-id"] || null,
      });
    }
  });

  try {
    await page.goto(`${BASE}/login`, { waitUntil: "networkidle", timeout: 120000 });
    await page.locator("#email").fill(EMAIL);
    await page.locator("#password").fill(PASS);
    await Promise.all([
      page.waitForResponse((r) => r.url().includes("/api/auth/token/") && r.request().method() === "POST", { timeout: 60000 }),
      page.getByRole("button", { name: /^sign in$/i }).click(),
    ]);
    await page.waitForTimeout(1500);
    assert(!page.url().includes("/login"), `logged in, url=${page.url()}`);
    report.steps.push("login-ok");
    await shot(page, "01-after-login");

    await page.goto(`${BASE}/?tab=firewall-config`, { waitUntil: "domcontentloaded", timeout: 120000 });
    await page.getByText("PII Detection", { exact: false }).first().waitFor({ state: "visible", timeout: 60000 });
    report.steps.push("config-page-visible");
    await shot(page, "02-config-loaded");

    const getResp = report.network.filter((n) => n.url.includes("/api/firewall/config/") && n.method === "GET");
    assert(getResp.some((n) => n.status === 200), "GET /api/firewall/config/ 200");

    const piiSwitch = page.getByRole("switch", { name: /PII Detection/i }).first();
    await piiSwitch.waitFor({ state: "visible", timeout: 30000 });
    const before = await piiSwitch.getAttribute("aria-checked") || await piiSwitch.getAttribute("data-state");
    report.pii_before = before;
    await piiSwitch.click();
    await page.waitForTimeout(400);
    const afterToggle = await piiSwitch.getAttribute("aria-checked") || await piiSwitch.getAttribute("data-state");
    report.pii_after_toggle = afterToggle;
    assert(before !== afterToggle, `PII switch toggled ${before} -> ${afterToggle}`);
    report.steps.push("pii-toggled");

    const [put] = await Promise.all([
      page.waitForResponse((r) => r.url().includes("/api/firewall/config/") && r.request().method() === "PUT", { timeout: 30000 }),
      page.getByRole("button", { name: /Save Configuration/i }).click(),
    ]);
    assert(put.status() === 200, `PUT save HTTP ${put.status()}`);
    let putBody = null;
    try { putBody = await put.json(); } catch {}
    report.put_pii_detection_enabled = putBody?.pii_detection_enabled;
    report.steps.push("save-200");
    await shot(page, "03-after-save");

    await page.reload({ waitUntil: "domcontentloaded", timeout: 120000 });
    await page.getByText("PII Detection", { exact: false }).first().waitFor({ state: "visible", timeout: 60000 });
    const pii2 = page.getByRole("switch", { name: /PII Detection/i }).first();
    await pii2.waitFor({ state: "visible", timeout: 30000 });
    const reloaded = await pii2.getAttribute("aria-checked") || await pii2.getAttribute("data-state");
    report.pii_after_reload = reloaded;
    assert(String(reloaded) === String(afterToggle), `reload persists toggle ${afterToggle} vs ${reloaded}`);
    report.steps.push("reload-matches-save");
    await shot(page, "04-after-reload");

    report.ok = report.asserts.every((a) => a.pass) && report.pageErrors.length === 0;
  } catch (err) {
    report.error = String(err?.message || err);
    try { await shot(page, "99-failure"); } catch {}
  } finally {
    await browser.close();
    fs.mkdirSync(OUT.replace(/\/[^/]+$/, ""), { recursive: true });
    fs.writeFileSync(OUT, JSON.stringify(report, null, 2));
    console.log(JSON.stringify({ ok: report.ok, error: report.error, steps: report.steps, asserts: report.asserts, put: report.put_pii_detection_enabled, reload: report.pii_after_reload }));
    if (!report.ok) process.exit(1);
  }
}

main();
