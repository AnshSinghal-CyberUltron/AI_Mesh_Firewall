/**
 * MCP-page Ralph — Checkpoint 08 (register flow): on failure show a clear error on
 * the SAME modal, keep it open, and let the user FIX + RETRY. Verifies the full
 * cycle via network capture: register-fail → inline error → fix URL → Retry fires
 * a PATCH (applies the fix) + a re-sync (re-connects), modal stays open the whole
 * time. Cancel at the end deletes the orphan row (never list with 0 tools).
 */
import fs from "node:fs";
import { launchBrowser, login, openRegisterDialog } from "./mcp_page_typesim.mjs";

const OUT = process.env.SHOT_DIR || "mcp-parallel/findings/mcp-page/cp08";
const modalOpen = (page) => page.getByText("Register MCP Server", { exact: false }).count().then((c) => c > 0).catch(() => false);
const alertText = (page) => page.locator('[role="alert"]').first().textContent().catch(() => null);

async function waitForError(page, ms = 20000) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    if ((await page.locator('[role="alert"]').count().catch(() => 0)) > 0) return true;
    if (!(await modalOpen(page))) return false; // modal closed = no error path
    await page.waitForTimeout(400);
  }
  return false;
}

async function main() {
  fs.mkdirSync(OUT, { recursive: true });
  const report = { checkpoint: "08", ok: false, error: null, net: [] };
  const { browser, page } = await launchBrowser();
  page.on("request", (r) => {
    const u = r.url();
    if (u.includes("/mcp-connector/servers/")) report.net.push(`${r.method()} ${u.split("/api")[1] || u}`);
  });
  page.on("pageerror", (e) => (report.pageError = String(e.message || e)));
  try {
    await login(page);
    await openRegisterDialog(page);
    await page.getByPlaceholder("my-mcp-server").type("cp08-verify", { delay: 8 });
    await page.getByPlaceholder("https://my-server.example.com/mcp").type("https://example.com/mcp", { delay: 8 });

    // 1) Register → fail → inline error, modal open
    await page.getByRole("button", { name: /^Register$/i }).click();
    const err1Shown = await waitForError(page);
    const err1 = await alertText(page);
    const open1 = await modalOpen(page);
    await page.screenshot({ path: `${OUT}/01-fail1.png` });
    const netAfter1 = [...report.net];

    // 2) FIX the URL (edit), then Retry
    const urlLoc = page.getByPlaceholder("https://my-server.example.com/mcp");
    await urlLoc.click();
    await page.keyboard.press(process.platform === "darwin" ? "Meta+A" : "Control+A");
    await page.keyboard.press("Delete");
    await urlLoc.type("https://example.org/mcp", { delay: 8 });
    const netBeforeRetry = report.net.length;
    await page.getByRole("button", { name: /Retry connection/i }).click();
    const err2Shown = await waitForError(page);
    const err2 = await alertText(page);
    const open2 = await modalOpen(page);
    await page.screenshot({ path: `${OUT}/02-fail2.png` });
    const retryNet = report.net.slice(netBeforeRetry);

    // 3) Cancel deletes the orphan row
    await page.getByRole("button", { name: /^Cancel$/i }).click().catch(() => {});
    await page.waitForTimeout(800);

    const createdPost = netAfter1.some((n) => /^POST \/mcp-connector\/servers\/$/.test(n));
    const firstSync = netAfter1.some((n) => /^POST \/mcp-connector\/servers\/[^/]+\/tools\/$/.test(n));
    const retryPatch = retryNet.some((n) => /^PATCH \/mcp-connector\/servers\/[^/]+\/$/.test(n));
    const retrySync = retryNet.some((n) => /^POST \/mcp-connector\/servers\/[^/]+\/tools\/$/.test(n));
    const deleteOrphan = report.net.some((n) => /^DELETE \/mcp-connector\/servers\/[^/]+\/$/.test(n));

    report.errorShownFirst = err1Shown;
    report.modalStayedOpenFirst = open1;
    report.errorShownAfterRetry = err2Shown;
    report.modalStayedOpenAfterRetry = open2;
    report.createdOnce = createdPost;
    report.firstSyncFired = firstSync;
    report.retryPatchFired = retryPatch; // fix applied
    report.retrySyncFired = retrySync;   // re-connected
    report.orphanDeletedOnCancel = deleteOrphan;
    report.cp08Pass =
      err1Shown && open1 && retryPatch && retrySync && open2 && createdPost && !retryNet.some((n) => /^POST \/mcp-connector\/servers\/$/.test(n));
    report.ok = true;

    console.log(JSON.stringify({
      errorShownFirst: err1Shown, err1: err1?.slice(0, 80),
      modalStayedOpenFirst: open1,
      retryPatchFired: retryPatch, retrySyncFired: retrySync,
      errorShownAfterRetry: err2Shown, err2: err2?.slice(0, 80),
      modalStayedOpenAfterRetry: open2,
      noDuplicateCreateOnRetry: !retryNet.some((n) => /^POST \/mcp-connector\/servers\/$/.test(n)),
      orphanDeletedOnCancel: deleteOrphan,
      cp08Pass: report.cp08Pass,
    }, null, 2));
    console.log(report.cp08Pass ? "CP08: PASS — clear error, modal stays open, fix+retry (PATCH+resync) works, no dup create" : "CP08: FAIL");
  } catch (e) {
    report.error = e.message;
    await page.screenshot({ path: `${OUT}/99-error.png` }).catch(() => {});
    console.error("CP08 FAIL:", e.message);
  } finally {
    fs.writeFileSync(`${OUT}/report.json`, JSON.stringify(report, null, 2));
    await browser.close();
    process.exit(report.cp08Pass ? 0 : 1);
  }
}
main();
