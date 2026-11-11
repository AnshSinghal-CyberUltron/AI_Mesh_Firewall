// Screenshots the static mockups with Playwright.
// Usage: node shoot.mjs <outDir> [direction/page[@theme] ...]
// Default set: every direction x {landing, console, policy} at desktop 1440x900
// and mobile 390x844 (deviceScaleFactor 2), primary theme, plus the console in
// the alternate theme at desktop. Full-page captures are capped at 2600 css px.
import { createRequire } from "node:module";
import path from "node:path";
import fs from "node:fs";
import { fileURLToPath, pathToFileURL } from "node:url";
const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const here = path.dirname(fileURLToPath(import.meta.url));
const out = process.argv[2];
if (!out) throw new Error("usage: node shoot.mjs <outDir> [dir/page[@theme]...]");
fs.mkdirSync(out, { recursive: true });

const alt = { checkpoint: "dark", "night-shift": "light", redline: "dark" };
const defaults = [];
for (const d of ["checkpoint", "night-shift", "redline"]) {
  for (const p of ["landing", "console", "policy"]) defaults.push(`${d}/${p}`);
  defaults.push(`${d}/console@${alt[d]}`);
}
const jobs = process.argv.slice(3).length ? process.argv.slice(3) : defaults;
const vps = (process.env.VPS || "desktop,mobile").split(",");
const VIEW = {
  desktop: { viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 },
  mobile: { viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true },
};

const browser = await chromium.launch();
for (const vp of vps) {
  const ctx = await browser.newContext({ ...VIEW[vp], reducedMotion: "reduce" });
  for (const job of jobs) {
    const [rel, theme] = job.split("@");
    if (theme && vp === "mobile" && !process.env.ALT_MOBILE) continue;
    const page = await ctx.newPage();
    const errors = [];
    page.on("pageerror", (e) => errors.push(String(e)));
    page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
    const url = pathToFileURL(path.join(here, `${rel}.html`)).href + (theme ? `?theme=${theme}` : "");
    await page.goto(url, { waitUntil: "networkidle" });
    await page.evaluate(() => document.fonts.ready);
    await page.waitForTimeout(300);
    const name = `${rel.replace("/", "--")}--${vp}${theme ? "--" + theme : ""}`;
    const h = await page.evaluate(() => document.documentElement.scrollHeight);
    const w = await page.evaluate(() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]);
    await page.screenshot({ path: path.join(out, `${name}.png`) });
    await page.screenshot({ path: path.join(out, `${name}--full.png`), fullPage: true, clip: { x: 0, y: 0, width: VIEW[vp].viewport.width, height: Math.min(h, 2600) } });
    console.log(`${name} h=${h} scrollW=${w[0]} clientW=${w[1]}${w[0] > w[1] ? " HORIZONTAL-OVERFLOW" : ""}${errors.length ? " ERR " + errors.join(" | ") : ""}`);
    await page.close();
  }
  await ctx.close();
}
await browser.close();
