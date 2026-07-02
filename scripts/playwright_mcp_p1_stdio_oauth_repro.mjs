/**
 * P1.1 — Playwright repro: stdio Linear OAuth bug (B1).
 * Register Linear (stdio + mcp-remote), capture authorize buttons + error messages.
 *
 * Run:
 *   NODE_PATH="$PWD/tests/e2e/node_modules" \
 *   BASE_URL=http://127.0.0.1:8180 \
 *   SHOT_DIR=mcp-parallel/findings/p1-1 \
 *   E2E_REPORT=mcp-parallel/findings/p1-1/report.json \
 *   node scripts/playwright_mcp_p1_stdio_oauth_repro.mjs
 */
import { chromium } from "playwright";
import fs from "node:fs";

const BASE = (process.env.BASE_URL || "http://127.0.0.1:8180").replace(/\/$/, "");
const EMAIL = process.env.TEST_EMAIL || "admin@zeroshield.io";
const PASS = process.env.TEST_PASSWORD || "Adm1n!Pass#2024";
const OUT = process.env.E2E_REPORT || "mcp-parallel/findings/p1-1/report.json";
const SHOT_DIR = process.env.SHOT_DIR || "mcp-parallel/findings/p1-1";
const PRESET = "Linear MCP";

const report = {
  story: "P1.1",
  bug: "B1",
  base: BASE,
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

async function main() {
  mkdir();
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1200 } });
  const page = await context.newPage();

  page.on("pageerror", (e) => report.pageErrors.push(String(e.message || e)));
  page.on("response", (r) => {
    const u = r.url();
    if (u.includes("/oauth/") || u.includes("mcp-connector/servers")) {
      report.network.push({ url: u, status: r.status(), method: r.request().method() });
    }
  });

  try {
    await login(page);

    await page.goto(`${BASE}/?tab=firewall-1-4`, { waitUntil: "domcontentloaded", timeout: 120000 });
    await page.locator("text=/MCP Guardrails/i").first().waitFor({ state: "visible", timeout: 60000 });
    report.steps.push("navigate-1-4");
    await shot(page, "01-mcp-panel");

    // Register Linear preset if not already present
    const existing = page.locator("h4", { hasText: PRESET }).first();
    if (!(await existing.isVisible().catch(() => false))) {
      const presetBtn = page.getByRole("button", { name: /Linear MCP/i });
      await presetBtn.waitFor({ state: "visible", timeout: 30000 });
      const [createRes] = await Promise.all([
        page.waitForResponse(
          (r) => r.url().includes("/api/mcp-connector/servers/") && r.request().method() === "POST",
          { timeout: 120000 }
        ),
        presetBtn.click(),
      ]);
      report.findings.registerStatus = createRes.status();
      const createBody = await createRes.json().catch(() => ({}));
      report.findings.registeredServer = {
        id: createBody.id,
        name: createBody.name,
        transport: createBody.transport,
        auth_type: createBody.auth_type,
        args: createBody.args,
        url: createBody.url,
      };
      report.steps.push(createRes.status() === 409 ? "linear-already-exists" : "register-linear-preset");
    } else {
      report.steps.push("linear-already-listed");
    }

    await page.waitForTimeout(1500);
    await shot(page, "02-after-register");

    const card = page.locator("h4", { hasText: PRESET }).first().locator("xpath=ancestor::div[contains(@class,'p-4')][1]");
    await card.waitFor({ state: "visible", timeout: 30000 });

    const authorizeButtons = card.getByRole("button", { name: /^Authorize$|^Re-authorize$/i });
    const authorizeCount = await authorizeButtons.count();
    report.findings.authorizeButtonCount = authorizeCount;
    report.findings.authorizeButtonLabels = await authorizeButtons.allTextContents();

    const cardText = await card.innerText();
    report.findings.cardSnippet = cardText.slice(0, 2000);
    report.findings.hasAuthorizationRequiredBadge = /authorization required/i.test(cardText);
    report.findings.hasNoUrlError = /Server has no URL|OAuth is only for HTTP/i.test(cardText);

    await shot(page, "03-linear-card-buttons");

    // Click each Authorize button and capture errors
    const clickResults = [];
    for (let i = 0; i < authorizeCount; i++) {
      const btn = authorizeButtons.nth(i);
      const label = (await btn.textContent())?.trim() || `btn-${i}`;
      const [popup] = await Promise.all([
        context.waitForEvent("page", { timeout: 5000 }).catch(() => null),
        btn.click(),
      ]);
      await page.waitForTimeout(2000);
      const bodyText = await page.locator("body").innerText();
      const errMatch = bodyText.match(/OAuth[^\\n]{0,120}(failed|error)[^\\n]*/i)
        || bodyText.match(/Server has no URL[^\\n]*/i)
        || bodyText.match(/OAuth is only for HTTP[^\\n]*/i);
      clickResults.push({
        button: label,
        popupOpened: !!popup,
        popupUrl: popup ? popup.url() : null,
        visibleError: errMatch ? errMatch[0] : null,
      });
      if (popup && !popup.isClosed()) await popup.close().catch(() => {});
      await shot(page, `04-after-click-${i}-${label.replace(/\s+/g, "-")}`);
    }
    report.findings.authorizeClickResults = clickResults;

    const globalErrors = await page.locator("text=/OAuth|Server has no URL|authorization/i").allTextContents();
    report.findings.visibleOAuthMessages = [...new Set(globalErrors)].slice(0, 20);

    // Bug confirmed if: 2+ authorize buttons OR no-url error visible
    const bugConfirmed =
      authorizeCount >= 2
      || report.findings.hasNoUrlError
      || clickResults.some((r) => r.visibleError && /no URL|only for HTTP/i.test(r.visibleError));

    report.findings.b1BugConfirmed = bugConfirmed;
    report.findings.b1BugPartial = authorizeCount === 1 && report.findings.hasAuthorizationRequiredBadge;
    report.ok = true;
    report.steps.push("repro-complete");

    console.log(JSON.stringify(report.findings, null, 2));
    console.log(bugConfirmed ? "B1 BUG CONFIRMED" : "B1 may be fixed or partial — see findings");
  } catch (e) {
    report.error = e.message;
    await shot(page, "99-error").catch(() => {});
    console.error("FAIL", e.message);
  } finally {
    fs.writeFileSync(OUT, JSON.stringify(report, null, 2));
    await browser.close();
    process.exit(report.ok ? 0 : 1);
  }
}

main();
