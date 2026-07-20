/**
 * OpenAI SDK frontend gate — chat completions through the live Vite proxy.
 *
 * Phases (E2E_PHASE env, default "all"):
 *   chat    — clean AttackSimulator scenario → HTTP 200 + ALLOWED
 *   stream  — stream toggle → SSE + ALLOWED
 *   errors  — injection → 4xx + BLOCKED (not ERROR)
 *   howto   — HowToUse cURL snippet targets /v1/chat/completions
 *
 * Run from repo root:
 *   NODE_PATH=$PWD/tests/e2e/node_modules BASE_URL=http://127.0.0.1:8180 \
 *     E2E_REPORT=runs/playwright_openai_sdk.json node scripts/playwright_openai_sdk.mjs
 */
import { chromium } from "playwright";
import fs from "node:fs";

const BASE = (process.env.BASE_URL || "http://127.0.0.1:8180").replace(/\/$/, "");
const EMAIL = process.env.TEST_EMAIL || "admin@zeroshield.io";
const PASS = process.env.TEST_PASSWORD || "Adm1n!Pass#2024";
const OUT = process.env.E2E_REPORT || "runs/playwright_openai_sdk.json";
const SHOT_DIR = process.env.SHOT_DIR || "runs/openai_sdk";
const MODEL = process.env.SIM_MODEL || "gemma-free";
const PHASE = (process.env.E2E_PHASE || "all").toLowerCase();

const report = { base: BASE, phase: PHASE, ok: false, steps: [], asserts: [], pageErrors: [], notes: [], error: null };
let CURRENT_PHASE = "init";

function want(...names) {
  if (PHASE === "all") return true;
  return names.includes(PHASE);
}

function assert(cond, label) {
  report.asserts.push({ label, pass: !!cond, phase: CURRENT_PHASE });
  if (!cond) throw new Error(`ASSERT FAILED [${CURRENT_PHASE}]: ${label}`);
}

async function shot(page, name) {
  try {
    fs.mkdirSync(SHOT_DIR, { recursive: true });
    await page.screenshot({ path: `${SHOT_DIR}/${name}.png`, fullPage: false });
  } catch {}
}

async function seedAuth(page) {
  return page.evaluate(async ({ email, pass, model }) => {
    const r = await fetch("/api/auth/token/", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password: pass }),
    });
    if (!r.ok) return { ok: false, status: r.status };
    const j = await r.json();
    localStorage.setItem("auth_access", j.access);
    localStorage.setItem("auth_refresh", j.refresh);
    try {
      const k = await fetch("/api/gateways/simulator-default/", {
        method: "POST",
        headers: { Authorization: `Bearer ${j.access}` },
      });
      const kj = await k.json().catch(() => ({}));
      if (kj.storage_key && kj.key) localStorage.setItem(kj.storage_key, kj.key);
    } catch {}
    localStorage.setItem("zeroshield_simulator_model", model);
    return { ok: true };
  }, { email: EMAIL, pass: PASS, model: MODEL });
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

async function bootAuthed(page) {
  CURRENT_PHASE = "login";
  await page.goto(`${BASE}/login`, { waitUntil: "domcontentloaded", timeout: 120000 });
  await waitForControlPlane(page);
  const seeded = await seedAuth(page);
  assert(seeded.ok, `seed admin auth (token mint 2xx, got ${seeded.status ?? "ok"})`);
  report.steps.push("login");
}

async function gotoGatewayTab(page) {
  await page.goto(`${BASE}/?tab=firewall-1-1`, { waitUntil: "domcontentloaded", timeout: 120000 });
  await page.locator("text=/Attack Simulator/i").first().waitFor({ state: "visible", timeout: 60000 });
  await page.waitForResponse((r) => r.url().includes("/api/firewall/models/"), { timeout: 30000 }).catch(() => {});
  await page.waitForTimeout(500);
}

async function runScenario(page, panel, scenarioText, { stream = false } = {}) {
  const reset = panel.getByRole("button", { name: /^Reset$/ });
  if (await reset.count()) {
    await reset.first().click();
    await page.waitForTimeout(200);
  }
  if (stream) {
    const streamBox = panel.getByText(/Stream/i).locator("..").locator('input[type="checkbox"]').first();
    if (await streamBox.count()) {
      const checked = await streamBox.isChecked();
      if (!checked) await streamBox.check();
    }
  }
  await panel.getByRole("button", { name: scenarioText }).first().click();
  await page.waitForTimeout(400);
  const runBtn = panel.getByRole("button", { name: /Run Pipeline/ }).first();
  await runBtn.waitFor({ state: "visible", timeout: 60000 });
  await page.waitForFunction(
    (el) => el && !el.disabled,
    await runBtn.elementHandle(),
    { timeout: 60000 },
  ).catch(() => {});
  let resp = null;
  for (let attempt = 0; attempt < 6 && !resp; attempt++) {
    const respP = page
      .waitForResponse(
        (r) => r.url().includes("/v1/chat/completions") && r.request().method() === "POST",
        { timeout: 120000 },
      )
      .catch(() => null);
    await runBtn.click();
    const noop = await page
      .getByText(/Select a connected model/i)
      .first()
      .waitFor({ state: "visible", timeout: 4000 })
      .then(() => true)
      .catch(() => false);
    if (noop) {
      await page.waitForTimeout(1500);
      continue;
    }
    resp = await respP;
  }
  if (!resp) throw new Error("AttackSimulator never fired /v1/chat/completions (model not selected)");
  await page.waitForTimeout(1500);
  return resp;
}

async function main() {
  fs.mkdirSync("runs", { recursive: true });
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 1200 },
    permissions: ["clipboard-read", "clipboard-write"],
  });
  const page = await context.newPage();
  page.on("pageerror", (e) => report.pageErrors.push({ phase: CURRENT_PHASE, message: String(e.message || e) }));
  page.on("dialog", (d) => d.accept());

  try {
    await bootAuthed(page);
    await gotoGatewayTab(page);
    const attackPanel = page
      .locator("div.ai-mesh-card")
      .filter({ has: page.getByRole("heading", { name: /Attack Simulator/i }) })
      .first();

    if (want("chat", "stream", "errors", "all")) {
      await attackPanel.scrollIntoViewIfNeeded();
      await attackPanel.waitFor({ state: "visible", timeout: 60000 });
      await page.waitForResponse((r) => r.url().includes("/api/firewall/models/"), { timeout: 60000 }).catch(() => {});
      await page.waitForTimeout(800);
    }

    if (want("howto", "all")) {
      CURRENT_PHASE = "howto";
      const howToHeader = page.getByRole("button", { name: /Route Your AI Requests Through the Gateway/i }).first();
      if (await howToHeader.count()) {
        await howToHeader.click();
        await page.getByRole("button", { name: /^cURL$/ }).first().click();
        await page.waitForTimeout(200);
        const copyBtn = page.locator('button[title="Copy to clipboard"]').first();
        if (await copyBtn.count()) {
          await copyBtn.click();
          await page.waitForTimeout(300);
          const clip = await page.evaluate(() => navigator.clipboard.readText().catch(() => ""));
          assert(clip.includes("/v1/chat/completions"), "HowTo cURL targets /v1/chat/completions");
          report.steps.push("howto");
        }
      }
    }

    if (want("chat", "all")) {
      CURRENT_PHASE = "chat";
      const resp = await runScenario(page, attackPanel, /Clean Prompt \(Safe\)/);
      const status = resp.status();
      report.notes.push(`clean chat HTTP ${status}`);
      assert(status === 200, `clean chat allowed (HTTP ${status})`);
      await attackPanel.getByText(/^ALLOWED$/).first().waitFor({ state: "visible", timeout: 90000 });
      assert(/Paris|France|capital/i.test(await attackPanel.innerText()), "clean ALLOW surfaces model answer");
      report.steps.push("chat");
      await shot(page, "chat-allowed");
    }

    if (want("stream", "all")) {
      CURRENT_PHASE = "stream";
      const resp = await runScenario(page, attackPanel, /Clean Prompt \(Safe\)/, { stream: true });
      const status = resp.status();
      const ctype = resp.headers()["content-type"] || "";
      report.notes.push(`stream HTTP ${status} ctype=${ctype}`);
      assert(status === 200, `stream chat allowed (HTTP ${status})`);
      assert(/event-stream/i.test(ctype), `stream response is SSE (${ctype})`);
      await attackPanel.getByText(/^ALLOWED$/).first().waitFor({ state: "visible", timeout: 90000 });
      report.steps.push("stream");
      await shot(page, "stream-allowed");
    }

    if (want("errors", "all")) {
      CURRENT_PHASE = "errors";
      const resp = await runScenario(page, attackPanel, /Prompt Injection/);
      const status = resp.status();
      report.notes.push(`injection HTTP ${status}`);
      assert(status >= 400 && status < 500, `injection blocked (HTTP ${status} 4xx)`);
      await attackPanel.getByText(/^BLOCKED$/).first().waitFor({ state: "visible", timeout: 15000 });
      assert((await attackPanel.getByText(/^ERROR$/).count()) === 0, "injection banner reads BLOCKED, never ERROR");
      report.steps.push("errors");
      await shot(page, "injection-blocked");
    }

    assert(report.pageErrors.length === 0, `no page JS errors (${report.pageErrors.length})`);
    report.ok = true;
  } catch (e) {
    report.error = String(e.message || e);
    report.ok = false;
    await shot(page, "failure");
  } finally {
    fs.writeFileSync(OUT, JSON.stringify(report, null, 2));
    await browser.close();
    console.log(JSON.stringify({ ok: report.ok, steps: report.steps, error: report.error }, null, 2));
    process.exit(report.ok ? 0 : 1);
  }
}

main();
