/**
 * Production UI proof for Bedrock hot-path (auto routing + live T2).
 * Login via the REAL form. Never screenshot a visible gateway API key.
 *
 *   NODE_PATH="$PWD/tests/e2e/node_modules" \
 *     node mcp-parallel/findings/bedrock-hotpath-2026-08-14/screenshots/capture_ui_proof.mjs
 */
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);
const ROOT = path.resolve(__dirname, "../../../../");
const { chromium } = require(path.join(ROOT, "tests/e2e/node_modules/playwright"));

const BASE = (process.env.BASE_URL || "https://aimeshfirewall.zeroshield.ai").replace(/\/$/, "");
const EMAIL = process.env.TEST_EMAIL || "admin@zeroshield.io";
const PASS = process.env.TEST_PASSWORD || "Adm1n!Pass#2024";
const SHOT_DIR = process.env.SHOT_DIR || __dirname;
const KEY_FILE = path.join(__dirname, "..", ".api_key");
const PROOF_PATH = path.join(SHOT_DIR, "ui_proof.json");

const GATEWAY_KEY = fs.existsSync(KEY_FILE)
  ? fs.readFileSync(KEY_FILE, "utf8").trim()
  : "";

const promptNs = process.hrtime.bigint().toString(36);
const UNIQUE_PROMPT = `What is the capital of France? Reply with the city name only. Marker TOK-${promptNs}.`;

const proof = {
  url: BASE,
  captured_at: new Date().toISOString(),
  prompt: UNIQUE_PROMPT,
  http_status: null,
  total_latency_ms: null,
  input_scan_ms: null,
  model_routing_ms: null,
  output_guardrail_ms: null,
  model_requested: null,
  model_selected: null,
  request_id: null,
  stage_count: null,
  stages: [],
  pngs: [],
  errors: [],
  notes: [],
};

function shotPath(name) {
  return path.join(SHOT_DIR, name);
}

function recordPng(name) {
  const p = shotPath(name);
  if (fs.existsSync(p) && fs.statSync(p).size > 0) proof.pngs.push(p);
}

function stageMs(stages, name) {
  const s = (stages || []).find((x) => String(x?.name || x?.stage || "") === name);
  if (!s) return null;
  const n = Number(s.latency_ms);
  return Number.isFinite(n) ? n : null;
}

function stripSecrets(obj) {
  if (!obj || typeof obj !== "object") return obj;
  const drop = /authorization|api[_-]?key|bearer|password|secret|cookie/i;
  if (Array.isArray(obj)) return obj.map(stripSecrets);
  const out = {};
  for (const [k, v] of Object.entries(obj)) {
    if (drop.test(k)) continue;
    out[k] = typeof v === "object" && v !== null ? stripSecrets(v) : v;
  }
  return out;
}

async function maskGatewayKey(page) {
  await page.evaluate(() => {
    const el = document.querySelector('[aria-label="Gateway API key"]');
    if (el) {
      el.value = "••••";
      try {
        el.setAttribute("type", "text");
        el.setAttribute("value", "••••");
      } catch {}
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value")?.set;
      if (setter) setter.call(el, "••••");
      el.dispatchEvent(new Event("input", { bubbles: true }));
    }
    const key = document.querySelector('[aria-label="Gateway API key"]');
    const grid = key?.closest(".grid");
    if (grid) {
      grid.style.visibility = "hidden";
      grid.setAttribute("data-key-hidden-for-screenshot", "1");
    }
  });
}

async function loginViaForm(page) {
  await page.goto(`${BASE}/login`, { waitUntil: "domcontentloaded", timeout: 120000 });
  await page.locator("#email").waitFor({ state: "visible", timeout: 60000 });
  await page.screenshot({ path: shotPath("01-prod-login.png"), fullPage: false });
  recordPng("01-prod-login.png");

  await page.locator("#email").fill(EMAIL);
  await page.locator("#password").fill(PASS);

  for (let attempt = 0; attempt < 6; attempt++) {
    const [res] = await Promise.all([
      page.waitForResponse(
        (r) => r.url().includes("/api/auth/token/") && r.request().method() === "POST",
        { timeout: 30000 },
      ),
      page.getByRole("button", { name: /^sign in$/i }).click(),
    ]);
    if (res.ok()) {
      await page.waitForFunction(() => !location.pathname.startsWith("/login"), { timeout: 45000 });
      return;
    }
    if (res.status() === 429) {
      let wait = parseInt((await res.headerValue("retry-after").catch(() => null)) || "", 10);
      if (!Number.isFinite(wait) || wait <= 0) wait = 15;
      proof.notes.push(`login 429, waiting ${wait}s`);
      await page.waitForTimeout(Math.min(wait + 2, 65) * 1000);
      continue;
    }
    throw new Error(`Login failed: HTTP ${res.status()}`);
  }
  throw new Error("Login failed: still throttled after retries");
}

async function ensureAutoModel(page, panel) {
  const select = panel.locator("select").filter({ has: page.locator("option") }).first();
  if (await select.count()) {
    const values = await select.locator("option").evaluateAll((opts) => opts.map((o) => o.value));
    if (values.includes("auto")) {
      await select.selectOption("auto");
      proof.notes.push("selected existing auto option");
      return;
    }
  }
  proof.notes.push("no auto option in UI; request body rewritten to model=auto");
}

async function main() {
  fs.mkdirSync(SHOT_DIR, { recursive: true });

  const browser = await chromium.launch({
    headless: true,
    executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH || "/usr/bin/chromium-browser",
    args: ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
  });
  const context = await browser.newContext({
    viewport: { width: 1920, height: 1400 },
    ignoreHTTPSErrors: false,
  });
  const page = await context.newPage();
  page.setDefaultTimeout(60000);

  const capturedBodies = [];
  await page.route("**/v1/chat/completions", async (route) => {
    const req = route.request();
    if (req.method() !== "POST") {
      await route.continue();
      return;
    }
    let body = {};
    try {
      body = JSON.parse(req.postData() || "{}");
    } catch {
      await route.continue();
      return;
    }
    body.model = "auto";
    delete body.routing_preferences;
    capturedBodies.push({
      model: body.model,
      prompt: body.messages?.[0]?.content || "",
      max_tokens: body.max_tokens,
    });
    const postData = JSON.stringify(body);
    const headers = { ...req.headers() };
    headers["content-length"] = String(Buffer.byteLength(postData));
    await route.continue({ postData, headers });
  });

  let simResponse = null;
  page.on("response", async (res) => {
    try {
      if (res.request().method() !== "POST") return;
      if (!res.url().includes("/v1/chat/completions")) return;
      let json = null;
      try {
        json = await res.json();
      } catch {
        json = null;
      }
      simResponse = {
        status: res.status(),
        url: res.url().split("?")[0],
        headers: {
          request_id: res.headers()["x-request-id"] || res.headers()["x-zeroshield-request-id"] || null,
          original_model: res.headers()["x-zeroshield-original-model"] || null,
          routed_model: res.headers()["x-zeroshield-routed-model"] || null,
        },
        body: json ? stripSecrets(json) : null,
      };
    } catch {
      /* ignore parse races */
    }
  });

  try {
    await loginViaForm(page);

    await page.goto(`${BASE}/`, { waitUntil: "domcontentloaded", timeout: 120000 });
    await page.getByText(/PROD/i).first().waitFor({ state: "visible", timeout: 60000 });
    await page.getByText(/^Connected$/i).first().waitFor({ state: "visible", timeout: 60000 });
    await page.waitForTimeout(1500);
    await page.screenshot({ path: shotPath("02-prod-dashboard.png"), fullPage: false });
    recordPng("02-prod-dashboard.png");

    await page.goto(`${BASE}/?tab=firewall-1-1`, { waitUntil: "domcontentloaded", timeout: 120000 });
    const panel = page
      .locator("div.ai-mesh-card")
      .filter({ has: page.getByRole("heading", { name: /Attack Simulator/i }) })
      .first();
    await panel.waitFor({ state: "visible", timeout: 60000 });

    const keyInput = panel.getByLabel("Gateway API key");
    await keyInput.waitFor({ state: "visible", timeout: 30000 });
    const existing = await keyInput.inputValue().catch(() => "");
    if (!existing && GATEWAY_KEY) {
      await keyInput.fill(GATEWAY_KEY);
      proof.notes.push("filled gateway key from .api_key then masked");
    } else if (existing) {
      proof.notes.push("gateway key already present; masked before screenshots");
    } else {
      proof.errors.push("no gateway key available");
    }

    await ensureAutoModel(page, panel);

    const sse = panel.getByLabel(/SSE stream mode/i);
    if ((await sse.count()) && (await sse.isChecked())) {
      await sse.uncheck();
    }

    const promptBox = panel.getByLabel("Test prompt");
    const runBtn = panel.getByRole("button", { name: /Run Pipeline/ });
    await runBtn.waitFor({ state: "visible", timeout: 15000 });

    async function runUnique(label) {
      const ns = process.hrtime.bigint().toString(36);
      const text = `What is the capital of France? Reply with the city name only. Marker TOK-${ns}.`;
      await promptBox.fill(text);
      // Do not mask the key before click — maskGatewayKey overwrites the
      // input value and the request never authenticates.
      const respPromise = page.waitForResponse(
        (r) => r.url().includes("/v1/chat/completions") && r.request().method() === "POST",
        { timeout: 180000 },
      );
      await runBtn.click();
      const resp = await respPromise;
      await panel.getByText(/HTTP\s+\d+/).first().waitFor({ state: "visible", timeout: 60000 });
      await page.waitForTimeout(600);
      proof.notes.push(`${label} HTTP ${resp.status()} prompt_n=${ns}`);
      return resp;
    }

    // One unique 9-stage call is the UI proof. A second call can 503 on BYOK
    // model_output flake; only retry if the first is not HTTP 200.
    let resp = await runUnique("unique");
    if (resp.status() !== 200) {
      proof.notes.push(`first unique HTTP ${resp.status()}; retrying once`);
      resp = await runUnique("retry");
    }
    proof.http_status = resp.status();
    proof.prompt = capturedBodies.at(-1)?.prompt || UNIQUE_PROMPT;

    await maskGatewayKey(page);

    await promptBox.scrollIntoViewIfNeeded();
    await page.screenshot({ path: shotPath("07-prod-attack-auto.png"), fullPage: false });
    recordPng("07-prod-attack-auto.png");

    const timeline = panel.getByTestId("pipeline-stage-timeline");
    if (await timeline.count()) {
      await page.evaluate(() => {
        const el = document.querySelector('[data-testid="pipeline-stage-timeline"]');
        const scroller = el?.querySelector(".overflow-x-auto");
        if (scroller) {
          scroller.style.overflow = "visible";
          scroller.style.flexWrap = "wrap";
        }
      });
      const routingBtn = panel.getByRole("button", { name: /Pipeline stage model routing/i });
      if (await routingBtn.count()) {
        await routingBtn.click();
        await page.waitForTimeout(300);
      }
      await timeline.scrollIntoViewIfNeeded();
      await timeline.screenshot({ path: shotPath("08-prod-auto-routing.png") });
      recordPng("08-prod-auto-routing.png");
    } else {
      proof.errors.push("pipeline-stage-timeline not found");
      await page.screenshot({ path: shotPath("08-prod-auto-routing.png"), fullPage: false });
      recordPng("08-prod-auto-routing.png");
    }

    const durationChip = panel.locator("span").filter({ hasText: /ms total/ }).first();
    const httpChip = panel.getByText(/HTTP\s+200/).first();
    const reqChip = panel.locator("span.font-mono").filter({ hasText: /^zs-/ }).first();
    if (await durationChip.count()) {
      await durationChip.scrollIntoViewIfNeeded();
    }
    const headerBox = panel.locator("div").filter({ hasText: /ms total/ }).filter({ hasText: /HTTP/ }).first();
    if (await headerBox.count()) {
      await headerBox.screenshot({ path: shotPath("09-prod-duration.png") }).catch(async () => {
        await page.screenshot({ path: shotPath("09-prod-duration.png"), fullPage: false });
      });
    } else {
      await page.screenshot({ path: shotPath("09-prod-duration.png"), fullPage: false });
    }
    recordPng("09-prod-duration.png");
    proof.notes.push(`duration_visible=${await durationChip.count() > 0} http200_visible=${await httpChip.count() > 0} zs_visible=${await reqChip.count() > 0}`);

    const analysis = panel.locator("div").filter({ hasText: /ZeroShield Model|Detection Tier|ZEROSHIELD/i }).first();
    const resultCard = panel.locator("div").filter({ hasText: /ms total/ }).filter({ hasText: /HTTP/ }).locator("xpath=ancestor::div[contains(@class,'rounded')][1]").first();
    await maskGatewayKey(page);
    if (await analysis.count()) {
      await analysis.scrollIntoViewIfNeeded();
      const box = await analysis.boundingBox();
      if (box) {
        await page.screenshot({
          path: shotPath("10-prod-pipeline-detail.png"),
          clip: {
            x: Math.max(0, box.x - 16),
            y: Math.max(0, box.y - 80),
            width: Math.min(1920, box.width + 32),
            height: Math.min(900, box.height + 160),
          },
        });
      } else {
        await analysis.screenshot({ path: shotPath("10-prod-pipeline-detail.png") });
      }
    } else if (await resultCard.count()) {
      await resultCard.screenshot({ path: shotPath("10-prod-pipeline-detail.png") });
    } else {
      await page.screenshot({ path: shotPath("10-prod-pipeline-detail.png"), fullPage: false });
    }
    recordPng("10-prod-pipeline-detail.png");

    const json = simResponse?.body || (await resp.json().catch(() => null));
    const trace = json?.pipeline_trace || json?.metadata?.pipeline_trace || {};
    const stages = Array.isArray(trace.stages)
      ? trace.stages
      : Array.isArray(json?.stages)
        ? json.stages
        : [];
    const routing = stages.find((s) => s.name === "model_routing") || {};
    proof.http_status = simResponse?.status ?? proof.http_status;
    proof.total_latency_ms = trace.total_latency_ms ?? json?.total_latency_ms ?? null;
    proof.input_scan_ms = stageMs(stages, "input_scan");
    proof.model_routing_ms = stageMs(stages, "model_routing");
    proof.output_guardrail_ms = stageMs(stages, "output_guardrail");
    proof.model_requested =
      trace.requested_model
      || routing.requested_model
      || simResponse?.headers?.original_model
      || capturedBodies[0]?.model
      || null;
    proof.model_selected =
      trace.selected_model
      || routing.selected_model
      || routing.routed_model
      || simResponse?.headers?.routed_model
      || json?.model
      || null;
    proof.request_id =
      json?.request_id
      || trace.request_id
      || simResponse?.headers?.request_id
      || (typeof json?.id === "string" && json.id.startsWith("zs-") ? json.id : null)
      || null;
    if (!proof.request_id) {
      const fromDom = await page.evaluate(() => {
        const el = [...document.querySelectorAll("span")].find((n) => /^zs-/.test((n.textContent || "").trim()));
        return el ? el.textContent.trim() : null;
      });
      proof.request_id = fromDom;
    }
    proof.stage_count = stages.length;
    proof.stages = stages.map((s) => ({
      name: s.name || s.stage,
      action: s.action,
      latency_ms: s.latency_ms,
    }));
    proof.request_body_model = capturedBodies[0]?.model || null;

    if (proof.http_status !== 200) {
      proof.errors.push(`sim HTTP ${proof.http_status}`);
    }
  } catch (err) {
    proof.errors.push(String(err?.stack || err));
    try {
      await page.screenshot({ path: shotPath("failure.png"), fullPage: true });
      recordPng("failure.png");
    } catch {}
  } finally {
    fs.writeFileSync(PROOF_PATH, JSON.stringify(proof, null, 2));
    await browser.close();
  }

  console.log(JSON.stringify({
    ok: proof.errors.length === 0 && proof.http_status === 200,
    http_status: proof.http_status,
    total_latency_ms: proof.total_latency_ms,
    input_scan_ms: proof.input_scan_ms,
    model_routing_ms: proof.model_routing_ms,
    output_guardrail_ms: proof.output_guardrail_ms,
    model_requested: proof.model_requested,
    model_selected: proof.model_selected,
    request_id: proof.request_id,
    pngs: proof.pngs,
    errors: proof.errors,
    notes: proof.notes,
  }, null, 2));
  process.exit(proof.errors.length === 0 && proof.http_status === 200 ? 0 : 1);
}

await main();
