/**
 * MCP-page Ralph — Checkpoint 01: reusable TYPE-SIMULATION harness.
 *
 * Types like a REAL USER — char-by-char via page.keyboard.type(char,{delay:40})
 * (pressSequentially-equivalent) and clears via keyboard (Ctrl/Cmd+A → Delete).
 * It NEVER calls locator.fill() — that is the whole point: fill() sets .value in
 * one shot and masks the controlled-input remount/focus-loss + comma-drop bug,
 * whereas realistic keystrokes reproduce it.
 *
 * Exports (reused by checkpoints 02/03/06/10 …):
 *   launchBrowser(opts) → { browser, context, page }
 *   login(page)
 *   openRegisterDialog(page)
 *   clearByKeyboard(page, locator)          // NEVER fill
 *   typeSim(page, locator, text, opts)      // returns rich per-keystroke result
 *   describeActive(page)
 *
 * Run standalone (checkpoint-01 smoke test — proves the harness works e2e):
 *   PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium-browser \
 *   BASE_URL=http://127.0.0.1:8180 node scripts/ralph/mcp_page_typesim.mjs
 */
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import path from "node:path";
import fs from "node:fs";

const require = createRequire(import.meta.url);
const HERE = path.dirname(fileURLToPath(import.meta.url));
const { chromium } = require(path.join(HERE, "../../tests/e2e/node_modules/playwright"));

export const BASE = (process.env.BASE_URL || "http://127.0.0.1:8180").replace(/\/$/, "");
const EMAIL = process.env.TEST_EMAIL || "admin@zeroshield.io";
const PASS = process.env.TEST_PASSWORD || "Adm1n!Pass#2024";
const DEFAULT_DELAY = Number(process.env.TYPE_DELAY_MS || 40);

export async function launchBrowser({ viewport = { width: 1440, height: 1200 } } = {}) {
  const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH || "/usr/bin/chromium-browser";
  const browser = await chromium.launch({
    headless: true,
    executablePath,
    args: ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
  });
  const context = await browser.newContext({ viewport });
  const page = await context.newPage();
  return { browser, context, page };
}

export async function login(page) {
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle", timeout: 120000 });
  await page.locator("#email").fill(EMAIL); // login is not under test — fill is fine here
  await page.locator("#password").fill(PASS);
  const [res] = await Promise.all([
    page.waitForResponse((r) => r.url().includes("/api/auth/token/") && r.request().method() === "POST"),
    page.getByRole("button", { name: /^sign in$/i }).click(),
  ]);
  if (!res.ok()) throw new Error(`Login failed: ${res.status()}`);
  await page.waitForFunction(() => !location.pathname.startsWith("/login"), { timeout: 30000 });
}

export async function openRegisterDialog(page) {
  await page.goto(`${BASE}/?tab=firewall-1-4`, { waitUntil: "domcontentloaded", timeout: 120000 });
  await page.locator("text=/MCP Guardrails/i").first().waitFor({ state: "visible", timeout: 60000 });
  await page.getByRole("button", { name: /^Register Server$/i }).first().click();
  await page.getByText("Register MCP Server", { exact: false }).first().waitFor({ state: "visible", timeout: 15000 });
}

export async function describeActive(page) {
  return page.evaluate(() => {
    const ae = document.activeElement;
    if (!ae || ae === document.body) return { kind: "body", tag: "BODY" };
    return {
      kind: "element",
      tag: ae.tagName,
      type: ae.type || null,
      placeholder: ae.placeholder || null,
      ariaLabel: ae.getAttribute("aria-label"),
      id: ae.id || null,
      valueLen: "value" in ae ? String(ae.value || "").length : null,
    };
  });
}

/** Clear a focused field using ONLY the keyboard — never fill(). */
export async function clearByKeyboard(page, locator) {
  await locator.click();
  const mod = process.platform === "darwin" ? "Meta" : "Control";
  await page.keyboard.press(`${mod}+A`);
  await page.keyboard.press("Delete");
}

/** Is the target locator's element the current activeElement? */
async function keptFocus(page, locator) {
  const handle = await locator.elementHandle();
  if (!handle) return false;
  return page.evaluate((target) => document.activeElement === target, handle);
}

/**
 * Type `text` into `locator` like a real user, tracking focus + value per keystroke.
 * Uses page.keyboard.type(char,{delay}) — types into the FOCUSED element, so a
 * mid-type remount that drops focus causes subsequent chars to be lost (repro).
 * Returns comma-preservation + focus-retention diagnostics. NEVER calls fill().
 */
export async function typeSim(page, locator, text, opts = {}) {
  const delay = opts.delay ?? DEFAULT_DELAY;
  const clickTimeout = opts.clickTimeout ?? 6000; // fail fast on covered/absent fields
  await locator.click({ timeout: clickTimeout });
  if (opts.clearFirst !== false) await clearByKeyboard(page, locator);
  await locator.click({ timeout: clickTimeout }); // ensure focus after clear

  const keystrokes = [];
  if (opts.fast) {
    // FAST mode: type the whole string via pressSequentially (real-user cadence,
    // types into the focused element so a mid-type focus drop still loses chars)
    // and check final focus once — no per-char evaluate (for matrix verification).
    await locator.pressSequentially(text, { delay });
    const kept = await keptFocus(page, locator);
    keystrokes.push({ index: text.length - 1, char: text.slice(-1), keptFocus: kept, active: await describeActive(page) });
  } else {
    for (let i = 0; i < text.length; i++) {
      const ch = text[i];
      await page.keyboard.type(ch, { delay }); // real-user cadence; never fill
      const active = await describeActive(page);
      const kept = await keptFocus(page, locator);
      keystrokes.push({ index: i, char: ch, keptFocus: kept, active });
      if (!kept && opts.stopOnFocusLoss !== false) break;
    }
  }

  const finalValue = await locator.inputValue().catch(async () => (await locator.textContent()) || "");
  const focusLossAt = keystrokes.findIndex((k) => !k.keptFocus);
  const commasTyped = (text.match(/,/g) || []).length;
  const commasKept = (String(finalValue).match(/,/g) || []).length;
  return {
    expected: text,
    finalValue,
    valueCorrect: finalValue === text,
    typedLength: text.length,
    valueLength: String(finalValue).length,
    focusKeptAllKeystrokes: focusLossAt === -1,
    focusLossAtKeystroke: focusLossAt === -1 ? null : focusLossAt,
    commasTyped,
    commasKept,
    commaDrop: commasKept < commasTyped,
    keystrokes,
  };
}

// ── Checkpoint-01 smoke test: prove the harness runs e2e against the live page ──
async function main() {
  const outDir = process.env.SHOT_DIR || "mcp-parallel/findings/mcp-page/cp01";
  fs.mkdirSync(outDir, { recursive: true });
  const report = { checkpoint: "01", base: BASE, ok: false, smoke: null, error: null };
  const { browser, page } = await launchBrowser();
  page.on("pageerror", (e) => (report.pageError = String(e.message || e)));
  try {
    await login(page);
    await openRegisterDialog(page);
    await page.screenshot({ path: `${outDir}/01-modal-open.png` });
    // Smoke: type a short comma-containing string into the name field via type-sim.
    const nameLoc = page.getByPlaceholder("my-mcp-server");
    const r = await typeSim(page, nameLoc, "a,b,c", { stopOnFocusLoss: false });
    report.smoke = {
      typedInto: "name",
      expected: r.expected,
      finalValue: r.finalValue,
      commasTyped: r.commasTyped,
      commasKept: r.commasKept,
      focusKeptAll: r.focusKeptAllKeystrokes,
    };
    // The harness "works" if it typed via keyboard and read back a value (bug detection is CP02).
    report.harnessWorks = typeof r.finalValue === "string" && r.keystrokes.length > 0;
    report.ok = report.harnessWorks;
    await page.screenshot({ path: `${outDir}/02-after-typesim.png` });
    console.log(JSON.stringify(report, null, 2));
    console.log(report.ok ? "CP01: type-sim harness WORKS (keyboard.type, no fill)" : "CP01: harness FAILED");
  } catch (e) {
    report.error = e.message;
    await page.screenshot({ path: `${outDir}/99-error.png` }).catch(() => {});
    console.error("CP01 FAIL:", e.message);
  } finally {
    fs.writeFileSync(`${outDir}/report.json`, JSON.stringify(report, null, 2));
    await browser.close();
    process.exit(report.ok ? 0 : 1);
  }
}

// Only run main() when executed directly (not when imported by CP02/03/…).
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main();
}
