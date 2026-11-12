"""Differential fuzz: translated `regex` vs `re` on random patterns (revision 5).

Adds possessive quantifiers, atomic groups and lookbehinds to the grammar (Reviewer A, rev 4),
and runs patterns in worker processes so a pattern that hangs inside `re` itself (which cannot
be interrupted) is skipped and counted instead of hanging the run.

    python fuzz.py <seed> <patterns>
"""
import multiprocessing as mp, random, re, sys, time, warnings

def check_one(args):
    seed, i = args
    import regex, fuzz_gen as G
    from translate_prototype import translate, Refused
    warnings.simplefilter("ignore")
    R = random.Random(seed * 1_000_003 + i); G.R = R
    p = R.choice(["", "", "(?i)", "(?m)", "(?s)", "(?im)"]) + G.seq()
    try:
        a = re.compile(p)
    except Exception:
        return ("skipped", p, None)
    try:
        b = regex.compile(translate(p))
    except Refused:
        return ("refused", p, None)
    except Exception as e:
        return ("compile-fail", p, f"{type(e).__name__}: {e}")
    bad = None; n = 0
    for _ in range(8):
        s = "".join(R.choice(G.ALPHA) for _ in range(R.randint(0, 24)))
        t0 = time.thread_time()
        sa = [m.span() for m in a.finditer(s)]
        t_re = time.thread_time() - t0
        try:
            sb = [m.span() for m in b.finditer(s, timeout=1.0)]
        except TimeoutError:
            # `regex` used > 1 s of CPU where `re` finished: a speed difference, recorded apart
            return ("rx-timeout", p, f"re {t_re*1e3:.1f} ms on {s!r}")
        n += 1
        if sa != sb and bad is None:
            bad = (s, sa, sb)
    return ("mismatch" if bad else "ok", p, bad or n)

def run(seed, n):
    sys.path.insert(0, ".")
    counts = {"ok": 0, "mismatch": 0, "rx-timeout": 0, "skipped": 0, "refused": 0, "compile-fail": 0, "re-hang": 0}
    checks = 0
    t0 = time.perf_counter()
    ctx = mp.get_context("fork")
    todo = list(range(n))
    while todo:
        with ctx.Pool(2) as pool:
            pending = [(j, pool.apply_async(check_one, ((seed, j),))) for j in todo[:1000]]
            rest = todo[1000:]
            done = []
            for idx, (j, res) in enumerate(pending):
                try:
                    kind, p, info = res.get(timeout=20)
                except mp.TimeoutError:
                    counts["re-hang"] += 1
                    # restart the pool; resubmit everything after the hung pattern
                    todo = [k for k, _ in pending[idx + 1:]] + rest
                    pool.terminate()
                    break
                counts[kind] += 1
                if kind == "ok": checks += info
                elif kind in ("mismatch", "compile-fail", "rx-timeout") and counts[kind] <= 10:
                    print(kind.upper(), repr(p), info, flush=True)
            else:
                todo = rest
    print(f"seed={seed} patterns={n} checks={checks} " + " ".join(f"{k}={v}" for k, v in counts.items())
          + f" in {time.perf_counter()-t0:.1f}s")

if __name__ == "__main__":
    run(int(sys.argv[1]) if len(sys.argv) > 1 else 1, int(sys.argv[2]) if len(sys.argv) > 2 else 4000)
