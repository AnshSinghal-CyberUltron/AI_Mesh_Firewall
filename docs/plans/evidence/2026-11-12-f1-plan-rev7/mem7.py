"""Revision 7 (Plan Reviewer B, revision 6, blocker 1; issue #158): match-time memory.

    python mem7.py case <name> <cap_bytes> <kib> <threads>   # one case in THIS process (fresh RSS)
    python mem7.py all <cap_bytes>                          # every case, one child process each
    python mem7.py corpus                                   # ordinary rules: peak match memory, no cap
    python mem7.py speed                                    # match speed of the corpus (for A/B wheels)

Needs the `regex` build with the match-memory cap (`regex-2023.8.8-match-memory-cap.patch` on top of
the clock patch): `regex._regex.set_match_memory_limit(bytes)` (0: no cap) and
`regex._regex.match_memory_peak(reset)` -> (cap, largest growth any finished match state charged).
The cap counts the match-time buffers that grow with the text: the backtracking, structure and
pruning stacks, each group's capture list and the repeat guard lists. Exceeding it raises
MemoryError, which the scanner turns into a refused pass.

Every case uses the revision-7 translator (unreferenced groups are emitted non-capturing) and the
per-pass budget of section 5.2.4: max(1, len / 128 KiB) x 100 ms of thread CPU.
"""
import os
import random
import resource
import subprocess
import sys
import threading
import time
import warnings

warnings.simplefilter("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import regex  # noqa: E402
import regex._regex as R  # noqa: E402

import translate_prototype as T  # noqa: E402

if not hasattr(R, "set_match_memory_limit"):  # the clock-only wheel: `speed` runs, the cap cases do not
    R.set_match_memory_limit = lambda n: None
    R.match_memory_peak = lambda reset=False: (0, 0)

D16c = "(" * 16 + r"\w" + ")" * 16 + "*"
CASES = {
    # Reviewer B's mem_probe2.py / mem_probe3.py shapes (pattern, text unit)
    "cap1": (r"(\w)*", "a"),
    "cap16": (D16c, "a"),
    "capword": (r"(\w+\s)*", "abcd "),
    "email": (r"((\w+)@(\w+)\.(\w+))", "ab@cd.ef "),
    "alt2": (r"(?:ab|cd)*(?!)", "ab"),
    "alt2_ok": (r"(?:ab|cd)*", "ab"),
    "look": (r"(?:a(?=a))*(?!)", "a"),
    "lazy": (r"(?:ab|cd)*?(?!)", "ab"),
    "nest_alt": ("(?:" * 15 + r"(?:ab|cd)" + ")*" * 15 + "(?!)", "ab"),
    "atomic_rep": (r"(?:(?>ab|a)b?)*(?!)", "ab"),
    "back": (r"(\w)(?:\w*\1)*(?!)", "ab"),
    # new: a referenced capture inside a repeat keeps capturing (the cap is what bounds it)
    "refcap": (r"(?:(\w)\1?)*(?!)", "ab"),
    "refcond": (r"(?:(a)|b)*(?(1)c|d)", "ab"),
}


def vmrss_mib() -> float:
    for line in open("/proc/self/status"):
        if line.startswith("VmRSS"):
            return int(line.split()[1]) / 1024
    return 0.0


def maxrss_mib() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


def one_case(name: str, cap: int, kib: int, threads: int) -> None:
    pat, unit = CASES[name]
    R.set_match_memory_limit(cap)
    rx = T.compile_rule(pat)
    text = (unit * (kib * 1024 // len(unit) + 1))[: kib * 1024]
    budget = 0.1 * max(1, kib / 128)
    R.match_memory_peak(True)
    m0, r0 = maxrss_mib(), vmrss_mib()
    res = []

    def scan() -> None:
        c0 = time.thread_time()
        out, n = "ok", 0
        try:
            for _m in rx.finditer(text, timeout=budget, concurrent=True):
                n += 1
        except TimeoutError:
            out = "timeout"
        except MemoryError:
            out = "MemoryError"
        res.append(f"{out} {(time.thread_time() - c0) * 1e3:.0f} ms")

    ts = [threading.Thread(target=scan) for _ in range(threads)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    _cap, peak = R.match_memory_peak()
    after = vmrss_mib() - r0
    import ctypes
    ctypes.CDLL("libc.so.6").malloc_trim(0)  # what the scanner does after a pass refused for memory
    print(f"{name:10} {pat[:36]!r:40} cap {cap / 2**20:5.0f} MiB  {kib} KiB x{threads}: {', '.join(res):48} "
          f"charged peak {peak / 2**20:7.1f} MiB  RSS peak +{maxrss_mib() - m0:6.0f} MiB  "
          f"VmRSS after +{after:5.0f} MiB, after malloc_trim +{vmrss_mib() - r0:4.0f} MiB", flush=True)


def run_all(cap: int) -> None:
    me = os.path.abspath(__file__)
    for kib, threads in ((512, 1), (512, 3)):
        for name in CASES:
            if threads == 3 and name not in ("cap1", "cap16", "look", "alt2_ok", "refcap"):
                continue
            subprocess.run([sys.executable, me, "case", name, str(cap), str(kib), str(threads)], check=False)


_TEXTS: dict = {}


def texts():
    """perf5.py's three texts (punctuated prose, unpunctuated prose, code), 512 KiB each."""
    if _TEXTS:
        return _TEXTS
    r2 = random.Random(3)
    words = "the model answer explains how quarterly revenue grew while costs fell in 2025 and 2026 across regions".split()
    out, i, total = [], 0, 0
    while total < 524288:
        w = r2.choice(words)
        i += 1
        out.append(w + (". " if i % 9 == 0 else ", " if i % 7 == 0 else "\n" if i % 61 == 0 else " "))
        total += len(out[-1])
    prose = "".join(out)[:524288]
    unp = " ".join(r2.choice(words) for _ in range(120000))[:524288]
    ident = lambda: "".join(r2.choice("abcdefghijklmnopqrstuvwxyz_") for _ in range(r2.randint(3, 12)))  # noqa: E731
    parts: list[str] = []
    while sum(map(len, parts)) < 32768:
        parts.append(f"{ident()} = {ident()}({ident()}, 0x{r2.getrandbits(32):08x})\n" if r2.random() < 0.5 else
                     f"    {ident()}.{ident()}[\"{''.join(r2.choice('ABCDEFabcdef0123456789+/') for _ in range(24))}\"] += 1\n")
    code = ("".join(parts)[:32768] * 17)[:524288]
    _TEXTS.update({"punctuated prose": prose, "unpunctuated prose": unp, "code": code})
    return _TEXTS


CORPUS = [r"\bpassword\b", r"(?i)\bconfidential\b", r"\w+@\w+\.\w+", r"\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}",
          r"api[_-]?key\s*[:=]\s*\w{16,}", r"(?i)bitcoin", r"AKIA[0-9A-Z]{16}", r"\bEMP-\d{6}\b",
          r"(?i)\bproject\s+falcon\b", r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b", r"\b\w+\b", r"\b\w+\s+(?:token|secret)\b",
          r"(?i)\binternal use only\b", r"\b\d{3}-\d{2}-\d{4}\b", r"(?i)\bproject\s+\w+\b",
          r"^(\w+\s?){1,30}[.!?]", r"(?:\w+\s)*!", r"(?i)\b(?:invoice|receipt|payment|refund|order|account|"
          r"balance|transfer|wire|iban)\b", r"(\w+)\s+\1", r"[^\n]{200,}"]


def corpus() -> None:
    R.set_match_memory_limit(0)
    print("ordinary rules over 512 KiB, no cap: largest charged growth per pass (MiB) / thread CPU (ms)")
    worst = 0
    for p in CORPUS:
        rx = T.compile_rule(p)
        cells = []
        for name, text in texts().items():
            R.match_memory_peak(True)
            c0 = time.thread_time()
            try:
                n = sum(1 for _ in rx.finditer(text, timeout=0.4, concurrent=True))
                out = ""
            except TimeoutError:
                n, out = 0, " timeout"
            _c, peak = R.match_memory_peak()
            worst = max(worst, peak)
            cells.append(f"{name}: {peak / 2**20:.3f} MiB / {(time.thread_time() - c0) * 1e3:.0f} ms{out}")
        print(f"  {p[:44]!r:48} " + "; ".join(cells), flush=True)
    print(f"largest charged growth over the corpus: {worst / 2**20:.3f} MiB")


def speed() -> None:
    import statistics
    ts = texts()
    rows = []
    for p in CORPUS:
        rx = T.compile_rule(p)
        best = []
        for text in ts.values():
            v = []
            for _ in range(5):
                c0 = time.thread_time()
                try:
                    sum(1 for _ in rx.finditer(text[:131072], timeout=1.0, concurrent=True))
                except TimeoutError:
                    pass
                v.append(time.thread_time() - c0)
            best.append(min(v))
        rows.append(sum(best))
    print("corpus match CPU over 128 KiB x 3 texts, best of 5, sum per pattern (ms):",
          " ".join(f"{x * 1e3:.2f}" for x in rows))
    print(f"total {sum(rows) * 1e3:.1f} ms; median per pattern {statistics.median(rows) * 1e3:.2f} ms; "
          f"_regex {R.__file__}")


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "case":
        one_case(sys.argv[2], int(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5]))
    elif cmd == "all":
        run_all(int(sys.argv[2]))
    elif cmd == "corpus":
        corpus()
    elif cmd == "speed":
        speed()
