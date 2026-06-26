/**
 * Frontend UI/UX defect diagnostic (Org-A operator, frontend-only scope).
 * Logs in, walks every firewall tab + core pages, and per page captures:
 *   - console errors/warnings, uncaught pageerrors
 *   - failed network requests (4xx/5xx, requestfailed)
 *   - fatal UI text ("Failed to fetch", stack traces, blank/error states)
 *   - count of interactive controls (to spot empty/dead panels)
 * Writes runs/fe_diagnostic.json + a screenshot per page. Pure diagnosis;
 * fixes happen in frontend/ source.
 */
import { chromium } from "playwright";
import fs from "node:fs";
import path from "node:path";

const BASE = (process.env.BASE_URL || "http://127.0.0.1:8180").replace(/\/$/, "");
const EMAIL = process.env.TEST_EMAIL || "fe-test@local.test";
const PASS = process.env.TEST_PASSWORD || "FeTest!Pass#2026";
const OUTDIR = "runs/fe_diag";
fs.mkdirSync(OUTDIR, { recursive: true });

const TABS = [
  "firewall", "firewall-1-1", "firewall-1-2", "firewall-1-3", "firewall-1-4",
  "firewall-1-5", "firewall-1-6", "firewall-1-7", "firewall-config",
];
// Non-tab routes worth visiting
const ROUTES = ["/", "/settings", "/profile"];

async function login(page) {
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle", timeout: 90000 });
  await page.locator("#email").fill(EMAIL);
  await page.locator("#password").fill(PASS);
  await Promise.all([
    page.waitForURL(/tab=|\/$/, { timeout: 60000 }),
    page.getByRole("button", { name: /^sign in$/i }).click(),
  ]);
}

function attachCollectors(page, sink) {
  page.on("console", (m) => {
    const t = m.type();
    if (t === "error" || t === "warning") sink.console.push({ type: t, text: m.text().slice(0, 300) });
  });
  page.on("pageerror", (e) => sink.pageerrors.push(String(e).slice(0, 300)));
  page.on("requestfailed", (r) =>
    sink.netfail.push({ url: r.url().slice(0, 160), err: r.failure()?.errorText || "failed" }));
  page.on("response", (r) => {
    const s = r.status();
    if (s >= 400) sink.http.push({ url: r.url().slice(0, 160), status: s });
  });
}

async function main() {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  const report = { base: BASE, when: new Date().toISOString(), pages: [], summary: {} };

  await login(page);

  const visit = async (label, url) => {
    const sink = { console: [], pageerrors: [], netfail: [], http: [] };
    attachCollectors(page, sink);
    let fatal = null, controls = 0, bodyLen = 0;
    try {
      await page.goto(url, { waitUntil: "networkidle", timeout: 90000 });
      await page.waitForTimeout(1200);
      const body = await page.locator("body").innerText().catch(() => "");
      bodyLen = body.length;
      if (/Failed to fetch|Cannot read propert|undefined is not|TypeError|DisallowedHost|Something went wrong|Application error/i.test(body))
        fatal = body.split("\n").find((l) => /Failed to fetch|TypeError|Cannot read|Something went wrong|Application error/i.test(l))?.slice(0, 200) || "fatal text";
      controls = await page.locator("button, a[href], input, select, textarea, [role=button], [role=tab]").count();
      await page.screenshot({ path: path.join(OUTDIR, `${label}.png`) }).catch(() => {});
    } catch (e) {
      fatal = `navigation error: ${String(e).slice(0, 160)}`;
    }
    // dedupe + downgrade noisy 4xx that are expected probes
    const httpErr = sink.http.filter((h) => !/\/health|\/healthz/.test(h.url));
    page.removeAllListeners("console");
    page.removeAllListeners("pageerror");
    page.removeAllListeners("requestfailed");
    page.removeAllListeners("response");
    report.pages.push({
      label, url, bodyLen, controls, fatal,
      console: sink.console.slice(0, 12),
      pageerrors: sink.pageerrors.slice(0, 8),
      netfail: sink.netfail.slice(0, 12),
      http: httpErr.slice(0, 12),
    });
    const bad = (fatal ? 1 : 0) + sink.pageerrors.length + sink.console.length + httpErr.length + sink.netfail.length;
    console.log(`${bad ? "⚠️ " : "✓ "}${label.padEnd(16)} controls=${controls} bodyLen=${bodyLen} ` +
      `console=${sink.console.length} pageerr=${sink.pageerrors.length} http4xx5xx=${httpErr.length} netfail=${sink.netfail.length}${fatal ? " FATAL:" + fatal : ""}`);
  };

  for (const t of TABS) await visit(t, `${BASE}/?tab=${t}`);
  for (const r of ROUTES) await visit("route" + r.replace(/\//g, "_"), `${BASE}${r}`);

  report.summary = {
    pages: report.pages.length,
    pagesWithIssues: report.pages.filter((p) => p.fatal || p.pageerrors.length || p.console.length || p.http.length || p.netfail.length).length,
    totalConsole: report.pages.reduce((a, p) => a + p.console.length, 0),
    totalPageErrors: report.pages.reduce((a, p) => a + p.pageerrors.length, 0),
    totalHttp: report.pages.reduce((a, p) => a + p.http.length, 0),
  };
  fs.writeFileSync("runs/fe_diagnostic.json", JSON.stringify(report, null, 2));
  console.log("\nSUMMARY", JSON.stringify(report.summary));
  await browser.close();
}
main().catch((e) => { console.error("DIAG FATAL", e); process.exit(1); });
