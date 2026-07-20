/**
 * MCP-page Ralph — CP31: the §1.4 Context Assembly flow-nodes (Context Fields
 * assembled / PII Redaction sanitized / Size Check denied / Final Context
 * approved) must show REAL, uncapped numbers from the server action_counts
 * aggregate — not the broken "500 assembled / 0 / 0 / 0" (capped results.length +
 * mismapped stages). Asserts assembled is NOT pinned at the 500 page-cap and that
 * "approved" (monitor+allow) is populated (no longer swallowed).
 */
import fs from "node:fs";
import { launchBrowser, login, BASE } from "./mcp_page_typesim.mjs";

const OUT = process.env.SHOT_DIR || "mcp-parallel/findings/mcp-page/cp31";
const numOf = (s) => Number(String(s || "").replace(/[^0-9]/g, "")) || 0;

async function main() {
  fs.mkdirSync(OUT, { recursive: true });
  const report = { checkpoint: "31", nodes: {}, ok: false };
  const { browser, page } = await launchBrowser();
  page.on("pageerror", (e) => (report.pageError = String(e.message || e)));
  try {
    await login(page);
    // Capture the threat-feed response (the source of count + action_counts).
    const feedPromise = page.waitForResponse(
      (r) => r.url().includes("/api/security/threat-feed/") && r.url().includes("source=mcp_scan"),
      { timeout: 90000 },
    ).catch(() => null);
    await page.goto(`${BASE}/?tab=firewall-1-4`, { waitUntil: "domcontentloaded", timeout: 120000 });
    await page.getByText(/Traffic path/i).first().waitFor({ state: "visible", timeout: 60000 });
    const feedRes = await feedPromise;
    if (feedRes) {
      const body = await feedRes.json().catch(() => ({}));
      report.feedEnvelope = { count: body.count, action_counts: body.action_counts };
    }
    // Poll until the Context Fields node populates (threat-feed → state → render).
    const readNode = async (label) => {
      const card = page.getByText(new RegExp(`^${label}$`, "i")).locator("xpath=..");
      return ((await card.innerText().catch(() => "")) || "").replace(/\s+/g, " ").trim();
    };
    for (let i = 0; i < 30; i++) {
      const t = await readNode("Context Fields");
      if (numOf(t) > 0) break;
      await page.waitForTimeout(600);
    }
    for (const label of ["Context Fields", "PII Redaction", "Size Check", "Final Context"]) {
      report.nodes[label] = await readNode(label);
    }
    await page.screenshot({ path: `${OUT}/01-flow.png` });

    const assembled = numOf(report.nodes["Context Fields"]);
    const sanitized = numOf(report.nodes["PII Redaction"]);
    const denied = numOf(report.nodes["Size Check"]);
    const approved = numOf(report.nodes["Final Context"]);
    report.parsed = { assembled, sanitized, denied, approved };

    // The old bug pinned assembled=500 (page cap) and approved=0 (monitor swallowed).
    report.assembledNotCapped = assembled !== 500 && assembled > 0;      // real total (live ~4000)
    report.approvedPopulated = approved > 0;                            // monitor+allow now counted
    report.consistent = assembled >= denied + sanitized;               // stages ⊆ total
    report.cp31Pass = report.assembledNotCapped && report.approvedPopulated && report.consistent && !report.pageError;
    report.ok = true;

    console.log(JSON.stringify({ nodes: report.nodes, parsed: report.parsed,
      assembledNotCapped: report.assembledNotCapped, approvedPopulated: report.approvedPopulated,
      consistent: report.consistent, cp31Pass: report.cp31Pass }, null, 2));
    console.log(report.cp31Pass
      ? `CP31: PASS — §1.4 stages show REAL numbers (assembled=${assembled} not 500-cap, approved=${approved}>0)`
      : "CP31: FAIL");
  } catch (e) {
    report.error = e.message;
    await page.screenshot({ path: `${OUT}/99-error.png` }).catch(() => {});
    console.error("CP31 FAIL:", e.message);
  } finally {
    fs.writeFileSync(`${OUT}/report.json`, JSON.stringify(report, null, 2));
    await browser.close();
    process.exit(report.cp31Pass ? 0 : 1);
  }
}
main();
