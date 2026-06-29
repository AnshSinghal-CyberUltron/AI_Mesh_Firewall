/**
 * E2 MCP sandbox stdio flow — preset register, tools/list (sync), tools/call.
 * Requires full stack: control + gateway + mcp-broker with MCP_STDIO_IN_PROCESS=false.
 * Run: BASE_URL=http://127.0.0.1:8180 node scripts/playwright_mcp_sandbox_stdio.mjs
 */
import { chromium } from "playwright";
import fs from "node:fs";

const BASE = (process.env.BASE_URL || "http://127.0.0.1:8180").replace(/\/$/, "");
const EMAIL = process.env.TEST_EMAIL || "admin@zeroshield.io";
const PASS = process.env.TEST_PASSWORD || "Adm1n!Pass#2024";
const OUT = process.env.E2E_REPORT || "runs/playwright_mcp_sandbox_stdio.json";
const PRESET_NAME = process.env.MCP_STDIO_PRESET || "Vibe Check";
const TOOL_CALL_ARGS = process.env.MCP_TOOL_ARGS || JSON.stringify({
  goal: "E2 stdio sandbox verification",
  plan: "Register stdio MCP, sync tools/list, invoke tools/call via UI",
});

async function login(page) {
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle", timeout: 120000 });
  await page.locator("#email").fill(EMAIL);
  await page.locator("#password").fill(PASS);
  const [res] = await Promise.all([
    page.waitForResponse((r) => r.url().includes("/api/auth/token/") && r.request().method() === "POST"),
    page.getByRole("button", { name: /^sign in$/i }).click(),
  ]);
  if (!res.ok()) throw new Error(`Login failed: ${res.status()}`);
  const data = await res.json();
  return data.access;
}

async function main() {
  fs.mkdirSync("runs", { recursive: true });
  const report = { base: BASE, preset: PRESET_NAME, ok: false, steps: [], error: null };
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();

  try {
    const token = await login(page);
    report.steps.push("login");

    await page.goto(`${BASE}/?tab=firewall-1-4`, { waitUntil: "networkidle", timeout: 120000 });
    const bodyText = await page.locator("body").innerText();
    if (/Failed to fetch|Vite.*error|Cannot find module/i.test(bodyText)) {
      throw new Error("Fatal UI error on MCP tab");
    }
    report.steps.push("navigate-mcp-tab");

    const presetBtn = page.getByRole("button", { name: new RegExp(PRESET_NAME, "i") });
    const existingCard = page.locator("h4", { hasText: PRESET_NAME }).first();
    if (await existingCard.isVisible().catch(() => false)) {
      report.steps.push("stdio-server-already-listed");
    } else {
      await presetBtn.waitFor({ state: "visible", timeout: 30000 });
      const [createRes] = await Promise.all([
        page.waitForResponse(
          (r) => r.url().includes("/api/mcp-connector/servers/") && r.request().method() === "POST",
          { timeout: 120000 }
        ),
        presetBtn.click(),
      ]);
      if (!createRes.ok() && createRes.status() !== 409) {
        const errBody = await createRes.text().catch(() => "");
        throw new Error(`Preset register failed: HTTP ${createRes.status()} ${errBody.slice(0, 200)}`);
      }
      report.steps.push(createRes.status() === 409 ? "stdio-preset-exists" : "stdio-preset-register");
    }

    await page.getByRole("button", { name: /^refresh$/i }).first().click().catch(() => {});
    await page.waitForTimeout(1500);

    const serverCard = page.locator("h4", { hasText: PRESET_NAME }).first();
    await serverCard.waitFor({ state: "visible", timeout: 30000 });
    const card = serverCard.locator("xpath=ancestor::div[contains(@class,'rounded')][1]");
    const syncBtn = card.getByRole("button", { name: /sync tools from server/i });
    const [syncRes] = await Promise.all([
      page.waitForResponse(
        (r) => /\/api\/mcp-connector\/servers\/[^/]+\/tools\/?$/.test(r.url()) && r.request().method() === "POST",
        { timeout: 180000 }
      ),
      syncBtn.click(),
    ]);
    const syncBody = await syncRes.json().catch(() => ({}));
    if (!syncRes.ok()) {
      throw new Error(`tools/list sync failed: HTTP ${syncRes.status()}`);
    }
    if (syncBody.error || syncBody.connection_status === "failed") {
      throw new Error(`tools/list sync error: ${syncBody.error || "connection failed"}`);
    }
    report.steps.push(`tools-list-synced:${syncBody.synced ?? "?"}`);

    await page.getByRole("tab", { name: /tool execution/i }).click();
    await page.waitForResponse(
      (r) => r.url().includes("/api/mcp-connector/tools/") && r.request().method() === "GET",
      { timeout: 120000 }
    ).catch(() => null);
    await page.locator('[role="status"], .animate-spin').first().waitFor({ state: "hidden", timeout: 120000 }).catch(() => {});
    await page.waitForSelector('[aria-label="Execution server"]', { timeout: 120000 });

    const serverSelect = page.getByLabel("Execution server");
    const serverOpts = await serverSelect.locator("option").evaluateAll((els) =>
      els.map((o) => ({ value: o.value, text: (o.textContent || "").trim() }))
    );
    const serverMatch = serverOpts.find((o) => o.value && new RegExp(PRESET_NAME, "i").test(o.text));
    if (!serverMatch?.value) throw new Error("Server not found in execution dropdown");
    await serverSelect.selectOption(serverMatch.value);

    const toolSelect = page.getByLabel("Tool to execute");
    await toolSelect.waitFor({ state: "visible", timeout: 30000 });
    const toolOpts = await toolSelect.locator("option").evaluateAll((els) =>
      els.map((o) => ({ value: o.value, text: (o.textContent || "").trim() }))
    );
    const toolMatch = toolOpts.find((o) => o.value && !/select tool|choose a server/i.test(o.text));
    if (!toolMatch?.value) throw new Error("No tools available after sync");
    const preferredTool = toolOpts.find(
      (o) => o.value && /vibe_check|echo|ping|health/i.test(o.text)
    );
    await toolSelect.selectOption((preferredTool || toolMatch).value);

    await page.getByLabel("Tool arguments (JSON)").fill(TOOL_CALL_ARGS);

    const [callRes] = await Promise.all([
      page.waitForResponse(
        (r) => r.url().includes("/api/mcp-connector/tools/call/") && r.request().method() === "POST",
        { timeout: 180000 }
      ),
      page.getByRole("button", { name: /execute tool/i }).click(),
    ]);
    if (!callRes.ok()) {
      const errBody = await callRes.text().catch(() => "");
      throw new Error(`tools/call failed: HTTP ${callRes.status()} ${errBody.slice(0, 200)}`);
    }
    const callBody = await callRes.json().catch(() => ({}));
    if (callBody.error && !callBody.result) {
      throw new Error(`tools/call error: ${callBody.detail || callBody.error}`);
    }
    report.steps.push("tools-call-ok");
    report.ok = true;
    console.log("OK MCP sandbox stdio flow", report.steps.join(" → "));
  } catch (e) {
    report.error = e.message;
    console.log("FAIL MCP sandbox stdio", e.message);
  } finally {
    await browser.close();
    fs.writeFileSync(OUT, JSON.stringify(report, null, 2));
    console.log("Report:", OUT);
    process.exit(report.ok ? 0 : 1);
  }
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
