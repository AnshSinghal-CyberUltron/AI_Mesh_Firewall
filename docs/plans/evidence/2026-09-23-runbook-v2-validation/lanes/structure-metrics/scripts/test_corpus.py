#!/usr/bin/env python3
"""Count Python test files + lines at a git commit (reads blobs from git, not the working tree).
Usage: test_corpus.py <repo> <commit> <out.json>
Definitions:
  T1 name-pattern: basename matches test_*.py or *_test.py (pytest default discovery pattern)
  T2 T1 + conftest.py
  T3 any .py file under a directory named tests/ or test/ (+ T1 anywhere)
Scopes: whole commit tree; excluding vendored/venv/worktree dirs; gateway/ only; gateway/ai_mesh_gateway/tests only."""
import subprocess, sys, json, re, os
from collections import Counter
repo, commit, outp = sys.argv[1:4]
names = subprocess.run(["git", "-C", repo, "ls-tree", "-r", "--name-only", commit], capture_output=True, text=True, check=True).stdout.split("\n")
names = [n for n in names if n.endswith(".py")]
EXCL = re.compile(r"(^|/)(\.venv[^/]*|venv|site-packages|node_modules|\.claude/worktrees)/")
def is_t1(p): b = os.path.basename(p); return (b.startswith("test_") and b.endswith(".py")) or b.endswith("_test.py")
def is_t2(p): return is_t1(p) or os.path.basename(p) == "conftest.py"
def is_t3(p): return is_t1(p) or bool(re.search(r"(^|/)tests?/", p))
# line counts via cat-file --batch
proc = subprocess.Popen(["git", "-C", repo, "cat-file", "--batch"], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
def lines_of(path):
    proc.stdin.write(f"{commit}:{path}\n".encode()); proc.stdin.flush()
    hdr = proc.stdout.readline().split()
    size = int(hdr[2]); data = proc.stdout.read(size); proc.stdout.read(1)
    return data.count(b"\n")
cache = {}
res = {}
scopes = {
    "whole_tree": lambda p: True,
    "excl_venv_worktrees": lambda p: not EXCL.search(p),
    "gateway/": lambda p: p.startswith("gateway/"),
    "gateway/ai_mesh_gateway/tests/": lambda p: p.startswith("gateway/ai_mesh_gateway/tests/"),
    "non-gateway": lambda p: not p.startswith("gateway/") and not EXCL.search(p),
}
for sn, sf in scopes.items():
    for dn, df in (("T1_test_*.py|*_test.py", is_t1), ("T2_T1+conftest", is_t2), ("T3_T1+any .py under tests/", is_t3)):
        fl = [p for p in names if sf(p) and df(p)]
        tot = 0
        for p in fl:
            if p not in cache: cache[p] = lines_of(p)
            tot += cache[p]
        res[f"{sn} | {dn}"] = {"files": len(fl), "lines": tot}
t1 = [p for p in names if is_t1(p)]
top = Counter(p.split("/")[0] if "/" in p else "." for p in t1)
dirs = Counter("/".join(p.split("/")[:3]) for p in t1)
json.dump({"commit": commit, "py_files_in_tree": len(names), "results": res, "T1_by_top_dir": top, "T1_by_3level_dir": dirs,
           "T1_files": sorted(t1)}, open(outp, "w"), indent=1)
for k, v in res.items(): print(f"{k:70s} files={v['files']:4d} lines={v['lines']}")
print("T1 by top dir:", dict(top))
