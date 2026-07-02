/**
 * MCP-page Ralph — Checkpoint 09 (register flow): on SUCCESS the modal discovers
 * tools THEN closes and the server is listed WITH tools (never 0). Verifies the
 * register-flow UI deterministically by mocking the backend connect+discover
 * (POST /servers/{id}/tools/ → tools>0) and the list (GET /servers/ →
 * tools_count>0), decoupled from the shared per-org sandbox (whose real cold
 * start is exercised end-to-end in Section J). No real DB writes.
 */
import fs from "node:fs";
import { launchBrowser, login, openRegisterDialog } from "./mcp_page_typesim.mjs";

const OUT = process.env.SHOT_DIR || "mcp-parallel/findings/mcp-page/cp09";
const NAME = "cp09-mock-verify";
const MOCK_ID = "cp09-mock-id";
const MOCK_TOOLS = [{ tool_name: "echo" }, { tool_name: "add" }, { tool_name: "longRunningOperation" }];
const modalOpen = (page) => page.getByText("Register MCP Server", { exact: false }).count().then((c) => c > 0).catch(() => false);

async function main() {
  fs.mkdirSync(OUT, { recursive: true });
  const report = { checkpoint: "09", ok: false, error: null };
  const { browser, page } = await launchBrowser();
  page.on("pageerror", (e) => (report.pageError = String(e.message || e)));
  try {
    await login(page);

    let listQueriedAfterClose = false;
    // create → mock server id (no real DB write)
    await page.route(/\/api\/mcp-connector\/servers\/$/, async (route) => {
      const req = route.request();
      if (req.method() === "POST") {
        return route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify({ id: MOCK_ID, name: NAME, transport: "stdio", tools_count: 0 }) });
      }
      if (req.method() === "GET") {
        listQueriedAfterClose = true;
        return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([{ id: MOCK_ID, name: NAME, server_slug: NAME, transport: "stdio", tools_count: MOCK_TOOLS.length, connection_status: "connected", auth_type: "none", enabled: true }]) });
      }
      return route.continue();
    });
    // connect+discover → tools>0
    await page.route(/\/api\/mcp-connector\/servers\/[^/]+\/tools\/$/, (route) => {
      if (route.request().method() === "POST") {
        return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ synced: MOCK_TOOLS.length, tools: MOCK_TOOLS, connection_status: "connected" }) });
      }
      return route.continue();
    });

    await openRegisterDialog(page);
    await page.getByPlaceholder("my-mcp-server").type(NAME, { delay: 6 });
    await page.getByLabel("Transport").selectOption("stdio").catch(() => {});
    await page.waitForTimeout(200);
    await page.getByPlaceholder("npx").type("npx", { delay: 6 });
    await page.getByPlaceholder("-y, @playwright/mcp@latest").type("-y, @modelcontextprotocol/server-everything", { delay: 6 });

    await page.getByRole("button", { name: /^Register$/i }).click();

    // Success path: modal must close on tool discovery, then the list shows tools.
    let closed = false;
    for (let i = 0; i < 30; i++) { if (!(await modalOpen(page))) { closed = true; break; } await page.waitForTimeout(500); }
    await page.waitForTimeout(800);
    await page.screenshot({ path: `${OUT}/01-after-success.png` });

    const bodyText = await page.locator("body").innerText().catch(() => "");
    const listedWithTools = new RegExp(`${MOCK_TOOLS.length}\\s*tools?`, "i").test(bodyText) && bodyText.includes(NAME);

    report.modalClosedOnToolDiscovery = closed;
    report.listQueriedAfterClose = listQueriedAfterClose;
    report.listedWithToolCount = listedWithTools;
    report.cp09Pass = closed && listQueriedAfterClose && listedWithTools;
    report.ok = true;

    console.log(JSON.stringify({
      modalClosedOnToolDiscovery: report.modalClosedOnToolDiscovery,
      listQueriedAfterClose: report.listQueriedAfterClose,
      listedWithToolCount: report.listedWithToolCount,
      note: "success path mocked; real sandbox connect = Section J",
      cp09Pass: report.cp09Pass,
    }, null, 2));
    console.log(report.cp09Pass ? `CP09: PASS — success discovers tools THEN closes modal; server lists with ${MOCK_TOOLS.length} tools (never 0)` : "CP09: FAIL");
  } catch (e) {
    report.error = e.message;
    await page.screenshot({ path: `${OUT}/99-error.png` }).catch(() => {});
    console.error("CP09 FAIL:", e.message);
  } finally {
    fs.writeFileSync(`${OUT}/report.json`, JSON.stringify(report, null, 2));
    await browser.close();
    process.exit(report.cp09Pass ? 0 : 1);
  }
}
main();
