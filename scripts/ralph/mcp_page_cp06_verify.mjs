/**
 * MCP-page Ralph — Checkpoint 06: comprehensive modal-typing verification.
 * Types LONG comma+space strings char-by-char into EVERY stdio field and asserts
 * final value correct + focus never drops, across 4 viewports × 2 themes (light/
 * dark) with zero console errors. Uses the CP01 harness (keyboard.type, no fill).
 */
import fs from "node:fs";
import { launchBrowser, login, openRegisterDialog, typeSim, BASE } from "./mcp_page_typesim.mjs";

const OUT = process.env.SHOT_DIR || "mcp-parallel/findings/mcp-page/cp06";
const VIEWPORTS = [
  { w: 1440, h: 1200 }, { w: 1024, h: 900 }, { w: 768, h: 1024 }, { w: 375, h: 812 },
];
const THEMES = ["light", "dark"];
// long comma+space strings per field
const FIELDS = {
  name: "srv,alpha,beta gamma,delta",
  description: "line one, line two, and three, four",
  command: "npx",
  args: "-y,@scope/pkg,--flag value,--k v,extra,y",
  env: "KEY1=a,b,c\nKEY2=hello world,x",
};

async function setTheme(page, theme) {
  await page.evaluate((t) => {
    localStorage.setItem("zeroshield_theme", t);
    document.documentElement.classList.toggle("dark", t === "dark");
  }, theme);
}
async function openWithRetry(page, tries = 3) {
  for (let i = 0; i < tries; i++) { try { await openRegisterDialog(page); return; } catch (e) { if (i === tries - 1) throw e; await page.waitForTimeout(1500); } }
}

async function main() {
  fs.mkdirSync(OUT, { recursive: true });
  const report = { checkpoint: "06", combos: [], ok: false, allPass: false, error: null };
  const { browser, context, page } = await launchBrowser();
  const consoleErrors = [];
  page.on("console", (m) => { if (m.type() === "error") consoleErrors.push(m.text().slice(0, 140)); });
  page.on("pageerror", (e) => consoleErrors.push("PAGEERROR: " + String(e.message || e).slice(0, 140)));
  try {
    await login(page);
    for (const vp of VIEWPORTS) {
      for (const theme of THEMES) {
        const combo = { viewport: `${vp.w}x${vp.h}`, theme, fields: {}, pass: false, consoleErrors: [] };
        const before = consoleErrors.length;
        try {
          await page.setViewportSize({ width: vp.w, height: vp.h });
          await page.goto(`${BASE}/?tab=firewall-1-4`, { waitUntil: "domcontentloaded", timeout: 60000 });
          await setTheme(page, theme);
          await page.locator("text=/MCP Guardrails/i").first().waitFor({ state: "visible", timeout: 40000 });
          await openWithRetry(page);
          // switch to stdio to expose command/args/env
          const tsel = page.getByLabel("Transport");
          if (await tsel.count()) { await tsel.selectOption("stdio").catch(() => {}); await page.waitForTimeout(300); }

          const targets = {
            name: page.getByPlaceholder("my-mcp-server"),
            description: page.getByPlaceholder("Optional description"),
            command: page.getByPlaceholder("npx"),
            args: page.getByPlaceholder("-y, @playwright/mcp@latest"),
            env: page.getByPlaceholder("GITHUB_TOKEN=ghp_xxx"),
          };
          let allFieldsOk = true;
          for (const [key, text] of Object.entries(FIELDS)) {
            const loc = targets[key];
            if (!(await loc.count().catch(() => 0))) { combo.fields[key] = "absent"; allFieldsOk = false; continue; }
            const r = await typeSim(page, loc, text, { stopOnFocusLoss: false, clickTimeout: 6000, fast: true });
            const ok = r.valueCorrect && r.focusKeptAllKeystrokes;
            combo.fields[key] = ok ? "PASS" : `FAIL(value=${JSON.stringify(r.finalValue)} focus=${r.focusKeptAllKeystrokes})`;
            if (!ok) allFieldsOk = false;
          }
          await page.screenshot({ path: `${OUT}/${vp.w}-${theme}.png` });
          combo.consoleErrors = consoleErrors.slice(before);
          combo.pass = allFieldsOk && combo.consoleErrors.length === 0;
        } catch (e) {
          combo.error = String(e.message || e).slice(0, 160);
        }
        report.combos.push(combo);
        console.log(`[${combo.viewport} ${theme}] ${combo.pass ? "PASS" : "FAIL"} ${JSON.stringify(combo.fields)}${combo.consoleErrors.length ? " consoleErr:" + combo.consoleErrors.length : ""}${combo.error ? " ERR:" + combo.error : ""}`);
      }
    }
    report.allPass = report.combos.length === VIEWPORTS.length * THEMES.length && report.combos.every((c) => c.pass);
    report.ok = true;
    console.log(report.allPass ? `CP06: ALL ${report.combos.length} combos PASS (values correct, focus kept, no console errors)` : "CP06: some combos FAILED");
  } catch (e) {
    report.error = e.message;
    console.error("CP06 FAIL:", e.message);
  } finally {
    fs.writeFileSync(`${OUT}/report.json`, JSON.stringify(report, null, 2));
    await browser.close();
    process.exit(report.allPass ? 0 : 1);
  }
}
main();
