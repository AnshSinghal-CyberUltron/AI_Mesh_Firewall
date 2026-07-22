/*
 * ZeroShield demo — end-to-end Playwright validation (customer perspective).
 *
 * Run (with the demo backend serving the frontend on :8800):
 *   NODE_PATH=/opt/homebrew/lib/node_modules node tests/playwright_demo.cjs
 * Or as a @playwright/test spec: see tests/README.md.
 */
const { chromium } = require("playwright");
const BASE = process.env.DEMO_URL || "http://127.0.0.1:8770";
// Every data route is behind a control-plane login, so the suite must sign in the
// way a customer does. Credentials come from the environment — never hardcoded.
const EMAIL = process.env.DEMO_EMAIL || process.env.TEST_EMAIL || "";
const PASSWORD = process.env.DEMO_PASSWORD || process.env.TEST_PASSWORD || "";

let pass = 0, fail = 0;
const ok = (c, m) => { if (c) { pass++; console.log("  ✅ " + m); } else { fail++; console.log("  ❌ " + m); } };

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newContext().then((c) => c.newPage());
  const errors = [];
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text().slice(0, 120)); });
  try {
    // 0) Sign in — the demo binds to the logged-in org's key, policies and models.
    if (!EMAIL || !PASSWORD) {
      throw new Error(
        "DEMO_EMAIL / DEMO_PASSWORD (or TEST_EMAIL / TEST_PASSWORD) must be set — " +
        "every demo data route requires a control-plane login."
      );
    }
    await page.goto(BASE, { waitUntil: "domcontentloaded", timeout: 30000 });
    await page.waitForSelector('[data-testid="login-screen"]', { timeout: 20000 });
    await page.fill('[data-testid="login-email"]', EMAIL);
    await page.fill('[data-testid="login-password"]', PASSWORD);
    await page.click('[data-testid="login-submit"]');
    await page.waitForFunction(
      () => document.querySelector('[data-testid="login-screen"]')?.hidden === true,
      { timeout: 30000 }
    );
    ok(true, `signed in as ${EMAIL}`);

    // 1) Gateway connection
    await page.waitForFunction(() => /\d+\s*models/.test(document.querySelector("#conn")?.textContent || ""), { timeout: 20000 });
    const conn = await page.textContent("#conn");
    ok(/\d+\s*models/.test(conn), `gateway connected: "${conn.trim()}"`);

    // 1b) Observability/governance panel (§6) renders from the SDK key
    await page.waitForFunction(() => /enforcement/i.test(document.querySelector('[data-testid="vz-obs"]')?.textContent || ""), { timeout: 15000 });
    const obs = await page.textContent('[data-testid="vz-obs"]');
    ok(/enforcement/i.test(obs) && /polic/i.test(obs), `observability panel shows governance: "${obs.replace(/\s+/g, " ").slice(0, 70)}"`);

    // 2) Chat (non-stream so the visualizer renders)
    await page.click('[data-testid="tab-chat"]');
    await page.uncheck("#chat-stream");
    await page.fill('[data-testid="chat-input"]', "Say hello in exactly three words");
    await page.click('[data-testid="chat-send"]');
    await page.waitForFunction(() => document.querySelectorAll('#chat-thread .msg.assistant .txt')[0]?.textContent?.length > 0, { timeout: 40000 });
    const reply = await page.textContent("#chat-thread .msg.assistant .txt");
    ok(reply && reply.length > 1, `chat reply rendered: "${(reply || "").slice(0, 40)}"`);
    const stages = await page.$$eval('[data-testid="vz-stages"] .stage', (els) => els.map((e) => e.dataset.stage));
    ok(stages.includes("model_routing") && stages.includes("output_guardrail"), `visualizer shows ${stages.length} pipeline stages`);

    // 3) Output validation — PII → redact
    await page.click('[data-testid="tab-validation"]');
    // Clear the visualizer first so we don't read the previous request's stale render.
    await page.evaluate(() => { document.querySelector('[data-testid="vz-stages"]').innerHTML = ""; });
    await page.click('[data-testid="preset-0"]'); // PII
    await page.click('[data-testid="val-run"]');
    // Wait for the NEW result: some stage must show redact or block (firewall acted on PII).
    await page.waitForFunction(() => Array.from(document.querySelectorAll('[data-testid="vz-stages"] .stage'))
      .some((s) => ["redact", "block"].includes(s.dataset.action)), { timeout: 40000 });
    const acted = await page.$$eval('[data-testid="vz-stages"] .stage', (els) =>
      els.filter((e) => ["redact", "block"].includes(e.dataset.action)).map((e) => `${e.dataset.stage}:${e.dataset.action}`));
    ok(acted.length > 0, `PII → firewall acted: ${acted.join(", ")}`);

    // 4) Output validation — injection → block (visualized, not a crash)
    await page.evaluate(() => { document.querySelector('[data-testid="vz-stages"]').innerHTML = ""; });
    await page.click('[data-testid="preset-2"]'); // injection
    await page.click('[data-testid="val-run"]');
    await page.waitForFunction(() => {
      const els = Array.from(document.querySelectorAll('[data-testid="vz-stages"] .stage'));
      return els.some((s) => s.dataset.action === "block");
    }, { timeout: 40000 });
    const hasBlock = await page.$$eval('[data-testid="vz-stages"] .stage', (els) => els.some((e) => e.dataset.action === "block"));
    ok(hasBlock, "injection → pipeline visualizes a BLOCK stage");
    const incident = await page.$('[data-testid="vz-incident"]');
    const incidentId = incident ? await incident.textContent() : "";
    ok(/^zs-/.test(incidentId || ""), `block surfaces an Incident ID: "${incidentId}"`);

    // 5) RAG ingest + grounded query
    await page.click('[data-testid="tab-rag"]');
    await page.fill('[data-testid="rag-name"]', "Refund Policy");
    await page.fill('[data-testid="rag-content"]', "Enterprise customers get priority refund processing within 5 days. Standard refunds take 14 business days.");
    await page.click('[data-testid="rag-ingest"]');
    await page.waitForTimeout(1000);
    await page.fill('[data-testid="rag-query"]', "How long do refunds take for enterprise customers?");
    await page.click('[data-testid="rag-ask"]');
    await page.waitForFunction(() => (document.querySelector('[data-testid="rag-answer"]')?.textContent || "").length > 5 && !/^…$/.test(document.querySelector('[data-testid="rag-answer"]').textContent), { timeout: 40000 });
    const ragAns = await page.textContent('[data-testid="rag-answer"]');
    ok(/5\s*days|five days/i.test(ragAns), `RAG grounded answer: "${ragAns.slice(0, 60)}"`);

    // 6) MCP context
    await page.click('[data-testid="tab-mcp"]');
    await page.click('[data-testid="mcp-run"]');
    await page.waitForFunction(() => { const t = document.querySelector('[data-testid="mcp-answer"]')?.textContent || ""; return t.length > 5 && t !== "…"; }, { timeout: 40000 });
    const mcpAns = await page.textContent('[data-testid="mcp-answer"]');
    ok(/C-10293|enterprise/i.test(mcpAns), `MCP context-grounded answer: "${mcpAns.slice(0, 50)}"`);

    // 7) Routing visualizer
    await page.click('[data-testid="tab-routing"]');
    await page.click('[data-testid="route-run"]');
    await page.waitForFunction(() => (document.querySelector('[data-testid="vz-selected"]')?.textContent || "").length > 0, { timeout: 40000 });
    const served = await page.textContent('[data-testid="vz-selected"]');
    ok(served && served.length > 0, `routing visualizer shows served model: "${served}"`);

    // 7b) Governance signals strip — the gateway sends quota / clamp / review state
    // as RESPONSE HEADERS. Assert it renders whatever arrived, and stays silent when
    // nothing did (an empty strip must never read as "all clear").
    const gov = await page.$eval('[data-testid="vz-gov"]',
      (e) => ({ hidden: e.hidden, text: (e.textContent || "").replace(/\s+/g, " ").trim() }));
    ok(gov.hidden || /token limit|tokens left|params rewritten|human review/i.test(gov.text),
      gov.hidden ? "governance strip hidden (no headers sent)" : `governance strip: "${gov.text.slice(0, 80)}"`);

    // 7c) MCP scan posture is stated, not implied. Under the default `tag` posture the
    // firewall detects but does NOT modify or block, and the UI must say so.
    await page.click('[data-testid="tab-mcp"]');
    await page.waitForFunction(
      () => (document.querySelector('[data-testid="mcp-posture"]')?.textContent || "").length > 0,
      { timeout: 20000 });
    const posture = await page.textContent('[data-testid="mcp-posture"]');
    ok(/MCP posture:/.test(posture) && /(enforcing|detect-only)/.test(posture),
      `MCP posture disclosed: "${posture.replace(/\s+/g, " ").slice(0, 90)}"`);

    // 8) Refresh persistence (page reloads cleanly, reconnects)
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.waitForFunction(() => /models/.test(document.querySelector("#conn")?.textContent || ""), { timeout: 20000 });
    ok(true, "page reload reconnects to gateway");

    ok(errors.length === 0, `no console errors (${errors.length})` + (errors.length ? ": " + JSON.stringify(errors.slice(0, 3)) : ""));
  } catch (e) {
    fail++; console.log("  ❌ EXCEPTION: " + e.message);
  } finally {
    await browser.close();
  }
  console.log(`\nRESULT: ${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
})();
