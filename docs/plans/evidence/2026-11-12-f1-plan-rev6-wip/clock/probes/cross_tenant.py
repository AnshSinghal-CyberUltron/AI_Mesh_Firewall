"""Cross-tenant budget test that plan section 5.2.4/5.2.5 needs (Reviewer B, blocker 1).

    python cross_tenant.py [PASSES]

A worker has T scan threads. Org A may hold max(1, T-1) of them; org B gets the remaining one.
Org A's threads run pathological passes back to back; each is refused at its own per-pass budget.
Org B's pass is the plan's off-loop pass: k customer rules over <= 128 KiB, one per-pass budget of
100 ms of thread CPU shared by the rules, debited with time.thread_time(), each call
timeout=remaining (refused without calling regex when remaining <= 0).
B's text and rule count are calibrated so that B's pass, alone, costs about 60% of its budget.
PASS criterion (the plan's requirement 4/6): B's pass is never refused while A burns T-1 threads.
"""
import math, statistics, sys, threading, time
sys.path.insert(0, "/tmp/claude-0/-home-claude/2a18a307-83e0-5467-9172-0939d20bde00/scratchpad/xlate5/clock/run-1")
import regex
from translate_prototype import translate

PASSES = int(sys.argv[1]) if len(sys.argv) > 1 else 20
BUDGET = 0.100                       # guard_wait_budget_s() x max(1, len/128 KiB) at <= 128 KiB
B_RULES = [regex.compile(translate(p)) for p in (
    r"\b\w+\s+(?:token|secret)\b", r"\b\w+\s+(?:apikey|passwd)\b", r"\b\w+\s+(?:bearer|cookie)\b",
    r"\b\w+\s+(?:session|cred)\b", r"\b\w+\s+(?:private|key)\b")]
FULL = ("alpha beta gamma delta " * 6000)[:128 * 1024]     # unpunctuated: the stopper-free worst case
A_RX = regex.compile(translate(r"\d+\d+\d+[^\d]")); A_BAD = "1" * 2000

def b_pass(rules, text):
    spent = 0.0
    for rx in rules:
        remaining = BUDGET - spent
        if remaining <= 0:
            return "refused", spent
        c0 = time.thread_time()
        try:
            for _ in rx.finditer(text, timeout=remaining, concurrent=True):
                pass
        except TimeoutError:
            return "refused", spent + time.thread_time() - c0
        spent += time.thread_time() - c0
    return "ok", spent

def alone(rules, text, n=7):
    return statistics.median(b_pass(rules, text)[1] for _ in range(n))

# B's pass: k rules over `length` characters. Either given (argv: PASSES K LENGTH, so that two wheels
# run identical inputs) or calibrated here to about TARGET of the budget (argv: PASSES 0 0 [TARGET]).
K_ARG = int(sys.argv[2]) if len(sys.argv) > 2 else 0
LEN_ARG = int(sys.argv[3]) if len(sys.argv) > 3 else 0
TARGET = float(sys.argv[4]) if len(sys.argv) > 4 else 0.60
if K_ARG and LEN_ARG:
    k, length = K_ARG, LEN_ARG
else:
    per_rule = alone(B_RULES[:1], FULL, 9)
    k = min(len(B_RULES), max(1, math.ceil(TARGET * BUDGET / per_rule)))
    length = len(FULL)
    for _ in range(3):        # iterate: cost is linear in the text length
        cost = alone(B_RULES[:k], FULL[:length], 9)
        length = min(len(FULL), int(length * TARGET * BUDGET / cost))
RULES, TEXT = B_RULES[:k], FULL[:length]
base = alone(RULES, TEXT, 9)

import importlib.metadata as md, hashlib
print(f"regex {md.version('regex')} .so sha256 {hashlib.sha256(open(regex._regex.__file__,'rb').read()).hexdigest()[:16]}")
print(f"B's pass: {k} rules over {length} chars ({length / 1024:.0f} KiB); alone it costs {base*1e3:.1f} ms of thread CPU = "
      f"{100*base/BUDGET:.0f}% of its {BUDGET*1e3:.0f} ms budget ({PASSES} passes per row)")
print("| T (scan threads) | org A threads busy | B refused | B own CPU per pass, median / max ms | "
      "B wall per pass, median ms | A's passes: own CPU at refusal, median ms (n) |")
print("|---|---|---|---|---|---|")
for T, a_threads in ((1, 0), (2, 1), (4, 3), (8, 7)):
    stop = threading.Event(); a_cpu = []; lock = threading.Lock()
    def burn():
        while not stop.is_set():
            c0 = time.thread_time()
            try:
                A_RX.search(A_BAD, timeout=BUDGET, concurrent=True)
            except TimeoutError:
                with lock: a_cpu.append(time.thread_time() - c0)
    ts = [threading.Thread(target=burn, daemon=True) for _ in range(a_threads)]
    for t in ts: t.start()
    time.sleep(0.2)
    res = []
    for _ in range(PASSES):
        w0 = time.perf_counter(); r, spent = b_pass(RULES, TEXT); res.append((r, spent, time.perf_counter() - w0))
    stop.set()
    for t in ts: t.join()
    cpu = [s for _, s, _ in res]; wall = [w for _, _, w in res]
    label = "baseline (B alone)" if a_threads == 0 else str(T)
    a_txt = f"{statistics.median(a_cpu)*1e3:.1f} ({len(a_cpu)})" if a_cpu else "-"
    print(f"| {label} | {a_threads} | {sum(r == 'refused' for r, _, _ in res)}/{PASSES} | "
          f"{statistics.median(cpu)*1e3:.1f} / {max(cpu)*1e3:.1f} | {statistics.median(wall)*1e3:.0f} | {a_txt} |", flush=True)
