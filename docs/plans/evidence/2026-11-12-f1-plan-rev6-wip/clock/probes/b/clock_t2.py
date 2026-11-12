"""T = 2 scan threads (this host has 2 CPUs): org A may hold max(1, T-1) = 1 thread (plan 5.2.5),
org B gets the other. B's ordinary pass needs ~60% of its 100 ms per-pass budget alone."""
import sys, threading, time, regex
sys.path.insert(0, "."); from translate_prototype import translate
B = regex.compile(translate(r"\b\w+\s+(?:token|secret)\b"))
A = regex.compile(translate(r"\d+\d+\d+[^\d]")); BAD = "1" * 2000
def own(text):
    c0 = time.thread_time(); list(B.finditer(text, concurrent=True)); return time.thread_time() - c0
n = 100_000
while own("alpha beta gamma delta " * (n // 23)) < 0.060: n += 20_000
TEXT = "alpha beta gamma delta " * (n // 23)
print(f"B alone: {own(TEXT)*1e3:.0f} ms own CPU on {len(TEXT)//1024} KiB (per-pass budget 100 ms)")
for a_threads in (0, 1):
    stop = threading.Event()
    def burn():
        while not stop.is_set():
            try: A.search(BAD, timeout=0.1, concurrent=True)
            except TimeoutError: pass
    ts = [threading.Thread(target=burn) for _ in range(a_threads)]; [t.start() for t in ts]; time.sleep(0.05)
    res = []
    for _ in range(10):
        c0 = time.thread_time()
        try: list(B.finditer(TEXT, timeout=0.100, concurrent=True)); res.append(("ok", time.thread_time() - c0))
        except TimeoutError: res.append(("refused", time.thread_time() - c0))
    stop.set(); [t.join() for t in ts]
    print(f"org A on {a_threads} scan thread(s): B refused {sum(r == 'refused' for r, _ in res)}/10, B own CPU at refusal ~{1e3*sorted(c for r, c in res)[5]:.0f} ms")
