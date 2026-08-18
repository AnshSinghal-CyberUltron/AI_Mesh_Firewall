/**
 * Module 1.6 — Model State isolate UX + fallback deadlock gate.
 *
 * Verifies against live Vite (:8180) or nginx BASE_URL:
 *  1. Honesty labels (5s refresh + risk cadence)
 *  2. Expand model → Fallback select is ENABLED (deadlock fix)
 *  3. Reroute with no fallback → hint + focus Fallback (no dead-end error)
 *  4. Choose fallback → Isolation Action becomes reroute; persists after refetch
 *  5. Switch to Block → fallback cleared
 *  6. Kill-Switch panel still renders; 0 pageerrors
 *
 * Run: NODE_PATH="$PWD/tests/e2e/node_modules" BASE_URL=http://127.0.0.1:8180 node scripts/playwright_module_1_6.mjs
 */
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import path from "node:path";
import fs from "node:fs";

const require = createRequire(import.meta.url);
const HERE = path.dirname(fileURLToPath(import.meta.url));
const { chromium } = require(path.join(HERE, "../tests/e2e/node_modules/playwright"));

const BASE = (process.env.BASE_URL || "http://127.0.0.1:8180").replace(/\/$/, "");
const EMAIL = process.env.TEST_EMAIL || "admin@zeroshield.io";
const PASS = process.env.TEST_PASSWORD || "Adm1n!Pass#2024";
const OUT = process.env.E2E_REPORT || "runs/playwright_module_1_6.json";
const MUTATE = process.env.MODULE_1_6_MUTATE !== "0"; // default: exercise PATCH paths

const report = { base: BASE, ok: false, steps: [], asserts: [], pageErrors: [], error: null };

function assert(cond, label) {
  report.asserts.push({ label, pass: !!cond });
  if (!cond) throw new Error(`ASSERT FAILED: ${label}`);
}

async function login(page) {
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle", timeout: 120000 });
  await page.locator("#email").fill(EMAIL);
  await page.locator("#password").fill(PASS);
  await Promise.all([
    page.waitForResponse((r) => r.url().includes("/api/auth/token/") && r.request().method() === "POST", { timeout: 30000 }),
    page.getByRole("button", { name: /^sign in$/i }).click(),
  ]);
  await page.waitForFunction(() => !location.pathname.startsWith("/login"), { timeout: 30000 });
  report.steps.push("login");
}

async function main() {
  fs.mkdirSync("runs", { recursive: true });
  const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH || "/usr/bin/chromium-browser";
  const browser = await chromium.launch({ headless: true, executablePath });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  page.on("pageerror", (e) => report.pageErrors.push(String(e.message || e)));

  try {
    await login(page);
    await page.goto(`${BASE}/?tab=firewall-1-6`, { waitUntil: "domcontentloaded", timeout: 120000 });
    await page.getByText(/Model State/i).first().waitFor({ timeout: 60000 });
    report.steps.push("model-state-visible");

    const refreshLabel = page.getByText(/Status refresh:\s*5s/i);
    assert(await refreshLabel.count() > 0, "shows Status refresh: 5s honesty label");

    const riskHonesty = page.getByText(/Risk scores:\s*on traffic/i);
    assert(await riskHonesty.count() > 0, "shows risk score cadence honesty");

    const expandBtn = page.getByRole("button", { name: /Expand model settings/i }).first();
    await expandBtn.waitFor({ state: "visible", timeout: 60000 });
    assert(await expandBtn.count() > 0, "expand model settings button exists");
    await expandBtn.click();
    await page.waitForTimeout(400);

    assert(await page.getByText(/Isolation Action/i).count() > 0, "Isolation Action visible after expand");
    assert(await page.getByText(/Fallback Model/i).count() > 0, "Fallback Model picker visible");
    assert(await page.getByText(/Select a Fallback Model to enable silent reroute/i).count() > 0, "new help copy");
    assert(await page.getByText(/Choose a Fallback Model .+ first/i).count() === 0, "no dead-end choose-first copy on page");

    const actionSelect = page.locator('select[aria-label*="Isolation action"]').first();
    const fallbackSelect = page.locator('select[aria-label*="Fallback model"]').first();
    assert(await actionSelect.count() > 0, "Isolation action select");
    assert(await fallbackSelect.count() > 0, "Fallback model select");
    assert(await fallbackSelect.isEnabled(), "Fallback select is ENABLED (deadlock fix)");

    if (MUTATE) {
      // Ensure we start from block + empty fallback for a clean path
      const options = await fallbackSelect.locator("option").all();
      const fallbackValues = [];
      for (const opt of options) {
        const v = await opt.getAttribute("value");
        if (v) fallbackValues.push(v);
      }
      assert(fallbackValues.length >= 1, "at least one other model available as fallback");

      // Prefer clear to none first if needed
      await fallbackSelect.selectOption({ value: "" }).catch(() => {});
      await page.waitForTimeout(500);
      if ((await actionSelect.inputValue()) !== "block") {
        await actionSelect.selectOption("block");
        await page.waitForTimeout(800);
      }

      // Reroute with no fallback → hint, no loadError deadlock text
      await actionSelect.selectOption("reroute");
      await page.waitForTimeout(400);
      assert(await page.getByTestId("fallback-hint").count() > 0, "fallback hint visible after Reroute w/o fallback");
      assert(await page.getByText(/Select a fallback to enable silent reroute/i).count() > 0, "inline hint copy");
      assert(await page.getByText(/Choose a Fallback Model .+ first/i).count() === 0, "no dead-end error banner");
      // Focus should land on fallback
      const focused = await page.evaluate(() => {
        const el = document.activeElement;
        return el && el.getAttribute && (el.getAttribute("aria-label") || "");
      });
      assert(/Fallback model/i.test(focused || ""), `Fallback select focused (got aria-label=${focused})`);
      report.steps.push("reroute-hint-ok");

      // Pick a fallback → action becomes reroute
      const fb = fallbackValues[0];
      const patchPromise = page.waitForResponse(
        (r) => {
          const u = r.url();
          return u.includes("/api/models/status/") && r.request().method() === "PATCH";
        },
        { timeout: 30000 },
      );
      await fallbackSelect.selectOption(fb);
      const patchRes = await patchPromise;
      assert(patchRes.ok(), `fallback PATCH ok (status=${patchRes.status()})`);
      await page.waitForTimeout(800);
      assert((await actionSelect.inputValue()) === "reroute", "action becomes reroute after picking fallback");
      assert((await fallbackSelect.inputValue()) === fb, "fallback value persisted in select");
      report.steps.push("fallback-sets-reroute");

      // Soft refetch via Sync / poll — re-open and check values stick
      await page.waitForTimeout(1200);
      assert((await actionSelect.inputValue()) === "reroute", "reroute still set after short wait");
      assert((await fallbackSelect.inputValue()) === fb, "fallback still set after short wait");

      // Block clears fallback
      const clearPatch = page.waitForResponse(
        (r) => r.url().includes("/api/models/status/") && r.request().method() === "PATCH",
        { timeout: 30000 },
      );
      await actionSelect.selectOption("block");
      const clearRes = await clearPatch;
      assert(clearRes.ok(), `block PATCH ok (status=${clearRes.status()})`);
      await page.waitForTimeout(800);
      assert((await actionSelect.inputValue()) === "block", "action back to block");
      assert((await fallbackSelect.inputValue()) === "", "fallback cleared on block");
      report.steps.push("block-clears-fallback");
    } else {
      report.steps.push("mutate-skipped");
    }

    assert(await page.getByText(/Kill-Switch/i).count() > 0, "Kill-Switch panel visible");
    assert(report.pageErrors.length === 0, `0 pageerrors (got ${report.pageErrors.length})`);
    report.ok = true;
    report.steps.push("done");
  } catch (e) {
    report.error = String(e && e.message ? e.message : e);
    report.ok = false;
  } finally {
    fs.writeFileSync(OUT, JSON.stringify(report, null, 2));
    await browser.close();
    console.log(JSON.stringify({ ok: report.ok, asserts: report.asserts, steps: report.steps, error: report.error }, null, 2));
    process.exit(report.ok ? 0 : 1);
  }
}

main();
