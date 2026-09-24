"""Hand-drawn SVG charts. One scale places marks, ticks and labels; colours come from CSS tokens (class names)."""
import html


def _nice_ticks(vmax, n=5):
    raw = vmax / n
    mag = 10 ** len(str(int(raw))) / 10 if raw >= 1 else 0.1
    for m in (1, 2, 2.5, 5, 10):
        step = m * mag
        if vmax / step <= n:
            break
    ticks, v = [], 0.0
    while v <= vmax + 1e-9:
        ticks.append(round(v, 6))
        v += step
    return ticks


def line_chart(series, *, x_label, y_label, x_max, y_max, threshold=None, threshold_label="", width=760, height=360,
               x_ticks=None, y_ticks=None, desc=""):
    """series: [{name, cls, points:[(x, y, failed:bool)], label_at:'end'|'start', dash:bool}]"""
    L, R, T, B = 58, 150, 18, 46
    pw, ph = width - L - R, height - T - B
    xt = x_ticks or _nice_ticks(x_max)
    yt = y_ticks or _nice_ticks(y_max)
    xm, ym = max(xt[-1], x_max), max(yt[-1], y_max)

    def X(v):
        return L + pw * v / xm

    def Y(v):
        return T + ph - ph * min(v, ym) / ym

    o = [f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" aria-label="{html.escape(desc)}">']
    for v in yt:
        o.append(f'<line class="grid" x1="{L}" x2="{L + pw}" y1="{Y(v):.1f}" y2="{Y(v):.1f}"/>')
        o.append(f'<text class="tick" x="{L - 8}" y="{Y(v) + 4:.1f}" text-anchor="end">{v:g}</text>')
    for v in xt:
        o.append(f'<line class="tickmark" x1="{X(v):.1f}" x2="{X(v):.1f}" y1="{T + ph}" y2="{T + ph + 5}"/>')
        o.append(f'<text class="tick" x="{X(v):.1f}" y="{T + ph + 19}" text-anchor="middle">{v:g}</text>')
    o.append(f'<line class="axis" x1="{L}" x2="{L + pw}" y1="{T + ph}" y2="{T + ph}"/>')
    o.append(f'<text class="axis-label" x="{L + pw / 2:.1f}" y="{height - 6}" text-anchor="middle">{html.escape(x_label)}</text>')
    o.append(f'<text class="axis-label" transform="translate(14 {T + ph / 2:.1f}) rotate(-90)" text-anchor="middle">{html.escape(y_label)}</text>')
    if threshold is not None:
        o.append(f'<line class="threshold" x1="{L}" x2="{L + pw}" y1="{Y(threshold):.1f}" y2="{Y(threshold):.1f}"/>')
        o.append(f'<text class="threshold-label" x="{L + pw + 6}" y="{Y(threshold) + 4:.1f}">{html.escape(threshold_label)}</text>')
    used = []
    for s in series:
        pts = s["points"]
        d = " ".join(f"{'M' if i == 0 else 'L'}{X(x):.1f},{Y(y):.1f}" for i, (x, y, _f) in enumerate(pts))
        dash = ' stroke-dasharray="5 4"' if s.get("dash") else ""
        o.append(f'<path class="series {s["cls"]}" d="{d}" fill="none"{dash}/>')
        for x, y, failed in pts:
            cls = "pt-fail" if failed else "pt"
            clipped = y > ym
            o.append(f'<circle class="{cls} {s["cls"]}" cx="{X(x):.1f}" cy="{Y(y):.1f}" r="{4.2 if failed else 3.4}"/>')
            if clipped:
                o.append(f'<text class="clip" x="{X(x):.1f}" y="{Y(y) - 8:.1f}" text-anchor="middle">{y:g}↑</text>')
        lx, ly, _ = pts[-1]
        ty = Y(ly) + 4
        for u in used:
            if abs(u - ty) < 13:
                ty = u + 13
        used.append(ty)
        o.append(f'<text class="series-label {s["cls"]}" x="{X(lx) + 8:.1f}" y="{ty:.1f}">{html.escape(s["name"])}</text>')
    o.append("</svg>")
    return "\n".join(o)


def bar_chart(bars, *, value_label, v_max, width=760, row_h=34, desc="", unit_fmt="{:g}"):
    """bars: [{label, sub, value, cls, note}] horizontal bars, direct-labelled."""
    L, R, T = 250, 120, 8
    pw = width - L - R
    height = T + row_h * len(bars) + 34
    ticks = _nice_ticks(v_max)
    vm = max(ticks[-1], v_max)

    def X(v):
        return L + pw * v / vm

    o = [f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" aria-label="{html.escape(desc)}">']
    base = T + row_h * len(bars)
    for v in ticks:
        o.append(f'<line class="grid" x1="{X(v):.1f}" x2="{X(v):.1f}" y1="{T}" y2="{base}"/>')
        o.append(f'<text class="tick" x="{X(v):.1f}" y="{base + 16}" text-anchor="middle">{v:g}</text>')
    o.append(f'<text class="axis-label" x="{L + pw / 2:.1f}" y="{height - 4}" text-anchor="middle">{html.escape(value_label)}</text>')
    for i, b in enumerate(bars):
        y = T + i * row_h
        o.append(f'<text class="bar-label" x="{L - 10}" y="{y + row_h / 2 - 1:.1f}" text-anchor="end">{html.escape(b["label"])}</text>')
        if b.get("sub"):
            o.append(f'<text class="bar-sub" x="{L - 10}" y="{y + row_h / 2 + 12:.1f}" text-anchor="end">{html.escape(b["sub"])}</text>')
        w = max(X(b["value"]) - L, 1.5)
        o.append(f'<rect class="bar {b["cls"]}" x="{L}" y="{y + 7}" width="{w:.1f}" height="{row_h - 14}" rx="2"/>')
        o.append(f'<text class="bar-value" x="{L + w + 6:.1f}" y="{y + row_h / 2 + 4:.1f}">{unit_fmt.format(b["value"])}'
                 f'{(" · " + html.escape(b["note"])) if b.get("note") else ""}</text>')
    o.append("</svg>")
    return "\n".join(o)
