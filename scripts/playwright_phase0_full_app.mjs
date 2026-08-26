/**
 * Phase 0 full-app: Overview + every firewall tab that shares analytics period.
 *
 * Run:
 *   NODE_PATH="$PWD/tests/e2e/node_modules" BASE_URL=http://127.0.0.1:8180 \
 *     E2E_REPORT=mcp-parallel/findings/phase0c/tf_full_app.json \
 *     /usr/bin/node scripts/playwright_phase0_full_app.mjs
 */
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import path from "node:path";
import fs from "node:fs";

const require = createRequire(import.meta.url);
const HERE = path.dirname(fileURLToPath(import.meta.url));
const { chromium } = require(path.join(HERE, "../tests/e2e/node_modules/playwright"));

const BASE = (process.env.BASE_URL || "http://127.0.0.1:8180").replace(/\/$/, "");
const EMAIL = process.env.TEST_EMAIL || "admin@zeroshield.io";
const PASS = process.env.TEST_PASSWORD || "Adm1n!Pass#2024";
const OUT = process.env.E2E_REPORT || "mcp-parallel/findings/phase0c/tf_full_app.json";

const TABS = [
  "",
  "firewall-1-1",
  "firewall-1-2",
  "firewall-1-3",
  "firewall-1-4",
  "firewall-1-5",
  "firewall-1-6",
  "firewall-1-7",
  "firewall-config",
];

const ANALYTICS = ["/api/security/", "/api/dashboard/"];

const report = {
  ok: false,
  pageErrors: [],
  tabs: [],
  peakInflight: 0,
  error: null,
};

function isAnalytics(url) {
  return ANALYTICS.some((p) => url.includes(p));
}

async function login(page) {
  await page.goto(`${BASE}/login`, { waitUntil: "domcontentloaded", timeout: 120000 });
  await page.locator("#email").fill(EMAIL);
  await page.locator("#password").fill(PASS);
  const [ok] = await Promise.all([
    page.waitForResponse((r) => r.url().includes("/api/auth/token/") && r.request().method() === "POST", {
      timeout: 60000,
    }),
    page.getByRole("button", { name: /^sign in$/i }).click(),
  ]);
  if (!ok.ok()) throw new Error(`login HTTP ${ok.status()}`);
  await page.waitForFunction(() => !location.pathname.startsWith("/login"), { timeout: 30000 });
}

async function main() {
  fs.mkdirSync("mcp-parallel/findings/phase0c", { recursive: true });
  const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH || "/usr/bin/chromium-browser";
  const browser = await chromium.launch({ headless: true, executablePath });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  page.on("pageerror", (e) => report.pageErrors.push(String(e.message || e)));

  let inflight = 0;
  page.on("request", (req) => {
    if (req.method() === "GET" && isAnalytics(req.url())) {
      inflight += 1;
      report.peakInflight = Math.max(report.peakInflight, inflight);
    }
  });
  page.on("requestfinished", (req) => {
    if (req.method() === "GET" && isAnalytics(req.url())) inflight = Math.max(0, inflight - 1);
  });
  page.on("requestfailed", (req) => {
    if (req.method() === "GET" && isAnalytics(req.url())) inflight = Math.max(0, inflight - 1);
  });

  try {
    await login(page);

    for (const tab of TABS) {
      const url = tab ? `${BASE}/?tab=${tab}` : `${BASE}/`;
      const entry = { tab: tab || "overview", url, analytics: [], errors: [] };
      page.once("pageerror", (e) => entry.errors.push(String(e.message || e)));
      await page.goto(url, { waitUntil: "domcontentloaded", timeout: 120000 });
      const periodBtn = page.getByRole("button", { name: "30d", exact: true });
      if (await periodBtn.count()) {
        await periodBtn.first().click();
      }
      const deadline = Date.now() + 45000;
      const seen = [];
      while (Date.now() < deadline) {
        try {
          const resp = await page.waitForResponse(
            (r) => r.request().method() === "GET" && isAnalytics(r.url()),
            { timeout: 4000 },
          );
          seen.push({ url: resp.url(), status: resp.status() });
          if (seen.length >= 8) break;
        } catch {
          break;
        }
      }
      entry.analytics = seen;
      entry.statuses = seen.map((s) => s.status);
      entry.ok =
        entry.errors.length === 0 &&
        seen.every((s) => s.status !== 504 && s.status !== 500);
      report.tabs.push(entry);
    }

    report.ok =
      report.pageErrors.length === 0 &&
      report.tabs.length === TABS.length &&
      report.tabs.every((t) => t.ok);
  } catch (err) {
    report.error = String(err && err.stack ? err.stack : err);
  } finally {
    await browser.close();
    fs.writeFileSync(OUT, JSON.stringify(report, null, 2));
    console.log(
      JSON.stringify({
        ok: report.ok,
        tabs: report.tabs.map((t) => ({ tab: t.tab, n: t.analytics.length, ok: t.ok })),
        peakInflight: report.peakInflight,
        pageErrors: report.pageErrors.length,
        error: report.error,
      }),
    );
  }
  process.exit(report.ok ? 0 : 1);
}

main();
