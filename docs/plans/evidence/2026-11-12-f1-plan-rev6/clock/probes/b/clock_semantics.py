"""regex 2023.8.8 _regex.c:2211/18337 measures the timeout with C clock(): PROCESS CPU on Linux.
Tenant B's ordinary pass (alone ~well under its 100 ms budget) runs while tenant A's pathological
passes burn CPU on other scan threads of the SAME worker process (concurrent=True releases the GIL)."""
import sys, threading, time, regex
sys.path.insert(0, "."); from translate_prototype import translate
B = regex.compile(translate(r"\b\w+\s+(?:token|secret)\b"))
TEXT = ("alpha beta gamma delta " * 9000)            # unpunctuated: the plan's worst stopper-free case
A = regex.compile(translate(r"\d+\d+\d+[^\d]")); BAD = "1" * 2000
def b_pass(timeout):
    c0, w0 = time.thread_time(), time.perf_counter()
    try: list(B.finditer(TEXT, timeout=timeout, concurrent=True)); r = "ok"
    except TimeoutError: r = "TimeoutError"
    return r, (time.thread_time() - c0) * 1e3, (time.perf_counter() - w0) * 1e3
r, cpu, wall = b_pass(None); print(f"B alone, no timeout: {cpu:.0f} ms own thread CPU ({len(TEXT)//1024} KiB)")
BUDGET = 0.100
for burners in (0, 1, 2, 3):
    stop = threading.Event()
    def burn():
        while not stop.is_set():
            try: A.search(BAD, timeout=0.05, concurrent=True)   # tenant A: refused at its own 50 ms, again and again
            except TimeoutError: pass
    ts = [threading.Thread(target=burn) for _ in range(burners)]
    for t in ts: t.start()
    time.sleep(0.05)
    res = [b_pass(BUDGET) for _ in range(5)]
    stop.set(); [t.join() for t in ts]
    outs = [x[0] for x in res]; cpu = sorted(x[1] for x in res)[2]; wall = sorted(x[2] for x in res)[2]
    print(f"{burners} tenant-A scan threads busy: B with timeout=100 ms -> {outs.count('TimeoutError')}/5 TimeoutError; B's own thread CPU at the end {cpu:.0f} ms (median), wall {wall:.0f} ms")
