"""Reviewer A, revision 5, S1, as tests (budget 100 ms per pass, 4-character deltas).

(a) Policy "refuse" (documented limitation): A's sentence is refused on an early pass although the
    complete text, scanned once, matches in well under 1 ms.
(b) Policy "hold": the same stream is decided, with the same hit list as a whole-text scan; with a
    comma (a stopper that makes the expensive attempt final) it is refused only at the final pass,
    exactly where the whole-text scan is refused too.
"""
import sys, time
sys.path.insert(0, ".")
import regex, translate_prototype as T
from incremental import RuleScanner, Undecided

P = r"^(\w+\s?){1,30}[.!?]"
S = "The quarterly report shows that revenue grew in every region and the team now expects further gains next year."
BUDGET = 0.1
rx = regex.compile(T.translate(P))
t0 = time.thread_time(); whole = [m.span() for m in rx.finditer(S, timeout=BUDGET)]; t_whole = time.thread_time() - t0
assert whole == [(0, len(S))] and t_whole < 0.005, (whole, t_whole)

def stream(text, policy):
    sc = RuleScanner(P)
    cuts = list(range(4, len(text), 4)) + [len(text)]
    for c in cuts:
        final = c == len(text)
        try:
            r = sc.scan(text[:c], BUDGET) if policy == "refuse" else sc.scan_or_hold(text[:c], BUDGET, final=final)
        except TimeoutError:
            return ("refused", c)
    return ("decided", r)

# (a) the documented limitation of policy "refuse"
kind, at = stream(S, "refuse")
assert kind == "refused" and at < 40, (kind, at)
# (b) policy "hold"
kind, hits = stream(S, "hold")
assert kind == "decided" and hits == whole, (kind, hits)
comma = S.replace("grew in", "grew, in")
try:
    list(rx.finditer(comma, timeout=BUDGET)); ref = "decided"
except TimeoutError:
    ref = "refused"
kind, at = stream(comma, "hold")
assert ref == "refused" and kind == "refused" and at == len(comma), (ref, kind, at)
print(f"S1 tests pass: whole text {t_whole*1e3:.2f} ms; 'refuse' refused at {stream(S, 'refuse')[1]} chars; "
      f"'hold' decided {hits}; with a comma both 'hold' and the whole-text scan refuse")
