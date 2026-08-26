/**
 * Phase 0b frontend gates T-F3 / T-F4 against live Vite :8180.
 *
 * T-F4: cold overview load issues ≤ 2 concurrent heavy analytics requests.
 * T-F3: changing the time lens aborts in-flight requests for the previous period.
 * Honest error list: Module Trends is one of the four named jobs.
 *
 * Run:
 *   NODE_PATH="$PWD/tests/e2e/node_modules" BASE_URL=http://127.0.0.1:8180 \
 *     /usr/bin/node scripts/playwright_phase0b_frontend.mjs
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
const OUT = process.env.E2E_REPORT || "mcp-parallel/findings/phase0b/tf34_frontend.json";

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
  tf3: {},
  tf4: {},
  pageErrors: [],
  error: null,
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
  fs.mkdirSync("mcp-parallel/findings/phase0b", { recursive: true });
  const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH || "/usr/bin/chromium-browser";
  const browser = await chromium.launch({ headless: true, executablePath });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  page.on("pageerror", (e) => report.pageErrors.push(String(e.message || e)));

  try {
    await login(page);

    let inflight = 0;
    let peak = 0;
    let inflight24 = 0;
    let peak24 = 0;
    const started = [];
    const aborted = [];
    page.on("request", (req) => {
      if (req.method() !== "GET" || !isHeavy(req.url())) return;
      inflight += 1;
      peak = Math.max(peak, inflight);
      if (req.url().includes("period=24h")) {
        inflight24 += 1;
        peak24 = Math.max(peak24, inflight24);
      }
      started.push({ url: req.url(), at: Date.now() });
    });
    page.on("requestfinished", (req) => {
      if (req.method() !== "GET" || !isHeavy(req.url())) return;
      inflight = Math.max(0, inflight - 1);
      if (req.url().includes("period=24h")) inflight24 = Math.max(0, inflight24 - 1);
    });
    page.on("requestfailed", (req) => {
      if (req.method() !== "GET" || !isHeavy(req.url())) return;
      inflight = Math.max(0, inflight - 1);
      if (req.url().includes("period=24h")) inflight24 = Math.max(0, inflight24 - 1);
      aborted.push({
        url: req.url(),
        failure: req.failure()?.errorText || "",
      });
    });

    // Delay heavy responses so concurrency + abort are observable.
    await page.route("**/api/security/**", async (route) => {
      const url = route.request().url();
      if (isHeavy(url)) {
        await new Promise((r) => setTimeout(r, 2500));
      }
      await route.continue();
    });

    await page.goto(`${BASE}/`, { waitUntil: "domcontentloaded", timeout: 120000 });
    await page.getByText("Time lens", { exact: false }).waitFor({ timeout: 60000 });
    await page.waitForRequest(
      (req) => req.method() === "GET" && isHeavy(req.url()) && req.url().includes("period=24h"),
      { timeout: 60000 },
    );
    // Let the second concurrent 24h slot start before we abort.
    await page.waitForTimeout(400);

    const seven = page.getByRole("button", { name: "7d", exact: true });
    await seven.click();
    await page.waitForTimeout(8000);

    const cold24 = started.filter((s) => s.url.includes("period=24h"));
    const after7 = started.filter((s) => s.url.includes("period=7d"));
    const aborted24 = aborted.filter((s) => s.url.includes("period=24h"));

    report.tf4 = {
      peak: peak24,
      overallPeak: peak,
      cap: 2,
      pass: peak24 <= 2 && peak24 >= 1,
      startedCount: started.length,
      urls: [...new Set(started.map((s) => s.url))],
    };
    report.tf3 = {
      cold24Count: cold24.length,
      after7Count: after7.length,
      aborted24Count: aborted24.length,
      aborted,
      pass: aborted24.length >= 1 && after7.length >= 1,
    };

    report.ok = report.tf3.pass && report.tf4.pass && report.pageErrors.length === 0;
  } catch (err) {
    report.error = String(err && err.stack ? err.stack : err);
  } finally {
    await browser.close();
    fs.writeFileSync(OUT, JSON.stringify(report, null, 2));
    console.log(JSON.stringify({ ok: report.ok, tf3: report.tf3.pass, tf4: report.tf4.pass, peak24: report.tf4.peak, error: report.error }));
  }
  process.exit(report.ok ? 0 : 1);
}

main();
