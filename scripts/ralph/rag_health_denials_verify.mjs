/**
 * Verify Model & RAG Health shows Policy/Access Denials + Incidents RAG lane copy.
 * Uses live stack at BASE_URL (default http://127.0.0.1:8180).
 */
import { chromium } from "playwright";

const BASE_URL = process.env.BASE_URL || "http://127.0.0.1:8180";
const EMAIL = process.env.E2E_EMAIL || "admin@zeroshield.io";
const PASSWORD = process.env.E2E_PASSWORD || "Adm1n!Pass#2024";

async function login(page) {
  await page.goto(`${BASE_URL}/login`, { waitUntil: "domcontentloaded", timeout: 60000 });
  await page.locator("#email").fill(EMAIL);
  await page.locator("#password").fill(PASSWORD);
  await page.getByRole("button", { name: /sign in|log in/i }).click();
  await page.waitForURL((url) => !url.pathname.includes("/login"), { timeout: 60000 });
}

async function main() {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  const consoleErrors = [];
  page.on("console", (msg) => {
    if (msg.type() !== "error") return;
    const text = msg.text();
    // Live stack may point WS at a remote host that 403s in local Playwright — ignore.
    if (/WebSocket connection|wss?:\/\//i.test(text)) return;
    consoleErrors.push(text);
  });

  await login(page);

  // Health tab B
  await page.goto(`${BASE_URL}/models/exposure?tab=rag&period=24h`, {
    waitUntil: "networkidle",
    timeout: 90000,
  });
  // Click RAG tab explicitly in case query param is ignored before hydrate
  const ragTab = page.getByRole("tab", { name: /RAG/i });
  if (await ragTab.count()) {
    await ragTab.first().click();
  }
  try {
    await page.getByTestId("rag-health-metric-legend").waitFor({ timeout: 60000 });
  } catch (err) {
    const body = (await page.locator("body").innerText()).slice(0, 1200);
    console.error(JSON.stringify({ stage: "health-legend", body, url: page.url() }, null, 2));
    throw err;
  }
  const denialCard = page.getByTestId("rag-pre-pipeline-denials-card");
  await denialCard.waitFor({ timeout: 30000 });
  const denialText = await denialCard.innerText();
  const denialMatch = denialText.match(/(\d+)/);
  const denialCount = denialMatch ? Number(denialMatch[1]) : 0;
  const hasLegend = await page.getByText("Two different RAG metrics").isVisible();
  const hasPolicyLabel = await page.getByText("Policy / Access Denials").first().isVisible();

  // Incidents
  await page.goto(`${BASE_URL}/incidents?period=7d`, { waitUntil: "domcontentloaded", timeout: 60000 });
  await page.getByTestId("incidents-rag-lane-legend").waitFor({ timeout: 45000 });
  const laneLegend = await page.getByTestId("incidents-rag-lane-legend").innerText();
  const ragLaneChip = page.getByRole("button", { name: "RAG lane" });
  const hasRagLaneChip = await ragLaneChip.isVisible();

  const result = {
    ok:
      hasLegend &&
      hasPolicyLabel &&
      denialCount > 0 &&
      laneLegend.toLowerCase().includes("rag lane") &&
      laneLegend.includes("rag_query_blocked") &&
      hasRagLaneChip &&
      consoleErrors.length === 0,
    denialCount,
    hasLegend,
    hasPolicyLabel,
    hasRagLaneChip,
    laneLegendSnippet: laneLegend.slice(0, 180),
    consoleErrors: consoleErrors.slice(0, 5),
  };
  console.log(JSON.stringify(result, null, 2));
  await browser.close();
  process.exit(result.ok ? 0 : 1);
}

main().catch((err) => {
  console.error(String(err));
  process.exit(1);
});
