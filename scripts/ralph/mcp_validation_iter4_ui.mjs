#!/usr/bin/env node
/** Iteration 4 — verify "Scanning off" badge when 0 scan controls (firewall-1-4). */
import { launchBrowser, login, BASE } from "./mcp_page_typesim.mjs";

async function main() {
  const { browser, page } = await launchBrowser();
  const errors = [];
  page.on("console", (m) => {
    if (m.type() === "error" && !/ERR_NETWORK_CHANGED|IO_SUSPENDED|INTERNET_DISCONNECTED|ABORTED/.test(m.text())) {
      errors.push(m.text());
    }
  });
  await login(page);
  await page.goto(`${BASE}/?tab=firewall-1-4`, { waitUntil: "domcontentloaded", timeout: 120000 });
  await page.locator("text=/MCP Guardrails/i").first().waitFor({ state: "visible", timeout: 60000 });
  await page.getByRole("tab", { name: /MCP Servers/i }).click();
  await page.waitForTimeout(2000);

  const scanOff = page.getByText("Scanning off", { exact: true });
  const count = await scanOff.count();
  const scanSelectDisabled = await page
    .locator('select[aria-label="Default scan enforcement"]')
    .first()
    .isDisabled()
    .catch(() => false);

  const apiRows = await page.evaluate(async () => {
    const tok = localStorage.getItem("auth_access");
    const r = await fetch("/api/mcp-connector/scan-controls/", {
      headers: { Authorization: `Bearer ${tok}` },
    });
    const rows = await r.json();
    return Array.isArray(rows) ? rows.length : 0;
  });

  const pass = apiRows === 0 ? count > 0 && scanSelectDisabled : true;
  const out = {
    iter4UiPass: pass,
    apiScanRows: apiRows,
    scanningOffBadges: count,
    scanSelectDisabled,
    consoleErrors: errors.length,
  };
  console.log(JSON.stringify(out, null, 2));
  await browser.close();
  process.exit(pass && errors.length === 0 ? 0 : 1);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
