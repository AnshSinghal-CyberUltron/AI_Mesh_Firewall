"""How long after its timeout does a translated pathological pattern actually stop? (concurrent=True)"""
import statistics, sys, time, regex
sys.path.insert(0, ".")
from translate_prototype import translate
CASES = [(r"(\w+\s?){1,30}$", "ab " * 200 + "!"), (r"(\w+\s?){1,30}$", "a" * 30 + "!"),
         (r"(?:a|a){1,30}b", "a" * 40), (r"(\d+)+x", "1" * 40)]
for pat, text in CASES:
    rx = regex.compile(translate(pat))
    for t_ms in (1, 2, 5, 10, 20, 50, 100):
        walls = []
        for _ in range(9):
            t0 = time.perf_counter()
            try:
                for _m in rx.finditer(text, timeout=t_ms / 1e3, concurrent=True):
                    pass
                walls.append(None)
            except TimeoutError:
                walls.append((time.perf_counter() - t0) * 1e3)
        w = [x for x in walls if x is not None]
        if not w:
            print(f"{pat!r:24} len={len(text):4} timeout {t_ms:4} ms: completed (no timeout)"); continue
        print(f"{pat!r:24} len={len(text):4} timeout {t_ms:4} ms: fired median {statistics.median(w):7.1f} max {max(w):7.1f} ms  ratio max {max(w)/t_ms:4.2f}")
