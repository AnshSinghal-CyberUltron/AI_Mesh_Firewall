"""Build one side-by-side comparison PNG per screen from the desktop captures.

Usage: python3 -I compare.py <shots_dir> <panel_width> [colors]

Reads f6-<direction>-<screen>-desktop.png for the three directions and writes
f6-compare-<screen>.png: a label band per panel (direction name and theme)
above the capture, scaled to <panel_width> px (aspect kept, LANCZOS). With
[colors] the result is reduced to that many palette colours (FASTOCTREE) to
keep the file small enough to embed in a PR body.
"""
import pathlib
import sys

from PIL import Image, ImageDraw, ImageFont

SHOTS = pathlib.Path(sys.argv[1])
PW = int(sys.argv[2])
COLORS = int(sys.argv[3]) if len(sys.argv) > 3 else 0
DIRS = [("checkpoint", "Checkpoint", "light theme"),
        ("night-shift", "Night Shift", "dark theme"),
        ("redline", "Redline", "light theme")]
SCREENS = {"landing": "Public landing page", "console": "Console overview", "policy": "Policy editor"}
BOLD = "/usr/share/fonts/opentype/inter/Inter-Bold.otf"
REG = "/usr/share/fonts/opentype/inter/Inter-Regular.otf"
GUT, PAD, BAND, TITLE = 24, 24, 52, 64
BG, INK, INK2, RULE = (246, 247, 249), (17, 24, 39), (75, 85, 99), (209, 213, 219)

for screen, title in SCREENS.items():
    panels = [Image.open(SHOTS / f"f6-{d}-{screen}-desktop.png").convert("RGB") for d, _, _ in DIRS]
    ph = round(panels[0].height * PW / panels[0].width)
    panels = [p.resize((PW, ph), Image.LANCZOS) for p in panels]
    W = PAD * 2 + PW * 3 + GUT * 2
    H = PAD + TITLE + BAND + ph + PAD
    out = Image.new("RGB", (W, H), BG)
    dr = ImageDraw.Draw(out)
    f_title = ImageFont.truetype(BOLD, 30)
    f_sub = ImageFont.truetype(REG, 18)
    f_lab = ImageFont.truetype(BOLD, 24)
    f_lab2 = ImageFont.truetype(REG, 17)
    dr.text((PAD, PAD + 4), f"F6 directions: {title}", font=f_title, fill=INK)
    dr.text((PAD, PAD + 42), "Desktop 1440x900, primary theme, static mockups with sample data", font=f_sub, fill=INK2)
    y0 = PAD + TITLE
    for i, ((d, name, theme), img) in enumerate(zip(DIRS, panels)):
        x = PAD + i * (PW + GUT)
        dr.text((x, y0 + 10), name, font=f_lab, fill=INK)
        tw = dr.textlength(name, font=f_lab)
        dr.text((x + tw + 12, y0 + 16), theme, font=f_lab2, fill=INK2)
        out.paste(img, (x, y0 + BAND))
        dr.rectangle([x - 1, y0 + BAND - 1, x + PW, y0 + BAND + ph], outline=RULE, width=1)
    path = SHOTS / f"f6-compare-{screen}.png"
    if COLORS:
        out = out.quantize(colors=COLORS, method=Image.Quantize.FASTOCTREE)
    out.save(path, optimize=True)
    print(path.name, out.size, path.stat().st_size)
