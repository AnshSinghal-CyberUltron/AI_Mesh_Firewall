/**
 * Capture the MCP Servers tab and crop each Linear server card so we can VISUALLY
 * verify: a FAILED server shows its real "Failed" status + branded error — NOT a
 * misleading "Pending authorization / Authorize" affordance.
 */
import fs from "node:fs";
import { launchBrowser, login, BASE } from "./mcp_page_typesim.mjs";

const OUT = process.env.SHOT_DIR || "mcp-parallel/findings/mcp-page/SERVERCARDS";

async function main() {
  fs.mkdirSync(OUT, { recursive: true });
  const { browser, page } = await launchBrowser({ viewport: { width: 1280, height: 1400 } });
  const found = [];
  try {
    await login(page);
    await page.goto(`${BASE}/?tab=firewall-1-4&_cb=${Date.now()}`, { waitUntil: "networkidle", timeout: 120000 });
    await page.getByRole("tab", { name: /MCP Servers/i }).first().click().catch(() => {});
    await page.waitForTimeout(3500);

    // For each Linear server card, read its badge + whether it (wrongly) says pending-auth
    const cards = await page.evaluate(() => {
      const out = [];
      for (const h of document.querySelectorAll("h4")) {
        const name = (h.textContent || "").replace(/\s+/g, " ").trim();
        if (!/linear/i.test(name)) continue;
        const card = h.closest("[class*='rounded']") || h.parentElement;
        const txt = (card?.innerText || "").replace(/\s+/g, " ");
        out.push({
          name,
          badgeSaysPendingAuth: /Pending authorization/i.test(txt),
          badgeSaysFailed: /Failed/i.test(txt),
          saysAuthorizeToLoad: /Authorize to load tools/i.test(txt),
          hasStorageError: /too large to install|storage limit/i.test(txt),
          hasAuthError: /rejected authentication|re-authorize/i.test(txt),
        });
      }
      return out;
    });
    found.push(...cards);

    // scroll a Linear card into view and screenshot the server list region
    const anyLinear = page.getByText(/linear-mcp-p1-repro|Linear MCP/i).first();
    await anyLinear.scrollIntoViewIfNeeded().catch(() => {});
    await page.waitForTimeout(500);
    await page.screenshot({ path: `${OUT}/linear-cards-after-fix.png` });

    console.log(JSON.stringify({ cards: found }, null, 2));
    const stillMislabeled = found.filter((c) => c.badgeSaysPendingAuth && !c.hasAuthError);
    console.log(stillMislabeled.length === 0
      ? "CARDS: PASS — no failed/non-auth server mislabeled 'Pending authorization'"
      : "CARDS: FAIL — still mislabeled: " + JSON.stringify(stillMislabeled.map((c) => c.name)));
    process.exitCode = stillMislabeled.length === 0 ? 0 : 1;
  } catch (e) {
    console.error("CAPTURE FAIL:", e.message);
    await page.screenshot({ path: `${OUT}/99-error.png` }).catch(() => {});
    process.exitCode = 1;
  } finally {
    await browser.close();
  }
}
main();
