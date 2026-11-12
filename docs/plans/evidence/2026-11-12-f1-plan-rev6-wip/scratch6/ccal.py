"""Calibrate: C compile time (thread CPU, GIL held) of `_regex.compile` per shape, vs chars x copies."""
import regex, regex.regex as RR, sys, time, statistics, resource
sys.path.insert(0, ".")
import translate_prototype as T
_c = RR._regex.compile
last = {}
def timed(*a, **k):
    t0 = time.thread_time(); r = _c(*a, **k); last["c"] = time.thread_time() - t0; return r
class M:
    compile = staticmethod(timed)
    def __getattr__(self, k): return getattr(RR._orig, k)
RR._orig = RR._regex; RR._regex = M()
S = T._S
def units_chars(t):
    return T.compile_units(t) if hasattr(T, "compile_units") else None
shapes = {
 "x{1000}": r"\x61{1000}",
 "[a-z]{1000}": r"[\x61-\x7a]{1000}",
 "\\s{1000}": S + "{1000}",
 "(?:ab){500}": r"(?:\x61\x62){500}",
 "x{1000}x40": r"\x61{1000}" * 40,
 "nest (?:a)+ x12": "(?:" * 12 + r"\x61" + ")+" * 12,
 "nest ((a))+ x12": "(" * 12 + r"\x61" + ")+" * 12,
 "nest (?>(?>(a))+) x8": "(?>(?>(" * 8 + r"\x61" + "))+)" * 8,
 "(?:(?=a)a){500}": r"(?:(?=\x61)\x61){500}",
 "two-sided \\b . x330": (T._BOUNDARY + "(?-s:.)") * 330,
 "\\s x500": S * 500,
 "(?:\\w{3}\\s){250}": "(?:" + T._W + "{3}" + S + "){250}",
 "ci 64 sets": T.translate("(?i)" + "".join(f"[{chr(0x100+2*i)}{chr(0x101+2*i)}]" for i in range(64))),
 "(?:a|b|c|d|e|f|g|h){1000}": r"(?:\x61|\x62|\x63|\x64|\x65|\x66|\x67|\x68){1000}",
 "(?i:[a-z]){1000}": r"(?i:[\x61-\x7a]){1000}",
 "nest (?:ab|cd)+ x10": "(?:" * 10 + r"\x61\x62|\x63" + ")+" * 10,
}
for name, t in shapes.items():
    regex.purge(); t0 = time.thread_time(); regex.compile(t); tot = time.thread_time() - t0
    cs = []
    for _ in range(3):
        regex.purge(); regex.compile(t); cs.append(last["c"])
    c = min(cs)
    u = units_chars(t)
    print(f"{name:28} len {len(t):6} units {u!s:>8} total {tot*1e3:7.2f} ms  C {c*1e3:7.2f} ms  "
          + (f"C ns/unit {c*1e9/u:6.1f}  total ns/char {tot*1e9/len(t):6.0f}" if u else ""))
print("maxrss MiB", resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024)
