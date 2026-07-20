/**
 * Attach (over CDP) to the HEADED chromium started by headed_browser.sh, log in
 * to the app, land on the MCP Guardrails panel, and register a Linear
 * streamable-http + OAuth candidate so the operator has a concrete "Pending
 * authorization" card with an Authorize button to complete MANUALLY via noVNC.
 *
 * Leaves the browser RUNNING (does not close it) for the human hand-off.
 *
 *   node scripts/ralph/cdp_drive_login.mjs
 */
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import path from "node:path";
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);
const { chromium } = require(path.resolve(__dirname, "../../tests/e2e/node_modules/playwright"));

const CDP = process.env.CDP_URL || "http://127.0.0.1:9222";
const BASE = (process.env.BASE_URL || "http://127.0.0.1:8180").replace(/\/$/, "");
const EMAIL = process.env.TEST_EMAIL || "admin@zeroshield.io";
const PASS = process.env.TEST_PASSWORD || "Adm1n!Pass#2024";
const OAUTH_NAME = process.env.OAUTH_NAME || "Linear (manual OAuth)";
const OAUTH_URL = process.env.OAUTH_URL || "https://mcp.linear.app/mcp";

const browser = await chromium.connectOverCDP(CDP);
const ctx = browser.contexts()[0] || (await browser.newContext());
const page = ctx.pages()[0] || (await ctx.newPage());

async function loggedIn() {
  try { return await page.evaluate(() => !!(localStorage.getItem("auth_access"))); } catch { return false; }
}

// 1. login if needed
if (!(await loggedIn())) {
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle", timeout: 120000 });
  await page.locator("#email").fill(EMAIL);
  await page.locator("#password").fill(PASS);
  await Promise.all([
    page.waitForResponse((r) => r.url().includes("/api/auth/token/") && r.request().method() === "POST", { timeout: 60000 }),
    page.getByRole("button", { name: /^sign in$/i }).click(),
  ]);
  await page.waitForFunction(() => !location.pathname.startsWith("/login"), { timeout: 30000 });
  console.log("logged in");
} else {
  console.log("already logged in");
}

// 2. go to MCP panel
await page.goto(`${BASE}/?tab=firewall-1-4`, { waitUntil: "domcontentloaded", timeout: 120000 });
await page.locator("text=/MCP Guardrails/i").first().waitFor({ state: "visible", timeout: 60000 });

// 3. register the Linear streamable-http + oauth candidate (dedupe first) via API
const res = await page.evaluate(async ({ name, url }) => {
  const tok = (localStorage.getItem("auth_access") || "").replace(/^"|"$/g, "");
  const h = { Authorization: `Bearer ${tok}`, "Content-Type": "application/json" };
  const list = await (await fetch("/api/mcp-connector/servers/", { headers: h })).json().catch(() => []);
  const rows = Array.isArray(list) ? list : (list.results || []);
  for (const s of rows) if ((s.name || "") === name) await fetch(`/api/mcp-connector/servers/${s.id}/`, { method: "DELETE", headers: h });
  const r = await fetch("/api/mcp-connector/servers/", {
    method: "POST", headers: h,
    body: JSON.stringify({ name, transport: "streamable-http", url, auth_type: "oauth", description: "Manual-OAuth B2 candidate (item #16)" }),
  });
  return { status: r.status, body: await r.json().catch(() => ({})) };
}, { name: OAUTH_NAME, url: OAUTH_URL });
console.log("register:", res.status, res.body?.id || res.body?.url || JSON.stringify(res.body).slice(0, 200));

await page.reload({ waitUntil: "domcontentloaded" });
await page.locator("text=/MCP Guardrails/i").first().waitFor({ state: "visible", timeout: 60000 });
await page.waitForTimeout(1500);

// leave browser running for the human — DO NOT close.
console.log("READY: browser on MCP Guardrails panel, Linear HTTP-oauth card registered (Pending authorization).");
process.exit(0);
