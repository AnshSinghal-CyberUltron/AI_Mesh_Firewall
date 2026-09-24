#!/usr/bin/env python3
"""Task 3 step 1: harvest every Redis-key-looking string literal / f-string (and the
channel names) from control-plane and gateway NON-TEST python, with file:line.
A "key-like" string = [a-z0-9_.-]+ followed by one or more ':' segments (f-string
fields normalized to {}), or a known pub/sub channel constant.
Read-only AST walk.
"""
import ast
import json
import os
import re
import sys
from collections import defaultdict

REPO = "/home/contact_cyberultron_com/AI_Mesh_Firewall"
ROOTS = {
    "control": f"{REPO}/control/ai_mesh_control",
    "gateway": f"{REPO}/gateway/ai_mesh_gateway",
    "shared": f"{REPO}/shared/ai_mesh_shared",
}
HERE = os.path.dirname(os.path.abspath(__file__))
SKIP_DIRS = {"tests", "__pycache__", "migrations", "graphify-out", "node_modules", ".venv"}
KEYLIKE = re.compile(r"^[A-Za-z0-9_.\-{}*]+(:[A-Za-z0-9_.\-{}*]*)+$")


def py_files(root):
    for r, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for f in files:
            if f.endswith(".py") and not f.startswith("test_") and f != "conftest.py":
                yield os.path.join(r, f)


def fstring_pattern(node):
    parts = []
    for v in node.values:
        if isinstance(v, ast.Constant) and isinstance(v.value, str):
            parts.append(v.value)
        else:
            parts.append("{}")
    return "".join(parts)


out = defaultdict(list)  # (side, pattern) -> [loc]
for side, root in ROOTS.items():
    for p in py_files(root):
        try:
            tree = ast.parse(open(p, encoding="utf-8", errors="replace").read())
        except SyntaxError:
            continue
        # skip docstrings: collect ids of docstring Constant nodes
        doc_ids = set()
        for n in ast.walk(tree):
            if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if n.body and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant):
                    doc_ids.add(id(n.body[0].value))
        fstring_parts = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.JoinedStr):
                for v in n.values:
                    fstring_parts.add(id(v))
                s = fstring_pattern(n)
                if KEYLIKE.match(s) and " " not in s and not s.startswith(("http", "{}:{}")) and "://" not in s:
                    out[(side, s)].append(f"{os.path.relpath(p, REPO)}:{n.lineno}")
            elif isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in doc_ids and id(n) not in fstring_parts:
                s = n.value
                if KEYLIKE.match(s) and " " not in s and "://" not in s and len(s) < 120:
                    out[(side, s)].append(f"{os.path.relpath(p, REPO)}:{n.lineno}")

rows = sorted(out.items(), key=lambda kv: (kv[0][1], kv[0][0]))
by_pattern = defaultdict(dict)
for (side, s), locs in rows:
    by_pattern[s][side] = locs
print(f"distinct key-like literal patterns: {len(by_pattern)}")
for s, sides in sorted(by_pattern.items()):
    tag = "+".join(sorted(sides))
    print(f"[{tag:22s}] {s!r}")
    for side, locs in sorted(sides.items()):
        print(f"      {side}: {', '.join(locs[:8])}{' ...(+%d)' % (len(locs)-8) if len(locs) > 8 else ''}")
json.dump({s: sides for s, sides in by_pattern.items()}, open(os.path.join(HERE, "04_redis_key_literals.json"), "w"), indent=1)
