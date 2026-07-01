/**
 * M2.5 Threat Intelligence Ops — IOC CRUD, Redis sync, Attack Simulator → telemetry sync gate.
 *
 * Run:
 *   NODE_PATH="$PWD/tests/e2e/node_modules" BASE_URL=http://127.0.0.1:8180 \
 *     node scripts/playwright_m25_threat_intel_sync.mjs
 */
import { chromium } from "playwright";
import { execSync } from "node:child_process";
import fs from "node:fs";

const BASE = (process.env.BASE_URL || "http://127.0.0.1:8180").replace(/\/$/, "");
const EMAIL = process.env.TEST_EMAIL || "admin@zeroshield.io";
const PASS = process.env.TEST_PASSWORD || "Adm1n!Pass#2024";
const ORG_SLUG = process.env.ORG_SLUG || "zeroshield";
const OUT = process.env.E2E_REPORT || "runs/playwright_m25_threat_intel_sync.json";
const SHOT_DIR = process.env.SHOT_DIR || "runs/m25-threat-intel-sync";

const STAMP = Date.now();
const INDICATOR = process.env.IOC_INDICATOR || `pw_ioc_probe_${STAMP}`;
const THREAT_TYPE = process.env.IOC_THREAT_TYPE || "pw_jailbreak_probe";

const report = { base: BASE, ok: false, steps: [], asserts: [], pageErrors: [], notes: [], indicator: INDICATOR, error: null };
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

function inlineRedisSync() {
  try {
    const cmd =
      `docker exec -w /app/control ai_mesh_firewall-control-1 python manage.py shell -c ` +
      `"from auth.models import Organization; from module2.tasks import sync_threat_intel_to_redis; ` +
      `org=Organization.objects.filter(slug='${ORG_SLUG}').first(); assert org; sync_threat_intel_to_redis(org.id); print('redis_sync_ok')"`;
    const out = execSync(cmd, { encoding: "utf-8", timeout: 90000 });
    if (!out.includes("redis_sync_ok")) throw new Error(`inline redis sync failed: ${out}`);
    report.notes.push("inline redis sync ok (host docker)");
  } catch (err) {
    report.notes.push(`inline redis sync skipped (${String(err?.message || err).slice(0, 120)})`);
  }
}

function drainTelemetry() {
  try {
    execSync(
      `docker exec -w /app/control ai_mesh_firewall-control-1 python manage.py shell -c ` +
        `"from core.tasks import drain_telemetry_from_redis; print(drain_telemetry_from_redis(100))"`,
      { encoding: "utf-8", timeout: 60000 },
    );
  } catch {
    report.notes.push("telemetry drain skipped (no host docker)");
  }
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
  for (const origin of ["https://aimeshgateway.zeroshield.ai", "http://aimeshgateway.zeroshield.ai"]) {
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

async function provisionGatewayKey(page) {
  return page.evaluate(async () => {
    const tok = localStorage.getItem("auth_access");
    const headers = { Authorization: `Bearer ${tok}`, "Content-Type": "application/json" };
    const stamp = Date.now();
    const resp = await fetch("/api/gateways/keys/", {
      method: "POST",
      headers,
      body: JSON.stringify({ name: `pw-ti-e2e-${stamp}`, project_id: `threat-intel-e2e-${stamp}` }),
    });
    const body = await resp.json().catch(() => ({}));
    if (!resp.ok || !body.key) throw new Error(`gateway key create failed HTTP ${resp.status}`);
    return body.key;
  });
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

async function fetchTelemetrySummary(page, period = "24h") {
  return page.evaluate(async (p) => {
    const tok = localStorage.getItem("auth_access");
    const r = await fetch(`/api/module2/threat-intel/telemetry/?period=${p}&_=${Date.now()}`, {
      headers: { Authorization: `Bearer ${tok}` },
    });
    const body = await r.json().catch(() => ({}));
    return { status: r.status, summary: body.summary || {}, ioc_library: body.ioc_library || {} };
  }, period);
}

async function addIocAndSync(page) {
  CURRENT_PHASE = "m25-ioc-crud";
  await page.goto(`${BASE}/threat-intel?period=24h`, { waitUntil: "domcontentloaded", timeout: 120000 });
  await page.getByRole("heading", { name: /Threat Intelligence Ops/i }).waitFor({ state: "visible", timeout: 60000 });

  await page.getByRole("button", { name: /Fill example IOC/i }).click();
  await page.getByPlaceholder(/Threat Type/i).fill(THREAT_TYPE);
  await page.getByPlaceholder(/Indicator/i).fill(INDICATOR);
  const autoBlock = page.getByLabel(/Auto-block on match/i);
  if (!(await autoBlock.isChecked())) await autoBlock.check();

  const [createResp] = await Promise.all([
    page.waitForResponse((r) => r.url().includes("/api/module2/threat-intel/") && r.request().method() === "POST", { timeout: 60000 }),
    page.getByRole("button", { name: /^Save$/i }).click(),
  ]);
  assert(createResp.ok(), `Add Entry POST -> 2xx (${createResp.status()})`);
  await page.getByText(INDICATOR, { exact: false }).first().waitFor({ state: "visible", timeout: 30000 });
  report.steps.push("add-entry");

  const [syncResp] = await Promise.all([
    page.waitForResponse((r) => r.url().includes("/api/module2/threat-intel/sync/") && r.request().method() === "POST", { timeout: 60000 }),
    page.getByRole("button", { name: /Sync to Gateway/i }).click(),
  ]);
  assert(syncResp.ok(), `Sync to Gateway -> 2xx (${syncResp.status()})`);
  const syncBody = await syncResp.json().catch(() => ({}));
  report.notes.push(`sync redis_key=${syncBody.redis_key}`);

  await page.getByText(/Gateway sync queued/i).waitFor({ state: "visible", timeout: 30000 });
  await page.locator("code").filter({ hasText: /firewall:threat_intel:/i }).first().waitFor({ state: "visible", timeout: 30000 });
  assert(Boolean(syncBody.redis_key), "sync response includes redis_key");
  report.steps.push("sync-ui");

  inlineRedisSync();
  await page.waitForTimeout(6000);
  report.steps.push("redis-sync");
  await shot(page, "01-ioc-synced");
}

async function runLiveAttackSimulator(page, gatewayKey) {
  CURRENT_PHASE = "m1-attack-simulator";
  await page.goto(`${BASE}/?tab=firewall-1-1`, { waitUntil: "domcontentloaded", timeout: 120000 });
  const attackPanel = page.locator("div.ai-mesh-card").filter({ has: page.getByRole("heading", { name: /Attack Simulator/i }) }).first();
  await attackPanel.waitFor({ state: "visible", timeout: 60000 });
  await page.waitForTimeout(1000);
  await page.locator('[aria-label="Gateway API key"]').fill(gatewayKey);

  const prompt = `Please process this user text: ${INDICATOR}`;
  await page.getByLabel("Test prompt").fill(prompt);

  const [resp] = await Promise.all([
    page.waitForResponse((r) => r.url().includes("/v1/chat/completions") && r.request().method() === "POST", { timeout: 120000 }),
    attackPanel.getByRole("button", { name: /Run Pipeline/i }).click(),
  ]);
  report.notes.push(`attack simulator HTTP ${resp.status()}`);
  assert(resp.status() >= 400 && resp.status() < 600, `live gateway call completed (HTTP ${resp.status()})`);
  const body = await resp.json().catch(() => ({}));
  const blocked =
    (resp.status() >= 400 && resp.status() < 500)
    || String(body?.code || "").toLowerCase() === "threat_intel_blocked";
  assert(blocked, `IOC-bearing prompt blocked (HTTP ${resp.status()} code=${body?.code || ""})`);
  report.steps.push("live-attack");
  await shot(page, "02-attack-blocked");
}

async function assertIocMatchesIncremented(page, baselineMatches) {
  CURRENT_PHASE = "m25-telemetry-sync";
  drainTelemetry();

  let latest = baselineMatches;
  for (let i = 0; i < 12; i++) {
    await page.waitForTimeout(2000);
    const { status, summary } = await fetchTelemetrySummary(page, "24h");
    assert(status === 200, `telemetry refetch -> 200 (got ${status})`);
    latest = summary.threat_intel_matches ?? 0;
    if (latest > baselineMatches) break;
  }

  report.notes.push(`IOC Matches before=${baselineMatches} after=${latest}`);
  assert(latest >= baselineMatches + 1, `IOC Matches KPI incremented (delta=${latest - baselineMatches})`);

  const [telemResp] = await Promise.all([
    page.waitForResponse(
      (r) => r.url().includes("/api/module2/threat-intel/telemetry/") && r.request().method() === "GET" && r.ok(),
      { timeout: 90000 },
    ),
    page.goto(`${BASE}/threat-intel?period=24h`, { waitUntil: "domcontentloaded", timeout: 120000 }),
  ]);
  await page.getByRole("heading", { name: /Threat Intelligence Ops/i }).waitFor({ state: "visible", timeout: 60000 });
  await page.getByText(/IOC Matches/i).first().waitFor({ state: "visible", timeout: 30000 });

  const telem = await telemResp.json().catch(() => ({}));
  const uiMatches = telem?.summary?.threat_intel_matches ?? latest;
  assert(uiMatches >= baselineMatches + 1, `threat-intel page shows incremented IOC Matches (${uiMatches})`);
  report.steps.push("telemetry-sync");
  await shot(page, "03-ioc-matches");
}

async function main() {
  fs.mkdirSync("runs", { recursive: true });
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1100 } });
  await installGatewayProxy(context);
  const page = await context.newPage();
  page.on("pageerror", (e) => report.pageErrors.push({ phase: CURRENT_PHASE, message: String(e.message || e) }));

  try {
    await login(page);
    CURRENT_PHASE = "baseline";
    const baseline = await fetchTelemetrySummary(page, "24h");
    assert(baseline.status === 200, `baseline telemetry -> 200 (got ${baseline.status})`);
    const baseMatches = baseline.summary.threat_intel_matches ?? 0;
    report.notes.push(`baseline IOC Matches=${baseMatches}`);

    await addIocAndSync(page);
    const gatewayKey = await provisionGatewayKey(page);
    report.notes.push(`gateway key prefix=${String(gatewayKey).slice(0, 8)}`);
    await runLiveAttackSimulator(page, gatewayKey);
    await assertIocMatchesIncremented(page, baseMatches);
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
  console.log(JSON.stringify({ ok: report.ok, passed, failed, steps: report.steps, indicator: INDICATOR, error: report.error }, null, 2));
  process.exit(report.ok ? 0 : 1);
}

main();
