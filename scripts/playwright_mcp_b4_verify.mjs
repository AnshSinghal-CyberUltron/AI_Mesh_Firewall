/**
 * P5 item #16 — Playwright VERIFY of B4 (Add-Server dialog focus stability).
 *
 * Types long strings into every dialog field; asserts focus is retained per
 * keystroke. Also verifies GitHub preset prefill + focus on prefilled field.
 *
 * Prior fix (Dialog.jsx onCloseRef + stable handleKey) verified in P1.3;
 * this harness is the durable gate for verify-and-close.
 *
 * Run:
 *   BASE_URL=http://127.0.0.1:8180 node scripts/playwright_mcp_b4_verify.mjs
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
const SHOT_DIR = process.env.SHOT_DIR || "mcp-parallel/findings/p5-16";
const OUT = process.env.E2E_REPORT || `${SHOT_DIR}/report.json`;
const TYPED_LEN = Number(process.env.TYPED_LEN || 24);

const LONG = {
  name: "my-long-mcp-server-name-abc",
  url: "https://example-mcp-server.example.com/mcp/v1",
  description: "Optional description field typing test for focus retention across keystrokes.",
  command: "npx -y @modelcontextprotocol/server-everything",
  bearer: "sk-test-bearer-token-abcdefghijklmnop",
};

const report = { story: "P5#16", bug: "B4", base: BASE, ok: false, asserts: [], findings: {}, error: null };
const A = (name, cond, detail) => { report.asserts.push({ name, pass: !!cond, detail }); return !!cond; };
function mkdir() { fs.mkdirSync(SHOT_DIR, { recursive: true }); }
async function shot(page, name) { mkdir(); const p = `${SHOT_DIR}/${name}.png`; await page.screenshot({ path: p, fullPage: true }); return p; }

async function login(page) {
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle", timeout: 120000 });
  await page.locator("#email").fill(EMAIL);
  await page.locator("#password").fill(PASS);
  const [res] = await Promise.all([
    page.waitForResponse((r) => r.url().includes("/api/auth/token/") && r.request().method() === "POST"),
    page.getByRole("button", { name: /^sign in$/i }).click(),
  ]);
  if (!res.ok()) throw new Error(`Login failed: ${res.status()}`);
  await page.waitForFunction(() => !location.pathname.startsWith("/login"), { timeout: 30000 });
}

async function typeAndTrack(page, locator, fieldKey, text) {
  const sample = text.slice(0, TYPED_LEN);
  await locator.click();
  await locator.fill("");
  const handle = await locator.elementHandle();
  let focusKeptAll = true;
  let focusLossAt = null;

  for (let i = 0; i < sample.length; i++) {
    await page.keyboard.press(sample[i] === " " ? "Space" : sample[i]);
    const kept = await page.evaluate((el) => el === document.activeElement, handle);
    if (!kept) {
      focusKeptAll = false;
      focusLossAt = i;
      break;
    }
  }

  const value = await locator.inputValue().catch(() => "");
  return { field: fieldKey, focusKeptAllKeystrokes: focusKeptAll, focusLossAtKeystroke: focusLossAt, valueLength: value.length };
}

async function openRegisterDialog(page) {
  await page.goto(`${BASE}/?tab=firewall-1-4`, { waitUntil: "domcontentloaded", timeout: 120000 });
  await page.locator("text=/MCP Guardrails/i").first().waitFor({ state: "visible", timeout: 60000 });
  await page.getByRole("button", { name: /^Register Server$/i }).first().click();
  await page.getByText("Register MCP Server", { exact: false }).first().waitFor({ state: "visible", timeout: 15000 });
}

async function main() {
  mkdir();
  const browser = await chromium.launch({
    executablePath: CHROME,
    headless: true,
    args: ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
  });
  const page = await (await browser.newContext({ viewport: { width: 1440, height: 1200 } })).newPage();

  try {
    await login(page);
    await openRegisterDialog(page);
    await shot(page, "01-dialog-open");

    const fields = [];
    fields.push(await typeAndTrack(page, page.getByPlaceholder("my-mcp-server"), "name", LONG.name));
    await shot(page, "02-after-name-typing");
    fields.push(await typeAndTrack(page, page.getByPlaceholder("https://my-server.example.com/mcp"), "url", LONG.url));
    await shot(page, "03-after-url-typing");
    fields.push(await typeAndTrack(page, page.getByPlaceholder("Optional description"), "description", LONG.description));
    await shot(page, "04-after-description-typing");

    await page.getByLabel("Transport").selectOption("stdio");
    await page.waitForTimeout(300);
    fields.push(await typeAndTrack(page, page.getByPlaceholder("npx"), "stdio-command", LONG.command));
    await shot(page, "05-after-stdio-command");

    await page.getByLabel("Transport").selectOption("streamable-http");
    await page.getByLabel("Upstream authentication type").selectOption("bearer");
    await page.waitForTimeout(200);
    const bearerLoc = page.getByPlaceholder("Bearer token").or(page.locator('input[type="password"]').last());
    if (await bearerLoc.count()) {
      fields.push(await typeAndTrack(page, bearerLoc.first(), "bearer-token", LONG.bearer));
      await shot(page, "06-after-bearer-typing");
    }

    report.findings.fields = fields;
    const lossFields = fields.filter((f) => !f.focusKeptAllKeystrokes);
    report.findings.b4BugConfirmed = lossFields.length > 0;
    report.findings.b4BugNotReproducing = lossFields.length === 0;

    for (const f of fields) {
      A(`B4-F focus kept: ${f.field}`, f.focusKeptAllKeystrokes, f);
    }
    A("B4-ALL no focus loss in any field", lossFields.length === 0, lossFields.map((f) => f.field));

    // Close dialog and verify preset prefill + focus on prefilled field
    await page.keyboard.press("Escape");
    await page.waitForTimeout(400);
    await page.getByRole("button", { name: /GitHub MCP/i }).first().click();
    await page.getByText("Register MCP Server", { exact: false }).first().waitFor({ state: "visible", timeout: 15000 });
    await page.waitForTimeout(400);

    const nameVal = await page.getByPlaceholder("my-mcp-server").inputValue();
    const urlVal = await page.getByPlaceholder("https://my-server.example.com/mcp").inputValue();
    const transportVal = await page.getByLabel("Transport").inputValue().catch(() => null);
    const authVal = await page.getByLabel("Upstream authentication type").inputValue().catch(() => null);
    report.findings.prefill = { nameVal, urlVal, transportVal, authVal };
    await shot(page, "07-github-preset-prefill");

    A("B4-P1 preset prefills name=GitHub MCP", nameVal === "GitHub MCP", nameVal);
    A("B4-P2 preset prefills url", urlVal === "https://api.githubcopilot.com/mcp/", urlVal);
    A("B4-P3 preset transport=streamable-http", transportVal === "streamable-http", transportVal);
    A("B4-P4 preset auth_type=bearer", authVal === "bearer", authVal);

    const nameInput = page.getByPlaceholder("my-mcp-server");
    await nameInput.click();
    await nameInput.press("End");
    let presetFocusKept = true;
    const S = "-abcdefghijklmnopqrstuvwxyz0123456789";
    for (const ch of S) {
      await page.keyboard.press(ch === "-" ? "Minus" : (/[0-9]/.test(ch) ? `Digit${ch}` : `Key${ch.toUpperCase()}`));
      const isFocused = await nameInput.evaluate((el) => el === document.activeElement);
      if (!isFocused) { presetFocusKept = false; break; }
    }
    report.findings.focusKeptOnPrefilledField = presetFocusKept;
    A("B4-P5 focus retained on prefilled field", presetFocusKept, { typed: S.length });
    await shot(page, "08-after-prefill-typing");

    report.ok = report.asserts.every((a) => a.pass);
    console.log(JSON.stringify({ asserts: report.asserts, findings: report.findings }, null, 2));
    console.log(report.ok ? "B4 VERIFY: ALL PASS" : "B4 VERIFY: FAILURES");
  } catch (e) {
    report.error = e.message;
    console.error("FAIL", e.stack || e.message);
    await shot(page, "99-error").catch(() => {});
  } finally {
    fs.writeFileSync(OUT, JSON.stringify(report, null, 2));
    await browser.close();
    process.exit(report.ok ? 0 : 1);
  }
}

main();
