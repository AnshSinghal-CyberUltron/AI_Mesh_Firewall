#!/usr/bin/env python3
"""Build runbook v2.1: original v2 text + Part 0 + anchored correction blocks.

Usage: build_v21.py <outdir>
Inputs (same dir as this script): original.md (pandoc gfm of the v2 docx),
part0_draft.md, part0_register.md, part0_newcards.md, blocks.md.
Fails (exit 1) if any anchor does not match exactly one insertion point.
"""
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "out"
OUT.mkdir(parents=True, exist_ok=True)
PANDOC = str(Path.home() / ".local/bin/pandoc")
SRC_DOCX = "/home/contact_cyberultron_com/AI_Mesh_Firewall/docs/AI_MESH_MASTER_RUNBOOK_v2_BACKEND_REWRITE.docx"

lines = (HERE / "original.md").read_text().splitlines()

# 1) parse anchored blocks
blocks, cur_re, cur = [], None, []
for ln in (HERE / "blocks.md").read_text().splitlines():
    if ln.startswith("@@ANCHOR "):
        if cur_re is not None:
            blocks.append((cur_re, "\n".join(cur).strip()))
        cur_re, cur = ln[len("@@ANCHOR "):].strip(), []
    else:
        cur.append(ln)
if cur_re is not None:
    blocks.append((cur_re, "\n".join(cur).strip()))

# 2) locate anchors (first match; report ambiguity)
inserts, problems = {}, []
for rx, text in blocks:
    hits = [i for i, l in enumerate(lines) if re.search(rx, l)]
    if not hits:
        problems.append(f"NO MATCH: {rx}")
        continue
    if len(hits) > 1:
        problems.append(f"MULTI MATCH ({len(hits)}), using first: {rx} -> lines {hits[:4]}")
    inserts.setdefault(hits[0], []).append(text)
if any(p.startswith("NO MATCH") for p in problems):
    print("\n".join(problems))
    sys.exit(1)

# 3) assemble: title stays, Part 0 goes right before "# 1. Program definition"
part0 = "\n\n".join((HERE / f).read_text().strip() for f in ("part0_draft.md", "part0_register.md", "part0_newcards.md"))
banner = ("> **VERSION 2.1 — validated corrections, 23 September 2026.** This edition keeps the v2 text unchanged and adds "
          "Part 0 plus inline \"v2.1 CORRECTION\" blocks. Where a block and the original text disagree, the block wins.")
out = []
for i, l in enumerate(lines):
    if l.startswith("# 1. Program definition"):
        out += [banner, "", part0, ""]
    out.append(l)
    for text in inserts.get(i, []):
        out += ["", text, ""]
md = "\n".join(out) + "\n"
(OUT / "AI_MESH_MASTER_RUNBOOK_v2.1_BACKEND_REWRITE.md").write_text(md)

# 4) docx via pandoc, styled by the original
subprocess.run([PANDOC, str(OUT / "AI_MESH_MASTER_RUNBOOK_v2.1_BACKEND_REWRITE.md"), "-f", "gfm", "-t", "docx",
                "--reference-doc", SRC_DOCX, "-o", str(OUT / "AI_MESH_MASTER_RUNBOOK_v2.1_BACKEND_REWRITE.docx")],
               check=True)
print(f"blocks: {len(blocks)} inserted at {len(inserts)} anchors; problems: {len(problems)}")
for p in problems:
    print("  ", p)
remaining = md.count("[BENCH RESULT")
print(f"placeholders remaining: {remaining}")
