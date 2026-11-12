"""Timeout overshoot: when does `timeout=t` fire, in the calling thread's CPU and in wall time?

    python overshoot_mt.py [REPS]

Part 1, single thread (nothing else in this process): timeouts 10/50/100/400 ms.
Part 2, the same 100 ms timeout while K other threads of this process run pathological regex
scans (concurrent=True, so they burn CPU outside the GIL), K = 1, 3.
The pattern is the translation of a cubic DLP-shaped rule; on 1500 digits it never finishes
inside any of these timeouts, so every call ends in TimeoutError.
"""
import os
import statistics, sys, threading, time
sys.path.insert(0, os.environ.get("XLATE_SNAP") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
import regex
from translate_prototype import translate

REPS = int(sys.argv[1]) if len(sys.argv) > 1 else 7
RX = regex.compile(translate(r"\d+\d+\d+[^\d]"))
BAD = "1" * 1500

def fire(t):
    c0, w0 = time.thread_time(), time.perf_counter()
    try:
        RX.search(BAD, timeout=t, concurrent=True)
        return None
    except TimeoutError:
        return (time.thread_time() - c0) * 1e3, (time.perf_counter() - w0) * 1e3

def row(label, t_ms, res):
    cpu = [r[0] for r in res]; wall = [r[1] for r in res]
    print(f"| {label} | {t_ms} | {statistics.median(cpu):.1f} | {min(cpu):.1f}-{max(cpu):.1f} | "
          f"{statistics.median(cpu) - t_ms:+.1f} | {statistics.median(wall):.1f} | {max(wall):.1f} |", flush=True)

import importlib.metadata as md, hashlib
so = regex._regex.__file__
print(f"regex {md.version('regex')}  .so sha256 {hashlib.sha256(open(so,'rb').read()).hexdigest()[:16]}  python {sys.version.split()[0]}")
print("| Other busy threads | timeout ms | own thread CPU at fire, median ms | range | overshoot (median - timeout) | wall median ms | wall max ms |")
print("|---|---|---|---|---|---|---|")
for t_ms in (10, 50, 100, 400):
    res = [fire(t_ms / 1e3) for _ in range(REPS)]
    assert all(res), "a call completed: the pattern is not slow enough"
    row("0", t_ms, res)

for k in (1, 3):
    stop = threading.Event()
    def burn():
        while not stop.is_set():
            try: RX.search(BAD, timeout=0.05, concurrent=True)
            except TimeoutError: pass
    ts = [threading.Thread(target=burn, daemon=True) for _ in range(k)]
    for t in ts: t.start()
    time.sleep(0.1)
    for t_ms in (100, 400):
        res = [fire(t_ms / 1e3) for _ in range(REPS)]
        row(str(k), t_ms, res)
    stop.set()
    for t in ts: t.join()
