"""Plan 5.2.5, revision 6 (Reviewer B, revision 5, should-fix 3): Reviewer B's per-org inline loop
bucket simulation (rate 25% of one core, burst one 10 ms window, debited with each inline call's
thread CPU), extended with a WORKER-WIDE inline bucket on top: an inline customer scan runs only
when both its org's bucket and the worker bucket hold credit; otherwise it goes off the loop.

K busy orgs, each with 4 streams at 50 deltas/s, each delta costing about 4.5 ms of customer-regex
CPU (just under the 5 ms inline budget, so no inline call times out: the worst case for the loop).
Tenant B is a coroutine that sleeps 1 ms and records how late it wakes. Two baselines separate the
loop's own occupancy from host CPU contention: no busy org at all, and every busy org's scan off the
loop (a 0% worker bucket): the off-loop threads then still compete with the loop thread for the
host's 2 CPUs.

    python inline_bucket6.py [seconds] [reps]
"""
import asyncio, concurrent.futures as cf, random, statistics, sys, time
sys.path.insert(0, ".")
import regex
from translate_prototype import translate

RHO, BURST = 0.25, 0.010
RX = regex.compile(translate(r"\b\w+\s+(?:token|secret)\b"))
def make_text(n):
    return ("alpha beta gamma delta " * (n // 23 + 1))[:n]
def cost(txt):
    best = 1.0
    for _ in range(5):
        c0 = time.thread_time(); list(RX.finditer(txt, concurrent=True)); best = min(best, time.thread_time() - c0)
    return best
n = 32768
for _ in range(6):  # calibrate the text so one delta costs about 4.5 ms here
    n = int(n * 0.0045 / cost(make_text(n)))
TXT = make_text(n)
COST = cost(TXT)
def per_delta():
    list(RX.finditer(TXT, concurrent=True))

async def run(k_orgs, agg_rate, seconds, lag_gate=None):
    loop = asyncio.get_running_loop(); ex = cf.ThreadPoolExecutor(2)
    t_start = time.perf_counter()
    buckets = {o: [BURST, t_start] for o in range(k_orgs)}
    agg = [BURST, t_start]
    lat, cpu_lat, inline_cpu, offloop = [], [], 0.0, 0
    stop = t_start + seconds
    async def tenant_b():
        # Two delays per tick: wall lateness, and the loop thread's own CPU spent while B waited
        # (the loop work ahead of B, without the host preempting the loop thread).
        while time.perf_counter() < stop:
            t = time.perf_counter(); c = time.thread_time()
            await asyncio.sleep(0.001)
            lat.append(time.perf_counter() - t - 0.001); cpu_lat.append(time.thread_time() - c)
    def refill(bk, rate, now):
        bk[0] = min(BURST, bk[0] + (now - bk[1]) * rate); bk[1] = now
    async def stream(o):
        nonlocal inline_cpu, offloop
        await asyncio.sleep(random.random() * 0.02)
        while time.perf_counter() < stop:
            now = time.perf_counter()
            bk = buckets[o]; refill(bk, RHO, now)
            if agg_rate is not None:
                refill(agg, agg_rate, now)
            gated = lag_gate is not None and lat and lat[-1] > lag_gate
            if bk[0] > 0 and (agg_rate is None or agg[0] > 0) and not gated:
                c = time.thread_time(); per_delta(); used = time.thread_time() - c
                bk[0] -= used; inline_cpu += used
                if agg_rate is not None:
                    agg[0] -= used
            else:
                offloop += 1
                await loop.run_in_executor(ex, per_delta)
            await asyncio.sleep(0.020)
    await asyncio.gather(tenant_b(), *[stream(o) for o in range(k_orgs) for _ in range(4)])
    wall = time.perf_counter() - t_start
    ex.shutdown(wait=False, cancel_futures=True); lat.sort(); cpu_lat.sort()
    return (lat[len(lat) // 2] * 1e3, lat[int(len(lat) * 0.99)] * 1e3, inline_cpu / wall, offloop,
            cpu_lat[len(cpu_lat) // 2] * 1e3, cpu_lat[int(len(cpu_lat) * 0.99)] * 1e3, cpu_lat[-1] * 1e3)

if __name__ == "__main__":
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 3.0
    reps = int(sys.argv[2]) if len(sys.argv) > 2 else 2
    print(f"one delta = {COST*1e3:.2f} ms CPU ({len(TXT)} chars); per-org bucket {RHO:.0%}, burst {BURST*1e3:.0f} ms; {seconds:g} s per run, median of {reps}")
    print("| Worker-wide inline bucket | Busy orgs | Inline customer-regex share of the loop | Tenant B wall delay p50 | p99 | Loop CPU ahead of B p50 | p99 | max |")
    print("|---|---|---|---|---|---|---|---|")
    # (worker-wide rate, busy orgs, lag gate): the gate skips inline scans while the loop's last
    # measured lateness exceeds it (an oversubscribed host stretches every inline call in wall time,
    # and a wall-clock bucket then refills during the very calls it is meant to limit).
    configs = [(None, 0, None)] + [(agg, k, None) for agg in (None, 0.25, 0.40, 0.50, 0.0) for k in (2, 4, 6)]
    configs += [(0.25, k, 0.005) for k in (2, 4, 6)]
    if len(sys.argv) > 3:  # e.g. "0.0:2,0.25:4:0.005" to run only some configurations
        configs = [((None if c.split(":")[0] == "none" else float(c.split(":")[0])), int(c.split(":")[1]),
                    float(c.split(":")[2]) if len(c.split(":")) > 2 else None) for c in sys.argv[3].split(",")]
    for agg, k, gate in configs:
        if True:
            rs = [asyncio.run(run(k, agg, seconds, gate)) for _ in range(reps)]
            p50 = statistics.median(r[0] for r in rs); p99 = statistics.median(r[1] for r in rs)
            share = statistics.median(r[2] for r in rs)
            c50 = statistics.median(r[4] for r in rs); c99 = statistics.median(r[5] for r in rs); cmax = max(r[6] for r in rs)
            label = "none (rev. 5)" if agg is None else ("0% (every scan off the loop)" if agg == 0 else f"{agg:.0%}")
            if gate is not None:
                label += f" + lag gate {gate*1e3:g} ms"
            print(f"| {label} | {k} | {share*100:.0f}% | {p50:.1f} ms | {p99:.1f} ms | {c50:.2f} ms | {c99:.2f} ms | {cmax:.1f} ms |", flush=True)
