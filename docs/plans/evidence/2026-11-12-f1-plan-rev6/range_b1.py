"""Reviewer A, revision 5, blocker B1: translation cost through the range classifier.

    python range_b1.py TRANSLATOR_DIR [ROUNDS]

TRANSLATOR_DIR is `.` (revision 6) or `rev5_e2c3e2cf` (the revision-5 translator as committed at
e2c3e2cf, whose `_range_class` built every code point of a range up to 65,535 wide in Python).

1. Exactness of revision 6's `_range_class` (a bisect over the build-time `WORD_FLIPS` table): against
   a brute-force classification by `re`'s own `\\w` over EVERY code point of the range (prefix sums
   over all 1,114,112 code points, surrogates included). Ranges: every flip f as (f-1, f), (f, f),
   (f, f+1), (f-1, f+1); every run between two flips exactly; and 20,000 random ranges of random
   widths (1 to 0x10FFFF). Run only for revision 6 (revision 5 has no table).
2. A's patterns: 166 distinct wide ranges `[X-\\uffef]+` (996 characters) and the `\\b[X-\\uffef]` form,
   the parse-only verdict, then translation thread CPU with no CPU cap (to show the cost) and with the
   gateway's cap (revision 6: 50 ms, refused over it).
3. A's loop harness: an asyncio loop ticks every 1 ms; 6 such rules are translated and compiled
   one rule per executor step (a yield between rules, as plan 5.2.3 compiles them). Reported: the
   worker's thread CPU, loop ticks per second, and loop lateness p50 / p99 / max (wall).
Thread CPU unless marked wall.
"""
import asyncio, bisect, concurrent.futures as cf, json, os, random, re, statistics, sys, threading, time
D = sys.argv[1]
ROUNDS = int(sys.argv[2]) if len(sys.argv) > 2 else 3
sys.path.insert(0, os.path.abspath(D))
import warnings; warnings.simplefilter("ignore")
import regex
import translate_prototype as T

REV = "rev6" if hasattr(T, "MAX_DEPTH") else "rev5@e2c3e2cf"

def clear_caches():
    """Revision 5 memoised _range_class (lru_cache): every measurement starts cold, as a new plan does."""
    for v in vars(T).values():
        if callable(getattr(v, "cache_clear", None)):
            v.cache_clear()
out = {"translator": REV, "dir": D}

# A's patterns
starts = [0x100 + 37 * i for i in range(166)]
P_PLUS = "".join(f"[{chr(s)}-\uffef]+" for s in starts)
P_B = ""
for s in starts:
    piece = f"\\b[{chr(s)}-\uffef]"
    if len(P_B) + len(piece) > 1000:
        break
    P_B += piece
out["patterns"] = {"plus": {"chars": len(P_PLUS)}, "boundary": {"chars": len(P_B)}}

# 1. exactness of the bisect classifier (revision 6 only)
if REV == "rev6":
    t0 = time.time()
    W = re.compile(r"\w")
    N = sys.maxunicode + 1
    pref = [0] * (N + 1)
    acc = 0
    for cp in range(N):
        if W.match(chr(cp)):
            acc += 1
        pref[cp + 1] = acc
    def brute(a, b):
        k = pref[b + 1] - pref[a]
        return "w" if k == b - a + 1 else ("n" if k == 0 else None)
    flips = T._FLIPS
    cases = set()
    for f in flips:
        for a, b in ((f - 1, f), (f, f), (f, f + 1), (f - 1, f + 1)):
            if 0 <= a <= b < N:
                cases.add((a, b))
    bounds = [0] + list(flips) + [N]
    for i in range(len(bounds) - 1):
        a, b = bounds[i], bounds[i + 1] - 1
        if a <= b:
            cases.add((a, b))
    R = random.Random(11)
    for _ in range(20000):
        w = int(2 ** R.uniform(0, 20.1))
        a = R.randrange(0, N)
        cases.add((a, min(N - 1, a + w)))
    mism = [(a, b, T._range_class(a, b), brute(a, b)) for a, b in sorted(cases) if T._range_class(a, b) != brute(a, b)]
    out["range_class_exactness"] = {"ranges": len(cases), "flips": len(flips), "mismatches": len(mism),
                                    "examples": mism[:5], "seconds_wall": round(time.time() - t0, 1)}

# 2. A's patterns: verdict and translation cost
def verdict(p):
    try:
        T.check_limits(p)
        return "accepted"
    except T.Refused as e:
        return f"refused: {e}"

def tcost(p, **kw):
    best = None
    for _ in range(ROUNDS):
        clear_caches()
        t0 = time.thread_time()
        try:
            t = T.translate(p, **kw); res = len(t)
        except T.Refused as e:
            res = f"refused: {e}"
        dt = (time.thread_time() - t0) * 1e3
        best = dt if best is None or dt < best else best
    return round(best, 2), res

for name, p in (("plus", P_PLUS), ("boundary", P_B)):
    o = out["patterns"][name]
    o["check_limits"] = verdict(p)
    clear_caches(); t0 = time.thread_time(); verdict(p); o["check_limits_ms"] = round((time.thread_time() - t0) * 1e3, 2)
    if REV == "rev6":
        o["translate_uncapped_ms"], o["translated_chars"] = tcost(p, cpu_budget_s=None)
        o["translate_capped_ms"], o["capped_result"] = tcost(p)
        o["compile_rule"] = "ok" if not isinstance(T.compile_rule(p), str) else "?"
    else:
        o["translate_uncapped_ms"], o["translated_chars"] = tcost(p)

# 3. A's loop harness: 6 rules, one per executor step
async def harness(rules):
    loop = asyncio.get_running_loop()
    lates, ticks, stop = [], [0], [False]
    async def ticker():
        while not stop[0]:
            t = time.perf_counter(); await asyncio.sleep(0.001)
            lates.append((time.perf_counter() - t - 0.001) * 1e3); ticks[0] += 1
    cpu = [0.0]
    def one(p):
        t0 = time.thread_time()
        try:
            if REV == "rev6":
                T.compile_rule(p)
            else:
                regex.compile(T.translate(p))
        except Exception:
            pass
        cpu[0] += time.thread_time() - t0
    tk = asyncio.create_task(ticker())
    await asyncio.sleep(0.2); lates.clear(); ticks[0] = 0
    w0 = time.perf_counter()
    with cf.ThreadPoolExecutor(1) as ex:
        for p in rules:
            await loop.run_in_executor(ex, one, p)
    wall = time.perf_counter() - w0
    busy = list(lates); n = ticks[0]
    lates.clear(); ticks[0] = 0
    await asyncio.sleep(max(0.5, min(wall, 2.0)))
    idle_rate = ticks[0] / max(0.5, min(wall, 2.0))
    stop[0] = True; await tk
    q = sorted(busy)
    pct = lambda x: q[min(len(q) - 1, int(x * len(q)))] if q else 0.0
    return {"worker_thread_cpu_ms": round(cpu[0] * 1e3, 1), "wall_ms": round(wall * 1e3, 1),
            "ticks_per_s_during": round(n / wall, 0) if wall else None, "ticks_per_s_idle_after": round(idle_rate, 0),
            "lateness_ms_p50": round(pct(0.5), 2), "p99": round(pct(0.99), 2), "max": round(max(q) if q else 0.0, 2)}

rules = []
for k in range(6):  # 6 distinct rules (A: one plan, 6 such rules), same shape, shifted starts
    rules.append("".join(f"[{chr(s + k)}-\uffef]+" for s in starts))
clear_caches()
out["loop_harness"] = asyncio.run(harness(rules))
out["load"] = open("/proc/loadavg").read().split()[:3]
print(json.dumps(out, ensure_ascii=True))
