/**
 * Phase 0c customer E2E: Overview 30d time lens must complete without 504.
 *
 * Run:
 *   NODE_PATH="$PWD/tests/e2e/node_modules" BASE_URL=http://127.0.0.1:8180 \
 *     /usr/bin/node scripts/playwright_phase0c_30d.mjs
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
const OUT = process.env.E2E_REPORT || "mcp-parallel/findings/phase0c/tf30d_frontend.json";

const HEAVY = [
  "/api/security/soc-kpis/",
  "/api/security/attack-vector-trends/",
  "/api/security/module-kpis/",
  "/api/security/module-trends/",
];

function isHeavy(url) {
  return HEAVY.some((p) => url.includes(p));
}

const report = {
  ok: false,
  pageErrors: [],
  error: null,
  heavies30d: [],
  peakInflight: 0,
};

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
    if (req.method() === "GET" && isHeavy(req.url())) {
      inflight += 1;
      report.peakInflight = Math.max(report.peakInflight, inflight);
    }
  });
  page.on("requestfinished", (req) => {
    if (req.method() === "GET" && isHeavy(req.url())) inflight = Math.max(0, inflight - 1);
  });
  page.on("requestfailed", (req) => {
    if (req.method() === "GET" && isHeavy(req.url())) inflight = Math.max(0, inflight - 1);
  });

  try {
    await login(page);
    await page.goto(`${BASE}/`, { waitUntil: "domcontentloaded", timeout: 120000 });
    await page.getByText("Time lens", { exact: false }).waitFor({ timeout: 60000 });

    const thirty = page.getByRole("button", { name: "30d", exact: true });
    await thirty.click();

    const seen = new Set();
    const deadline = Date.now() + 90000;
    while (seen.size < 4 && Date.now() < deadline) {
      const resp = await page.waitForResponse(
        (r) => r.request().method() === "GET" && isHeavy(r.url()) && r.url().includes("period=30d"),
        { timeout: Math.max(1000, deadline - Date.now()) },
      );
      const url = resp.url();
      const key = HEAVY.find((p) => url.includes(p));
      if (key) seen.add(key);
      report.heavies30d.push({
        url,
        status: resp.status(),
        ok: resp.ok(),
      });
    }

    const statuses = report.heavies30d.map((r) => r.status);
    const all200 = report.heavies30d.length >= 4 && statuses.every((s) => s >= 200 && s < 300);
    const no504 = statuses.every((s) => s !== 504);
    report.ok = all200 && no504 && report.pageErrors.length === 0 && report.peakInflight <= 2;
    report.all200 = all200;
    report.no504 = no504;
    report.seenCount = seen.size;
  } catch (err) {
    report.error = String(err && err.stack ? err.stack : err);
  } finally {
    await browser.close();
    fs.writeFileSync(OUT, JSON.stringify(report, null, 2));
    console.log(
      JSON.stringify({
        ok: report.ok,
        seenCount: report.seenCount,
        peakInflight: report.peakInflight,
        statuses: (report.heavies30d || []).map((r) => r.status),
        error: report.error,
      }),
    );
  }
  process.exit(report.ok ? 0 : 1);
}

main();
