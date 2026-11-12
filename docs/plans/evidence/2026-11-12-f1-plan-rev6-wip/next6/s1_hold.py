"""Reviewer A, revision 5, S1: non-monotone pattern cost in the streaming scan. Policy "refuse"
(revision 5: a timeout on any pass refuses) against policy "hold" (a timeout on a non-final pass
holds and retries; only the final pass refuses), on A's case and on the incremental fuzz corpus.

    python s1_hold.py <seed> <patterns> <budget_ms>

For every stream: the decision of each policy, its thread CPU, the CPU of its timed-out passes,
and the non-streaming reference (one whole-text scan of the final text under the same budget).
Exactness: every hit list "hold" returns equals a whole-text finditer of that prefix.
Hold latency: on each delta where "hold" is undecided, the characters it holds beyond what an
unbounded-budget scanner would hold (its resume point q).
"""
import random, re, statistics, sys, time, warnings
sys.path.insert(0, ".")
warnings.simplefilter("ignore")
import fuzz_gen as G
import translate_prototype as T
from incremental import RuleScanner, Undecided

def run_stream(p, text, cuts, budget, policy):
    sc = RuleScanner(p, stopper_retry="once" if policy == "hold-once" else "always")
    oracle = RuleScanner(p) if policy != "refuse" else None
    c0 = time.thread_time(); outcome = "decided"; mism = 0; extra_hold = []; undecided = 0; oracle_dead = False; pol_cpu = 0.0; ref_timeout_cpu, ref_timeouts = 0.0, 0
    ends = cuts + [len(text)]
    for idx, c in enumerate(ends):
        prefix = text[:c]; last = idx == len(ends) - 1
        if policy == "refuse":
            c1 = time.thread_time()
            try:
                sc.scan(prefix, budget)
            except TimeoutError:
                pol_cpu += time.thread_time() - c1
                ref_timeout_cpu, ref_timeouts = time.thread_time() - c1, 1
                outcome = f"refused at {c}"; break
            pol_cpu += time.thread_time() - c1
            continue
        c1 = time.thread_time()
        try:
            r = sc.scan_or_hold(prefix, budget, final=last)
        except TimeoutError:
            pol_cpu += time.thread_time() - c1
            outcome = "refused at end"; break
        pol_cpu += time.thread_time() - c1
        oq = None
        if not oracle_dead:  # an unbounded scanner's resume point; skipped once it is itself catastrophic
            try:
                oracle.scan(prefix, min(20 * budget, 0.2)); oq = oracle.q if oracle.incremental else 0
            except TimeoutError:
                oracle_dead = True
        if isinstance(r, Undecided):
            undecided += 1
            if oq is not None:
                extra_hold.append(max(0, oq - r.hold_from))
        else:
            try:
                want = [m.span() for m in sc.rx.finditer(prefix, timeout=5.0)]
            except TimeoutError:
                want = r  # cannot happen: `hold` just decided this prefix within the budget
            if r != want:
                mism += 1
    if policy == "refuse":
        return outcome, pol_cpu, ref_timeout_cpu, ref_timeouts, mism, undecided, extra_hold
    return outcome, pol_cpu, sc.timeout_cpu, sc.timeouts, mism, undecided, extra_hold

def reference(p, text, budget):
    rx = RuleScanner(p).rx
    try:
        list(rx.finditer(text, timeout=budget, concurrent=True)); return "decided"
    except TimeoutError:
        return "refused"

def report(name, rows):
    pols = ["refuse", "hold", "hold-once"]
    print(f"\n{name}: {len(rows)} streams")
    for pol in pols:
        outs = [r[pol] for r in rows]
        refused = sum(o[0] != "decided" for o in outs)
        false_ref = sum(o[0] != "decided" and r["ref"] == "decided" for o, r in zip(outs, rows))
        cpu = sum(o[1] for o in outs) * 1e3; tcpu = sum(o[2] for o in outs) * 1e3
        tos = sum(o[3] for o in outs); mism = sum(o[4] for o in outs)
        und = sum(o[5] for o in outs); holds = [h for o in outs for h in o[6]]
        line = (f"  {pol:9} refused {refused:4} (where the whole text is NOT refused: {false_ref:3}); "
                f"CPU {cpu:9.1f} ms, of which timed-out passes {tcpu:8.1f} ms ({tos} timeouts)")
        if pol != "refuse":
            line += (f"; mismatches {mism}; undecided deltas {und}; extra held chars on those: "
                     + (f"median {statistics.median(holds):.0f}, max {max(holds)}" if holds else "none"))
        print(line)
    print(f"  whole-text reference refuses {sum(r['ref'] == 'refused' for r in rows)}")

if __name__ == "__main__":
    seed = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 2000
    budget = (float(sys.argv[3]) if len(sys.argv) > 3 else 1.0) / 1e3

    # A's case, 4-character deltas, the plan's 100 ms per-pass budget.
    S1 = r"^(\w+\s?){1,30}[.!?]"
    for label, text in (("A's sentence", "The quarterly report shows that revenue grew in every region and the team now expects further gains next year."),
                        ("A's sentence with a comma (the expensive attempt becomes final)", "The quarterly report shows that revenue grew, in every region and the team now expects further gains next year.")):
        cuts = list(range(4, len(text), 4))
        print(f"\nS1 {label}, {len(text)} chars in {len(cuts)+1} deltas, budget 100 ms:")
        print(f"  reference (whole text once): {reference(S1, text, 0.1)}")
        for pol in ("refuse", "hold", "hold-once"):
            o = run_stream(S1, text, cuts, 0.1, pol)
            print(f"  {pol:9} {o[0]:16} CPU {o[1]*1e3:7.1f} ms, timed-out passes {o[2]*1e3:7.1f} ms ({o[3]}), "
                  f"mismatches {o[4]}, undecided deltas {o[5]}, extra held chars max {max(o[6]) if o[6] else 0}")

    # The incremental fuzz corpus (incremental_fuzz.py's generator and chunkings) plus catastrophic shapes.
    R = random.Random(seed); G.R = R
    CAT = [S1, r"(\w+\s?)+$", r"(a|a)+b", r"(?:\w+\s?){1,20}x", r"(\d+)+x", r"^(\w+\s*)*:"]
    rows = []
    t0 = time.perf_counter()
    for i in range(n):
        p = CAT[i % len(CAT)] if i % 10 == 0 else R.choice(["", "", "(?i)", "(?m)", "(?s)"]) + G.seq()
        try:
            re.compile(p); sc = RuleScanner(p)
        except Exception:
            continue
        if not sc.incremental:
            continue
        alpha = G.ALPHA + list("AKIA0123456789-EMP password confidential key=abc")
        text = "".join(R.choice(alpha) for _ in range(R.randint(20, 160)))
        if p in CAT:
            text = " ".join("".join(R.choice("abcdef") for _ in range(R.randint(2, 7))) for _ in range(R.randint(4, 14))) + R.choice([".", "", ",", "!"])
        cuts = sorted(R.sample(range(1, len(text)), k=min(len(text) - 1, R.randint(1, 30))))
        row = {"ref": reference(p, text, budget)}
        for pol in ("refuse", "hold", "hold-once"):
            row[pol] = run_stream(p, text, cuts, budget, pol)
        rows.append(row)
    report(f"Corpus seed {seed}, budget {budget*1e3:g} ms per pass ({time.perf_counter()-t0:.0f}s)", rows)
    hit = [r for r in rows if r["refuse"][3] or r["hold"][3]]
    report(f"  of which streams where some pass timed out", hit)
