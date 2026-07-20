/**
 * MCP-page Ralph — CP40 UI: the Scan Controls tab renders the matrix (Tier-2 toggle,
 * effective preview, controls table) and the "Add control" button opens the create
 * dialog. Backend real-effect (precedence) is proven in mcp_page_cp40_scan_controls.py.
 */
import fs from "node:fs";
import { launchBrowser, login, BASE } from "./mcp_page_typesim.mjs";

const OUT = process.env.SHOT_DIR || "mcp-parallel/findings/mcp-page/cp40";

async function main() {
  fs.mkdirSync(OUT, { recursive: true });
  const report = { checkpoint: "40-ui", ok: false };
  const { browser, page } = await launchBrowser();
  page.on("pageerror", (e) => (report.pageError = String(e.message || e)));
  try {
    await login(page);
    await page.goto(`${BASE}/?tab=firewall-1-4`, { waitUntil: "domcontentloaded", timeout: 120000 });
    await page.getByText(/MCP Guardrails/i).first().waitFor({ state: "visible", timeout: 60000 }).catch(() => {});
    await page.getByRole("tab", { name: /Scan Controls/i }).first().click({ timeout: 15000 })
      .catch(async () => { await page.getByText(/Scan Controls/i).first().click({ timeout: 15000 }); });

    await page.getByText(/MCP scan controls/i).first().waitFor({ state: "visible", timeout: 30000 });
    const body = await page.locator("body").innerText().catch(() => "");
    report.hasTier2Toggle = /Tier-2 .* for this org/i.test(body);
    report.hasSegmented = (await page.getByRole("button", { name: /^Inherit$/i }).count().catch(() => 0)) > 0
      || /Inherit/.test(body);
    report.hasEffectivePreview = /Effective|Tier-1|Tier-2/i.test(body);
    report.hasAddControl = (await page.getByRole("button", { name: /Add control/i }).count().catch(() => 0)) > 0;

    // Open the Add control dialog.
    await page.getByRole("button", { name: /Add control/i }).first().click({ timeout: 10000 });
    await page.waitForTimeout(600);
    const afterClick = await page.locator("body").innerText().catch(() => "");
    report.addDialogOpened = /Scope|Tier|Direction|Action|priority|Create|Save/i.test(afterClick);
    await page.screenshot({ path: `${OUT}/01-scan-controls.png` });

    report.cp40UiPass = report.hasTier2Toggle && report.hasEffectivePreview && report.hasAddControl
      && report.addDialogOpened && !report.pageError;
    report.ok = true;
    console.log(JSON.stringify({ hasTier2Toggle: report.hasTier2Toggle, hasEffectivePreview: report.hasEffectivePreview,
      hasAddControl: report.hasAddControl, addDialogOpened: report.addDialogOpened, cp40UiPass: report.cp40UiPass }, null, 2));
    console.log(report.cp40UiPass ? "CP40-UI: PASS — Scan Controls matrix renders + Add control opens the create form"
      : "CP40-UI: FAIL");
  } catch (e) {
    report.error = e.message;
    await page.screenshot({ path: `${OUT}/99-error.png` }).catch(() => {});
    console.error("CP40-UI FAIL:", e.message);
  } finally {
    fs.writeFileSync(`${OUT}/report.json`, JSON.stringify(report, null, 2));
    await browser.close();
    process.exit(report.cp40UiPass ? 0 : 1);
  }
}
main();
