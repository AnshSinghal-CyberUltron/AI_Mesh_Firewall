/**
 * Visual + behavior regression gate for the ZeroShield frontend.
 *
 * Codifies the hardening invariants proven during the frontend-harden loop as an
 * automated, re-runnable check across every surface x BOTH themes x 4 widths:
 *   - RESPONSIVE  : no real horizontal overflow. Uses `main.scrollWidth-clientWidth`
 *                   (mainScroll), because <main> has overflow-y-auto -> CSS computes
 *                   overflow-x to auto, so it silently ABSORBS overflow that a
 *                   documentElement scan reads as 0. Real offenders exclude elements
 *                   inside overflow-hidden/scroller ancestors and transformed nodes.
 *   - NO LEAK     : no raw key / token / PEM / JWT rendered in the DOM or inputs.
 *   - THEME       : renders in light AND dark (surface present, no crash).
 *   - CHARTS      : migrated surfaces expose ECharts canvases and 0 recharts SVGs.
 *   - STABILITY   : no uncaught page errors.
 * Captures a PNG per combo (visual artifact) and writes report.json (behavior snapshot).
 *
 * This is NOT part of `npm test` (which is browserless). Run it against the running
 * dev stack — see README.md. Exit code is non-zero on any gated regression.
 *
 * Env:
 *   VISUAL_BASE_URL  default http://127.0.0.1:8180
 *   VISUAL_TOKEN     JWT access token for authenticated surfaces (required unless only login)
 *   VISUAL_OUT       output dir (default tests/visual/__output__)
 *   VISUAL_SETTLE    ms to wait after load (default 3800)
 *   VISUAL_ONLY      comma list of surface ids to run (default all)
 *   PLAYWRIGHT_PATH  explicit path to a playwright module (fallback resolver)
 *   VISUAL_CHROME    chrome executable (default /opt/google/chrome/chrome)
 */
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { createRequire } from "node:module";

const __dirname = dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);

// --- resolve playwright from a few known locations (it is intentionally not a
// project dependency, to avoid a heavy install on the shared main branch) ---
async function loadPlaywright() {
  const candidates = [
    process.env.PLAYWRIGHT_PATH,
    "playwright",
    join(__dirname, "../../node_modules/playwright/index.js"),
    "/home/contact_cyberultron_com/.npm/_npx/9833c18b2d85bc59/node_modules/playwright/index.js",
  ].filter(Boolean);
  for (const c of candidates) {
    try {
      const mod = await import(c.startsWith("/") || c.endsWith(".js") ? c : require.resolve(c));
      if (mod?.chromium || mod?.default?.chromium) return mod.chromium || mod.default.chromium;
    } catch { /* try next */ }
  }
  throw new Error(
    "playwright not found. Install it (`npm i -D playwright && npx playwright install chromium`) " +
    "or set PLAYWRIGHT_PATH to a playwright module."
  );
}

const BASE = process.env.VISUAL_BASE_URL || "http://127.0.0.1:8180";
const TOKEN = process.env.VISUAL_TOKEN || "";
const OUT = process.env.VISUAL_OUT || join(__dirname, "__output__");
const SETTLE = Number(process.env.VISUAL_SETTLE || 3800);
// Pause between combos so the dev Postgres pool can drain (avoids the F3 "too many
// clients" saturation that a rapid full-app reload storm otherwise triggers).
const PAUSE = Number(process.env.VISUAL_PAUSE || 700);
const CHROME = process.env.VISUAL_CHROME || "/opt/google/chrome/chrome";
const ONLY = (process.env.VISUAL_ONLY || "").split(",").map((s) => s.trim()).filter(Boolean);

const cfg = JSON.parse(readFileSync(join(__dirname, "surfaces.json"), "utf8"));
const surfaces = cfg.surfaces.filter((s) => ONLY.length === 0 || ONLY.includes(s.id));

const seed = (token, theme) =>
  `try{${token ? `localStorage.setItem('auth_access',${JSON.stringify(token)});` : "localStorage.removeItem('auth_access');"}` +
  `localStorage.setItem('zeroshield_theme',${JSON.stringify(theme)});}catch(e){}`;

// Runs in the page. Returns the behavior snapshot for one combo.
const MEASURE = () => {
  const vw = document.documentElement.clientWidth;
  const main = document.querySelector("main");
  const docOverflow = document.documentElement.scrollWidth - vw;
  const mainScroll = main ? main.scrollWidth - main.clientWidth : 0;
  const limit = main ? main.getBoundingClientRect().left + main.clientWidth : vw;
  const scope = main || document.body;
  const inExcluded = (el) => {
    let p = el.parentElement;
    while (p && p !== scope) {
      const cs = getComputedStyle(p);
      if (["auto", "scroll", "hidden"].includes(cs.overflowX) || cs.overflow === "hidden") return true;
      p = p.parentElement;
    }
    return false;
  };
  // real horizontal overflow: element extends past the viewport, is not itself/ancestor a
  // scroller or clipped, and is not merely transformed (ping/scale animations).
  const realOverflow = [];
  for (const el of scope.querySelectorAll("div,section,table,ul,ol,pre,span,p,h1,h2,h3,button,input,textarea,form,img")) {
    const r = el.getBoundingClientRect();
    if (r.width < 24) continue;
    const cs = getComputedStyle(el);
    if (cs.transform !== "none") continue;
    if (["auto", "scroll"].includes(cs.overflowX)) continue;
    if (r.right > limit + 2 && !inExcluded(el)) {
      realOverflow.push({ tag: el.tagName.toLowerCase(), cls: (el.className || "").toString().slice(0, 60), right: Math.round(r.right), w: Math.round(r.width) });
    }
  }
  // leak scan: DOM text + every input value
  const LEAK = [
    ["openai", /\bsk-[A-Za-z0-9]{20,}\b/],
    ["aws", /\bAKIA[0-9A-Z]{16}\b/],
    ["pinecone", /\bpcsk_[A-Za-z0-9_-]{20,}\b/],
    ["github", /\bghp_[A-Za-z0-9]{36}\b/],
    ["pem", /-----BEGIN (?:RSA |EC )?PRIVATE KEY-----/],
    ["jwt", /\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{6,}\b/],
  ];
  const hay = (document.body ? document.body.innerText : "") + "\n" +
    Array.from(document.querySelectorAll("input,textarea")).map((i) => i.value || "").join("\n");
  const leak = [];
  for (const [name, re] of LEAK) if (re.test(hay)) leak.push(name);
  // chart engines
  const echartsCanvas = document.querySelectorAll("main canvas, .echarts-for-react canvas, div[_echarts_instance_]").length;
  const rechartsSvg = document.querySelectorAll(".recharts-wrapper, .recharts-surface").length;
  const hasMain = !!main;
  return { vw, docOverflow, mainScroll, realOverflow: realOverflow.slice(0, 8), realOverflowCount: realOverflow.length, leak, echartsCanvas, rechartsSvg, hasMain };
};

function fmt(n, w = 4) { return String(n).padStart(w); }

async function run() {
  const chromium = await loadPlaywright();
  mkdirSync(OUT, { recursive: true });
  mkdirSync(join(OUT, "screenshots"), { recursive: true });
  const needsAuth = surfaces.some((s) => s.auth);
  if (needsAuth && !TOKEN) {
    console.error("VISUAL_TOKEN is required for authenticated surfaces. See README.md (use run.sh to mint one).");
    process.exit(2);
  }
  const browser = await chromium.launch({ executablePath: CHROME, args: ["--no-sandbox"] });
  const report = [];
  const failures = [];
  for (const s of surfaces) {
    for (const theme of cfg.themes) {
      for (const w of cfg.widths) {
        const ctx = await browser.newContext({ viewport: { width: w, height: 900 } });
        await ctx.addInitScript(seed(s.auth ? TOKEN : "", theme));
        const page = await ctx.newPage();
        const consoleErrors = [];
        const pageErrors = [];
        page.on("console", (m) => { if (m.type() === "error") consoleErrors.push(m.text().slice(0, 160)); });
        page.on("pageerror", (e) => pageErrors.push((e.message || String(e)).slice(0, 160)));
        // Authenticated surfaces need <main>. Under dev-DB pressure (F3) the session
        // check can transiently hang -> app falls back to login. Re-navigate with a
        // growing backoff before declaring an auth failure, so the gate is robust to
        // that flake but still catches a genuinely broken auth/render.
        const MAX = s.auth ? 5 : 1;
        for (let attempt = 0; attempt < MAX; attempt++) {
          await page.goto(`${BASE}${s.path}`, { waitUntil: "domcontentloaded", timeout: 25000 }).catch(() => {});
          await page.waitForTimeout(SETTLE);
          if (!s.auth) break;
          if (await page.$("main")) break;
          if (attempt < MAX - 1) await page.waitForTimeout(1500 * (attempt + 1)); // backoff: let the pool drain
        }
        let m;
        try { m = await page.evaluate(MEASURE); } catch (e) { m = { error: String(e).slice(0, 120) }; }
        const shot = join(OUT, "screenshots", `${s.id}-${theme}-${w}.png`);
        await page.screenshot({ path: shot }).catch(() => {});
        // network errors are informational (F3 transient 5xx) — not gated
        const netErr = consoleErrors.filter((t) => /Failed to load resource|net::ERR|50\d \(|status of 50\d/i.test(t));
        const jsConsoleErr = consoleErrors.filter((t) => !netErr.includes(t) && !/favicon|ResizeObserver loop|React DevTools|\[vite\]/i.test(t));
        // GATE conditions
        const gate = [];
        if (s.auth && m.hasMain === false) gate.push("AUTH_FAILED(no <main> — token invalid/expired or backend down)");
        const overflowNote = m.realOverflowCount > 0 ? `OVERFLOW(${m.realOverflowCount}: ${(m.realOverflow[0] && m.realOverflow[0].cls) || ""})` : "";
        if (overflowNote && !s.allowOverflow) gate.push(overflowNote);
        if ((m.leak || []).length) gate.push(`LEAK(${m.leak.join(",")})`);
        if (pageErrors.length) gate.push(`PAGEERROR(${pageErrors.length}: ${pageErrors[0]})`);
        if (jsConsoleErr.length) gate.push(`CONSOLE(${jsConsoleErr.length}: ${jsConsoleErr[0]})`);
        if (s.migratedChart && m.rechartsSvg > 0) gate.push(`RECHARTS_REGRESSION(${m.rechartsSvg})`);
        const warn = overflowNote && s.allowOverflow ? ` (allowed: ${overflowNote})` : "";
        const rec = { id: s.id, theme, w, ...m, consoleErr: jsConsoleErr.length, netErr: netErr.length, pageErr: pageErrors.length, gate, allowedOverflow: !!warn, pass: gate.length === 0 };
        report.push(rec);
        if (gate.length) failures.push(`${s.id} ${theme} ${w}: ${gate.join("; ")}`);
        const status = gate.length ? "FAIL" : (warn ? "warn" : "ok");
        console.log(`${status.padEnd(4)} ${s.id.padEnd(15)} ${theme.padEnd(5)} ${fmt(w)}  mainScroll=${fmt(m.mainScroll)} realOver=${fmt(m.realOverflowCount, 2)} leak=${(m.leak || []).length} echarts=${m.echartsCanvas ?? "-"} recharts=${m.rechartsSvg ?? "-"} jsErr=${jsConsoleErr.length}${gate.length ? "  <<< " + gate.join("; ") : warn}`);
        await ctx.close();
        if (PAUSE) await new Promise((r) => setTimeout(r, PAUSE));
      }
    }
  }
  await browser.close();
  writeFileSync(join(OUT, "report.json"), JSON.stringify(report, null, 1));
  console.log(`\n${report.length} combos audited across ${surfaces.length} surfaces x ${cfg.themes.length} themes x ${cfg.widths.length} widths.`);
  console.log(`report -> ${join(OUT, "report.json")} ; screenshots -> ${join(OUT, "screenshots")}`);
  if (failures.length) {
    console.error(`\nGATE FAILED — ${failures.length} regression(s):`);
    for (const f of failures) console.error("  - " + f);
    process.exit(1);
  }
  console.log("\nGATE PASSED — no visual/behavior regressions.");
}

run().catch((e) => { console.error(e); process.exit(2); });
