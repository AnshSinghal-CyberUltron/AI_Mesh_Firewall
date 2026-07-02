/**
 * MCP-page Ralph — CP33 UI: the catalog presets render in the Register modal and a
 * preset connects via the CLIENT FLOW (click preset → fills form → Register →
 * connect-first → tools discovered → lists). Confirms the catalog is UI-accessible
 * (the API matrix in mcp_page_cp33_catalog.py proves the full 10-server outcomes).
 */
import fs from "node:fs";
import { launchBrowser, login, openRegisterDialog, BASE } from "./mcp_page_typesim.mjs";

const OUT = process.env.SHOT_DIR || "mcp-parallel/findings/mcp-page/cp33ui";
const PRESET = process.env.CP33_PRESET || "Memory MCP"; // a local server that connects fast

async function main() {
  fs.mkdirSync(OUT, { recursive: true });
  const report = { checkpoint: "33-ui", ok: false };
  const { browser, page } = await launchBrowser();
  page.on("pageerror", (e) => (report.pageError = String(e.message || e)));
  const modalOpen = () => page.getByText("Register MCP Server", { exact: false }).count().then((c) => c > 0).catch(() => false);
  try {
    await login(page);
    await openRegisterDialog(page);
    // The catalog presets render as clickable chips/buttons in the modal.
    const presetNames = ["GitHub MCP", "Linear MCP", "Context7 MCP", "Playwright MCP", "Semgrep MCP",
      "Memory MCP", "Filesystem MCP", "Fetch MCP", "Everything MCP", "Vibe Check MCP"];
    const present = {};
    for (const n of presetNames) present[n] = await page.getByText(n, { exact: false }).count().then((c) => c > 0).catch(() => false);
    report.presetsPresent = present;
    report.presetCount = Object.values(present).filter(Boolean).length;

    // Client flow: click the preset → it fills the form → Register.
    await page.getByText(PRESET, { exact: false }).first().click({ timeout: 10000 });
    await page.waitForTimeout(500);
    await page.getByRole("button", { name: /^Register$/i }).click({ timeout: 10000 });

    // Success path: modal closes on tool discovery and the server lists WITH tools.
    let closed = false;
    for (let i = 0; i < 120; i++) { if (!(await modalOpen())) { closed = true; break; } await page.waitForTimeout(1000); }
    await page.waitForTimeout(1500);
    const body = await page.locator("body").innerText().catch(() => "");
    report.modalClosed = closed;
    report.serverListedWithTools = /\b([1-9]\d*)\s*tools?\b/i.test(body) && /Memory|memory/i.test(body);
    await page.screenshot({ path: `${OUT}/01-after-connect.png` });

    // cleanup: delete the server we created (best-effort via the card menu is fragile; leave it — API harness purges cp33-*).
    report.cp33UiPass = report.presetCount >= 10 && report.modalClosed && report.serverListedWithTools && !report.pageError;
    report.ok = true;
    console.log(JSON.stringify({ presetCount: report.presetCount, modalClosed: report.modalClosed,
      serverListedWithTools: report.serverListedWithTools, cp33UiPass: report.cp33UiPass }, null, 2));
    console.log(report.cp33UiPass ? `CP33-UI: PASS — ${report.presetCount}/10 catalog presets render; "${PRESET}" connected + listed tools via the client flow`
      : "CP33-UI: FAIL");
  } catch (e) {
    report.error = e.message;
    await page.screenshot({ path: `${OUT}/99-error.png` }).catch(() => {});
    console.error("CP33-UI FAIL:", e.message);
  } finally {
    fs.writeFileSync(`${OUT}/report.json`, JSON.stringify(report, null, 2));
    await browser.close();
    process.exit(report.cp33UiPass ? 0 : 1);
  }
}
main();
