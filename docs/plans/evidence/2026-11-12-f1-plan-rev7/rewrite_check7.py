"""Revision 7 (Plan Reviewer B, revision 6, blocker 2; issue #125 addendum): the REWRITE template
check must not run a tenant regex unbounded anywhere, and control's per-org gate must come first.

    python rewrite_check7.py

Issue #125's pattern `^(([^#]+))+#` passes both `_has_redos_shape` twins and the limits. A 1 ms
ticker thread stands in for the other request threads of the same process. Three paths:
  (a) control at HEAD: `_row_template` runs `re.search(pattern, template)` in the request thread
      (`firewall_v2_publish.py:586-593`, called at :458 BEFORE the `rewrite_served_by_v2` gate);
  (b) control in F1a-1 (revision 7): the gate first; then only the syntactic template rules, no
      tenant regex at all (the gateway's check is the authority);
  (c) the gateway in F1a-1: the translated pattern on the patched `regex`, on a scan thread with the
      per-pass budget (100 ms of that thread's CPU), `concurrent=True`; a timeout or a match makes
      the rule use the platform template for its category (never a document refusal).
Thread CPU of the check and the ticker's largest gap (wall), per template length.
"""
import re
import sys
import threading
import time
import warnings

warnings.simplefilter("ignore")
sys.path.insert(0, ".")
import translate_prototype as T  # noqa: E402

PATTERN = r"^(([^#]+))+#"
FORBIDDEN = ("://", "![", "](", "<", ">", "@", "www.")


def syntactic_safe(own: str, keywords=()) -> bool:
    """`_row_template`'s rules without `_matches_own_pattern` (control, revision 7)."""
    neutral = own.replace("{category}", "X").replace("{rule_id}", "X")
    return (bool(own) and len(own) <= 128 and "{" not in neutral and "}" not in neutral
            and not any(t in neutral for t in FORBIDDEN)
            and re.search(r"[A-Za-z0-9]\.[A-Za-z0-9]|\d\.\d|[A-Za-z0-9+/=_-]{16,}", neutral) is None
            and not any(w in own.casefold() for w in keywords))


def with_ticker(fn):
    gaps: list[float] = []
    stop = threading.Event()

    def ticker() -> None:
        last = time.perf_counter()
        while not stop.is_set():
            time.sleep(0.001)
            now = time.perf_counter()
            gaps.append(now - last)
            last = now

    tk = threading.Thread(target=ticker, daemon=True)
    tk.start()
    time.sleep(0.03)
    out = fn()
    time.sleep(0.01)
    stop.set()
    tk.join()
    return out, max(gaps) * 1e3


def template(n: int) -> str:
    base = ("abcd " * 30)[:n].rstrip()
    return base + "z" * (n - len(base))


a = T.check_limits(PATTERN)
print(f"pattern {PATTERN!r}: accepted by the limits (size {a.translated_size}, units {a.compile_units})")
print(f"{'template':>8} | {'(a) HEAD control: CPU / ticker gap':38} | {'(b) rev-7 control':26} | "
      f"{'(c) rev-7 gateway: outcome, CPU / ticker gap':48}")
rx = T.compile_rule(PATTERN)
for n in (20, 22, 24, 26, 28, 128):
    t = template(n)
    assert syntactic_safe(t), t
    if n <= 24:
        def head():
            c0 = time.thread_time()
            re.search(PATTERN, t)  # what control runs today, in the request thread
            return (time.thread_time() - c0) * 1e3
        cpu_a, gap_a = with_ticker(head)
        col_a = f"{cpu_a:8.0f} ms / {gap_a:6.0f} ms"
    else:
        col_a = "not run (doubles per character)"

    def ctl():
        c0 = time.thread_time()
        for _ in range(1000):
            syntactic_safe(t)
        return (time.thread_time() - c0) * 1e6 / 1000
    us_b, _gap = with_ticker(ctl)
    res: dict = {}

    def gw():
        def scan() -> None:
            c0 = time.thread_time()
            try:
                res["m"] = rx.search(t, timeout=0.1, concurrent=True) is not None
                res["out"] = "matched" if res["m"] else "no match"
            except TimeoutError:
                res["out"] = "timeout: platform template"
            except MemoryError:
                res["out"] = "memory: platform template"
            res["cpu"] = (time.thread_time() - c0) * 1e3
        th = threading.Thread(target=scan)
        th.start()
        th.join()
    _o, gap_c = with_ticker(gw)
    print(f"{n:8} | {col_a:38} | {us_b:8.1f} us, no regex     | {res['out']:26} {res['cpu']:6.1f} ms / {gap_c:5.1f} ms")
