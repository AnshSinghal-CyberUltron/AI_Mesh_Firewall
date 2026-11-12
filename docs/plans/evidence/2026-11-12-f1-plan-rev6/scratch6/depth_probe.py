import sys, re, regex, inspect
sys.path.insert(0, "..")
import translate_prototype as T

def at_depth(n, f):
    """call f() with n extra Python frames on the stack"""
    if n <= 0:
        return f()
    return at_depth(n - 1, f)

def base_depth():
    return len(inspect.stack(0))

# regex compile: max nesting of (?: that compiles from a shallow stack
def max_ok(make, fn, lo=1, hi=2000):
    while lo < hi:
        mid = (lo + hi + 1) // 2
        try:
            fn(make(mid)); lo = mid
        except RecursionError:
            hi = mid - 1
    return lo
print("base frames", base_depth())
for name, make in [("(?:", lambda n: "(?:" * n + "a" + ")" * n), ("(", lambda n: "(" * n + "a" + ")" * n),
                   ("(?>", lambda n: "(?>" * n + "a" + ")" * n), ("(?=", lambda n: "(?=" * n + "a" + ")" * n)]:
    print(f"regex.compile max nesting {name:5}", max_ok(make, lambda p: regex.compile(p)))
    print(f"re._parser   max nesting {name:5}", max_ok(make, lambda p: re._parser.parse(p, 0)))
    print(f"re.compile   max nesting {name:5}", max_ok(make, lambda p: re.compile(p)))
