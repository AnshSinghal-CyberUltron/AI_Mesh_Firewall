"""How many Python frames re.compile needs for a pattern nested `d` groups deep (fresh cache).

Run with each interpreter: `python re_frames_per_depth.py`.
"""
import inspect
import re
import sys

CASES = {
    "plain": lambda d: "(" * d + "a" + ")" * d,
    "noncap": lambda d: "(?:" * d + "a" + ")" * d,
    "lookbehind": lambda d: "(?<=a" * d + "b" + ")" * d,
    "alternation": lambda d: "(a|" * d + "b" + ")" * d,
    "repeat": lambda d: "(?:a" * d + "b" + ")*" * d,
    "atomic": lambda d: "(?>a" * d + "b" + ")" * d,
    "scoped flags": lambda d: "(?i:a" * d + "b" + ")" * d,
}


def frames_needed(pattern: str) -> int:
    base = len(inspect.stack(0))
    low, high = base, 3000
    while low < high - 1:
        mid = (low + high) // 2
        sys.setrecursionlimit(mid)
        re.purge()
        try:
            re.compile(pattern)
            high = mid
        except RecursionError:
            low = mid
        finally:
            sys.setrecursionlimit(1000)
    return high - base


print("python", sys.version.split()[0], "default recursion limit", sys.getrecursionlimit())
for name, make in CASES.items():
    print(f"{name:13s}", {d: frames_needed(make(d)) for d in (1, 16, 32, 33, 64, 400)})
