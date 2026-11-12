"""Revision 7 (Plan Reviewer B, revision 6, should-fix 5): customer-regex CPU per request, and what
scanning at release points instead of per upstream delta saves.

    python cost7.py

Reviewer B's `cost/cost_per_request.py` (rule sets: perf5's TEN_B, and 64 rules of the same shapes;
punctuated prose), with the incremental scanner called every G characters of new output instead of
every 4-character delta: G = 4 (revision 6, per delta), 64, 256 and at the end only. Property 4 makes
the released bytes independent of how the text is grouped, so G changes cost and release latency,
never what is released. Thread CPU, median of 5. Also the input scan of a 4 KiB prompt.
"""
import random
import statistics
import sys
import time
import warnings

warnings.simplefilter("ignore")
sys.path.insert(0, ".")
import regex  # noqa: E402

from incremental import RuleScanner  # noqa: E402
from translate_prototype import translate  # noqa: E402

TEN_B = [r"\bpassword\b", r"(?i)\bconfidential\b", r"\w+@\w+\.\w+", r"\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}",
         r"api[_-]?key\s*[:=]\s*\w{16,}", r"(?i)bitcoin", r"AKIA[0-9A-Z]{16}", r"\bEMP-\d{6}\b",
         r"(?i)\bproject\s+falcon\b", r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b"]


def sixty_four() -> list[str]:
    out = list(TEN_B)
    words = ["falcon", "orion", "atlas", "zephyr", "nimbus", "quartz", "cobalt", "ember", "harbor", "summit"]
    i = 0
    while len(out) < 64:
        w = words[i % 10] + str(i // 10)
        out += [rf"(?i)\bproject\s+{w}\b", rf"\bEMP{i}-\d{{6}}\b", rf"\b{w}[_-]?id\s*[:=]\s*\w{{8,}}"]
        i += 1
    return out[:64]


R = random.Random(3)
WORDS = ("the of and to in is that for it with as was on be by this are from or at an have not but "
         "which they their one all been has were more will would there what about when can also into "
         "system report quarter revenue customer account balance transfer invoice payment").split()


def prose(n: int) -> str:
    out, i, total = [], 0, 0
    while total < n:
        i += 1
        out.append(R.choice(WORDS) + (". " if i % 9 == 0 else ", " if i % 7 == 0 else " "))
        total += len(out[-1])
    return "".join(out)[:n]


def stream_cost(rules: list[str], resp: str, every: int | None, reps: int = 5) -> float:
    vals = []
    for _ in range(reps):
        scs = [RuleScanner(p, keep_final=False) for p in rules]
        ends = list(range(every, len(resp), every)) if every else []
        ends.append(len(resp))
        c0 = time.thread_time()
        for end in ends:
            t = resp[:end]
            for sc in scs:
                sc.scan(t, timeout=0.005)
        vals.append(time.thread_time() - c0)
    return statistics.median(vals) * 1e3


def main() -> None:
    print("| Rules | Response | Scan every | Scans | Stream scan CPU | Per KiB |")
    print("|---|---|---|---|---|---|")
    for name, rules in (("10 (TEN_B)", TEN_B), ("64 (same shapes)", sixty_four())):
        prompt = prose(4096)
        compiled = [regex.compile(translate(p)) for p in rules]
        ins = []
        for _ in range(5):
            c0 = time.thread_time()
            for c in compiled:
                for _m in c.finditer(prompt, timeout=0.1, concurrent=True):
                    pass
            ins.append(time.thread_time() - c0)
        print(f"| {name} | input scan, 4 KiB prompt | once | 1 | {statistics.median(ins) * 1e3:.2f} ms | |")
        for size in (2048, 8192):
            resp = prose(size)
            for every in (4, 64, 256, None):
                ms = stream_cost(rules, resp, every)
                n = (size // every) if every else 1
                print(f"| {name} | {size // 1024} KiB | {every or 'end only'} chars | {n} | {ms:.2f} ms | "
                      f"{ms / (size / 1024):.2f} ms |", flush=True)


if __name__ == "__main__":
    main()
