"""Match speed: section 4 of xlate5/perf5.py (same texts, patterns and calls), plus a check-heavy case.

    python match_speed.py OUT.json

Section 4 calls finditer(text, timeout=5, concurrent=True), so every timeout check reads the clock.
The check-heavy case is a cubic pattern whose run is almost all matcher iterations (one check per
256), timed with timeout=None (no clock read) and timeout=1000 (a clock read per check).
Every time is the best of 5 runs, in the calling thread's CPU time.
"""
import os
import json, random, sys, time
sys.path.insert(0, os.environ.get("XLATE_SNAP") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
import re, regex
from translate_prototype import translate

def cpu(fn, reps=5):
    best = 1e9
    for _ in range(reps):
        t0 = time.thread_time(); fn(); best = min(best, time.thread_time() - t0)
    return best * 1e3

# ---- verbatim from perf5.py section 4 -------------------------------------------------------
R2 = random.Random(3)
words = "the model answer explains how quarterly revenue grew while costs fell in 2025 and 2026 across regions".split()
prose = " ".join(R2.choice(words) for _ in range(9000))[:32768]
ident = lambda: "".join(R2.choice("abcdefghijklmnopqrstuvwxyz_") for _ in range(R2.randint(3, 12)))
code_parts = []
while sum(map(len, code_parts)) < 32768:
    k = R2.random()
    code_parts.append(f"{ident()} = {ident()}({ident()}, 0x{R2.getrandbits(32):08x})\n" if k < 0.5 else
                      f"    {ident()}.{ident()}[\"{''.join(R2.choice('ABCDEFabcdef0123456789+/') for _ in range(24))}\"] += 1\n")
code = "".join(code_parts)[:32768]
CORPUS = [r"\bpassword\b", r"(?i)\bconfidential\b", r"\w+@\w+\.\w+", r"\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}",
          r"api[_-]?key\s*[:=]\s*\w{16,}", r"(?i)bitcoin", r"AKIA[0-9A-Z]{16}", r"\bEMP-\d{6}\b",
          r"(?i)\bproject\s+falcon\b", r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b", r"\b\w+\b", r"\b\w+\s+(?:token|secret)\b",
          r"(?i)\binternal use only\b", r"\b\d{3}-\d{2}-\d{4}\b", r"(?i)\bproject\s+\w+\b"]
out = {"patterns": {}, "check_heavy": {}}
for p in CORPUS:
    a, b = re.compile(p), regex.compile(translate(p))
    row = {}
    for name, text in (("prose", prose), ("code", code)):
        assert [m.span() for m in a.finditer(text)] == [m.span() for m in b.finditer(text)], p
        row[name] = cpu(lambda: list(b.finditer(text, timeout=5, concurrent=True)))
    out["patterns"][p] = row
# ---- check-heavy: almost all matcher iterations ----------------------------------------------
CUBIC = regex.compile(translate(r"\d+\d+\d+[^\d]")); DIGITS = "1" * 260
for label, t in (("timeout=None", None), ("timeout=1000", 1000.0)):
    out["check_heavy"][label] = cpu(lambda: CUBIC.search(DIGITS, timeout=t, concurrent=True), reps=7)
import importlib.metadata as md, hashlib
out["so"] = hashlib.sha256(open(regex._regex.__file__, "rb").read()).hexdigest()[:16]
json.dump(out, open(sys.argv[1], "w"), indent=1)
print(sys.argv[1], out["so"], {k: round(v, 2) for k, v in out["check_heavy"].items()})
