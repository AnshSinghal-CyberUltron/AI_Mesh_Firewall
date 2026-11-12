"""Revision-5 cost measurements: translation, compile, parse-only checks, match speed, streaming.

Every time is thread CPU time unless marked wall (the `regex` timeout counts thread CPU time).
"""
import asyncio, gc, random, re, statistics, sys, threading, time, tracemalloc
import regex
sys.path.insert(0, ".")
from translate_prototype import INF, analyze, check_limits, translate, _ci_set, _ci_verdicts
from incremental import RuleScanner

def cpu(fn, reps=5):
    best = 1e9
    for _ in range(reps):
        t0 = time.thread_time(); fn(); best = min(best, time.thread_time() - t0)
    return best * 1e3

def clear():
    _ci_set.cache_clear(); _ci_verdicts.cache_clear()

out = []
def emit(line=""):
    print(line, flush=True); out.append(line)

# ---- 1. translation cost at the limits ---------------------------------------------------
R = random.Random(5)
cased = [chr(c) for c in range(0x41, 0x24F) if chr(c).lower() != chr(c).upper()]
def ci_literals(n):  # (?i) + n distinct cased literals
    return "(?i)" + "".join(re.escape(c) for c in R.sample(cased, n))
def ci_sets(n):      # (?i) + n distinct two-char sets
    return "(?i)" + "".join("[" + re.escape(a) + re.escape(b) + "]" for a, b in (R.sample(cased, 2) for _ in range(n)))
emit("## 1. Translation (cold caches), thread CPU ms")
emit("| Pattern | Length | ci items | Translate | Compile |")
emit("|---|---|---|---|---|")
for name, p in [("(?i) + 64 distinct literals", ci_literals(64)), ("(?i) + 64 distinct 2-char sets", ci_sets(64)),
                ("(?i) + 65 distinct literals (over the cap)", ci_literals(65))]:
    try:
        a = check_limits(p)
    except Exception as e:
        emit(f"| {name} | {len(p)} | — | refused: {e} | — |"); continue
    clear(); t = cpu(lambda: (clear(), translate(p)), reps=3)
    tr = translate(p); c = cpu(lambda: regex.compile(tr, flags=0) and regex.purge(), reps=3)
    emit(f"| {name} | {len(p)} | {a.ci_items} | {t:.1f} | {c:.1f} |")
plan = [ci_sets(64) for _ in range(64)]
clear(); t0 = time.thread_time()
for p in plan: check_limits(p); regex.compile(translate(p))
emit(f"\nA hostile 64-rule plan (each rule (?i) + 64 distinct 2-char sets), translate + compile, cold: "
     f"{(time.thread_time()-t0)*1e3:.0f} ms CPU")
# loop stall while that plan compiles on a worker thread
async def ticker(stop):
    lags = []; last = time.perf_counter()
    while not stop.is_set():
        await asyncio.sleep(0.001); now = time.perf_counter(); lags.append(now - last - 0.001); last = now
    return lags
async def stall():
    stop = threading.Event()
    def work():
        clear()
        for p in plan: regex.compile(translate(p))
        stop.set()
    task = asyncio.create_task(ticker(stop))
    await asyncio.get_running_loop().run_in_executor(None, work)
    lags = await task
    return statistics.quantiles(lags, n=100)[98] * 1e3, max(lags) * 1e3, len(lags)
p99, worst, ticks = asyncio.run(stall())
emit(f"Event-loop lag while a worker thread translates + compiles that plan: p99 {p99:.1f} ms, worst {worst:.1f} ms ({ticks} ticks)")

# ---- 2. compile cost at the expanded-repetition limit -----------------------------------
emit("\n## 2. Compile at the limits (expanded repetition <= 1000)")
emit("| Pattern | Expanded | Compile CPU ms | Traced peak KiB |")
emit("|---|---|---|---|")
shapes = [("x{1000}", "x{1000}"), ("(?:ab){500}", "(?:ab){500}"), ("(?:x{10}){100}", "(?:x{10}){100}"),
          ("[a-z0-9]{1000}", "[a-z0-9]{1000}"), ("(?:\\w{3}\\s){250}", r"(?:\w{3}\s){250}"),
          ("(?i)(?:[a-z]{10}){100}", "(?i)(?:[a-z]{10}){100}"), ("x{100} x 10", "x{100}" * 10),
          ("(?:a|b|c|d|e|f|g|h){1000}", "(?:a|b|c|d|e|f|g|h){1000}"),
          ("990-char literal alternation", "|".join(f"w{i:03d}" for i in range(198))[:990].rstrip("|")),
          ("(?:\\b\\w{3}\\b ){250} (one-sided)", r"(?:\b\w{3}\b ){250}"),
          ("x{1000} x 140 (B, rev 4)", "x{1000}" * 140)]
for name, p in shapes:
    try:
        a = check_limits(p)
    except Exception as e:
        emit(f"| `{name}` | — | refused: {e} | — |"); continue
    tr = translate(p)
    c = cpu(lambda: (regex.purge(), regex.compile(tr)), reps=3)
    regex.purge(); gc.collect(); tracemalloc.start(); obj = regex.compile(tr); peak = tracemalloc.get_traced_memory()[1]; tracemalloc.stop(); del obj
    emit(f"| `{name}` | {a.expanded_repetition} | {c:.1f} | {peak/1024:.0f} |")

# ---- 3. parse-only check (projector / control) -------------------------------------------
emit("\n## 3. Parse-only check (analysis + limits, no translation), thread CPU ms")
worst = [ci_sets(64), "x{10}" * 100, r"(?:\b\w{3}\b ){250}", "(" * 100 + "a" + ")" * 100,
         "|".join(f"w{i:03d}" for i in range(198))[:990].rstrip("|")]
for p in worst:
    emit(f"- `{p[:40]}...` ({len(p)} chars): {cpu(lambda: check_limits(p) if True else None, reps=5):.2f} ms" if True else "")
t0 = time.thread_time()
for p in plan: check_limits(p)
emit(f"- 64-rule hostile plan: {(time.thread_time()-t0)*1e3:.1f} ms")

# ---- 4. match speed against re -----------------------------------------------------------
emit("\n## 4. Match speed against `re` (32 KiB), thread CPU ms, best of 5")
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
emit("| Pattern | prose: re | prose: translated | code: re | code: translated | worst ratio |")
emit("|---|---|---|---|---|---|")
ratios = []
for p in CORPUS:
    a, b = re.compile(p), regex.compile(translate(p))
    row = []
    for text in (prose, code):
        assert [m.span() for m in a.finditer(text)] == [m.span() for m in b.finditer(text)], p
        ra = cpu(lambda: list(a.finditer(text))); rb = cpu(lambda: list(b.finditer(text, timeout=5, concurrent=True)))
        row += [ra, rb]
    ratio = max(row[1] / max(row[0], 0.05), row[3] / max(row[2], 0.05))
    ratios.append(ratio)
    emit(f"| `{p}` | {row[0]:.2f} | {row[1]:.2f} | {row[2]:.2f} | {row[3]:.2f} | {ratio:.1f}x |")
emit(f"Worst ratio (each side floored at 0.05 ms): {max(ratios):.1f}x")

# ---- 5. streaming: per-delta inline cost ---------------------------------------------------
import copy
emit("\n## 5. Streaming per-delta cost (10 rules), thread CPU ms per delta")
emit("Texts (deterministic, seed 3): `punctuated prose` (a period or comma about every 8 words, a newline about every 60);"
     " `unpunctuated prose` (words and spaces only: no stopper ever appears, the worst case for stopper-mode rules);"
     " `code` (assignments, calls, hex, base64-like strings).")
TEN_B = [r"\bpassword\b", r"(?i)\bconfidential\b", r"\w+@\w+\.\w+", r"\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}",
         r"api[_-]?key\s*[:=]\s*\w{16,}", r"(?i)bitcoin", r"AKIA[0-9A-Z]{16}", r"\bEMP-\d{6}\b",
         r"(?i)\bproject\s+falcon\b", r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b"]
TEN_A = [r"\bpassword\b", r"(?i)\bconfidential\b", r"\w+@\w+\.\w+", r"\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}",
         r"api[_-]?key\s*[:=]\s*\w{16,}", r"(?i)bitcoin", r"AKIA[0-9A-Z]{16}",
         r"(?i)\binternal use only\b", r"\b\d{3}-\d{2}-\d{4}\b", r"\b\w+\s+(?:token|secret)\b"]
def punct_prose(n):
    out, i = [], 0
    while sum(map(len, out)) < n:
        w = R2.choice(words); i += 1
        out.append(w + (". " if i % 9 == 0 else ", " if i % 7 == 0 else "\n" if i % 61 == 0 else " "))
    return "".join(out)[:n]
TEXTS = {"punctuated prose": punct_prose(524288),
         "unpunctuated prose": " ".join(R2.choice(words) for _ in range(120000))[:524288],
         "code": (code * 17)[:524288]}
def mode(sc):
    if not sc.incremental: return "full"
    return "reach" if sc.reach != INF else "stopper"
for set_name, rules in (("B's 10 rules", TEN_B), ("A's 10 rules", TEN_A)):
    probe = [RuleScanner(p) for p in rules]
    emit(f"\n{set_name}: modes " + ", ".join(f"`{p}` {mode(sc)}" for p, sc in zip(rules, probe)))
    emit("| Text | Output so far | Revision 4: whole rescan, all 10 | Revision 5: all 10, incremental | of which reach/stopper rules |")
    emit("|---|---|---|---|---|")
    compiled = [regex.compile(translate(p)) for p in rules]
    for tname, text in TEXTS.items():
        for n in (4096, 32768, 131072, 524288):
            prefix = text[:n]
            whole = cpu(lambda: [list(c.finditer(prefix, timeout=5, concurrent=True)) for c in compiled], reps=3)
            scs = [RuleScanner(p) for p in rules]
            step = max(4, n // 64)
            for c in range(step, n - 4, step):   # build realistic state from a chunked stream
                for sc in scs: sc.scan(prefix[:c])
            for sc in scs: sc.scan(prefix[:-4])
            snap = [(sc.q, sc.z, sc.stop_upto, len(sc.final)) for sc in scs]
            def restore():
                for sc, (q, z, up, nf) in zip(scs, snap):
                    sc.q, sc.z, sc.stop_upto = q, z, up; del sc.final[nf:]
            def delta(only_incremental):
                for sc in scs:
                    if only_incremental and not sc.incremental: continue
                    sc.scan(prefix)
                restore()
            emit(f"| {tname} | {n//1024} KiB | {whole:.2f} | {cpu(lambda: delta(False)):.3f} | {cpu(lambda: delta(True)):.3f} |")

open("perf5.md", "w").write("\n".join(out) + "\n")
