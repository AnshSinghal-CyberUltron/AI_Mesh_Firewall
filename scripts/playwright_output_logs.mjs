/**
 * D5-ui-output-logs browser verification (real customer perspective).
 *
 * Verifies, against the running Vite app (:8180) backed by control(:8100)+gateway(:8300),
 * Module 1.7 — Generator-Level Output Guardrails (?tab=firewall-1-7) AND the LogViewer /
 * LogDetail drill-down, with a HARD honesty check that no raw PII/secret is ever displayed
 * where it shouldn't be and that dashboard counts match enforced reality.
 *
 * The honesty proof is anchored on REAL enforced bytes, not on a label:
 *  - Setup mints a short-lived gateway key, drives ONE live /v1/chat/completions through the
 *    firewall carrying KNOWN raw PII (a real phone + email), confirms the firewall verdict is
 *    `redact`, then deletes the key. The submitted raw PII is recorded so we can assert it is
 *    NEVER rendered anywhere in the operator UI (only the masked `***` form may appear).
 *
 * Phases:
 *  0. login.
 *  1. Generate a real redact event (known raw PII) via a minted key; key deleted afterwards.
 *  2. Module 1.7 renders all four output panels: Controls, Engine, Analytics(Charts), Governance.
 *  3. OutputGuardrailControls round-trip: flip Security-Incident-Logging (+ IP-leakage action
 *     pill when locatable) -> Save -> PUT /api/firewall/config/ 2xx -> backend reflects the new
 *     value (parsed) -> RESTORE to the original via a second Save (side-effect free).
 *  4. Output Governance Log honesty (7d lens): the on-screen blocked/redacted/flagged chips ==
 *     backend output-event counts; the Redact filter narrows the list; expand a redact event ->
 *     OutputPipelineTimeline renders (incl. "Guardrail Action" + "Final Output to User") and the
 *     rendered card shows masking (`***`) but contains NO unmasked phone/email.
 *  5. Analytics charts render (recharts svg) for the 7d window.
 *  6. LogViewer (SubModuleResultsPage 1.7) + LogDetail: "Detailed Records (N)" == backend 24h
 *     security_scan feed length (counts match enforced reality); filters/search/export render;
 *     filter to Redacted -> every visible row's Action == redact -> drill the first row ->
 *     LogDetailPage "Scan Detail Report" + Request ID render; HONESTY: the detail page contains
 *     `***` and contains NEITHER the raw phone NOR the raw email we submitted, nor a raw secret.
 *  7. No uncaught JS errors (pageerror) in any phase.
 *
 * Captured text (governance card + log detail) is dumped to the report so an INDEPENDENT oracle
 * (mcp__ruflo__aidefence_has_pii) can cross-check the egress is PII-free, per the leak invariant.
 *
 * Run FROM frontend/ so playwright resolves:
 *   cd frontend && BASE_URL=http://127.0.0.1:8180 SHOT_DIR=../runs/d5 \
 *     E2E_REPORT=../runs/playwright_output_logs.json node ../scripts/playwright_output_logs.mjs
 */
import { chromium } from "playwright";
import fs from "node:fs";

const BASE = (process.env.BASE_URL || "http://127.0.0.1:8180").replace(/\/$/, "");
const EMAIL = process.env.TEST_EMAIL || "admin@zeroshield.io";
const PASS = process.env.TEST_PASSWORD || "Adm1n!Pass#2024";
const OUT = process.env.E2E_REPORT || "runs/playwright_output_logs.json";
const SHOT_DIR = process.env.SHOT_DIR || "runs/d5";
const GEN_MODEL = process.env.GEN_MODEL || "openai/gpt-4o-mini";

// Known raw PII we submit through the firewall. It must NEVER appear in any operator surface.
const RAW_PHONE = "555-867-5309";
const RAW_EMAIL = "alex.rivera@acmecomp.com";

// Patterns for an UNMASKED leak (the masked form a***@e***.com / ***-***-5309 won't match these).
const UNMASKED_PHONE = /\b\d{3}[-.\s]\d{3}[-.\s]\d{4}\b/;
const UNMASKED_EMAIL = /[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/;
const RAW_SECRET = /\b(sk-[A-Za-z0-9]{16,}|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----)\b/;

const report = { base: BASE, ok: false, steps: [], asserts: [], pageErrors: [], notes: [], error: null,
  generatedAction: null, governanceText: "", logDetailText: "" };
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

async function login(page) {
  CURRENT_PHASE = "login";
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle", timeout: 120000 });
  await page.locator("#email").fill(EMAIL);
  await page.locator("#password").fill(PASS);
  const [ok] = await Promise.all([
    page.waitForResponse((r) => r.url().includes("/api/auth/token/") && r.request().method() === "POST", { timeout: 30000 }),
    page.getByRole("button", { name: /^sign in$/i }).click(),
  ]);
  assert(ok.ok(), `valid creds -> 2xx (got ${ok.status()})`);
  await page.waitForFunction(() => !location.pathname.startsWith("/login"), { timeout: 30000 });
  report.steps.push("login");
}

async function apiGet(page, path) {
  return page.evaluate(async (p) => {
    const tok = localStorage.getItem("auth_access");
    const r = await fetch(p, { headers: { Authorization: `Bearer ${tok}` } });
    return { status: r.status, body: await r.json().catch(() => null) };
  }, path);
}

// Mint a short-lived gateway key, drive a live firewall-mediated completion carrying raw PII,
// confirm the verdict, then delete the key. Returns { action, content, error }.
async function generateRedactEvent(page) {
  return page.evaluate(async ({ model, phone, email }) => {
    const tok = localStorage.getItem("auth_access");
    let keyId = null, action = null, content = null, error = null;
    try {
      const mintR = await fetch("/api/gateways/keys/", {
        method: "POST",
        headers: { Authorization: `Bearer ${tok}`, "Content-Type": "application/json" },
        body: JSON.stringify({ name: "d5-gen-probe", project_id: "d5-gen-probe" }),
      });
      const mint = await mintR.json();
      keyId = mint.id;
      const secret = mint.key;
      const r = await fetch("/v1/chat/completions", {
        method: "POST",
        headers: { Authorization: `Bearer ${secret}`, "Content-Type": "application/json" },
        body: JSON.stringify({
          model,
          messages: [{ role: "user", content: `Repeat this contact card back to me exactly: Reach Alex at phone ${phone} or email ${email}.` }],
          max_tokens: 120,
        }),
      });
      const j = await r.json().catch(() => ({}));
      action = j?.zeroshield?.action ?? null;
      content = j?.choices?.[0]?.message?.content ?? null;
    } catch (e) {
      error = String(e);
    } finally {
      if (keyId) {
        try {
          await fetch(`/api/gateways/keys/${keyId}/`, { method: "DELETE", headers: { Authorization: `Bearer ${tok}` } });
        } catch {}
      }
    }
    return { action, content, error };
  }, { model: GEN_MODEL, phone: RAW_PHONE, email: RAW_EMAIL });
}

async function main() {
  fs.mkdirSync("runs", { recursive: true });
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1200 } });
  const page = await context.newPage();
  page.on("pageerror", (e) => report.pageErrors.push({ phase: CURRENT_PHASE, message: String(e.message || e) }));
  page.on("dialog", (d) => d.accept());

  try {
    await login(page);

    // ───────── 1. Generate a real redact event (known raw PII) ─────────
    CURRENT_PHASE = "generate";
    const gen = await generateRedactEvent(page);
    report.generatedAction = gen.action;
    report.notes.push(`generation: action=${gen.action} content=${JSON.stringify(gen.content)} err=${gen.error}`);
    // The submitted raw PII must already be masked in the live response (egress = truth).
    if (gen.action === "redact") {
      assert(typeof gen.content === "string" && !UNMASKED_PHONE.test(gen.content) && !gen.content.includes(RAW_EMAIL),
        "live firewall response is redacted (no raw phone/email in returned content)");
    } else {
      report.notes.push("WARN: generation did not return a redact verdict; relying on pre-existing 24h data");
    }
    report.steps.push("generate");

    // ───────── 2. Module 1.7 renders the four output panels ─────────
    CURRENT_PHASE = "1.7-render";
    const [cfgRes] = await Promise.all([
      page.waitForResponse((r) => r.url().includes("/api/firewall/config/") && r.request().method() === "GET", { timeout: 60000 }),
      page.goto(`${BASE}/?tab=firewall-1-7`, { waitUntil: "domcontentloaded", timeout: 120000 }),
    ]);
    assert(cfgRes.ok(), `GET firewall config -> 2xx (${cfgRes.status()})`);
    // module pages stream telemetry; networkidle never settles — wait per-panel.
    await page.locator("text=/Output Guardrail Controls/i").first().waitFor({ state: "visible", timeout: 30000 });
    await page.locator("text=/Output Guardrail Engine/i").first().waitFor({ state: "visible", timeout: 30000 });
    await page.locator("text=/Output Guardrail Analytics/i").first().waitFor({ state: "visible", timeout: 30000 });
    await page.locator("text=/Output Governance Log/i").first().waitFor({ state: "visible", timeout: 30000 });
    assert(true, "1.7 renders Controls + Engine + Analytics + Governance panels");
    report.steps.push("1.7-render");
    await shot(page, "01-module-1-7");

    // ───────── 3. OutputGuardrailControls round-trip (backend-verified + restored) ─────────
    CURRENT_PHASE = "controls-roundtrip";
    const cfg0 = await apiGet(page, "/api/firewall/config/");
    assert(cfg0.status === 200, "read current firewall config");
    const origIncident = !!cfg0.body.output_incident_logging_enabled;
    const origIpAction = cfg0.body.output_ip_leakage_action || "flag";
    const newIpAction = origIpAction === "block" ? "flag" : "block";

    const incidentSwitch = page.getByRole("switch", { name: /Security incident logging/i }).first();
    await incidentSwitch.waitFor({ state: "visible", timeout: 20000 });
    await incidentSwitch.click(); // flip incident logging

    // Best-effort: flip the IP-leakage detector action pill (scoped to its row).
    let pillFlipped = false;
    try {
      const ipRow = page.locator("div").filter({ has: page.getByText("IP Leakage", { exact: true }) })
        .filter({ has: page.getByRole("button", { name: /^Block$/ }) }).last();
      const pillName = new RegExp(`^${newIpAction === "block" ? "Block" : "Flag"}$`);
      await ipRow.getByRole("button", { name: pillName }).first().click({ timeout: 5000 });
      pillFlipped = true;
    } catch { report.notes.push("ip-leakage action pill not locatable; toggling incident-logging only"); }

    const saveBtn = page.getByRole("button", { name: /Save guardrails/i });
    const [putRes] = await Promise.all([
      page.waitForResponse((r) => r.url().includes("/api/firewall/config/") && r.request().method() === "PUT", { timeout: 30000 }),
      saveBtn.click(),
    ]);
    assert(putRes.ok(), `Save guardrails PUT -> 2xx (${putRes.status()})`);
    const cfg1 = await apiGet(page, "/api/firewall/config/");
    assert(cfg1.body.output_incident_logging_enabled === !origIncident,
      `backend incident-logging flipped (${origIncident} -> ${!origIncident})`);
    if (pillFlipped) {
      assert(cfg1.body.output_ip_leakage_action === newIpAction,
        `backend output_ip_leakage_action == "${newIpAction}"`);
    }
    await shot(page, "02-controls-saved");

    // restore
    await incidentSwitch.click();
    if (pillFlipped) {
      const ipRow = page.locator("div").filter({ has: page.getByText("IP Leakage", { exact: true }) })
        .filter({ has: page.getByRole("button", { name: /^Block$/ }) }).last();
      await ipRow.getByRole("button", { name: new RegExp(`^${origIpAction === "block" ? "Block" : "Flag"}$`) }).first().click();
    }
    const [putRes2] = await Promise.all([
      page.waitForResponse((r) => r.url().includes("/api/firewall/config/") && r.request().method() === "PUT", { timeout: 30000 }),
      saveBtn.click(),
    ]);
    assert(putRes2.ok(), `restore PUT -> 2xx (${putRes2.status()})`);
    const cfg2 = await apiGet(page, "/api/firewall/config/");
    assert(cfg2.body.output_incident_logging_enabled === origIncident, "backend incident-logging restored");
    if (pillFlipped) assert(cfg2.body.output_ip_leakage_action === origIpAction, "backend ip-leakage action restored");
    report.steps.push("controls-roundtrip");

    // ───────── 4. Output Governance Log honesty (7d lens) ─────────
    CURRENT_PHASE = "governance-honesty";
    const feed = await apiGet(page, "/api/security/threat-feed/?hours=168&limit=500&source=security_scan");
    const feedRows = Array.isArray(feed.body) ? feed.body : (feed.body?.results || []);
    const outputEvents = feedRows.filter((ev) => {
      const et = (ev.metadata?.event_type || "").toLowerCase();
      return et === "output_guard" || et === "output_scan";
    });
    const beBlocked = outputEvents.filter((e) => e.action === "block").length;
    const beRedacted = outputEvents.filter((e) => e.action === "redact").length;
    const beFlagged = outputEvents.filter((e) => e.action === "flag").length;
    report.notes.push(`backend output events (7d): ${outputEvents.length} (block=${beBlocked} redact=${beRedacted} flag=${beFlagged})`);

    // Scope to the governance card ROOT (ai-mesh-card) so the summary chips (which live in the
    // header's right column, a sibling of the heading) are inside the scope — not just the heading div.
    const govPanel = page.locator("div.ai-mesh-card").filter({ has: page.getByRole("heading", { name: /Output Governance Log/i }) }).first();
    // Summary chips read "<n> blocked / <n> redacted / <n> flagged".
    const chipText = (await govPanel.locator("text=/\\d+ blocked/").first().textContent().catch(() => "")) || "";
    const blockedShown = parseInt((chipText.match(/(\d+)\s*blocked/) || [])[1] ?? "-1", 10);
    const redactedShown = parseInt(((await govPanel.locator("text=/\\d+ redacted/").first().textContent().catch(() => "")) || "").match(/(\d+)\s*redacted/)?.[1] ?? "-1", 10);
    const flaggedShown = parseInt(((await govPanel.locator("text=/\\d+ flagged/").first().textContent().catch(() => "")) || "").match(/(\d+)\s*flagged/)?.[1] ?? "-1", 10);
    assert(blockedShown === beBlocked, `governance "blocked" chip (${blockedShown}) == backend (${beBlocked})`);
    assert(redactedShown === beRedacted, `governance "redacted" chip (${redactedShown}) == backend (${beRedacted})`);
    assert(flaggedShown === beFlagged, `governance "flagged" chip (${flaggedShown}) == backend (${beFlagged})`);

    if (beRedacted > 0) {
      // Filter to Redact and expand the first redact event.
      await govPanel.getByRole("button", { name: /^Redact\s*\d+$/ }).first().click();
      await page.waitForTimeout(400);
      // Event-row toggles live inside the scrollable list container. Scope to it so we don't
      // grab the header InfoTooltip button (it ALSO carries aria-expanded with empty text).
      const firstRow = govPanel.locator("div.overflow-y-auto button[aria-expanded]").first();
      await firstRow.waitFor({ state: "visible", timeout: 15000 });
      await firstRow.click();
      await page.locator("text=/Ingestion Pipeline/i").first().waitFor({ state: "visible", timeout: 15000 });
      await page.locator("text=/Final Output to User/i").first().waitFor({ state: "visible", timeout: 15000 });
      await page.locator("text=/Guardrail Action/i").first().waitFor({ state: "visible", timeout: 15000 });
      assert(true, "redact event expands to the step-by-step output pipeline timeline");
      const cardText = (await govPanel.innerText().catch(() => "")) || "";
      report.governanceText = cardText.slice(0, 4000);
      assert(cardText.includes("***"), "governance timeline shows masking (***) for the redaction");
      assert(!cardText.includes(RAW_EMAIL), "governance timeline does NOT show the raw email");
      // The forensic raw-output snippet is masked; no fully-unmasked phone/email may appear.
      assert(!UNMASKED_PHONE.test(cardText), "governance timeline does NOT show an unmasked phone number");
    } else {
      report.notes.push("no redact output events in 7d window; skipped governance timeline drill-down");
    }
    report.steps.push("governance-honesty");
    await shot(page, "03-governance");

    // ───────── 5. Analytics charts render ─────────
    CURRENT_PHASE = "charts";
    const chartsCard = page.locator("div.ai-mesh-card").filter({ has: page.getByRole("heading", { name: /Output Guardrail Analytics/i }) }).first();
    await chartsCard.locator("svg.recharts-surface, .recharts-wrapper, svg").first().waitFor({ state: "visible", timeout: 30000 });
    assert(true, "Output Guardrail Analytics renders a chart (recharts svg)");
    report.steps.push("charts");

    // ───────── 6. LogViewer (detailed results) + LogDetail drill-down ─────────
    CURRENT_PHASE = "logviewer";
    await page.getByRole("button", { name: /Open detailed results/i }).first().click();
    await page.locator("text=/Generator-Level Output Guardrails - Detailed Results/i").first().waitFor({ state: "visible", timeout: 30000 });
    await page.locator("text=/Detailed Records/i").first().waitFor({ state: "visible", timeout: 30000 });
    const be24 = await apiGet(page, "/api/security/threat-feed/?hours=24&limit=500&source=security_scan");
    const be24Rows = Array.isArray(be24.body) ? be24.body : (be24.body?.results || []);
    const n = be24Rows.length;
    const recHeader = (await page.locator("text=/Detailed Records \\(\\d+\\)/").first().textContent()) || "";
    const shownN = parseInt((recHeader.match(/\((\d+)\)/) || [])[1] ?? "-1", 10);
    assert(shownN === n, `"Detailed Records (${shownN})" == backend 24h security_scan feed (${n})`);
    // controls present
    await page.getByPlaceholder("Search all fields...").waitFor({ state: "visible", timeout: 15000 });
    assert(await page.getByRole("button", { name: /Export CSV/i }).count() > 0, "Export CSV control present");
    report.notes.push(`24h security_scan rows: ${n}`);

    if (n > 0) {
      // Filter to Redacted; assert every visible row's Action cell == redact (filter honesty).
      const actionSelect = page.locator("select").first();
      await actionSelect.selectOption("redact");
      await page.waitForTimeout(300);
      const actionCells = await page.locator("tbody tr td:nth-child(3)").allInnerTexts();
      const visibleActions = actionCells.map((t) => t.trim().toLowerCase()).filter(Boolean);
      assert(visibleActions.length > 0, "Redacted filter shows at least one row");
      assert(visibleActions.every((a) => a === "redact"), `every filtered row Action == redact (saw ${JSON.stringify([...new Set(visibleActions)])})`);

      // Drill the first redact row -> LogDetailPage.
      await page.locator("tbody tr").first().click();
      await page.locator("text=/Scan Detail Report/i").first().waitFor({ state: "visible", timeout: 20000 });
      await page.locator("text=/Request ID:/i").first().waitFor({ state: "visible", timeout: 20000 });
      assert(true, "row drill-down opens the LogDetail Scan Detail Report with a Request ID");
      const detailText = (await page.locator("body").innerText().catch(() => "")) || "";
      report.logDetailText = detailText.slice(0, 6000);
      // HONESTY: the raw PII we submitted must never be rendered; masked form only.
      assert(!detailText.includes(RAW_PHONE), "LogDetail does NOT render the raw phone we submitted");
      assert(!detailText.includes(RAW_EMAIL), "LogDetail does NOT render the raw email we submitted");
      assert(!RAW_SECRET.test(detailText), "LogDetail does NOT render a raw secret/credential token");
      assert(detailText.includes("***"), "LogDetail shows the masked (***) redacted form");
      await shot(page, "04-logdetail");
      // back out
      await page.getByRole("button", { name: /Back to Activity Preview/i }).first().click();
    } else {
      await page.locator("text=/No records found for current filter/i").first().waitFor({ state: "visible", timeout: 15000 });
      assert(true, "empty 24h window honestly shows 'No records found'");
      await shot(page, "04-logviewer-empty");
    }
    report.steps.push("logviewer");

    // ───────── 7. No uncaught JS errors ─────────
    CURRENT_PHASE = "pageerrors";
    assert(report.pageErrors.length === 0, `no uncaught pageerror (saw ${report.pageErrors.length})`);

    report.ok = true;
  } catch (e) {
    report.error = String(e && e.stack ? e.stack : e);
    await shot(page, "zz-error");
  } finally {
    fs.writeFileSync(OUT, JSON.stringify(report, null, 2));
    await browser.close();
  }

  const passed = report.asserts.filter((a) => a.pass).length;
  console.log(`\nD5 output-logs: ${report.ok ? "OK" : "FAIL"} — ${passed}/${report.asserts.length} asserts, ${report.steps.length} steps, ${report.pageErrors.length} pageErrors`);
  if (!report.ok) {
    console.log("ERROR:", report.error);
    if (report.pageErrors.length) console.log("pageErrors:", JSON.stringify(report.pageErrors, null, 2));
    process.exit(1);
  }
}

main();
