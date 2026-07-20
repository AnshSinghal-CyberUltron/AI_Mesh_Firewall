/**
 * PROVE the §1.4 (?tab=firewall-1-4) cleanup fixes are LIVE on the actual frontend
 * (Vite dev server, host :8180). One clean, cache-busted browser session (single login
 * to respect the 5/min login throttle) captures every changed surface + its live value:
 *   - Banner       (Sidebar "System Status")  → real state, degrades gracefully
 *   - Redactions   (§1.4 "PII Redaction … N sanitized") → N > 0 (was 0)
 *   - Flow strip   assembled / sanitized / denied / approved
 *   - Simulator    default MCP server = CONNECTED + dry-run decision
 * Saves PNGs (full page + per-surface crops, light+dark) and prints a JSON of live values.
 */
import fs from "node:fs";
import { launchBrowser, login, BASE } from "./mcp_page_typesim.mjs";

const OUT = process.env.SHOT_DIR || "mcp-parallel/findings/mcp-page/PROOF";
const TAB = `${BASE}/?tab=firewall-1-4`;

const numAfter = (t, w) => { const m = new RegExp("([\\d,]+)\\s+" + w, "i").exec(t || ""); return m ? Number(m[1].replace(/,/g, "")) : null; };

async function readSysStatus(page) {
  return page.evaluate(() => {
    const h = [...document.querySelectorAll("h3")].find((e) => /System Status/i.test(e.textContent || ""));
    if (!h) return null;
    const b = h.parentElement;
    return { line: (b.querySelector("p")?.textContent || "").trim(), label: (b.querySelectorAll("span")[0]?.textContent || "").trim() };
  });
}

async function main() {
  fs.mkdirSync(OUT, { recursive: true });
  const report = { url: TAB, servedFromViteDev: true, live: {}, ok: false };
  const { browser, context, page } = await launchBrowser({ viewport: { width: 1440, height: 1400 } });
  await context.setExtraHTTPHeaders({ "Cache-Control": "no-cache", Pragma: "no-cache" });
  try {
    await login(page);
    // hard, cache-busted load
    await page.goto(`${TAB}&_cb=${Date.now()}`, { waitUntil: "networkidle", timeout: 120000 });
    await page.getByText(/MCP Guardrails|PII Redaction|MCP Servers/i).first().waitFor({ state: "visible", timeout: 60000 });
    await page.waitForTimeout(3000); // let threat-feed + servers load

    // full page (light)
    await page.screenshot({ path: `${OUT}/01-full-page-light.png`, fullPage: true });

    // Banner
    report.live.banner = await readSysStatus(page);

    // Redaction flow strip
    const mainTxt = await page.evaluate(() => document.querySelector("main")?.innerText || "");
    report.live.flowStrip = {
      assembled: numAfter(mainTxt, "assembled"),
      sanitized: numAfter(mainTxt, "sanitized"),
      denied: numAfter(mainTxt, "denied"),
      approved: numAfter(mainTxt, "approved"),
    };
    // crop the flow strip (the region with the four §1.4 stage nodes)
    const strip = page.getByText(/PII Redaction/i).first();
    await strip.scrollIntoViewIfNeeded().catch(() => {});
    await page.screenshot({ path: `${OUT}/02-flowstrip-redactions.png` });

    // Simulator default + dry-run decision
    await page.getByText(/MCP Policy Simulator/i).first().scrollIntoViewIfNeeded().catch(() => {});
    await page.waitForTimeout(1500);
    report.live.simulatorDefaultServer = await page.evaluate(() => {
      const lbl = [...document.querySelectorAll("label")].find((l) => /MCP Server/i.test(l.textContent || ""));
      const sel = lbl && lbl.parentElement.querySelector("select");
      return sel ? (sel.options[sel.selectedIndex]?.text || "").trim() : null;
    });
    await page.screenshot({ path: `${OUT}/03-simulator-default.png` });
    // run a dry-run to show a decision renders
    await page.getByRole("button", { name: /Evaluate Policies/i }).click().catch(() => {});
    await page.getByText(/DRY-RUN · HTTP/i).first().waitFor({ state: "visible", timeout: 30000 }).catch(() => {});
    await page.waitForTimeout(800);
    report.live.simulatorDecisionShown = await page.evaluate(() => /DRY-RUN · HTTP \d/.test(document.querySelector("main")?.innerText || ""));
    await page.screenshot({ path: `${OUT}/04-simulator-decision.png` });

    // dark theme full page
    await page.evaluate(() => document.documentElement.classList.add("dark"));
    await page.goto(`${TAB}&_cb=${Date.now()}`, { waitUntil: "networkidle", timeout: 120000 });
    await page.waitForTimeout(3000);
    await page.evaluate(() => document.documentElement.classList.add("dark"));
    await page.waitForTimeout(500);
    await page.screenshot({ path: `${OUT}/05-full-page-dark.png`, fullPage: true });

    report.checks = {
      banner_reflects_real_state: !!report.live.banner && /Protected|Degraded|Offline|Connecting/i.test(report.live.banner.label),
      redactions_nonzero: (report.live.flowStrip.sanitized || 0) > 0,
      assembled_present: (report.live.flowStrip.assembled || 0) > 0,
      simulator_default_connected: /connected/i.test(report.live.simulatorDefaultServer || ""),
      simulator_decision_renders: report.live.simulatorDecisionShown === true,
    };
    report.ok = Object.values(report.checks).every(Boolean);
    console.log(JSON.stringify(report, null, 2));
    console.log(report.ok
      ? `PROOF: LIVE ON ${BASE} — banner="${report.live.banner?.line}/${report.live.banner?.label}", sanitized=${report.live.flowStrip.sanitized}, assembled=${report.live.flowStrip.assembled}, simDefault="${report.live.simulatorDefaultServer}"`
      : "PROOF: some checks failed — see report");
  } catch (e) {
    report.error = e.message;
    await page.screenshot({ path: `${OUT}/99-error.png` }).catch(() => {});
    console.error("PROOF FAIL:", e.message);
  } finally {
    fs.writeFileSync(`${OUT}/report.json`, JSON.stringify(report, null, 2));
    await browser.close();
    process.exit(report.ok ? 0 : 1);
  }
}
main();
