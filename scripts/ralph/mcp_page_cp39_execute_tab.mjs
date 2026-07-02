/**
 * MCP-page Ralph — CP39: the Tool Execution tab's every control drives a REAL
 * execution. Registers Everything (has echo) via the API, then in the browser:
 * pick the server select → pick the echo tool select → type JSON args → click
 * Execute Tool → assert the result panel shows the deterministic echo output.
 */
import fs from "node:fs";
import { launchBrowser, login, BASE } from "./mcp_page_typesim.mjs";

const OUT = process.env.SHOT_DIR || "mcp-parallel/findings/mcp-page/cp39";
const SLUG = process.env.CP39_SLUG; // server_slug of the registered Everything
const MSG = "cp39-exec-hello-777";

async function main() {
  fs.mkdirSync(OUT, { recursive: true });
  const report = { checkpoint: "39", ok: false };
  const { browser, page } = await launchBrowser();
  page.on("pageerror", (e) => (report.pageError = String(e.message || e)));
  let toolCallFired = false;
  page.on("response", (r) => {
    if (r.url().endsWith("/api/mcp-connector/tools/call/") && r.request().method() === "POST") toolCallFired = true;
  });
  try {
    await login(page);
    await page.goto(`${BASE}/?tab=firewall-1-4`, { waitUntil: "domcontentloaded", timeout: 120000 });
    await page.getByText(/MCP Guardrails/i).first().waitFor({ state: "visible", timeout: 60000 }).catch(() => {});
    await page.getByRole("tab", { name: /Tool Execution/i }).first().click({ timeout: 15000 })
      .catch(async () => { await page.getByText(/Tool Execution/i).first().click({ timeout: 15000 }); });
    await page.getByText(/Invoke a discovered MCP tool/i).first().waitFor({ state: "visible", timeout: 30000 });

    // Server select → the cp39 Everything server (by value=server_slug).
    const serverSel = page.getByLabel("Execution server");
    for (let i = 0; i < 20; i++) {
      const opts = await serverSel.locator("option").allTextContents().catch(() => []);
      if (opts.some((o) => o.includes(SLUG))) break;
      await page.waitForTimeout(500);
    }
    await serverSel.selectOption(SLUG);
    await page.waitForTimeout(600);

    // Tool select → echo (by visible label).
    const toolSel = page.getByLabel("Tool to execute");
    await toolSel.selectOption({ label: /echo/i }).catch(async () => {
      // fallback: pick the option whose text starts with "echo"
      const vals = await toolSel.locator("option").evaluateAll((os) =>
        os.filter((o) => /^echo\b/i.test(o.textContent || "")).map((o) => o.value));
      if (vals[0]) await toolSel.selectOption(vals[0]);
    });
    await page.waitForTimeout(400);

    // Arguments textarea → deterministic JSON.
    const args = page.getByLabel("Tool arguments (JSON)");
    await args.fill(JSON.stringify({ message: MSG }));

    // Execute.
    await page.getByRole("button", { name: /Execute Tool/i }).click({ timeout: 10000 });
    // Wait for the result to render.
    let resultText = "";
    for (let i = 0; i < 40; i++) {
      resultText = await page.locator("body").innerText().catch(() => "");
      if (new RegExp(`Echo:\\s*${MSG}`, "i").test(resultText)) break;
      await page.waitForTimeout(500);
    }
    await page.screenshot({ path: `${OUT}/01-execute-result.png` });

    report.toolCallFired = toolCallFired;
    report.resultShowsEcho = new RegExp(`Echo:\\s*${MSG}`, "i").test(resultText);
    report.cp39Pass = report.toolCallFired && report.resultShowsEcho && !report.pageError;
    report.ok = true;
    console.log(JSON.stringify({ toolCallFired: report.toolCallFired, resultShowsEcho: report.resultShowsEcho,
      cp39Pass: report.cp39Pass }, null, 2));
    console.log(report.cp39Pass ? `CP39: PASS — Tool Execution tab drove a real echo call; result shows "Echo: ${MSG}"`
      : "CP39: FAIL");
  } catch (e) {
    report.error = e.message;
    await page.screenshot({ path: `${OUT}/99-error.png` }).catch(() => {});
    console.error("CP39 FAIL:", e.message);
  } finally {
    fs.writeFileSync(`${OUT}/report.json`, JSON.stringify(report, null, 2));
    await browser.close();
    process.exit(report.cp39Pass ? 0 : 1);
  }
}
main();
