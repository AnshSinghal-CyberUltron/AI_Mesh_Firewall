/**
 * MCP-page Ralph — Checkpoint 02/03: reproduce the modal comma-drop / focus-loss
 * with TYPE-SIMULATION. Types "a,b,c --flag,x" char-by-char into the stdio
 * Arguments (comma-separated) field, plus name/url/command, and records which
 * fields drop commas or lose focus. Uses the CP01 harness (keyboard.type, no fill).
 *
 * Run:
 *   PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium-browser \
 *   BASE_URL=http://127.0.0.1:8180 node scripts/ralph/mcp_page_cp02_args_repro.mjs
 */
import fs from "node:fs";
import { launchBrowser, login, openRegisterDialog, typeSim } from "./mcp_page_typesim.mjs";

const OUT = process.env.SHOT_DIR || "mcp-parallel/findings/mcp-page/cp02";
const ARGS_STRING = "a,b,c --flag,x";

async function openWithRetry(page, tries = 3) {
  for (let i = 0; i < tries; i++) {
    try {
      await openRegisterDialog(page);
      return true;
    } catch (e) {
      if (i === tries - 1) throw e;
      await page.waitForTimeout(1500);
    }
  }
  return false;
}

/** Try several strategies to locate the stdio "Arguments" (comma-separated) input. */
async function findArgsField(page) {
  const cands = [
    page.getByLabel(/arg/i),
    page.locator('label:has-text("Args") ~ input, label:has-text("Arguments") ~ input').first(),
    page.getByPlaceholder(/comma|arg|--/i),
    page.locator('input[placeholder*="server-everything"]'),
  ];
  for (const c of cands) {
    if ((await c.count().catch(() => 0)) > 0) return c.first();
  }
  return null;
}

async function main() {
  fs.mkdirSync(OUT, { recursive: true });
  const report = { checkpoint: "02/03", argsString: ARGS_STRING, ok: false, fields: {}, error: null };
  const { browser, page } = await launchBrowser();
  page.on("pageerror", (e) => (report.pageError = String(e.message || e)));
  try {
    await login(page);
    await openWithRetry(page);
    await page.screenshot({ path: `${OUT}/01-modal.png` });

    // name / url (always visible)
    const nameLoc = page.getByPlaceholder("my-mcp-server");
    if (await nameLoc.count()) report.fields.name = await typeSim(page, nameLoc, "srv,one,two", { stopOnFocusLoss: false });
    const urlLoc = page.getByPlaceholder("https://my-server.example.com/mcp");
    if (await urlLoc.count()) report.fields.url = await typeSim(page, urlLoc, "https://x.io/a,b", { stopOnFocusLoss: false });

    // switch to stdio to expose command + args
    const tsel = page.getByLabel("Transport");
    if (await tsel.count()) { await tsel.selectOption("stdio").catch(() => {}); await page.waitForTimeout(400); }

    const cmdLoc = page.getByPlaceholder(/npx|command/i);
    if (await cmdLoc.count()) report.fields.command = await typeSim(page, cmdLoc.first(), "npx -y srv,a", { stopOnFocusLoss: false });

    const argsLoc = await findArgsField(page);
    if (argsLoc) {
      report.fields.args = await typeSim(page, argsLoc, ARGS_STRING, { stopOnFocusLoss: false });
      report.argsFieldFound = true;
    } else {
      report.argsFieldFound = false;
      // dump stdio inputs to identify the args field for a follow-up
      report.stdioInputs = await page.$$eval("input,textarea", (els) =>
        els.map((e) => ({ tag: e.tagName, ph: e.placeholder || null, label: e.labels?.[0]?.textContent?.trim() || null })));
    }
    await page.screenshot({ path: `${OUT}/02-after-typing.png` });

    // Summarize: which fields dropped commas or lost focus
    const drops = Object.entries(report.fields).filter(([, r]) => r.commaDrop);
    const focusLoss = Object.entries(report.fields).filter(([, r]) => !r.focusKeptAllKeystrokes);
    report.commaDropFields = drops.map(([k, r]) => ({ field: k, expected: r.expected, got: r.finalValue, commas: `${r.commasKept}/${r.commasTyped}` }));
    report.focusLossFields = focusLoss.map(([k, r]) => ({ field: k, lostAt: r.focusLossAtKeystroke }));
    report.bugReproduced = drops.length > 0 || focusLoss.length > 0;
    report.ok = true;

    console.log(JSON.stringify({
      argsFieldFound: report.argsFieldFound,
      commaDropFields: report.commaDropFields,
      focusLossFields: report.focusLossFields,
      bugReproduced: report.bugReproduced,
      argsResult: report.fields.args ? { expected: report.fields.args.expected, got: report.fields.args.finalValue, commas: `${report.fields.args.commasKept}/${report.fields.args.commasTyped}`, focusKept: report.fields.args.focusKeptAllKeystrokes } : null,
    }, null, 2));
    console.log(report.bugReproduced ? "CP02: BUG REPRODUCED (comma-drop / focus-loss)" : "CP02: no comma-drop/focus-loss observed");
  } catch (e) {
    report.error = e.message;
    await page.screenshot({ path: `${OUT}/99-error.png` }).catch(() => {});
    console.error("CP02 FAIL:", e.message);
  } finally {
    fs.writeFileSync(`${OUT}/report.json`, JSON.stringify(report, null, 2));
    await browser.close();
    process.exit(report.ok ? 0 : 1);
  }
}

main();
