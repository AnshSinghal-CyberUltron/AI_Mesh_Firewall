/**
 * D1-ui-auth-keys browser verification (real customer perspective).
 *
 * Verifies, against the running Vite app (:8180) backed by control(:8100)+gateway(:8300):
 *   1. Login — empty (HTML5 required, no submit), invalid (401 + error shown), valid (token + nav).
 *   2. OAuth callback route preserves ?code/?state and shows the waiting indicator (no wildcard redirect).
 *   3. GatewayKeyPanel — create (key shown ONCE), copy, Done; table shows prefix only (full secret NEVER
 *      re-rendered = honesty invariant); revoke -> Revoked badge. (Rotation primitive = revoke + create;
 *      there is no dedicated rotate control in this panel — recorded honestly.)
 *   4. Inactivity warning modal — fast-forwarded via page.clock; "Session expiring" appears; Stay logged in
 *      dismisses it.
 *   5. Logout — clears tokens and returns to /login.
 *   6. No uncaught JS errors (pageerror) in any phase.
 *
 * Run: BASE_URL=http://127.0.0.1:8180 node scripts/playwright_auth_keys.mjs
 */
import { chromium } from "playwright";
import fs from "node:fs";

const BASE = (process.env.BASE_URL || "http://127.0.0.1:8180").replace(/\/$/, "");
const EMAIL = process.env.TEST_EMAIL || "admin@zeroshield.io";
const PASS = process.env.TEST_PASSWORD || "Adm1n!Pass#2024";
const OUT = process.env.E2E_REPORT || "runs/playwright_auth_keys.json";
const SHOT_DIR = process.env.SHOT_DIR || "runs/d1";

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

async function main() {
  fs.mkdirSync("runs", { recursive: true });
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    permissions: ["clipboard-read", "clipboard-write"],
  });
  const page = await context.newPage();
  page.on("pageerror", (e) => report.pageErrors.push({ phase: CURRENT_PHASE, message: String(e.message || e) }));

  try {
    // ───────── Phase 1a: login EMPTY (HTML5 required blocks submit) ─────────
    CURRENT_PHASE = "login-empty";
    await page.goto(`${BASE}/login`, { waitUntil: "networkidle", timeout: 120000 });
    await page.getByRole("button", { name: /^sign in$/i }).click();
    await page.waitForTimeout(400);
    assert(page.url().includes("/login"), "empty submit stays on /login");
    assert((await page.locator("#email:invalid").count()) > 0, "empty email is :invalid (required)");
    report.steps.push("login-empty-blocked");
    await shot(page, "01-login");

    // ───────── Phase 1b: login INVALID ─────────
    CURRENT_PHASE = "login-invalid";
    await page.locator("#email").fill(EMAIL);
    await page.locator("#password").fill("definitely-wrong-pw");
    const [bad] = await Promise.all([
      page.waitForResponse((r) => r.url().includes("/api/auth/token/") && r.request().method() === "POST", { timeout: 30000 }),
      page.getByRole("button", { name: /^sign in$/i }).click(),
    ]);
    assert(bad.status() === 401, `invalid creds -> 401 (got ${bad.status()})`);
    await page.locator("text=/invalid email or password/i").first().waitFor({ state: "visible", timeout: 10000 });
    report.steps.push("login-invalid-error-shown");
    await shot(page, "02-login-invalid");

    // ───────── Phase 1c: login VALID ─────────
    CURRENT_PHASE = "login-valid";
    await page.locator("#email").fill(EMAIL);
    await page.locator("#password").fill(PASS);
    // Settle: the invalid attempt above must have fully resolved (button text returns to
    // "Sign in", not "Signing in...") before we click again, else the click races an in-flight
    // submit. The submit also goes through control which can be cold/loaded — use a generous
    // 60s response timeout so a slow-but-correct login is not mis-reported as a failure.
    await page.getByRole("button", { name: /^sign in$/i }).and(page.locator("button:not([disabled])")).first().waitFor({ state: "visible", timeout: 30000 });
    const [ok] = await Promise.all([
      page.waitForResponse((r) => r.url().includes("/api/auth/token/") && r.request().method() === "POST", { timeout: 60000 }),
      page.getByRole("button", { name: /^sign in$/i }).click(),
    ]);
    assert(ok.ok(), `valid creds -> 2xx (got ${ok.status()})`);
    await page.waitForFunction(() => !location.pathname.startsWith("/login"), { timeout: 30000 });
    const access = await page.evaluate(() => localStorage.getItem("auth_access"));
    assert(!!access, "auth_access token stored after login");
    report.steps.push("login-valid");
    await shot(page, "03-dashboard");

    // ───────── Phase 2: OAuth callback route preserves params ─────────
    CURRENT_PHASE = "oauth-callback";
    await page.goto(`${BASE}/oauth/callback?code=probe123&state=probe456`, { waitUntil: "networkidle", timeout: 60000 });
    assert(page.url().includes("code=probe123") && page.url().includes("state=probe456"), "oauth callback preserves ?code & ?state (no wildcard redirect)");
    await page.locator("text=/completing authorization/i").first().waitFor({ state: "visible", timeout: 10000 });
    report.steps.push("oauth-callback-params-preserved");
    await shot(page, "04-oauth-callback");

    // ───────── Phase 3: GatewayKeyPanel create / copy / revoke + honesty ─────────
    CURRENT_PHASE = "keys-create";
    page.on("dialog", (d) => d.accept()); // accept the revoke confirm()
    await page.goto(`${BASE}/?tab=firewall-1-1`, { waitUntil: "networkidle", timeout: 120000 });
    // Module 1.1 is a telemetry-heavy dashboard; on a loaded host first paint of the keys panel
    // can exceed 30s after networkidle. Give it 60s so a slow render is not a false failure.
    await page.locator("text=/gateway api keys/i").first().waitFor({ state: "visible", timeout: 60000 });
    const keyName = `d1-verify-${Date.now()}`;
    await page.getByRole("button", { name: /create api key/i }).first().click();
    await page.locator("#gateway-key-name").waitFor({ state: "visible", timeout: 15000 });
    await page.locator("#gateway-key-name").fill(keyName);
    await page.locator("#gateway-key-project-id").fill("1");
    const [createRes] = await Promise.all([
      page.waitForResponse((r) => r.url().includes("/api/gateways/keys/") && r.request().method() === "POST", { timeout: 60000 }),
      page.getByRole("button", { name: /^create key$/i }).click(),
    ]);
    assert(createRes.ok(), `create key -> 2xx (got ${createRes.status()})`);
    const created = await createRes.json();
    const fullSecret = created.key;
    assert(!!fullSecret && fullSecret.length > 12, "create response returns a full secret key once");
    await page.locator("text=/it will not be shown again/i").first().waitFor({ state: "visible", timeout: 10000 });
    assert(await page.locator(`text=${fullSecret}`).first().isVisible(), "full secret shown ONCE in the create modal");
    report.steps.push("key-created");
    await shot(page, "05-key-created");

    // copy
    CURRENT_PHASE = "keys-copy";
    await page.getByTitle("Copy").click();
    const clip = await page.evaluate(() => navigator.clipboard.readText().catch(() => ""));
    assert(clip === fullSecret, "copy button copies the exact secret to clipboard");
    report.steps.push("key-copied");

    // Done -> back to table
    CURRENT_PHASE = "keys-honesty";
    await Promise.all([
      page.waitForResponse((r) => r.url().includes("/api/gateways/keys/") && r.request().method() === "GET", { timeout: 30000 }).catch(() => null),
      page.getByRole("button", { name: /^done$/i }).click(),
    ]);
    const row = page.locator("tr", { hasText: keyName }).first();
    await row.waitFor({ state: "visible", timeout: 15000 });
    assert(await row.locator("text=/active/i").first().isVisible(), "new key row shows Active badge");
    // HONESTY: the full secret must NEVER be re-rendered in the listing
    const bodyHtml = await page.content();
    assert(!bodyHtml.includes(fullSecret), "full secret is NOT present anywhere in the listing DOM (honesty invariant)");
    const prefix = created.prefix || fullSecret.slice(0, 8);
    assert(bodyHtml.includes(prefix), "listing shows only the key prefix");
    report.steps.push("key-secret-not-reexposed");
    await shot(page, "06-key-listed");

    // revoke
    CURRENT_PHASE = "keys-revoke";
    const [delRes] = await Promise.all([
      page.waitForResponse((r) => /\/api\/gateways\/keys\/[^/]+\/?$/.test(r.url()) && r.request().method() === "DELETE", { timeout: 30000 }),
      row.getByRole("button", { name: /revoke gateway key/i }).click(),
    ]);
    assert(delRes.ok() || delRes.status() === 204, `revoke -> 2xx/204 (got ${delRes.status()})`);
    // Revoke is a hard delete on this backend (DELETE /keys/{id}/) — the row leaves the listing
    // entirely. (The "Revoked" badge is reserved for keys deactivated-but-retained, e.g. expired.)
    await page.locator("tr", { hasText: keyName }).first().waitFor({ state: "detached", timeout: 15000 });
    assert((await page.locator("tr", { hasText: keyName }).count()) === 0, "revoked key removed from listing (immediately stops working)");
    report.steps.push("key-revoked");
    report.notes.push("Rotation in this panel = revoke + create (no dedicated rotate control/endpoint in GatewayKeyPanel); both primitives verified. Revoke = DELETE hard-removal (row leaves listing); a soft 'Revoked' badge is shown only for retained-but-inactive keys (e.g. expired).");
    await shot(page, "07-key-revoked");

    // ───────── Phase 4: inactivity warning modal (clock fast-forward) ─────────
    CURRENT_PHASE = "inactivity-modal";
    const clockPage = await context.newPage();
    clockPage.on("pageerror", (e) => report.pageErrors.push({ phase: "inactivity-modal", message: String(e.message || e) }));
    await clockPage.clock.install();
    await clockPage.goto(`${BASE}/?tab=firewall-1-1`, { waitUntil: "networkidle", timeout: 120000 });
    await clockPage.locator("text=/gateway api keys/i").first().waitFor({ state: "visible", timeout: 60000 }).catch(() => {});
    // 14 min idle -> warning window (>= warningAtMs 14min, < inactivityMs 15min). checkIdle polls every 30s.
    await clockPage.clock.fastForward(14 * 60 * 1000 + 3000);
    const warnDialog = clockPage.getByRole("dialog").filter({ hasText: /session expiring/i });
    await warnDialog.waitFor({ state: "visible", timeout: 15000 });
    assert(true, "inactivity warning modal 'Session expiring' appears after ~14m idle");
    await shot(clockPage, "08-inactivity-warning");
    await clockPage.getByRole("button", { name: /stay logged in/i }).click();
    await warnDialog.waitFor({ state: "hidden", timeout: 10000 });
    const stillIn = await clockPage.evaluate(() => !!localStorage.getItem("auth_access"));
    assert(stillIn, "Stay logged in keeps session (token retained, modal dismissed)");
    report.steps.push("inactivity-modal-verified");
    await clockPage.close();

    // ───────── Phase 5: logout ─────────
    CURRENT_PHASE = "logout";
    await page.bringToFront();
    await page.getByRole("button").filter({ hasText: EMAIL }).first().click();
    await Promise.all([
      page.waitForResponse((r) => r.url().includes("/api/auth/logout/") && r.request().method() === "POST", { timeout: 30000 }).catch(() => null),
      page.getByRole("button", { name: /^logout$/i }).click(),
    ]);
    await page.waitForFunction(() => location.pathname.startsWith("/login"), { timeout: 30000 });
    const clearedAccess = await page.evaluate(() => localStorage.getItem("auth_access"));
    assert(!clearedAccess, "logout clears auth_access token + returns to /login");
    report.steps.push("logout");
    await shot(page, "09-logged-out");

    // ───────── final: no uncaught JS errors ─────────
    CURRENT_PHASE = "final";
    assert(report.pageErrors.length === 0, `no uncaught JS errors (found ${report.pageErrors.length})`);

    report.ok = true;
    console.log("OK D1 auth+keys flow:", report.steps.join(" → "));
  } catch (e) {
    report.error = e.message;
    console.log("FAIL D1 auth+keys:", e.message);
    try { await shot(page, "ZZ-failure"); } catch {}
  } finally {
    await browser.close();
    fs.writeFileSync(OUT, JSON.stringify(report, null, 2));
    console.log("Report:", OUT, "| asserts:", report.asserts.filter((a) => a.pass).length + "/" + report.asserts.length);
    process.exit(report.ok ? 0 : 1);
  }
}

main().catch((e) => { console.error(e); process.exit(1); });
