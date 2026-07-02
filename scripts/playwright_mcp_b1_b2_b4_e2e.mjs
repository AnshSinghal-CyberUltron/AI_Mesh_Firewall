/**
 * P5 item #17 — Combined B1/B2/B4 end-to-end Playwright verify.
 *
 * Single browser session covering:
 *   E2E-1  Linear stdio (B1): ≤1 Authorize, no "no URL" error
 *   E2E-2  HTTP oauth register (B2): pending badge, sync disabled, 1 Authorize
 *   E2E-3  Add-Server dialog (B4): focus kept per keystroke in all fields
 *
 * Tools-populate after authorize requires manual OAuth (headed noVNC) — out of
 * scope for this headless gate; see scripts/ralph/headed_browser.sh.
 *
 * Run:
 *   BASE_URL=http://127.0.0.1:8180 node scripts/playwright_mcp_b1_b2_b4_e2e.mjs
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
const SHOT_DIR = process.env.SHOT_DIR || "mcp-parallel/findings/p5-17";
const OUT = process.env.E2E_REPORT || `${SHOT_DIR}/report.json`;
const HTTP_NAME = "e2e-combined-http-oauth";
const HTTP_URL = "https://example.com/mcp";
const TYPED_LEN = 24;

const report = { story: "P5#17", bugs: ["B1", "B2", "B4"], base: BASE, ok: false, asserts: [], findings: {}, error: null };
const A = (name, cond, detail) => { report.asserts.push({ name, pass: !!cond, detail }); return !!cond; };
function mkdir() { fs.mkdirSync(SHOT_DIR, { recursive: true }); }
async function shot(page, name) { mkdir(); await page.screenshot({ path: `${SHOT_DIR}/${name}.png`, fullPage: true }); }

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
        hasNoUrlError: /Server has no URL|OAuth is only for HTTP/i.test(txt),
        authorizeCount: authBtns.length,
        syncDisabled: syncBtn ? syncBtn.disabled : null,
      };
    }
    return null;
  }, want);
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

async function typeAndTrack(page, locator, fieldKey, text) {
  const sample = text.slice(0, TYPED_LEN);
  await locator.click();
  await locator.fill("");
  const handle = await locator.elementHandle();
  let focusKeptAll = true;
  for (let i = 0; i < sample.length; i++) {
    await page.keyboard.press(sample[i] === " " ? "Space" : sample[i]);
    const kept = await page.evaluate((el) => el === document.activeElement, handle);
    if (!kept) { focusKeptAll = false; break; }
  }
  return { field: fieldKey, focusKeptAllKeystrokes: focusKeptAll };
}


async function dismissRegisterDialog(page) {
  const dialogTitle = page.getByText("Register MCP Server", { exact: false }).first();
  if (!(await dialogTitle.isVisible().catch(() => false))) return;
  // Dialog onClose only hides the modal — Cancel would delete addServerId (B2).
  await page.keyboard.press("Escape");
  await dialogTitle.waitFor({ state: "hidden", timeout: 15000 }).catch(() => {});
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

    // ---- E2E-1: Linear stdio (B1) ----
    const linearExists = await page.locator("h4", { hasText: "Linear MCP" }).first().isVisible().catch(() => false);
    if (!linearExists) {
      await Promise.all([
        page.waitForResponse((r) => r.url().includes("/api/mcp-connector/servers/") && r.request().method() === "POST", { timeout: 120000 }),
        page.getByRole("button", { name: /Linear MCP/i }).click(),
      ]);
    }
    await page.waitForTimeout(1500);
    const linear = await cardFacts(page, "Linear MCP");
    report.findings.linear = linear;
    await shot(page, "01-linear-stdio-card");
    A("E2E-B1-1 Linear: no 'no URL' error", linear && !linear.hasNoUrlError, linear?.cardSnippet);
    A("E2E-B1-2 Linear: ≤1 Authorize button", linear && linear.authorizeCount <= 1, linear?.authorizeCount);

    // ---- E2E-2: HTTP oauth (B2) ----
    await deleteByName(page, "e2e-verify-http-oauth");
    await deleteByName(page, "e2e-combined-http-oauth");
    await deleteByName(page, HTTP_NAME);
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.locator("text=/MCP Guardrails/i").first().waitFor({ state: "visible", timeout: 60000 });
    await page.getByRole("button", { name: /^Register Server$/i }).first().click();
    await page.getByText("Register MCP Server", { exact: false }).first().waitFor({ state: "visible", timeout: 15000 });
    await page.getByPlaceholder("my-mcp-server").fill(HTTP_NAME);
    await page.getByPlaceholder("https://my-server.example.com/mcp").fill(HTTP_URL);
    await page.getByLabel("Transport").selectOption("streamable-http");
    await page.getByLabel("Upstream authentication type").selectOption("oauth");
    const [createRes] = await Promise.all([
      page.waitForResponse((r) => r.url().includes("/api/mcp-connector/servers/") && r.request().method() === "POST", { timeout: 60000 }),
      page.getByRole("button", { name: /^Register$/, exact: true }).click(),
    ]);
    await page.locator("h4", { hasText: HTTP_NAME }).first().waitFor({ state: "visible", timeout: 30000 });
    const http = await cardFacts(page, HTTP_NAME);
    report.findings.http = http;
    await shot(page, "02-http-oauth-pending-card");
    A("E2E-B2-1 HTTP oauth: registered 201", createRes.status() === 201, createRes.status());
    A("E2E-B2-2 pending authorization badge", http && http.hasPendingAuthBadge, http?.cardSnippet);
    A("E2E-B2-3 Authorize to load tools (no N tools)", http && http.hasAuthorizeToLoadTools && !http.showsNumericToolsCount, http);
    A("E2E-B2-4 exactly ONE Authorize", http && http.authorizeCount === 1, http?.authorizeCount);
    A("E2E-B2-5 Sync disabled pre-auth", http && http.syncDisabled === true, http?.syncDisabled);
    await dismissRegisterDialog(page);

    // ---- E2E-3: Dialog focus (B4) ----
    await page.getByRole("button", { name: /^Register Server$/i }).first().click();
    await page.getByText("Register MCP Server", { exact: false }).first().waitFor({ state: "visible", timeout: 15000 });
    await shot(page, "03-dialog-open");
    const fields = [];
    fields.push(await typeAndTrack(page, page.getByPlaceholder("my-mcp-server"), "name", "a".repeat(TYPED_LEN)));
    fields.push(await typeAndTrack(page, page.getByPlaceholder("https://my-server.example.com/mcp"), "url", "b".repeat(TYPED_LEN)));
    fields.push(await typeAndTrack(page, page.getByPlaceholder("Optional description"), "description", "c".repeat(TYPED_LEN)));
    await page.getByLabel("Transport").selectOption("stdio");
    await page.waitForTimeout(200);
    fields.push(await typeAndTrack(page, page.getByPlaceholder("npx"), "stdio-command", "d".repeat(TYPED_LEN)));
    report.findings.focusFields = fields;
    await shot(page, "04-after-dialog-typing");
    const lossFields = fields.filter((f) => !f.focusKeptAllKeystrokes);
    for (const f of fields) A(`E2E-B4 focus kept: ${f.field}`, f.focusKeptAllKeystrokes, f);
    A("E2E-B4 no focus loss in any field", lossFields.length === 0, lossFields.map((f) => f.field));

    await deleteByName(page, HTTP_NAME).catch(() => {});

    report.ok = report.asserts.every((a) => a.pass);
    console.log(JSON.stringify({ asserts: report.asserts, findings: report.findings }, null, 2));
    console.log(report.ok ? "B1/B2/B4 E2E: ALL PASS" : "B1/B2/B4 E2E: FAILURES");
  } catch (e) {
    report.error = e.message;
    console.error("FAIL", e.stack || e.message);
    await shot(page, "99-error").catch(() => {});
  } finally {
    fs.writeFileSync(OUT, JSON.stringify(report, null, 2));
    await browser.close();
    process.exit(report.ok ? 0 : 1);
  }
}

main();
