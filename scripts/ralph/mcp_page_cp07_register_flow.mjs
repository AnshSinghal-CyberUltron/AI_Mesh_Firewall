/**
 * MCP-page Ralph — Checkpoint 07 (Section B register flow): verify that Register
 * attempts the MCP connection INLINE and the modal STAYS OPEN during the attempt
 * (does NOT close on create). Registers an HTTP server whose sync fails
 * (example.com/mcp resolves so it registers, but is not an MCP server) and asserts
 * the modal remains open with a connecting/error state — then cancels (which
 * deletes the orphan row so nothing lists with 0 tools).
 */
import fs from "node:fs";
import { launchBrowser, login, openRegisterDialog, BASE } from "./mcp_page_typesim.mjs";

const OUT = process.env.SHOT_DIR || "mcp-parallel/findings/mcp-page/cp07";

async function modalOpen(page) {
  return (await page.getByText("Register MCP Server", { exact: false }).count().catch(() => 0)) > 0;
}

async function main() {
  fs.mkdirSync(OUT, { recursive: true });
  const report = { checkpoint: "07", ok: false, error: null };
  const { browser, page } = await launchBrowser();
  page.on("pageerror", (e) => (report.pageError = String(e.message || e)));
  try {
    await login(page);
    await openRegisterDialog(page);

    // Fill an HTTP server that resolves (registers) but fails to connect as MCP.
    await page.getByPlaceholder("my-mcp-server").fill(""); // login-safe non-typed field
    await page.getByPlaceholder("my-mcp-server").type("cp07-verify", { delay: 10 });
    // transport streamable-http (default), auth none
    const urlLoc = page.getByPlaceholder("https://my-server.example.com/mcp");
    await urlLoc.type("https://example.com/mcp", { delay: 10 });

    // Click Register; the OLD code closed the modal immediately on create.
    const beforeClick = await modalOpen(page);
    await page.getByRole("button", { name: /^Register$|^Retry connection$/i }).click();

    // Poll: within the connect attempt the modal must stay OPEN; capture connecting
    // label and/or the eventual inline error. Fail if the modal closes on create.
    let stayedOpen = true;
    let sawConnecting = false;
    let sawError = false;
    for (let i = 0; i < 40; i++) {
      const open = await modalOpen(page);
      if (!open) { stayedOpen = false; break; }
      const btnText = (await page.getByRole("button", { name: /Connecting|Retry connection|Register/i }).allTextContents().catch(() => [])).join(" ");
      if (/Connecting/i.test(btnText)) sawConnecting = true;
      const errCount = await page.locator('[role="alert"]').count().catch(() => 0);
      if (errCount > 0) { sawError = true; break; }
      await page.waitForTimeout(500);
    }
    await page.screenshot({ path: `${OUT}/01-after-register.png` });
    const errText = sawError ? (await page.locator('[role="alert"]').first().textContent().catch(() => "")) : null;

    report.beforeClickModalOpen = beforeClick;
    report.stayedOpenDuringConnect = stayedOpen;
    report.sawConnectingState = sawConnecting;
    report.sawInlineError = sawError;
    report.inlineErrorText = errText?.slice(0, 160) || null;
    // CP07 pass = modal did NOT close on create (stayed open through the inline connect attempt)
    report.cp07Pass = stayedOpen && (sawConnecting || sawError);

    // Cleanup: cancel deletes the orphan created row (never list with 0 tools).
    await page.getByRole("button", { name: /^Cancel$/i }).click().catch(() => {});
    await page.waitForTimeout(800);

    report.ok = true;
    console.log(JSON.stringify({
      stayedOpenDuringConnect: report.stayedOpenDuringConnect,
      sawConnectingState: report.sawConnectingState,
      sawInlineError: report.sawInlineError,
      inlineErrorText: report.inlineErrorText,
      cp07Pass: report.cp07Pass,
    }, null, 2));
    console.log(report.cp07Pass ? "CP07: PASS — Register attempts connection inline; modal stays open" : "CP07: FAIL — modal closed on create or no inline connect state");
  } catch (e) {
    report.error = e.message;
    await page.screenshot({ path: `${OUT}/99-error.png` }).catch(() => {});
    console.error("CP07 FAIL:", e.message);
  } finally {
    fs.writeFileSync(`${OUT}/report.json`, JSON.stringify(report, null, 2));
    await browser.close();
    process.exit(report.cp07Pass ? 0 : 1);
  }
}
main();
