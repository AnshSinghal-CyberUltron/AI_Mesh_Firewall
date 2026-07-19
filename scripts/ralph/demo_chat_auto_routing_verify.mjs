/**
 * Gate: demo Chat model=auto works with routing_preferences (stream off + on).
 *
 * Proves:
 *   - login via real form
 *   - auto + stream OFF → assistant reply + vz-stages ≥1 + vz-selected non-empty
 *   - auto + stream ON → same when allowed
 *   - SDK snippet includes enable_routing
 *
 * Run:
 *   PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium-browser \
 *   node scripts/ralph/demo_chat_auto_routing_verify.mjs
 */
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import path from "node:path";

const require = createRequire(import.meta.url);
const HERE = path.dirname(fileURLToPath(import.meta.url));
const { chromium } = require(path.join(HERE, "../../tests/e2e/node_modules/playwright"));

const EMAIL = process.env.TEST_EMAIL || "admin@zeroshield.io";
const PASS = process.env.TEST_PASSWORD || "Adm1n!Pass#2024";
const BASE = (process.env.BASE_URL || "http://127.0.0.1:8180").replace(/\/$/, "");
const DEMO = `${BASE}/demo/`;

let pass = 0;
let fail = 0;
const ok = (cond, msg) => {
  if (cond) {
    pass++;
    console.log(`  ✅ ${msg}`);
  } else {
    fail++;
    console.log(`  ❌ ${msg}`);
  }
};

async function launch() {
  const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH || "/usr/bin/chromium-browser";
  return chromium.launch({
    headless: true,
    executablePath,
    args: ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
  });
}

async function login(page) {
  await page.goto(DEMO, { waitUntil: "domcontentloaded", timeout: 60000 });
  await page.waitForSelector('[data-testid="login-screen"]', { timeout: 20000 });
  await page.fill('[data-testid="login-email"]', EMAIL);
  await page.fill('[data-testid="login-password"]', PASS);
  await page.click('[data-testid="login-submit"]');
  await page.waitForSelector('[data-testid="org-badge"]', { timeout: 45000 });
  const org = await page.textContent('[data-testid="org-badge"]');
  ok(/zeroshield/i.test(org || ""), `org badge: ${org}`);
}

async function selectAuto(page) {
  await page.selectOption('[data-testid="chat-model"]', "auto");
  const v = await page.inputValue('[data-testid="chat-model"]');
  ok(v === "auto", `chat-model=${v}`);
}

async function waitForStages(page, label, timeoutMs = 180000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const stages = page.locator('[data-testid="vz-stages"] .stage');
    const n = await stages.count();
    if (n >= 1) {
      ok(true, `${label}: ${n} stage(s)`);
      return true;
    }
    const empty = await page.locator('[data-testid="vz-empty-reason"]').textContent().catch(() => "");
    const thread = await page.locator('[data-testid="chat-thread"]').textContent().catch(() => "");
    if (/not configured for external inference/i.test(`${empty}\n${thread}`)) {
      ok(false, `${label}: still model_not_configured — ${empty || thread.slice(0, 120)}`);
      return false;
    }
    await page.waitForTimeout(500);
  }
  const empty = await page.locator('[data-testid="vz-empty-reason"]').textContent().catch(() => "(none)");
  ok(false, `${label}: no stages within timeout; empty=${empty}`);
  return false;
}

async function runOnce(page, { stream, prompt }) {
  await selectAuto(page);
  if (stream) {
    await page.check("#chat-stream");
  } else {
    await page.uncheck("#chat-stream");
  }
  await page.fill('[data-testid="chat-input"]', prompt);
  await page.click('[data-testid="chat-send"]');

  const sdk = await page.locator('[data-testid="sdk-code"]').inputValue();
  ok(/enable_routing/i.test(sdk), `SDK snippet has enable_routing (stream=${stream})`);
  ok(/True|true|False|false/.test(sdk), `SDK snippet has routing bool`);

  const okStages = await waitForStages(page, stream ? "stream ON" : "stream OFF");
  if (okStages) {
    const selected = (await page.locator('[data-testid="vz-selected"]').textContent())?.trim() || "";
    ok(!!selected && selected !== "auto", `vz-selected=${selected}`);
  }
  const thread = (await page.locator('[data-testid="chat-thread"]').textContent()) || "";
  ok(!/not configured for external inference/i.test(thread), "no model_not_configured in thread");
}

async function main() {
  console.log(`demoChatAutoRoutingVerify BASE=${BASE}`);
  const browser = await launch();
  try {
    const page = await browser.newPage();
    page.setDefaultTimeout(60000);
    await login(page);
    await page.click('[data-testid="tab-chat"]');
    await runOnce(page, {
      stream: false,
      prompt: "In one short sentence, what is a reverse proxy?",
    });
    await page.click("#chat-clear");
    await runOnce(page, {
      stream: true,
      prompt: "Say hello in three words.",
    });
  } finally {
    await browser.close();
  }
  console.log(`\ndemoChatAutoRoutingPass: ${fail === 0} (${pass}/${pass + fail})`);
  process.exit(fail === 0 ? 0 : 1);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
