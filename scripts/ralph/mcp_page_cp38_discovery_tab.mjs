/**
 * MCP-page Ralph — CP38: the Tool Discovery tab renders REAL data (tool cards with
 * name + server badge + expandable input schema) and its Refresh control works.
 * Data source = GET /api/mcp-connector/tools/ (verified real in the API harness).
 */
import fs from "node:fs";
import { launchBrowser, login, BASE } from "./mcp_page_typesim.mjs";

const OUT = process.env.SHOT_DIR || "mcp-parallel/findings/mcp-page/cp38";

async function main() {
  fs.mkdirSync(OUT, { recursive: true });
  const report = { checkpoint: "38", ok: false };
  const { browser, page } = await launchBrowser();
  page.on("pageerror", (e) => (report.pageError = String(e.message || e)));
  let toolsRefetched = false;
  page.on("response", (r) => {
    if (r.url().endsWith("/api/mcp-connector/tools/") && r.request().method() === "GET") toolsRefetched = true;
  });
  try {
    await login(page);
    await page.goto(`${BASE}/?tab=firewall-1-4`, { waitUntil: "domcontentloaded", timeout: 120000 });
    await page.getByText(/MCP Guardrails/i).first().waitFor({ state: "visible", timeout: 60000 }).catch(() => {});
    // Click the Tool Discovery tab (Radix TabsTrigger → role="tab").
    await page.getByRole("tab", { name: /Tool Discovery/i }).first().click({ timeout: 15000 })
      .catch(async () => { await page.getByText(/Tool Discovery/i).first().click({ timeout: 15000 }); });
    await page.getByText(/Tools discovered across all connected MCP servers/i).first()
      .waitFor({ state: "visible", timeout: 30000 });
    // Wait for the tool cards to populate (poll for a real tool name).
    let cardCount = 0;
    for (let i = 0; i < 30; i++) {
      cardCount = await page.locator("h4.font-mono").count().catch(() => 0);
      if (cardCount > 0) break;
      await page.waitForTimeout(600);
    }
    const bodyText = await page.locator("body").innerText().catch(() => "");
    report.toolCardCount = cardCount;
    report.hasRealToolNames = /browser_click|echo|create_entities|list_allowed_directories|query-docs|fetch/i.test(bodyText);
    report.hasServerBadge = /Playwright|Everything|Memory|Filesystem|Context7/i.test(bodyText);
    report.hasInputSchema = (await page.getByText(/Input Schema/i).count().catch(() => 0)) > 0;

    // Refresh control re-fetches.
    toolsRefetched = false;
    await page.getByRole("button", { name: /Refresh tools/i }).first().click({ timeout: 10000 }).catch(() => {});
    for (let i = 0; i < 12; i++) { if (toolsRefetched) break; await page.waitForTimeout(400); }
    report.refreshRefetched = toolsRefetched;
    await page.screenshot({ path: `${OUT}/01-tool-discovery.png` });

    report.cp38Pass = cardCount > 0 && report.hasRealToolNames && report.hasInputSchema && report.refreshRefetched && !report.pageError;
    report.ok = true;
    console.log(JSON.stringify({ toolCardCount: cardCount, hasRealToolNames: report.hasRealToolNames,
      hasServerBadge: report.hasServerBadge, hasInputSchema: report.hasInputSchema,
      refreshRefetched: report.refreshRefetched, cp38Pass: report.cp38Pass }, null, 2));
    console.log(report.cp38Pass ? `CP38: PASS — Tool Discovery tab renders ${cardCount} real tool cards + Refresh works`
      : "CP38: FAIL");
  } catch (e) {
    report.error = e.message;
    await page.screenshot({ path: `${OUT}/99-error.png` }).catch(() => {});
    console.error("CP38 FAIL:", e.message);
  } finally {
    fs.writeFileSync(`${OUT}/report.json`, JSON.stringify(report, null, 2));
    await browser.close();
    process.exit(report.cp38Pass ? 0 : 1);
  }
}
main();
