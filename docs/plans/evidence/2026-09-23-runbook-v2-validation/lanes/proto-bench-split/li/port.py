import sys
U, OUT = sys.argv[1], sys.argv[2]
src = open(U).read()
old = r"""    if s.guard_topology not in ("in_process", "owner"):\n"""
assert src.count(old) == 2, src.count(old)
src = src.replace(old, r"""    if s.guard_topology not in ("in_process", "owner", "remote"):\n""")
doc_old = "unit-1 rvproto tree. Every knob defaults to the unchanged behaviour.\n"
assert src.count(doc_old) == 1
src = src.replace(doc_old, doc_old + "proto-bench-split port (tools/apply_li_split.py): identical edits; the only change is the config.py anchor,\n"
                  "which in rvproto-frozen-1 also lists the split topology: guard_topology not in (\"in_process\", \"owner\", \"remote\").\n")
open(OUT, "w").write(src)
print("ported")
