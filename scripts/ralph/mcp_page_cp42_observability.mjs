/**
 * MCP-page Ralph — CP42: the Observability tab (post-CP26 fix) — every control works +
 * real data, across BOTH themes at 1440/1024/768/375 with ZERO console errors.
 *   controls: time-range selector (→ loadEvents), Refresh (→ loadEvents+loadEventSummary)
 *   data:     summary cards (total + decision counts) + event list, from /events/ + /events/summary/
 */
import fs from "node:fs";
import { launchBrowser, login, BASE } from "./mcp_page_typesim.mjs";

const OUT = process.env.SHOT_DIR || "mcp-parallel/findings/mcp-page/cp42";
const VIEWPORTS = [[1440, 900], [1024, 768], [768, 1024], [375, 812]];

async function main() {
  fs.mkdirSync(OUT, { recursive: true });
  const report = { checkpoint: "42", consoleErrors: [], sweep: [], ok: false };
  const { browser, page } = await launchBrowser();
  page.on("pageerror", (e) => report.consoleErrors.push("pageerror: " + String(e.message || e)));
  page.on("console", (m) => { if (m.type() === "error") report.consoleErrors.push("console.error: " + m.text().slice(0, 160)); });

  let summaryFetched = 0, eventsFetched = 0;
  page.on("response", (r) => {
    const u = r.url();
    if (u.includes("/api/mcp-connector/events/summary/")) summaryFetched++;
    else if (u.includes("/api/mcp-connector/events/")) eventsFetched++;
  });

  const gotoObs = async () => {
    await page.goto(`${BASE}/?tab=firewall-1-4`, { waitUntil: "domcontentloaded", timeout: 120000 });
    await page.getByText(/MCP Guardrails/i).first().waitFor({ state: "visible", timeout: 60000 }).catch(() => {});
    await page.getByRole("tab", { name: /Observability/i }).first().click({ timeout: 15000 })
      .catch(async () => { await page.getByText(/Observability/i).first().click({ timeout: 15000 }); });
    await page.waitForTimeout(2000);
  };

  try {
    await login(page);
    await gotoObs();

    // ── real data: summary cards render a numeric total + decision info ──
    const body = await page.locator("body").innerText().catch(() => "");
    report.hasSummary = /\b\d{2,}\b/.test(body) && /(allow|block|redact|monitor|error|Total|events)/i.test(body);

    // ── time-range control: changing it re-fetches events ──
    const before = eventsFetched;
    // Time window SegmentedControl segments: 1h/24h/7d/30d/All (default 24h). Click
    // "7d" (a different value) to trigger loadEvents(168)+loadEventSummary(168).
    await page.getByText("7d", { exact: true }).first().click({ timeout: 8000 }).catch(() => {});
    for (let i = 0; i < 20; i++) { if (eventsFetched > before) break; await page.waitForTimeout(300); }
    if (eventsFetched === before) {
      // fallback: click "All"
      await page.getByText("All", { exact: true }).first().click({ timeout: 8000 }).catch(() => {});
      for (let i = 0; i < 20; i++) { if (eventsFetched > before) break; await page.waitForTimeout(300); }
    }
    report.timeRangeRefetched = eventsFetched > before;

    // ── Refresh control re-fetches both ──
    const s0 = summaryFetched, e0 = eventsFetched;
    await page.getByRole("button", { name: /Refresh events/i }).first().click({ timeout: 8000 }).catch(() => {});
    for (let i = 0; i < 16; i++) { if (summaryFetched > s0 && eventsFetched > e0) break; await page.waitForTimeout(300); }
    report.refreshRefetched = summaryFetched > s0 && eventsFetched > e0;

    // ── responsive + theme sweep: 4 viewports × {light, dark}, no h-overflow ──
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
    report.cp42Pass = report.hasSummary && report.timeRangeRefetched && report.refreshRefetched
      && report.noConsoleErrors && report.noOverflow;
    report.ok = true;
    console.log(JSON.stringify({ hasSummary: report.hasSummary, timeRangeRefetched: report.timeRangeRefetched,
      refreshRefetched: report.refreshRefetched, noConsoleErrors: report.noConsoleErrors,
      consoleErrors: report.consoleErrors.slice(0, 5), noOverflow: report.noOverflow, cp42Pass: report.cp42Pass }, null, 2));
    console.log(report.cp42Pass ? "CP42: PASS — Observability controls work + real data; both themes 4 viewports, 0 console errors"
      : "CP42: FAIL");
  } catch (e) {
    report.error = e.message;
    await page.screenshot({ path: `${OUT}/99-error.png` }).catch(() => {});
    console.error("CP42 FAIL:", e.message);
  } finally {
    fs.writeFileSync(`${OUT}/report.json`, JSON.stringify(report, null, 2));
    await browser.close();
    process.exit(report.cp42Pass ? 0 : 1);
  }
}
main();
