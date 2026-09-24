import sys
p = sys.argv[1]
src = open(p).read()
old = r'''        s = swap(s, "import os\nimport struct\nimport sys\n", "import os\nimport signal\nimport struct\nimport sys\nimport time\n", p)'''
new = r'''        s = swap(s, "import os\nimport socket\nimport sys\nimport time\n", "import os\nimport signal\nimport socket\nimport sys\nimport time\n", p)'''
assert src.count(old) == 1, src.count(old)
src = src.replace(old, new)
doc_old = "which in rvproto-frozen-1 also lists the split topology: guard_topology not in (\"in_process\", \"owner\", \"remote\").\n"
assert src.count(doc_old) == 1
src = src.replace(doc_old, doc_old + "and the owner.py import anchor (rvproto-frozen-1's owner imports socket and already imports time).\n")
src = src.replace("the only change is the config.py anchor,", "the only changes are the config.py anchor,")
open(p, "w").write(src)
print("port2 ok")
