/**
 * Regression: OAuth-required servers must be presented as "authorization
 * required" (not a usable 0-tools card) until authorized (bug #3).
 *
 * Invariants:
 *   - stdio mcp-remote (Linear), unauthorized: shows "authorization required"
 *     badge; Sync stays ENABLED (token is gateway-side — gating would deadlock).
 *   - http OAuth 2.1 (auth_type=oauth), unauthorized: shows the badge AND Sync
 *     is DISABLED (a pre-auth manual sync is always a failing call; the control
 *     OAuth flow auto-syncs on success).
 *   - non-OAuth server (Playwright stdio): NO badge; Sync enabled.
 *
 * The authorized transition (badge clears + sync re-enables once oauth_authorized
 * flips) is verified live in the iteration log; it needs DB access to set the
 * encrypted token, so it is out of scope for this pure-UI test.
 *
 * Run: BASE_URL=http://127.0.0.1:8180 node tests/e2e/mcp_sandbox_adversarial/mcp_oauth_pending_state.mjs
 */
import { chromium } from "playwright";

const BASE = (process.env.BASE_URL || "http://127.0.0.1:8180").replace(/\/$/, "");
const EMAIL = process.env.TEST_EMAIL || "admin@zeroshield.io";
const PASS = process.env.TEST_PASSWORD || "Adm1n!Pass#2024";

function assert(cond, msg) {
  if (!cond) throw new Error(`ASSERT FAILED: ${msg}`);
}

async function login(page) {
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle", timeout: 120000 });
  if (page.url().includes("/login")) {
    await page.locator("#email").fill(EMAIL);
    await page.locator("#password").fill(PASS);
    const [res] = await Promise.all([
      page.waitForResponse((r) => r.url().includes("/api/auth/token/") && r.request().method() === "POST", { timeout: 120000 }),
      page.getByRole("button", { name: /^sign in$/i }).click(),
    ]);
    assert(res.ok(), `login failed: ${res.status()}`);
    await page.waitForURL((u) => !u.pathname.includes("/login"), { timeout: 120000 });
  }
}

/** Read one card's pending-auth state by server name. */
async function cardState(page, name) {
  return page.evaluate((wanted) => {
    for (const h of document.querySelectorAll("h4")) {
      const clean = h.textContent.replace(/(Unknown|Connected|Disconnected|Failed|Degraded|needs re-auth|authorization required|low risk|medium risk|high risk|Re-authorize|Authorize).*$/i, "").trim();
      if (clean !== wanted) continue;
      let node = h, card = null;
      for (let i = 0; i < 10 && node; i++) {
        node = node.parentElement; if (!node) break;
        const dels = [...node.querySelectorAll("button")].filter((b) => /Delete server/i.test(b.getAttribute("aria-label") || b.textContent));
        if (dels.length === 1) { card = node; break; }
      }
      if (!card) return null;
      const syncBtn = [...card.querySelectorAll("button")].find((b) => /Sync tools/i.test(b.getAttribute("aria-label") || ""));
      return { badge: /authorization required/i.test(h.textContent), syncDisabled: syncBtn ? syncBtn.disabled : null };
    }
    return null;
  }, name);
}

async function main() {
  const browser = await chromium.launch();
  const page = await (await browser.newContext()).newPage();
  const report = { steps: [], ok: false };
  let probeId = null;
  try {
    await login(page);
    report.steps.push("login");
    await page.goto(`${BASE}/?tab=firewall-1-4`, { waitUntil: "load", timeout: 180000 });
    await page.getByRole("button", { name: /register server/i }).first().waitFor({ timeout: 60000 });

    // Ensure a Linear stdio (mcp-remote) and a Playwright stdio (non-oauth) exist.
    for (const preset of ["Linear MCP", "Playwright MCP"]) {
      const present = await page.evaluate((p) => [...document.querySelectorAll("h4")].some((h) => h.textContent.trim().startsWith(p.replace(/ MCP$/, ""))), preset);
      if (!present) { await page.getByRole("button", { name: new RegExp(`^${preset}$`) }).click().catch(() => {}); await page.waitForTimeout(1500); }
    }

    // Self-provision an unauthorized http-oauth server via the app's token.
    probeId = await page.evaluate(async () => {
      const token = localStorage.getItem("auth_access")?.replace(/^"|"$/g, "");
      const res = await fetch("/api/mcp-connector/servers/", {
        method: "POST",
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
        body: JSON.stringify({ name: "ZZ Pending Probe", transport: "streamable-http", url: "https://mcp.linear.app/mcp", auth_type: "oauth" }),
      });
      return (await res.json()).id;
    });
    assert(probeId, "failed to create http-oauth probe");
    await page.goto(`${BASE}/?tab=firewall-1-4`, { waitUntil: "load", timeout: 180000 });
    await page.waitForTimeout(1500);
    report.steps.push("provisioned");

    const linear = await cardState(page, "Linear MCP");
    assert(linear, "Linear MCP card not found");
    assert(linear.badge === true, "stdio mcp-remote must show 'authorization required' badge");
    assert(linear.syncDisabled === false, "stdio sync must stay ENABLED (no deadlock)");
    report.steps.push("stdio-badge-sync-enabled");

    const probe = await cardState(page, "ZZ Pending Probe");
    assert(probe, "http-oauth probe card not found");
    assert(probe.badge === true, "http-oauth unauthorized must show badge");
    assert(probe.syncDisabled === true, "http-oauth unauthorized Sync must be DISABLED");
    report.steps.push("http-oauth-badge-sync-disabled");

    const pw = await cardState(page, "Playwright");
    assert(pw, "Playwright card not found");
    assert(pw.badge === false, "non-oauth server must NOT show badge");
    assert(pw.syncDisabled === false, "non-oauth Sync must be enabled");
    report.steps.push("non-oauth-clean");

    report.ok = true;
    console.log("PASS mcp_oauth_pending_state", JSON.stringify(report, null, 2));
  } catch (e) {
    report.error = String(e && e.message ? e.message : e);
    console.error("FAIL mcp_oauth_pending_state", JSON.stringify(report, null, 2));
    process.exitCode = 1;
  } finally {
    if (probeId) {
      await page.evaluate(async (id) => {
        const token = localStorage.getItem("auth_access")?.replace(/^"|"$/g, "");
        await fetch(`/api/mcp-connector/servers/${id}/`, { method: "DELETE", headers: { Authorization: `Bearer ${token}` } });
      }, probeId).catch(() => {});
    }
    await browser.close();
  }
}

main();
