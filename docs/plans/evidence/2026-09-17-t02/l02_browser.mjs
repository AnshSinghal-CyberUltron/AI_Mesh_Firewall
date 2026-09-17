/**
 * T02 L02 Playwright smoke against baked nginx :8180.
 * Real login form. Never logs passwords.
 */
import { createRequire } from "node:module";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, "../../../..");
const require = createRequire(import.meta.url);
const { chromium } = require(path.join(REPO, "tests/e2e/node_modules/playwright"));

const BASE = (process.env.BASE_URL || "http://127.0.0.1:8180").replace(/\/$/, "");
const EMAIL = process.env.TEST_EMAIL;
const PASS = process.env.TEST_PASSWORD;
const OUT = process.env.E2E_REPORT || path.join(HERE, "l02_browser.json");
const SHOTS = process.env.SHOT_DIR || path.join(HERE, "shots");

const report = {
  task: "T02",
  base: BASE,
  email: EMAIL,
  started_at: new Date().toISOString(),
  html_has_vite_client: null,
  hashed_js: null,
  ok: false,
  steps: [],
  asserts: [],
  pageErrors: [],
  console_errors: [],
  error: null,
};

function assert(cond, label) {
  report.asserts.push({ label, pass: !!cond });
  if (!cond) throw new Error(`ASSERT FAILED: ${label}`);
}

async function shot(page, name) {
  fs.mkdirSync(SHOTS, { recursive: true });
  await page.screenshot({ path: path.join(SHOTS, `${name}.png`), fullPage: true });
}

async function main() {
  if (!EMAIL || !PASS) throw new Error("TEST_EMAIL and TEST_PASSWORD required");
  fs.mkdirSync(SHOTS, { recursive: true });
  const executablePath =
    process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH || "/usr/bin/chromium-browser";
  const browser = await chromium.launch({
    headless: true,
    executablePath,
    args: ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
  });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  page.on("pageerror", (e) => report.pageErrors.push(String(e.message || e).slice(0, 400)));
  page.on("console", (msg) => {
    if (msg.type() === "error") report.console_errors.push(msg.text().slice(0, 400));
  });

  try {
    await page.goto(`${BASE}/login`, { waitUntil: "networkidle", timeout: 120000 });
    const html = await page.content();
    report.html_has_vite_client = html.includes("@vite/client") || html.includes("/@vite/");
    report.hashed_js = (html.match(/\/assets\/index-[A-Za-z0-9_-]+\.js/) || [null])[0];
    report.server = await page.evaluate(async () => {
      const r = await fetch("/");
      return r.headers.get("server");
    }).catch(() => null);
    await shot(page, "01-login");
    assert(report.html_has_vite_client === false, "baked console has no Vite client");
    assert(Boolean(report.hashed_js), "hashed JS asset present");

    let loginRes = null;
    for (let attempt = 0; attempt < 6; attempt++) {
      await page.locator("#email").fill(EMAIL);
      await page.locator("#password").fill(PASS);
      const [res] = await Promise.all([
        page.waitForResponse(
          (r) => r.url().includes("/api/auth/token/") && r.request().method() === "POST",
          { timeout: 60000 },
        ),
        page.getByRole("button", { name: /^sign in$/i }).click(),
      ]);
      loginRes = res;
      if (res.ok()) break;
      if (res.status() === 429) {
        const ra = parseInt((await res.headerValue("retry-after").catch(() => null)) || "15", 10);
        await page.waitForTimeout(Math.min((Number.isFinite(ra) ? ra : 15) + 2, 65) * 1000);
        continue;
      }
      throw new Error(`Login HTTP ${res.status()}`);
    }
    report.login_status = loginRes.status();
    await page.waitForFunction(() => !location.pathname.startsWith("/login"), { timeout: 60000 });
    report.steps.push("login-ok");
    await shot(page, "02-after-login");

    await page.goto(`${BASE}/?tab=firewall-config`, { waitUntil: "domcontentloaded", timeout: 120000 });
    await page.getByText("Enforcement Mode", { exact: false }).first().waitFor({ state: "visible", timeout: 60000 });
    report.steps.push("config-page-visible");
    await shot(page, "03-config-loaded");

    const chatUrls = [];
    page.on("response", (r) => {
      if (r.url().includes("/v1/chat/completions")) {
        chatUrls.push({ url: r.url(), status: r.status() });
      }
    });

    await page.goto(`${BASE}/?tab=attack-simulator`, { waitUntil: "domcontentloaded", timeout: 120000 }).catch(() => null);
    const prompt = page.getByPlaceholder(/prompt|message|ask/i).first();
    const visible = await prompt.isVisible().catch(() => false);
    report.simulator_prompt_visible = visible;
    if (visible) {
      await prompt.fill("Reply with the single word pong.");
      const send = page.getByRole("button", { name: /send|run|submit|attack/i }).first();
      if (await send.isVisible().catch(() => false)) {
        await send.click();
        await page.waitForTimeout(8000);
      }
    }
    report.chat_network = chatUrls;
    await shot(page, "04-simulator");

    report.ok = report.asserts.every((a) => a.pass) && report.pageErrors.length === 0;
    report.finished_at = new Date().toISOString();
  } catch (err) {
    report.error = String(err && err.message ? err.message : err).slice(0, 800);
    report.ok = false;
    await shot(page, "99-error").catch(() => {});
  } finally {
    fs.writeFileSync(OUT, JSON.stringify(report, null, 2));
    await browser.close();
  }
  if (!report.ok) process.exit(2);
}

main();
