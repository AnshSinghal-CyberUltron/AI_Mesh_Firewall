/**
 * MCP-page cleanup items 19-20 — MCP Policy Simulator defaults to a CONNECTED server
 * (not a Failed one), and dry-run + live-call both WORK and render a decision.
 *
 * item 19 bug: the simulator defaulted to `list[0]` — the first registered server,
 * whatever its state — so a Failed/0-tool server could be pre-selected, making the
 * sandbox dead on arrival. Fix: default to the first connected server WITH tools;
 * show each server's connection status + tool count in the dropdown; give graceful
 * "no tools / not connected" guidance.
 *
 * Proves LIVE on ?tab=firewall-1-4:
 *   A. default MCP Server option is CONNECTED (text contains "connected · N tools")
 *   B. a Tool is auto-selected (non-empty)
 *   C. Dry-Run "Evaluate Policies" → renders a verdict (ALLOW/BLOCK/REDACT/ERROR)
 *      with "DRY-RUN · HTTP <status>" and a Matched Policies/Rules section
 *   D. Live "Invoke Tool" → renders a verdict with "LIVE · HTTP <status>"
 */
import fs from "node:fs";
import { launchBrowser, login, BASE } from "./mcp_page_typesim.mjs";

const OUT = process.env.SHOT_DIR || "mcp-parallel/findings/mcp-page/cleanup19";
const TAB = `${BASE}/?tab=firewall-1-4`;
const VERDICTS = /\b(ALLOW|BLOCK|REDACT|ERROR|ALLOWED|BLOCKED|REDACTED)\b/i;

// read the <select> whose preceding label contains `labelText`
const readSelect = (page, labelText) =>
  page.evaluate((lt) => {
    const labels = [...document.querySelectorAll("label")];
    const lbl = labels.find((l) => new RegExp(lt, "i").test(l.textContent || ""));
    if (!lbl) return null;
    // the select is the next select sibling within the same field wrapper
    const wrap = lbl.parentElement;
    const sel = wrap && wrap.querySelector("select");
    if (!sel) return null;
    const opt = sel.options[sel.selectedIndex];
    return { value: sel.value, text: (opt?.text || "").trim(), count: sel.options.length };
  }, labelText);

async function runAndReadVerdict(page, runLabel, expectMode) {
  await page.getByRole("button", { name: runLabel }).click();
  // wait for the verdict panel (shows "<MODE> · HTTP <status>")
  await page.getByText(new RegExp(`${expectMode} · HTTP`, "i")).first().waitFor({ state: "visible", timeout: 45000 });
  // Read the FULL main text — the verdict panel is within it (its "· HTTP" line
  // is what we just waited for). Slice a window AROUND the "· HTTP" marker so the
  // pattern checks see the verdict label + matched-policy sections, not just the
  // left-column form that sits above it.
  return page.evaluate(() => {
    const full = document.querySelector("main")?.innerText || "";
    const idx = full.search(/(DRY-RUN|LIVE) · HTTP \d/);
    // include a little before (the verdict label sits just above the HTTP line)
    return idx >= 0 ? full.slice(Math.max(0, idx - 120), idx + 500) : full;
  });
}

async function main() {
  fs.mkdirSync(OUT, { recursive: true });
  const report = { checkpoint: "cleanup-19-20", ok: false };
  const { browser, page } = await launchBrowser();
  const consoleErrors = [];
  page.on("pageerror", (e) => consoleErrors.push("pageerror: " + String(e.message || e)));
  page.on("console", (m) => { if (m.type() === "error") consoleErrors.push(m.text().slice(0, 160)); });

  try {
    await login(page);
    await page.goto(TAB, { waitUntil: "domcontentloaded", timeout: 120000 });
    await page.getByText(/MCP Policy Simulator/i).first().waitFor({ state: "visible", timeout: 60000 });
    await page.getByText(/MCP Policy Simulator/i).first().scrollIntoViewIfNeeded();
    // let the server list + tools load and the default selection settle
    await page.waitForTimeout(2500);

    const server = await readSelect(page, "MCP Server");
    const tool = await readSelect(page, "^\\s*Tool");
    report.defaultServer = server;
    report.defaultTool = tool;
    await page.screenshot({ path: `${OUT}/A-default-connected.png` });

    // A + B
    report.serverIsConnected = !!server && /connected/i.test(server.text);
    report.toolAutoSelected = !!tool && !!tool.value;

    // C — dry-run
    const dryTxt = await runAndReadVerdict(page, /Evaluate Policies/i, "DRY-RUN");
    await page.screenshot({ path: `${OUT}/C-dryrun-verdict.png` });
    report.dryRun = {
      text: dryTxt,
      hasVerdict: VERDICTS.test(dryTxt),
      hasHttp: /DRY-RUN · HTTP \d/i.test(dryTxt),
      hasDecisionDetail: /Matched Polic|Matched Rules|No policies matched/i.test(dryTxt),
    };

    // D — live call
    await page.getByRole("button", { name: /Live Call/i }).click();
    await page.waitForTimeout(400);
    const liveTxt = await runAndReadVerdict(page, /Invoke Tool/i, "LIVE");
    await page.screenshot({ path: `${OUT}/D-live-verdict.png` });
    report.liveCall = {
      text: liveTxt,
      hasVerdict: VERDICTS.test(liveTxt),
      hasHttp: /LIVE · HTTP \d/i.test(liveTxt),
    };

    report.consoleErrors = consoleErrors;
    report.item1920Pass =
      report.serverIsConnected &&
      report.toolAutoSelected &&
      report.dryRun.hasVerdict && report.dryRun.hasHttp && report.dryRun.hasDecisionDetail &&
      report.liveCall.hasVerdict && report.liveCall.hasHttp &&
      consoleErrors.length === 0;
    report.ok = true;

    console.log(JSON.stringify(report, null, 2));
    console.log(
      report.item1920Pass
        ? `CLEANUP-19-20: PASS — default server "${server?.text}" (connected); tool "${tool?.value}"; dry-run + live-call both render a decision`
        : "CLEANUP-19-20: FAIL",
    );
  } catch (e) {
    report.error = e.message;
    await page.screenshot({ path: `${OUT}/99-error.png` }).catch(() => {});
    console.error("CLEANUP-19-20 FAIL:", e.message);
  } finally {
    fs.writeFileSync(`${OUT}/report.json`, JSON.stringify(report, null, 2));
    await browser.close();
    process.exit(report.item1920Pass ? 0 : 1);
  }
}
main();
