"""axe-core on every Lattice page: both themes, desktop and mobile.
Usage: python3 -I axe_lattice.py <lattice_dir> <axe.min.js>
Rules: wcag2a, wcag2aa, wcag21aa, best-practice (same set as the 11 Nov run)."""
import os
import pathlib
import sys

from playwright.sync_api import sync_playwright

D, AXE = pathlib.Path(sys.argv[1]).resolve(), pathlib.Path(sys.argv[2]).read_text()
V = {"desktop": {"viewport": {"width": 1440, "height": 900}}, "mobile": {"viewport": {"width": 390, "height": 844}, "is_mobile": True, "has_touch": True}}
total = clean = 0
with sync_playwright() as p:
    proxy = os.environ.get("HTTPS_PROXY")
    b = p.chromium.launch(executable_path="/usr/bin/google-chrome", proxy={"server": proxy} if proxy else None)
    for vp, opts in V.items():
        ctx = b.new_context(**opts, reduced_motion="reduce")
        for page in ["landing", "console", "policy", "request", "keys"]:
            for theme in ["dark", "light"]:
                pg = ctx.new_page()
                pg.goto((D / f"{page}.html").as_uri() + f"?theme={theme}", wait_until="networkidle")
                pg.evaluate("document.fonts.ready")
                pg.add_script_tag(content=AXE)
                res = pg.evaluate("axe.run(document, {runOnly: {type: 'tag', values: ['wcag2a','wcag2aa','wcag21aa','best-practice']}})")
                v = [(x["id"], x["impact"], len(x["nodes"]), x["nodes"][0]["target"]) for x in res["violations"]]
                total += 1; clean += not v
                print(f"{vp:7} {page:8} {theme:5} violations={len(v)} {v[:4]}")
                pg.close()
        ctx.close()
    b.close()
print(f"{clean} of {total} page variants with no violations")
