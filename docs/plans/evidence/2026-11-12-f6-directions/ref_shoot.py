"""Reference captures of the AI-DSPM console (public demo, synthetic data).

Usage: python3 -I ref_shoot.py <out_dir> <url> <name> [<url> <name> ...]
Desktop 1440x900 (DSF 1) and mobile 390x844 (DSF 2), viewport only.
"""
import json
import os
import pathlib
import sys

from PIL import Image
from playwright.sync_api import sync_playwright

OUT = pathlib.Path(sys.argv[1]); OUT.mkdir(parents=True, exist_ok=True)
pairs = list(zip(sys.argv[2::2], sys.argv[3::2]))
VIEW = {
    "desktop": {"viewport": {"width": 1440, "height": 900}, "device_scale_factor": 1},
    "mobile": {"viewport": {"width": 390, "height": 844}, "device_scale_factor": 2, "is_mobile": True, "has_touch": True},
}
with sync_playwright() as p:
    proxy = os.environ.get("HTTPS_PROXY")
    b = p.chromium.launch(executable_path=os.environ.get("CHROME", "/usr/bin/google-chrome"),
                          proxy={"server": proxy} if proxy else None)
    for vp, opts in VIEW.items():
        ctx = b.new_context(**opts, reduced_motion="reduce")
        for url, name in pairs:
            pg = ctx.new_page()
            errs = []
            pg.on("pageerror", lambda e, errs=errs: errs.append(str(e)[:200]))
            pg.goto(url, wait_until="networkidle", timeout=60000)
            pg.evaluate("document.fonts.ready")
            pg.wait_for_timeout(1500)
            f = OUT / f"ref-aidspm-{name}-{vp}.png"
            pg.screenshot(path=str(f))
            with Image.open(f) as im:
                im.load(); im.save(f, optimize=True)
            info = pg.evaluate("({title: document.title, url: location.href, h: document.documentElement.scrollHeight, fonts: [...new Set([...document.fonts].filter(x=>x.status==='loaded').map(x=>x.family))]})")
            print(json.dumps({"file": f.name, "errors": errs, **info}))
            pg.close()
        ctx.close()
    b.close()
