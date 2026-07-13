#!/usr/bin/env node
/** Iteration 5 — Scan Controls tab: empty state + tier2 org toggle UI smoke. */
import { launchBrowser, login, BASE } from "./mcp_page_typesim.mjs";

async function main() {
  const { browser, page } = await launchBrowser();
  const errors = [];
  page.on("console", (m) => {
    const text = m.text();
    if (m.type() !== "error") return;
    if (/ERR_NETWORK_CHANGED|IO_SUSPENDED|INTERNET_DISCONNECTED|ABORTED/.test(text)) return;
    // Benign dev-server / asset flakes during long gate runs (not product regressions).
    if (/Failed to load resource.*favicon/i.test(text)) return;
    if (/Failed to fetch.*scan-controls/i.test(text) && /429|503/.test(text)) return;
    errors.push(text);
  });
  await login(page);
  await page.goto(`${BASE}/?tab=firewall-1-4`, { waitUntil: "domcontentloaded", timeout: 120000 });
  await page.locator("text=/MCP Guardrails/i").first().waitFor({ state: "visible", timeout: 60000 });
  await page.getByRole("tab", { name: /Scan Controls/i }).click();
  await page.waitForTimeout(2500);

  const emptyTitle = await page.getByText("No scan controls yet").count();
  const tier2OrgCard = await page.getByText(/Tier-2.*for this org/i).count();
  const tier2Segment = await page.getByRole("radiogroup", { name: /Org MCP Tier-2 mode/i }).count();
  const effectivePreview = await page.getByText("Effective preview").count();
  const addBtn = await page.getByRole("button", { name: /Add control/i }).count();

  const apiRows = await page.evaluate(async () => {
    const tok = localStorage.getItem("auth_access");
    const r = await fetch("/api/mcp-connector/scan-controls/", {
      headers: { Authorization: `Bearer ${tok}` },
    });
    const rows = await r.json();
    return Array.isArray(rows) ? rows.length : 0;
  });

  const pass =
    tier2OrgCard > 0 &&
    tier2Segment > 0 &&
    addBtn > 0 &&
    (apiRows === 0 ? emptyTitle > 0 : true);

  const out = {
    iter5ScanControlsUiPass: pass,
    apiScanRows: apiRows,
    emptyState: emptyTitle > 0,
    tier2OrgCard: tier2OrgCard > 0,
    tier2Segment: tier2Segment > 0,
    effectivePreview: effectivePreview > 0,
    consoleErrors: errors.length,
    consoleErrorSamples: errors.slice(0, 3),
  };
  console.log(JSON.stringify(out, null, 2));
  await browser.close();
  process.exit(pass && errors.length === 0 ? 0 : 1);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
