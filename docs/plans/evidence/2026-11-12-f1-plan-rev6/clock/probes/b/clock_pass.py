"""Plan 5.2.4/5.2.5, faithfully: one off-loop pass of org B's rules over <=128 KiB (budget 100 ms of
thread CPU, shared by its rules, debited with time.thread_time(), each call timeout=remaining).
Org A holds max(1, T-1) scan threads; on this 2-CPU host T = 2, so A holds ONE thread."""
import sys, threading, time, regex
sys.path.insert(0, "."); from translate_prototype import translate
RULES = [regex.compile(translate(p)) for p in (r"\b\w+\s+(?:token|secret)\b", r"\b\w+\s+(?:apikey|passwd)\b",
         r"\b\w+\s+(?:bearer|cookie)\b")]
TEXT = ("alpha beta gamma delta " * 6000)[:128 * 1024]
A = regex.compile(translate(r"\d+\d+\d+[^\d]")); BAD = "1" * 2000
BUDGET = 0.100
def b_pass():
    spent = 0.0
    for rx in RULES:
        remaining = BUDGET - spent
        if remaining <= 0: return "refused", spent
        c0 = time.thread_time()
        try: list(rx.finditer(TEXT, timeout=remaining, concurrent=True))
        except TimeoutError: return "refused", spent + time.thread_time() - c0
        finally: pass
        spent += time.thread_time() - c0
    return "ok", spent
print(f"B's pass alone: {b_pass()}")
for a_threads in (0, 1, 3):
    stop = threading.Event()
    def burn():
        while not stop.is_set():
            try: A.search(BAD, timeout=0.1, concurrent=True)
            except TimeoutError: pass
    ts = [threading.Thread(target=burn) for _ in range(a_threads)]; [t.start() for t in ts]; time.sleep(0.05)
    res = [b_pass() for _ in range(10)]
    stop.set(); [t.join() for t in ts]
    print(f"org A busy on {a_threads} other scan thread(s): B's pass refused {sum(r == 'refused' for r, _ in res)}/10; B's own thread CPU when refused ~{1e3*min((s for r, s in res), default=0):.0f}-{1e3*max(s for r, s in res):.0f} ms of its 100 ms")
