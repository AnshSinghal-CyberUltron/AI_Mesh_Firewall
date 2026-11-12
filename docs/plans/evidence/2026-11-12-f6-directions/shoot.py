"""Render the F6 direction mockups (static HTML) to PNG with headless Chrome.

Usage: python3 -I shoot.py <mockups_dir> <out_dir>

For each direction x screen: desktop 1440x900 (DSF 1) and mobile 390x844 (DSF 2,
isMobile, touch), primary theme, viewport-only (what the owner sees first).
The console overview is also captured in each direction's alternate theme at
desktop. Pages are opened from file:// exactly as committed; the only network
traffic is Google Fonts, which the pages @import (through HTTPS_PROXY if set).
Browser: $CHROME, default /usr/bin/google-chrome.

Writes <out_dir>/f6-<direction>-<screen>-<desktop|mobile>.png and a JSON log
(<out_dir>/shoot-log.json) with console errors, failed requests, horizontal
overflow and the load status of each declared font family. PNGs are re-saved
with zlib optimisation (lossless).
"""
import json
import os
import pathlib
import sys
import time

from PIL import Image
from playwright.sync_api import sync_playwright

MOCK = pathlib.Path(sys.argv[1]).resolve()
OUT = pathlib.Path(sys.argv[2]).resolve()
OUT.mkdir(parents=True, exist_ok=True)

DIRECTIONS = ["checkpoint", "night-shift", "redline"]
SCREENS = ["landing", "console", "policy"]
ALT = {"checkpoint": "dark", "night-shift": "light", "redline": "dark"}
FAMILIES = {
    "checkpoint": ["Overpass", "Overpass Mono"],
    "night-shift": ["Atkinson Hyperlegible Next", "Atkinson Hyperlegible Mono"],
    "redline": ["Spectral", "Public Sans", "JetBrains Mono"],
}
VIEW = {
    "desktop": {"viewport": {"width": 1440, "height": 900}, "device_scale_factor": 1},
    "mobile": {"viewport": {"width": 390, "height": 844}, "device_scale_factor": 2,
               "is_mobile": True, "has_touch": True},
}

jobs = []
for d in DIRECTIONS:
    for s in SCREENS:
        for vp in ("desktop", "mobile"):
            jobs.append((d, s, None, vp))
    jobs.append((d, "console", ALT[d], "desktop"))

log = []
t0 = time.time()
with sync_playwright() as p:
    proxy = os.environ.get("HTTPS_PROXY")
    browser = p.chromium.launch(
        executable_path=os.environ.get("CHROME", "/usr/bin/google-chrome"),
        proxy={"server": proxy} if proxy else None,
    )
    for vp in ("desktop", "mobile"):
        ctx = browser.new_context(**VIEW[vp], reduced_motion="reduce")
        for d, s, theme, jvp in jobs:
            if jvp != vp:
                continue
            page = ctx.new_page()
            errors, failed = [], []
            page.on("pageerror", lambda e, errs=errors: errs.append(str(e)))
            page.on("console", lambda m, errs=errors: errs.append(m.text) if m.type == "error" else None)
            page.on("requestfailed", lambda r, fl=failed: fl.append(f"{r.url[:100]} {r.failure}"))
            url = (MOCK / d / f"{s}.html").as_uri() + (f"?theme={theme}" if theme else "")
            page.goto(url, wait_until="networkidle")
            page.evaluate("document.fonts.ready")
            page.wait_for_timeout(400)
            fonts = page.evaluate(
                """(fams) => Object.fromEntries(fams.map(f => [f,
                    [...document.fonts].filter(x => x.family.replace(/["']/g,'') === f && x.status === 'loaded').length]))""",
                FAMILIES[d],
            )
            sw, cw, sh = page.evaluate(
                "[document.documentElement.scrollWidth, document.documentElement.clientWidth, document.documentElement.scrollHeight]"
            )
            text_len = page.evaluate("document.body.innerText.trim().length")
            screen = s if not theme else f"{s}-{theme}"
            name = f"f6-{d}-{screen}-{vp}.png"
            page.screenshot(path=str(OUT / name))
            with Image.open(OUT / name) as im:  # lossless recompression only
                im.load()
                im.save(OUT / name, optimize=True)
            rec = {"file": name, "url": url.replace(str(MOCK), "<mockups>"), "fonts_loaded_faces": fonts,
                   "scroll_width": sw, "client_width": cw, "scroll_height": sh, "text_chars": text_len,
                   "horizontal_overflow": sw > cw, "errors": errors, "failed_requests": failed}
            log.append(rec)
            print(json.dumps(rec))
            page.close()
        ctx.close()
    chrome_version = browser.version
    browser.close()

(OUT / "shoot-log.json").write_text(json.dumps(
    {"chrome": chrome_version, "seconds": round(time.time() - t0, 1), "pages": log}, indent=2))
print(f"done {len(log)} pages in {time.time() - t0:.1f}s, chrome {chrome_version}")
