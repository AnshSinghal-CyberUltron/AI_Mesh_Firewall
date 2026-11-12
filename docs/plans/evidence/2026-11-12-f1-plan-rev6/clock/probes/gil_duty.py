"""GIL convoy as a function of the event loop's Python duty cycle (plan 5.2.4, revision 6).

    python gil_duty.py [KIB] [RUNS]

`_regex.c` releases the GIL for a match (`concurrent=True`) but `safe_check_cancel()` takes it back
every 256 matcher iterations. So an off-loop scan's WALL time depends on how much Python the event
loop runs. `gil_probe.py` measured only a loop that is 100% busy (x12 at a 5 ms switch interval).
Here thread H stands in for the loop: it runs pure Python for d% of every 10 ms period and sleeps
for the rest (d = 0, 25, 50, 75, 90, 100). Thread M runs one translated customer rule over KIB KiB,
`timeout=None, concurrent=True` (no refusal; we measure only time). Reported per duty cycle: M's
own thread CPU and wall (medians), and wall / CPU. Run on the patched wheel: the ratio is the
same on stock, which only differs in which CPU its timeout counts.
"""
import os
import statistics, sys, threading, time
sys.path.insert(0, os.environ.get("XLATE_SNAP") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
import regex
from translate_prototype import translate

RX = regex.compile(translate(r"\b\w+\s+(?:token|secret)\b"))
KIB = int(sys.argv[1]) if len(sys.argv) > 1 else 384
RUNS = int(sys.argv[2]) if len(sys.argv) > 2 else 5
TEXT = ("alpha beta gamma delta " * (KIB * 1024 // 23 + 1))[:KIB * 1024]
PERIOD = 0.010

def m_scan():
    c0, w0 = time.thread_time(), time.perf_counter()
    for _ in RX.finditer(TEXT, timeout=None, concurrent=True):
        pass
    return (time.thread_time() - c0) * 1e3, (time.perf_counter() - w0) * 1e3

import importlib.metadata as md, hashlib
print(f"# {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}  load average: {', '.join(open('/proc/loadavg').read().split()[:3])}")
print(f"text {KIB} KiB, {RUNS} runs per row; regex {md.version('regex')} .so sha256 "
      f"{hashlib.sha256(open(regex._regex.__file__,'rb').read()).hexdigest()[:16]}; H period {PERIOD*1e3:.0f} ms")
print("| switch interval | H Python duty cycle | H measured duty | M own CPU median ms | M wall median ms | wall / CPU |")
print("|---|---|---|---|---|---|")
for sw in (0.005, 0.001):
    sys.setswitchinterval(sw)
    for duty in (0, 25, 50, 75, 90, 100):
        stop = threading.Event(); hstat = []
        def h():
            busy_s = PERIOD * duty / 100
            c0, w0 = time.thread_time(), time.perf_counter()
            while not stop.is_set():
                if busy_s > 0:
                    end = time.perf_counter() + busy_s
                    while time.perf_counter() < end:
                        pass
                if duty < 100:
                    time.sleep(PERIOD - busy_s)
            hstat.append((time.thread_time() - c0) / max(1e-9, time.perf_counter() - w0))
        th = threading.Thread(target=h, daemon=True) if duty > 0 else None
        if th:
            th.start(); time.sleep(0.05)
        res = []
        for _ in range(RUNS):
            out = [None]
            tm = threading.Thread(target=lambda: out.__setitem__(0, m_scan()))
            tm.start(); tm.join(); res.append(out[0])
        stop.set()
        if th:
            th.join()
        cpu = statistics.median(r[0] for r in res); wall = statistics.median(r[1] for r in res)
        hd = f"{hstat[0]*100:.0f}%" if hstat else "-"
        print(f"| {sw*1e3:.0f} ms | {duty}% | {hd} | {cpu:.1f} | {wall:.0f} | {wall/cpu:.1f} |", flush=True)
sys.setswitchinterval(0.005)
print("# other top CPU at end:")
for line in os.popen("ps -eo pcpu,args --sort=-pcpu | sed -n 2,5p").read().splitlines():
    print("#", line[:120])
