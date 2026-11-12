"""How much does the thread CPU of one fixed pass vary on this shared host? (no other thread in-process)

    python cpu_variance.py [N]
B's pass (3 translated rules over 128 KiB of unpunctuated text, no timeout), N times, one thread.
"""
import statistics, sys, time
sys.path.insert(0, "/tmp/claude-0/-home-claude/2a18a307-83e0-5467-9172-0939d20bde00/scratchpad/xlate5/clock/run-1")
import regex
from translate_prototype import translate
RULES = [regex.compile(translate(p)) for p in (r"\b\w+\s+(?:token|secret)\b", r"\b\w+\s+(?:apikey|passwd)\b", r"\b\w+\s+(?:bearer|cookie)\b")]
TEXT = ("alpha beta gamma delta " * 6000)[:128 * 1024]
N = int(sys.argv[1]) if len(sys.argv) > 1 else 60
xs = []
for _ in range(N):
    c0 = time.thread_time()
    for rx in RULES:
        for _m in rx.finditer(TEXT, concurrent=True): pass
    xs.append((time.thread_time() - c0) * 1e3)
q = statistics.quantiles(xs, n=20)
print(f"{N} passes: min {min(xs):.1f}, p50 {statistics.median(xs):.1f}, p95 {q[18]:.1f}, max {max(xs):.1f} ms; max/min {max(xs)/min(xs):.2f}")
