import re, sys, time, regex
sys.path.insert(0, ".")
from translate_prototype import translate, _CASED
part, parts = int(sys.argv[1]), int(sys.argv[2])
ALL = "".join(chr(c) for c in range(sys.maxunicode + 1) if not 0xD800 <= c <= 0xDFFF)
t0 = time.perf_counter(); bad = 0
for i, cp in enumerate(_CASED):
    if i % parts != part or 0xD800 <= cp <= 0xDFFF: continue
    p = "(?i)" + re.escape(chr(cp))
    ra = {m.start() for m in re.finditer(p, ALL)}
    rb = {m.start() for m in regex.compile(translate(p)).finditer(ALL)}
    if ra != rb:
        bad += 1
        if bad <= 10: print("MISMATCH", hex(cp), sorted(hex(ord(ALL[j])) for j in ra ^ rb)[:6], flush=True)
print(f"part {part}: {len(_CASED)//parts} literals, mismatches={bad} in {time.perf_counter()-t0:.0f}s", flush=True)
