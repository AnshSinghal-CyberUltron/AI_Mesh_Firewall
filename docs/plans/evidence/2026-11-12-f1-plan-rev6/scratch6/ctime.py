import regex, regex.regex as RR, sys, time
_orig = RR._regex.compile
class Shim:
    def __getattr__(self, k): return getattr(_orig_mod, k)
_orig_mod = RR._regex
stats = {}
def timed(*a, **k):
    t0 = time.thread_time(); r = _orig(*a, **k); stats["c"] = time.thread_time() - t0; stats["codelen"] = len(a[2]); return r
class M:
    compile = staticmethod(timed)
    def __getattr__(self, k): return getattr(_orig_mod, k)
RR._regex = M()
for D in (8, 10, 12, 14):
    for name, p in [("(a)+", "(" * D + "a" + ")+" * D), ("(?:a)+", "(?:" * D + "a" + ")+" * D), ("(?>a)+", "(?>" * D + "a" + ")+" * D)]:
        regex.purge(); t0 = time.thread_time(); regex.compile(p); tot = time.thread_time() - t0
        print(f"D={D:2} {name:7} total {tot*1e3:7.1f} ms  C _regex.compile {stats['c']*1e3:7.1f} ms  code words {stats['codelen']}")
