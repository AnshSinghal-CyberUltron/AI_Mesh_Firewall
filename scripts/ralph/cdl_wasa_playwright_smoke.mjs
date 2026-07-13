#!/usr/bin/env node
/** CDL WASA Playwright smoke — login, policies, logout. */
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import path from "node:path";

const require = createRequire(import.meta.url);
const HERE = path.dirname(fileURLToPath(import.meta.url));
const { chromium } = require(path.join(HERE, "../../tests/e2e/node_modules/playwright"));

const BASE = (process.env.BASE_URL || "http://127.0.0.1:8180").replace(/\/$/, "");
const EMAIL = process.env.CDL_ADMIN_EMAIL || "admin@zeroshield.io";
const PASSWORD = process.env.CDL_ADMIN_PASSWORD || "Adm1n!Pass#2024";

const errors = [];

async function main() {
  const browser = await chromium.launch({
    headless: true,
    executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH || undefined,
  });
  const page = await browser.newPage();
  page.on("console", (msg) => {
    if (msg.type() === "error") errors.push(msg.text());
  });

  await page.goto(`${BASE}/login`, { waitUntil: "networkidle", timeout: 120000 });
  await page.fill('input[type="email"], input[name="email"]', EMAIL);
  await page.fill('input[type="password"]', PASSWORD);
  await page.click('button[type="submit"]');
  await page.waitForFunction(() => !window.location.pathname.includes("/login"), { timeout: 120000 });

  await page.goto(`${BASE}/?tab=firewall-1-4`, { waitUntil: "networkidle", timeout: 120000 });
  await page.waitForTimeout(2000);

  const ok = errors.length === 0;
  console.log(JSON.stringify({ cdlPlaywrightSmoke: ok, consoleErrors: errors.length }, null, 2));
  await browser.close();
  process.exit(ok ? 0 : 1);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
