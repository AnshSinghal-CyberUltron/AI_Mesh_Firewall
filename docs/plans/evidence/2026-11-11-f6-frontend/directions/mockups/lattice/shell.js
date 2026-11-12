// Lattice shell: sidebar, mobile bar, page head and the box helper shared by
// every Lattice screen. Plain JS, no build step. Reads window.SAMPLE.
(function () {
  const S = window.SAMPLE;
  const theme = document.documentElement.dataset.theme || "dark";
  const mark = theme === "light" ? "zeroshield-wordmark.png" : "zeroshield-wordmark-ondark.png";

  const P = {
    grid: '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
    list: '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
    flask: '<path d="M9 3h6M10 3v6L4.5 18.5A2 2 0 0 0 6.2 21.5h11.6a2 2 0 0 0 1.7-3L14 9V3"/><path d="M7 15h10"/>',
    plug: '<path d="M9 2v6M15 2v6M6 8h12v3a6 6 0 0 1-12 0zM12 17v5"/>',
    sliders: '<path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6"/>',
    search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
    sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
    menu: '<path d="M4 6h16M4 12h16M4 18h16"/>',
    chev: '<path d="m6 9 6 6 6-6"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    copy: '<rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1"/>',
    link: '<path d="M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7"/><path d="M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7"/>',
    play: '<path d="m7 4 13 8-13 8z"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    trash: '<path d="M3 6h18M8 6V4h8v2M6 6l1 15h10l1-15"/>',
    back: '<path d="m15 18-6-6 6-6"/>',
    more: '<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>',
  };
  const icon = (n) => `<svg class="i" viewBox="0 0 24 24" aria-hidden="true">${P[n]}</svg>`;
  const fmt = (n) => Number(n).toLocaleString("en-US");

  const MODULES = [
    ["1.1", "Gateway and keys", "keys"], ["1.2", "Policies", "policy"], ["1.3", "RAG and vectors"], ["1.4", "MCP tools"],
    ["1.5", "Model routing"], ["1.6", "Kill switch"], ["1.7", "Output guardrails"],
  ];

  function sidebar(active) {
    const a = (key, inner, href = "#") => `<a href="${href}"${active === key ? ' class="on" aria-current="page"' : ""}>${inner}</a>`;
    return `<aside class="side" aria-label="Console navigation">
      <a class="brand" href="landing.html"><img src="${mark}" alt="ZeroShield"><span>AI Mesh Firewall</span></a>
      <nav class="nav" aria-label="Sections">
        ${a("console", icon("grid") + "Overview", "console.html")}
        ${a("request", icon("list") + "Requests", "request.html")}
        <h4>Checkpoints</h4>
        ${MODULES.map(([id, name, key]) => a(key || id, `<span class="n">${id}</span>${name}`, key ? key + ".html" : "#")).join("")}
        <h4>Tools</h4>
        ${a("sim", icon("flask") + "Attack simulator")}
        ${a("conn", icon("plug") + "Connections")}
      </nav>
      <div class="foot nav">
        ${a("settings", icon("sliders") + "Settings")}
        <div class="org"><span class="av">DO</span><span><b>${S.org}</b>Production</span></div>
      </div>
    </aside>`;
  }

  function mbar() {
    return `<div class="mbar"><button class="btn sq ghost" aria-label="Open navigation">${icon("menu")}</button>
      <img src="${mark}" alt="ZeroShield"><span class="p">AI Mesh Firewall</span>
      <span class="r"><span class="avatar" aria-label="Signed in as ${S.user}">AR</span></span></div>`;
  }

  function head({ eyebrow, title, sub, ctrls }) {
    const std = `<button class="btn search" aria-label="Search">${icon("search")}Find a request, key or rule<kbd>Ctrl K</kbd></button>
      <button class="btn desk">${icon("clock")}Last 24 hours${icon("chev")}</button>
      <button class="btn sq desk" aria-label="Switch theme">${icon("sun")}</button>
      <span class="avatar desk" aria-label="Signed in as ${S.user}">AR</span>`;
    return `<header class="head"><div><p class="eyebrow">${eyebrow}</p><h1>${title}</h1><p class="sub">${sub}</p></div>
      <div class="ctrls">${ctrls ?? std}</div></header>`;
  }

  // One box: title, one-line purpose, body, and the source line.
  // src is a short source label; path is the endpoint it stands for (shown on hover).
  function box({ span = "s3", title, purpose, tag = "", body, src, path = "", fresh = "fresh", age, cls = "", label }) {
    const fr = { fresh: "Fresh", stale: "Stale", record: "Record" }[fresh];
    return `<section class="box ${span} ${cls}" aria-label="${label || title}">
      <div class="bh"><h2>${title}</h2><span class="r">${tag}</span></div>
      <p class="bp">${purpose}</p>
      <div class="bb">${body}</div>
      <div class="bf"><span class="fr ${fresh}">${fr}</span><span class="age">${age}</span><span class="src"${path ? ` title="${path}"` : ""}>${src}</span></div>
    </section>`;
  }

  const chip = (tone, word, extra = "") => `<span class="chip ${tone} ${extra}">${word}</span>`;
  const id = (t) => `<span class="id">${t}</span>`;

  // Sparkline path from numbers, drawn into a 100 x 40 viewBox.
  function spark(values, { stroke = "var(--accent)", fill = true } = {}) {
    const max = Math.max(...values), min = Math.min(...values);
    const pts = values.map((v, i) => [(i / (values.length - 1)) * 100, 36 - ((v - min) / (max - min || 1)) * 30]);
    const d = pts.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(2)} ${y.toFixed(2)}`).join(" ");
    const area = `${d} L100 40 L0 40 Z`;
    return `<svg class="spark" viewBox="0 0 100 40" preserveAspectRatio="none" aria-hidden="true">
      ${fill ? `<path d="${area}" fill="color-mix(in srgb, ${stroke} 16%, transparent)"/>` : ""}
      <path d="${d}" fill="none" stroke="${stroke}" stroke-width="1.6" vector-effect="non-scaling-stroke"/></svg>`;
  }

  // 24 hourly cells: the Lattice signature. level 0..1 sets the fill; tone colours it.
  function cells(levels, tone = "accent", label = "Last 24 hours, one cell per hour") {
    return `<div class="cells" role="img" aria-label="${label}">${levels.map((l) => {
      const t = typeof l === "object" ? l : { v: l, tone };
      return `<i style="background:color-mix(in srgb, var(--${t.tone}) ${Math.round(12 + t.v * 78)}%, var(--surface2))"></i>`;
    }).join("")}</div>`;
  }

  function mount(active, html) {
    document.body.innerHTML = `<div class="app">${sidebar(active)}<main class="main" id="main">${mbar()}${html}</main></div>`;
  }

  window.L = { S, icon, fmt, sidebar, mbar, head, box, chip, id, spark, cells, mount, mark, theme };
})();
