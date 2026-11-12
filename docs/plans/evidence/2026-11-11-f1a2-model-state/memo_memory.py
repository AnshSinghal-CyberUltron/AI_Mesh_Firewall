"""F1a-2 evidence: memory of the projector memos (rev 1 §4.2.5; rev 2 keeps the per-org memo in RAM
and the per-row digest memo in a projector-private table, loaded per changed org).

    nice -n 10 /home/claude/work/venv-ctl312/bin/python -I memo_memory.py
"""

import gc
import os
import sys
import tracemalloc

print(f"python={sys.version.split()[0]}")
gc.collect()
tracemalloc.start()
b = tracemalloc.get_traced_memory()[0]
m = {i: (os.urandom(32), os.urandom(120)) for i in range(100_000)}
u = tracemalloc.get_traced_memory()[0] - b
print(f"compact row memo dict[int,(32B digest,120B triple)]: {u/1e6:.1f} MB per 100k rows -> {u/1e5:.0f} B/row -> {u*5/1e6:.0f} MB at 500k rows")
del m
gc.collect()
b = tracemalloc.get_traced_memory()[0]
o = {i: ("org-%06d" % i, 1, os.urandom(32), tuple(f"model-{j}" for j in range(5)), None, None, 5)
     for i in range(100_000)}
u = tracemalloc.get_traced_memory()[0] - b
print(f"rev-2 per-org memo dict[int,(slug,rev,32B hash,5 served keys,allowed,default,row count)]: "
      f"{u/1e6:.1f} MB per 100k orgs -> {u/1e5:.0f} B/org")
