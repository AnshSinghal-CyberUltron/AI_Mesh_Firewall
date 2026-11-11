// Tiny inline-SVG chart helpers for the static mockups. Colours come from the
// direction's CSS custom properties so the same chart re-themes itself.
(function () {
  const ns = "http://www.w3.org/2000/svg";
  const el = (tag, attrs) => {
    const n = document.createElementNS(ns, tag);
    for (const [k, v] of Object.entries(attrs || {})) n.setAttribute(k, v);
    return n;
  };
  const series = ["allow", "redact", "monitor", "block"];

  // Stacked bars per hour. opts: { keys, gap, radius, colors: {key: cssVar} }
  function stackedBars(host, rows, opts = {}) {
    const keys = opts.keys || series;
    const w = host.clientWidth || 600;
    const h = opts.height || host.clientHeight || 180;
    const svg = el("svg", { width: w, height: h, viewBox: `0 0 ${w} ${h}`, role: "img", "aria-label": opts.label || "Verdicts per hour" });
    const max = Math.max(...rows.map((r) => keys.reduce((a, k) => a + r[k], 0)));
    const bw = w / rows.length;
    const gap = opts.gap ?? Math.max(2, bw * 0.25);
    const axisH = opts.axis ? 18 : 0;
    const plotH = h - axisH;
    if (opts.gridlines) {
      for (let i = 1; i <= 3; i++) {
        const y = plotH - (plotH * i) / 4;
        svg.appendChild(el("line", { x1: 0, x2: w, y1: y, y2: y, stroke: "var(--line)", "stroke-width": 1 }));
      }
    }
    rows.forEach((r, i) => {
      let y = plotH;
      keys.forEach((k) => {
        const v = r[k];
        if (!v) return;
        const bh = Math.max(opts.minSeg ?? 1.5, (v / max) * (plotH - 4));
        y -= bh;
        svg.appendChild(el("rect", {
          x: i * bw + gap / 2, y, width: bw - gap, height: bh,
          fill: `var(--${(opts.colors && opts.colors[k]) || k})`, rx: opts.radius ?? 0,
        }));
      });
      if (opts.axis && i % 6 === 0) {
        const t = el("text", { x: i * bw + gap / 2, y: h - 4, fill: "var(--ink2)", "font-size": 11, "font-family": "var(--font-ui)" });
        t.textContent = `${String(r.h).padStart(2, "0")}:00`;
        svg.appendChild(t);
      }
    });
    host.replaceChildren(svg);
  }

  // Single-series sparkline (line + optional area)
  function sparkline(host, values, opts = {}) {
    const w = host.clientWidth || 120;
    const h = opts.height || host.clientHeight || 32;
    const max = Math.max(...values), min = Math.min(...values);
    const pts = values.map((v, i) => [(i / (values.length - 1)) * (w - 2) + 1, h - 2 - ((v - min) / (max - min || 1)) * (h - 4)]);
    const d = pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(" ");
    const svg = el("svg", { width: w, height: h, viewBox: `0 0 ${w} ${h}`, "aria-hidden": "true" });
    if (opts.area) svg.appendChild(el("path", { d: `${d} L${w - 1},${h} L1,${h} Z`, fill: opts.area, opacity: opts.areaOpacity ?? 0.18 }));
    svg.appendChild(el("path", { d, fill: "none", stroke: opts.stroke || "var(--accent-text)", "stroke-width": opts.width || 1.75, "stroke-linejoin": "round" }));
    host.replaceChildren(svg);
  }

  window.MockCharts = { stackedBars, sparkline };
  window.fmt = (n) => Number(n).toLocaleString("en-US");
})();
