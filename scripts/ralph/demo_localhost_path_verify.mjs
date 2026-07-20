/**
 * Gate: /demo path prefix works on localhost AND 127.0.0.1 via Vite proxy.
 *
 * Proves:
 *   - bare /demo → 308/redirect → lands on /demo/
 *   - CSS loads under /demo/
 *   - login POST goes to /demo/api/login (never bare /api/login)
 *   - successful auth shows org badge
 *
 * Run:
 *   PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium-browser \
 *   node scripts/ralph/demo_localhost_path_verify.mjs
 */
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import path from "node:path";

const require = createRequire(import.meta.url);
const HERE = path.dirname(fileURLToPath(import.meta.url));
const { chromium } = require(path.join(HERE, "../../tests/e2e/node_modules/playwright"));

const EMAIL = process.env.TEST_EMAIL || "admin@zeroshield.io";
const PASS = process.env.TEST_PASSWORD || "Adm1n!Pass#2024";
const HOSTS = (process.env.DEMO_HOSTS || "http://localhost:8180,http://127.0.0.1:8180")
  .split(",")
  .map((s) => s.trim().replace(/\/$/, ""))
  .filter(Boolean);

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

async function verifyHost(browser, origin) {
  console.log(`\n── ${origin} ──`);
  const context = await browser.newContext();
  const page = await context.newPage();
  const cssHits = [];
  const loginPosts = [];

  page.on("request", (req) => {
    const u = req.url();
    if (u.includes("styles.css")) cssHits.push(u);
    if (req.method() === "POST" && u.includes("/api/login")) loginPosts.push(u);
  });

  // 1) bare /demo → must end on /demo/
  const bare = await page.goto(`${origin}/demo`, { waitUntil: "domcontentloaded", timeout: 60000 });
  const afterBare = page.url();
  ok(/\/demo\/?(\?|$)/.test(new URL(afterBare).pathname) && afterBare.includes("/demo/"),
    `bare /demo → ${afterBare}`);
  ok(bare?.status() === 200 || bare?.request()?.redirectedFrom() != null || afterBare.endsWith("/demo/") || afterBare.includes("/demo/?"),
    `navigated successfully (status=${bare?.status() ?? "n/a"})`);

  await page.waitForSelector('[data-testid="login-screen"]', { timeout: 15000 });
  ok(true, "login screen visible after bare /demo");

  const cssUnderDemo = cssHits.some((u) => {
    try {
      return new URL(u).pathname.startsWith("/demo/");
    } catch {
      return false;
    }
  });
  ok(cssUnderDemo, `stylesheet under /demo/ (hits=${cssHits.slice(-3).join(" | ") || "none"})`);

  // 2) /demo/ login → POST must be /demo/api/login
  await page.goto(`${origin}/demo/`, { waitUntil: "domcontentloaded", timeout: 60000 });
  await page.waitForSelector('[data-testid="login-email"]', { timeout: 15000 });
  loginPosts.length = 0;

  await page.fill('[data-testid="login-email"]', EMAIL);
  await page.fill('[data-testid="login-password"]', PASS);

  const loginRespPromise = page.waitForResponse(
    (r) => r.request().method() === "POST" && r.url().includes("/api/login"),
    { timeout: 30000 },
  );
  await page.click('[data-testid="login-submit"]');
  const loginResp = await loginRespPromise;
  const loginUrl = loginResp.url();
  let loginPath = "";
  try {
    loginPath = new URL(loginUrl).pathname;
  } catch {
    loginPath = loginUrl;
  }

  ok(loginPath === "/demo/api/login", `login POST path=${loginPath}`);
  ok(!loginPosts.some((u) => {
    try {
      return new URL(u).pathname === "/api/login";
    } catch {
      return false;
    }
  }), "no bare /api/login POST");
  ok(loginResp.ok(), `login HTTP ${loginResp.status()}`);

  // Badge placeholder is "org" until bootApp() finishes /api/me — wait for real text.
  await page.waitForFunction(() => {
    const t = document.querySelector('[data-testid="org-badge"]')?.textContent?.trim();
    return Boolean(t && t !== "org");
  }, { timeout: 20000 });
  const org = (await page.textContent('[data-testid="org-badge"]'))?.trim() || "";
  ok(org.length > 0 && org !== "org", `org badge="${org}"`);

  await context.close();
}

async function main() {
  console.log("demo_localhost_path_verify");
  console.log(`hosts: ${HOSTS.join(", ")}`);
  const browser = await launch();
  try {
    for (const origin of HOSTS) {
      await verifyHost(browser, origin);
    }
  } finally {
    await browser.close();
  }
  console.log(`\n${pass} passed, ${fail} failed`);
  const result = { demoLocalhostPathPass: fail === 0, pass, fail, hosts: HOSTS };
  console.log(JSON.stringify(result));
  process.exit(fail === 0 ? 0 : 1);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
