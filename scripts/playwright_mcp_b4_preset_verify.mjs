/**
 * P5 item #18 — Playwright VERIFY for B4: presets prefill the Add Server modal
 * correctly AND controlled inputs keep focus per keystroke.
 *
 * (The per-field 24-keystroke focus trace is also covered by
 * scripts/playwright_mcp_p1_dialog_focus_repro.mjs; this adds the "presets
 * prefill correctly" leg + a focus re-confirmation on a prefilled field.)
 *
 * System chromium (bundled unsupported on ubuntu26.04), headless — does NOT
 * touch the headed noVNC browser used for manual OAuth.
 *   BASE_URL=http://127.0.0.1:8180 node scripts/playwright_mcp_b4_preset_verify.mjs
 */
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import path from "node:path";
import fs from "node:fs";
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);
const { chromium } = require(path.resolve(__dirname, "../tests/e2e/node_modules/playwright"));

const BASE = (process.env.BASE_URL || "http://127.0.0.1:8180").replace(/\/$/, "");
const EMAIL = process.env.TEST_EMAIL || "admin@zeroshield.io";
const PASS = process.env.TEST_PASSWORD || "Adm1n!Pass#2024";
const CHROME = process.env.CHROME_PATH || "/usr/bin/chromium-browser";
const SHOT_DIR = process.env.SHOT_DIR || "mcp-parallel/findings/b4-preset";
const OUT = `${SHOT_DIR}/report.json`;
const report = { story: "P5#18", bug: "B4", ok: false, asserts: [], findings: {}, error: null };
const A = (n, c, d) => { report.asserts.push({ name: n, pass: !!c, detail: d }); return !!c; };
function mkdir() { fs.mkdirSync(SHOT_DIR, { recursive: true }); }

async function login(page) {
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle", timeout: 120000 });
  await page.locator("#email").fill(EMAIL);
  await page.locator("#password").fill(PASS);
  await Promise.all([
    page.waitForResponse((r) => r.url().includes("/api/auth/token/") && r.request().method() === "POST"),
    page.getByRole("button", { name: /^sign in$/i }).click(),
  ]);
  await page.waitForFunction(() => !location.pathname.startsWith("/login"), { timeout: 30000 });
}

async function main() {
  mkdir();
  const browser = await chromium.launch({ executablePath: CHROME, headless: true, args: ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"] });
  const page = await (await browser.newContext({ viewport: { width: 1440, height: 1100 } })).newPage();
  try {
    await login(page);
    await page.goto(`${BASE}/?tab=firewall-1-4`, { waitUntil: "domcontentloaded", timeout: 120000 });
    await page.locator("text=/MCP Guardrails/i").first().waitFor({ state: "visible", timeout: 60000 });

    // Click the GitHub MCP preset (requiresAuth => opens prefilled modal).
    await page.getByRole("button", { name: /GitHub MCP/i }).first().click();
    await page.getByText("Register MCP Server", { exact: false }).first().waitFor({ state: "visible", timeout: 15000 });
    await page.waitForTimeout(400);

    const nameVal = await page.getByPlaceholder("my-mcp-server").inputValue();
    const urlVal = await page.getByPlaceholder("https://my-server.example.com/mcp").inputValue();
    const transportVal = await page.getByLabel("Transport").inputValue().catch(() => null);
    const authVal = await page.getByLabel("Upstream authentication type").inputValue().catch(() => null);
    report.findings.prefill = { nameVal, urlVal, transportVal, authVal };
    fs.mkdirSync(SHOT_DIR, { recursive: true });
    await page.screenshot({ path: `${SHOT_DIR}/01-github-preset-prefill.png`, fullPage: true });

    A("B4-P1 preset prefills name=GitHub MCP", nameVal === "GitHub MCP", nameVal);
    A("B4-P2 preset prefills url=api.githubcopilot.com/mcp/", urlVal === "https://api.githubcopilot.com/mcp/", urlVal);
    A("B4-P3 preset prefills transport=streamable-http", transportVal === "streamable-http", transportVal);
    A("B4-P4 preset prefills auth_type=bearer (suggestedAuthType)", authVal === "bearer", authVal);

    // Focus re-confirmation: type a long string into the prefilled name field,
    // assert focus is retained after every keystroke.
    const nameInput = page.getByPlaceholder("my-mcp-server");
    await nameInput.click();
    await nameInput.press("End");
    let focusKept = true;
    const S = "-abcdefghijklmnopqrstuvwxyz0123456789";
    for (const ch of S) {
      await page.keyboard.press(ch === "-" ? "Minus" : (/[0-9]/.test(ch) ? `Digit${ch}` : `Key${ch.toUpperCase()}`));
      const isFocused = await nameInput.evaluate((el) => el === document.activeElement);
      if (!isFocused) { focusKept = false; break; }
    }
    report.findings.focusKeptOnPrefilledField = focusKept;
    A("B4-P5 focus retained per keystroke on prefilled field", focusKept, { typed: S.length });

    report.ok = report.asserts.every((a) => a.pass);
    console.log(JSON.stringify({ asserts: report.asserts, findings: report.findings }, null, 2));
    console.log(report.ok ? "B4 PRESET VERIFY: ALL PASS" : "B4 PRESET VERIFY: FAILURES");
  } catch (e) {
    report.error = e.message; console.error("FAIL", e.stack || e.message);
    try { await page.screenshot({ path: `${SHOT_DIR}/99-error.png`, fullPage: true }); } catch {}
  } finally {
    fs.writeFileSync(OUT, JSON.stringify(report, null, 2));
    await browser.close();
    process.exit(report.ok ? 0 : 1);
  }
}
main();
