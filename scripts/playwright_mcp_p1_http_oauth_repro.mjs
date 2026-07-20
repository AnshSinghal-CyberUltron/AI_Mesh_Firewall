/**
 * P1.2 — Playwright repro: HTTP OAuth server lists with 0 tools (B2).
 * Register streamable-http + auth_type=oauth, capture immediate list state.
 *
 * Run:
 *   NODE_PATH="$PWD/tests/e2e/node_modules" \
 *   BASE_URL=http://127.0.0.1:8180 \
 *   SHOT_DIR=mcp-parallel/findings/p1-2 \
 *   E2E_REPORT=mcp-parallel/findings/p1-2/report.json \
 *   node scripts/playwright_mcp_p1_http_oauth_repro.mjs
 */
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import path from "node:path";
import fs from "node:fs";

const require = createRequire(import.meta.url);
const { chromium } = require(
  path.join(fileURLToPath(new URL(".", import.meta.url)), "../tests/e2e/node_modules/playwright")
);

const BASE = (process.env.BASE_URL || "http://127.0.0.1:8180").replace(/\/$/, "");
const EMAIL = process.env.TEST_EMAIL || "admin@zeroshield.io";
const PASS = process.env.TEST_PASSWORD || "Adm1n!Pass#2024";
const OUT = process.env.E2E_REPORT || "mcp-parallel/findings/p1-2/report.json";
const SHOT_DIR = process.env.SHOT_DIR || "mcp-parallel/findings/p1-2";
const NET_TRACE = process.env.NET_TRACE || "mcp-parallel/findings/p1-2/network.jsonl";
const PROBE_NAME = process.env.PROBE_NAME || `p1-2-http-oauth-${Date.now().toString(36)}`;
const OAUTH_URL = process.env.OAUTH_MCP_URL || "https://mcp.linear.app/mcp";

const report = {
  story: "P1.2",
  bug: "B2",
  base: BASE,
  probeName: PROBE_NAME,
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

async function shot(page, name) {
  mkdir();
  const path = `${SHOT_DIR}/${name}.png`;
  await page.screenshot({ path, fullPage: true });
  report.steps.push(`screenshot:${name}`);
  return path;
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

/** Extract one server card's visible + backend-aligned state by h4 title prefix. */
async function cardState(page, namePrefix) {
  return page.evaluate((wanted) => {
    for (const h of document.querySelectorAll("h4")) {
      const title = h.textContent.replace(/\s+/g, " ").trim();
      if (!title.includes(wanted)) continue;
      let node = h;
      let card = null;
      for (let i = 0; i < 12 && node; i++) {
        node = node.parentElement;
        if (!node) break;
        const dels = [...node.querySelectorAll("button")].filter((b) =>
          /Delete server/i.test(b.getAttribute("aria-label") || "")
        );
        if (dels.length === 1) {
          card = node;
          break;
        }
      }
      if (!card) return null;
      const syncBtn = [...card.querySelectorAll("button")].find((b) =>
        /Sync tools/i.test(b.getAttribute("aria-label") || "")
      );
      const authorizeBtn = [...card.querySelectorAll("button")].find((b) =>
        /^Authorize$|^Re-authorize$/i.test((b.textContent || "").trim())
      );
      const toolsMatch = card.textContent.match(/(\d+)\s+tools/i);
      const connBadge = [...h.querySelectorAll("span")].map((s) => s.textContent.trim()).filter(Boolean);
      return {
        titleSnippet: title.slice(0, 300),
        cardSnippet: card.innerText.slice(0, 2500),
        toolsCount: toolsMatch ? Number(toolsMatch[1]) : null,
        hasAuthorizationRequiredBadge: /authorization required/i.test(h.textContent),
        hasNeedsReauthBadge: /needs re-auth/i.test(h.textContent),
        connectionLabels: connBadge,
        hasUnknownConnection: /\bUnknown\b/i.test(h.textContent),
        syncDisabled: syncBtn ? syncBtn.disabled : null,
        hasAuthorizeButton: !!authorizeBtn,
        authorizeLabel: authorizeBtn ? authorizeBtn.textContent.trim() : null,
      };
    }
    return null;
  }, namePrefix);
}

async function apiGet(page, path) {
  return page.evaluate(async (p) => {
    const tok = localStorage.getItem("auth_access");
    const r = await fetch(p, { headers: { Authorization: `Bearer ${tok}` } });
    return { status: r.status, body: await r.json().catch(() => null) };
  }, path);
}

async function registerViaApi(page) {
  return page.evaluate(
    async ({ name, url }) => {
      const token = localStorage.getItem("auth_access")?.replace(/^"|"$/g, "");
      const res = await fetch("/api/mcp-connector/servers/", {
        method: "POST",
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
        body: JSON.stringify({
          name,
          transport: "streamable-http",
          url,
          auth_type: "oauth",
          description: "P1.2 B2 repro — unauthorized HTTP OAuth probe",
        }),
      });
      const body = await res.json().catch(() => ({}));
      return { status: res.status, body };
    },
    { name: PROBE_NAME, url: OAUTH_URL }
  );
}

async function registerViaModal(page) {
  await page.getByRole("button", { name: /^Register Server$/i }).first().click();
  await page.getByText("Register MCP Server", { exact: false }).first().waitFor({ state: "visible", timeout: 15000 });
  await page.getByPlaceholder("my-mcp-server").fill(PROBE_NAME);
  await page.getByPlaceholder("https://my-server.example.com/mcp").fill(OAUTH_URL);
  await page.getByLabel("Transport").selectOption("streamable-http");
  await page.getByLabel("Upstream authentication type").selectOption("oauth");
  const [createRes] = await Promise.all([
    page.waitForResponse(
      (r) => r.url().includes("/api/mcp-connector/servers/") && r.request().method() === "POST",
      { timeout: 60000 }
    ),
    page.getByRole("button", { name: /^Register$/, exact: true }).click(),
  ]);
  const body = await createRes.json().catch(() => ({}));
  return { status: createRes.status(), body, via: "modal" };
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
    if (!u.includes("mcp-connector") && !u.includes("/oauth/")) return;
    const entry = {
      ts: new Date().toISOString(),
      url: u,
      status: r.status(),
      method: r.request().method(),
    };
    report.network.push(entry);
    netStream.write(`${JSON.stringify(entry)}\n`);
  });

  let probeId = null;

  try {
    await login(page);

    await page.goto(`${BASE}/?tab=firewall-1-4`, { waitUntil: "domcontentloaded", timeout: 120000 });
    await page.locator("text=/MCP Guardrails/i").first().waitFor({ state: "visible", timeout: 60000 });
    report.steps.push("navigate-1-4");
    await shot(page, "01-mcp-panel-before");

    // Prefer UI modal registration (customer path); fall back to API if SSRF/validation blocks.
    let registerResult = null;
    try {
      registerResult = await registerViaModal(page);
      report.findings.registerVia = "modal";
      report.steps.push(`register-modal-${registerResult.status}`);
      if (!registerResult.status || registerResult.status >= 400) {
        throw new Error(`modal register failed: ${registerResult.status}`);
      }
      probeId = registerResult.body?.id;
      await page.waitForTimeout(800);
      await shot(page, "02-after-modal-register");
    } catch (modalErr) {
      report.findings.modalRegisterError = String(modalErr.message || modalErr);
      registerResult = await registerViaApi(page);
      report.findings.registerVia = "api-fallback";
      report.steps.push(`register-api-${registerResult.status}`);
      if (!registerResult.status || registerResult.status >= 400) {
        throw new Error(`API register failed: ${registerResult.status} ${JSON.stringify(registerResult.body)}`);
      }
      probeId = registerResult.body?.id;
      await page.goto(`${BASE}/?tab=firewall-1-4`, { waitUntil: "domcontentloaded", timeout: 120000 });
      await page.waitForTimeout(1500);
      await shot(page, "02-after-api-register");
    }

    report.findings.registeredServer = {
      id: probeId,
      ...(registerResult?.body || {}),
    };

    // Immediate list state — no authorize, no sync
    const serversAfter = await apiGet(page, "/api/mcp-connector/servers/");
    const backendRow = Array.isArray(serversAfter.body)
      ? serversAfter.body.find((s) => s.id === probeId || s.name === PROBE_NAME)
      : null;
    report.findings.backendRow = backendRow
      ? {
          id: backendRow.id,
          name: backendRow.name,
          transport: backendRow.transport,
          auth_type: backendRow.auth_type,
          oauth_authorized: backendRow.oauth_authorized,
          tools_count: backendRow.tools_count,
          connection_status: backendRow.connection_status,
          last_sync_at: backendRow.last_sync_at,
        }
      : null;

    const uiCard = await cardState(page, PROBE_NAME);
    report.findings.uiCard = uiCard;
    report.findings.listedImmediately =
      !!backendRow && (await page.locator("h4", { hasText: PROBE_NAME }).count()) > 0;

    await shot(page, "03-immediate-list-state");

    // Tools endpoint should be empty / gated pre-auth
    if (probeId) {
      const toolsRes = await apiGet(page, `/api/mcp-connector/servers/${probeId}/tools/`);
      report.findings.toolsEndpoint = { status: toolsRes.status, count: Array.isArray(toolsRes.body) ? toolsRes.body.length : null };
    }

    const f = report.findings;
    const toolsZero =
      (f.uiCard?.toolsCount === 0 || f.uiCard?.toolsCount === null) &&
      (f.backendRow?.tools_count === 0 || f.backendRow?.tools_count == null);

    // B2 = misleading "ready" 0-tools card (no pending cues) OR lists with 0 tools while unauthorized
    const pendingCues =
      f.uiCard?.hasAuthorizationRequiredBadge &&
      f.uiCard?.syncDisabled === true &&
      f.uiCard?.hasAuthorizeButton;

    const b2BugConfirmed =
      f.listedImmediately &&
      toolsZero &&
      !f.backendRow?.oauth_authorized &&
      (!pendingCues || f.uiCard?.hasUnknownConnection);

    const b2Partial =
      f.listedImmediately && toolsZero && !f.backendRow?.oauth_authorized && pendingCues;

    report.findings.b2BugConfirmed = b2BugConfirmed;
    report.findings.b2BugPartial = b2Partial;
    report.findings.b2ListsZeroToolsWhileUnauthorized =
      f.listedImmediately && toolsZero && !f.backendRow?.oauth_authorized;

    report.ok = true;
    report.steps.push("repro-complete");

    console.log(JSON.stringify(report.findings, null, 2));
    console.log(
      b2BugConfirmed
        ? "B2 BUG CONFIRMED — ready-looking 0-tools card"
        : b2Partial
          ? "B2 PARTIAL — lists with 0 tools but pending cues present"
          : "B2 state unclear — see findings"
    );
  } catch (e) {
    report.error = e.message;
    await shot(page, "99-error").catch(() => {});
    console.error("FAIL", e.message);
  } finally {
    if (probeId) {
      await page
        .evaluate(async (id) => {
          const token = localStorage.getItem("auth_access")?.replace(/^"|"$/g, "");
          await fetch(`/api/mcp-connector/servers/${id}/`, {
            method: "DELETE",
            headers: { Authorization: `Bearer ${token}` },
          });
        }, probeId)
        .catch(() => {});
      report.steps.push("cleanup-delete");
    }
    fs.writeFileSync(OUT, JSON.stringify(report, null, 2));
    netStream.end();
    await browser.close();
    process.exit(report.ok ? 0 : 1);
  }
}

main();
