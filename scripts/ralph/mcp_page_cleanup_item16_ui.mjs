/**
 * MCP-page cleanup item 16 — LIVE UI proof that the §1.4 "PII Redaction … sanitized"
 * flow-node is no longer 0. The node value maps to threatFeedActionCounts.redact
 * (firewall-module-utils.js:286 → firewall-submodules.jsx:137). Before CLEANUP-16 the
 * collapse threat-feed reported redact=0 (scan-capped), so this read "0 sanitized"; after
 * the fix it reflects the real distinct-request redaction count.
 *
 * Asserts, on ?tab=firewall-1-4 in BOTH themes:
 *   - "PII Redaction … N sanitized" with N > 0
 *   - "Context Fields … M assembled" with M large (distinct requests, uncapped)
 *   - the funnel partition is consistent: assembled >= sanitized + denied + approved-ish
 */
import fs from "node:fs";
import { launchBrowser, login, BASE } from "./mcp_page_typesim.mjs";

const OUT = process.env.SHOT_DIR || "mcp-parallel/findings/mcp-page/cleanup16";

const numAfter = (text, word) => {
  const m = new RegExp("([\\d,]+)\\s+" + word, "i").exec(text || "");
  return m ? Number(m[1].replace(/,/g, "")) : null;
};

async function main() {
  fs.mkdirSync(OUT, { recursive: true });
  const report = { checkpoint: "cleanup-16-ui", themes: {}, ok: false };
  const { browser, page } = await launchBrowser();
  page.on("pageerror", (e) => (report.pageerror = String(e.message || e)));

  try {
    await login(page);
    await page.goto(`${BASE}/?tab=firewall-1-4`, { waitUntil: "domcontentloaded", timeout: 120000 });
    await page.getByText(/PII Redaction/i).first().waitFor({ state: "visible", timeout: 60000 });

    for (const theme of ["light", "dark"]) {
      await page.evaluate((t) => {
        const el = document.documentElement;
        if (t === "dark") el.classList.add("dark"); else el.classList.remove("dark");
      }, theme);

      // The flow-node strip populates after the threat-feed fetch resolves (~0.5s);
      // poll up to ~12s for a non-zero "sanitized" so we don't race the first paint.
      let assembled = null, sanitized = null, denied = null, approved = null;
      for (let i = 0; i < 24; i++) {
        const txt = await page.evaluate(() => document.querySelector("main")?.innerText || "");
        assembled = numAfter(txt, "assembled");
        sanitized = numAfter(txt, "sanitized");
        denied = numAfter(txt, "denied");
        approved = numAfter(txt, "approved");
        if (sanitized != null && sanitized > 0 && assembled != null) break;
        await page.waitForTimeout(500);
      }
      await page.screenshot({ path: `${OUT}/${theme}-1-4-flowstrip.png` });
      report.themes[theme] = { assembled, sanitized, denied, approved };
    }

    const t = report.themes;
    const sOk = Object.values(t).every((x) => typeof x.sanitized === "number" && x.sanitized > 0);
    const aOk = Object.values(t).every((x) => typeof x.assembled === "number" && x.assembled > 0);
    // funnel sanity: assembled (distinct requests) >= sanitized + denied (subset outcomes)
    const partOk = Object.values(t).every(
      (x) => x.assembled >= (x.sanitized || 0) + (x.denied || 0),
    );
    report.noPageError = !report.pageerror;
    report.item16UiPass = sOk && aOk && partOk && report.noPageError;
    report.ok = true;

    console.log(JSON.stringify(report, null, 2));
    console.log(
      report.item16UiPass
        ? `CLEANUP-16-UI: PASS — §1.4 "PII Redaction" shows ${t.light.sanitized} sanitized (light) / ${t.dark.sanitized} (dark); assembled=${t.light.assembled}; no longer 0`
        : "CLEANUP-16-UI: FAIL",
    );
  } catch (e) {
    report.error = e.message;
    await page.screenshot({ path: `${OUT}/99-error.png` }).catch(() => {});
    console.error("CLEANUP-16-UI FAIL:", e.message);
  } finally {
    fs.writeFileSync(`${OUT}/report.json`, JSON.stringify(report, null, 2));
    await browser.close();
    process.exit(report.item16UiPass ? 0 : 1);
  }
}
main();
