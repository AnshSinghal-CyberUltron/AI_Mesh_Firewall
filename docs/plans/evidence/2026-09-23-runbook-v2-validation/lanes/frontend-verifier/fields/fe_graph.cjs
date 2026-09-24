// Frontend import graph (static + dynamic imports) from src/main.jsx, and per-file property reads.
// Usage: node fe_graph.cjs <out.json>
const fs = require("fs"), path = require("path");
const FE = "/home/contact_cyberultron_com/AI_Mesh_Firewall/frontend";
const swc = require(path.join(FE, "node_modules/@swc/core"));
const SRC = path.join(FE, "src");
function resolve(from, spec) {
  if (!spec.startsWith(".")) return null; // package import
  const base = path.resolve(path.dirname(from), spec);
  for (const c of [base, base + ".js", base + ".jsx", base + ".mjs", path.join(base, "index.js"), path.join(base, "index.jsx")]) {
    if (fs.existsSync(c) && fs.statSync(c).isFile()) return c;
  }
  return "UNRESOLVED:" + base;
}
function walk(node, visit) {
  if (!node || typeof node !== "object") return;
  if (Array.isArray(node)) { for (const n of node) walk(n, visit); return; }
  if (typeof node.type === "string") visit(node);
  for (const k of Object.keys(node)) { if (k === "span") continue; const v = node[k]; if (v && typeof v === "object") walk(v, visit); }
}
const info = {};
function analyse(file) {
  if (info[file]) return;
  const code = fs.readFileSync(file, "utf8");
  let ast;
  try { ast = swc.parseSync(code, { syntax: "ecmascript", jsx: true, dynamicImport: true }); }
  catch (e) { info[file] = { error: String(e).slice(0, 200), imports: [], reads: [], strings: [] }; return; }
  const imports = new Set(), reads = new Set(), strings = new Set(), pkgs = new Set();
  walk(ast, (n) => {
    if (n.type === "ImportDeclaration" || n.type === "ExportAllDeclaration" || (n.type === "ExportNamedDeclaration" && n.source)) {
      const r = resolve(file, n.source.value); if (r) imports.add(r); else pkgs.add(n.source.value);
    }
    if (n.type === "CallExpression" && n.callee && n.callee.type === "Import" && n.arguments[0] && n.arguments[0].expression.type === "StringLiteral") {
      const r = resolve(file, n.arguments[0].expression.value); if (r) imports.add(r); else pkgs.add(n.arguments[0].expression.value);
    }
    if (n.type === "MemberExpression") {
      const p = n.property;
      if (p.type === "Identifier") reads.add(p.value);
      else if (p.type === "Computed" && p.expression && p.expression.type === "StringLiteral") reads.add(p.expression.value);
    }
    if (n.type === "ObjectPattern") {
      for (const pr of n.properties) {
        if (pr.type === "KeyValuePatternProperty") { const k = pr.key; if (k.type === "Identifier") reads.add(k.value); else if (k.type === "StringLiteral") reads.add(k.value); }
        if (pr.type === "AssignmentPatternProperty") reads.add(pr.key.value);
      }
    }
    if (n.type === "StringLiteral") strings.add(n.value);
    if (n.type === "TemplateLiteral") for (const q of n.quasis) strings.add(q.cooked ?? q.raw);
  });
  info[file] = { imports: [...imports], pkgs: [...pkgs], reads: [...reads], strings: [...strings] };
  for (const i of imports) if (!i.startsWith("UNRESOLVED")) analyse(i);
}
analyse(path.join(SRC, "main.jsx"));
// all non-test source files for the "unreachable" list
const all = [];
(function w(d) { for (const n of fs.readdirSync(d)) { if (n === ".claude-flow") continue; const p = path.join(d, n); const s = fs.statSync(p); if (s.isDirectory()) w(p); else if (/\.(jsx?|mjs)$/.test(n) && !/\.test\.js$/.test(n)) all.push(p); } })(SRC);
const reachable = Object.keys(info).sort();
const unreachable = all.filter((f) => !info[f]).sort();
const rel = (f) => path.relative(SRC, f);
const out = { reachable: reachable.map(rel), unreachable: unreachable.map((f) => ({ file: rel(f), bytes: fs.statSync(f).size })), files: {} };
for (const f of reachable) out.files[rel(f)] = { ...info[f], imports: info[f].imports.map((i) => i.startsWith("UNRESOLVED") ? i : rel(i)), bytes: fs.statSync(f).size };
fs.writeFileSync(process.argv[2], JSON.stringify(out, null, 1));
console.log(`reachable from main.jsx: ${reachable.length} files; unreachable non-test source files: ${unreachable.length}`);
console.log(`unresolved imports: ${reachable.flatMap((f) => info[f].imports.filter((i) => i.startsWith("UNRESOLVED"))).length}`);
console.log(`parse errors: ${reachable.filter((f) => info[f].error).length}`);
console.log("unreachable (bytes):"); for (const u of out.unreachable) console.log(`  ${String(u.bytes).padStart(7)}  ${u.file}`);
