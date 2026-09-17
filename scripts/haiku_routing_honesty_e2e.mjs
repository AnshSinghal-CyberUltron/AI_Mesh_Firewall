/**
 * Public Attack Simulator: selecting Haiku must remap, not 404-then-auto.
 *
 *   NODE_PATH=$PWD/tests/e2e/node_modules \
 *   BASE_URL=https://aimeshfirewall.zeroshield.ai \
 *   node scripts/haiku_routing_honesty_e2e.mjs
 */
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);
const { chromium } = require(path.join(__dirname, "../tests/e2e/node_modules/playwright"));

const BASE = (process.env.BASE_URL || "https://aimeshfirewall.zeroshield.ai").replace(/\/$/, "");
const EMAIL = process.env.TEST_EMAIL || "admin@zeroshield.io";
const PASS = process.env.TEST_PASSWORD || "Adm1n!Pass#2024";
const MODEL = process.env.SIM_MODEL || "Haiku";
const SHOT_DIR = process.env.SHOT_DIR || path.join(__dirname, "../runs/haiku-routing-honesty");
const OUT = process.env.E2E_REPORT || path.join(SHOT_DIR, "report.json");
const PREMASKED =
  "Please process this user record: SSN ***-**-6789, email j***@a***.com, " +
  "phone ***-***-5309, credit card ****-****-****-1111.";

const report = {
  ok: false,
  base: BASE,
  started_at: new Date().toISOString(),
  asserts: [],
  pageErrors: [],
  chats: [],
  ui: {},
  error: null,
};

function assert(cond, label) {
  report.asserts.push({ label, pass: !!cond });
  if (!cond) throw new Error(`ASSERT FAILED: ${label}`);
}

function redact(value) {
  const seen = new WeakSet();
  const walk = (node) => {
    if (node == null) return node;
    if (typeof node === "string") {
      return node
        .replace(/zs-[A-Za-z0-9._-]+/g, "zs-[REDACTED]")
        .replace(/\bsk-[A-Za-z0-9._-]+/g, "sk-[REDACTED]")
        .replace(/Bearer\s+[^\s"]+/gi, "Bearer [REDACTED]");
    }
    if (typeof node !== "object") return node;
    if (seen.has(node)) return "[cycle]";
    seen.add(node);
    if (Array.isArray(node)) return node.map(walk);
    const out = {};
    for (const [k, v] of Object.entries(node)) {
      if (/(api[_-]?key|authorization|secret|token|password|cookie)/i.test(k)) out[k] = "[REDACTED]";
      else out[k] = walk(v);
    }
    return out;
  };
  return walk(value);
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
    } catch {
      /* simulator key optional if already stored */
    }
    localStorage.setItem("zeroshield_simulator_model", model);
    return { ok: true };
  }, { email: EMAIL, pass: PASS, model: MODEL });
}

async function main() {
  fs.mkdirSync(SHOT_DIR, { recursive: true });
  const browser = await chromium.launch({
    headless: true,
    executablePath: process.env.CHROMIUM_PATH || "/usr/bin/chromium-browser",
    args: ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
  });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
  page.on("pageerror", (e) => report.pageErrors.push(String(e.message || e)));
  page.on("console", (msg) => {
    const text = String(msg.text() || "");
    if (/\/v1\/chat\/completions/i.test(text) && /404/i.test(text)) {
      report.pageErrors.push(`console ${msg.type()}: ${text.slice(0, 300)}`);
    }
  });

  const chats = [];
  page.on("response", async (res) => {
    if (res.request().method() !== "POST" || !res.url().includes("/v1/chat/completions")) return;
    let requestBody = null;
    try { requestBody = JSON.parse(res.request().postData() || "{}"); } catch { requestBody = null; }
    let responseBody = null;
    try {
      const ct = res.headers()["content-type"] || "";
      if (ct.includes("json")) responseBody = await res.json();
    } catch {
      responseBody = null;
    }
    chats.push({
      url: res.url(),
      status: res.status(),
      requestModel: requestBody?.model,
      preferred: requestBody?.routing_preferences?.preferred_model,
      enable_routing: requestBody?.routing_preferences?.enable_routing,
      response: responseBody,
    });
  });

  try {
    await page.goto(`${BASE}/login`, { waitUntil: "domcontentloaded", timeout: 120000 });
    const seeded = await seedAuth(page);
    assert(seeded.ok, `seed auth (${seeded.status ?? "ok"})`);
    await page.goto(`${BASE}/?tab=firewall-1-1`, { waitUntil: "domcontentloaded", timeout: 120000 });
    const panel = page.locator("div.ai-mesh-card").filter({
      has: page.getByRole("heading", { name: /Attack Simulator/i }),
    }).first();
    await panel.waitFor({ state: "visible", timeout: 60000 });

    const picker = panel.locator("select").first();
    await picker.waitFor({ state: "visible", timeout: 60000 });
    await picker.locator(`option[value="${MODEL}"]`).waitFor({ state: "attached", timeout: 60000 });
    const options = await picker.locator("option").allTextContents();
    report.ui.modelOptions = options.map((t) => t.trim()).filter(Boolean);
    await picker.selectOption(MODEL);
    report.ui.selectedModel = await picker.inputValue().catch(() => "");

    await panel.getByLabel("Test prompt").fill(PREMASKED);
    await panel.getByRole("button", { name: /Run Pipeline/i }).click();
    await panel.getByText(/Live gateway pipeline/i).waitFor({ timeout: 90000 });
    await page.screenshot({ path: path.join(SHOT_DIR, "01-haiku-premasked.png"), fullPage: true });

    const routingChip = panel.getByRole("button", { name: /Pipeline stage model routing/i });
    await routingChip.waitFor({ state: "visible", timeout: 15000 });
    report.ui.routingChipLabel = await routingChip.getAttribute("aria-label");
    await routingChip.click();
    await panel.getByText(/Why this model/i).waitFor({ timeout: 10000 });
    await page.screenshot({ path: path.join(SHOT_DIR, "02-model-routing-card.png"), fullPage: true });

    report.chats = redact(chats);
    const statuses = chats.map((c) => c.status);
    const firstBodies = chats.map((c) => ({
      status: c.status,
      requestModel: c.requestModel,
      preferred: c.preferred,
      original: c.response?.zeroshield?.routing?.original_model
        || c.response?.zeroshield?.original_model,
      selected: c.response?.zeroshield?.routing?.selected_model
        || c.response?.zeroshield?.selected_model,
      routingAction: (c.response?.pipeline_trace?.stages || c.response?.stages || [])
        .find((s) => s.name === "model_routing")?.action,
    }));
    report.ui.chatSummary = firstBodies;

    assert(!statuses.includes(404), `no console/gateway 404 on /v1/chat/completions (got ${statuses.join(",")})`);
    const ok = chats.find((c) => c.status === 200 && c.response);
    assert(Boolean(ok), `pipeline completed HTTP 200 (statuses ${statuses.join(",")})`);
    const routing = ok.response?.zeroshield?.routing || {};
    const stages = ok.response?.pipeline_trace?.stages || ok.response?.stages || [];
    const routingStage = stages.find((s) => s.name === "model_routing") || {};
    const requested = String(
      routing.original_model
      || routing.requested_model
      || routingStage.requested_model
      || "",
    );
    const selected = String(routing.selected_model || routingStage.selected_model || "");
    const action = String(routingStage.action || "");
    report.ui.requested = requested;
    report.ui.selected = selected;
    report.ui.routingAction = action;
    report.ui.why = await panel.getByText(/Why this model/i).first().textContent().catch(() => "");
    const whyBody = await panel.locator("p").filter({ hasText: /You asked for|Routing policy selected/i }).first().textContent().catch(() => "");
    report.ui.whyBody = whyBody;

    assert(requested === MODEL, `requested/original model is ${MODEL} (got ${requested})`);
    assert(selected && selected !== MODEL, `selected a callable model, not ${MODEL} (got ${selected})`);
    assert(action === "reroute", `model_routing action is reroute (got ${action})`);
    assert(Boolean(whyBody), "WHY THIS MODEL summary is visible");
    assert(/You asked for Haiku/i.test(whyBody), `WHY copy is a Haiku reroute (got ${whyBody})`);
    assert(/cost/i.test(whyBody), `WHY THIS MODEL mentions cost (got ${whyBody})`);
    assert(!/public data-sensitivity/i.test(whyBody), "WHY THIS MODEL does not lead with public sensitivity");
    assert(/reroute/i.test(String(report.ui.routingChipLabel || "")), `timeline chip is reroute (got ${report.ui.routingChipLabel})`);

    report.ok = report.asserts.every((a) => a.pass);
  } catch (err) {
    report.error = String(err?.message || err);
    report.ok = false;
    await page.screenshot({ path: path.join(SHOT_DIR, "99-failure.png"), fullPage: true }).catch(() => {});
  } finally {
    report.finished_at = new Date().toISOString();
    fs.writeFileSync(OUT, JSON.stringify(report, null, 2));
    await browser.close();
    console.log(JSON.stringify({ ok: report.ok, out: OUT, asserts: report.asserts }, null, 2));
    if (!report.ok) process.exit(1);
  }
}

main();
