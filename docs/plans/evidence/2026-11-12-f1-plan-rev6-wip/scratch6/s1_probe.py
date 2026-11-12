import sys, time
sys.path.insert(0, ".")
from incremental import RuleScanner, Undecided
import regex, translate_prototype as T
P = r"^(\w+\s?){1,30}[.!?]"
S = "The quarterly report shows that revenue grew in every region and the team now expects further gains next year."
rx = regex.compile(T.translate(P))
t0 = time.thread_time(); m = rx.search(S); print("whole text:", m.span() if m else None, f"{(time.thread_time()-t0)*1e3:.3f} ms")
for k in range(4, len(S) + 1, 4):
    t0 = time.thread_time()
    try:
        list(rx.finditer(S[:k], timeout=0.1)); r = "ok"
    except TimeoutError:
        r = "TIMEOUT"
    print(k, r, f"{(time.thread_time()-t0)*1e3:.1f} ms", repr(S[:k][-12:])) if r != "ok" or k % 20 == 0 else None
    if r != "ok": break
