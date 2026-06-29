/**
 * D4-ui-mcp browser verification (real customer perspective).
 *
 * Verifies, against the running Vite app (:8180) backed by control(:8100)+gateway(:8300),
 * Module 1.4 — Context Assembly & MCP Guardrails (?tab=firewall-1-4). The customer-reachable
 * control surface is the MCPConnectorPanel (which embeds the registry, Tool Discovery, Tool
 * Execution, the MCPScanControlMatrix, MCP Security Policies, and Observability tabs). NOTE:
 * MCPManagerPanel and MCPScannerPanel exist in the tree but are NOT wired into any live route
 * (referenced only in comments) — they are not customer-reachable, so the honest verification
 * drives the panel that actually renders for module 1.4.
 *
 *  1. Panel renders ("MCP Guardrails") with the six tabs; the MCP Servers tab lists servers.
 *  2. UI honesty: the number of server cards rendered == GET /api/mcp-connector/servers/ count,
 *     and the number of on-screen "Connected" badges == backend connected count.
 *  3. Register CRUD: open the Register modal, register a uniquely-named streamable-http server
 *     (POST /api/mcp-connector/servers/ -> 2xx); the new card appears AND backend GET lists it.
 *     Then Delete it via the card control (DELETE -> 2xx); the card disappears AND backend GET
 *     no longer lists it (side-effect free).
 *  4. Server default scan enforcement: change a connected server's "Default scan enforcement"
 *     Select (PATCH -> 2xx); backend reflects the new value; then RESTORE the original (PATCH).
 *  5. Tool Discovery tab renders discovered tools (rows > 0, consistent with backend tools).
 *  6. Tool Execution: select everything-mcp -> echo, run a tool call (POST tools/call/ -> 2xx);
 *     the on-screen decision badge reads "Allow" AND the echoed text preview is shown — the UI
 *     verdict mirrors the backend decision (honesty), not an independent claim.
 *  7. Scan Controls (MCPScanControlMatrix), MCP Security Policies, Observability tabs all render.
 *  8. No uncaught JS errors (pageerror) in any phase.
 *
 * Run: BASE_URL=http://127.0.0.1:8180 node scripts/playwright_mcp_panels.mjs
 *   (run FROM frontend/ so playwright resolves: cd frontend && node ../scripts/playwright_mcp_panels.mjs)
 */
import { chromium } from "playwright";
import fs from "node:fs";

const BASE = (process.env.BASE_URL || "http://127.0.0.1:8180").replace(/\/$/, "");
const EMAIL = process.env.TEST_EMAIL || "admin@zeroshield.io";
const PASS = process.env.TEST_PASSWORD || "Adm1n!Pass#2024";
const OUT = process.env.E2E_REPORT || "runs/playwright_mcp_panels.json";
const SHOT_DIR = process.env.SHOT_DIR || "runs/d4";

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

async function login(page) {
  CURRENT_PHASE = "login";
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle", timeout: 120000 });
  await page.locator("#email").fill(EMAIL);
  await page.locator("#password").fill(PASS);
  const [ok] = await Promise.all([
    page.waitForResponse((r) => r.url().includes("/api/auth/token/") && r.request().method() === "POST", { timeout: 30000 }),
    page.getByRole("button", { name: /^sign in$/i }).click(),
  ]);
  assert(ok.ok(), `valid creds -> 2xx (got ${ok.status()})`);
  await page.waitForFunction(() => !location.pathname.startsWith("/login"), { timeout: 30000 });
  report.steps.push("login");
}

// Read the access token out of the page so the harness can cross-check backend truth itself.
async function apiGet(page, path) {
  return page.evaluate(async (p) => {
    const tok = localStorage.getItem("auth_access");
    const r = await fetch(p, { headers: { Authorization: `Bearer ${tok}` } });
    return { status: r.status, body: await r.json().catch(() => null) };
  }, path);
}

async function clickTab(page, name) {
  const tab = page.getByRole("tab", { name });
  await tab.first().click();
}

async function main() {
  fs.mkdirSync("runs", { recursive: true });
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1100 } });
  const page = await context.newPage();
  page.on("pageerror", (e) => report.pageErrors.push({ phase: CURRENT_PHASE, message: String(e.message || e) }));
  page.on("dialog", (d) => d.accept()); // accept delete confirm dialogs

  const probeName = `d4-probe-${Date.now().toString(36)}`;
  let createdServerId = null;

  try {
    await login(page);

    // ───────── Module 1.4: MCP panel renders + servers list ─────────
    CURRENT_PHASE = "1.4-render";
    const [serversRes] = await Promise.all([
      page.waitForResponse(
        (r) => r.url().includes("/api/mcp-connector/servers/") && r.request().method() === "GET",
        { timeout: 60000 }
      ),
      page.goto(`${BASE}/?tab=firewall-1-4`, { waitUntil: "domcontentloaded", timeout: 120000 }),
    ]);
    assert(serversRes.ok(), `GET servers -> 2xx (${serversRes.status()})`);
    const backendServers = await serversRes.json();
    assert(Array.isArray(backendServers) && backendServers.length > 0, `backend lists ${backendServers.length} servers`);
    // module pages stream telemetry, so networkidle never settles — wait per-element.
    await page.locator("text=/MCP Guardrails/i").first().waitFor({ state: "visible", timeout: 30000 });
    await page.getByRole("tab", { name: /MCP Servers/i }).first().waitFor({ state: "visible", timeout: 30000 });
    await page.getByRole("tab", { name: /Tool Discovery/i }).first().waitFor({ state: "visible", timeout: 30000 });
    await page.getByRole("tab", { name: /Tool Execution/i }).first().waitFor({ state: "visible", timeout: 30000 });
    await page.getByRole("tab", { name: /Scan Controls/i }).first().waitFor({ state: "visible", timeout: 30000 });
    await page.getByRole("tab", { name: /MCP Security Policies/i }).first().waitFor({ state: "visible", timeout: 30000 });
    await page.getByRole("tab", { name: /Observability/i }).first().waitFor({ state: "visible", timeout: 30000 });
    assert(true, "1.4 renders MCPConnectorPanel with all six tabs");
    report.steps.push("1.4-render");
    await shot(page, "01-module-1-4");

    // ───────── UI honesty: cards == backend, connected badges == backend connected ─────────
    CURRENT_PHASE = "servers-honesty";
    // one Delete button per server card → a reliable on-screen card count.
    await page.getByLabel("Delete server").first().waitFor({ state: "visible", timeout: 30000 });
    const cardCount = await page.getByLabel("Delete server").count();
    assert(cardCount === backendServers.length, `rendered cards (${cardCount}) == backend servers (${backendServers.length})`);
    const backendConnected = backendServers.filter((s) => s.connection_status === "connected").length;
    // Count only the per-card connection badges (class .rounded-md), excluding the page-header
    // status pill (.rounded-full border) which also reads "Connected".
    const connectedBadges = await page.evaluate(() =>
      [...document.querySelectorAll("span.rounded-md")].filter((e) => {
        const direct = [...e.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent.trim()).join("");
        return direct === "Connected";
      }).length
    );
    assert(
      connectedBadges === backendConnected,
      `on-screen "Connected" badges (${connectedBadges}) == backend connected (${backendConnected})`
    );
    assert(backendConnected > 0, `at least one connected MCP server (${backendConnected})`);
    report.steps.push("servers-honesty");

    // ───────── Register CRUD (real customer control), side-effect free ─────────
    CURRENT_PHASE = "register";
    await page.getByRole("button", { name: /^Register Server$/i }).first().click();
    await page.getByText("Register MCP Server", { exact: false }).first().waitFor({ state: "visible", timeout: 15000 });
    await page.getByPlaceholder("my-mcp-server").fill(probeName);
    // Use stdio transport: the streamable-http path is gated by an SSRF guard that requires the
    // host to resolve (public hosts don't resolve from inside the control container). stdio needs
    // only an allow-listed interpreter + args and reliably creates the row.
    await page.getByLabel("Transport").selectOption("stdio");
    await page.getByPlaceholder("npx").fill("npx");
    await page.getByPlaceholder("-y, @playwright/mcp@latest").fill("-y, @modelcontextprotocol/server-everything");
    const [createRes] = await Promise.all([
      page.waitForResponse(
        (r) => r.url().endsWith("/api/mcp-connector/servers/") && r.request().method() === "POST",
        { timeout: 30000 }
      ),
      page.getByRole("button", { name: /^Register$/, exact: true }).click(),
    ]);
    assert(createRes.ok(), `register POST -> 2xx (${createRes.status()})`);
    const createBody = await createRes.json().catch(() => ({}));
    createdServerId = createBody.id || createBody.server?.id || null;
    // new card visible
    const probeCard = page.locator("div.p-4").filter({ hasText: probeName });
    await probeCard.first().waitFor({ state: "visible", timeout: 20000 });
    assert(true, `new server card "${probeName}" appears in the list`);
    // backend lists it
    const afterAdd = await apiGet(page, "/api/mcp-connector/servers/");
    assert(
      afterAdd.body.some((s) => s.name === probeName),
      `backend GET lists the registered server "${probeName}"`
    );
    report.steps.push("register");
    await shot(page, "02-registered");

    // Delete (cleanup) via the card control
    CURRENT_PHASE = "delete";
    const [delRes] = await Promise.all([
      page.waitForResponse(
        (r) => /\/api\/mcp-connector\/servers\/[^/]+\/$/.test(r.url()) && r.request().method() === "DELETE",
        { timeout: 30000 }
      ),
      probeCard.first().getByLabel("Delete server").click(),
    ]);
    assert(delRes.ok(), `delete DELETE -> 2xx (${delRes.status()})`);
    await probeCard.first().waitFor({ state: "detached", timeout: 20000 }).catch(() => {});
    const afterDel = await apiGet(page, "/api/mcp-connector/servers/");
    assert(
      !afterDel.body.some((s) => s.name === probeName),
      `backend GET no longer lists "${probeName}" (side-effect free)`
    );
    createdServerId = null;
    report.steps.push("delete");

    // ───────── Server default scan enforcement (real control + restore) ─────────
    CURRENT_PHASE = "scan-default";
    const connected = backendServers.find((s) => s.connection_status === "connected");
    assert(!!connected, "found a connected server to drive the scan-default control");
    const origAction = connected.default_scan_action || "tag";
    const newAction = origAction === "block" ? "tag" : "block";
    const targetCard = page.locator("div.p-4").filter({ hasText: connected.name });
    const scanSelect = targetCard.first().getByLabel("Default scan enforcement");
    const [patchRes] = await Promise.all([
      page.waitForResponse(
        (r) => /\/api\/mcp-connector\/servers\/[^/]+\/$/.test(r.url()) && r.request().method() === "PATCH",
        { timeout: 30000 }
      ),
      scanSelect.selectOption(newAction),
    ]);
    assert(patchRes.ok(), `scan-default PATCH -> 2xx (${patchRes.status()})`);
    const afterPatch = await apiGet(page, "/api/mcp-connector/servers/");
    const patched = afterPatch.body.find((s) => s.id === connected.id);
    assert(patched && patched.default_scan_action === newAction, `backend default_scan_action == "${newAction}"`);
    // restore
    const [restoreRes] = await Promise.all([
      page.waitForResponse(
        (r) => /\/api\/mcp-connector\/servers\/[^/]+\/$/.test(r.url()) && r.request().method() === "PATCH",
        { timeout: 30000 }
      ),
      scanSelect.selectOption(origAction),
    ]);
    assert(restoreRes.ok(), `scan-default restore PATCH -> 2xx (${restoreRes.status()})`);
    const afterRestore = await apiGet(page, "/api/mcp-connector/servers/");
    const restored = afterRestore.body.find((s) => s.id === connected.id);
    assert(restored && restored.default_scan_action === origAction, `backend restored to "${origAction}"`);
    report.steps.push("scan-default");

    // ───────── Tool Discovery tab ─────────
    CURRENT_PHASE = "tool-discovery";
    await clickTab(page, /Tool Discovery/i);
    const backendTools = await apiGet(page, "/api/mcp-connector/tools/");
    const toolList = Array.isArray(backendTools.body) ? backendTools.body : (backendTools.body?.results || backendTools.body?.tools || []);
    assert(toolList.length > 0, `backend exposes ${toolList.length} discovered tools`);
    // a tool name from the backend is rendered on the page
    const sampleTool = toolList.find((t) => t.name === "echo") || toolList[0];
    await page.getByText(sampleTool.name, { exact: false }).first().waitFor({ state: "visible", timeout: 20000 });
    assert(true, `Tool Discovery renders discovered tools (sample "${sampleTool.name}")`);
    report.steps.push("tool-discovery");
    await shot(page, "03-tool-discovery");

    // ───────── Tool Execution (live firewall-mediated call, honest verdict) ─────────
    CURRENT_PHASE = "tool-execution";
    await clickTab(page, /Tool Execution/i);
    const execServer = page.getByLabel("Execution server");
    await execServer.waitFor({ state: "visible", timeout: 20000 });
    await execServer.selectOption("everything-mcp");
    // pick the echo tool by its option value (label starts with "echo")
    const echoValue = await page.getByLabel("Tool to execute").evaluate((sel) => {
      const opt = [...sel.options].find((o) => /^echo\b/i.test(o.textContent || ""));
      return opt ? opt.value : "";
    });
    assert(!!echoValue, "everything-mcp exposes an echo tool in the execution picker");
    await page.getByLabel("Tool to execute").selectOption(echoValue);
    await page.getByLabel("Tool arguments (JSON)").fill('{"message":"zeroshield-d4-probe"}');
    const [callRes] = await Promise.all([
      page.waitForResponse(
        (r) => r.url().includes("/api/mcp-connector/tools/call/") && r.request().method() === "POST",
        { timeout: 60000 }
      ),
      page.getByRole("button", { name: /Execute Tool/i }).click(),
    ]);
    assert(callRes.ok(), `tool call POST -> 2xx (${callRes.status()})`);
    const callBody = await callRes.json().catch(() => ({}));
    assert(callBody.decision === "allow", `backend decision == "allow" (got "${callBody.decision}")`);
    // UI honesty: the decision badge + echoed preview must reflect the backend result
    await page.getByText("Execution Response", { exact: false }).first().waitFor({ state: "visible", timeout: 20000 });
    await page.locator("text=/^Allow$/").first().waitFor({ state: "visible", timeout: 20000 });
    await page.getByText("zeroshield-d4-probe", { exact: false }).first().waitFor({ state: "visible", timeout: 20000 });
    assert(true, 'on-screen verdict badge "Allow" + echoed preview mirror the backend decision');
    report.steps.push("tool-execution");
    await shot(page, "04-tool-execution");

    // ───────── Scan Controls / Security Policies / Observability render ─────────
    CURRENT_PHASE = "scan-controls";
    await clickTab(page, /Scan Controls/i);
    await page.locator("text=/scan control|Tier-1|Effective preview|Tier 1/i").first().waitFor({ state: "visible", timeout: 20000 });
    assert(true, "Scan Controls (MCPScanControlMatrix) renders");
    await shot(page, "05-scan-controls");

    CURRENT_PHASE = "policies";
    await clickTab(page, /MCP Security Policies/i);
    await page.locator("text=/Polic/i").first().waitFor({ state: "visible", timeout: 20000 });
    assert(true, "MCP Security Policies tab renders");

    CURRENT_PHASE = "observability";
    await clickTab(page, /Observability/i);
    await page.locator("text=/evidence|event|No .*activity|Observability/i").first().waitFor({ state: "visible", timeout: 20000 });
    assert(true, "Observability tab renders");
    report.steps.push("scan-controls+policies+observability");
    await shot(page, "06-observability");

    // ───────── No uncaught JS errors ─────────
    CURRENT_PHASE = "pageerrors";
    assert(report.pageErrors.length === 0, `no uncaught pageerror (saw ${report.pageErrors.length})`);

    report.ok = true;
  } catch (e) {
    report.error = String(e && e.stack ? e.stack : e);
    // best-effort cleanup of the probe server if a phase failed mid-CRUD
    try {
      if (createdServerId) {
        await page.evaluate(async (id) => {
          const tok = localStorage.getItem("auth_access");
          await fetch(`/api/mcp-connector/servers/${id}/`, { method: "DELETE", headers: { Authorization: `Bearer ${tok}` } });
        }, createdServerId);
      }
    } catch {}
    await shot(page, "zz-error");
  } finally {
    fs.writeFileSync(OUT, JSON.stringify(report, null, 2));
    await browser.close();
  }

  const passed = report.asserts.filter((a) => a.pass).length;
  console.log(`\nD4 MCP panels: ${report.ok ? "OK" : "FAIL"} — ${passed}/${report.asserts.length} asserts, ${report.steps.length} steps, ${report.pageErrors.length} pageErrors`);
  if (!report.ok) {
    console.log("ERROR:", report.error);
    if (report.pageErrors.length) console.log("pageErrors:", JSON.stringify(report.pageErrors, null, 2));
    process.exit(1);
  }
}

main();
