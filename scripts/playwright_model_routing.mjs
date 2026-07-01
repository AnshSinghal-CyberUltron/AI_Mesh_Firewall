/**
 * D2-ui-model-routing browser verification (real customer perspective).
 *
 * Verifies, against the running Vite app (:8180) backed by control(:8100)+gateway(:8300):
 *
 *  Module 1.5 — Multi-Model Governance & Routing (?tab=firewall-1-5):
 *   1. ModelConnectionPanel renders ("Model Connections") and lists ≥1 connected model.
 *   2. ModelGovernancePanel renders ("Model allowlist & default…").
 *   3. RoutingGovernancePanel ("Routing Governance"): flip the Dynamic Routing toggle, Save routing
 *      (PUT /api/firewall/config/ → 2xx), assert the on-screen toggle AND the backend config reflect
 *      the new value, then RESTORE the original value (toggle back + save) so the run is side-effect free.
 *   4. RoutingAuditPanel footer renders ("Routing Audit Trail").
 *
 *  Module 1.6 — Inline Model Isolation & Kill-Switch (?tab=firewall-1-6):
 *   5. ModelStatePanel renders ("Model State & Risk Monitor") and KillSwitchPanel ("Kill-Switch Management").
 *   6. Create a kill-switch (disable) against a connected model via the modal (POST /api/kill-switches/ → 2xx);
 *      the new row appears with the model name and a status badge that MATCHES the create response is_active
 *      (UI honesty: the badge is not asserted independently of the backend truth).
 *   7. Toggle it: deactivate → "Inactive" badge; activate → "Active" badge — each step asserted against the
 *      live GET /api/kill-switches/ so the badge always mirrors backend state.
 *   8. Delete it (cleanup) — DELETE → row removed; final GET shows it gone.
 *   9. No uncaught JS errors (pageerror) in any phase.
 *
 * Run: BASE_URL=http://127.0.0.1:8180 node scripts/playwright_model_routing.mjs
 */
import { chromium } from "playwright";
import fs from "node:fs";

const BASE = (process.env.BASE_URL || "http://127.0.0.1:8180").replace(/\/$/, "");
const EMAIL = process.env.TEST_EMAIL || "admin@zeroshield.io";
const PASS = process.env.TEST_PASSWORD || "Adm1n!Pass#2024";
const OUT = process.env.E2E_REPORT || "runs/playwright_model_routing.json";
const SHOT_DIR = process.env.SHOT_DIR || "runs/d2";

const report = { base: BASE, ok: false, steps: [], asserts: [], pageErrors: [], notes: [], error: null };
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

async function main() {
  fs.mkdirSync("runs", { recursive: true });
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  page.on("pageerror", (e) => report.pageErrors.push({ phase: CURRENT_PHASE, message: String(e.message || e) }));
  page.on("dialog", (d) => d.accept()); // accept delete/confirm dialogs

  try {
    await login(page);

    // ───────── Module 1.5: model routing lane ─────────
    CURRENT_PHASE = "1.5-panels";
    const [modelsRes] = await Promise.all([
      page.waitForResponse((r) => r.url().includes("/api/firewall/models/") && r.request().method() === "GET", { timeout: 60000 }).catch(() => null),
      page.goto(`${BASE}/?tab=firewall-1-5`, { waitUntil: "domcontentloaded", timeout: 120000 }),
    ]);
    // module pages stream telemetry, so networkidle never settles — wait per-panel instead.
    await page.locator("text=/llm model connections/i").first().waitFor({ state: "visible", timeout: 30000 });
    await page.locator("text=/model allowlist/i").first().waitFor({ state: "visible", timeout: 30000 });
    await page.locator("text=/routing governance/i").first().waitFor({ state: "visible", timeout: 30000 });
    await page.locator("text=/routing audit trail/i").first().waitFor({ state: "visible", timeout: 30000 });
    assert(true, "1.5 renders ModelConnections + ModelGovernance + RoutingGovernance + RoutingAudit panels");
    report.steps.push("1.5-panels-render");
    await shot(page, "01-module-1-5");

    // connected models listed (use the backend list as the source of truth)
    CURRENT_PHASE = "1.5-models-listed";
    let firstModel = "gpt4o-mini";
    if (modelsRes && modelsRes.ok()) {
      const models = await modelsRes.json().catch(() => []);
      const list = Array.isArray(models) ? models : models.results || [];
      assert(list.length > 0, `backend reports ≥1 connected model (got ${list.length})`);
      firstModel = list[0].model_name || firstModel;
      // at least one connected model_name is rendered in the Model Connections panel
      await page.locator(`text=${list[0].model_name}`).first().waitFor({ state: "visible", timeout: 20000 }).catch(() => {});
      const anyShown = await page.locator(`text=${list[0].model_name}`).first().isVisible().catch(() => false);
      assert(anyShown, `connected model "${list[0].model_name}" is shown in the UI`);
    } else {
      report.notes.push("models GET not observed on nav; relying on later modal fetch");
    }
    report.steps.push("1.5-models-listed");

    // ───────── 1.5: Routing Governance toggle round-trip (with restore) ─────────
    CURRENT_PHASE = "1.5-routing-toggle";
    const routingToggle = page.getByRole("button", { name: /^(Enabled|Disabled)$/ }).first();
    await routingToggle.waitFor({ state: "visible", timeout: 15000 });
    const originalLabel = (await routingToggle.innerText()).trim();
    assert(originalLabel === "Enabled" || originalLabel === "Disabled", `routing toggle shows a known state (got "${originalLabel}")`);

    await routingToggle.click(); // flip in UI
    const flippedLabel = originalLabel === "Enabled" ? "Disabled" : "Enabled";
    const [saveRes] = await Promise.all([
      page.waitForResponse((r) => r.url().includes("/api/firewall/config/") && r.request().method() === "PUT", { timeout: 30000 }),
      page.getByRole("button", { name: /save routing/i }).click(),
    ]);
    assert(saveRes.ok(), `save routing -> 2xx (got ${saveRes.status()})`);
    const savedCfg = await saveRes.json().catch(() => ({}));
    const expectEnabled = flippedLabel === "Enabled";
    assert(savedCfg.routing_enabled === expectEnabled, `backend config routing_enabled now ${expectEnabled} (got ${savedCfg.routing_enabled})`);
    await page.getByRole("button", { name: new RegExp(`^${flippedLabel}$`) }).first().waitFor({ state: "visible", timeout: 10000 });
    assert(true, `UI toggle reflects backend state after save ("${flippedLabel}")`);
    report.steps.push("1.5-routing-saved");
    await shot(page, "02-routing-flipped");

    // RESTORE original value (no side effects left behind)
    CURRENT_PHASE = "1.5-routing-restore";
    await page.getByRole("button", { name: new RegExp(`^${flippedLabel}$`) }).first().click();
    const [restoreRes] = await Promise.all([
      page.waitForResponse((r) => r.url().includes("/api/firewall/config/") && r.request().method() === "PUT", { timeout: 30000 }),
      page.getByRole("button", { name: /save routing/i }).click(),
    ]);
    assert(restoreRes.ok(), `restore routing -> 2xx (got ${restoreRes.status()})`);
    const restoredCfg = await restoreRes.json().catch(() => ({}));
    assert(restoredCfg.routing_enabled === (originalLabel === "Enabled"), "backend config restored to original routing_enabled");
    report.steps.push("1.5-routing-restored");

    // ───────── Module 1.6: isolation & kill-switch lane ─────────
    CURRENT_PHASE = "1.6-panels";
    await page.goto(`${BASE}/?tab=firewall-1-6`, { waitUntil: "domcontentloaded", timeout: 120000 });
    await page.locator("text=/model state & risk monitor/i").first().waitFor({ state: "visible", timeout: 30000 });
    await page.locator("text=/kill-switch management/i").first().waitFor({ state: "visible", timeout: 30000 });
    assert(true, "1.6 renders ModelState + KillSwitch panels");
    report.steps.push("1.6-panels-render");
    await shot(page, "03-module-1-6");

    // create a kill-switch (disable) against a connected model
    CURRENT_PHASE = "1.6-ks-create";
    // pre-clean: a model that already has a kill-switch at this scope is hidden in the create modal,
    // so remove any leftover for our target first (keeps this gate re-runnable).
    const authHeader = { Authorization: `Bearer ${await page.evaluate(() => localStorage.getItem("auth_access"))}` };
    {
      const r = await page.request.get(`${BASE}/api/kill-switches/`, { headers: authHeader });
      const d = await r.json().catch(() => ({}));
      const arr = Array.isArray(d) ? d : d.results || [];
      for (const k of arr.filter((k) => k.model_name === firstModel)) {
        await page.request.delete(`${BASE}/api/kill-switches/${k.id}/`, { headers: authHeader });
      }
      if (arr.some((k) => k.model_name === firstModel)) await page.reload({ waitUntil: "domcontentloaded" });
    }
    await page.getByRole("button", { name: /create kill-switch/i }).first().click();
    // modal opens + fetches connected models
    await page.locator("text=/create kill-switch/i").first().waitFor({ state: "visible", timeout: 15000 });
    await page.waitForResponse((r) => r.url().includes("/api/firewall/models/") && r.request().method() === "GET", { timeout: 30000 }).catch(() => null);
    // target-model picker is the KillSwitchModelCombobox inside the modal (placeholder is unique to it);
    // a bare getByRole("combobox") would match a native <select> on the page behind the modal backdrop.
    const combo = page.getByPlaceholder(/search connected models/i).first();
    // modal renders the combobox only after its connected-models GET resolves; under a loaded
    // stack that can exceed 15s, so match the 30s used elsewhere in this lane (avoids false-fail).
    await combo.waitFor({ state: "visible", timeout: 30000 });
    await combo.click();
    await combo.fill(firstModel);
    // scope to the combobox's own listbox (<ul role=listbox>); native <select> options also expose role=option.
    const option = page
      .getByRole("listbox")
      .getByRole("option", { name: new RegExp(firstModel.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "i") })
      .first();
    await option.waitFor({ state: "visible", timeout: 10000 });
    await option.click();
    // action defaults to "disable"
    const [createRes] = await Promise.all([
      page.waitForResponse((r) => r.url().includes("/api/kill-switches/") && r.request().method() === "POST", { timeout: 30000 }),
      page.getByRole("button", { name: /^create$/i }).click(),
    ]);
    assert(createRes.ok(), `create kill-switch -> 2xx (got ${createRes.status()})`);
    const created = await createRes.json().catch(() => ({}));
    const ksId = created.id;
    assert(!!ksId, "create response returns a kill-switch id");
    report.steps.push("1.6-ks-created");

    // row appears; badge matches the backend is_active (honesty: badge mirrors truth).
    // scope to the KILL-SWITCH table row (its controls carry "kill switch for <model>" aria-labels) —
    // the ModelState panel above also renders a row for the same model.
    CURRENT_PHASE = "1.6-ks-row";
    const row = page
      .locator("tr", { hasText: firstModel })
      .filter({ has: page.locator(`button[aria-label*="kill switch for ${firstModel}" i]`) })
      .first();
    await row.waitFor({ state: "visible", timeout: 15000 });
    const wantActive = created.is_active === true;
    if (wantActive) {
      assert(await row.locator("text=/active/i").first().isVisible(), "new kill-switch row shows Active badge (matches backend is_active=true)");
    } else {
      assert(await row.locator("text=/inactive/i").first().isVisible(), "new kill-switch row shows Inactive badge (matches backend is_active=false)");
    }
    await shot(page, "04-ks-created");

    // helper to read backend truth for this switch
    const ksState = async () => {
      const r = await page.request.get(`${BASE}/api/kill-switches/`, {
        headers: { Authorization: `Bearer ${await page.evaluate(() => localStorage.getItem("auth_access"))}` },
      });
      const data = await r.json().catch(() => ({}));
      const arr = Array.isArray(data) ? data : data.results || [];
      return arr.find((k) => k.id === ksId);
    };

    // toggle: ensure we exercise BOTH deactivate -> Inactive and activate -> Active
    CURRENT_PHASE = "1.6-ks-toggle";
    if (wantActive) {
      // deactivate
      await Promise.all([
        page.waitForResponse((r) => /\/api\/kill-switches\/[^/]+\/deactivate\/?$/.test(r.url()) && r.request().method() === "POST", { timeout: 30000 }),
        row.getByRole("button", { name: new RegExp(`deactivate kill switch for ${firstModel}`, "i") }).click(),
      ]);
      await row.locator("text=/inactive/i").first().waitFor({ state: "visible", timeout: 10000 });
      assert((await ksState())?.is_active === false, "after deactivate: backend is_active=false (UI shows Inactive)");
    }
    // activate
    await Promise.all([
      page.waitForResponse((r) => /\/api\/kill-switches\/[^/]+\/activate\/?$/.test(r.url()) && r.request().method() === "POST", { timeout: 30000 }),
      row.getByRole("button", { name: new RegExp(`activate kill switch for ${firstModel}`, "i") }).click(),
    ]);
    await row.locator("text=/\\bactive\\b/i").first().waitFor({ state: "visible", timeout: 10000 });
    assert((await ksState())?.is_active === true, "after activate: backend is_active=true (UI shows Active)");
    // deactivate again so we have proven both directions
    await Promise.all([
      page.waitForResponse((r) => /\/api\/kill-switches\/[^/]+\/deactivate\/?$/.test(r.url()) && r.request().method() === "POST", { timeout: 30000 }),
      row.getByRole("button", { name: new RegExp(`deactivate kill switch for ${firstModel}`, "i") }).click(),
    ]);
    await row.locator("text=/inactive/i").first().waitFor({ state: "visible", timeout: 10000 });
    assert((await ksState())?.is_active === false, "deactivate round-trip confirmed (backend is_active=false)");
    report.steps.push("1.6-ks-toggled");
    await shot(page, "05-ks-toggled");

    // delete (cleanup)
    CURRENT_PHASE = "1.6-ks-delete";
    const [delRes] = await Promise.all([
      page.waitForResponse((r) => /\/api\/kill-switches\/[^/]+\/?$/.test(r.url()) && r.request().method() === "DELETE", { timeout: 30000 }),
      row.getByRole("button", { name: new RegExp(`delete kill switch for ${firstModel}`, "i") }).click(),
    ]);
    assert(delRes.ok() || delRes.status() === 204, `delete kill-switch -> 2xx/204 (got ${delRes.status()})`);
    await row.waitFor({ state: "detached", timeout: 15000 }).catch(() => {});
    assert((await ksState()) === undefined, "deleted kill-switch is gone from the backend listing (cleanup)");
    report.steps.push("1.6-ks-deleted");
    await shot(page, "06-ks-deleted");

    // ───────── final: no uncaught JS errors ─────────
    CURRENT_PHASE = "final";
    assert(report.pageErrors.length === 0, `no uncaught JS errors (found ${report.pageErrors.length})`);

    report.ok = true;
    console.log("OK D2 model/routing/kill-switch flow:", report.steps.join(" → "));
  } catch (e) {
    report.error = e.message;
    console.log("FAIL D2 model/routing:", e.message);
    try { await shot(page, "ZZ-failure"); } catch {}
  } finally {
    await browser.close();
    fs.writeFileSync(OUT, JSON.stringify(report, null, 2));
    console.log("Report:", OUT, "| asserts:", report.asserts.filter((a) => a.pass).length + "/" + report.asserts.length);
    process.exit(report.ok ? 0 : 1);
  }
}

main().catch((e) => { console.error(e); process.exit(1); });
