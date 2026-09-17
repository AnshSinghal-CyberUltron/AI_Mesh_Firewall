/**
 * T01 L01-1/2/3 Playwright against baked nginx :8180.
 * Real login form (not localStorage). Never logs passwords.
 *
 *   NODE_PATH="$PWD/tests/e2e/node_modules" BASE_URL=http://127.0.0.1:8180 \
 *     TEST_EMAIL=... TEST_PASSWORD=... node docs/plans/evidence/2026-09-17-t01/l01_browser.mjs
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
const OUT = process.env.E2E_REPORT || path.join(HERE, "l01_browser.json");
const SHOTS = process.env.SHOT_DIR || path.join(HERE, "shots");

const report = {
  task: "T01",
  base: BASE,
  email: EMAIL,
  started_at: new Date().toISOString(),
  html_has_vite_client: null,
  hashed_js: null,
  hashed_css: null,
  ok: false,
  steps: [],
  asserts: [],
  pageErrors: [],
  console_errors: [],
  network: [],
  gates: { "L01-1": { pass: false }, "L01-2": { pass: false }, "L01-3": { pass: false } },
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

function rec(res) {
  const h = res.headers();
  return {
    method: res.request().method(),
    url: res.url(),
    status: res.status(),
    x_request_id: h["x-request-id"] || h["X-Request-ID"] || null,
  };
}

async function main() {
  if (!EMAIL || !PASS) throw new Error("TEST_EMAIL and TEST_PASSWORD required");
  fs.mkdirSync(SHOTS, { recursive: true });

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
  page.on("response", (r) => {
    const url = r.url();
    if (
      url.includes("/api/firewall/config/") ||
      url.includes("/api/auth/") ||
      url.includes("/api/policies/")
    ) {
      report.network.push(rec(r));
    }
  });

  try {
    await page.goto(`${BASE}/login`, { waitUntil: "networkidle", timeout: 120000 });
    const html = await page.content();
    report.html_has_vite_client = html.includes("@vite/client") || html.includes("/@vite/");
    report.hashed_js = (html.match(/\/assets\/index-[A-Za-z0-9_-]+\.js/) || [null])[0];
    report.hashed_css = (html.match(/\/assets\/index-[A-Za-z0-9_-]+\.css/) || [null])[0];
    await shot(page, "01-login");
    assert(report.html_has_vite_client === false, "baked console has no Vite client");
    assert(Boolean(report.hashed_js), "hashed JS asset present");

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
    report.login_x_request_id = (await loginRes.headerValue("x-request-id").catch(() => null));
    await page.waitForFunction(() => !location.pathname.startsWith("/login"), { timeout: 60000 });
    report.steps.push("login-ok");
    await shot(page, "02-after-login");

    await page.goto(`${BASE}/?tab=firewall-config`, { waitUntil: "domcontentloaded", timeout: 120000 });
    await page.getByText("Enforcement Mode", { exact: false }).first().waitFor({ state: "visible", timeout: 60000 });
    report.steps.push("config-page-visible");
    await shot(page, "03-config-loaded");

    // --- L01-1: enforcement save/reload ---
    const modeSelect = page.getByLabel("Enforcement Mode");
    await modeSelect.waitFor({ state: "visible", timeout: 30000 });
    const beforeMode = await modeSelect.inputValue();
    report.gates["L01-1"].before = beforeMode;
    const targetMode = beforeMode === "monitor" ? "block" : "monitor";
    await modeSelect.selectOption(targetMode);
    await page.waitForTimeout(400);
    const [putMode] = await Promise.all([
      page.waitForResponse(
        (r) => r.url().includes("/api/firewall/config/") && r.request().method() === "PUT",
        { timeout: 30000 },
      ),
      page.getByRole("button", { name: /Save Configuration/i }).click(),
    ]);
    assert(putMode.status() === 200, `L01-1 PUT save HTTP ${putMode.status()}`);
    report.gates["L01-1"].put_status = putMode.status();
    report.gates["L01-1"].put_x_request_id = await putMode.headerValue("x-request-id").catch(() => null);
    let putModeBody = null;
    try {
      putModeBody = await putMode.json();
    } catch {}
    report.gates["L01-1"].put_enforcement_mode = putModeBody?.enforcement_mode;
    await shot(page, "04-l01-1-after-save");

    await page.reload({ waitUntil: "domcontentloaded", timeout: 120000 });
    await page.getByText("Enforcement Mode", { exact: false }).first().waitFor({ state: "visible", timeout: 60000 });
    const mode2 = page.getByLabel("Enforcement Mode");
    await mode2.waitFor({ state: "visible", timeout: 30000 });
    const reloadedMode = await mode2.inputValue();
    report.gates["L01-1"].after_reload = reloadedMode;
    assert(reloadedMode === targetMode, `L01-1 reload enforcement ${reloadedMode} === ${targetMode}`);
    report.gates["L01-1"].pass = true;
    report.steps.push("l01-1-save-reload");
    await shot(page, "05-l01-1-after-reload");

    // restore enforcement to snapshot block for remaining gates
    if (reloadedMode !== "block") {
      await mode2.selectOption("block");
      await Promise.all([
        page.waitForResponse(
          (r) => r.url().includes("/api/firewall/config/") && r.request().method() === "PUT",
          { timeout: 30000 },
        ),
        page.getByRole("button", { name: /Save Configuration/i }).click(),
      ]);
      report.steps.push("l01-1-restored-block");
    }

    // --- L01-3: PII Detection save/reload A/B ---
    const piiSwitch = page.getByRole("switch", { name: /PII Detection/i }).first();
    await piiSwitch.scrollIntoViewIfNeeded();
    await piiSwitch.waitFor({ state: "visible", timeout: 30000 });
    const piiBefore = await piiSwitch.getAttribute("aria-checked");
    report.gates["L01-3"].before = piiBefore;
    const wantOn = piiBefore !== "true";
    await piiSwitch.click();
    await page.waitForTimeout(400);
    const piiToggled = await piiSwitch.getAttribute("aria-checked");
    assert(piiToggled !== piiBefore, `PII toggled ${piiBefore} -> ${piiToggled}`);
    const [putPii] = await Promise.all([
      page.waitForResponse(
        (r) => r.url().includes("/api/firewall/config/") && r.request().method() === "PUT",
        { timeout: 30000 },
      ),
      page.getByRole("button", { name: /Save Configuration/i }).click(),
    ]);
    assert(putPii.status() === 200, `L01-3 PUT HTTP ${putPii.status()}`);
    let putPiiBody = null;
    try {
      putPiiBody = await putPii.json();
    } catch {}
    report.gates["L01-3"].put_status = putPii.status();
    report.gates["L01-3"].put_pii_detection_enabled = putPiiBody?.pii_detection_enabled;
    report.gates["L01-3"].put_x_request_id = await putPii.headerValue("x-request-id").catch(() => null);
    await shot(page, "06-l01-3-after-save");

    await page.reload({ waitUntil: "domcontentloaded", timeout: 120000 });
    await page.getByRole("switch", { name: /PII Detection/i }).first().waitFor({ state: "visible", timeout: 60000 });
    const piiReloaded = await page.getByRole("switch", { name: /PII Detection/i }).first().getAttribute("aria-checked");
    report.gates["L01-3"].after_reload = piiReloaded;
    const expectedChecked = wantOn ? "true" : "false";
    assert(piiReloaded === expectedChecked, `L01-3 reload PII ${piiReloaded} === ${expectedChecked}`);
    report.gates["L01-3"].pass = true;
    report.steps.push("l01-3-save-reload");
    await shot(page, "07-l01-3-after-reload");

    // restore PII off to match snapshot
    const piiNow = page.getByRole("switch", { name: /PII Detection/i }).first();
    if ((await piiNow.getAttribute("aria-checked")) === "true") {
      await piiNow.click();
      await Promise.all([
        page.waitForResponse(
          (r) => r.url().includes("/api/firewall/config/") && r.request().method() === "PUT",
          { timeout: 30000 },
        ),
        page.getByRole("button", { name: /Save Configuration/i }).click(),
      ]);
      report.steps.push("l01-3-restored-pii-off");
    }

    await page
      .getByText("Configuration saved successfully — active via Redis hot-reload")
      .waitFor({ state: "hidden", timeout: 8000 })
      .catch(() => {});

    // --- L01-2: Rewrite is not offered; control still rejects API rewrite ---
    const guardHeading = page.getByRole("heading", { name: /Output Guardrail Controls/i });
    await guardHeading.scrollIntoViewIfNeeded();
    await guardHeading.waitFor({ state: "visible", timeout: 30000 });
    await page.getByText("PII / PD Leakage", { exact: false }).first().scrollIntoViewIfNeeded();
    await page.waitForTimeout(400);
    const rewriteCount = await page.getByRole("button", { name: /^Rewrite$/ }).count();
    report.gates["L01-2"].rewrite_button_count = rewriteCount;
    report.gates["L01-2"].action_button_counts = {
      Block: await page.getByRole("button", { name: /^Block$/ }).count(),
      Redact: await page.getByRole("button", { name: /^Redact$/ }).count(),
      Flag: await page.getByRole("button", { name: /^Flag$/ }).count(),
      Allow: await page.getByRole("button", { name: /^Allow$/ }).count(),
      Rewrite: rewriteCount,
    };
    assert(rewriteCount === 0, `L01-2 Rewrite pills still visible: ${rewriteCount}`);

    const apiRewrite = await page.evaluate(async () => {
      const bearer = localStorage.getItem("auth_access");
      const headers = { "Content-Type": "application/json", Accept: "application/json" };
      if (bearer) headers.Authorization = `Bearer ${bearer}`;
      const res = await fetch("/api/firewall/config/", {
        method: "PUT",
        credentials: "include",
        headers,
        body: JSON.stringify({ output_pii_action: "rewrite" }),
      });
      let body = null;
      try {
        body = await res.json();
      } catch {}
      return {
        status: res.status,
        x_request_id: res.headers.get("x-request-id"),
        body,
      };
    });
    report.gates["L01-2"].put_status = apiRewrite.status;
    report.gates["L01-2"].put_x_request_id = apiRewrite.x_request_id;
    report.gates["L01-2"].error_mentions_rewrite =
      JSON.stringify(apiRewrite.body || {}).toLowerCase().includes("rewrite");
    const staleBanner = await page
      .getByText("Configuration saved successfully — active via Redis hot-reload")
      .count();
    report.gates["L01-2"].stale_success_banner_count = staleBanner;
    const guardCard = page.locator("h3", { hasText: "Output Guardrail Controls" }).locator("xpath=ancestor::div[contains(@class,'rounded-xl')][1]");
    fs.mkdirSync(SHOTS, { recursive: true });
    await guardCard.screenshot({ path: path.join(SHOTS, "08-l01-2-rewrite-save.png") });
    await shot(page, "08b-l01-2-rewrite-fullpage");
    assert(apiRewrite.status === 400, `L01-2 rewrite PUT ${apiRewrite.status} expected 400`);
    assert(staleBanner === 0, "L01-2 stale configuration-saved banner still visible after rewrite 400");
    report.gates["L01-2"].pass = true;
    report.steps.push("l01-2-rewrite-not-offered-api-400");

    report.ok =
      report.gates["L01-1"].pass &&
      report.gates["L01-2"].pass &&
      report.gates["L01-3"].pass &&
      report.pageErrors.length === 0 &&
      report.html_has_vite_client === false;
  } catch (err) {
    report.error = String(err?.message || err);
    try {
      await shot(page, "99-failure");
    } catch {}
  } finally {
    report.finished_at = new Date().toISOString();
    await browser.close();
    fs.mkdirSync(path.dirname(OUT), { recursive: true });
    fs.writeFileSync(OUT, JSON.stringify(report, null, 2));
    console.log(
      JSON.stringify(
        {
          ok: report.ok,
          error: report.error,
          steps: report.steps,
          gates: report.gates,
          hashed_js: report.hashed_js,
          vite: report.html_has_vite_client,
        },
        null,
        2,
      ),
    );
    if (!report.ok) process.exit(1);
  }
}

main();
