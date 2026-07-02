/**
 * MCP-page Ralph — Checkpoint 03: type-sim EVERY modal field with a
 * comma+space string and record per-field which drop separators / lose focus
 * ("remount"). Reuses the CP01 harness (keyboard.type, no fill).
 *
 * Run:
 *   PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium-browser \
 *   BASE_URL=http://127.0.0.1:8180 node scripts/ralph/mcp_page_cp03_allfields_repro.mjs
 */
import fs from "node:fs";
import { launchBrowser, login, openRegisterDialog, typeSim } from "./mcp_page_typesim.mjs";

const OUT = process.env.SHOT_DIR || "mcp-parallel/findings/mcp-page/cp03";
const S = "a,b,c x,y"; // comma + space probe

async function openWithRetry(page, tries = 3) {
  for (let i = 0; i < tries; i++) {
    try { await openRegisterDialog(page); return; } catch (e) { if (i === tries - 1) throw e; await page.waitForTimeout(1500); }
  }
}
async function firstPresent(page, cands) {
  for (const c of cands) { if ((await c.count().catch(() => 0)) > 0) return c.first(); }
  return null;
}
async function probe(page, record, key, locator, text = S) {
  if (!locator) { record[key] = { present: false }; return; }
  try {
    // scroll into the modal + focus with a short timeout so a covered field
    // records an error instead of hanging the whole run.
    await locator.scrollIntoViewIfNeeded({ timeout: 4000 }).catch(() => {});
    const r = await typeSim(page, locator, text, { stopOnFocusLoss: false });
    record[key] = {
      present: true, expected: r.expected, got: r.finalValue, valueCorrect: r.valueCorrect,
      commas: `${r.commasKept}/${r.commasTyped}`, commaDrop: r.commaDrop,
      focusKept: r.focusKeptAllKeystrokes, focusLostAt: r.focusLossAtKeystroke,
    };
  } catch (e) {
    record[key] = { present: true, unreachable: true, error: String(e.message || e).slice(0, 120) };
  }
}

async function main() {
  fs.mkdirSync(OUT, { recursive: true });
  const report = { checkpoint: "03", probe: S, fields: {}, ok: false, error: null };
  const { browser, page } = await launchBrowser();
  page.on("pageerror", (e) => (report.pageError = String(e.message || e)));
  try {
    await login(page);
    await openWithRetry(page);

    // Always-visible fields
    await probe(page, report.fields, "name", page.getByPlaceholder("my-mcp-server"));
    await probe(page, report.fields, "url", page.getByPlaceholder("https://my-server.example.com/mcp"));
    await probe(page, report.fields, "description", await firstPresent(page, [page.getByPlaceholder(/description/i)]));

    // stdio fields (exact placeholders from the definitive modal field map)
    const tsel = page.getByLabel("Transport");
    if (await tsel.count()) { await tsel.selectOption("stdio").catch(() => {}); await page.waitForTimeout(400); }
    await probe(page, report.fields, "command", page.getByPlaceholder("npx"));
    await probe(page, report.fields, "args", page.getByPlaceholder("-y, @playwright/mcp@latest"));
    await probe(page, report.fields, "env", page.getByPlaceholder("GITHUB_TOKEN=ghp_xxx"), "K=a,b\nJ=c d");

    // http + Custom Header auth fields
    if (await tsel.count()) { await tsel.selectOption("streamable-http").catch(() => {}); await page.waitForTimeout(300); }
    const authSel = page.getByLabel(/authentication type|auth type/i);
    if (await authSel.count()) {
      await authSel.selectOption({ label: "Custom Header" }).catch(() => {});
      await page.waitForTimeout(300);
      const hdrInputs = page.locator('[role=presentation] input[type="text"], [role=dialog] input[type="text"]');
      const n = await hdrInputs.count();
      // header key/value are the trailing two text inputs after name/url
      if (n >= 2) {
        await probe(page, report.fields, "header_key", hdrInputs.nth(n - 2));
        await probe(page, report.fields, "header_value", hdrInputs.nth(n - 1));
      }
      await authSel.selectOption({ label: "Bearer Token" }).catch(() => {});
      await page.waitForTimeout(200);
      await probe(page, report.fields, "bearer", await firstPresent(page, [page.getByPlaceholder(/bearer|token|sk-/i), page.locator('[role=presentation] input[type="password"]').last()]));
    }

    // Per-field record
    const buggy = Object.entries(report.fields).filter(([, r]) => r.present && (r.commaDrop || !r.focusKept));
    report.record = Object.fromEntries(Object.entries(report.fields).map(([k, r]) => [k,
      !r.present ? "absent" : (r.commaDrop ? `SEPARATOR-DROP(commas ${r.commas})` : (!r.focusKept ? `FOCUS-LOSS@${r.focusLostAt}` : "clean")),
    ]));
    report.buggyFields = buggy.map(([k]) => k);
    report.ok = true;

    console.log(JSON.stringify({ record: report.record, buggyFields: report.buggyFields }, null, 2));
    console.log(report.buggyFields.length ? `CP03: buggy fields → ${report.buggyFields.join(", ")}` : "CP03: no buggy fields");
  } catch (e) {
    report.error = e.message;
    await page.screenshot({ path: `${OUT}/99-error.png` }).catch(() => {});
    console.error("CP03 FAIL:", e.message);
  } finally {
    fs.writeFileSync(`${OUT}/report.json`, JSON.stringify(report, null, 2));
    await browser.close();
    process.exit(report.ok ? 0 : 1);
  }
}
main();
