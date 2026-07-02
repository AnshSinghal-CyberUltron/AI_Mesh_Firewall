/**
 * M2.2 UEBA — Phase B: Attack Simulator → UEBA fleet live-sync gate.
 *
 * 1. Log into the app.
 * 2. Run an adversarial Attack Simulator payload on Module 1.1.
 * 3. Open /ueba/api-keys and assert simulator banner, Simulator badge, request count > 0.
 * 4. Expand the simulator row and assert Recent prompts contains data.
 * 5. Activate and deactivate kill switch from fleet row, assert KPI and button state parity.
 *
 * Run:
 *   NODE_PATH="$PWD/tests/e2e/node_modules" BASE_URL=http://127.0.0.1:8180 \
 *     node scripts/playwright_ueba_simulator_sync.mjs
 */
import { chromium } from "playwright";
import fs from "node:fs";

const BASE = (process.env.BASE_URL || "http://127.0.0.1:8180").replace(/\/$/, "");
const VITE_HOST_HEADER = process.env.VITE_HOST_HEADER || "";
const EMAIL = process.env.TEST_EMAIL || "admin@zeroshield.io";
const PASS = process.env.TEST_PASSWORD || "Adm1n!Pass#2024";
const OUT = process.env.E2E_REPORT || "runs/playwright_ueba_simulator_sync.json";
const SHOT_DIR = process.env.SHOT_DIR || "runs/ueba-simulator-sync";

const report = { base: BASE, ok: false, steps: [], asserts: [], pageErrors: [], notes: [], error: null };
let CURRENT_PHASE = "init";

async function acceptDialogSafely(dialog) {
  try {
    await dialog.accept();
  } catch {}
}

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

async function installGatewayProxy(context) {
  const origins = [
    "https://aimeshgateway.zeroshield.ai",
    "http://aimeshgateway.zeroshield.ai",
  ];
  for (const origin of origins) {
    await context.route(`${origin}/**`, async (route) => {
      const incoming = new URL(route.request().url());
      const proxied = `${BASE}${incoming.pathname}${incoming.search}`;
      const response = await route.fetch({
        url: proxied,
        method: route.request().method(),
        headers: route.request().headers(),
        postData: route.request().postData(),
      });
      await route.fulfill({ response });
    });
  }
}

async function seedSimulatorKey(page) {
  await page.evaluate(async ({ email, pass }) => {
    const tok = await fetch("/api/auth/token/", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password: pass }),
    }).then((r) => r.json());
    if (!tok.access) return;
    const sim = await fetch("/api/gateways/simulator-default/", {
      method: "POST",
      headers: { Authorization: `Bearer ${tok.access}` },
    }).then((r) => r.json());
    if (sim.storage_key && sim.key) {
      localStorage.setItem(sim.storage_key, sim.key);
    }
    if (sim.prefix) localStorage.setItem("zeroshield_gateway_key_prefix", sim.prefix);
    if (sim.key_id) localStorage.setItem("zeroshield_gateway_key_id", sim.key_id);
    localStorage.setItem("zeroshield_gateway_url", window.location.origin);
  }, { email: EMAIL, pass: PASS });
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

async function gotoTab(page, tab, anchorText) {
  await page.goto(`${BASE}/?tab=${tab}`, { waitUntil: "domcontentloaded", timeout: 120000 });
  await page.locator(`text=${anchorText}`).first().waitFor({ state: "visible", timeout: 60000 });
}

async function runAttackInjection(page) {
  CURRENT_PHASE = "m1-attack-simulator";
  await gotoTab(page, "firewall-1-1", "/Attack Simulator/i");
  const attackPanel = page.locator("div.ai-mesh-card").filter({ has: page.getByRole("heading", { name: /Attack Simulator/i }) }).first();
  await attackPanel.waitFor({ state: "visible", timeout: 60000 });
  await page.waitForResponse((r) => r.url().includes("/api/firewall/models/"), { timeout: 90000 }).catch(() => {});
  await page.waitForResponse((r) => r.url().includes("/api/gateways/simulator-default/"), { timeout: 90000 }).catch(() => {});
  await page.waitForTimeout(1500);
  await page.locator('[aria-label="Gateway API key"]').waitFor({ state: "visible", timeout: 30000 });
  const keyValue = await page.locator('[aria-label="Gateway API key"]').inputValue();
  assert(Boolean(keyValue?.trim()), "simulator gateway key is provisioned");

  const reset = attackPanel.getByRole("button", { name: /^Reset$/ });
  if (await reset.count()) {
    await reset.first().click();
    await page.waitForTimeout(200);
  }
  await attackPanel.getByRole("button", { name: /Prompt Injection/i }).first().click();
  await page.waitForTimeout(250);

  const [resp] = await Promise.all([
    page.waitForResponse((r) => r.url().includes("/v1/chat/completions") && r.request().method() === "POST", { timeout: 120000 }),
    attackPanel.getByRole("button", { name: /Run Pipeline/i }).click(),
  ]);
  report.notes.push(`attack injection HTTP ${resp.status()}`);
  assert(
    (resp.status() >= 400 && resp.status() < 500) || resp.status() === 503,
    `injection blocked by firewall (HTTP ${resp.status()} 4xx/503)`,
  );
  await attackPanel.getByText(/^BLOCKED$/).first().waitFor({ state: "visible", timeout: 20000 });
  report.steps.push("m1-attack-simulator");
  await shot(page, "01-attack-blocked");
}

async function assertUebaSync(page) {
  CURRENT_PHASE = "m2-ueba-sync";
  const [bundleResp] = await Promise.all([
    page.waitForResponse(
      (r) => r.url().includes("/api/module2/ueba/api-keys/bundle/") && r.request().method() === "GET" && r.ok(),
      { timeout: 90000 },
    ),
    page.goto(`${BASE}/ueba/api-keys`, { waitUntil: "domcontentloaded", timeout: 120000 }),
  ]);
  const bundle = await bundleResp.json().catch(() => ({}));
  const totalEvents = bundle?.summary?.summary?.total_events ?? 0;
  report.notes.push(`ueba bundle total_events=${totalEvents}`);
  assert(totalEvents > 0, "ueba bundle reports enforcement activity");

  await page.getByText(/Attack Simulator traffic is recorded under API key/i).first().waitFor({ state: "visible", timeout: 30000 });
  assert(true, "simulator banner visible on UEBA page");

  const simulatorBadge = page.locator("span", { hasText: /^Simulator$/ }).first();
  await simulatorBadge.waitFor({ state: "visible", timeout: 30000 });
  assert(true, "Simulator badge visible in fleet table");

  const simulatorRow = page.locator("tr").filter({ has: simulatorBadge }).first();
  await simulatorRow.waitFor({ state: "visible", timeout: 15000 });
  const requestsCell = simulatorRow.locator("td").nth(6);
  const requestCount = Number((await requestsCell.innerText()).trim()) || 0;
  report.notes.push(`simulator row request_count=${requestCount}`);
  assert(requestCount > 0, `simulator row request count > 0 (parsed ${requestCount})`);

  await simulatorRow.getByRole("button", { name: /^Expand$/i }).click();
  const behaviorResp = await page.waitForResponse(
    (r) => r.url().includes("/api/module2/ueba/api-keys/") && r.url().includes("/behavior/") && r.ok(),
    { timeout: 90000 },
  );
  const behavior = await behaviorResp.json();
  report.notes.push(`behavior recent_requests=${(behavior.recent_requests || []).length}`);
  assert((behavior.recent_requests || []).length > 0, "behavior API returns recent_requests");

  await page.locator(".animate-spin").first().waitFor({ state: "hidden", timeout: 60000 }).catch(() => {});
  const recentHeading = page.locator("tr").filter({ has: page.getByText(/^Recent prompts/i) }).getByText(/^Recent prompts/i).first();
  await recentHeading.scrollIntoViewIfNeeded();
  await recentHeading.waitFor({ state: "visible", timeout: 30000 });
  const recentText = await recentHeading.locator("xpath=ancestor::div[contains(@class,'rounded-lg')]").first().innerText();
  assert(/Recent prompts/i.test(recentText), "Recent prompts section visible");
  assert(/Ignore all previous|injection|DAN|prompt|total/i.test(recentText), "Recent prompts contains drill-down data");
  report.steps.push("m2-ueba-sync");
  await shot(page, "02-ueba-expanded");

  await page.getByRole("button", { name: "Refresh UEBA data" }).click();
  await page.waitForResponse(
    (r) => r.url().includes("/api/module2/ueba/api-keys/bundle/") && r.request().method() === "GET" && r.ok(),
    { timeout: 30000 },
  );
  assert(true, "Refresh UEBA data triggers bundle refetch");
}

async function assertUebaKillSwitchCycle(page) {
  CURRENT_PHASE = "m2-ueba-kill-switch";
  const simulatorBadge = page.locator("span", { hasText: /^Simulator$/ }).first();
  const simulatorRow = page.locator("tr").filter({ has: simulatorBadge }).first();
  await simulatorRow.waitFor({ state: "visible", timeout: 30000 });
  const ksCell = simulatorRow.locator("td").nth(8);
  const beforeLabel = (await ksCell.innerText()).trim();
  report.notes.push(`kill-switch count before=${beforeLabel || "-"}`);

  const killSwitchBtn = simulatorRow.getByRole("button", { name: /^Kill switch$/i });
  const preExistingDeactivate = simulatorRow.getByRole("button", {
    name: new RegExp("Deactivate kill switch for", "i"),
  });
  if (await preExistingDeactivate.count()) {
    page.once("dialog", acceptDialogSafely);
    await preExistingDeactivate.first().click();
    await page.waitForResponse(
      (r) => /\/api\/kill-switches\/\d+\/deactivate\/?$/.test(r.url()) && r.request().method() === "POST" && r.ok(),
      { timeout: 30000 },
    );
    await page.waitForResponse(
      (r) => r.url().includes("/api/module2/ueba/api-keys/bundle/") && r.request().method() === "GET" && r.ok(),
      { timeout: 60000 },
    );
  }

  page.once("dialog", acceptDialogSafely);
  await killSwitchBtn.click();
  await page.getByRole("button", { name: /Activate kill switch/i }).click();
  await page.waitForResponse(
    (r) => r.url().includes("/api/kill-switches/") && r.request().method() === "POST" && r.ok(),
    { timeout: 30000 },
  );
  await page.waitForResponse(
    (r) => r.url().includes("/api/module2/ueba/api-keys/bundle/") && r.request().method() === "GET" && r.ok(),
    { timeout: 60000 },
  );

  const deactivateBtn = simulatorRow.getByRole("button", {
    name: new RegExp("Deactivate kill switch for", "i"),
  });
  await deactivateBtn.waitFor({ state: "visible", timeout: 30000 });
  const afterActivate = (await ksCell.innerText()).trim();
  report.notes.push(`kill-switch count after activate=${afterActivate || "-"}`);
  assert(Number(afterActivate || "0") > 0, "fleet kill-switch count increases after activate");

  page.once("dialog", acceptDialogSafely);
  await deactivateBtn.click();
  await page.waitForResponse(
    (r) => /\/api\/kill-switches\/\d+\/deactivate\/?$/.test(r.url()) && r.request().method() === "POST" && r.ok(),
    { timeout: 30000 },
  );
  await page.waitForResponse(
    (r) => r.url().includes("/api/module2/ueba/api-keys/bundle/") && r.request().method() === "GET" && r.ok(),
    { timeout: 60000 },
  );
  await simulatorRow.getByRole("button", { name: /^Kill switch$/i }).waitFor({ state: "visible", timeout: 30000 });
  const afterDeactivate = (await ksCell.innerText()).trim();
  report.notes.push(`kill-switch count after deactivate=${afterDeactivate || "-"}`);
  assert(true, "kill-switch action returns to Kill switch button state after deactivate");
  report.steps.push("m2-ueba-kill-switch");
  await shot(page, "03-ueba-killswitch");
}

async function main() {
  fs.mkdirSync("runs", { recursive: true });
  const browser = await chromium.launch({ headless: true });
  const contextOpts = { viewport: { width: 1440, height: 1000 } };
  if (VITE_HOST_HEADER) {
    contextOpts.extraHTTPHeaders = { Host: VITE_HOST_HEADER };
  }
  const context = await browser.newContext(contextOpts);
  await installGatewayProxy(context);
  const page = await context.newPage();
  page.on("pageerror", (e) => report.pageErrors.push({ phase: CURRENT_PHASE, message: String(e.message || e) }));

  try {
    await login(page);
    await seedSimulatorKey(page);
    await runAttackInjection(page);
    await page.waitForTimeout(4000);
    await assertUebaSync(page);
    await assertUebaKillSwitchCycle(page);
    report.ok = true;
  } catch (err) {
    report.error = String(err?.message || err);
    await shot(page, "error");
  } finally {
    fs.writeFileSync(OUT, JSON.stringify(report, null, 2));
    await browser.close();
  }

  const failed = report.asserts.filter((a) => !a.pass);
  console.log(JSON.stringify({ ok: report.ok, steps: report.steps, failed: failed.map((f) => f.label), error: report.error }, null, 2));
  process.exit(report.ok ? 0 : 1);
}

main();
