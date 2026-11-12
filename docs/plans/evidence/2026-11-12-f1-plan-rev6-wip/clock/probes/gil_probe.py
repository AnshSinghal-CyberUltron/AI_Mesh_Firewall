"""Does the matcher release the GIL, and does a scan thread waiting for the GIL accrue CPU?

    python gil_probe.py

_regex.c releases the GIL for the whole match when the state is multithreaded
(concurrent=True, or concurrent=None on a str/bytes), but safe_check_cancel() takes the GIL back
every 256 matcher iterations to call PyErr_CheckSignals() and the timeout check, then releases it.
So a scan thread needs the GIL briefly but often. Here thread H is a pure-Python busy loop that
holds the GIL (a stand-in for a busy event loop); thread M runs one customer rule over the text.
We record M's outcome under timeout=100 ms (text: KIB KiB, argv[1]), M's own thread CPU and M's wall time.
"""
import statistics, sys, threading, time
sys.path.insert(0, "/tmp/claude-0/-home-claude/2a18a307-83e0-5467-9172-0939d20bde00/scratchpad/xlate5/clock/run-1")
import regex
from translate_prototype import translate

RX = regex.compile(translate(r"\b\w+\s+(?:token|secret)\b"))
KIB = int(sys.argv[1]) if len(sys.argv) > 1 else 32
RUNS = int(sys.argv[2]) if len(sys.argv) > 2 else 3
TEXT = ("alpha beta gamma delta " * (KIB * 1024 // 23 + 1))[:KIB * 1024]
assert len(TEXT) == KIB * 1024

def m_scan(concurrent, timeout):
    c0, w0, p0 = time.thread_time(), time.perf_counter(), time.process_time()
    try:
        for _ in RX.finditer(TEXT, timeout=timeout, concurrent=concurrent):
            pass
        r = "ok"
    except TimeoutError:
        r = "timeout"
    return r, (time.thread_time() - c0) * 1e3, (time.perf_counter() - w0) * 1e3, (time.process_time() - p0) * 1e3

def h_rate(seconds=0.3):
    n = 0; end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        n += 1
    return n / seconds

import importlib.metadata as md, hashlib
print(f"text {KIB} KiB, {RUNS} runs per row; regex {md.version('regex')} .so sha256 {hashlib.sha256(open(regex._regex.__file__,'rb').read()).hexdigest()[:16]}"
      f"  switch interval {sys.getswitchinterval()*1e3:.1f} ms")
print("| H (pure-Python busy thread) | switch interval | concurrent= | M outcome | M own CPU median ms | M wall median ms | process CPU during M, median ms (what stock clock() counts) |")
print("|---|---|---|---|---|---|---|")
for sw in (0.005, 0.001):
    sys.setswitchinterval(sw)
    for h_busy in (False, True):
        for conc in (True, None, False):
            stop = threading.Event(); h_counts = []
            def h():
                n = 0; t0 = time.perf_counter()
                while not stop.is_set():
                    n += 1
                h_counts.append(n / (time.perf_counter() - t0))
            th = threading.Thread(target=h, daemon=True) if h_busy else None
            if th: th.start(); time.sleep(0.05)
            res = []
            for _ in range(RUNS):
                out = [None]
                tm = threading.Thread(target=lambda: out.__setitem__(0, m_scan(conc, 0.100)))
                tm.start(); tm.join()
                res.append(out[0])
            stop.set()
            if th: th.join()
            outs = [x[0] for x in res]
            print(f"| {'busy' if h_busy else 'none'} | {sw*1e3:.0f} ms | {conc} | "
                  f"{outs.count('ok')} ok, {outs.count('timeout')} timeout | {statistics.median(x[1] for x in res):.1f} | "
                  f"{statistics.median(x[2] for x in res):.0f} | {statistics.median(x[3] for x in res):.0f} |", flush=True)
sys.setswitchinterval(0.005)
