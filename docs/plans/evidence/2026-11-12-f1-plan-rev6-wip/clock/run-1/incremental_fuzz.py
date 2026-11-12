"""Differential test: the incremental scanner's hits equal a whole-text rescan after every delta."""
import random, re, sys, time, warnings, regex
sys.path.insert(0, ".")
warnings.simplefilter("ignore")
import fuzz_gen as G
from translate_prototype import INF, Refused, analyze
from incremental import RuleScanner

seed = int(sys.argv[1]) if len(sys.argv) > 1 else 1
n = int(sys.argv[2]) if len(sys.argv) > 2 else 3000
R = random.Random(seed); G.R = R
REAL = [r"\bpassword\b", r"(?i)\bconfidential\b", r"\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}", r"(?i)bitcoin",
        r"AKIA[0-9A-Z]{16}", r"\bEMP-\d{6}\b", r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b", r"\b\d{3}-\d{2}-\d{4}\b",
        r"(?i)\binternal use only\b", r"(?m)^secret: \w{4}$", r"(?<=key=)\w{8}(?!\w)", r"x(?=ab{2,3}c)"]
checked = incremental = mism = steps = rx_timeouts = 0
t0 = time.perf_counter()
for i in range(n):
    p = REAL[i % len(REAL)] if i % 5 == 0 else R.choice(["", "", "(?i)", "(?m)", "(?s)"]) + G.seq()
    try:
        re.compile(p); a = analyze(p)
        sc = RuleScanner(p)
    except (Refused, re.error, ValueError, OverflowError):
        continue
    if not sc.incremental:
        continue
    incremental += 1
    alpha = G.ALPHA + list("AKIA0123456789-EMP password confidential key=abc")
    text = "".join(R.choice(alpha) for _ in range(R.randint(20, 160)))
    if p in REAL:  # plant real matches
        text = text[:30] + R.choice(["AKIA" + "Q" * 16, "EMP-123456", "GB82WEST12345698765432", "123-45-6789",
                                      "password", "CONFIDENTIAL", "1234 5678 9012 3456", "key=abcdefgh "]) + text[30:]
    cuts = sorted(R.sample(range(1, len(text)), k=min(len(text) - 1, R.randint(1, 30))))
    ok = True
    timed_out = False
    for c in cuts + [len(text)]:
        prefix = text[:c]
        try:
            got = sc.scan(prefix)
            want = [m.span() for m in sc.rx.finditer(prefix, timeout=1.0)]
        except TimeoutError:
            timed_out = True; break
        steps += 1
        if got != want:
            mism += 1; ok = False
            if mism <= 10: print("MISMATCH", repr(p), "reach", sc.reach, repr(prefix), got, want)
            break
    if timed_out: rx_timeouts += 1; continue
    checked += ok
print(f"seed={seed} patterns={n} incremental={incremental} streams-ok={checked} steps={steps} mismatches={mism} rx-timeouts={rx_timeouts} in {time.perf_counter()-t0:.1f}s")
