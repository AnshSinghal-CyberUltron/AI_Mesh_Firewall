/**
 * Login + smoke all AI Mesh Firewall sidebar tabs (standalone frontend).
 * Usage: BASE_URL=http://127.0.0.1:8180 node scripts/playwright_firewall_tabs.mjs
 */
import { chromium } from "playwright";

const BASE_URL = (process.env.BASE_URL || "http://127.0.0.1:8180").replace(/\/$/, "");
const EMAIL = process.env.TEST_EMAIL || "admin@zeroshield.io";
const PASS = process.env.TEST_PASSWORD || "Adm1n!Pass#2024";

const TABS = [
  { tab: "firewall", label: "Overview" },
  { tab: "firewall-1-1", label: "1.1 Gateway" },
  { tab: "firewall-1-2", label: "1.2 Policy" },
  { tab: "firewall-1-3", label: "1.3 RAG" },
  { tab: "firewall-1-4", label: "1.4 Context/MCP" },
  { tab: "firewall-1-5", label: "1.5 Multi-model" },
  { tab: "firewall-1-6", label: "1.6 Isolation" },
  { tab: "firewall-1-7", label: "1.7 Output" },
  { tab: "firewall-config", label: "Inputs" },
];

async function main() {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  let failed = 0;

  await page.goto(`${BASE_URL}/login`, { waitUntil: "networkidle", timeout: 60000 });
  await page.locator("#email").fill(EMAIL);
  await page.locator("#password").fill(PASS);
  await Promise.all([
    page.waitForURL(/\?tab=|\/$/, { timeout: 60000 }),
    page.getByRole("button", { name: /^sign in$/i }).click(),
  ]);
  if (!page.url().includes("tab=")) {
    await page.goto(`${BASE_URL}/?tab=firewall`, { waitUntil: "networkidle" });
  }

  for (const { tab, label } of TABS) {
    const url = `${BASE_URL}/?tab=${tab}`;
    const res = await page.goto(url, { waitUntil: "networkidle", timeout: 90000 });
    const status = res?.status() ?? 0;
    const body = await page.locator("body").innerText();
    const hasErrorOverlay =
      /Failed to fetch|500 Internal|Vite.*error|Cannot find module/i.test(body);
    if (status >= 400 || hasErrorOverlay) {
      console.log(`FAIL ${label} (${tab}) status=${status}`);
      failed += 1;
    } else {
      console.log(`OK ${label} (${tab})`);
    }
  }

  await browser.close();
  if (failed) {
    console.log(`--- failed=${failed}`);
    process.exit(1);
  }
  console.log(`--- all ${TABS.length} tabs OK`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
