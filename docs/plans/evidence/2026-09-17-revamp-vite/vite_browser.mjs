/**
 * Playwright smoke: Vite (not baked nginx) on :8180. Real login form.
 * Never logs passwords. JSON-only evidence (no committed screenshots).
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
const OUT = process.env.E2E_REPORT || path.join(HERE, "vite_browser.json");

const report = {
  task: "revamp-vite",
  base: BASE,
  email: EMAIL ? `${EMAIL.split("@")[0]}@…` : null,
  started_at: new Date().toISOString(),
  html_has_vite_client: null,
  hashed_js: null,
  server: null,
  login_http: null,
  after_login_path: null,
  config_http: null,
  ok: false,
  asserts: [],
  pageErrors: [],
  console_errors: [],
  error: null,
};

function assert(cond, label) {
  report.asserts.push({ label, pass: !!cond });
  if (!cond) throw new Error(`ASSERT FAILED: ${label}`);
}

async function main() {
  if (!EMAIL || !PASS) throw new Error("TEST_EMAIL and TEST_PASSWORD required");
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
  const configStatuses = [];
  page.on("response", (r) => {
    if (r.url().includes("/api/firewall/config") && r.request().method() === "GET") {
      configStatuses.push(r.status());
    }
  });

  try {
    await page.goto(`${BASE}/login`, { waitUntil: "domcontentloaded", timeout: 120000 });
    await page.waitForSelector("#email", { timeout: 120000 });
    const html = await page.content();
    report.html_has_vite_client = html.includes("@vite/client") || html.includes("/@vite/");
    report.hashed_js = (html.match(/\/assets\/index-[A-Za-z0-9_-]+\.js/) || [null])[0];
    report.server = await page
      .evaluate(async () => {
        const r = await fetch("/");
        return r.headers.get("server");
      })
      .catch(() => null);

    assert(report.html_has_vite_client === true, "Vite client present on :8180");
    assert(!report.hashed_js, "baked hashed /assets/index-*.js absent");

    let loginRes = null;
    for (let attempt = 0; attempt < 8; attempt++) {
      await page.locator("#email").fill(EMAIL);
      await page.locator("#password").fill(PASS);
      const [res] = await Promise.all([
        page.waitForResponse((r) => r.url().includes("/api/") && r.request().method() === "POST", {
          timeout: 60000,
        }).catch(() => null),
        page.locator('button[type="submit"]').click(),
      ]);
      loginRes = res;
      if (res && res.status() === 200) break;
      if (res && res.status() === 429) {
        await page.waitForTimeout(15000);
        continue;
      }
      await page.waitForTimeout(2000);
    }
    report.login_http = loginRes ? loginRes.status() : null;
    assert(report.login_http === 200, "login HTTP 200");

    await page.waitForFunction(() => !window.location.pathname.includes("/login"), {
      timeout: 60000,
    });
    report.after_login_path = await page.evaluate(() => window.location.pathname);

    const waitUntil = Date.now() + 45000;
    while (!configStatuses.length && Date.now() < waitUntil) {
      await page.waitForTimeout(250);
    }
    if (!configStatuses.length) {
      report.config_http = await page.evaluate(async () => {
        const token =
          window.localStorage.getItem("auth_access") ||
          window.localStorage.getItem("access") ||
          "";
        const r = await fetch("/api/firewall/config/", {
          headers: token ? { Authorization: `Bearer ${token}` } : {},
        });
        return r.status;
      });
    } else {
      report.config_http = configStatuses[configStatuses.length - 1];
    }
    report.config_statuses = configStatuses;
    assert(report.config_http === 200, "firewall config GET 200 after login");
    assert(report.pageErrors.length === 0, "no page errors");

    report.ok = report.asserts.every((a) => a.pass);
  } catch (e) {
    report.error = String(e && e.message ? e.message : e).slice(0, 800);
    report.ok = false;
  } finally {
    report.finished_at = new Date().toISOString();
    fs.mkdirSync(path.dirname(OUT), { recursive: true });
    fs.writeFileSync(OUT, JSON.stringify(report, null, 2));
    await browser.close();
  }
  if (!report.ok) process.exit(1);
}

main();
