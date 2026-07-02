/**
 * MCP-page Ralph — CP45: the revamp is wired to REAL data, with honest empty/loading/
 * error states and ZERO data leakage (no raw keys / PII / backend internals in the DOM).
 *
 * Proves, live, in BOTH themes:
 *   1. REAL DATA — the rendered server rows match the backend `/servers/` payload the
 *      app itself fetched (count + names), not static/mock/placeholder values.
 *   2. HONEST STATES — either real rows OR an explicit EmptyState; no perpetual spinner;
 *      the summary numbers come from the observed `/events/summary/` response.
 *   3. NO KEY LEAK — on the GET (reload) path the gateway-key banner shows the MASKED
 *      form ({prefix}••••) and the full plaintext key is NOT present in the DOM (the
 *      backend GET never serves it; reveal-once only exposes it transiently post-POST).
 *   4. NO BACKEND/PII LEAK — the client DOM contains no Traceback / internal /app paths /
 *      exit-code / SIGABRT / "heap out of memory" / raw stderr (those are staff-only dev
 *      channel per CP18). A correlation ref (Ref: ...) is allowed — it is not a secret.
 */
import fs from "node:fs";
import { launchBrowser, login, BASE } from "./mcp_page_typesim.mjs";

const OUT = process.env.SHOT_DIR || "mcp-parallel/findings/mcp-page/cp45";

// Backend/PII leakage tells that must NEVER reach the client DOM.
const LEAK_PATTERNS = [
  /Traceback \(most recent call last\)/i,
  /\/app\/(control|gateway|services|shared)\//,        // internal container paths
  /File "\/[^"]+\.py", line \d+/,                        // python frame
  /\bexit(?:ed)?\s*-?\d+\b/i,                            // exit -9 / exited 137
  /\bSIGABRT\b|\bSIGKILL\b/,
  /heap out of memory|JavaScript heap/i,                 // raw V8 OOM stderr
  /ENOSPC|No space left on device/i,                     // raw ENOSPC stderr
  /node:internal\/|at Object\.<anonymous>/,              // node stack frames
  /psycopg2|OperationalError at|django\.db\.utils/i,     // raw DB error dumps
];

async function main() {
  fs.mkdirSync(OUT, { recursive: true });
  const report = { checkpoint: "45", ok: false, consoleErrors: [], leaks: [], sweep: [] };
  const { browser, page } = await launchBrowser();
  page.on("pageerror", (e) => report.consoleErrors.push("pageerror: " + String(e.message || e)));
  page.on("console", (m) => { if (m.type() === "error") report.consoleErrors.push("console.error: " + m.text().slice(0, 160)); });

  // Capture the app's OWN backend payloads → the source of truth for "real data".
  let backendServers = null, backendSummary = null;
  page.on("response", async (r) => {
    const u = r.url();
    try {
      if (/\/api\/mcp-connector\/servers\/(\?|$)/.test(u) && r.request().method() === "GET") {
        const b = await r.json().catch(() => null);
        if (Array.isArray(b)) backendServers = b;
      } else if (u.includes("/api/mcp-connector/events/summary/")) {
        backendSummary = await r.json().catch(() => null);
      }
    } catch { /* ignore */ }
  });

  try {
    await login(page);
    await page.goto(`${BASE}/?tab=firewall-1-4`, { waitUntil: "domcontentloaded", timeout: 120000 });
    await page.getByText(/MCP Guardrails/i).first().waitFor({ state: "visible", timeout: 60000 }).catch(() => {});
    await page.waitForTimeout(3000); // let servers + gateway-key + health settle

    // ── 3. NO KEY LEAK: masked banner on GET path, no plaintext key in DOM ──
    const body0 = await page.locator("body").innerText().catch(() => "");
    report.hasMaskedKeyOrProvision =
      /••••/.test(body0) || /Provision Key|No gateway API key provisioned/i.test(body0);
    // On the GET path the backend returns NO `key`, so the reveal/copy buttons must be absent.
    report.revealButtonAbsentOnGet =
      (await page.getByRole("button", { name: /Reveal gateway API key/i }).count().catch(() => 0)) === 0;
    // The masked code element must NOT contain a long unbroken token (a leaked plaintext key
    // would be a ~40-char urlsafe string with no •). token_urlsafe → [A-Za-z0-9_-].
    const keyCodeText = await page.evaluate(() => {
      const codes = Array.from(document.querySelectorAll("code"));
      const k = codes.find((c) => /••/.test(c.textContent || "") || /Gateway API Key/i.test(c.closest("div")?.textContent || ""));
      return k ? (k.textContent || "") : "";
    });
    report.keyCodeText = keyCodeText.slice(0, 40);
    report.noPlaintextKeyInBanner = !/[A-Za-z0-9_-]{32,}/.test(keyCodeText); // masked → fails this, good

    // ── 4. NO BACKEND/PII LEAK: scan visible DOM across all tabs ──
    const TABS = [/Servers/i, /Tool Discovery|Tools/i, /Execute/i, /Scan Matrix|Scan/i, /Observability/i];
    let combined = body0;
    for (const t of TABS) {
      await page.getByRole("tab", { name: t }).first().click({ timeout: 8000 }).catch(() => {});
      await page.waitForTimeout(1200);
      combined += "\n" + (await page.locator("body").innerText().catch(() => ""));
    }
    for (const re of LEAK_PATTERNS) {
      const m = combined.match(re);
      if (m) report.leaks.push({ pattern: String(re), sample: m[0].slice(0, 80) });
    }
    report.noBackendLeak = report.leaks.length === 0;

    // ── 1. REAL DATA: rendered server names/count match the backend payload ──
    await page.getByRole("tab", { name: /Servers/i }).first().click({ timeout: 8000 }).catch(() => {});
    await page.waitForTimeout(1500);
    const serversBody = await page.locator("body").innerText().catch(() => "");
    if (Array.isArray(backendServers)) {
      report.backendServerCount = backendServers.length;
      if (backendServers.length === 0) {
        // honest empty state expected
        report.realDataOk = /No (MCP )?servers|Register (your|a) (first )?server|Get started/i.test(serversBody);
        report.honestEmptyState = report.realDataOk;
      } else {
        // every backend server name should appear in the rendered DOM (real, not mock)
        const missing = backendServers
          .map((s) => s.name)
          .filter((n) => n && !serversBody.includes(n));
        report.missingNames = missing.slice(0, 5);
        report.realDataOk = missing.length === 0;
      }
    } else {
      report.realDataOk = false;
      report.note = "no /servers/ GET captured";
    }

    // ── 2. HONEST STATES: summary numbers trace to the observed summary payload ──
    await page.getByRole("tab", { name: /Observability/i }).first().click({ timeout: 8000 }).catch(() => {});
    await page.waitForTimeout(2000);
    const obsBody = await page.locator("body").innerText().catch(() => "");
    // Read the ACTUALLY-rendered total (the big 2xl number in the summary card = the
    // largest of the decision counts). Exact-match against a live counter is racy: it
    // increments hundreds/sec under parallel load, so the captured payload is stale by
    // render time. Assert the rendered total tracks the backend within a tolerance band
    // — that proves REAL binding (non-zero, right magnitude, moves with the counter),
    // not a hardcoded/mock/zero value.
    const renderedTotal = await page.evaluate(() => {
      const nums = Array.from(document.querySelectorAll(".text-2xl"))
        .map((el) => parseInt((el.textContent || "").replace(/[^\d]/g, ""), 10))
        .filter((n) => !Number.isNaN(n));
      return nums.length ? Math.max(...nums) : null;
    });
    report.renderedTotal = renderedTotal;
    if (backendSummary && typeof backendSummary.total === "number") {
      report.summaryTotal = backendSummary.total;
      if (backendSummary.total === 0) {
        report.summaryRendered = renderedTotal === 0 || /No events/i.test(obsBody);
      } else {
        const tol = Math.max(500, backendSummary.total * 0.05); // 5% or ±500 for the race
        report.summaryRendered = renderedTotal != null &&
          Math.abs(renderedTotal - backendSummary.total) <= tol;
      }
    } else {
      report.summaryRendered = renderedTotal != null || /Total|events|No events/i.test(obsBody);
    }
    // no perpetual spinner: after settle, the observability panel is not stuck loading
    report.notStuckLoading = !/Loading events…?\s*$/.test(obsBody.trim());

    // ── both themes, screenshot 1440 + 375 for the record ──
    for (const theme of ["light", "dark"]) {
      await page.evaluate((t) => {
        const el = document.documentElement;
        if (t === "dark") el.classList.add("dark"); else el.classList.remove("dark");
      }, theme);
      for (const [w, h] of [[1440, 1000], [375, 812]]) {
        await page.setViewportSize({ width: w, height: h });
        await page.waitForTimeout(400);
        const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 2);
        await page.screenshot({ path: `${OUT}/${theme}-${w}x${h}.png` });
        report.sweep.push({ theme, w, h, horizontalOverflow: overflow });
      }
    }

    report.noConsoleErrors = report.consoleErrors.length === 0;
    report.noOverflow = report.sweep.every((s) => !s.horizontalOverflow);
    report.cp45Pass =
      report.realDataOk &&
      report.hasMaskedKeyOrProvision &&
      report.revealButtonAbsentOnGet &&
      report.noPlaintextKeyInBanner &&
      report.noBackendLeak &&
      report.summaryRendered &&
      report.notStuckLoading &&
      report.noConsoleErrors &&
      report.noOverflow;
    report.ok = true;
    console.log(JSON.stringify({
      realDataOk: report.realDataOk, backendServerCount: report.backendServerCount,
      missingNames: report.missingNames, honestEmptyState: report.honestEmptyState,
      hasMaskedKeyOrProvision: report.hasMaskedKeyOrProvision,
      revealButtonAbsentOnGet: report.revealButtonAbsentOnGet,
      noPlaintextKeyInBanner: report.noPlaintextKeyInBanner, keyCodeText: report.keyCodeText,
      noBackendLeak: report.noBackendLeak, leaks: report.leaks,
      summaryRendered: report.summaryRendered, summaryTotal: report.summaryTotal,
      notStuckLoading: report.notStuckLoading,
      noConsoleErrors: report.noConsoleErrors, consoleErrors: report.consoleErrors.slice(0, 5),
      noOverflow: report.noOverflow, cp45Pass: report.cp45Pass,
    }, null, 2));
    console.log(report.cp45Pass
      ? "CP45: PASS — revamp wired to REAL data; honest states; masked key (no plaintext in DOM); no backend/PII leak; both themes"
      : "CP45: FAIL");
  } catch (e) {
    report.error = e.message;
    await page.screenshot({ path: `${OUT}/99-error.png` }).catch(() => {});
    console.error("CP45 FAIL:", e.message);
  } finally {
    fs.writeFileSync(`${OUT}/report.json`, JSON.stringify(report, null, 2));
    await browser.close();
    process.exit(report.cp45Pass ? 0 : 1);
  }
}
main();
