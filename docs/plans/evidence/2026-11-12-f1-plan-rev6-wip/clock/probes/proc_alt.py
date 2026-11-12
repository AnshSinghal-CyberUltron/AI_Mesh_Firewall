"""Process-based alternative: what moving one off-loop pass to a worker process costs.

    python proc_alt.py

1. IPC: send the pass's text (128 KiB, 512 KiB; ASCII and non-ASCII) to a live worker process and get
   a small hit list back (the worker does no scanning, so this is pure transport). Pipe+pickle (what
   multiprocessing / ProcessPoolExecutor do) and a pre-allocated SharedMemory block (UTF-8 copy).
   Reported: the sending thread's CPU, the worker's CPU, and the round-trip wall time.
2. Memory: RSS of a fresh worker (spawn) after importing regex + the translator, and after it
   compiles a 64-rule plan: realistic DLP rules, and compile-heavy rules at the limits.
3. Startup: fork and spawn of one worker, to the first answered request.
"""
import multiprocessing as mp, os, statistics, sys, time
from multiprocessing import shared_memory
SNAP = "/tmp/claude-0/-home-claude/2a18a307-83e0-5467-9172-0939d20bde00/scratchpad/xlate5/clock/run-1"
sys.path.insert(0, SNAP)

def rss_kib():
    for line in open("/proc/self/status"):
        if line.startswith("VmRSS:"): return int(line.split()[1])

def worker(conn, shm_name):
    sys.path.insert(0, SNAP)
    shm = shared_memory.SharedMemory(name=shm_name) if shm_name else None
    while True:
        c0 = time.thread_time()          # recv blocks without CPU, so this also counts unpickling
        msg = conn.recv()
        if msg[0] == "text":
            n = len(msg[1])
        elif msg[0] == "shm":
            n = len(bytes(shm.buf[:msg[1]]).decode("utf-8"))
        elif msg[0] == "plan":
            import regex
            from translate_prototype import translate
            before = rss_kib()
            progs = [regex.compile(translate(p)) for p in msg[1]]
            conn.send(("rss", before, rss_kib(), len(progs))); continue
        elif msg[0] == "base":
            import regex, translate_prototype  # noqa: F401
            conn.send(("rss", rss_kib())); continue
        elif msg[0] == "stop":
            return
        hits = [(i, i + 8) for i in range(0, 80, 8)]
        conn.send((n, hits, time.thread_time() - c0))

def ipc(text, mode, reps=40):
    parent, child = mp.Pipe()
    shm = shared_memory.SharedMemory(create=True, size=4 * 512 * 1024 + 16) if mode == "shm" else None
    p = mp.get_context("fork").Process(target=worker, args=(child, shm.name if shm else None)); p.start()
    snd, wrk, rt = [], [], []
    for i in range(reps + 3):
        w0 = time.perf_counter(); c0 = time.thread_time()
        if mode == "pipe":
            parent.send(("text", text))
        else:
            b = text.encode("utf-8"); shm.buf[:len(b)] = b; parent.send(("shm", len(b)))
        c1 = time.thread_time()
        n, hits, wcpu = parent.recv()
        c2 = time.thread_time(); w1 = time.perf_counter()
        assert n == len(text)
        if i >= 3:
            snd.append((c1 - c0 + c2 - c1) * 1e3); wrk.append(wcpu * 1e3); rt.append((w1 - w0) * 1e3)
    parent.send(("stop",)); p.join()
    if shm: shm.close(); shm.unlink()
    med = statistics.median
    return med(snd), med(wrk), med(rt), sorted(rt)[int(0.9 * len(rt))]

if __name__ == "__main__":
    import regex, importlib.metadata as md
    print(f"python {sys.version.split()[0]}, regex {md.version('regex')}, pid {os.getpid()}")
    print("\n## 1. IPC per pass (worker does no scanning; 40 round trips, median)")
    print("| Text | Transport | sender thread CPU ms | worker CPU ms | round trip wall, median / p90 ms |")
    print("|---|---|---|---|---|")
    for kib in (128, 512):
        ascii_t = ("alpha beta gamma delta " * (kib * 1024 // 23 + 1))[:kib * 1024]
        uni_t = ("álpha βeta γamma 数据 " * (kib * 1024 // 18 + 1))[:kib * 1024]
        for label, t in (("ASCII", ascii_t), ("non-ASCII", uni_t)):
            for mode in ("pipe", "shm"):
                s, w, r, r90 = ipc(t, mode)
                print(f"| {kib} KiB {label} | {'Pipe + pickle' if mode == 'pipe' else 'SharedMemory (UTF-8 copy)'} | {s:.3f} | {w:.3f} | {r:.2f} / {r90:.2f} |", flush=True)

    print("\n## 2. Per-process memory (fresh spawned worker; VmRSS)")
    sys.path.insert(0, SNAP)
    real = [r"\bpassword\b", r"(?i)\bconfidential\b", r"\w+@\w+\.\w+", r"\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}",
            r"api[_-]?key\s*[:=]\s*\w{16,}", r"(?i)bitcoin", r"AKIA[0-9A-Z]{16}", r"\bEMP-\d{6}\b",
            r"(?i)\bproject\s+falcon\b", r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b"]
    plan_real = [p + f"(?:x{i})?" for i in range(7) for p in real][:64]            # 64 distinct realistic rules
    plan_heavy = [r"(?:\b\w{3}\b ){250}" + f"x{i}" for i in range(64)]            # perf5's heaviest shape at the limit
    ctx = mp.get_context("spawn")
    for label, plan in (("64 realistic DLP rules", plan_real), ("64 compile-heavy rules at the limits", plan_heavy)):
        a, b = ctx.Pipe(); p = ctx.Process(target=worker, args=(b, None)); p.start()
        a.send(("base",)); _, base = a.recv()
        a.send(("plan", plan)); _, before, after, n = a.recv()
        a.send(("stop",)); p.join()
        print(f"- {label}: worker RSS after importing regex + translator {base/1024:.1f} MiB; "
              f"compiling {n} rules adds {(after - before)/1024:.1f} MiB (total {after/1024:.1f} MiB)", flush=True)

    print("\n## 3. Worker startup to first answer (median of 5)")
    for method in ("fork", "spawn"):
        ts = []
        for _ in range(5):
            ctx = mp.get_context(method); a, b = ctx.Pipe()
            w0 = time.perf_counter(); p = ctx.Process(target=worker, args=(b, None)); p.start()
            a.send(("text", "x")); a.recv(); ts.append((time.perf_counter() - w0) * 1e3)
            a.send(("stop",)); p.join()
        print(f"- {method}: {statistics.median(ts):.0f} ms wall")
