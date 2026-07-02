/**
 * Regression: transport-aware OAuth in the MCP connector UI (bugs #1 dup / #2 stdio-OAuth).
 *
 * Invariants proven here:
 *   A. No server card ever shows more than ONE "Authorize" button (dup OAuth UI, #1).
 *   B. A stdio mcp-remote server (Linear) shows exactly one authorize button — the
 *      gateway-side one — and never the control HTTP OAuth 2.1 button that 400s
 *      "Server has no URL" (#2).
 *   C. The Register modal HIDES the "OAuth 2.1" auth option for stdio transport,
 *      and SHOWS it for streamable-http (so the invalid stdio+oauth row can't be
 *      created from the UI).
 *
 * Run: BASE_URL=http://127.0.0.1:8180 node tests/e2e/mcp_sandbox_adversarial/mcp_oauth_transport_ui.mjs
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

async function main() {
  const browser = await chromium.launch();
  const page = await (await browser.newContext()).newPage();
  const report = { steps: [], ok: false };
  try {
    await login(page);
    report.steps.push("login");

    await page.goto(`${BASE}/?tab=firewall-1-4`, { waitUntil: "load", timeout: 180000 });
    await page.getByRole("button", { name: /register server/i }).first().waitFor({ timeout: 60000 });

    // Ensure a Linear stdio (mcp-remote) server exists as the canonical stdio case.
    const hasLinear = await page.evaluate(() =>
      [...document.querySelectorAll("h4")].some((h) => /^Linear MCP\b/.test(h.textContent.trim()))
    );
    if (!hasLinear) {
      await page.getByRole("button", { name: /^Linear MCP$/ }).click();
      await page.waitForTimeout(2500);
    }
    report.steps.push(`linear-present:${hasLinear}`);

    // Invariant A + B: enumerate every server card's authorize-button count.
    const cardStats = await page.evaluate(() => {
      const stats = [];
      document.querySelectorAll("h4").forEach((h) => {
        // climb to the smallest ancestor holding exactly one "Delete server" btn
        let node = h, card = null;
        for (let i = 0; i < 10 && node; i++) {
          node = node.parentElement; if (!node) break;
          const dels = [...node.querySelectorAll("button")].filter((b) => /Delete server/i.test(b.getAttribute("aria-label") || b.textContent));
          if (dels.length === 1) { card = node; break; }
        }
        if (!card) return;
        const name = h.textContent.replace(/(Unknown|Connected|Disconnected|Failed|Degraded).*$/, "").trim();
        const authorizeCount = [...card.querySelectorAll("button")]
          .filter((b) => /authorize/i.test(b.getAttribute("aria-label") || b.textContent)).length;
        stats.push({ name, authorizeCount });
      });
      return stats;
    });
    report.cardCount = cardStats.length;
    const dupCards = cardStats.filter((c) => c.authorizeCount > 1);
    assert(dupCards.length === 0, `dup OAuth UI (#1): cards with >1 authorize button: ${JSON.stringify(dupCards)}`);
    report.steps.push(`no-dup-across-${cardStats.length}-cards`);

    const linear = cardStats.find((c) => /^Linear MCP\b/.test(c.name));
    assert(linear, "Linear MCP stdio card not found");
    assert(linear.authorizeCount === 1, `Linear stdio should have exactly 1 authorize button, got ${linear.authorizeCount}`);
    report.steps.push("linear-single-authorize");

    // Invariant C: modal auth options are transport-aware.
    await page.getByRole("button", { name: /register server/i }).first().click();
    await page.locator('[role="dialog"] select[aria-label="Transport"]').waitFor({ timeout: 30000 });

    const readAuthOptions = () => page.evaluate(() => {
      const dlg = document.querySelector('[role="dialog"]');
      const sel = dlg?.querySelector('select[aria-label="Upstream authentication type"]');
      return sel ? [...sel.options].map((o) => o.value) : null;
    });

    await page.locator('[role="dialog"] select[aria-label="Transport"]').selectOption("stdio");
    const stdioOpts = await readAuthOptions();
    assert(stdioOpts && !stdioOpts.includes("oauth"), `stdio must NOT offer oauth; options=${JSON.stringify(stdioOpts)}`);
    report.steps.push("modal-stdio-hides-oauth");

    await page.locator('[role="dialog"] select[aria-label="Transport"]').selectOption("streamable-http");
    const httpOpts = await readAuthOptions();
    assert(httpOpts && httpOpts.includes("oauth"), `streamable-http MUST offer oauth; options=${JSON.stringify(httpOpts)}`);
    report.steps.push("modal-http-shows-oauth");

    report.ok = true;
    console.log("PASS mcp_oauth_transport_ui", JSON.stringify(report, null, 2));
  } catch (e) {
    report.error = String(e && e.message ? e.message : e);
    console.error("FAIL mcp_oauth_transport_ui", JSON.stringify(report, null, 2));
    process.exitCode = 1;
  } finally {
    await browser.close();
  }
}

main();
