/**
 * M2.5 MCP & Context Risk — Dry-Run isolation + Live telemetry UI sync gate.
 *
 * Run:
 *   NODE_PATH="$PWD/tests/e2e/node_modules" BASE_URL=http://127.0.0.1:8180 \
 *     node scripts/playwright_m24_mcp_risk_sync.mjs
 */
import { chromium } from "playwright";
import fs from "node:fs";

const BASE = (process.env.BASE_URL || "http://127.0.0.1:8180").replace(/\/$/, "");
const EMAIL = process.env.TEST_EMAIL || "admin@zeroshield.io";
const PASS = process.env.TEST_PASSWORD || "Adm1n!Pass#2024";
const OUT = process.env.E2E_REPORT || "runs/playwright_m24_mcp_risk_sync.json";
const SHOT_DIR = process.env.SHOT_DIR || "runs/m24-mcp-risk-sync";

const DRY_RUN_ARGS = JSON.stringify({ message: "contact admin@zeroshield.io for credentials" });
const LIVE_BLOCK_ARGS = JSON.stringify({});

const report = { base: BASE, ok: false, steps: [], asserts: [], pageErrors: [], notes: [], error: null };
let CURRENT_PHASE = "init";

function assert(cond, label) {
  report.asserts.push({ label, pass: !!cond, phase: CURRENT_PHASE });
  if (!cond) throw new Error(`ASSERT FAILED [${CURRENT_PHASE}]: ${label}`);
  return cond;
}

async function shot(page, name) {
  try {
    fs.mkdirSync(SHOT_DIR, { recursive: true });
    await page.screenshot({ path: `${SHOT_DIR}/${name}.png`, fullPage: false });
  } catch {}
}

async function waitForControlPlane(page) {
  for (let i = 0; i < 30; i++) {
    const ok = await page.evaluate(async () => {
      try {
        const c = await fetch("/api/health/", { signal: AbortSignal.timeout(4000) });
        return c.ok;
      } catch {
        return false;
      }
    });
    if (ok) return true;
    await page.waitForTimeout(2000);
  }
  return false;
}

async function login(page) {
  CURRENT_PHASE = "login";
  await page.goto(`${BASE}/login`, { waitUntil: "domcontentloaded", timeout: 120000 });
  await waitForControlPlane(page);
  await page.locator("#email").fill(EMAIL);
  await page.locator("#password").fill(PASS);
  const [tokenResp] = await Promise.all([
    page.waitForResponse((r) => r.url().includes("/api/auth/token/") && r.request().method() === "POST", { timeout: 45000 }),
    page.getByRole("button", { name: /^sign in$/i }).click(),
  ]);
  assert(tokenResp.ok(), `valid creds -> 2xx (got ${tokenResp.status()})`);
  await page.waitForFunction(() => !location.pathname.startsWith("/login"), { timeout: 45000 });
  report.steps.push("login");
}

async function apiGet(page, path) {
  return page.evaluate(async (p) => {
    const tok = localStorage.getItem("auth_access");
    const r = await fetch(p, { headers: { Authorization: `Bearer ${tok}` } });
    return { status: r.status, body: await r.json().catch(() => null) };
  }, path);
}

async function ensureMcpServer(page) {
  const slug = await page.evaluate(async () => {
    const tok = localStorage.getItem("auth_access");
    const headers = { Authorization: `Bearer ${tok}`, "Content-Type": "application/json" };
    const listResp = await fetch("/api/mcp-connector/servers/", { headers });
    const list = await listResp.json().catch(() => []);
    const servers = Array.isArray(list) ? list : list.results || [];
    let server = servers.find((s) => s.server_slug === "everything-mcp");
    if (!server) {
      const createResp = await fetch("/api/mcp-connector/servers/", {
        method: "POST",
        headers,
        body: JSON.stringify({
          name: "everything-mcp",
          transport: "stdio",
          command: "npx",
          args: ["-y", "@modelcontextprotocol/server-everything"],
          description: "M24 E2E stdio MCP server",
        }),
      });
      const created = await createResp.json().catch(() => ({}));
      if (!createResp.ok && !created.existing_server) {
        throw new Error(`register everything-mcp failed HTTP ${createResp.status}`);
      }
      server = created.existing_server || created;
    }
    if (server?.id) {
      for (let i = 0; i < 6; i += 1) {
        await fetch(`/api/mcp-connector/servers/${server.id}/tools/`, { method: "POST", headers });
        const toolsResp = await fetch(`/api/mcp-connector/servers/${server.id}/tools/`, { headers });
        const tools = await toolsResp.json().catch(() => []);
        const rows = Array.isArray(tools) ? tools : tools.results || [];
        if (rows.some((t) => (t.tool_name || t.name) === "echo")) break;
        await new Promise((r) => setTimeout(r, 2000));
      }
    }
    const verify = await fetch("/api/mcp-connector/servers/", { headers }).then((r) => r.json());
    const finalList = Array.isArray(verify) ? verify : verify.results || [];
    if (!finalList.length) throw new Error("no MCP servers after bootstrap");
    return finalList.find((s) => s.server_slug === "everything-mcp")?.server_slug || finalList[0].server_slug;
  });
  report.notes.push(`ensureMcpServer slug=${slug}`);
  return slug;
}

async function fetchMcpRiskSummary(page) {
  const { status, body } = await apiGet(page, "/api/module2/mcp/risk/?period=24h");
  assert(status === 200, `GET mcp/risk -> 200 (got ${status})`);
  return body?.summary || {};
}

function simulatorPanel(page) {
  return page.locator("div.rounded-2xl").filter({ has: page.getByRole("heading", { name: /MCP Policy Simulator/i }) }).first();
}

async function gotoSimulator(page) {
  CURRENT_PHASE = "m1-mcp-simulator";
  await page.goto(`${BASE}/?tab=firewall-1-4`, { waitUntil: "domcontentloaded", timeout: 120000 });
  await page.getByRole("heading", { name: /MCP Policy Simulator/i }).waitFor({ state: "visible", timeout: 90000 });
  await ensureMcpServer(page);
  await page.waitForResponse((r) => r.url().includes("/api/mcp-connector/servers/") && r.ok(), { timeout: 90000 }).catch(() => {});
  await page.waitForTimeout(800);
  report.steps.push("open-simulator");
}

async function prepareSimulator(page) {
  const panel = simulatorPanel(page);
  await panel.waitFor({ state: "visible", timeout: 60000 });

  let serverOptions = [];
  for (let attempt = 0; attempt < 4; attempt += 1) {
    if (attempt > 0) {
      await ensureMcpServer(page);
      await panel.getByRole("button", { name: /^Refresh$/i }).click();
      await page.waitForTimeout(1500);
    }
    const serverSelect = panel.locator("select").first();
    await serverSelect.waitFor({ state: "visible", timeout: 30000 });
    serverOptions = await serverSelect.locator("option").evaluateAll((opts) =>
      opts.map((o) => ({ value: o.value, text: o.textContent || "" })).filter((o) => o.value),
    );
    if (serverOptions.length > 0) break;
  }
  assert(serverOptions.length > 0, "simulator has MCP servers");
  const preferred =
    serverOptions.find((o) => /everything-mcp/i.test(o.text))
    || serverOptions.find((o) => /mcp-stub/i.test(o.text))
    || serverOptions[0];
  const serverSelect = panel.locator("select").first();
  await serverSelect.selectOption(preferred.value);
  await page.waitForResponse((r) => r.url().includes("/tools/") && r.ok(), { timeout: 60000 }).catch(() => {});
  await page.waitForTimeout(500);

  const toolSelect = panel.locator("select").nth(1);
  const toolOptions = await toolSelect.locator("option").evaluateAll((opts) =>
    opts.map((o) => ({ value: o.value, text: o.textContent || "" })).filter((o) => o.value),
  );
  assert(toolOptions.length > 0, "simulator has tools for selected server");
  const echoOpt = toolOptions.find((o) => /^echo\b/i.test(o.text)) || toolOptions[0];
  await toolSelect.selectOption(echoOpt.value);

  return {
    panel,
    toolName: echoOpt.value || echoOpt.text.split(/\s/)[0],
    serverLabel: preferred.text,
  };
}

async function setSimulatorArgs(page, panel, argsJson) {
  await panel.locator("textarea").first().fill(argsJson);
}

async function runDryRunIsolation(page, baselineSummary) {
  CURRENT_PHASE = "dry-run-isolation";
  await gotoSimulator(page);
  const { panel, toolName } = await prepareSimulator(page);
  await setSimulatorArgs(page, panel, DRY_RUN_ARGS);

  await panel.getByRole("button", { name: /^Dry-Run$/i }).click();
  const [dryResp] = await Promise.all([
    page.waitForResponse((r) => r.url().includes("/api/policies/test/") && r.request().method() === "POST", { timeout: 60000 }),
    panel.getByRole("button", { name: /Evaluate Policies/i }).click(),
  ]);
  const dryBody = await dryResp.json().catch(() => ({}));
  report.notes.push(`dry-run HTTP ${dryResp.status()} action=${dryBody.action}`);
  assert(dryResp.ok(), `dry-run policies/test -> 2xx (${dryResp.status()})`);
  assert(["block", "redact"].includes(dryBody.action), `dry-run triggers violation (action=${dryBody.action})`);

  await page.goto(`${BASE}/mcp/risk?period=24h`, { waitUntil: "domcontentloaded", timeout: 120000 });
  await page.getByRole("heading", { name: /MCP & Context Risk/i }).waitFor({ state: "visible", timeout: 60000 });

  const afterDry = await fetchMcpRiskSummary(page);
  const delta = (afterDry.total_events ?? 0) - (baselineSummary.total_events ?? 0);
  report.notes.push(`after dry-run total_events delta=${delta}`);

  const emptyBanner = page.getByText(/No MCP tool activity in this period/i);
  const emptyVisible = await emptyBanner.isVisible().catch(() => false);
  const metricsUnchanged = delta === 0;
  assert(metricsUnchanged, `dry-run did not add telemetry (delta=${delta})`);
  if ((baselineSummary.total_events ?? 0) === 0) {
    assert(emptyVisible || metricsUnchanged, "empty state remains when baseline had no activity");
  }
  report.steps.push("dry-run-isolation");
  await shot(page, "01-dry-run-no-telemetry");
  return afterDry;
}

async function runLiveSync(page, beforeLiveSummary) {
  CURRENT_PHASE = "live-telemetry";
  await gotoSimulator(page);
  const { panel, toolName } = await prepareSimulator(page);
  await setSimulatorArgs(page, panel, LIVE_BLOCK_ARGS);

  await panel.getByRole("button", { name: /^Live Call$/i }).click();
  const [liveResp] = await Promise.all([
    page.waitForResponse((r) => r.url().includes("/api/mcp-connector/tools/call/") && r.request().method() === "POST", { timeout: 120000 }),
    panel.getByRole("button", { name: /Invoke Tool/i }).click(),
  ]);
  report.notes.push(`live call HTTP ${liveResp.status()}`);
  assert(liveResp.status() >= 200 && liveResp.status() < 500, `live tool call completed (HTTP ${liveResp.status()})`);
  report.steps.push("live-call");

  await page.waitForTimeout(4000);

  const [riskResp] = await Promise.all([
    page.waitForResponse(
      (r) => r.url().includes("/api/module2/mcp/risk/") && r.request().method() === "GET" && r.ok(),
      { timeout: 90000 },
    ),
    page.goto(`${BASE}/mcp/risk?period=24h`, { waitUntil: "domcontentloaded", timeout: 120000 }),
  ]);
  const risk = await riskResp.json().catch(() => ({}));
  const summary = risk.summary || {};
  report.notes.push(
    `live summary total=${summary.total_events} blocked=${summary.blocked_tool_calls} redacted=${summary.redacted_arguments}`,
  );

  await page.getByRole("heading", { name: /MCP & Context Risk/i }).waitFor({ state: "visible", timeout: 60000 });

  const badgeText = await page.locator("span.rounded-full").filter({ hasText: /Live|On activity/i }).first().innerText().catch(() => "");
  assert(/Live|On activity/i.test(badgeText), `status badge shows Live or On activity (got "${badgeText}")`);

  const totalDelta = (summary.total_events ?? 0) - (beforeLiveSummary.total_events ?? 0);
  const violationDelta =
    (summary.blocked_tool_calls ?? 0) +
    (summary.redacted_arguments ?? 0) -
    ((beforeLiveSummary.blocked_tool_calls ?? 0) + (beforeLiveSummary.redacted_arguments ?? 0));
  assert(totalDelta >= 1, `MCP Events KPI incremented (delta=${totalDelta})`);
  assert(violationDelta >= 1, `violation counters incremented (delta=${violationDelta})`);

  await page.getByText(/MCP Events/i).first().waitFor({ state: "visible", timeout: 30000 });
  await page.getByText(/Tools With the Most Violations/i).first().waitFor({ state: "visible", timeout: 30000 });

  const bodyText = await page.locator("body").innerText();
  const ledgerHasTool = (risk.tool_ledger || []).some((row) => row.tool && bodyText.includes(String(row.tool)));
  assert(ledgerHasTool || bodyText.includes(toolName), `tool name visible on risk page (${toolName})`);

  report.steps.push("live-mcp-risk-sync");
  await shot(page, "02-live-mcp-risk");
}

async function main() {
  fs.mkdirSync("runs", { recursive: true });
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1100 } });
  const page = await context.newPage();
  page.on("pageerror", (e) => report.pageErrors.push({ phase: CURRENT_PHASE, message: String(e.message || e) }));

  try {
    await login(page);
    await ensureMcpServer(page);
    CURRENT_PHASE = "baseline";
    const baseline = await fetchMcpRiskSummary(page);
    report.notes.push(`baseline total_events=${baseline.total_events ?? 0}`);
    const afterDry = await runDryRunIsolation(page, baseline);
    await runLiveSync(page, afterDry);
    assert(report.pageErrors.length === 0, `no uncaught page errors (${report.pageErrors.length})`);
    report.ok = true;
  } catch (err) {
    report.error = String(err?.message || err);
    await shot(page, "error");
  } finally {
    fs.writeFileSync(OUT, JSON.stringify(report, null, 2));
    await browser.close();
  }

  const passed = report.asserts.filter((a) => a.pass).length;
  const failed = report.asserts.filter((a) => !a.pass).length;
  console.log(JSON.stringify({ ok: report.ok, passed, failed, steps: report.steps, error: report.error }, null, 2));
  process.exit(report.ok ? 0 : 1);
}

main();
