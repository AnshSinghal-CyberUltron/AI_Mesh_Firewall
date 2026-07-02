/**
 * MCP-page Ralph — CP17 UI verify: on a sanitized sync failure the modal shows
 * (a) the branded message with "(Ref: …)" AND (b) a stable "Error code: MCP_…"
 * line, and stays open. Type-sim input; backend sync route-mocked to the exact
 * shape the CP17 backend now returns ({error, error_code, correlation_id}).
 */
import fs from "node:fs";
import { launchBrowser, login, openRegisterDialog, typeSim } from "./mcp_page_typesim.mjs";

const OUT = process.env.SHOT_DIR || "mcp-parallel/findings/mcp-page/cp17";
const MOCK_ID = "cp17-mock-id";
const REF = "abc123def456";
const CODE = "MCP_UNAVAILABLE";
const modalOpen = (page) => page.getByText("Register MCP Server", { exact: false }).count().then((c) => c > 0).catch(() => false);

async function main() {
  fs.mkdirSync(OUT, { recursive: true });
  const report = { checkpoint: "17-ui", ok: false, error: null };
  const { browser, page } = await launchBrowser();
  page.on("pageerror", (e) => (report.pageError = String(e.message || e)));

  await page.route(/\/api\/mcp-connector\/servers\/$/, (route) => {
    const m = route.request().method();
    if (m === "POST") return route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify({ id: MOCK_ID, name: "cp17-ui", transport: "streamable-http", tools_count: 0 }) });
    if (m === "GET") return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([]) });
    return route.continue();
  });
  await page.route(/\/api\/mcp-connector\/servers\/[^/]+\/tools\/$/, (route) => {
    if (route.request().method() === "POST") {
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
        synced: 0, tools: [], connection_status: "failed",
        error: `The MCP server could not be reached or returned an error. Verify the configuration and retry. (Ref: ${REF})`,
        error_code: CODE, correlation_id: REF,
      }) });
    }
    return route.continue();
  });
  await page.route(/\/api\/mcp-connector\/servers\/[^/]+\/$/, (route) => {
    if (route.request().method() === "DELETE") return route.fulfill({ status: 204, body: "" });
    return route.continue();
  });

  try {
    await login(page);
    await openRegisterDialog(page);
    await typeSim(page, page.getByPlaceholder("my-mcp-server"), "cp17-ui", { fast: true, stopOnFocusLoss: false });
    await page.getByLabel("Transport").selectOption("streamable-http").catch(() => {});
    await page.waitForTimeout(150);
    await typeSim(page, page.getByPlaceholder(/https:\/\/|example\.com|url/i).first(), "https://example.com/mcp", { fast: true, stopOnFocusLoss: false }).catch(() => {});
    await page.getByRole("button", { name: /^Register$/i }).click();

    // Modal must stay open with the inline error + code line.
    let stillOpen = false;
    for (let i = 0; i < 16; i++) { if (await modalOpen(page)) { stillOpen = true; } await page.waitForTimeout(400); if (i > 3 && stillOpen) break; }
    await page.waitForTimeout(400);
    const alertText = await page.locator('[role="alert"]').innerText().catch(() => "");
    await page.screenshot({ path: `${OUT}/01-inline-error-with-code.png` });

    report.stillOpen = await modalOpen(page);
    report.showsBrandedMessage = /could not be reached or returned an error/i.test(alertText);
    report.showsRef = new RegExp(`\\(ref:\\s*${REF}\\)`, "i").test(alertText);
    report.showsErrorCode = new RegExp(`error code:\\s*${CODE}`, "i").test(alertText);
    report.noLeak = !/<!doctype|exited with code|proc\.|sandbox-agent|gateway logs/i.test(alertText);
    report.cp17UiPass = report.stillOpen && report.showsBrandedMessage && report.showsRef && report.showsErrorCode && report.noLeak;
    report.ok = true;
    report.alertText = alertText.slice(0, 300);

    console.log(JSON.stringify({
      stillOpen: report.stillOpen, showsBrandedMessage: report.showsBrandedMessage,
      showsRef: report.showsRef, showsErrorCode: report.showsErrorCode, noLeak: report.noLeak,
      cp17UiPass: report.cp17UiPass, alertText: report.alertText,
    }, null, 2));
    console.log(report.cp17UiPass ? "CP17-UI: PASS — modal shows branded message + (Ref) + stable Error code, stays open, no leak" : "CP17-UI: FAIL");
  } catch (e) {
    report.error = e.message;
    await page.screenshot({ path: `${OUT}/99-error.png` }).catch(() => {});
    console.error("CP17-UI FAIL:", e.message);
  } finally {
    fs.writeFileSync(`${OUT}/report.json`, JSON.stringify(report, null, 2));
    await browser.close();
    process.exit(report.cp17UiPass ? 0 : 1);
  }
}
main();
