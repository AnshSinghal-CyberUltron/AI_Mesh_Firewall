"""Crash fuzz of the ASAN+UBSan build of the patched `regex`, driven by fuzz_gen.py's grammar.

    python asan_fuzz.py OUT_DIR SECONDS [WORKERS]       (parent; the venv-asan interpreter)

The parent runs child processes, each over a batch of patterns, until SECONDS have passed. Each child
writes the pattern it is about to compile/match to OUT_DIR/cur-<pid>.txt first, so a child that dies
by a signal or an ASAN/UBSan abort names the pattern. Children get:
  LD_PRELOAD=libasan.so            (ASAN must be the first library; CPython itself is not instrumented)
  PYTHONMALLOC=malloc              (otherwise pymalloc serves small PyMem_Malloc blocks, with no redzones)
  ASAN_OPTIONS=detect_leaks=0:halt_on_error=1:log_path=OUT_DIR/asan   UBSAN_OPTIONS=halt_on_error=1:...
Per pattern (the plan's usage): re.compile (skip invalid), translate (skip Refused), regex.compile,
then finditer(text, pos=q, timeout=0.2, concurrent=True) over 6 short texts and 2 long texts.
Plus the limit-edge shapes, once per child.
"""
import json, os, random, re, signal, subprocess, sys, time
SNAP = os.environ.get("XLATE_SNAP") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
LIBASAN = subprocess.run(["gcc", "-print-file-name=libasan.so"], capture_output=True, text=True).stdout.strip()

EDGE = ["x{1000}", "(?:ab){500}", "[a-z0-9]{1000}", r"(?:\w{3}\s){250}", "x{100}" * 10, "(?:a|b|c|d|e|f|g|h){1000}",
        "|".join(f"w{i:03d}" for i in range(198))[:990].rstrip("|"), r"(?:\b\w{3}\b ){250}",
        "(" * 32 + "a" + ")" * 32, "(?:" * 32 + "a" + ")" * 32, r"\s" * 500, r"\b." * 330, r"(?=\w)" * 166 + "a",
        "(?i)" + "".join(f"[{chr(0x41+i)}{chr(0x100+i)}]" for i in range(64)), r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b",
        r"(?i)(?:k|s|i)+?\b", r"(?s)(?:.|\n){0,1000}x", r"(?:(?:a?){0,30}){0,30}b", r"(a|b|ab)*c", r"((a)|b)*?(?(2)c|d)",
        r"(?<=\w{3})x", r"(?<!\d)\d{3,1000}(?!\d)", r"(?>a+)+b", r"a*+a", r"(?:x++)++y"]

def child(seed, start, count, out):
    sys.path.insert(0, SNAP)
    import warnings; warnings.simplefilter("ignore")
    import regex, fuzz_gen as G
    from translate_prototype import translate, Refused
    cur = open(os.path.join(out, f"cur-{os.getpid()}.txt"), "w")
    def note(kind, p):
        cur.seek(0); cur.truncate(); cur.write(json.dumps([kind, p])); cur.flush()
    stats = {"patterns": 0, "compiled": 0, "refused": 0, "invalid": 0, "matches": 0, "timeouts": 0, "errors": 0}
    def run(p, R):
        try:
            re.compile(p)
        except Exception:
            stats["invalid"] += 1; return
        try:
            tr = translate(p)
        except Refused:
            stats["refused"] += 1; return
        except Exception:
            stats["errors"] += 1; return
        note("compile", p)
        try:
            rx = regex.compile(tr)
        except Exception:
            stats["errors"] += 1; return
        stats["compiled"] += 1
        texts = ["".join(R.choice(G.ALPHA) for _ in range(R.randint(0, 24))) for _ in range(6)]
        texts += ["".join(R.choice(G.ALPHA) for _ in range(R.randint(500, 3000))) for _ in range(2)]
        for t in texts:
            note("match", p)
            q = R.randint(0, len(t)) if t and R.random() < 0.5 else 0
            try:
                for _m in rx.finditer(t, pos=q, timeout=0.2, concurrent=True):
                    stats["matches"] += 1
            except TimeoutError:
                stats["timeouts"] += 1
            except Exception:
                stats["errors"] += 1
    R0 = random.Random(seed)
    if start == 0:
        for p in EDGE:
            stats["patterns"] += 1; run(p, R0)
    for i in range(start, start + count):
        R = random.Random(seed * 1_000_003 + i); G.R = R
        p = R.choice(["", "", "(?i)", "(?m)", "(?s)", "(?im)"]) + G.seq()
        stats["patterns"] += 1; run(p, R)
    note("done", "")
    print(json.dumps(stats), flush=True)

def parent(out, seconds, workers):
    os.makedirs(out, exist_ok=True)
    env = dict(os.environ, LD_PRELOAD=LIBASAN, PYTHONMALLOC="malloc", PYTHONDONTWRITEBYTECODE="1",
               ASAN_OPTIONS=f"detect_leaks=0:halt_on_error=1:abort_on_error=0:exitcode=99:log_path={out}/asan",
               UBSAN_OPTIONS=f"halt_on_error=1:print_stacktrace=1:log_path={out}/ubsan")
    env.pop("PYTHONPATH", None)
    # positive control: ASAN intercepts an out-of-bounds memset on a malloc'd block in this setup
    ctl = subprocess.run([sys.executable, "-c",
        "import ctypes, regex; c = ctypes.CDLL(None); c.malloc.restype = ctypes.c_void_p; "
        "p = c.malloc(16); c.memset(ctypes.c_void_p(p), 0, 32)"],
        env=dict(env, ASAN_OPTIONS="detect_leaks=0:halt_on_error=1:exitcode=99"), capture_output=True, text=True)
    hit = "heap-buffer-overflow" in ctl.stderr
    print(f"positive control (16-byte malloc, 32-byte memset): exit {ctl.returncode}, ASAN report: {hit}", flush=True)
    total = {}; deaths = []; batch = 300; nxt = 0; seed = 7
    t_end = time.time() + seconds; running = []
    while time.time() < t_end or running:
        while time.time() < t_end and len(running) < workers:
            pr = subprocess.Popen([sys.executable, "-s", "-B", __file__, "child", str(seed), str(nxt), str(batch), out],
                                  env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            running.append((pr, nxt)); nxt += batch
        time.sleep(0.2)
        for pr, st in list(running):
            if pr.poll() is None: continue
            running.remove((pr, st))
            so, se = pr.communicate()
            if pr.returncode == 0 and so.strip():
                for k, v in json.loads(so.strip().splitlines()[-1]).items(): total[k] = total.get(k, 0) + v
            else:
                curf = os.path.join(out, f"cur-{pr.pid}.txt")
                last = open(curf).read() if os.path.exists(curf) else "?"
                sig = -pr.returncode if pr.returncode < 0 else None
                deaths.append({"batch_start": st, "returncode": pr.returncode,
                               "signal": signal.Signals(sig).name if sig else None, "last": last, "stderr_tail": se[-2000:]})
                print("CHILD DIED", json.dumps(deaths[-1])[:3000], flush=True)
            cf = os.path.join(out, f"cur-{pr.pid}.txt")
            if os.path.exists(cf) and pr.returncode == 0: os.remove(cf)
    reports = [f for f in os.listdir(out) if f.startswith(("asan", "ubsan"))]
    summary = {"seconds": seconds, "workers": workers, "seed": seed, "patterns_scheduled": nxt, "totals": total,
               "child_deaths": len(deaths), "sanitizer_report_files": reports}
    json.dump({"summary": summary, "deaths": deaths}, open(os.path.join(out, "summary.json"), "w"), indent=1)
    print(json.dumps(summary), flush=True)

if __name__ == "__main__":
    if sys.argv[1] == "child":
        child(int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), sys.argv[5])
    else:
        parent(sys.argv[1], float(sys.argv[2]), int(sys.argv[3]) if len(sys.argv) > 3 else 2)
