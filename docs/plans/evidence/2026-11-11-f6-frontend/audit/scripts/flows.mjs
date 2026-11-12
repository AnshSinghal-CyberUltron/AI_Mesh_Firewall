// Targeted broken-flow checks against the running build (small, single context).
import { createRequire } from "node:module";
import fs from "node:fs";
const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const BASE = process.env.BASE || "http://127.0.0.1:4176";
const OUT = process.env.OUT;
const out = {};
const browser = await chromium.launch();

// 1. Theme default on a dark-preferring OS with no stored preference.
{
  const ctx = await browser.newContext({ colorScheme: "dark" });
  const p = await ctx.newPage();
  await p.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  out.themeDefaultOnDarkOS = await p.evaluate(() => ({ htmlDark: document.documentElement.classList.contains("dark"), stored: localStorage.getItem("zeroshield_theme") }));
  await ctx.close();
}

const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await ctx.newPage();
await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
await page.fill('input[type="email"]', process.env.F6_ADMIN_EMAIL);
await page.fill('input[type="password"]', process.env.F6_ADMIN_PW);
await page.click('button[type="submit"]');
await page.waitForURL((u) => !u.pathname.startsWith("/login"));
await page.waitForTimeout(1500);

// 2. Browser Back after switching tabs in the sidebar.
await page.goto(`${BASE}/?tab=firewall-1-2`, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(1500);
await page.getByRole("button", { name: "Output Guardrails" }).first().click();
await page.waitForTimeout(800);
const afterClick = page.url();
await page.goBack();
await page.waitForTimeout(1200);
out.backButton = { start: "/?tab=firewall-1-2", afterSidebarClick: afterClick.replace(BASE, ""), afterBack: page.url().replace(BASE, "") };

// 3. Global header search.
await page.goto(`${BASE}/?tab=firewall-1-2`, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(1500);
const search = page.getByPlaceholder("Search models, policies, threats, or infrastructure...");
const before = { url: page.url(), text: (await page.locator("main").innerText().catch(() => "")).length };
await search.fill("PII");
await search.press("Enter");
await page.waitForTimeout(1200);
out.search = { urlBefore: before.url.replace(BASE, ""), urlAfter: page.url().replace(BASE, ""), valueAfterEnter: await search.inputValue(), mainTextLenBefore: before.text, mainTextLenAfter: (await page.locator("main").innerText().catch(() => "")).length };

// 4. "Scan Model" header button.
await page.getByRole("button", { name: "Scan Model" }).click();
await page.waitForTimeout(1000);
out.scanModel = { urlAfter: page.url().replace(BASE, ""), h1: await page.locator("h1").first().innerText().catch(() => null) };

// 5. Detailed results view survives reload?
await page.goto(`${BASE}/?tab=firewall-1-1`, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(2500);
const btn = page.getByRole("button", { name: /Open detailed results|View full results/ }).first();
if (await btn.count()) {
  await btn.click();
  await page.waitForTimeout(1500);
  const urlResults = page.url().replace(BASE, "");
  const h = await page.locator("h1, h2").first().innerText().catch(() => null);
  await page.screenshot({ path: `${OUT}/flow-results-before-reload.png` });
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.waitForTimeout(2500);
  await page.screenshot({ path: `${OUT}/flow-results-after-reload.png` });
  out.resultsReload = { urlOnResults: urlResults, headingOnResults: h, headingAfterReload: await page.locator("h1, h2").first().innerText().catch(() => null) };
} else out.resultsReload = "button not found";

// 6. Websocket target.
const wsUrls = [];
page.on("websocket", (ws) => wsUrls.push(ws.url().replace(/token=[^&]+/, "token=<redacted>")));
await page.goto(`${BASE}/`, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(3000);
out.websocket = wsUrls;

// 7. Header identity line.
out.headerUser = await page.locator("header").first().innerText().then((t) => t.split("\n").filter((l) => l.includes("@")));

fs.writeFileSync(`${OUT}/flows.json`, JSON.stringify(out, null, 2));
console.log(JSON.stringify(out, null, 2));
await browser.close();
