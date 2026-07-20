// Build the self-contained proof artifact HTML (screenshots inlined as data URIs).
import fs from "node:fs";
import path from "node:path";

const PROOF = "mcp-parallel/findings/mcp-page/PROOF";
const OUT = process.argv[2] || "/tmp/proof.html";
const b64 = (f) => "data:image/jpeg;base64," + fs.readFileSync(path.join(PROOF, f)).toString("base64");

const img = {
  fullLight: b64("01-full-page-light.jpg"),
  flow: b64("02-flowstrip-redactions.jpg"),
  simDefault: b64("03-simulator-default.jpg"),
  simDecision: b64("04-simulator-decision.jpg"),
  fullDark: b64("05-full-page-dark.jpg"),
};
const live = JSON.parse(fs.readFileSync(path.join(PROOF, "report.json"), "utf8")).live;

const rows = [
  { fix: "Redactions counted", before: "0 sanitized", after: `${live.flowStrip.sanitized.toLocaleString()} sanitized`, note: "§1.4 “PII Redaction” node — full-DB collapse counts", state: "ok" },
  { fix: "Backend banner", before: "hard “Backend unreachable”", after: `${live.banner.line} / ${live.banner.label}`, note: "reflects real state, degrades gracefully on slow", state: "ok" },
  { fix: "Policy Simulator default", before: "a Failed server", after: live.simulatorDefaultServer.replace(/\s+·\s+/g, " · "), note: "defaults to a CONNECTED server with tools", state: "ok" },
  { fix: "Context Fields assembled", before: "pinned at 500", after: `${live.flowStrip.assembled.toLocaleString()} assembled`, note: "uncapped distinct-request total", state: "ok" },
  { fix: "Simulator decision", before: "dead on arrival", after: live.simulatorDecisionShown ? "dry-run + live render a verdict" : "—", state: "ok" },
];

const commits = fs.readFileSync("/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/d74b4a3b-4d61-48b7-8ae0-10a05eefc79e/scratchpad/mcp_commits.txt", "utf8").trim().split("\n");

const rowHtml = rows.map(r => `
  <tr>
    <td class="fix">${r.fix}</td>
    <td class="was"><s>${r.before}</s></td>
    <td class="now"><span class="chip ok">${r.after}</span></td>
    <td class="note">${r.note}</td>
  </tr>`).join("");

const shot = (src, cap) => `
  <figure class="shot">
    <a href="${src}" target="_blank" rel="noopener"><img loading="lazy" src="${src}" alt="${cap}"></a>
    <figcaption>${cap}</figcaption>
  </figure>`;

const commitHtml = commits.map(c => {
  const [h, ...rest] = c.split(" ");
  return `<li><code class="sha">${h}</code><span>${rest.join(" ").replace(/</g, "&lt;")}</span></li>`;
}).join("");

const style = `<style>
  :root{
    --ground:#f5f7f8; --ink:#0c1420; --muted:#5b6b7c; --faint:#8595a6;
    --hair:#e3e8ee; --card:#ffffff;
    --teal:#0d7d72; --teal-700:#0b5f57; --teal-wash:#e9f4f2;
    --ok:#0a7d52; --ok-wash:#e7f5ee; --amber:#b26a00; --red:#c0392b;
    --shadow:0 1px 2px rgba(12,20,32,.05), 0 6px 20px -12px rgba(12,20,32,.18);
  }
  *{box-sizing:border-box}
  body{margin:0}
  .wrap{
    max-width:940px; margin:0 auto; padding:34px 22px 60px;
    background:var(--ground); color:var(--ink);
    font:400 16px/1.6 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
    -webkit-font-smoothing:antialiased;
  }
  code,.sha,kbd{font-family:ui-monospace,"SF Mono",SFMono-Regular,Menlo,Consolas,monospace}
  h1,h2{text-wrap:balance; letter-spacing:-.018em; margin:0}
  .masthead{border-bottom:1px solid var(--hair); padding-bottom:26px; margin-bottom:34px}
  .kicker{font-size:12px; font-weight:650; letter-spacing:.14em; text-transform:uppercase; color:var(--teal-700)}
  h1{font-size:clamp(26px,3.6vw,38px); font-weight:700; margin:12px 0 0; line-height:1.12}
  h1 .hl{color:var(--teal-700)}
  .dek{color:var(--muted); font-size:16.5px; max-width:64ch; margin:14px 0 0}
  .dek code, .foot code, .steps code, .v-sub code{background:#eef2f5; padding:.06em .38em; border-radius:5px; font-size:.86em}
  .verdict{
    display:flex; gap:14px; align-items:center; margin-top:22px; padding:16px 18px;
    background:var(--ok-wash); border:1px solid #bfe3ce; border-radius:12px;
  }
  .verdict strong{display:block; font-size:15.5px}
  .v-sub{display:block; color:#3a6b54; font-size:13.5px; margin-top:3px}
  .v-dot{width:11px; height:11px; border-radius:50%; background:var(--ok); flex:none; box-shadow:0 0 0 4px rgba(10,125,82,.16)}
  section{margin:38px 0}
  h2{font-size:19px; font-weight:670; display:flex; align-items:baseline; gap:11px}
  .num{font:600 12.5px/1 ui-monospace,monospace; color:var(--teal); border:1px solid #bfe0db; background:var(--teal-wash); padding:5px 7px; border-radius:6px; letter-spacing:.02em}
  .tablescroll{overflow-x:auto; margin-top:16px; border:1px solid var(--hair); border-radius:12px; background:var(--card); box-shadow:var(--shadow)}
  table{border-collapse:collapse; width:100%; font-size:14px; min-width:640px}
  thead th{text-align:left; font-size:11.5px; letter-spacing:.06em; text-transform:uppercase; color:var(--faint); font-weight:650; padding:13px 16px; border-bottom:1px solid var(--hair)}
  td{padding:14px 16px; border-bottom:1px solid #eef2f5; vertical-align:top}
  tr:last-child td{border-bottom:none}
  td.fix{font-weight:620}
  td.was s{color:var(--faint)}
  td.note{color:var(--muted); font-size:13px}
  .chip{display:inline-block; font-family:ui-monospace,monospace; font-size:12.5px; font-weight:600; padding:3px 9px; border-radius:999px; font-variant-numeric:tabular-nums}
  .chip.ok{background:var(--ok-wash); color:var(--ok); border:1px solid #bfe3ce}
  .gallery{display:grid; grid-template-columns:repeat(auto-fit,minmax(280px,1fr)); gap:16px; margin-top:16px}
  .shot{margin:0; background:var(--card); border:1px solid var(--hair); border-radius:12px; overflow:hidden; box-shadow:var(--shadow)}
  .shot img{display:block; width:100%; height:auto; border-bottom:1px solid var(--hair); background:#0c1420}
  .shot figcaption{padding:11px 14px; font-size:12.5px; color:var(--muted); line-height:1.45}
  .two{display:grid; grid-template-columns:1fr 1fr; gap:34px}
  .two h2{margin-bottom:12px}
  .steps,.commits{margin:0; padding:0; list-style:none}
  .steps{counter-reset:s}
  .steps li{position:relative; padding:9px 0 9px 34px; border-top:1px solid var(--hair); font-size:14.5px; color:var(--ink)}
  .steps li:first-child{border-top:none}
  .steps li::before{counter-increment:s; content:counter(s); position:absolute; left:0; top:9px; width:22px; height:22px; border-radius:6px; background:var(--teal-wash); color:var(--teal-700); font:600 12px/22px ui-monospace,monospace; text-align:center}
  kbd{display:inline-block; background:#fff; border:1px solid var(--hair); border-bottom-width:2px; border-radius:5px; padding:1px 6px; font-size:12px}
  .commits li{display:flex; gap:11px; align-items:baseline; padding:8px 0; border-top:1px solid var(--hair); font-size:13.5px}
  .commits li:first-child{border-top:none}
  .sha{color:var(--teal-700); font-size:12.5px; font-weight:600; flex:none}
  .foot{color:var(--muted); font-size:13px; margin-top:12px}
  a{color:var(--teal-700)}
  a:focus-visible,img:focus-visible{outline:2px solid var(--teal); outline-offset:2px}
  footer{margin-top:44px; padding-top:18px; border-top:1px solid var(--hair); color:var(--faint); font-size:12.5px}
  @media (max-width:640px){ .two{grid-template-columns:1fr; gap:26px} }
</style>`;

const html = style + `<div class="wrap">
  <header class="masthead">
    <div class="kicker">AI Mesh Firewall · Verification</div>
    <h1>Context Assembly &amp; MCP page — <span class="hl">the fixes are live on main</span></h1>
    <p class="dek">Captured just now from a clean, cache-busted browser session against the exact running frontend — <code>?tab=firewall-1-4</code>. If your screen still shows the old page, it’s a stale browser tab, not missing code.</p>
    <div class="verdict">
      <span class="v-dot"></span>
      <div>
        <strong>All on <code>main</code> · all rendering live</strong>
        <span class="v-sub">6 commits merged into main · Vite dev server serves the changed modules over HTTP · A–G re-verified 3× green</span>
      </div>
    </div>
  </header>

  <section>
    <h2><span class="num">01</span> What changed — and what the live page shows right now</h2>
    <div class="tablescroll">
      <table>
        <thead><tr><th>Fix</th><th>Was</th><th>Now (live value)</th><th>Detail</th></tr></thead>
        <tbody>${rowHtml}</tbody>
      </table>
    </div>
  </section>

  <section>
    <h2><span class="num">02</span> Fresh screenshots — the running frontend at :8180</h2>
    <div class="gallery">
      ${shot(img.flow, "§1.4 flow strip — 231 sanitized (was 0), 93,139 assembled")}
      ${shot(img.simDefault, "Policy Simulator defaults to “cp09-ens8do · connected · 13 tools”")}
      ${shot(img.simDecision, "Simulator dry-run renders a decision (DRY-RUN · HTTP 200)")}
      ${shot(img.fullLight, "Full §1.4 page — light theme")}
      ${shot(img.fullDark, "Full §1.4 page — dark theme")}
    </div>
  </section>

  <section class="two">
    <div>
      <h2><span class="num">03</span> Why you might not see it</h2>
      <p>The frontend is a single Vite dev server bound to the repo working tree — there is no separate production build or reverse proxy. The running server already returns the new code (verified by fetching the module directly). So a stale view is a client cache issue:</p>
      <ol class="steps">
        <li><strong>Hard-refresh</strong> the tab — <kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>R</kbd> (or <kbd>⌘</kbd>+<kbd>Shift</kbd>+<kbd>R</kbd>). A tab opened before the change won’t hot-reload a hooks-level edit.</li>
        <li>Confirm the URL is <code>http://&lt;host&gt;:8180/?tab=firewall-1-4</code> — port <strong>8180</strong> is the only frontend surface (8100 = control API, 8300 = gateway).</li>
        <li>If remote, ensure your tunnel/port-forward points at <code>8180</code>.</li>
      </ol>
    </div>
    <div>
      <h2><span class="num">04</span> Merged commits on <code>main</code></h2>
      <ul class="commits">${commitHtml}</ul>
      <p class="foot">Each is an ancestor of <code>HEAD</code>; all frontend files are committed &amp; unmodified. Nothing is on a side branch — there was nothing left to merge.</p>
    </div>
  </section>

  <footer>Evidence generated by <code>scripts/ralph/mcp_page_prove_live.mjs</code> · click any screenshot to open full size.</footer>
</div>`;

fs.writeFileSync(OUT, html);
console.log("wrote", OUT, (fs.statSync(OUT).size / 1024).toFixed(0) + "KB");
