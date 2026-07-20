/**
 * P3 item #14 — Playwright VERIFY of the B1 fix (post item #13).
 *
 * Asserts, in a real browser against the running stack:
 *  A) Linear (stdio + mcp-remote): NO "Server has no URL" error; the broken
 *     control http-oauth Authorize button is ABSENT; at most one authorize
 *     affordance (the legitimate gateway one, serverNeedsOAuth→startOAuth);
 *     clicking it never surfaces "no URL"/"only for HTTP".
 *  B) HTTP oauth (streamable-http + auth_type=oauth): EXACTLY ONE Authorize
 *     button; clicking OPENS A POPUP; a distinct "authorization required"
 *     pending badge is shown (B2 preview); NO "no URL" error (the guard lets
 *     HTTP through to discovery).
 *
 * Runs the SYSTEM chromium (playwright-bundled chromium is unsupported on
 * ubuntu26.04): executablePath=/usr/bin/chromium-browser. Playwright is
 * resolved from tests/e2e/node_modules regardless of cwd.
 *
 * Run:
 *   BASE_URL=http://127.0.0.1:8180 node scripts/playwright_mcp_b1_verify.mjs
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
const SHOT_DIR = process.env.SHOT_DIR || "mcp-parallel/findings/b1-verify";
const OUT = process.env.E2E_REPORT || `${SHOT_DIR}/report.json`;
const HTTP_NAME = "b1-verify-http-oauth";
// A resolvable public host (SSRF guard requires DNS resolution) that does NOT
// advertise OAuth metadata → discovery fails fast, so clicking Authorize opens
// the popup (pre-fetch) without doing real DCR against a live provider.
const HTTP_URL = "https://example.com/mcp";

const report = { story: "P3#14", bug: "B1", base: BASE, ok: false, asserts: [], findings: {}, pageErrors: [], error: null };
const A = (name, cond, detail) => { report.asserts.push({ name, pass: !!cond, detail }); return !!cond; };

function mkdir() { fs.mkdirSync(SHOT_DIR, { recursive: true }); }
async function shot(page, name) { mkdir(); const p = `${SHOT_DIR}/${name}.png`; await page.screenshot({ path: p, fullPage: true }); return p; }

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

async function token(page) {
  return page.evaluate(() => (localStorage.getItem("auth_access") || "").replace(/^"|"$/g, ""));
}
async function deleteByName(page, nameIncludes) {
  return page.evaluate(async (want) => {
    const tok = (localStorage.getItem("auth_access") || "").replace(/^"|"$/g, "");
    const h = { Authorization: `Bearer ${tok}`, "Content-Type": "application/json" };
    const list = await (await fetch("/api/mcp-connector/servers/", { headers: h })).json().catch(() => []);
    const rows = Array.isArray(list) ? list : (list.results || []);
    let n = 0;
    for (const s of rows) {
      if ((s.name || "").includes(want)) { await fetch(`/api/mcp-connector/servers/${s.id}/`, { method: "DELETE", headers: h }); n++; }
    }
    return n;
  }, nameIncludes);
}

/** Locate a server card by h4 title substring; return the card handle + button facts. */
async function cardFacts(page, nameIncludes) {
  return page.evaluate((want) => {
    for (const h of document.querySelectorAll("h4")) {
      const title = h.textContent.replace(/\s+/g, " ").trim();
      if (!title.includes(want)) continue;
      let node = h, card = null;
      for (let i = 0; i < 12 && node; i++) {
        node = node.parentElement; if (!node) break;
        if ([...node.querySelectorAll("button")].some((b) => /Delete server/i.test(b.getAttribute("aria-label") || ""))) { card = node; break; }
      }
      if (!card) return null;
      const authBtns = [...card.querySelectorAll("button")].filter((b) => /^Authorize$|^Re-authorize$/i.test((b.textContent || "").trim()));
      const cardText = card.innerText;
      return {
        authorizeCount: authBtns.length,
        authorizeLabels: authBtns.map((b) => (b.textContent || "").trim()),
        hasAuthorizationRequiredBadge: /pending authorization|authorization required/i.test(cardText),
        hasNoUrlError: /Server has no URL|OAuth is only for HTTP/i.test(cardText),
        toolsCount: (cardText.match(/(\d+)\s+tools/i) || [])[1] ?? null,
        cardSnippet: cardText.slice(0, 1200),
      };
    }
    return null;
  }, nameIncludes);
}

async function clickAuthorizeAndWatchPopup(page, context, nameIncludes) {
  // Find the first Authorize button inside the named card and click it.
  const handle = await page.evaluateHandle((want) => {
    for (const h of document.querySelectorAll("h4")) {
      if (!h.textContent.replace(/\s+/g, " ").includes(want)) continue;
      let node = h, card = null;
      for (let i = 0; i < 12 && node; i++) { node = node.parentElement; if (!node) break;
        if ([...node.querySelectorAll("button")].some((b) => /Delete server/i.test(b.getAttribute("aria-label") || ""))) { card = node; break; } }
      if (!card) return null;
      return [...card.querySelectorAll("button")].find((b) => /^Authorize$|^Re-authorize$/i.test((b.textContent || "").trim())) || null;
    }
    return null;
  }, nameIncludes);
  const el = handle.asElement();
  if (!el) return { clicked: false, popupOpened: false, visibleError: null };
  const [popup] = await Promise.all([
    context.waitForEvent("page", { timeout: 6000 }).catch(() => null),
    el.click(),
  ]);
  await page.waitForTimeout(2500);
  const body = await page.locator("body").innerText();
  const errMatch = body.match(/Server has no URL[^\n]*/i) || body.match(/OAuth is only for HTTP[^\n]*/i)
    || body.match(/OAuth (start|authorize) failed[^\n]{0,160}/i);
  const popupUrl = popup ? popup.url() : null;
  if (popup && !popup.isClosed()) await popup.close().catch(() => {});
  return { clicked: true, popupOpened: !!popup, popupUrl, visibleError: errMatch ? errMatch[0] : null };
}

async function main() {
  mkdir();
  const browser = await chromium.launch({ executablePath: CHROME, headless: true, args: ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"] });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1200 } });
  const page = await context.newPage();
  page.on("pageerror", (e) => report.pageErrors.push(String(e.message || e)));

  try {
    await login(page);
    await page.goto(`${BASE}/?tab=firewall-1-4`, { waitUntil: "domcontentloaded", timeout: 120000 });
    await page.locator("text=/MCP Guardrails/i").first().waitFor({ state: "visible", timeout: 60000 });
    await shot(page, "01-panel");

    // ---- Scenario A: Linear stdio ----
    const linearExists = await page.locator("h4", { hasText: "Linear MCP" }).first().isVisible().catch(() => false);
    if (!linearExists) {
      const presetBtn = page.getByRole("button", { name: /Linear MCP/i });
      await presetBtn.waitFor({ state: "visible", timeout: 30000 });
      await Promise.all([
        page.waitForResponse((r) => r.url().includes("/api/mcp-connector/servers/") && r.request().method() === "POST", { timeout: 120000 }),
        presetBtn.click(),
      ]);
    }
    await page.waitForTimeout(1500);
    const linear = await cardFacts(page, "Linear MCP");
    report.findings.linear = linear;
    await shot(page, "02-linear-card");
    const linearClick = linear ? await clickAuthorizeAndWatchPopup(page, context, "Linear MCP") : null;
    report.findings.linearClick = linearClick;
    await shot(page, "03-linear-after-click");

    A("A1 Linear stdio: no 'Server has no URL' error on card", linear && linear.hasNoUrlError === false, linear?.cardSnippet);
    A("A2 Linear stdio: <=1 authorize button (no dup control button)", linear && linear.authorizeCount <= 1, linear?.authorizeLabels);
    A("A3 Linear stdio: click never shows 'no URL'/'only for HTTP'", !linearClick || !/no URL|only for HTTP/i.test(linearClick.visibleError || ""), linearClick);

    // ---- Scenario B: HTTP oauth ----
    await deleteByName(page, HTTP_NAME);
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.locator("text=/MCP Guardrails/i").first().waitFor({ state: "visible", timeout: 60000 });
    // Register via modal (also exercises the form's oauth-for-HTTP path).
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
    report.findings.httpRegisterStatus = createRes.status();
    await page.waitForTimeout(1500);
    const http = await cardFacts(page, HTTP_NAME);
    report.findings.http = http;
    await shot(page, "04-http-oauth-card");
    const httpClick = http ? await clickAuthorizeAndWatchPopup(page, context, HTTP_NAME) : null;
    report.findings.httpClick = httpClick;
    await shot(page, "05-http-oauth-after-click");

    A("B1 HTTP oauth: registered ok (201)", createRes.status() === 201, createRes.status());
    A("B2 HTTP oauth: exactly ONE Authorize button", http && http.authorizeCount === 1, http?.authorizeLabels);
    A("B3 HTTP oauth: clicking Authorize opens a popup", httpClick && httpClick.popupOpened === true, httpClick);
    A("B4 HTTP oauth: distinct 'authorization required' pending badge", http && http.hasAuthorizationRequiredBadge === true, http);
    A("B5 HTTP oauth: no 'Server has no URL' error (guard lets HTTP through)", http && http.hasNoUrlError === false, http?.cardSnippet);

    await deleteByName(page, HTTP_NAME).catch(() => {});

    report.ok = report.asserts.every((a) => a.pass);
    console.log(JSON.stringify({ asserts: report.asserts, findings: report.findings, pageErrors: report.pageErrors }, null, 2));
    console.log(report.ok ? "B1 VERIFY: ALL ASSERTIONS PASS" : "B1 VERIFY: FAILURES PRESENT");
  } catch (e) {
    report.error = e.message; await shot(page, "99-error").catch(() => {}); console.error("FAIL", e.stack || e.message);
  } finally {
    fs.writeFileSync(OUT, JSON.stringify(report, null, 2));
    await browser.close();
    process.exit(report.ok ? 0 : 1);
  }
}
main();
