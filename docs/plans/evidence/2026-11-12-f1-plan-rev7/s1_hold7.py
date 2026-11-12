"""Revision 7 (Plan Reviewer A, revision 6, S1): hold-and-retry with a per-STREAM bound.

    python s1_hold7.py <budget_ms>

Streams of 4-character deltas, scanned with `scan_or_hold`, under two policies:
  rev 6: hold-once, the cap measured as n - q, no per-stream bound (revision 6 as committed);
  rev 7: hold-once, stopper-gated after the first timeout, the cap measured on the HELD text
         (characters since the rule's last decided pass), at most 4 timed-out non-final passes per
         rule per stream.
Cases: Reviewer A's (`probes/hold_attack.py`, `hold_bullets.py`: H1, H1b, H2, H3, bullet lists) plus
6-64 KiB prose, quoted prose and code, and a whole-text-rescan rule. For each: the whole text scanned
once non-streaming under the same budget (the requirement-4 reference), and per policy the outcome,
the timed-out passes, the stream's thread CPU and the most text held.
Part 2: `scan_pass` with a plan of the expensive rule plus 9 ordinary rules (TEN_B without the IBAN
shape): how often an ordinary rule was left undecided (expected: never, they are scanned first).
"""
import random
import sys
import time
import warnings

warnings.simplefilter("ignore")
sys.path.insert(0, ".")
from incremental import RuleScanner, Undecided, scan_pass  # noqa: E402

BUDGET = float(sys.argv[1]) / 1e3 if len(sys.argv) > 1 else 0.1
R = random.Random(7)
WORDS = "revenue grew in every region and the team now expects further gains next year report shows that sales".split()


def sentence(k: int) -> str:
    return " ".join(R.choice(WORDS) for _ in range(k)).capitalize() + "."


def prose(nchars: int, sep: str = " ") -> str:
    out: list[str] = []
    total = 0
    while total < nchars:
        out.append(sentence(R.randint(8, 14)))
        total += len(out[-1]) + len(sep)
    return sep.join(out)


def bullets(nchars: int) -> str:
    r = random.Random(3)
    w = "the gateway scans every request and blocks secrets before they reach the model so teams ship faster".split()
    items: list[str] = []
    total = 0
    while total < nchars:
        items.append("- " + " ".join(r.choice(w) for _ in range(r.randint(7, 13))).capitalize() + ".")
        total += len(items[-1]) + 1
    return "\n".join(items) + "\n"


def code(nchars: int) -> str:
    r = random.Random(5)
    out: list[str] = []
    total = 0
    while total < nchars:
        name = "".join(r.choice("abcdefghij_") for _ in range(r.randint(3, 9)))
        out.append(f"{name} = compute({name}, 0x{r.getrandbits(24):06x})  # sum of totals.\n")
        total += len(out[-1])
    return "".join(out)


P = r"(\w+\s?){1,30}[.!?]"
P_WHOLE = r"(\w+\s?){1,30}[.!?]\s*(?=(?s:.)*)"
CASES = [
    ("A's sentence (s1_test)", P, "In the third quarter the company reported strong growth across all of its major regions and markets."),
    ("H1 plain sentences, 6 KiB", P, prose(6000)),
    ("H1b plain sentences, 3 KiB", P, prose(3000)),
    ("H2 quoted sentences, 6 KiB", P, prose(6000, sep='" "')),
    ("H3 whole-text-rescan rule, 6 KiB", P_WHOLE, prose(6000)),
    ("bullet list, 2 KiB", P, bullets(2000)),
    ("bullet list, 16 KiB", P, bullets(16000)),
    ("plain sentences, 64 KiB", P, prose(65536)),
    ("code, 16 KiB", P, code(16384)),
]


def whole(p: str, text: str) -> str:
    sc = RuleScanner(p)
    try:
        list(sc.rx.finditer(text, timeout=BUDGET * max(1, len(text) / 131072), concurrent=True))
        return "decided"
    except TimeoutError:
        return "REFUSED"


def stream(p: str, text: str, rev: int):
    sc = (RuleScanner(p, stopper_retry="once") if rev == 6 else
          RuleScanner(p, stopper_retry="once", max_timeouts=4, hold_measure="held", keep_final=False, gate=True))
    released = 0
    held = 0
    t0 = time.thread_time()
    cuts = list(range(4, len(text), 4)) + [len(text)]
    for c in cuts:
        last = c == len(text)
        try:
            r = sc.scan_or_hold(text[:c], BUDGET * max(1, c / 131072), final=last)
        except TimeoutError as e:
            return f"REFUSED at {c} ({str(e)[:24]})", sc.timeouts, (time.thread_time() - t0), held
        if isinstance(r, Undecided):
            held = max(held, c - released)
        else:
            released = c
    return "decided", sc.timeouts, (time.thread_time() - t0), held


print(f"budget {BUDGET * 1e3:g} ms per pass (x len/128 KiB); 4-character deltas; thread CPU")
print(f"{'case':34} {'chars':>6} {'whole text':>10} | {'rev 6: outcome':30} {'t/o':>4} {'CPU s':>6} {'held':>5} | "
      f"{'rev 7: outcome':30} {'t/o':>4} {'CPU s':>6} {'held':>5}")
for name, p, text in CASES:
    w = whole(p, text)
    r6 = stream(p, text, 6)
    r7 = stream(p, text, 7)
    print(f"{name:34} {len(text):6} {w:>10} | {r6[0]:30} {r6[1]:4} {r6[2]:6.2f} {r6[3]:5} | "
          f"{r7[0]:30} {r7[1]:4} {r7[2]:6.2f} {r7[3]:5}", flush=True)

print("\npart 2: scan_pass, the expensive rule plus 9 ordinary rules, one shared budget per pass")
TEN = [r"\bpassword\b", r"(?i)\bconfidential\b", r"\w+@\w+\.\w+", r"\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}",
       r"api[_-]?key\s*[:=]\s*\w{16,}", r"(?i)bitcoin", r"AKIA[0-9A-Z]{16}", r"\bEMP-\d{6}\b",
       r"(?i)\bproject\s+falcon\b"]
for name, text in (("H2 quoted sentences, 6 KiB", prose(6000, sep='" "')), ("bullet list, 2 KiB", bullets(2000))):
    rules = [RuleScanner(q, stopper_retry="once", max_timeouts=4, hold_measure="held", keep_final=False, gate=True)
             for q in [P] + TEN]
    undecided_ordinary = 0
    passes = 0
    outcome = "decided"
    t0 = time.thread_time()
    cuts = list(range(4, len(text), 4)) + [len(text)]
    for c in cuts:
        try:
            res = scan_pass(rules, text[:c], BUDGET, final=c == len(text))
        except TimeoutError as e:
            outcome = f"REFUSED at {c} ({str(e)[:30]})"
            break
        passes += 1
        undecided_ordinary += sum(isinstance(res[i], Undecided) for i in range(1, len(rules)))
    print(f"  {name}: {outcome}; {passes} passes; ordinary rules left undecided {undecided_ordinary} times; "
          f"expensive rule timed out {rules[0].timeouts} times; CPU {time.thread_time() - t0:.2f} s")
