import re, sys, time, unicodedata, regex
sys.path.insert(0, ".")
from translate_prototype import translate
t0=time.perf_counter()
ALL="".join(chr(c) for c in range(sys.maxunicode+1) if not 0xD800<=c<=0xDFFF)
bad=0
for flags in ("", "(?i)"):
  for pat in [r"\d",r"\D",r"\w",r"\W",r"\s",r"\S",r"[\d]",r"[^\d]",r"[\D]",r"[\w_x]",r"[^\W]",r"[\s\d]",r"[^\s]",r"[\W\d]"]:
    p=flags+pat
    a=re.compile(p); b=regex.compile(translate(p))
    ra=set(m.start() for m in a.finditer(ALL)); rb=set(m.start() for m in b.finditer(ALL))
    if ra!=rb:
        bad+=1; print("MISMATCH", repr(p), len(ra^rb), [hex(ord(ALL[i])) for i in list(ra^rb)[:5]])
print(f"classes: {bad} mismatches in {time.perf_counter()-t0:.1f}s")
# \b / \B next to every code point
t0=time.perf_counter(); bad=0
for flags in ("", "(?i)"):
  for pat in (r"\b", r"\B", r"\bx", r"x\b", r"\Bx", r"x\B"):
    a=re.compile(flags+pat); b=regex.compile(translate(flags+pat))
    for c in ALL[::1]:
        for s in (c, "x"+c, c+"x", "x"+c+"x", " "+c):
            if [m.span() for m in a.finditer(s)]!=[m.span() for m in b.finditer(s)]:
                bad+=1
                if bad<6: print("BMISMATCH", repr(flags+pat), hex(ord(c)), repr(s))
print(f"boundaries: {bad} mismatches in {time.perf_counter()-t0:.1f}s")
