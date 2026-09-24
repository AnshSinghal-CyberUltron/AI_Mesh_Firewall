// Inventory every API path literal (string or template) in frontend/src (non-test), with file:line.
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";
const ROOT = "/home/contact_cyberultron_com/AI_Mesh_Firewall/frontend/src";
const files = [];
(function walk(d) {
  for (const n of readdirSync(d)) {
    if (n === ".claude-flow" || n === "node_modules") continue;
    const p = join(d, n);
    const s = statSync(p);
    if (s.isDirectory()) walk(p);
    else if (/\.(jsx?|mjs)$/.test(n) && !/\.test\.js$/.test(n)) files.push(p);
  }
})(ROOT);
const re = /(["'`])((?:(?!\1)[^\\\n]|\\.)*?(?:\/api\/|\/v1\/|\/health\b|\/mcp\b)(?:(?!\1)[^\\\n]|\\.)*)\1/g;
const rows = [];
for (const f of files) {
  const lines = readFileSync(f, "utf8").split("\n");
  lines.forEach((ln, i) => {
    if (/^\s*(\/\/|\*)/.test(ln)) return; // skip comments
    let m;
    while ((m = re.exec(ln))) rows.push({ file: relative(ROOT, f), line: i + 1, lit: m[2] });
  });
}
// normalise ${...} to {x}
const norm = (s) => s.replace(/\$\{[^}]*\}/g, "{x}").replace(/\?.*$/, "");
const byPath = new Map();
for (const r of rows) {
  const k = norm(r.lit);
  if (!byPath.has(k)) byPath.set(k, []);
  byPath.get(k).push(`${r.file}:${r.line}`);
}
const keys = [...byPath.keys()].sort();
for (const k of keys) console.log(`${String(byPath.get(k).length).padStart(3)}  ${k}   <- ${byPath.get(k).slice(0, 4).join(", ")}${byPath.get(k).length > 4 ? " ..." : ""}`);
console.log(`\nTOTAL literal occurrences=${rows.length} distinct normalised paths=${keys.length} files scanned=${files.length}`);
