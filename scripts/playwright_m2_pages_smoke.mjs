/**
 * Quick smoke: M2.2 UEBA, M2.4 Model/RAG, M2.6 Incidents render + guide buttons.
 * Run:
 *   docker run --rm --add-host=host.docker.internal:host-gateway -v "%CD%:/work" -w /work \
 *     mcr.microsoft.com/playwright:v1.60.0-jammy node /work/scripts/playwright_m2_pages_smoke.mjs
 */
import { chromium } from "playwright";

const BASE = (process.env.BASE_URL || "http://host.docker.internal:8180").replace(/\/$/, "");
const EMAIL = process.env.TEST_EMAIL || "admin@zeroshield.io";
const PASS = process.env.TEST_PASSWORD || "Adm1n!Pass#2024";

const report = { ok: false, steps: [], errors: [] };

function step(name, pass, detail = "") {
  report.steps.push({ name, pass, detail });
  if (!pass) throw new Error(`${name}: ${detail || "failed"}`);
}

async function login(page) {
  await page.goto(`${BASE}/login`, { waitUntil: "domcontentloaded", timeout: 120000 });
  await page.fill('input[type="email"], input[name="email"]', EMAIL);
  await page.fill('input[type="password"]', PASS);
  await page.click('button[type="submit"]');
  await page.waitForURL((url) => !url.pathname.includes("/login"), { timeout: 120000 });
}

async function assertNoRenderError(page, label) {
  const body = await page.locator("body").innerText();
  const bad =
    /failed to render/i.test(body)
    || /Rendered more hooks/i.test(body)
    || /Try again/i.test(body) && /failed to render/i.test(body);
  step(`${label}: no error boundary`, !bad, bad ? "error boundary visible" : "");
}

async function main() {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  page.on("pageerror", (err) => report.errors.push(String(err)));

  try {
    await login(page);
    step("login", true);

    await page.goto(`${BASE}/ueba/api-keys`, { waitUntil: "domcontentloaded", timeout: 120000 });
    await page.waitForTimeout(5000);
    await assertNoRenderError(page, "M2.2");
    const m22Heading = page.getByRole("heading", { name: /API Key Behavior Analytics/i });
    step("M2.2: page heading visible", await m22Heading.count() > 0);

    await page.goto(`${BASE}/models/exposure`, { waitUntil: "domcontentloaded", timeout: 120000 });
    await page.waitForTimeout(2000);
    await assertNoRenderError(page, "M2.4");
    const guide24 = page.getByRole("button", { name: /^Guide$/i });
    step("M2.4: Guide button present", await guide24.count() > 0);
    await guide24.first().click();
    await page.waitForTimeout(500);
    const guide24Modal = page.getByRole("dialog");
    step("M2.4: Guide modal opens", await guide24Modal.count() > 0);
    const guide24Body = await guide24Modal.innerText();
    step("M2.4: Guide explains purpose", /Model|RAG|objective|exposure/i.test(guide24Body));

    await page.goto(`${BASE}/incidents`, { waitUntil: "domcontentloaded", timeout: 120000 });
    await page.waitForTimeout(3000);
    await assertNoRenderError(page, "M2.6");
    const guide26 = page.getByRole("button", { name: /^Guide$/i });
    step("M2.6: Guide button present", await guide26.count() > 0);
    const alwaysOpenPanel = page.getByText("M2.6 · SOC Guide", { exact: false });
    step("M2.6: guide not always-open sidebar", await alwaysOpenPanel.count() === 0);
    await guide26.first().click();
    await page.waitForTimeout(500);
    const guide26Modal = page.getByRole("dialog");
    step("M2.6: Guide modal opens", await guide26Modal.count() > 0);

    report.ok = true;
    console.log(JSON.stringify(report, null, 2));
  } catch (e) {
    report.error = e.message;
    console.log(JSON.stringify(report, null, 2));
    process.exit(1);
  } finally {
    await browser.close();
  }
}

main();
