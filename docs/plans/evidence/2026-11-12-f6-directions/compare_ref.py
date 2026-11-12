"""Side by side: the AI-DSPM reference page next to the Lattice screen it inspired.

Usage: python3 -I compare_ref.py <shots_dir> <ref_dir> <panel_width> [colors]

Reads ref-aidspm-<page>-desktop.png from <ref_dir> and f6-lattice-<screen>-desktop.png
from <shots_dir>; writes <shots_dir>/f6-compare-lattice-<screen>.png. Captures are
scaled to <panel_width> (LANCZOS); with [colors] the result is palette-reduced
(FASTOCTREE) to keep it small enough to embed in a PR body.
"""
import pathlib
import sys

from PIL import Image, ImageDraw, ImageFont

SHOTS, REF = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
PW = int(sys.argv[3])
COLORS = int(sys.argv[4]) if len(sys.argv) > 4 else 0
PAIRS = {
    # screen: (title, reference page, reference label)
    "landing": ("Public landing page", "landing", "aidspm.zeroshield.ai/"),
    "console": ("Console overview", "app", "aidspm.zeroshield.ai/app"),
    "policy": ("Policy editor", "policies", "aidspm.zeroshield.ai/policies"),
    "request": ("Request detail (Scan Detail)", "detail", "aidspm.zeroshield.ai/findings/fnd-2291, a record page"),
    "keys": ("Gateway and API keys", "integrations", "aidspm.zeroshield.ai/integrations, nearest page (no key page)"),
}
BOLD = "/usr/share/fonts/opentype/inter/Inter-Bold.otf"
REG = "/usr/share/fonts/opentype/inter/Inter-Regular.otf"
GUT, PAD, BAND, TITLE = 28, 24, 52, 64
BG, INK, INK2, RULE = (246, 247, 249), (17, 24, 39), (75, 85, 99), (209, 213, 219)

for screen, (title, ref, ref_label) in PAIRS.items():
    left = Image.open(REF / f"ref-aidspm-{ref}-desktop.png").convert("RGB")
    right = Image.open(SHOTS / f"f6-lattice-{screen}-desktop.png").convert("RGB")
    ph = round(left.height * PW / left.width)
    panels = [(left, "AI-DSPM", f"reference · {ref_label}"), (right, "Lattice", "new direction · dark theme, sample data")]
    W, H = PAD * 2 + PW * 2 + GUT, PAD + TITLE + BAND + ph + PAD
    out = Image.new("RGB", (W, H), BG)
    dr = ImageDraw.Draw(out)
    dr.text((PAD, PAD + 4), f"F6 Lattice next to AI-DSPM: {title}", font=ImageFont.truetype(BOLD, 30), fill=INK)
    dr.text((PAD, PAD + 42), "Desktop 1440x900. Left: the owner's AI-DSPM product (public demo, synthetic data). Right: the Lattice mockup.",
            font=ImageFont.truetype(REG, 18), fill=INK2)
    y0 = PAD + TITLE
    for i, (img, name, sub) in enumerate(panels):
        img = img.resize((PW, ph), Image.LANCZOS)
        x = PAD + i * (PW + GUT)
        f_lab = ImageFont.truetype(BOLD, 24)
        dr.text((x, y0 + 10), name, font=f_lab, fill=INK)
        dr.text((x + dr.textlength(name, font=f_lab) + 12, y0 + 16), sub, font=ImageFont.truetype(REG, 17), fill=INK2)
        out.paste(img, (x, y0 + BAND))
        dr.rectangle([x - 1, y0 + BAND - 1, x + PW, y0 + BAND + ph], outline=RULE, width=1)
    if COLORS:
        out = out.quantize(colors=COLORS, method=Image.Quantize.FASTOCTREE)
    path = SHOTS / f"f6-compare-lattice-{screen}.png"
    out.save(path, optimize=True)
    print(path.name, out.size, path.stat().st_size)
