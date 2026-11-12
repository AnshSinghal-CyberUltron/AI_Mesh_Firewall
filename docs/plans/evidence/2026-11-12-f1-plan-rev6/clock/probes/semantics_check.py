"""Timeout semantics of a patched build: main thread, worker thread, migration, NaN, inf, KeyboardInterrupt path untouched."""
import regex, threading, time, signal, os, sys
rx = regex.compile(r"\d+\d+\d+[^\d]")
txt = "1" * 5000
def timed(where):
    c0 = time.thread_time()
    try:
        rx.search(txt, timeout=0.05, concurrent=True); return f"{where}: no timeout?!"
    except TimeoutError as e:
        return f"{where}: TimeoutError at {(time.thread_time()-c0)*1e3:.1f} ms own CPU"
print(timed("main thread"))
out = []; t = threading.Thread(target=lambda: out.append(timed("worker thread"))); t.start(); t.join(); print(out[0])
it = rx.finditer(txt, timeout=0.05, concurrent=True)
res = []
def adv():
    try: next(it); res.append("migrated iterator: advanced?!")
    except TimeoutError as e: res.append(f"migrated iterator: TimeoutError ({e})")
    except StopIteration: res.append("migrated iterator: StopIteration")
t = threading.Thread(target=adv); t.start(); t.join(); print(res[0])
try: regex.compile("a").search("a", timeout=float("nan")); print("nan: accepted")
except ValueError: print("nan: ValueError")
t0 = time.perf_counter(); print("inf:", regex.compile("a+b").search("a" * 1000 + "b", timeout=float("inf")) is not None, f"{(time.perf_counter()-t0)*1e3:.1f} ms")
# SIGALRM on the main thread interrupts a long untimed match on the main thread (PyErr_CheckSignals path kept)
signal.signal(signal.SIGALRM, lambda *a: (_ for _ in ()).throw(KeyboardInterrupt()))
signal.setitimer(signal.ITIMER_REAL, 0.2)
t0 = time.perf_counter()
try: rx.search("1" * 20000, concurrent=True); print("signal: match finished?!")
except KeyboardInterrupt: print(f"signal on main thread: interrupted after {(time.perf_counter()-t0)*1e3:.0f} ms wall")
