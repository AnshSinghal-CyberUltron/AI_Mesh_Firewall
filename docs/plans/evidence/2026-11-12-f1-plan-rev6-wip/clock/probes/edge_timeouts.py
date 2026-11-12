"""decode_timeout edge cases (single thread): NaN, inf, huge, 0, negative, small.
A cubic pattern on 1500 digits runs for seconds, so 'completed fast' cannot hide a missing timeout."""
import sys, time, regex
rx = regex.compile(r"\d+\d+\d+\D")
s = "1" * 1500
SHORT = "1" * 60                          # used where the stock meaning is 'no timeout', so the probe ends
MID = "1" * 300                           # ~0.1-0.2 s: used for huge timeouts (patched: clamped, so the call completes)
for t in (float("nan"), float("inf"), 1e300, 1e13, 9.2e12, 0.0, -1.0, 0.05):
    nolimit = (t != t) or t < 0
    c0 = time.thread_time()
    try:
        huge = t == float('inf') or t > 1e6
        rx.search(SHORT if nolimit else MID if huge else s, timeout=t); out = "completed" + (" (short text)" if nolimit else " (300 digits)" if huge else "")
    except TimeoutError as e: out = f"TimeoutError({e})"
    except Exception as e: out = f"{type(e).__name__}({e})"
    print(f"timeout={t!r:>18}: {out:40} after {(time.thread_time()-c0)*1e3:8.1f} ms thread CPU", flush=True)
# does a NaN / negative timeout really mean 'no limit'? run the long text under a 3 s watchdog in a child
import subprocess
for t in ("float('nan')", "-1.0"):
    code = f"import regex; regex.compile(r'\\d+\\d+\\d+\\D').search('1'*1500, timeout={t})"
    try:
        subprocess.run([sys.executable, "-I", "-c", code], timeout=3); print(f"timeout={t}: finished within 3 s")
    except subprocess.TimeoutExpired: print(f"timeout={t}: still running after 3 s (no limit applied)")
