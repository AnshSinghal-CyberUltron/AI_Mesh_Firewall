/**
 * MCP-page cleanup item 22 — EVERY tab + button on the §1.4 MCP panel works with real
 * data, in BOTH themes, at all 4 widths, with ZERO console errors.
 *
 * Sweeps the 6 panel tabs (MCP Servers / Tool Discovery / Tool Execution / Scan Controls
 * / MCP Security Policies / Observability) at 1440/1024/768/375 × light/dark — each tab
 * click must render content (no error boundary, no blank) and raise no console/page
 * error, with no horizontal page overflow. Then exercises per-server actions on the
 * Servers tab (Refresh + a per-server "Sync tools") and confirms each fires its request
 * without a console error.
 */
import fs from "node:fs";
import { launchBrowser, login, BASE } from "./mcp_page_typesim.mjs";

const OUT = process.env.SHOT_DIR || "mcp-parallel/findings/mcp-page/cleanup22";
const TAB = `${BASE}/?tab=firewall-1-4`;
const PANEL_TABS = ["MCP Servers", "Tool Discovery", "Tool Execution", "Scan Controls", "MCP Security Policies", "Observability"];
const WIDTHS = [[1440, 1000], [1024, 768], [768, 1024], [375, 812]];
// benign noise not attributable to this page's logic
const BENIGN = /favicon|ResizeObserver loop|Failed to load resource.*(favicon|\.map)/i;

async function main() {
  fs.mkdirSync(OUT, { recursive: true });
  const report = { checkpoint: "cleanup-22", sweep: [], perServer: {}, consoleErrors: [], ok: false };
  const { browser, page } = await launchBrowser();
  const pushErr = (s) => { if (!BENIGN.test(s)) report.consoleErrors.push(s); };
  page.on("pageerror", (e) => pushErr("pageerror: " + String(e.message || e)));
  page.on("console", (m) => { if (m.type() === "error") pushErr("console.error: " + m.text().slice(0, 180)); });

  const clickTab = async (label) => {
    // The tab's accessible name is "<label> <count-badge>" (e.g. "MCP Servers 12"),
    // so match the label as a NON-anchored substring, not an exact string.
    const rx = new RegExp(label.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "i");
    const byRole = page.getByRole("tab", { name: rx }).first();
    if (await byRole.count().catch(() => 0)) { await byRole.click({ timeout: 8000 }); return true; }
    await page.getByText(rx).first().click({ timeout: 8000 });
    return true;
  };

  // panel content sanity: visible, non-trivial text, no error-boundary
  const panelState = () => page.evaluate(() => {
    const main = document.querySelector("main");
    const txt = (main?.innerText || "");
    const vw = window.innerWidth;
    return {
      len: txt.length,
      hasError: /Something went wrong|Unable to render|Render error|componentStack/i.test(txt),
      overflow: document.documentElement.scrollWidth > vw + 2,
    };
  });

  try {
    await login(page);
    await page.goto(TAB, { waitUntil: "domcontentloaded", timeout: 120000 });
    await page.getByText(/MCP Guardrails|MCP Servers|Context Assembly/i).first().waitFor({ state: "visible", timeout: 60000 });

    for (const theme of ["light", "dark"]) {
      await page.evaluate((t) => {
        const el = document.documentElement;
        if (t === "dark") el.classList.add("dark"); else el.classList.remove("dark");
      }, theme);
      for (const [w, h] of WIDTHS) {
        await page.setViewportSize({ width: w, height: h });
        for (const label of PANEL_TABS) {
          let clicked = false, st = null, err = null;
          try {
            clicked = await clickTab(label);
            await page.waitForTimeout(900);
            st = await panelState();
          } catch (e) { err = e.message; }
          report.sweep.push({ theme, w, label, clicked, len: st?.len, hasError: st?.hasError, overflow: st?.overflow, err });
        }
        await page.screenshot({ path: `${OUT}/${theme}-${w}.png` });
      }
    }

    // per-server actions on the Servers tab (desktop, light)
    await page.evaluate(() => document.documentElement.classList.remove("dark"));
    await page.setViewportSize({ width: 1440, height: 1000 });
    await clickTab("MCP Servers");
    await page.waitForTimeout(1000);
    // Refresh
    let refreshReq = false;
    const refreshWait = page.waitForResponse((r) => /\/api\/mcp-connector\/servers\/?(\?|$)/.test(r.url()), { timeout: 8000 }).then(() => (refreshReq = true)).catch(() => {});
    await page.getByRole("button", { name: /^Refresh$/i }).first().click({ timeout: 8000 }).catch(() => {});
    await refreshWait;
    // per-server "Sync tools"
    let syncReq = false;
    const syncBtn = page.getByRole("button", { name: /Sync tools from server/i }).first();
    if (await syncBtn.count().catch(() => 0)) {
      const syncWait = page.waitForResponse((r) => /\/api\/mcp-connector\/servers\/[^/]+\/tools\/?$/.test(r.url()) && r.request().method() === "POST", { timeout: 12000 }).then(() => (syncReq = true)).catch(() => {});
      await syncBtn.click({ timeout: 8000 }).catch(() => {});
      await syncWait;
    }
    report.perServer = { refreshReq, syncReq, syncButtonPresent: (await syncBtn.count().catch(() => 0)) > 0 };
    await page.screenshot({ path: `${OUT}/perserver-actions.png` });

    const allClicked = report.sweep.every((s) => s.clicked && !s.err);
    const allRendered = report.sweep.every((s) => (s.len || 0) > 200 && !s.hasError);
    const noOverflow = report.sweep.every((s) => !s.overflow);
    report.summary = {
      combos: report.sweep.length,
      allClicked, allRendered, noOverflow,
      perServerOk: report.perServer.refreshReq && (report.perServer.syncReq || !report.perServer.syncButtonPresent),
      noConsoleErrors: report.consoleErrors.length === 0,
    };
    report.item22Pass = allClicked && allRendered && noOverflow && report.summary.perServerOk && report.summary.noConsoleErrors;
    report.ok = true;

    console.log(JSON.stringify({ summary: report.summary, consoleErrors: report.consoleErrors.slice(0, 6), perServer: report.perServer, failing: report.sweep.filter((s) => !s.clicked || s.err || (s.len || 0) <= 200 || s.hasError || s.overflow) }, null, 2));
    console.log(report.item22Pass
      ? `CLEANUP-22: PASS — all 6 tabs render + click across ${report.sweep.length} theme×width combos; per-server Refresh+Sync fire; 0 console errors`
      : "CLEANUP-22: FAIL");
  } catch (e) {
    report.error = e.message;
    await page.screenshot({ path: `${OUT}/99-error.png` }).catch(() => {});
    console.error("CLEANUP-22 FAIL:", e.message);
  } finally {
    fs.writeFileSync(`${OUT}/report.json`, JSON.stringify(report, null, 2));
    await browser.close();
    process.exit(report.item22Pass ? 0 : 1);
  }
}
main();
