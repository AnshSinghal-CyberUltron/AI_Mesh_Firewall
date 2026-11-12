// Audit capture: logs in through the real UI, walks every reachable tab at
// desktop (1440x900) and mobile (390x844 @2x), light and dark; records API
// calls, console errors, page height, horizontal overflow and axe-core results.
// Usage: BASE=http://127.0.0.1:4176 OUT=<dir> node capture.mjs
import { createRequire } from "node:module";
import fs from "node:fs";
import path from "node:path";
const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const axeSource = fs.readFileSync(require.resolve("axe-core/axe.min.js"), "utf8");

const BASE = process.env.BASE || "http://127.0.0.1:4176";
const OUT = process.env.OUT;
const EMAIL = process.env.F6_ADMIN_EMAIL;
const PW = process.env.F6_ADMIN_PW;
if (!OUT || !EMAIL || !PW) throw new Error("OUT, F6_ADMIN_EMAIL, F6_ADMIN_PW required");
fs.mkdirSync(OUT, { recursive: true });

const TABS = [
  ["overview", "/"],
  ["1-1-gateway", "/?tab=firewall-1-1"],
  ["1-2-policy", "/?tab=firewall-1-2"],
  ["1-3-rag", "/?tab=firewall-1-3"],
  ["1-4-mcp", "/?tab=firewall-1-4"],
  ["1-5-governance", "/?tab=firewall-1-5"],
  ["1-6-killswitch", "/?tab=firewall-1-6"],
  ["1-7-output", "/?tab=firewall-1-7"],
  ["inputs-config", "/?tab=firewall-config"],
  ["profile", "/?tab=profile"],
  ["settings", "/?tab=settings"],
];
const VIEWPORTS = {
  desktop: { viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 },
  mobile: { viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true },
};
const ONLY = (process.env.ONLY || "").split(",").filter(Boolean);
const THEMES = (process.env.THEMES || "light,dark").split(",");
const VPS = (process.env.VPS || "desktop,mobile").split(",");
const AXE = process.env.AXE !== "0";

const browser = await chromium.launch();
const results = [];
// Sample the CPU time of this process tree once a second; renderers exit when
// their page closes, so keep the max seen per pid and sum at the end.
const { execSync } = await import("node:child_process");
const cpuByPid = new Map();
function sampleCpu() {
  try {
    const rows = execSync("ps -eo pid=,ppid=,times=").toString().trim().split("\n").map((l) => l.trim().split(/\s+/).map(Number));
    const kids = new Map();
    for (const [pid, ppid] of rows) { if (!kids.has(ppid)) kids.set(ppid, []); kids.get(ppid).push(pid); }
    const tree = new Set([process.pid]); const st = [process.pid];
    while (st.length) { const q = st.pop(); for (const k of kids.get(q) || []) if (!tree.has(k)) { tree.add(k); st.push(k); } }
    for (const [pid, , t] of rows) if (tree.has(pid)) cpuByPid.set(pid, Math.max(cpuByPid.get(pid) || 0, t));
  } catch {}
}
const sampler = setInterval(sampleCpu, 1000);
const redact = (t) => String(t).replace(/(token=)[A-Za-z0-9._-]+/g, "$1<redacted>").replace(/eyJ[A-Za-z0-9._-]{20,}/g, "<jwt-redacted>");

async function login(ctx) {
  const page = await ctx.newPage();
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.fill('input[type="email"]', EMAIL);
  await page.fill('input[type="password"]', PW);
  await Promise.all([
    page.waitForResponse((r) => r.url().includes("/api/auth/token/"), { timeout: 20000 }),
    page.click('button[type="submit"]'),
  ]);
  await page.waitForURL((u) => !u.pathname.startsWith("/login"), { timeout: 20000 });
  await page.close();
}

for (const vpName of VPS) {
  for (const theme of THEMES) {
    const ctx = await browser.newContext({ ...VIEWPORTS[vpName], colorScheme: theme, reducedMotion: "reduce" });
    await ctx.addInitScript((t) => { try { localStorage.setItem("zeroshield_theme", t); } catch {} }, theme);
    // Login page first (unauthenticated).
    {
      const page = await ctx.newPage();
      const errs = [];
      page.on("console", (m) => { if (m.type() === "error") errs.push(redact(m.text()).slice(0, 300)); });
      await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
      await page.waitForTimeout(800);
      const file = `login--${vpName}--${theme}.png`;
      await page.screenshot({ path: path.join(OUT, file), fullPage: true });
      let axe = null;
      if (AXE && theme === "light" || AXE && vpName === "desktop") {
        await page.addScriptTag({ content: axeSource });
        axe = await page.evaluate(async () => {
          const r = await window.axe.run(document, { resultTypes: ["violations"] });
          return r.violations.map((v) => ({ id: v.id, impact: v.impact, help: v.help, nodes: v.nodes.length, sample: v.nodes.slice(0, 3).map((n) => n.target.join(" ")) }));
        });
      }
      results.push({ name: "login", vp: vpName, theme, file, consoleErrors: errs, axe });
      await page.close();
    }
    await login(ctx);
    for (const [name, url] of TABS) {
      if (ONLY.length && !ONLY.includes(name)) continue;
      const page = await ctx.newPage();
      const api = new Map();
      const errs = [];
      const failed = [];
      page.on("console", (m) => { if (m.type() === "error") errs.push(redact(m.text()).slice(0, 300)); });
      page.on("pageerror", (e) => errs.push("PAGEERROR " + redact(e).slice(0, 300)));
      page.on("requestfailed", (r) => failed.push(`${r.method()} ${new URL(r.url()).pathname} ${r.failure()?.errorText}`));
      page.on("response", (r) => {
        const u = new URL(r.url());
        if (/^\/(api|v1|health|gw-health|ws)\b/.test(u.pathname)) {
          const key = `${r.request().method()} ${u.pathname.replace(/[0-9a-f]{8}-[0-9a-f-]{27,}/g, "{uuid}").replace(/\/\d+\//g, "/{id}/")}`;
          const prev = api.get(key) || { count: 0, statuses: new Set() };
          prev.count++; prev.statuses.add(r.status());
          api.set(key, prev);
        }
      });
      const t0 = Date.now();
      await page.goto(`${BASE}${url}`, { waitUntil: "domcontentloaded" });
      try { await page.waitForLoadState("networkidle", { timeout: 15000 }); } catch {}
      const settleMs = Date.now() - t0;
      await page.waitForTimeout(2500);
      const metrics = await page.evaluate(() => ({
        height: document.documentElement.scrollHeight,
        width: document.documentElement.scrollWidth,
        clientWidth: document.documentElement.clientWidth,
        h1: [...document.querySelectorAll("h1")].map((h) => h.textContent.trim()).slice(0, 3),
        h2count: document.querySelectorAll("h2").length,
        buttons: document.querySelectorAll("button").length,
        inputs: document.querySelectorAll("input,select,textarea").length,
        skeletons: document.querySelectorAll('[class*="animate-pulse"],[class*="shimmer"]').length,
        spinners: document.querySelectorAll('[class*="animate-spin"]').length,
        textSample: document.body.innerText.slice(0, 1200),
        fontFamilies: [...new Set([...document.querySelectorAll("body *")].slice(0, 4000).map((e) => getComputedStyle(e).fontFamily.split(",")[0].trim()))].slice(0, 10),
        fontSizes: Object.entries([...document.querySelectorAll("body *")].slice(0, 6000).reduce((a, e) => { if (e.childNodes.length && [...e.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim())) { const s = getComputedStyle(e).fontSize; a[s] = (a[s] || 0) + 1; } return a; }, {})).sort((a, b) => b[1] - a[1]),
        overflowers: [...document.querySelectorAll("body *")].filter((e) => { const r = e.getBoundingClientRect(); return r.right > document.documentElement.clientWidth + 1 && r.width > 0 && getComputedStyle(e).position !== "fixed"; }).slice(0, 5).map((e) => `${e.tagName.toLowerCase()}.${String(e.className).slice(0, 80)}`),
      }));
      const vpFile = `${name}--${vpName}--${theme}.png`;
      const fullFile = `${name}--${vpName}--${theme}--full.png`;
      await page.screenshot({ path: path.join(OUT, vpFile) });
      // Full page capped at 12000 css px to keep files sane.
      const capH = Math.min(metrics.height, 12000);
      await page.screenshot({ path: path.join(OUT, fullFile), fullPage: true, clip: { x: 0, y: 0, width: metrics.clientWidth, height: capH } });
      let axe = null;
      if (AXE && (vpName === "desktop")) {
        await page.addScriptTag({ content: axeSource });
        axe = await page.evaluate(async () => {
          const r = await window.axe.run(document, { resultTypes: ["violations"] });
          return r.violations.map((v) => ({ id: v.id, impact: v.impact, help: v.help, nodes: v.nodes.length, sample: v.nodes.slice(0, 3).map((n) => n.target.join(" ")) }));
        });
      }
      results.push({
        name, url, vp: vpName, theme, file: vpFile, fullFile, settleMs, metrics,
        api: [...api.entries()].map(([k, v]) => `${k} -> ${[...v.statuses].join("/")} x${v.count}`).sort(),
        consoleErrors: [...new Set(errs)].slice(0, 20), failed: [...new Set(failed)].slice(0, 20), axe,
      });
      console.log(`${name} ${vpName} ${theme} h=${metrics.height} settle=${settleMs}ms errs=${errs.length} api=${api.size} axe=${axe ? axe.length : "-"}`);
      await page.close();
    }
    await ctx.close();
  }
}
sampleCpu(); clearInterval(sampler);
const cpuSeconds = [...cpuByPid.values()].reduce((a, b) => a + b, 0);
console.log(`CPU seconds (node + chromium process tree, sampled 1/s): ${cpuSeconds}`);
await browser.close();
fs.writeFileSync(path.join(OUT, "results.json"), JSON.stringify({ cpuSeconds, results }, null, 2));
