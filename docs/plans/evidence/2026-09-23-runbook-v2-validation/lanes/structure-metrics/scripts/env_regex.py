#!/usr/bin/env python3
"""Regex (grep-style) env-var read counts, to test if the runbook's 144/232 came from a text search.
Usage: env_regex.py <root> <subdir>... [--tests]"""
import os, re, sys, itertools
args = [a for a in sys.argv[1:] if not a.startswith("--")]
root, subs = args[0], args[1:]
inc_tests = "--tests" in sys.argv
SKIPD = {"__pycache__", ".claude-flow", ".swarm", "graphify-out", ".venv", "node_modules", ".claude"}
files = []
for s in subs:
    for dp, dn, fn in os.walk(os.path.join(root, s)):
        dn[:] = [d for d in dn if d not in SKIPD and (inc_tests or d not in ("tests", "migrations"))]
        for f in fn:
            if f.endswith(".py") and (inc_tests or (not f.startswith("test_") and f != "conftest.py")):
                files.append(os.path.join(dp, f))
Q = r"[\"']"
N = r"([A-Za-z_][A-Za-z0-9_]*)"
pats = {
 "getenv": rf"\bgetenv\(\s*{Q}{N}{Q}",
 "environ.get": rf"environ\.get\(\s*{Q}{N}{Q}",
 "environ[]": rf"environ\[\s*{Q}{N}{Q}\s*\]",
 "in_environ": rf"{Q}{N}{Q}\s+in\s+os\.environ",
 "environ.setdefault/pop": rf"environ\.(?:setdefault|pop)\(\s*{Q}{N}{Q}",
 "get_env(": rf"\bget_env\(\s*{Q}{N}{Q}",
 "_env_int/_env_float(": rf"\b_env_(?:int|float|bool)\(\s*{Q}{N}{Q}",
}
hits = {k: [] for k in pats}
for p in files:
    try:
        text = open(p, encoding="utf-8").read()
    except Exception:
        continue
    for k, rx in pats.items():
        for m in re.finditer(rx, text):
            hits[k].append((p, m.group(1)))
def summarize(keys):
    allh = [h for k in keys for h in hits[k]]
    return len({n for _, n in allh}), len(allh)
print(f"scope={subs} tests={inc_tests} files={len(files)}")
for k in pats:
    print(f"  {k:24s} names/sites = {summarize([k])}")
combos = [["getenv", "environ.get"], ["getenv", "environ.get", "environ[]"],
          ["getenv", "environ.get", "environ[]", "in_environ", "environ.setdefault/pop"],
          ["getenv", "environ.get", "get_env("],
          ["getenv", "environ.get", "environ[]", "get_env("],
          list(pats)]
for c in combos:
    print(f"  {'+'.join(c):95s} names/sites = {summarize(c)}")
