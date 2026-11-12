"""WCAG contrast for colour pairs Lattice uses beyond tokens.mjs `pairs`.
Usage: python3 -I contrast_extra.py <lattice/tokens.css>"""
import pathlib
import re
import sys

css = pathlib.Path(sys.argv[1]).read_text()
blocks = {"dark": css.split(':root[data-theme="light"]')[0], "light": css.split(':root[data-theme="light"]')[1]}
def tok(block):
    return {m.group(1): m.group(2) for m in re.finditer(r"--([a-z0-9-]+):\s*(#[0-9A-Fa-f]{6});", block)}
def rgb(h): return [int(h[i:i + 2], 16) for i in (1, 3, 5)]
def mix(a, b, p):  # CSS color-mix(in srgb, a p%, b)
    A, B = rgb(a), rgb(b); return "#" + "".join(f"{round(x * p + y * (1 - p)):02X}" for x, y in zip(A, B))
def lum(h):
    c = [v / 255 for v in rgb(h)]; c = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
def ratio(a, b):
    x, y = sorted([lum(a), lum(b)], reverse=True); return (x + 0.05) / (y + 0.05)
fails = 0
for theme, block in blocks.items():
    t = tok(block)
    if theme == "light":
        base = tok(blocks["dark"]); base.update(t); t = base
    navon = mix(t["accent"], t["nav"], 0.14); sel = mix(t["accent"], t["surface"], 0.10); tr = mix(t["accent"], t["surface"], 0.08)
    checks = [
        ("allow-text", t["allow-text"], "surface2 (footer 'Fresh')", t["surface2"], 4.5),
        ("monitor-text", t["monitor-text"], "surface2 (footer 'Stale')", t["monitor-text"] and t["surface2"], 4.5),
        ("ink2", t["ink2"], "surface2 (footer source, age)", t["surface2"], 4.5),
        ("redact-text", t["redact-text"], "surface2 (chip on inset)", t["surface2"], 4.5),
        ("block-text", t["block-text"], "surface2 (chip on inset)", t["surface2"], 4.5),
        ("monitor-text", t["monitor-text"], "page (header note)", t["page"], 4.5),
        ("accent-text", t["accent-text"], "active nav fill", navon, 4.5),
        ("nav-ink", t["nav-ink"], "active nav fill", navon, 4.5),
        ("ink", t["ink"], "selected policy fill", sel, 4.5),
        ("ink", t["ink"], "selected table row", tr, 4.5),
        ("ink2", t["ink2"], "page (eyebrow, sub)", t["page"], 4.5),
        ("redact-ink", t["redact-ink"], "redact (placeholder chip)", t["redact"], 4.5),
    ]
    for fg_name, fg, bg_name, bg, floor in checks:
        r = ratio(fg, bg); ok = r >= floor; fails += not ok
        print(f"{theme:5} {fg_name:12} {fg} on {bg_name:30} {bg}  {r:5.2f}  {'pass' if ok else 'FAIL'}")
print("extra pairs:", 2 * 12, "failures:", fails)
sys.exit(1 if fails else 0)
