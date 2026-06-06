/**
 * Command grid smoke: module-kpis/trends parity on overview cards.
 * Usage: BASE_URL=http://127.0.0.1:8180 node scripts/playwright_overview_command_grid.mjs
 */
import { chromium } from "playwright";
import fs from "node:fs";
import path from "node:path";

const BASE_URL = (process.env.BASE_URL || "http://127.0.0.1:8180").replace(/\/$/, "");
const EMAIL = process.env.TEST_EMAIL || "admin@zeroshield.io";
const PASS = process.env.TEST_PASSWORD || "Adm1n!Pass#2024";
const OUT = process.env.E2E_REPORT || "runs/playwright_overview_command_grid.json";

const MODULE_IDS = ["1.1", "1.2", "1.3", "1.4", "1.5", "1.6", "1.7"];

const MODULE_CARD_TITLES = {
  "1.1": "AI Gateway & Traffic Ingress",
  "1.2": "Policy Management",
  "1.3": "RAG & Vector DB Firewall",
  "1.4": "Context Assembly & MCP",
  "1.5": "Multi-Model Governance",
  "1.6": "Model Isolation & Kill-Switch",
  "1.7": "Output Guardrails",
};

function moduleCard(page, mid) {
  const title = MODULE_CARD_TITLES[mid];
  const grid = page.locator("section").filter({
    has: page.getByRole("heading", { name: "AI Mesh Firewall command grid" }),
  });
  return grid.getByRole("button", { name: new RegExp(title, "i") });
}

async function main() {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  const report = { baseUrl: BASE_URL, checks: [], failed: 0 };

  let moduleKpis = null;
  let moduleTrends = null;

  page.on("response", async (response) => {
    const url = response.url();
    if (!response.ok()) return;
    try {
      if (url.includes("/api/security/module-kpis/")) {
        moduleKpis = await response.json();
      }
      if (url.includes("/api/security/module-trends/")) {
        moduleTrends = await response.json();
      }
    } catch {
      /* ignore non-json */
    }
  });

  await page.goto(`${BASE_URL}/login`, { waitUntil: "networkidle", timeout: 60000 });
  await page.locator("#email").fill(EMAIL);
  await page.locator("#password").fill(PASS);
  await Promise.all([
    page.waitForURL(/\?tab=|\/$/, { timeout: 60000 }),
    page.getByRole("button", { name: /^sign in$/i }).click(),
  ]);

  await page.goto(`${BASE_URL}/?tab=firewall`, { waitUntil: "networkidle", timeout: 90000 });
  await page.getByText("AI Mesh Firewall command grid").waitFor({ timeout: 30000 });

  if (!moduleKpis?.modules) {
    report.checks.push({ name: "module-kpis loaded", ok: false });
    report.failed += 1;
  } else {
    report.checks.push({ name: "module-kpis loaded", ok: true });
  }

  if (!moduleTrends || !moduleTrends["1.1"]) {
    report.checks.push({ name: "module-trends loaded", ok: false });
    report.failed += 1;
  } else {
    report.checks.push({ name: "module-trends loaded", ok: true });
  }

  for (const mid of MODULE_IDS) {
    const card = moduleCard(page, mid);
    const cardVisible = await card.isVisible().catch(() => false);
    if (!cardVisible) {
      report.checks.push({ name: `card ${mid} visible`, ok: false });
      report.failed += 1;
      continue;
    }

    const cardText = await card.innerText();
    const mod = moduleKpis?.modules?.[mid];
    if (mod?.total > 0) {
      const totalStr = mod.total.toLocaleString();
      const ok = cardText.includes(totalStr);
      report.checks.push({ name: `card ${mid} total matches API (${totalStr})`, ok });
      if (!ok) report.failed += 1;
    }

    await card.scrollIntoViewIfNeeded();
    let hasChart = false;
    for (let attempt = 0; attempt < 6; attempt += 1) {
      hasChart = (await card.locator(".recharts-surface").count()) > 0;
      if (hasChart) break;
      await page.waitForTimeout(200);
    }
    if (!hasChart) {
      hasChart = (await card.getByText("Pressure Curve").count()) > 0;
    }
    report.checks.push({ name: `card ${mid} pressure chart renders`, ok: hasChart });
    if (!hasChart) report.failed += 1;

    if (moduleTrends?.[mid]?.length) {
      const pressureSum = moduleTrends[mid].reduce(
        (sum, pt) => sum + (pt.pressure ?? pt.value ?? 0),
        0,
      );
      const metricKey = moduleTrends.pressure_metric?.[mid] || (mid === "1.4" ? "redacted" : mid === "1.6" ? "critical" : "blocked");
      const kpiPressure = mod?.[metricKey] ?? 0;
      const ok = pressureSum === kpiPressure;
      report.checks.push({
        name: `card ${mid} trend pressure sum matches KPI ${metricKey}`,
        ok,
        pressureSum,
        kpiPressure,
      });
      if (!ok) report.failed += 1;
    }
  }

  const submoduleTab = process.env.SMOKE_SUBMODULE_TAB || "firewall-1-3";
  await moduleCard(page, "1.3").click();
  await page.waitForURL(new RegExp(`tab=${submoduleTab.replace("-", "\\-")}`), { timeout: 30000 });
  report.checks.push({ name: `click-through to ${submoduleTab}`, ok: page.url().includes(submoduleTab) });
  if (!page.url().includes(submoduleTab)) report.failed += 1;

  fs.mkdirSync(path.dirname(OUT), { recursive: true });
  fs.writeFileSync(OUT, JSON.stringify(report, null, 2));

  await browser.close();

  for (const check of report.checks) {
    console.log(`${check.ok ? "OK" : "FAIL"} ${check.name}`);
  }

  if (report.failed) {
    console.log(`--- failed=${report.failed} report=${OUT}`);
    process.exit(1);
  }
  console.log(`--- command grid smoke passed report=${OUT}`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
