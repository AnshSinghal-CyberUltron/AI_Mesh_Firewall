/**
 * P1.4 — Playwright repro: tool call surfaces "MCP sandbox is temporarily unavailable" (B3).
 * Stops mcp-broker to force gateway broker-unreachable path, then triggers tools/call via UI.
 *
 * Run:
 *   PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium-browser \
 *   BASE_URL=http://127.0.0.1:8180 \
 *   SHOT_DIR=mcp-parallel/findings/p1-4 \
 *   E2E_REPORT=mcp-parallel/findings/p1-4/report.json \
 *   node scripts/playwright_mcp_p1_sandbox_unavailable_repro.mjs
 */
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import path from "node:path";
import fs from "node:fs";
import { execSync } from "node:child_process";

const require = createRequire(import.meta.url);
const { chromium } = require(
  path.join(fileURLToPath(new URL(".", import.meta.url)), "../tests/e2e/node_modules/playwright")
);

const BASE = (process.env.BASE_URL || "http://127.0.0.1:8180").replace(/\/$/, "");
const EMAIL = process.env.TEST_EMAIL || "admin@zeroshield.io";
const PASS = process.env.TEST_PASSWORD || "Adm1n!Pass#2024";
const OUT = process.env.E2E_REPORT || "mcp-parallel/findings/p1-4/report.json";
const SHOT_DIR = process.env.SHOT_DIR || "mcp-parallel/findings/p1-4";
const NET_TRACE = process.env.NET_TRACE || "mcp-parallel/findings/p1-4/network.jsonl";
const BROKER_CONTAINER = process.env.BROKER_CONTAINER || "ai_mesh_mcp_broker";
const GATEWAY_CONTAINER = process.env.GATEWAY_CONTAINER || "ai_mesh_firewall-gateway-1";
const PRESET_NAME = process.env.MCP_STDIO_PRESET || "Everything MCP";

const TARGET_MSG = "MCP sandbox is temporarily unavailable";

const report = {
  story: "P1.4",
  bug: "B3",
  base: BASE,
  preset: PRESET_NAME,
  ok: false,
  steps: [],
  findings: {},
  network: [],
  pageErrors: [],
  error: null,
};

function mkdir() {
  fs.mkdirSync(SHOT_DIR, { recursive: true });
  fs.mkdirSync("mcp-parallel/findings", { recursive: true });
}

function dockerLogs(container, tail = 80) {
  try {
    return execSync(`docker logs ${container} --tail ${tail} 2>&1`, { encoding: "utf8", timeout: 15000 });
  } catch (e) {
    return String(e.stdout || e.message || e);
  }
}

function dockerStop(container) {
  try {
    execSync(`docker stop ${container}`, { encoding: "utf8", timeout: 30000 });
    return { stopped: true };
  } catch (e) {
    return { stopped: false, error: String(e.message || e) };
  }
}

function dockerStart(container) {
  try {
    execSync(`docker start ${container}`, { encoding: "utf8", timeout: 60000 });
    return { started: true };
  } catch (e) {
    return { started: false, error: String(e.message || e) };
  }
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

async function ensurePresetSynced(page) {
  await page.goto(`${BASE}/?tab=firewall-1-4`, { waitUntil: "domcontentloaded", timeout: 120000 });
  await page.locator("text=/MCP Guardrails/i").first().waitFor({ state: "visible", timeout: 60000 });
  report.steps.push("navigate-1-4");

  const presetBtn = page.getByRole("button", { name: new RegExp(PRESET_NAME, "i") });
  const existingCard = page.locator("h4", { hasText: PRESET_NAME }).first();
  if (!(await existingCard.isVisible().catch(() => false))) {
    await presetBtn.waitFor({ state: "visible", timeout: 30000 });
    const [createRes] = await Promise.all([
      page.waitForResponse(
        (r) => r.url().includes("/api/mcp-connector/servers/") && r.request().method() === "POST",
        { timeout: 120000 }
      ),
      presetBtn.click(),
    ]);
    if (!createRes.ok() && createRes.status() !== 409) {
      throw new Error(`Preset register failed: ${createRes.status()}`);
    }
    report.steps.push("preset-register");
  } else {
    report.steps.push("preset-already-listed");
  }

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
  report.findings.preFailureSync = { status: syncRes.status(), body: syncBody };
  if (!syncRes.ok() || syncBody.error || syncBody.connection_status === "failed") {
    throw new Error(`Pre-failure sync failed: ${syncBody.error || syncRes.status()}`);
  }
  report.steps.push(`pre-sync-ok:${syncBody.synced ?? "?"}`);
}

async function triggerToolCallWhileBrokerDown(page) {
  await page.getByRole("tab", { name: /tool execution/i }).click();
  await page.waitForTimeout(800);
  await shot(page, "01-tool-execution-tab");

  const serverSelect = page.getByLabel("Execution server");
  await serverSelect.waitFor({ state: "visible", timeout: 60000 });
  const serverOpts = await serverSelect.locator("option").evaluateAll((els) =>
    els.map((o) => ({ value: o.value, text: (o.textContent || "").trim() }))
  );
  const serverMatch = serverOpts.find((o) => o.value && new RegExp(PRESET_NAME.replace(/ MCP$/i, ""), "i").test(o.text));
  if (!serverMatch?.value) throw new Error(`Server '${PRESET_NAME}' not in execution dropdown`);
  await serverSelect.selectOption(serverMatch.value);

  const toolSelect = page.getByLabel("Tool to execute");
  await toolSelect.waitFor({ state: "visible", timeout: 30000 });
  const toolOpts = await toolSelect.locator("option").evaluateAll((els) =>
    els.map((o) => ({ value: o.value, text: (o.textContent || "").trim() }))
  );
  const toolMatch = toolOpts.find((o) => o.value && /echo/i.test(o.text));
  const pick = toolMatch || toolOpts.find((o) => o.value && !/select tool|choose a server/i.test(o.text));
  if (!pick?.value) throw new Error("No executable tool after sync");
  await toolSelect.selectOption(pick.value);
  report.findings.selectedTool = pick.text;

  await page.getByLabel("Tool arguments (JSON)").fill(JSON.stringify({ message: "p1-4 sandbox-down probe" }));

  report.findings.brokerLogsBefore = dockerLogs(BROKER_CONTAINER, 40);
  const stopResult = dockerStop(BROKER_CONTAINER);
  report.findings.brokerStop = stopResult;
  report.steps.push(stopResult.stopped ? "broker-stopped" : "broker-stop-failed");

  const callEntry = { ts: new Date().toISOString(), url: "", status: null, method: "POST", body: null };
  const [callRes] = await Promise.all([
    page.waitForResponse(
      (r) => r.url().includes("/api/mcp-connector/tools/call/") && r.request().method() === "POST",
      { timeout: 180000 }
    ),
    page.getByRole("button", { name: /execute tool/i }).click(),
  ]);

  callEntry.url = callRes.url();
  callEntry.status = callRes.status();
  callEntry.body = await callRes.json().catch(() => null);
  report.network.push(callEntry);

  await page.waitForTimeout(1500);
  const bodyText = await page.locator("body").innerText();
  const visibleErrors = [];
  if (/tool execution failed/i.test(bodyText)) {
    const m = bodyText.match(/Tool execution failed:[^\n]+/i);
    if (m) visibleErrors.push(m[0].trim());
  }
  const alertRegion = await page.locator('[role="alert"], .text-red-600, .text-red-500').allTextContents().catch(() => []);
  visibleErrors.push(...alertRegion.map((t) => t.trim()).filter(Boolean));

  report.findings.toolCallResponse = callEntry;
  report.findings.visibleErrors = [...new Set(visibleErrors)];
  report.findings.pageContainsTarget = bodyText.includes(TARGET_MSG);
  report.findings.responseContainsTarget = JSON.stringify(callEntry.body || "").includes(TARGET_MSG);

  await shot(page, "02-after-failed-tool-call");
  report.findings.gatewayLogsAfter = dockerLogs(GATEWAY_CONTAINER, 120);

  dockerStart(BROKER_CONTAINER);
  report.steps.push("broker-restarted");

  const b3Confirmed =
    report.findings.pageContainsTarget ||
    report.findings.responseContainsTarget ||
    report.findings.visibleErrors.some((e) => e.includes(TARGET_MSG));

  report.findings.b3BugConfirmed = b3Confirmed;
  report.findings.b3Message = TARGET_MSG;
  return b3Confirmed;
}

async function main() {
  mkdir();
  const netStream = fs.createWriteStream(NET_TRACE, { flags: "w" });
  const chromiumPath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH;
  const browser = await chromium.launch({
    headless: true,
    ...(chromiumPath ? { executablePath: chromiumPath } : {}),
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
    await ensurePresetSynced(page);
    const confirmed = await triggerToolCallWhileBrokerDown(page);
    report.ok = confirmed;
    report.steps.push(confirmed ? "repro-confirmed" : "repro-inconclusive");

    fs.writeFileSync(`${SHOT_DIR}/broker-logs-before.txt`, report.findings.brokerLogsBefore || "");
    fs.writeFileSync(`${SHOT_DIR}/gateway-logs-after.txt`, report.findings.gatewayLogsAfter || "");

    console.log(JSON.stringify(report.findings, null, 2));
    console.log(confirmed ? `B3 CONFIRMED — "${TARGET_MSG}" surfaced` : "B3 message not found in UI/API");
  } catch (e) {
    report.error = e.message;
    dockerStart(BROKER_CONTAINER).catch?.(() => {});
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
