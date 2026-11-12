"""A finditer created on one thread and advanced on another (patched: fail closed if it has a timeout)."""
import threading, regex
rx = regex.compile(r"\d+\d+\d+\D")
for timeout in (None, 5.0):
    it = rx.finditer("1" * 300 + "x" + "1" * 300, timeout=timeout, concurrent=True)   # created here
    out = []
    def consume():
        try: out.append(f"{sum(1 for _ in it)} match(es)")
        except TimeoutError as e: out.append(f"TimeoutError({e})")
    t = threading.Thread(target=consume); t.start(); t.join()
    print(f"timeout={timeout}: created on main thread, advanced on another: {out[0]}")
