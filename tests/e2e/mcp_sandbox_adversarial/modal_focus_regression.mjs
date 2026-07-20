/**
 * Regression: Register MCP Server modal must NOT lose input focus per keystroke.
 *
 * Root cause (fixed in frontend/src/components/ui/Dialog.jsx): the Dialog
 * focus-trap useEffect depended on `handleKey`, which depended on the inline
 * `onClose` prop (new identity every render). Every keystroke -> setAddForm ->
 * re-render -> effect teardown+setup -> cleanup refocus + autofocus stole focus
 * from the field being typed in. Only the first character of any word survived.
 *
 * This test types multi-character strings one character at a time (real key
 * events) into the Name field and Description textarea, then asserts the FULL
 * value landed and the field still holds focus. Pre-fix this fails (name="Z").
 *
 * Run: BASE_URL=http://127.0.0.1:8180 node tests/e2e/mcp_sandbox_adversarial/modal_focus_regression.mjs
 */
import { chromium } from "playwright";

const BASE = (process.env.BASE_URL || "http://127.0.0.1:8180").replace(/\/$/, "");
const EMAIL = process.env.TEST_EMAIL || "admin@zeroshield.io";
const PASS = process.env.TEST_PASSWORD || "Adm1n!Pass#2024";

const NAME_SAMPLE = "Zebra-Focus-Probe-123";
const DESC_SAMPLE = "multi word description stays focused";

function assert(cond, msg) {
  if (!cond) throw new Error(`ASSERT FAILED: ${msg}`);
}

async function login(page) {
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle", timeout: 120000 });
  // Already authenticated (persisted session) -> app redirects away from /login.
  if (page.url().includes("/login")) {
    await page.locator("#email").fill(EMAIL);
    await page.locator("#password").fill(PASS);
    const [res] = await Promise.all([
      page.waitForResponse(
        (r) => r.url().includes("/api/auth/token/") && r.request().method() === "POST",
        { timeout: 120000 }
      ),
      page.getByRole("button", { name: /^sign in$/i }).click(),
    ]);
    assert(res.ok(), `login failed: ${res.status()}`);
    await page.waitForURL((u) => !u.pathname.includes("/login"), { timeout: 120000 });
  }
}

async function typeCharByChar(page, selector, text) {
  const el = page.locator(selector);
  await el.click();
  // pressSequentially dispatches a real keydown/keypress/input per character —
  // the exact interaction that re-triggered the Dialog effect pre-fix.
  await el.pressSequentially(text, { delay: 15 });
}

async function main() {
  const browser = await chromium.launch();
  const ctx = await browser.newContext();
  const page = await ctx.newPage();
  const report = { steps: [], ok: false };
  try {
    await login(page);
    report.steps.push("login");

    await page.goto(`${BASE}/?tab=firewall-1-4`, { waitUntil: "load", timeout: 180000 });
    await page.getByRole("button", { name: /register server/i }).first().click();
    await page.locator('[role="dialog"] input[placeholder="my-mcp-server"]').waitFor({ timeout: 30000 });
    report.steps.push("modal-open");

    // Name field (input) — the field the bug report used.
    await typeCharByChar(page, '[role="dialog"] input[placeholder="my-mcp-server"]', NAME_SAMPLE);
    const nameState = await page.evaluate(() => {
      const dlg = document.querySelector('[role="dialog"]');
      const el = dlg?.querySelector('input[placeholder="my-mcp-server"]');
      return { value: el?.value, focused: document.activeElement === el, ae: document.activeElement?.tagName };
    });
    report.nameState = nameState;
    assert(nameState.value === NAME_SAMPLE, `name value "${nameState.value}" != "${NAME_SAMPLE}" (focus stolen per keystroke)`);
    assert(nameState.focused, `name input lost focus; activeElement=${nameState.ae}`);
    report.steps.push("name-preserved");

    // Description field (textarea) — proves the fix is field-agnostic.
    await typeCharByChar(page, '[role="dialog"] textarea[placeholder="Optional description"]', DESC_SAMPLE);
    const descState = await page.evaluate(() => {
      const dlg = document.querySelector('[role="dialog"]');
      const el = dlg?.querySelector('textarea[placeholder="Optional description"]');
      return { value: el?.value, focused: document.activeElement === el, ae: document.activeElement?.tagName };
    });
    report.descState = descState;
    assert(descState.value === DESC_SAMPLE, `desc value "${descState.value}" != "${DESC_SAMPLE}"`);
    assert(descState.focused, `desc textarea lost focus; activeElement=${descState.ae}`);
    report.steps.push("desc-preserved");

    report.ok = true;
    console.log("PASS modal_focus_regression", JSON.stringify(report, null, 2));
  } catch (e) {
    report.error = String(e && e.message ? e.message : e);
    console.error("FAIL modal_focus_regression", JSON.stringify(report, null, 2));
    process.exitCode = 1;
  } finally {
    await browser.close();
  }
}

main();
