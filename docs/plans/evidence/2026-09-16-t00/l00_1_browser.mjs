/**
 * T00 L00-1: real login form against baked nginx :8180.
 * Captures token/me request IDs, hashed asset, no Vite client.
 */
import { createRequire } from "node:module";
import path from "node:path";
import fs from "node:fs";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, "../../../..");
const { chromium } = require(path.join(REPO, "tests/e2e/node_modules/playwright"));

const BASE = (process.env.BASE_URL || "http://127.0.0.1:8180").replace(/\/$/, "");
const EMAIL = process.env.TEST_EMAIL || "admin@zeroshield.io";
const PASS = process.env.TEST_PASSWORD || "Adm1n!Pass#2024";
const SHOTS = path.join(HERE, "shots");
fs.mkdirSync(SHOTS, { recursive: true });

const out = {
  base: BASE,
  started_at: new Date().toISOString(),
  requests: [],
  html_has_vite_client: null,
  hashed_js: null,
  hashed_css: null,
  login: null,
  me: null,
  post_login_path: null,
  title: null,
  console_errors: [],
  pass: false,
};

function rec(res) {
  const h = res.headers();
  return {
    method: res.request().method(),
    url: res.url(),
    status: res.status(),
    "x-request-id": h["x-request-id"] || h["X-Request-ID"] || null,
    content_type: h["content-type"] || null,
  };
}

const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH || "/usr/bin/chromium-browser";
const browser = await chromium.launch({
  headless: true,
  executablePath,
  args: ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
});
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await context.newPage();
page.on("console", (msg) => {
  if (msg.type() === "error") out.console_errors.push(msg.text().slice(0, 400));
});
page.on("response", (res) => {
  const u = res.url();
  if (u.includes("/api/") || u.includes("/gw-health") || u.includes("/v1/") || u.includes("/assets/")) {
    out.requests.push(rec(res));
  }
});

try {
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle", timeout: 120000 });
  const html = await page.content();
  out.html_has_vite_client = html.includes("@vite/client") || html.includes("/@vite/");
  const js = html.match(/\/assets\/index-[A-Za-z0-9_-]+\.js/);
  const css = html.match(/\/assets\/index-[A-Za-z0-9_-]+\.css/);
  out.hashed_js = js ? js[0] : null;
  out.hashed_css = css ? css[0] : null;
  await page.screenshot({ path: path.join(SHOTS, "baked-login.png"), fullPage: true });

  await page.locator("#email").fill(EMAIL);
  await page.locator("#password").fill(PASS);

  let loginRes = null;
  for (let attempt = 0; attempt < 6; attempt++) {
    const [res] = await Promise.all([
      page.waitForResponse((r) => r.url().includes("/api/auth/token/") && r.request().method() === "POST", { timeout: 60000 }),
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
  out.login = rec(loginRes);
  await page.waitForFunction(() => !location.pathname.startsWith("/login"), { timeout: 60000 });
  out.post_login_path = new URL(page.url()).pathname;
  out.title = await page.title();
  const me = out.requests.find((r) => r.url.includes("/api/auth/me/") && r.status === 200);
  out.me = me || null;
  await page.waitForTimeout(2500);
  await page.screenshot({ path: path.join(SHOTS, "baked-post-login.png"), fullPage: true });

  // second surface: overview still same origin, asset still hashed
  await page.goto(`${BASE}/`, { waitUntil: "domcontentloaded", timeout: 60000 });
  await page.waitForTimeout(2000);
  await page.screenshot({ path: path.join(SHOTS, "baked-overview.png"), fullPage: true });

  out.pass = Boolean(
    out.login?.status === 200 &&
      out.login?.["x-request-id"] &&
      out.hashed_js &&
      out.html_has_vite_client === false &&
      out.post_login_path &&
      !out.post_login_path.startsWith("/login")
  );
} catch (e) {
  out.error = String(e);
  await page.screenshot({ path: path.join(SHOTS, "baked-failure.png"), fullPage: true }).catch(() => {});
} finally {
  out.finished_at = new Date().toISOString();
  fs.writeFileSync(path.join(HERE, "l00_1_browser.json"), JSON.stringify(out, null, 2));
  await browser.close();
}

if (!out.pass) {
  console.error(JSON.stringify(out, null, 2));
  process.exit(1);
}
console.log(JSON.stringify({ pass: true, login: out.login, me: out.me, hashed_js: out.hashed_js, path: out.post_login_path }, null, 2));
