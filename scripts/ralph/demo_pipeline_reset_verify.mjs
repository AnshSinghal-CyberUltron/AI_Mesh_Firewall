/**
 * Gate: Request Pipeline resets immediately on new request / tab switch,
 * and stale in-flight completions must not repaint an older gen.
 *
 *   PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium-browser \
 *   BASE_URL=http://127.0.0.1:8180 node scripts/ralph/demo_pipeline_reset_verify.mjs
 */
import { chromium } from "../../tests/e2e/node_modules/playwright/index.mjs";

const BASE = process.env.BASE_URL || "http://127.0.0.1:8180";
const DEMO = `${BASE.replace(/\/$/, "")}/demo/`;
const EMAIL = process.env.DEMO_EMAIL || "admin@zeroshield.io";
const PASS = process.env.DEMO_PASS || "Adm1n!Pass#2024";
const EXE = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH || undefined;

const checks = [];
function ok(label, pass, detail = "") {
  checks.push({ label, pass: !!pass, detail });
  console.log(`${pass ? "✅" : "❌"} ${label}${detail ? ` — ${detail}` : ""}`);
}

async function login(page) {
  await page.goto(DEMO, { waitUntil: "domcontentloaded", timeout: 60000 });
  if (await page.locator("#login-screen").isVisible().catch(() => false)) {
    await page.fill("#login-email", EMAIL);
    await page.fill("#login-password", PASS);
    await page.click("#login-submit");
    await page.waitForSelector("#app:not([hidden])", { timeout: 60000 });
  }
  await page.waitForSelector("#chat-input", { timeout: 30000 });
}

(async () => {
  console.log(`demoPipelineResetVerify BASE=${BASE}`);
  const browser = await chromium.launch({
    headless: true,
    executablePath: EXE,
    args: ["--no-sandbox", "--disable-dev-shm-usage"],
  });
  const page = await browser.newPage();
  page.setDefaultTimeout(45000);

  try {
    await login(page);

    // Seed a fake completed pipeline so we can observe an immediate clear.
    await page.evaluate(() => {
      const stages = document.querySelector("#vz-stages");
      if (stages) {
        stages.innerHTML = `<div class="pst-stage stage" data-testid="seed-stage">seeded</div>`;
      }
      const route = document.querySelector("#vz-route");
      if (route) route.innerHTML = `<div data-testid="seed-route">old route</div>`;
    });
    ok("seeded old pipeline DOM", await page.locator("#vz-stages .stage, #vz-stages [data-testid=seed-stage]").count() > 0);

    // New chat request must clear immediately to pending (before network completes).
    await page.route("**/api/chat**", async (route) => {
      // Hold the response so we can assert pending UI first.
      await new Promise((r) => setTimeout(r, 2500));
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          content: "held reply",
          trace: {
            action: "allow",
            routing: { requested: "auto", selected: "held-model", reason: "test" },
            stages: [{ name: "input_scan", action: "allow", latency_ms: 1 }],
          },
        }),
      });
    });

    await page.fill("#chat-input", "pipeline reset probe");
    await page.press("#chat-input", "Enter");

    await page.waitForSelector('[data-testid="vz-pending"]', { timeout: 3000 });
    const pendingText = await page.locator('[data-testid="vz-pending"]').textContent();
    ok("Enter → pending immediately", /Running request/i.test(pendingText || ""), pendingText?.trim());
    ok("Enter → stages cleared", (await page.locator("#vz-stages .stage, #vz-stages .pst-stage").count()) === 0);
    ok("Enter → seed route gone", (await page.locator("[data-testid=seed-route]").count()) === 0);

    // Tab switch mid-flight → idle + ignore later completion.
    await page.click('.tab[data-tab="rag"]');
    await page.waitForSelector('[data-testid="vz-idle"]', { timeout: 3000 });
    const idleText = await page.locator('[data-testid="vz-idle"]').textContent();
    ok("tab switch → idle immediately", /Run a request/i.test(idleText || ""), idleText?.trim());

    // Wait for the held chat response to finish; pipeline must stay idle (stale gen).
    await page.waitForTimeout(3000);
    const stillIdle = (await page.locator('[data-testid="vz-idle"]').count()) > 0;
    const noHeldStage = (await page.locator("#vz-stages .stage, #vz-stages .pst-stage").count()) === 0;
    ok("stale completion does not repaint after tab switch", stillIdle && noHeldStage);

    // Fresh request on RAG should go pending again.
    await page.unroute("**/api/chat**");
    await page.route("**/api/rag/query", async (route) => {
      await new Promise((r) => setTimeout(r, 1500));
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          content: "rag held",
          retrieved: [],
          trace: {
            action: "allow",
            routing: { requested: "auto", selected: "rag-model" },
            stages: [{ name: "model_output", action: "allow", latency_ms: 2 }],
          },
        }),
      });
    });
    await page.fill("#rag-query", "reset rag probe");
    await page.click("#rag-ask");
    await page.waitForSelector('[data-testid="vz-pending"]', { timeout: 3000 });
    ok("RAG ask → pending immediately", true);

    await page.waitForSelector("#pipeline-stage-timeline, #vz-stages .pst-stage, #vz-stages .stage", { timeout: 8000 });
    ok("RAG ask → final stages paint for current gen", (await page.locator("#vz-stages .stage, #vz-stages .pst-stage").count()) > 0);

    const failed = checks.filter((c) => !c.pass);
    console.log(JSON.stringify({
      demoPipelineResetPass: failed.length === 0,
      passed: checks.filter((c) => c.pass).length,
      failed: failed.length,
      checks,
    }, null, 2));
    process.exit(failed.length ? 1 : 0);
  } catch (e) {
    console.error("FATAL", e);
    console.log(JSON.stringify({ demoPipelineResetPass: false, error: String(e) }, null, 2));
    process.exit(1);
  } finally {
    await browser.close();
  }
})();
