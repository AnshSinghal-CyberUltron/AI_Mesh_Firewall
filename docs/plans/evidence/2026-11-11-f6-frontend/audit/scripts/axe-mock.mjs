import { createRequire } from "node:module";
import fs from "node:fs";
const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const axe = fs.readFileSync(require.resolve("axe-core/axe.min.js"), "utf8");
const M = process.argv[2];
const b = await chromium.launch();
const res = {};
for (const vp of [{ width: 1440, height: 900 }, { width: 390, height: 844 }]) for (const d of ["checkpoint", "night-shift", "redline"]) for (const p of ["landing", "console", "policy"]) for (const theme of ["", "?theme=" + ({ checkpoint: "dark", "night-shift": "light", redline: "dark" })[d]]) {
  const pg = await b.newPage({ viewport: vp });
  await pg.goto(`file://${M}/${d}/${p}.html${theme}`, { waitUntil: "networkidle" });
  await pg.evaluate(() => document.fonts.ready);
  await pg.addScriptTag({ content: axe });
  const v = await pg.evaluate(async () => (await window.axe.run(document, { resultTypes: ["violations"], runOnly: ["wcag2a", "wcag2aa", "wcag21aa", "best-practice"] })).violations.map((x) => `${x.impact} ${x.id} (${x.nodes.length}) ${x.nodes.slice(0, 2).map((n) => n.target.join(" ")).join(" | ")}`));
  res[`${d}/${p}${theme ? "@alt" : ""} ${vp.width}`] = v;
  await pg.close();
}
await b.close();
fs.writeFileSync(process.argv[3], JSON.stringify(res, null, 2));
for (const [k, v] of Object.entries(res)) console.log(k, v.length ? "\n  " + v.join("\n  ") : "clean");
