/**
 * T01 OpenRouter inference via baked console Attack Simulator (:8180).
 * Clean prompt only. Never logs secrets.
 */
import { createRequire } from "node:module";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, "../../../..");
const require = createRequire(import.meta.url);
const { chromium } = require(path.join(REPO, "tests/e2e/node_modules/playwright"));

const BASE = (process.env.BASE_URL || "http://127.0.0.1:8180").replace(/\/$/, "");
const EMAIL = process.env.TEST_EMAIL;
const PASS = process.env.TEST_PASSWORD;
const OUT = process.env.E2E_REPORT || path.join(HERE, "l01_openrouter_browser.json");
const SHOTS = process.env.SHOT_DIR || path.join(HERE, "shots");
const PROMPT = "Reply with the single word pong.";

const report = {
  task: "T01",
  gate: "L01-3-openrouter-ui",
  base: BASE,
  email: EMAIL,
  ok: false,
  steps: [],
  asserts: [],
  pageErrors: [],
  console_errors: [],
  chat: null,
  hashed_js: null,
  html_has_vite_client: null,
  error: null,
};

function assert(cond, label) {
  report.asserts.push({ label, pass: !!cond });
  if (!cond) throw new Error(`ASSERT FAILED: ${label}`);
}

async function shot(page, name) {
  fs.mkdirSync(SHOTS, { recursive: true });
  await page.screenshot({ path: path.join(SHOTS, `${name}.png`), fullPage: true });
}

function leak(text) {
  return /sk-or-v1-|sk-or-[a-z0-9]{8,}/i.test(String(text || ""));
}

async function main() {
  if (!EMAIL || !PASS) throw new Error("TEST_EMAIL and TEST_PASSWORD required");
  const executablePath =
    process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH || "/usr/bin/chromium-browser";
  const browser = await chromium.launch({
    headless: true,
    executablePath,
    args: ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
  });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  page.on("pageerror", (e) => report.pageErrors.push(String(e.message || e).slice(0, 400)));
  page.on("console", (msg) => {
    if (msg.type() === "error") report.console_errors.push(msg.text().slice(0, 400));
  });

  try {
    await page.goto(`${BASE}/login`, { waitUntil: "networkidle", timeout: 120000 });
    const html = await page.content();
    report.html_has_vite_client = html.includes("@vite/client") || html.includes("/@vite/");
    report.hashed_js = (html.match(/\/assets\/index-[A-Za-z0-9_-]+\.js/) || [null])[0];
    assert(report.html_has_vite_client === false, "baked console has no Vite client");
    await page.locator("#email").fill(EMAIL);
    await page.locator("#password").fill(PASS);
    let loginRes = null;
    for (let attempt = 0; attempt < 6; attempt++) {
      const [res] = await Promise.all([
        page.waitForResponse(
          (r) => r.url().includes("/api/auth/token/") && r.request().method() === "POST",
          { timeout: 60000 },
        ),
        page.getByRole("button", { name: /^sign in$/i }).click(),
      ]);
      loginRes = res;
      if (res.ok()) break;
      if (res.status() === 429) {
        const ra = parseInt((await res.headerValue("retry-after").catch(() => null)) || "15", 10);
        await page.waitForTimeout(Math.min((Number.isFinite(ra) ? ra : 15) + 2, 65) * 1000);
        continue;
      }
      throw new Error(`Login HTTP ${res.status()}`);
    }
    report.login_status = loginRes.status();
    await page.waitForFunction(() => !location.pathname.startsWith("/login"), { timeout: 60000 });
    report.steps.push("login-ok");

    await page.goto(`${BASE}/?tab=firewall-1-1`, { waitUntil: "domcontentloaded", timeout: 120000 });
    await page.getByRole("heading", { name: /Attack Simulator/i }).waitFor({ state: "visible", timeout: 90000 });
    report.steps.push("attack-sim-visible");
    await shot(page, "09-attack-sim");

    await page.getByText("Simulator model:", { exact: false }).first().waitFor({ state: "visible", timeout: 90000 });
    const modelLabel = await page.getByText("Simulator model:", { exact: false }).first().innerText();
    report.simulator_model_label = modelLabel.slice(0, 120);
    assert(/gpt-4o-mini/i.test(modelLabel), `simulator model shows gpt-4o-mini: ${modelLabel}`);

    const prompt = page.getByLabel("Test prompt");
    await prompt.fill(PROMPT);
    const chatWait = page.waitForResponse(
      (r) => r.url().includes("/v1/chat/completions") && r.request().method() === "POST",
      { timeout: 90000 },
    );
    await page.getByRole("button", { name: /Run Pipeline/i }).click();
    const chatRes = await chatWait;
    let body = null;
    try {
      body = await chatRes.json();
    } catch {}
    if (leak(JSON.stringify(body || {}))) {
      throw new Error("chat response contained OpenRouter key");
    }
    const choices = Array.isArray(body?.choices) ? body.choices : [];
    const content = String(choices[0]?.message?.content || "").slice(0, 200);
    const trace = body?.pipeline_trace || body?.metadata?.pipeline_trace || body?.zeroshield?.pipeline_trace || {};
    const stages = Array.isArray(trace.stages) ? trace.stages : [];
    const modelStage = stages.find((s) => s && s.name === "model_output") || {};
    report.chat = {
      http: chatRes.status(),
      x_request_id: await chatRes.headerValue("x-request-id").catch(() => null),
      has_choices: choices.length > 0,
      assistant_preview: content,
      model_output: modelStage.action || null,
      final_action: trace.final_action || null,
      max_tokens: (() => {
        try {
          return JSON.parse(chatRes.request().postData() || "{}").max_tokens;
        } catch {
          return null;
        }
      })(),
    };
    await shot(page, "10-attack-sim-after-run");
    assert(report.chat.http === 200, `UI chat HTTP ${report.chat.http}`);
    assert(report.chat.has_choices === true, "UI chat has choices");
    assert(report.chat.max_tokens !== 0, `UI chat is not scan-only max_tokens=${report.chat.max_tokens}`);
    assert(report.chat.model_output !== "skip", `model_output ${report.chat.model_output}`);
    report.steps.push("ui-inference-ok");
    report.ok = report.pageErrors.length === 0 && report.html_has_vite_client === false;
  } catch (err) {
    report.error = String(err?.message || err);
    try {
      await shot(page, "99-openrouter-ui-failure");
    } catch {}
  } finally {
    report.finished_at = new Date().toISOString();
    await browser.close();
    fs.writeFileSync(OUT, JSON.stringify(report, null, 2));
    console.log(
      JSON.stringify(
        {
          ok: report.ok,
          error: report.error,
          chat: report.chat,
          hashed_js: report.hashed_js,
          steps: report.steps,
        },
        null,
        2,
      ),
    );
    if (!report.ok) process.exit(1);
  }
}

main();
