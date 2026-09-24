#!/usr/bin/env python3
"""Aggregate py-spy raw (collapsed) stacks from all gateway workers: self-time share by function and
by file, and inclusive share for selected v1 functions. usage: pyspy_summary.py DIR"""
import sys, re
from collections import Counter
from pathlib import Path
# Leaves that are blocking waits py-spy (--nonblocking) does not classify as idle: the thread-pool
# idle get() (CPython 3.12 concurrent/futures/thread.py:90), the policy-engine worker's idle
# SimpleQueue.get() (policy_engine.py:304) and the event loop parked in epoll (asyncio/runners.py:118).
IDLE_LEAVES = ("_worker (concurrent/futures/thread.py:90)", "_loop (ai_mesh_gateway/policy_engine.py:304)",
               "run (asyncio/runners.py:118)")
selfc, filec, incl = Counter(), Counter(), Counter()
total = 0
idle = 0
for f in Path(sys.argv[1]).glob("worker-*.txt"):
    for line in f.read_text().splitlines():
        if not line.strip():
            continue
        stack, _, n = line.rpartition(" ")
        n = int(n)
        frames = stack.split(";")
        if frames[-1].strip() in IDLE_LEAVES or not frames[-1].strip():
            idle += n
            continue
        total += n
        leaf = frames[-1]
        selfc[leaf] += n
        m = re.search(r"\(([^:)]+)", leaf)
        filec[(m.group(1).rsplit("/", 1)[-1]) if m else leaf] += n
        seen = set()
        for fr in frames:
            fn = fr.split(" (")[0]
            fl = re.search(r"\(([^:)]+)", fr)
            key = f"{fn} ({fl.group(1).rsplit('/', 1)[-1] if fl else '?'})"
            if key not in seen:
                incl[key] += n
                seen.add(key)
print(f"non-idle samples: {total}  (excluded idle-wait samples: {idle})")
print("\n## self time by file (top 15)")
for k, v in filec.most_common(15):
    print(f"{100*v/total:6.2f}%  {k}")
print("\n## self time by function (top 25)")
for k, v in selfc.most_common(25):
    print(f"{100*v/total:6.2f}%  {k[:150]}")
print("\n## inclusive time (top 40)")
for k, v in incl.most_common(40):
    print(f"{100*v/total:6.2f}%  {k[:150]}")
