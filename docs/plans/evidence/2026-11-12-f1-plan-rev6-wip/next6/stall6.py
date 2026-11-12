"""Event-loop stall while a worker thread translates and compiles a plan at the revision-6 limits,
rule by rule with a GIL yield between rules (plan 5.2.3), against a busy pure-Python thread.

What bounds one rule's stall: the Python part of translation and compile is interrupted every
switch interval (5 ms); the C call `_regex.compile` holds the GIL to the end, and the compile-units
cap bounds it (largest measured: 3.8 ms). Wall time on a shared host: each scenario runs in the same
process, alternating, and the busy-thread baseline shows the host's own noise.

    python stall6.py [rounds]
"""
import asyncio, statistics, sys, threading, time
sys.path.insert(0, ".")
import regex
import translate_prototype as T

def plan_rules():
    def fill(unit, tail=""):
        best = None
        for k in range(1, 1000):
            p = unit * k + tail
            try:
                T.check_limits(p); best = p
            except T.Refused:
                break
        return best
    rules = [fill(r"\b.") + f"(?:x{i})" for i in range(8)]                       # translated-size cap
    deep = next("(" * d + "a" + ")+" * d for d in range(16, 0, -1) if _ok("(" * d + "a" + ")+" * d))
    rules += [deep + f"(?:z{i})" for i in range(8)]                                # compile-units cap
    rules += [r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b", r"(?i)\bconfidential\b"] * 8      # ordinary rules
    res = T.check_plan(rules)
    return [p for p in rules if isinstance(res[p], T.Analysis)]
def _ok(p):
    try:
        T.check_limits(p); return True
    except T.Refused:
        return False

def compile_plan(rules, yield_between):
    regex.purge(); T._ci_set.cache_clear(); T._ci_verdicts.cache_clear()
    for p in rules:
        regex.compile(T.translate(p, cpu_budget_s=None))
        if yield_between:
            time.sleep(0)

def busy(seconds):
    end = time.perf_counter() + seconds; x = 0
    while time.perf_counter() < end:
        for i in range(2000):
            x += i * i

async def measure(work):
    lags = []; done = threading.Event()
    th = threading.Thread(target=lambda: (work(), done.set())); th.start()
    while not done.is_set():
        t = time.perf_counter(); await asyncio.sleep(0.001); lags.append(time.perf_counter() - t - 0.001)
    th.join()
    return lags

if __name__ == "__main__":
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    rules = plan_rules()
    t0 = time.thread_time(); compile_plan(rules, True); cpu = time.thread_time() - t0
    print(f"plan: {len(rules)} rules accepted by check_plan; translate + compile {cpu*1e3:.0f} ms CPU; switch interval {sys.getswitchinterval()*1e3:.0f} ms")
    scen = {"compile, yield between rules": lambda: compile_plan(rules, True),
            "compile, no yield": lambda: compile_plan(rules, False),
            "busy pure-Python thread (same duration)": lambda: busy(cpu)}
    acc = {k: [] for k in scen}
    for _ in range(rounds):
        for k, w in scen.items():
            acc[k].append(asyncio.run(measure(w)))
    print("| Worker thread | Loop ticks | Lag p50 | p99 | max (median of rounds) | max (worst round) |")
    print("|---|---|---|---|---|---|")
    for k, runs in acc.items():
        allv = sorted(v for r in runs for v in r)
        mx = [max(r) for r in runs]
        print(f"| {k} | {len(allv)} | {allv[len(allv)//2]*1e3:.2f} ms | {allv[int(len(allv)*0.99)]*1e3:.2f} ms | "
              f"{statistics.median(mx)*1e3:.1f} ms | {max(mx)*1e3:.1f} ms |")
