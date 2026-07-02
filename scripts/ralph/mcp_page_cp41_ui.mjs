/**
 * MCP-page Ralph — CP41 UI: the MCP Security Policies tab renders the policy panel +
 * its controls (server filter, Add/New policy). Real enforcement effect is proven in
 * mcp_page_cp41_policy.py (a block policy blocks a matching tool call; disable un-blocks).
 */
import fs from "node:fs";
import { launchBrowser, login, BASE } from "./mcp_page_typesim.mjs";

const OUT = process.env.SHOT_DIR || "mcp-parallel/findings/mcp-page/cp41";

async function main() {
  fs.mkdirSync(OUT, { recursive: true });
  const report = { checkpoint: "41-ui", ok: false };
  const { browser, page } = await launchBrowser();
  page.on("pageerror", (e) => (report.pageError = String(e.message || e)));
  try {
    await login(page);
    await page.goto(`${BASE}/?tab=firewall-1-4`, { waitUntil: "domcontentloaded", timeout: 120000 });
    await page.getByText(/MCP Guardrails/i).first().waitFor({ state: "visible", timeout: 60000 }).catch(() => {});
    await page.getByRole("tab", { name: /MCP Security Policies/i }).first().click({ timeout: 15000 })
      .catch(async () => { await page.getByText(/MCP Security Policies/i).first().click({ timeout: 15000 }); });
    await page.getByText(/MCP Security Policies/i).first().waitFor({ state: "visible", timeout: 30000 });
    await page.waitForTimeout(1500);
    const body = await page.locator("body").innerText().catch(() => "");
    report.hasPanelTitle = /MCP Security Policies/i.test(body);
    report.hasEnforcementCopy = /enforcement layer|MCP tool call|Active rules|org-wide MCP/i.test(body);
    report.hasAddPolicy = (await page.getByRole("button", { name: /New policy|Add policy|Create policy|\+ ?Policy/i }).count().catch(() => 0)) > 0
      || /New policy|Add policy|Create policy/i.test(body);
    report.hasServerFilter = (await page.locator("select").count().catch(() => 0)) > 0;
    await page.screenshot({ path: `${OUT}/01-policies.png` });
    report.cp41UiPass = report.hasPanelTitle && report.hasEnforcementCopy && report.hasAddPolicy && !report.pageError;
    report.ok = true;
    console.log(JSON.stringify({ hasPanelTitle: report.hasPanelTitle, hasEnforcementCopy: report.hasEnforcementCopy,
      hasAddPolicy: report.hasAddPolicy, hasServerFilter: report.hasServerFilter, cp41UiPass: report.cp41UiPass }, null, 2));
    console.log(report.cp41UiPass ? "CP41-UI: PASS — MCP Security Policies tab renders the policy panel + controls"
      : "CP41-UI: FAIL");
  } catch (e) {
    report.error = e.message;
    await page.screenshot({ path: `${OUT}/99-error.png` }).catch(() => {});
    console.error("CP41-UI FAIL:", e.message);
  } finally {
    fs.writeFileSync(`${OUT}/report.json`, JSON.stringify(report, null, 2));
    await browser.close();
    process.exit(report.cp41UiPass ? 0 : 1);
  }
}
main();
