/**
 * Verify the popup-blocked → SAME-TAB OAuth fallback (Cursor/Electron browsers).
 *
 * Stubs window.open to return null (popup blocked) and captures
 * window.location.assign, then clicks Authorize/Re-authorize on the Linear
 * (manual OAuth) card and asserts:
 *   - the app navigates SAME-TAB to the provider authorize URL (mcp.linear.app)
 *   - localStorage "mcp_oauth_pending" is stashed (drives auto-sync on return)
 *
 * System chromium, headless. Run:
 *   BASE_URL=http://127.0.0.1:8180 node scripts/playwright_mcp_oauth_popup_fallback.mjs
 */
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import path from "node:path";
import fs from "node:fs";
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);
const { chromium } = require(path.resolve(__dirname, "../tests/e2e/node_modules/playwright"));

const BASE = (process.env.BASE_URL || "http://127.0.0.1:8180").replace(/\/$/, "");
const EMAIL = process.env.TEST_EMAIL || "admin@zeroshield.io";
const PASS = process.env.TEST_PASSWORD || "Adm1n!Pass#2024";
const CHROME = process.env.CHROME_PATH || "/usr/bin/chromium-browser";
const SHOT_DIR = process.env.SHOT_DIR || "mcp-parallel/findings/oauth-fallback";
const CARD = "Linear (manual OAuth)";
const report = { ok: false, asserts: [], findings: {}, error: null };
const A = (n, c, d) => { report.asserts.push({ name: n, pass: !!c, detail: d }); return !!c; };

const browser = await chromium.launch({ executablePath: CHROME, headless: true, args: ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"] });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 1100 } });
// Popup-blocked emulation + capture the mcp_oauth_pending stash BEFORE the
// same-tab navigation tears down the app context.
const stashed = [];
await ctx.exposeFunction("__recordStash", (v) => { stashed.push(v); });
await ctx.addInitScript(() => {
  window.open = () => null;
  const origSet = window.localStorage.setItem.bind(window.localStorage);
  window.localStorage.setItem = (k, v) => {
    if (k === "mcp_oauth_pending") { try { window.__recordStash(v); } catch (_) { /* ignore */ } }
    return origSet(k, v);
  };
});
const page = await ctx.newPage();

// Capture the same-tab navigation to the provider WITHOUT actually loading it
// (abort mcp.linear.app so the page stays on the app origin → localStorage readable).
const navAttempts = [];
page.on("request", (r) => { if (r.isNavigationRequest() && /mcp\.linear\.app/i.test(r.url())) navAttempts.push(r.url()); });
await page.route("https://mcp.linear.app/**", (route) => route.abort());

try {
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle", timeout: 120000 });
  await page.locator("#email").fill(EMAIL);
  await page.locator("#password").fill(PASS);
  await Promise.all([
    page.waitForResponse((r) => r.url().includes("/api/auth/token/") && r.request().method() === "POST"),
    page.getByRole("button", { name: /^sign in$/i }).click(),
  ]);
  await page.waitForFunction(() => !location.pathname.startsWith("/login"), { timeout: 30000 });
  await page.goto(`${BASE}/?tab=firewall-1-4`, { waitUntil: "domcontentloaded", timeout: 120000 });
  await page.locator("text=/MCP Guardrails/i").first().waitFor({ state: "visible", timeout: 60000 });
  // Wait for the async servers list to render the target card.
  await page.locator("h4", { hasText: CARD }).first().waitFor({ state: "visible", timeout: 30000 });

  // Find the Authorize/Re-authorize button inside the Linear (manual OAuth) card.
  const btn = await page.evaluateHandle((want) => {
    for (const h of document.querySelectorAll("h4")) {
      if (!h.textContent.includes(want)) continue;
      let node = h, card = null;
      for (let i = 0; i < 12 && node; i++) { node = node.parentElement; if (!node) break;
        if ([...node.querySelectorAll("button")].some((b) => /Delete server/i.test(b.getAttribute("aria-label") || ""))) { card = node; break; } }
      if (!card) return null;
      return [...card.querySelectorAll("button")].find((b) => /^Authorize$|^Re-authorize$/i.test((b.textContent || "").trim())) || null;
    }
    return null;
  }, CARD);
  const el = btn.asElement();
  A("card + authorize button present", !!el, null);
  if (el) {
    await el.click();
    // wait for oauth/authorize round-trip (Linear discovery+DCR) + the same-tab
    // fallback to attempt navigation to the provider (captured + aborted above).
    await page.waitForFunction(() => true, { timeout: 100 }).catch(() => {});
    for (let i = 0; i < 90 && navAttempts.length === 0; i++) await page.waitForTimeout(500);
    let pending = null;
    try { pending = JSON.parse(stashed[0] || "null"); } catch { pending = null; }
    report.findings.navAttempts = navAttempts;
    report.findings.pending = pending;
    A("same-tab navigation to provider authorize URL", navAttempts.some((u) => /mcp\.linear\.app\/authorize/i.test(u)), navAttempts);
    A("mcp_oauth_pending stashed for auto-sync-on-return", !!(pending && pending.id), pending);
    A("did NOT show 'Popup blocked' error", !(await page.locator("text=/Popup blocked/i").count()), null);
  }
  fs.mkdirSync(SHOT_DIR, { recursive: true });
  await page.screenshot({ path: `${SHOT_DIR}/01-after-fallback.png`, fullPage: true }).catch(() => {});
  report.ok = report.asserts.every((a) => a.pass);
  console.log(JSON.stringify(report, null, 2));
  console.log(report.ok ? "POPUP-FALLBACK: ALL PASS" : "POPUP-FALLBACK: FAILURES");
} catch (e) {
  report.error = e.message; console.error("FAIL", e.stack || e.message);
} finally {
  await browser.close();
  process.exit(report.ok ? 0 : 1);
}
