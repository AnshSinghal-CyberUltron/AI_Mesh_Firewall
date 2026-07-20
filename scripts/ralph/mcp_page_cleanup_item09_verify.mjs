/**
 * MCP-page cleanup item 09 — LIVE page verify (Playwright): the server list shows
 * every server in a RESOLVED state (Connected/Failed/Syncing) and NO server sits at
 * a stuck "Unknown" — in both themes at 1440 + 375. Screenshots for the record.
 *
 * Backed by item 07 (registration auto-sync) + item 08 (re-sync resolved the 12
 * stuck unknowns) + the item-09 map fix (connection_status "syncing" now renders
 * "Syncing", not falling through to "Unknown").
 */
import fs from "node:fs";
import { launchBrowser, login, BASE } from "./mcp_page_typesim.mjs";

const OUT = process.env.SHOT_DIR || "mcp-parallel/findings/mcp-page/cleanup09";
const VIEWPORTS = [[1440, 1000], [375, 812]];

async function main() {
  fs.mkdirSync(OUT, { recursive: true });
  const report = { checkpoint: "cleanup-09", consoleErrors: [], sweep: [], ok: false };
  const { browser, page } = await launchBrowser();
  page.on("pageerror", (e) => report.consoleErrors.push("pageerror: " + String(e.message || e)));
  page.on("console", (m) => { if (m.type() === "error") report.consoleErrors.push("console.error: " + m.text().slice(0, 160)); });

  try {
    await login(page);
    await page.goto(`${BASE}/?tab=firewall-1-4`, { waitUntil: "domcontentloaded", timeout: 120000 });
    await page.getByText(/MCP Guardrails/i).first().waitFor({ state: "visible", timeout: 60000 }).catch(() => {});
    // ensure the Servers tab is active
    await page.getByRole("tab", { name: /Servers/i }).first().click({ timeout: 10000 }).catch(() => {});
    await page.waitForTimeout(2500);

    // Count connection-status badges per label across the server list. A server card
    // shows exactly one connection badge among Connected/Failed/Syncing/Unknown.
    const counts = await page.evaluate(() => {
      const labels = ["Connected", "Failed", "Syncing", "Connecting", "Unknown", "Needs re-auth", "Pending"];
      const out = {};
      for (const l of labels) out[l] = 0;
      // scan visible badge-ish elements
      const nodes = Array.from(document.querySelectorAll("span, div"));
      for (const n of nodes) {
        const t = (n.textContent || "").trim();
        if (labels.includes(t) && n.children.length <= 1) {
          // only count leaf-ish badges (avoid double-counting parents)
          out[t] = (out[t] || 0) + 1;
        }
      }
      // total server cards ~ by the server name headings (Server icon rows). Fallback: cards.
      const cards = document.querySelectorAll("[class*='Card'], .rounded-lg");
      return { badges: out, cardEstimate: cards.length };
    });
    report.badgeCounts = counts.badges;
    // The stuck state we eliminated: a server showing "Unknown" connection status.
    // After items 07/08 every server resolved, so there must be 0 stuck "Unknown".
    report.stuckUnknown = counts.badges["Unknown"] || 0;
    report.hasResolved = (counts.badges["Connected"] || 0) + (counts.badges["Failed"] || 0) > 0;

    // no "Never synced" stuck copy on any card
    const body = await page.locator("body").innerText().catch(() => "");
    report.hasNeverSynced = /never synced/i.test(body);

    // theme + width sweep with screenshots
    for (const theme of ["light", "dark"]) {
      await page.evaluate((t) => {
        const el = document.documentElement;
        if (t === "dark") el.classList.add("dark"); else el.classList.remove("dark");
      }, theme);
      for (const [w, h] of VIEWPORTS) {
        await page.setViewportSize({ width: w, height: h });
        await page.waitForTimeout(400);
        const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 2);
        await page.screenshot({ path: `${OUT}/${theme}-${w}x${h}.png` });
        report.sweep.push({ theme, w, h, horizontalOverflow: overflow });
      }
    }

    report.noConsoleErrors = report.consoleErrors.length === 0;
    report.noOverflow = report.sweep.every((s) => !s.horizontalOverflow);
    report.cleanup09Pass = report.stuckUnknown === 0 && report.hasResolved
      && !report.hasNeverSynced && report.noConsoleErrors && report.noOverflow;
    report.ok = true;
    console.log(JSON.stringify({
      badgeCounts: report.badgeCounts, stuckUnknown: report.stuckUnknown,
      hasResolved: report.hasResolved, hasNeverSynced: report.hasNeverSynced,
      noConsoleErrors: report.noConsoleErrors, consoleErrors: report.consoleErrors.slice(0, 4),
      noOverflow: report.noOverflow, cleanup09Pass: report.cleanup09Pass,
    }, null, 2));
    console.log(report.cleanup09Pass
      ? "CLEANUP-09: PASS — no server stuck at 'Unknown'; resolved states render; both themes, no overflow, 0 console errors"
      : "CLEANUP-09: FAIL");
  } catch (e) {
    report.error = e.message;
    await page.screenshot({ path: `${OUT}/99-error.png` }).catch(() => {});
    console.error("CLEANUP-09 FAIL:", e.message);
  } finally {
    fs.writeFileSync(`${OUT}/report.json`, JSON.stringify(report, null, 2));
    await browser.close();
    process.exit(report.cleanup09Pass ? 0 : 1);
  }
}
main();
