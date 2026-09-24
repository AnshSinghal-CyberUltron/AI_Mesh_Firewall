#!/usr/bin/env python3
"""Task 1(d) refinement: for every FirewallConfig gateway-payload key, classify each
quoted-key occurrence in gateway non-test code by syntactic context and capture the
receiver expression, so declaration-only hits (config.py env-default dict literal,
config_sync.py type tables, comments) are separated from real reads.

Read-only source analysis. Input: 01_firewallconfig_fields.json (payload map).
"""
import json
import os
import re
import tokenize
import io

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = "/home/contact_cyberultron_com/AI_Mesh_Firewall"
d = json.load(open(os.path.join(HERE, "01_firewallconfig_fields.json")))
payload_keys = list(d["payload_map"].keys()) + ["org_slug"]
files = [os.path.join(REPO, p) for p in d["gw_files"]]


def comment_or_docstring_lines(path):
    """Return set of line numbers that are comments or inside string-only statements (docstrings)."""
    src = open(path, encoding="utf-8", errors="replace").read()
    lines = set()
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
    except Exception:
        return lines
    prev_sig = None
    for i, t in enumerate(toks):
        if t.type == tokenize.COMMENT:
            lines.add(t.start[0])
        if t.type == tokenize.STRING:
            # docstring heuristic: string token that is the first significant token on its logical line
            if prev_sig is None or prev_sig.type in (tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT, tokenize.NL):
                # check next significant token is NEWLINE (i.e. bare string statement)
                j = i + 1
                while j < len(toks) and toks[j].type in (tokenize.NL, tokenize.COMMENT):
                    j += 1
                if j < len(toks) and toks[j].type == tokenize.NEWLINE:
                    for ln in range(t.start[0], t.end[0] + 1):
                        lines.add(ln)
        if t.type not in (tokenize.NL, tokenize.COMMENT):
            prev_sig = t
    return lines


CMT = {p: comment_or_docstring_lines(p) for p in files}
TXT = {p: open(p, encoding="utf-8", errors="replace").read().splitlines() for p in files}


def classify(path, ln, line, key):
    rel = os.path.relpath(path, REPO)
    q = r"""["']""" + re.escape(key) + r"""["']"""
    if ln in CMT[path]:
        return "COMMENT/DOCSTRING", ""
    if rel.endswith("config_sync.py") and ln <= 110:
        return "DECL:config_sync type table", ""
    if rel.endswith("gateway/ai_mesh_gateway/config.py"):
        return "DECL:config.py env-default", ""
    m = re.search(r"([\w\.\)\]\(\"' ]{0,60}?)\.get\(\s*" + q, line)
    if m:
        return "READ:.get", m.group(1).strip()[-50:]
    m = re.search(r"([\w\.\)\]]+)\s*\[\s*" + q + r"\s*\](?!\s*=[^=])", line)
    if m:
        return "READ:subscript", m.group(1)
    m = re.search(r"([\w\.\)\]]+)\s*\[\s*" + q + r"\s*\]\s*=[^=]", line)
    if m:
        return "WRITE:subscript-assign", m.group(1)
    m = re.search(q + r"\s+(not\s+)?in\s+([\w\.\(\)]+)", line)
    if m:
        return "READ:membership", m.group(2)
    m = re.search(r"(_action|_enabled|_weight|_flag|_num|_bool|_str|_float|_int|_get)\w*\(\s*(?:[\"'][\w]+[\"']\s*,\s*)?" + q, line)
    if m:
        return "READ:helper-call", m.group(1)
    if re.search(q + r"\s*:", line):
        return "DICT-LITERAL key (write/emit)", ""
    if re.search(r"^\s*" + q + r"\s*,?\s*(#.*)?$", line) or re.search(q + r"\s*,", line):
        return "TUPLE/LIST element", ""
    return "OTHER", ""


rows = {}
for key in payload_keys:
    pat = re.compile(r"""(["'])""" + re.escape(key) + r"""\1""")
    hits = []
    for p in files:
        for i, line in enumerate(TXT[p], 1):
            if pat.search(line):
                cls, recv = classify(p, i, line, key)
                hits.append({"loc": f"{os.path.relpath(p, REPO)}:{i}", "class": cls, "recv": recv, "line": line.strip()[:170]})
    rows[key] = hits

summary = []
for key, hits in rows.items():
    reads = [h for h in hits if h["class"].startswith("READ")]
    tuples = [h for h in hits if h["class"] == "TUPLE/LIST element"]
    summary.append((key, len(hits), len(reads), len(tuples)))

print("payload_key | total quoted hits | READ-context hits | TUPLE/LIST hits (need manual check)")
for s in summary:
    print(f"{s[0]:36s} {s[1]:4d} {s[2]:4d} {s[3]:4d}")
print()
for key, hits in rows.items():
    print(f"### {key}")
    for h in hits:
        print(f"   {h['class']:32s} recv={h['recv']!s:30s} {h['loc']}: {h['line']}")
json.dump(rows, open(os.path.join(HERE, "02_payload_key_read_contexts.json"), "w"), indent=1)
