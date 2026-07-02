/**
 * P4 item #15 — Playwright VERIFY of the B2 fix.
 *
 * A freshly-registered HTTP oauth server must show a DISTINCT "Pending
 * authorization" state, never a normal-looking card with a grey "Unknown"
 * status + "0 tools". Asserts:
 *   B2-1 card shows a distinct "Pending authorization" badge
 *   B2-2 card does NOT show the misleading "N tools" count (shows "Authorize to
 *        load tools" instead) and does NOT read as "Connected"/"Unknown"
 *   B2-3 the Sync button is DISABLED (syncBlockedForAuth) pre-auth
 *   B2-4 exactly ONE Authorize button
 *   B2-5 backend tools_count is 0 pre-auth (no premature tools)
 *
 * System chromium (bundled unsupported on ubuntu26.04). Run:
 *   BASE_URL=http://127.0.0.1:8180 node scripts/playwright_mcp_b2_verify.mjs
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
const SHOT_DIR = process.env.SHOT_DIR || "mcp-parallel/findings/b2-verify";
const OUT = process.env.E2E_REPORT || `${SHOT_DIR}/report.json`;
const NAME = "b2-verify-pending";
const URL = "https://example.com/mcp";

const report = { story: "P4#15", bug: "B2", base: BASE, ok: false, asserts: [], findings: {}, error: null };
const A = (name, cond, detail) => { report.asserts.push({ name, pass: !!cond, detail }); return !!cond; };
function mkdir() { fs.mkdirSync(SHOT_DIR, { recursive: true }); }
async function shot(page, n) { mkdir(); const p = `${SHOT_DIR}/${n}.png`; await page.screenshot({ path: p, fullPage: true }); return p; }

async function login(page) {
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle", timeout: 120000 });
  await page.locator("#email").fill(EMAIL);
  await page.locator("#password").fill(PASS);
  const [res] = await Promise.all([
    page.waitForResponse((r) => r.url().includes("/api/auth/token/") && r.request().method() === "POST"),
    page.getByRole("button", { name: /^sign in$/i }).click(),
  ]);
  if (!res.ok()) throw new Error(`Login failed: ${res.status()}`);
  await page.waitForFunction(() => !location.pathname.startsWith("/login"), { timeout: 30000 });
}
async function deleteByName(page, want) {
  return page.evaluate(async (w) => {
    const tok = (localStorage.getItem("auth_access") || "").replace(/^"|"$/g, "");
    const h = { Authorization: `Bearer ${tok}`, "Content-Type": "application/json" };
    const list = await (await fetch("/api/mcp-connector/servers/", { headers: h })).json().catch(() => []);
    const rows = Array.isArray(list) ? list : (list.results || []);
    let n = 0;
    for (const s of rows) if ((s.name || "").includes(w)) { await fetch(`/api/mcp-connector/servers/${s.id}/`, { method: "DELETE", headers: h }); n++; }
    return n;
  }, want);
}
async function cardFacts(page, want) {
  return page.evaluate((w) => {
    for (const h of document.querySelectorAll("h4")) {
      if (!h.textContent.replace(/\s+/g, " ").includes(w)) continue;
      let node = h, card = null;
      for (let i = 0; i < 12 && node; i++) { node = node.parentElement; if (!node) break;
        if ([...node.querySelectorAll("button")].some((b) => /Delete server/i.test(b.getAttribute("aria-label") || ""))) { card = node; break; } }
      if (!card) return null;
      const txt = card.innerText;
      const syncBtn = [...card.querySelectorAll("button")].find((b) => /Sync tools/i.test(b.getAttribute("aria-label") || ""));
      const authBtns = [...card.querySelectorAll("button")].filter((b) => /^Authorize$|^Re-authorize$/i.test((b.textContent || "").trim()));
      return {
        cardSnippet: txt.slice(0, 1200),
        hasPendingAuthBadge: /pending authorization/i.test(txt),
        hasAuthorizeToLoadTools: /authorize to load tools/i.test(txt),
        showsNumericToolsCount: /\b\d+\s+tools\b/i.test(txt),
        readsConnected: /\bConnected\b/i.test(h.textContent),
        readsUnknownStatus: /\bUnknown\b/i.test(h.textContent),
        authorizeCount: authBtns.length,
        syncDisabled: syncBtn ? syncBtn.disabled : null,
      };
    }
    return null;
  }, want);
}
async function apiTools(page, name) {
  return page.evaluate(async (nm) => {
    const tok = (localStorage.getItem("auth_access") || "").replace(/^"|"$/g, "");
    const h = { Authorization: `Bearer ${tok}` };
    const list = await (await fetch("/api/mcp-connector/servers/", { headers: h })).json().catch(() => []);
    const rows = Array.isArray(list) ? list : (list.results || []);
    const row = rows.find((s) => (s.name || "").includes(nm));
    return row ? { tools_count: row.tools_count, oauth_authorized: row.oauth_authorized, connection_status: row.connection_status } : null;
  }, name);
}

async function main() {
  mkdir();
  const browser = await chromium.launch({ executablePath: CHROME, headless: true, args: ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"] });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1200 } });
  const page = await context.newPage();
  try {
    await login(page);
    await page.goto(`${BASE}/?tab=firewall-1-4`, { waitUntil: "domcontentloaded", timeout: 120000 });
    await page.locator("text=/MCP Guardrails/i").first().waitFor({ state: "visible", timeout: 60000 });
    await deleteByName(page, NAME);
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.locator("text=/MCP Guardrails/i").first().waitFor({ state: "visible", timeout: 60000 });

    // Register a fresh HTTP oauth server via the modal.
    await page.getByRole("button", { name: /^Register Server$/i }).first().click();
    await page.getByText("Register MCP Server", { exact: false }).first().waitFor({ state: "visible", timeout: 15000 });
    await page.getByPlaceholder("my-mcp-server").fill(NAME);
    await page.getByPlaceholder("https://my-server.example.com/mcp").fill(URL);
    await page.getByLabel("Transport").selectOption("streamable-http");
    await page.getByLabel("Upstream authentication type").selectOption("oauth");
    const [createRes] = await Promise.all([
      page.waitForResponse((r) => r.url().includes("/api/mcp-connector/servers/") && r.request().method() === "POST", { timeout: 60000 }),
      page.getByRole("button", { name: /^Register$/, exact: true }).click(),
    ]);
    report.findings.registerStatus = createRes.status();
    await page.waitForTimeout(1500);

    const card = await cardFacts(page, NAME);
    const back = await apiTools(page, NAME);
    report.findings.card = card;
    report.findings.backend = back;
    await shot(page, "01-pending-card");

    A("B2-1 distinct 'Pending authorization' badge shown", card && card.hasPendingAuthBadge === true, card);
    A("B2-2a shows 'Authorize to load tools' (not a numeric tools count)", card && card.hasAuthorizeToLoadTools === true && card.showsNumericToolsCount === false, { hasHint: card?.hasAuthorizeToLoadTools, numeric: card?.showsNumericToolsCount });
    A("B2-2b does NOT read as Connected/Unknown ready card", card && card.readsConnected === false && card.readsUnknownStatus === false, { connected: card?.readsConnected, unknown: card?.readsUnknownStatus });
    A("B2-3 Sync button DISABLED pre-auth", card && card.syncDisabled === true, card?.syncDisabled);
    A("B2-4 exactly ONE Authorize button", card && card.authorizeCount === 1, card?.authorizeCount);
    A("B2-5 backend tools_count is 0 and oauth_authorized false pre-auth", back && back.tools_count === 0 && back.oauth_authorized === false, back);

    await deleteByName(page, NAME).catch(() => {});
    report.ok = report.asserts.every((a) => a.pass);
    console.log(JSON.stringify({ asserts: report.asserts, findings: report.findings }, null, 2));
    console.log(report.ok ? "B2 VERIFY: ALL ASSERTIONS PASS" : "B2 VERIFY: FAILURES PRESENT");
  } catch (e) {
    report.error = e.message; await shot(page, "99-error").catch(() => {}); console.error("FAIL", e.stack || e.message);
  } finally {
    fs.writeFileSync(OUT, JSON.stringify(report, null, 2));
    await browser.close();
    process.exit(report.ok ? 0 : 1);
  }
}
main();
