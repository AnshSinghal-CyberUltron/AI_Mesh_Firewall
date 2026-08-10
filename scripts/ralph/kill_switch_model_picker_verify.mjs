#!/usr/bin/env node
/**
 * E2E: fleet Kill switch opens KillSwitchActionDialog with model picker
 * (not the legacy "all models" Credential kill switch modal).
 *
 *   NODE_PATH="$PWD/tests/e2e/node_modules" BASE_URL=http://127.0.0.1:8180 \
 *     node scripts/ralph/kill_switch_model_picker_verify.mjs
 */
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import path from "node:path";
import fs from "node:fs";

const require = createRequire(import.meta.url);
const HERE = path.dirname(fileURLToPath(import.meta.url));
const { chromium } = require(path.join(HERE, "../../tests/e2e/node_modules/playwright"));

const BASE = (process.env.BASE_URL || "http://127.0.0.1:8180").replace(/\/$/, "");
const EMAIL = process.env.TEST_EMAIL || "admin@zeroshield.io";
const PASS = process.env.TEST_PASSWORD || "Adm1n!Pass#2024";
const OUT = path.join(HERE, "../../mcp-parallel/findings/kill-switch-model-picker");

async function login(page) {
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle", timeout: 120000 });
  await page.locator("#email").fill(EMAIL);
  await page.locator("#password").fill(PASS);
  for (let attempt = 0; attempt < 6; attempt++) {
    const [res] = await Promise.all([
      page.waitForResponse((r) => r.url().includes("/api/auth/token/") && r.request().method() === "POST"),
      page.getByRole("button", { name: /^sign in$/i }).click(),
    ]);
    if (res.status() === 429) {
      const retryAfter = Number(res.headers()["retry-after"] || 15);
      await page.waitForTimeout(Math.max(5, retryAfter) * 1000);
      continue;
    }
    if (!res.ok()) throw new Error(`login HTTP ${res.status()}`);
    break;
  }
  await page.waitForURL((u) => !u.pathname.includes("/login"), { timeout: 60000 });
}

async function main() {
  fs.mkdirSync(OUT, { recursive: true });
  const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH || undefined;
  const browser = await chromium.launch({
    headless: true,
    ...(executablePath ? { executablePath } : {}),
    args: ["--no-sandbox", "--disable-dev-shm-usage"],
  });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
  const consoleErrors = [];
  page.on("pageerror", (e) => consoleErrors.push(String(e)));
  page.on("console", (m) => {
    if (m.type() === "error") consoleErrors.push(m.text());
  });

  await login(page);
  await page.goto(`${BASE}/ueba/api-keys`, { waitUntil: "networkidle", timeout: 120000 });
  await page.getByText("API Key Fleet Inspector").waitFor({ timeout: 90000 });

  // Prefer the fleet-row action (red button), NOT the filter chip also labeled "Kill switch".
  const killBtn = page
    .locator("table")
    .getByRole("button", { name: /^Kill switch$/i })
    .first();
  const killCount = await page.locator("table").getByRole("button", { name: /^Kill switch$/i }).count();
  if (killCount === 0) {
    await page.screenshot({ path: path.join(OUT, "no-kill-btn.png"), fullPage: true });
    throw new Error("No fleet-row 'Kill switch' action buttons (all keys may already have active switches)");
  }
  await killBtn.scrollIntoViewIfNeeded();
  await killBtn.click();
  await page.waitForTimeout(2000);
  const bodyAfter = await page.locator("body").innerText().catch(() => "");
  fs.writeFileSync(path.join(OUT, "after-click-body.txt"), bodyAfter.slice(0, 4000));
  await page.screenshot({ path: path.join(OUT, "after-click.png"), fullPage: true }).catch(() => {});
  fs.writeFileSync(
    path.join(OUT, "console.json"),
    JSON.stringify({ consoleErrors, killButtonCount: killCount }, null, 2),
  );

  // Prefer text markers over fragile fixed-overlay class matching
  const markers = [
    page.getByText("Activate kill switch", { exact: false }),
    page.getByText("Model (client-requested)", { exact: false }),
    page.getByText("Credential kill switch", { exact: false }),
    page.getByText("Immediately blocks", { exact: false }),
  ];
  let matched = null;
  for (const loc of markers) {
    try {
      await loc.first().waitFor({ state: "visible", timeout: 5000 });
      matched = await loc.first().innerText();
      break;
    } catch {
      // try next
    }
  }
  if (!matched) {
    const result = {
      ok: false,
      reason: "dialog_not_opened",
      killButtonCount: killCount,
      bodyPreview: bodyAfter.slice(0, 800),
      consoleErrors: consoleErrors.slice(0, 20),
    };
    fs.writeFileSync(path.join(OUT, "verdict.json"), JSON.stringify(result, null, 2));
    console.log(JSON.stringify(result, null, 2));
    await browser.close();
    process.exit(1);
  }

  const dialogText = bodyAfter;
  const hasLegacyAllModels = /Immediately blocks\s+all models/i.test(dialogText)
    || /Credential kill switch/i.test(dialogText);
  const hasNewTitle = /Activate kill switch/i.test(dialogText);
  const hasModelLabel = /Model \(client-requested\)/i.test(dialogText);
  const modelSelect = page.locator("label", { hasText: /Model \(client-requested\)/i }).locator("select");
  const optionCount = await modelSelect.locator("option").count().catch(() => 0);
  const customInput = page.getByPlaceholder(/gpt-4o-mini/i);
  const hasCustom = await customInput.count();
  const selectDisabled = await modelSelect.isDisabled().catch(() => true);

  await page.screenshot({ path: path.join(OUT, "dialog.png"), fullPage: false });

  const result = {
    ok: hasNewTitle && hasModelLabel && !hasLegacyAllModels && hasCustom > 0,
    hasLegacyAllModels,
    hasNewTitle,
    hasModelLabel,
    matched,
    killButtonCount: killCount,
    modelOptionCount: optionCount,
    selectDisabled,
    hasCustomModelInput: hasCustom > 0,
    consoleErrors: consoleErrors.slice(0, 10),
  };
  fs.writeFileSync(path.join(OUT, "verdict.json"), JSON.stringify(result, null, 2));
  console.log(JSON.stringify(result, null, 2));
  await browser.close();
  if (!result.ok) process.exit(1);
}

main().catch((err) => {
  console.error("FAIL", err);
  process.exit(1);
});
