/**
 * MCP-page Ralph — Checkpoint 10 (register flow comprehensive verify, type-sim):
 *   A) connect error  → modal STAYS OPEN with inline error (not listed)
 *   B) connected/0 tools → modal STAYS OPEN with inline error (NEVER lists 0 tools)
 *   C) tools discovered  → modal CLOSES and server lists WITH tools
 * Inputs typed via keyboard (type-sim, no fill). Backend route-mocked so all three
 * are deterministic (real sandbox connect = Section J).
 */
import fs from "node:fs";
import { launchBrowser, login, openRegisterDialog, typeSim } from "./mcp_page_typesim.mjs";

const OUT = process.env.SHOT_DIR || "mcp-parallel/findings/mcp-page/cp10";
const MOCK_ID = "cp10-mock-id";
const modalOpen = (page) => page.getByText("Register MCP Server", { exact: false }).count().then((c) => c > 0).catch(() => false);
const hasAlert = (page) => page.locator('[role="alert"]').count().then((c) => c > 0).catch(() => false);

async function main() {
  fs.mkdirSync(OUT, { recursive: true });
  const report = { checkpoint: "10", scenarios: {}, ok: false, error: null };
  const { browser, page } = await launchBrowser();
  page.on("pageerror", (e) => (report.pageError = String(e.message || e)));

  // Mutable backend behaviour driven per scenario.
  const cur = { name: "", sync: {}, list: [] };
  await page.route(/\/api\/mcp-connector\/servers\/$/, (route) => {
    const m = route.request().method();
    if (m === "POST") return route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify({ id: MOCK_ID, name: cur.name, transport: "stdio", tools_count: 0 }) });
    if (m === "GET") return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(cur.list) });
    return route.continue();
  });
  await page.route(/\/api\/mcp-connector\/servers\/[^/]+\/tools\/$/, (route) => {
    if (route.request().method() === "POST") return route.fulfill(cur.sync);
    return route.continue();
  });
  await page.route(/\/api\/mcp-connector\/servers\/[^/]+\/$/, (route) => {
    if (route.request().method() === "DELETE") return route.fulfill({ status: 204, body: "" });
    return route.continue();
  });

  async function runScenario(key, { name, sync, list, expectClosed }) {
    cur.name = name; cur.sync = sync; cur.list = list;
    await openRegisterDialog(page);
    await typeSim(page, page.getByPlaceholder("my-mcp-server"), name, { fast: true, stopOnFocusLoss: false });
    await page.getByLabel("Transport").selectOption("stdio").catch(() => {});
    await page.waitForTimeout(150);
    await typeSim(page, page.getByPlaceholder("npx"), "npx", { fast: true, stopOnFocusLoss: false });
    await typeSim(page, page.getByPlaceholder("-y, @playwright/mcp@latest"), "-y, @modelcontextprotocol/server-everything", { fast: true, stopOnFocusLoss: false });
    await page.getByRole("button", { name: /^Register$/i }).click();

    let closed = false;
    for (let i = 0; i < 24; i++) { if (!(await modalOpen(page))) { closed = true; break; } await page.waitForTimeout(500); }
    await page.waitForTimeout(500);
    const openNow = await modalOpen(page);
    const alert = await hasAlert(page);
    const bodyText = await page.locator("body").innerText().catch(() => "");
    const listedZeroTools = new RegExp(`${name}[\\s\\S]{0,120}?0\\s*tools`, "i").test(bodyText); // 0-tools card for our server
    const listedWithTools = closed && bodyText.includes(name) && /\b3\s*tools?\b/i.test(bodyText);
    await page.screenshot({ path: `${OUT}/${key}.png` });

    const res = { closed, modalOpen: openNow, inlineError: alert, listedZeroTools, listedWithTools };
    // pass logic per scenario
    if (expectClosed) res.pass = closed && listedWithTools && !listedZeroTools;
    else res.pass = !closed && openNow && alert && !listedZeroTools;
    report.scenarios[key] = res;

    // reset for next scenario: cancel (deletes orphan) if still open
    if (openNow) { await page.getByRole("button", { name: /^Cancel$/i }).click().catch(() => {}); await page.waitForTimeout(400); }
    return res;
  }

  try {
    await login(page);
    const errSync = { status: 502, contentType: "application/json", body: JSON.stringify({ error: "Upstream MCP error: connection refused" }) };
    const zeroSync = { status: 200, contentType: "application/json", body: JSON.stringify({ synced: 0, tools: [], connection_status: "connected" }) };
    const okSync = { status: 200, contentType: "application/json", body: JSON.stringify({ synced: 3, tools: [{ tool_name: "echo" }, { tool_name: "add" }, { tool_name: "x" }], connection_status: "connected" }) };
    const okList = [{ id: MOCK_ID, name: "cp10-ok", server_slug: "cp10-ok", transport: "stdio", tools_count: 3, connection_status: "connected", auth_type: "none", enabled: true }];

    await runScenario("A_error", { name: "cp10-err", sync: errSync, list: [], expectClosed: false });
    await runScenario("B_zerotools", { name: "cp10-zero", sync: zeroSync, list: [], expectClosed: false });
    await runScenario("C_success", { name: "cp10-ok", sync: okSync, list: okList, expectClosed: true });

    const s = report.scenarios;
    report.neverListedZeroTools = !s.A_error.listedZeroTools && !s.B_zerotools.listedZeroTools && !s.C_success.listedZeroTools;
    report.cp10Pass = s.A_error.pass && s.B_zerotools.pass && s.C_success.pass && report.neverListedZeroTools;
    report.ok = true;

    console.log(JSON.stringify({
      A_error: s.A_error, B_zerotools: s.B_zerotools, C_success: s.C_success,
      neverListedZeroTools: report.neverListedZeroTools, cp10Pass: report.cp10Pass,
    }, null, 2));
    console.log(report.cp10Pass ? "CP10: PASS — success lists with tools; failure/0-tools stay open with inline error; NEVER lists 0 tools" : "CP10: FAIL");
  } catch (e) {
    report.error = e.message;
    await page.screenshot({ path: `${OUT}/99-error.png` }).catch(() => {});
    console.error("CP10 FAIL:", e.message);
  } finally {
    fs.writeFileSync(`${OUT}/report.json`, JSON.stringify(report, null, 2));
    await browser.close();
    process.exit(report.cp10Pass ? 0 : 1);
  }
}
main();
