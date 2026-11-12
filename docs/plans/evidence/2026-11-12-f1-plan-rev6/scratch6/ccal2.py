import regex, regex.regex as RR, sys, time, re
sys.path.insert(0, ".")
import translate_prototype as T
_c = RR._regex.compile; last = {}
def timed(*a, **k):
    t0 = time.thread_time(); r = _c(*a, **k); last["c"] = time.thread_time() - t0; return r
class M:
    compile = staticmethod(timed)
    def __getattr__(self, k): return getattr(RR._orig, k)
RR._orig = RR._regex; RR._regex = M()
src_shapes = {
 "(?:(a)){1000}": "(?:(a)){1000}",
 "(?:(a)(b)(c)(d)){250}": "(?:(a)(b)(c)(d)){250}",
 "((a)){1000}": "((a)){1000}",
 "(?:(?:(a)){10}){100}*": "(?:(?:(a)){10}){100}",
 "((((a)+)+)+)+ x12": "(" * 12 + "a" + ")+" * 12,
 "((a)|b)+ x10": "(" * 10 + "a|b" + ")+" * 10,
 "(a)(?:\\1){1000}": r"(a)(?:\1){1000}",
 "(a)(?(1)b|c){1000}": r"(a)(?:(?(1)b|c)){1000}",
 "(?=(a)){1000}": r"(?:(?=(a))a){1000}",
 "(?>(a)){1000}": r"(?:(?>(a))){1000}",
 "((?>a+))+ x12": "((?>" * 6 + "a+" + "))+" * 6,
 "(a)*+ nest x12": "(" * 12 + "a" + ")*+" * 12,
 "(a){2} nest x9": "(" * 9 + "a" + "){2}" * 9,
 "(a)+? nest x12": "(" * 12 + "a" + ")+?" * 12,
 "(?i)(a)+ nest x12": "(?i)" + "(" * 12 + "a" + ")+" * 12,
 "(a){1,3} nest x9": "(" * 9 + "a" + "){1,3}" * 9,
 "\\b(a)+ nest x11": "(" * 11 + r"\ba" + ")+" * 11,
}
rows = []
for name, p in src_shapes.items():
    try:
        a = T.check_limits(p)
    except T.Refused as e:
        print(f"{name:26} refused: {e}"); continue
    t = T.translate(p)
    cs = []
    for _ in range(3):
        regex.purge(); t0 = time.thread_time(); regex.compile(t); tot = time.thread_time() - t0; cs.append((last["c"], tot))
    c, tot = min(cs)
    u = T.compile_units(t)
    print(f"{name:26} units {u:7} (est {a.compile_units:7}) len {len(t):6} total {tot*1e3:7.2f} ms C {c*1e3:7.2f} ms  C ns/unit {c*1e9/u:6.1f}")
