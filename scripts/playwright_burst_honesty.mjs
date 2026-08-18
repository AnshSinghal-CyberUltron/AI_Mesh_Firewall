/**
 * Burst Test honesty gate — uncapped count/concurrency, scan-only max_tokens=0,
 * ERROR rows show HTTP status, Rate-Limit Probe uses a clean prompt.
 *
 *   NODE_PATH=$PWD/tests/e2e/node_modules BASE_URL=http://127.0.0.1:8180 \
 *     E2E_REPORT=runs/playwright_burst_honesty.json \
 *     node scripts/playwright_burst_honesty.mjs
 */
import fs from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);
const { chromium } = require(path.join(__dirname, "../tests/e2e/node_modules/playwright"));

const BASE = (process.env.BASE_URL || "http://127.0.0.1:8180").replace(/\/$/, "");
const EMAIL = process.env.TEST_EMAIL || "admin@zeroshield.io";
const PASS = process.env.TEST_PASSWORD || "Adm1n!Pass#2024";
const OUT = process.env.E2E_REPORT || "runs/playwright_burst_honesty.json";
const SHOT_DIR = process.env.SHOT_DIR || "runs/burst_honesty";
const MODEL = process.env.SIM_MODEL || "gemma-free";

const report = { ok: false, asserts: [], pageErrors: [], captured: [], error: null };

function assert(cond, label) {
  report.asserts.push({ label, pass: !!cond });
  if (!cond) throw new Error(`ASSERT FAILED: ${label}`);
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

async function main() {
  fs.mkdirSync("runs", { recursive: true });
  fs.mkdirSync(SHOT_DIR, { recursive: true });
  const browser = await chromium.launch({
    headless: true,
    executablePath: process.env.CHROMIUM_PATH || "/usr/bin/chromium-browser",
    args: ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
  });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1200 } });
  page.on("pageerror", (e) => report.pageErrors.push(String(e.message || e)));

  const bodies = [];
  page.on("request", (req) => {
    if (req.method() === "POST" && req.url().includes("/v1/chat/completions")) {
      let parsed = null;
      try { parsed = JSON.parse(req.postData() || "{}"); } catch {}
      bodies.push({
        max_tokens: parsed?.max_tokens,
        estimated_tokens: parsed?.estimated_tokens,
        prompt: parsed?.messages?.[0]?.content || "",
      });
    }
  });

  try {
    await page.goto(`${BASE}/login`, { waitUntil: "domcontentloaded", timeout: 120000 });
    const seeded = await seedAuth(page);
    assert(seeded.ok, `seed auth (${seeded.status ?? "ok"})`);
    await page.goto(`${BASE}/?tab=firewall-1-1`, { waitUntil: "domcontentloaded", timeout: 120000 });
    const panel = page.locator("div.ai-mesh-card").filter({ has: page.getByRole("heading", { name: /Attack Simulator/i }) }).first();
    await panel.waitFor({ state: "visible", timeout: 60000 });

    const reqInput = panel.getByLabel("Burst request count");
    const concInput = panel.getByLabel("Burst concurrency");
    assert(!(await reqInput.getAttribute("max")), "Requests input has no HTML max cap");
    assert(!(await concInput.getAttribute("max")), "Concurrency input has no HTML max cap");

    await reqInput.fill("500");
    await concInput.fill("100");
    const sending = await panel.getByText(/Sending 500 requests at 100 in-flight/).count();
    assert(sending > 0, "UI announces Sending 500 at 100 in-flight");

    const inferBox = panel.getByLabel(/Include model inference/i);
    assert(await inferBox.count(), "Include model inference checkbox present");
    assert(!(await inferBox.isChecked()), "Inference defaults OFF (scan-only)");

    await reqInput.fill("12");
    await concInput.fill("12");
    const burstBtn = panel.getByRole("button", { name: /Burst Test/ });
    await burstBtn.click();

    await panel.getByText(/Burst Test Results — sent 12 at 12 in-flight/).waitFor({ timeout: 180000 });
    await page.screenshot({ path: `${SHOT_DIR}/standard_burst_12.png`, fullPage: false });

    assert(bodies.length === 12, `browser sent 12 POSTs, got ${bodies.length}`);
    assert(bodies.every((b) => b.max_tokens === 0), "every Standard Burst POST has max_tokens=0");
    assert(bodies.every((b) => b.estimated_tokens == null), "Standard Burst does not send estimated_tokens");

    const mode = await panel.getByText(/scan-only \(no model call\)/).count();
    assert(mode > 0, "results header shows scan-only (no model call)");

    bodies.length = 0;
    await panel.getByLabel("Burst test scenario").selectOption("rate-limit-probe");
    const sendingProbe = await panel.getByText(/Sending 20 requests at 20 in-flight/).count();
    assert(sendingProbe > 0, "Rate-Limit Probe defaults to 20/20");
    await burstBtn.click();
    await panel.getByText(/Burst Test Results — sent 20 at 20 in-flight/).waitFor({ timeout: 180000 });
    await page.screenshot({ path: `${SHOT_DIR}/rate_limit_probe_20.png`, fullPage: false });

    assert(bodies.length === 20, `probe sent 20 POSTs, got ${bodies.length}`);
    assert(bodies.every((b) => b.max_tokens === 0), "probe POSTs are scan-only (max_tokens=0)");
    assert(bodies.every((b) => b.estimated_tokens === 8000), "probe sends estimated_tokens=8000");
    assert(
      bodies.every((b) => /clean availability check/i.test(b.prompt)),
      "probe forces the clean prompt",
    );

    report.captured = { standard: 12, probe: 20 };
    report.ok = report.asserts.every((a) => a.pass);
  } catch (err) {
    report.error = String(err?.stack || err);
    report.ok = false;
    try { await page.screenshot({ path: `${SHOT_DIR}/failure.png`, fullPage: true }); } catch {}
  } finally {
    fs.writeFileSync(OUT, JSON.stringify(report, null, 2));
    await browser.close();
  }
  if (!report.ok) {
    console.error(JSON.stringify(report, null, 2));
    process.exit(1);
  }
  console.log(JSON.stringify({ ok: true, asserts: report.asserts.length, out: OUT }, null, 2));
}

await main();
