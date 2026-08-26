/**
 * Phase 0a frontend gates T-F1 / T-F2 / T-F5 against live Vite :8180.
 *
 * T-F1: hide the document, wait HIDDEN_MS (default 10 min), assert zero analytics polls.
 * T-F2: burst 15 WS enforcement notifications; refetch count is bounded (debounce).
 * T-F5: hung analytics response aborts around 30s, not nginx 300s.
 *
 * Run:
 *   NODE_PATH="$PWD/tests/e2e/node_modules" BASE_URL=http://127.0.0.1:8180 \
 *     node scripts/playwright_phase0a_frontend.mjs
 */
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { execFileSync } from "node:child_process";
import fs from "node:fs";

const require = createRequire(import.meta.url);
const HERE = path.dirname(fileURLToPath(import.meta.url));
const { chromium } = require(path.join(HERE, "../tests/e2e/node_modules/playwright"));

const BASE = (process.env.BASE_URL || "http://127.0.0.1:8180").replace(/\/$/, "");
const EMAIL = process.env.TEST_EMAIL || "admin@zeroshield.io";
const PASS = process.env.TEST_PASSWORD || "Adm1n!Pass#2024";
const HIDDEN_MS = Number(process.env.HIDDEN_MS || 600_000);
const OUT = process.env.E2E_REPORT || "mcp-parallel/findings/phase0a/tf_frontend.json";
const CONTAINER = process.env.CONTROL_CONTAINER || "ai_mesh_firewall-control-1";

const report = {
  ok: false,
  tf1: {},
  tf2: {},
  tf5: {},
  pageErrors: [],
  error: null,
};

function isAnalytics(url) {
  return url.includes("/api/security/") || url.includes("/api/dashboard/");
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

async function hideDocument(page) {
  await page.evaluate(() => {
    Object.defineProperty(document, "hidden", { configurable: true, get: () => true });
    Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "hidden" });
    document.dispatchEvent(new Event("visibilitychange"));
  });
}

function burstNotify() {
  const py = `
import os, django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "main_app.settings")
django.setup()
from ws.notify import send_enforcement_notification
from auth.models import UserProfile
from django.contrib.auth import get_user_model
User = get_user_model()
u = User.objects.filter(email__iexact="admin@zeroshield.io").first()
org_id = u.profile.organization_id
for i in range(15):
    send_enforcement_notification({"type": "enforcement_event", "id": f"tf2-{i}", "action": "monitor"}, organization_id=org_id)
print(org_id)
`;
  return execFileSync(
    "docker",
    [
      "exec",
      "-e",
      "PYTHONPATH=/app/shared:/app/control/ai_mesh_control",
      CONTAINER,
      "python",
      "-c",
      py,
    ],
    { encoding: "utf8", timeout: 60000 },
  );
}

async function main() {
  fs.mkdirSync("mcp-parallel/findings/phase0a", { recursive: true });
  const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH || "/usr/bin/chromium-browser";
  const browser = await chromium.launch({ headless: true, executablePath });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  page.on("pageerror", (e) => report.pageErrors.push(String(e.message || e)));

  try {
    await login(page);
    await page.goto(`${BASE}/`, { waitUntil: "domcontentloaded", timeout: 120000 });
    await page.waitForTimeout(8000);

    // ───── T-F5: hung analytics must abort around 30s, not nginx 300s ─────
    let hungStartedAt = 0;
    const failed = [];
    const onFailed = (req) => {
      if (req.url().includes("/api/security/soc-kpis")) {
        failed.push({ url: req.url(), at: Date.now() - hungStartedAt, failure: req.failure()?.errorText });
      }
    };
    page.on("requestfailed", onFailed);
    await page.route("**/api/security/soc-kpis**", async (route) => {
      if (!hungStartedAt) hungStartedAt = Date.now();
      await new Promise((r) => setTimeout(r, 180_000));
      try {
        await route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
      } catch {
        /* fetch aborted */
      }
    });
    await page.waitForTimeout(45_000);
    page.off("requestfailed", onFailed);
    await page.unroute("**/api/security/soc-kpis**");
    const tf5Ms = failed[0]?.at ?? null;
    report.tf5 = {
      aborted: failed.length > 0,
      ms: tf5Ms,
      wellBefore300s: tf5Ms != null && tf5Ms >= 20_000 && tf5Ms < 60_000,
      failed,
    };

    // ───── T-F2: burst notify while visible ─────
    const tf2Hits = [];
    const onTf2 = (req) => {
      if (req.method() === "GET" && isAnalytics(req.url())) tf2Hits.push(req.url());
    };
    page.on("request", onTf2);
    const before = tf2Hits.length;
    burstNotify();
    await page.waitForTimeout(5000);
    page.off("request", onTf2);
    const extra = tf2Hits.length - before;
    report.tf2 = { extraAnalyticsGets: extra, bounded: extra <= 4, urls: tf2Hits.slice(before) };

    // ───── T-F1: hide, wait, zero analytics ─────
    const tf1Hits = [];
    const onTf1 = (req) => {
      if (req.method() === "GET" && isAnalytics(req.url())) tf1Hits.push({ url: req.url(), at: Date.now() });
    };
    await hideDocument(page);
    await page.waitForTimeout(500);
    const hideAt = Date.now();
    page.on("request", onTf1);
    await page.waitForTimeout(HIDDEN_MS);
    page.off("request", onTf1);
    report.tf1 = {
      hiddenMs: HIDDEN_MS,
      analyticsGets: tf1Hits.length,
      zero: tf1Hits.length === 0,
      hits: tf1Hits,
      hideAt,
    };

    report.ok =
      report.tf1.zero &&
      report.tf2.bounded &&
      report.tf5.wellBefore300s &&
      report.pageErrors.length === 0;
  } catch (err) {
    report.error = String(err?.stack || err);
    report.ok = false;
  } finally {
    await browser.close();
    fs.writeFileSync(OUT, JSON.stringify(report, null, 2));
    console.log(JSON.stringify({ ok: report.ok, tf1: report.tf1, tf2: report.tf2, tf5: report.tf5, error: report.error }, null, 2));
    if (!report.ok) process.exit(1);
  }
}

main();
