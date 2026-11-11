// Writes <direction>/tokens.css from tokens.mjs. The primary theme is the
// default (:root); the other theme applies under [data-theme="<name>"].
import fs from "node:fs";
import { directions } from "./tokens.mjs";

const folder = { checkpoint: "checkpoint", nightShift: "night-shift", redline: "redline" };
const kebab = (s) => s.replace(/[A-Z]/g, (m) => "-" + m.toLowerCase());

for (const [key, d] of Object.entries(directions)) {
  const out = [`/* Generated from tokens.mjs by build-tokens.mjs. Do not edit by hand. */`,
    `@import url("https://fonts.googleapis.com/css2?${d.fonts.google}&display=swap");`, ""];
  const block = (sel, t) => {
    out.push(`${sel} {`);
    for (const [k, v] of Object.entries(t)) out.push(`  --${kebab(k)}: ${v};`);
    if (sel.startsWith(":root")) {
      out.push(`  --font-ui: ${d.fonts.ui};`, `  --font-mono: ${d.fonts.mono};`);
      if (d.fonts.display) out.push(`  --font-display: ${d.fonts.display};`);
    }
    out.push("}", "");
  };
  block(":root", d.themes[d.primary]);
  for (const [name, t] of Object.entries(d.themes)) {
    if (name !== d.primary) block(`:root[data-theme="${name}"]`, t);
  }
  const dir = new URL(`./${folder[key]}/`, import.meta.url);
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(new URL("tokens.css", dir), out.join("\n"));
  console.log(`wrote ${folder[key]}/tokens.css`);
}
