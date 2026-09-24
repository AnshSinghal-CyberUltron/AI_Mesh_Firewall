#!/usr/bin/env python3
"""Top leaf frames (self samples) per cost bucket, using the same bucket rules as pyspy_buckets.py.
usage: pyspy_bucket_frames.py DIR [N]"""
import sys
from collections import Counter, defaultdict
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from pyspy_buckets import IDLE_LEAVES, WAIT_LEAVES, PHASES, INFRA  # noqa: E402
N = int(sys.argv[2]) if len(sys.argv) > 2 else 6
leaf = defaultdict(Counter); incl = defaultdict(Counter); tot = Counter(); total = 0
# generic root frames present in (almost) every request stack; excluded from the inclusive view
ROOTS = ("gunicorn", "arbiter.py", "workers.py", "runners.py", "base_events.py", "events.py", "threading.py",
         "wsgiapp.py", "app/base.py", "workers/base.py")
for f in Path(sys.argv[1]).glob("worker-*.txt"):
    for line in f.read_text().splitlines():
        if not line.strip():
            continue
        stack, _, n = line.rpartition(" ")
        n = int(n)
        frames = [x.strip() for x in stack.split(";")]
        if not frames[-1] or frames[-1] in IDLE_LEAVES or frames[-1] in WAIT_LEAVES:
            continue
        total += n
        hit = None
        for name, keys in PHASES:
            if any(k in fr for fr in frames for k in keys):
                hit = name; break
        if hit is None:
            for fr in reversed(frames):
                for name, keys in INFRA:
                    if any(k in fr for k in keys):
                        hit = name; break
                if hit:
                    break
        hit = hit or "other"
        tot[hit] += n
        leaf[hit][frames[-1]] += n
        seen = set()
        for fr in frames[:-1]:
            key = fr.split(" (")[0] + " (" + (fr.split(" (")[1].split(":")[0] if " (" in fr else "?") + ")"
            if any(r in key for r in ROOTS) or key in seen:
                continue
            seen.add(key)
            incl[hit][key] += n
for b, v in tot.most_common():
    print(f"\n## {100*v/total:.2f}%  {b}")
    print("   top self (leaf) frames:")
    for fr, c in leaf[b].most_common(N):
        print(f"     {100*c/total:5.2f}%  {fr[:140]}")
    print("   top inclusive frames (share of ALL non-idle samples whose stack passes through it):")
    for fr, c in incl[b].most_common(N):
        print(f"     {100*c/total:5.2f}%  {fr[:140]}")
