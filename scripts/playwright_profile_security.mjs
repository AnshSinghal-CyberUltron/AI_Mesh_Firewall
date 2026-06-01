/**
 * Security-sensitive UI checks for Profile page.
 * - password visibility and lifecycle
 * - safe error message rendering
 * - hidden-field request handling
 *
 * Run:
 *   BASE_URL=http://127.0.0.1:8180 node scripts/playwright_profile_security.mjs
 */
import { chromium } from "playwright";
import fs from "node:fs";

const BASE = (process.env.BASE_URL || "http://127.0.0.1:8180").replace(/\/$/, "");
const EMAIL = process.env.TEST_EMAIL || "admin@zeroshield.io";
const PASS = process.env.TEST_PASSWORD || "Adm1n!Pass#2024";
const OUT = process.env.E2E_REPORT || "runs/playwright_profile_security.json";

function fail(message, findings) {
  findings.push({ ok: false, message });
  throw new Error(message);
}

async function login(page) {
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle", timeout: 120000 });
  await page.locator("#email").fill(EMAIL);
  await page.locator("#password").fill(PASS);
  const [res] = await Promise.all([
    page.waitForResponse((r) => r.url().includes("/api/auth/token/") && r.request().method() === "POST"),
    page.getByRole("button", { name: /^sign in$/i }).click(),
  ]);
  if (!res.ok()) throw new Error(`Login failed: ${res.status()}`);
}

async function runChecks(page) {
  const findings = [];

  await page.goto(`${BASE}/?tab=profile`, { waitUntil: "networkidle", timeout: 120000 });

  const emailInput = page.locator('input[type="email"]').first();
  const firstNameInput = page.locator('label:has-text("First Name") + input');
  const currentPasswordInput = page.locator('label:has-text("Current Password") + input').first();
  const saveProfileButton = page.getByRole("button", { name: "Save Profile" });
  const changePasswordButton = page.getByRole("button", { name: /change password/i });
  const changePasswordInputs = page.locator(
    'form:has(button:has-text("Change Password")) input[type="password"]'
  );

  // 1) Password visibility defaults: all password fields stay masked.
  const passwordTypeCount = await changePasswordInputs.count();
  if (passwordTypeCount !== 3) {
    fail(`Expected 3 masked password fields in Change Password form, found ${passwordTypeCount}`, findings);
  }
  findings.push({ ok: true, message: "Change Password fields are masked (type=password)." });

  const originalEmail = (await emailInput.inputValue()).trim();
  const updatedEmail = originalEmail.includes("+")
    ? originalEmail.replace("+", "+sec-")
    : originalEmail.replace("@", "+sec@");

  // 2) Hidden-field handling: current password appears only when email changes.
  if (await currentPasswordInput.isVisible().catch(() => false)) {
    fail("Current password field should be hidden when email is unchanged.", findings);
  }
  await emailInput.fill(updatedEmail);
  await currentPasswordInput.waitFor({ state: "visible", timeout: 10000 });
  const currentPasswordType = await currentPasswordInput.getAttribute("type");
  if (currentPasswordType !== "password") {
    fail(`Current password field type should be password, got "${currentPasswordType}"`, findings);
  }
  findings.push({ ok: true, message: "Current password field visibility toggles correctly for email changes." });

  // 3) Hidden-field lifecycle: value must clear when field is hidden.
  await currentPasswordInput.fill("SensitiveValue!123");
  await emailInput.fill(originalEmail);
  await currentPasswordInput.waitFor({ state: "hidden", timeout: 10000 });
  await emailInput.fill(updatedEmail);
  await currentPasswordInput.waitFor({ state: "visible", timeout: 10000 });
  const currentPasswordValue = await currentPasswordInput.inputValue();
  if (currentPasswordValue !== "") {
    fail("Current password value persisted after field hide/show; expected cleared value.", findings);
  }
  findings.push({ ok: true, message: "Current password value is cleared when field lifecycle hides/re-shows it." });

  // 4) Hidden-field request handling: hidden current_password must not be sent.
  let profilePayload = null;
  await page.route("**/api/auth/me/profile/", async (route) => {
    if (route.request().method() !== "PATCH") return route.continue();
    profilePayload = JSON.parse(route.request().postData() || "{}");
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: 1,
        email: originalEmail,
        first_name: "Secure",
        last_name: "User",
        roles: ["admin"],
        is_active: true,
        preferences: { theme: "system", email_notifications: true, security_alerts: true },
      }),
    });
  });
  await emailInput.fill(originalEmail);
  await firstNameInput.fill("Secure");
  await saveProfileButton.click();
  if (!profilePayload) {
    fail("Profile update request was not captured for hidden-field payload check.", findings);
  }
  if (Object.prototype.hasOwnProperty.call(profilePayload, "current_password")) {
    fail("Hidden field leak: request payload included current_password when email was unchanged.", findings);
  }
  findings.push({ ok: true, message: "Profile payload excludes hidden current_password when email is unchanged." });
  await page.unroute("**/api/auth/me/profile/");

  // 5) Error-message safety check: reject stack traces / SQL leakage in UI.
  await page.route("**/api/auth/change-password/", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    await route.fulfill({
      status: 400,
      contentType: "application/json",
      body: JSON.stringify({
        detail: "Traceback (most recent call last): database error in auth/views.py line 88",
      }),
    });
  });
  await page.locator('label:has-text("Current Password") + input').nth(1).fill("OldPassword!1");
  await page.locator('label:has-text("New Password") + input').fill("NewPassword!1");
  await page.locator('label:has-text("Confirm New Password") + input').fill("NewPassword!1");
  await changePasswordButton.click();
  const pageTextAfterError = await page.locator("body").innerText();
  if (/traceback|line \d+|auth\/views\.py|sql|database error/i.test(pageTextAfterError)) {
    fail("UI leaked backend-internal error details in password change flow.", findings);
  }
  findings.push({ ok: true, message: "Password-change error message does not expose backend internals." });
  await page.unroute("**/api/auth/change-password/");

  // 6) Password lifecycle: successful change clears all password fields.
  await page.route("**/api/auth/change-password/", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ detail: "ok" }),
    });
  });
  const oldPwd = page.locator('label:has-text("Current Password") + input').nth(1);
  const newPwd = page.locator('label:has-text("New Password") + input');
  const confirmPwd = page.locator('label:has-text("Confirm New Password") + input');
  await oldPwd.fill("OldPassword!1");
  await newPwd.fill("AnotherNew!123");
  await confirmPwd.fill("AnotherNew!123");
  await changePasswordButton.click();
  if ((await oldPwd.inputValue()) || (await newPwd.inputValue()) || (await confirmPwd.inputValue())) {
    fail("Password lifecycle failure: one or more password fields retained value after success.", findings);
  }
  findings.push({ ok: true, message: "Password fields are cleared after successful password change." });
  await page.unroute("**/api/auth/change-password/");

  return findings;
}

async function main() {
  fs.mkdirSync("runs", { recursive: true });
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  const report = { base: BASE, failed: 0, findings: [] };

  try {
    await login(page);
    report.findings = await runChecks(page);
  } catch (error) {
    report.failed = 1;
    report.findings.push({ ok: false, message: error.message });
  } finally {
    await browser.close();
  }

  if (report.findings.some((f) => !f.ok)) report.failed = 1;
  fs.mkdirSync(OUT.substring(0, OUT.lastIndexOf("/")) || ".", { recursive: true });
  fs.writeFileSync(OUT, JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
  console.log("Report:", OUT);
  process.exit(report.failed ? 1 : 0);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
