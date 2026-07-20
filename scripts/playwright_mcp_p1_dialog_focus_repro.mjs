/**
 * P1.3 — Playwright repro: Add-Server dialog focus loss per keystroke (B4).
 * Type long strings into every dialog field; record activeElement after each key.
 *
 * Run:
 *   PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium-browser \
 *   BASE_URL=http://127.0.0.1:8180 \
 *   SHOT_DIR=mcp-parallel/findings/p1-3 \
 *   E2E_REPORT=mcp-parallel/findings/p1-3/report.json \
 *   node scripts/playwright_mcp_p1_dialog_focus_repro.mjs
 */
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import path from "node:path";
import fs from "node:fs";
import { existsSync } from "node:fs";

const require = createRequire(import.meta.url);
const { chromium } = require(
  path.join(fileURLToPath(new URL(".", import.meta.url)), "../tests/e2e/node_modules/playwright")
);

const BASE = (process.env.BASE_URL || "http://127.0.0.1:8180").replace(/\/$/, "");
const EMAIL = process.env.TEST_EMAIL || "admin@zeroshield.io";
const PASS = process.env.TEST_PASSWORD || "Adm1n!Pass#2024";
const OUT = process.env.E2E_REPORT || "mcp-parallel/findings/p1-3/report.json";
const SHOT_DIR = process.env.SHOT_DIR || "mcp-parallel/findings/p1-3";
const NET_TRACE = process.env.NET_TRACE || "mcp-parallel/findings/p1-3/network.jsonl";
const CHROME =
  process.env.CHROME_PATH ||
  process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH ||
  (existsSync("/usr/bin/chromium-browser") ? "/usr/bin/chromium-browser" : null);
const TYPED_LEN = Number(process.env.TYPED_LEN || 24);
  name: "my-long-mcp-server-name-abc",
  url: "https://example-mcp-server.example.com/mcp/v1",
  description: "Optional description field typing test for focus retention across keystrokes.",
  command: "npx -y @modelcontextprotocol/server-everything",
  bearer: "sk-test-bearer-token-abcdefghijklmnop",
};

const report = {
  story: "P1.3",
  bug: "B4",
  base: BASE,
  ok: false,
  steps: [],
  findings: { fields: [] },
  network: [],
  pageErrors: [],
  error: null,
};

function mkdir() {
  fs.mkdirSync(SHOT_DIR, { recursive: true });
  fs.mkdirSync("mcp-parallel/findings", { recursive: true });
}

async function shot(page, name) {
  mkdir();
  const p = `${SHOT_DIR}/${name}.png`;
  await page.screenshot({ path: p, fullPage: true });
  report.steps.push(`screenshot:${name}`);
  return p;
}

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
  report.steps.push("login");
}

async function describeActive(page) {
  return page.evaluate(() => {
    const ae = document.activeElement;
    if (!ae || ae === document.body) return { kind: "body", tag: "BODY" };
    return {
      kind: "element",
      tag: ae.tagName,
      type: ae.type || null,
      placeholder: ae.placeholder || null,
      ariaLabel: ae.getAttribute("aria-label"),
      name: ae.name || null,
      id: ae.id || null,
      valueLen: "value" in ae ? String(ae.value || "").length : null,
      className: (ae.className || "").slice(0, 80),
    };
  });
}

/** Returns true if focus stayed on the target locator's element. */
async function matchesTarget(page, locator, active) {
  const handle = await locator.elementHandle();
  if (!handle) return false;
  return page.evaluate(
    ({ active, target }) => {
      const ae = document.activeElement;
      if (!ae || ae === document.body) return active.kind === "body" && false;
      return ae === target;
    },
    { active, target: handle }
  );
}

async function typeAndTrack(page, locator, fieldKey, text) {
  const sample = text.slice(0, TYPED_LEN);
  await locator.click();
  await locator.fill("");
  const keystrokes = [];

  for (let i = 0; i < sample.length; i++) {
    await page.keyboard.press(sample[i] === " " ? "Space" : sample[i]);
    const active = await describeActive(page);
    const kept = await matchesTarget(page, locator, active);
    keystrokes.push({
      index: i,
      char: sample[i],
      keptFocus: kept,
      active,
    });
    if (!kept) break;
  }

  const value = await locator.inputValue().catch(() => locator.textContent());
  const focusLossAt = keystrokes.findIndex((k) => !k.keptFocus);
  return {
    field: fieldKey,
    typedLength: sample.length,
    valueLength: value?.length ?? null,
    finalValue: value,
    focusKeptAllKeystrokes: focusLossAt === -1,
    focusLossAtKeystroke: focusLossAt === -1 ? null : focusLossAt,
    keystrokes,
  };
}

async function openRegisterDialog(page) {
  await page.goto(`${BASE}/?tab=firewall-1-4`, { waitUntil: "domcontentloaded", timeout: 120000 });
  await page.locator("text=/MCP Guardrails/i").first().waitFor({ state: "visible", timeout: 60000 });
  await page.getByRole("button", { name: /^Register Server$/i }).first().click();
  await page.getByText("Register MCP Server", { exact: false }).first().waitFor({ state: "visible", timeout: 15000 });
  report.steps.push("open-register-dialog");
}

async function main() {
  mkdir();
  const netStream = fs.createWriteStream(NET_TRACE, { flags: "w" });
  const browser = await chromium.launch({
    headless: true,
    ...(CHROME ? { executablePath: CHROME } : {}),
  });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1200 } });
  const page = await context.newPage();

  page.on("pageerror", (e) => report.pageErrors.push(String(e.message || e)));
  page.on("response", async (r) => {
    const u = r.url();
    if (!u.includes("mcp-connector") && !u.includes("/api/auth/")) return;
    const entry = { ts: new Date().toISOString(), url: u, status: r.status(), method: r.request().method() };
    report.network.push(entry);
    netStream.write(`${JSON.stringify(entry)}\n`);
  });

  try {
    await login(page);
    await openRegisterDialog(page);
    await shot(page, "01-dialog-open");

    const nameLoc = page.getByPlaceholder("my-mcp-server");
    const urlLoc = page.getByPlaceholder("https://my-server.example.com/mcp");
    const descLoc = page.getByPlaceholder("Optional description");

    const nameResult = await typeAndTrack(page, nameLoc, "name", LONG.name);
    report.findings.fields.push(nameResult);
    await shot(page, "02-after-name-typing");

    const urlResult = await typeAndTrack(page, urlLoc, "url", LONG.url);
    report.findings.fields.push(urlResult);
    await shot(page, "03-after-url-typing");

    const descResult = await typeAndTrack(page, descLoc, "description", LONG.description);
    report.findings.fields.push(descResult);
    await shot(page, "04-after-description-typing");

    // Stdio transport fields
    await page.getByLabel("Transport").selectOption("stdio");
    await page.waitForTimeout(300);
    const cmdLoc = page.getByPlaceholder("npx");
    const argsLoc = page.locator('input[placeholder*="@modelcontextprotocol"], input').filter({ hasText: "" }).nth(3);
    // args field: placeholder is comma-separated args
    const argsField = page.locator('label:has-text("Args") + input, label:has-text("Args") ~ input').first();
    const envField = page.locator('textarea').filter({ has: page.locator('xpath=..') }).last();

    const cmdResult = await typeAndTrack(page, cmdLoc, "stdio-command", LONG.command);
    report.findings.fields.push(cmdResult);
    await shot(page, "05-after-stdio-command");

    // Bearer auth on http transport
    await page.getByLabel("Transport").selectOption("streamable-http");
    await page.getByLabel("Upstream authentication type").selectOption("bearer");
    await page.waitForTimeout(200);
    const bearerLoc = page.getByPlaceholder("Bearer token").or(page.locator('input[type="password"]').last());
    if (await bearerLoc.count()) {
      const bearerResult = await typeAndTrack(page, bearerLoc.first(), "bearer-token", LONG.bearer);
      report.findings.fields.push(bearerResult);
      await shot(page, "06-after-bearer-typing");
    }

    const anyLoss = report.findings.fields.some((f) => !f.focusKeptAllKeystrokes);
    const lossFields = report.findings.fields.filter((f) => !f.focusKeptAllKeystrokes);

    report.findings.b4BugConfirmed = anyLoss;
    report.findings.b4BugNotReproducing = !anyLoss;
    report.findings.focusLossFields = lossFields.map((f) => ({
      field: f.field,
      focusLossAtKeystroke: f.focusLossAtKeystroke,
      finalValue: f.finalValue,
    }));

    report.ok = true;
    report.steps.push("repro-complete");

    console.log(JSON.stringify(report.findings, null, 2));
    console.log(
      anyLoss
        ? `B4 BUG CONFIRMED — focus lost in: ${lossFields.map((f) => f.field).join(", ")}`
        : "B4 NOT REPRODUCING — focus kept for all fields"
    );
  } catch (e) {
    report.error = e.message;
    await shot(page, "99-error").catch(() => {});
    console.error("FAIL", e.message);
  } finally {
    fs.writeFileSync(OUT, JSON.stringify(report, null, 2));
    netStream.end();
    await browser.close();
    process.exit(report.ok ? 0 : 1);
  }
}

main();
