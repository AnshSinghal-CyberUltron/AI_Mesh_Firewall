"""Revision 7 (Plan Reviewer B, revision 6, should-fix 4): a fast lane in the scan scheduler.

    python drr_fast7.py [trials]

Discrete-event simulation in thread CPU (wall time when each scan thread has a core). T scan threads.
H heavy orgs, each with an endless backlog of budget-sized passes (b = 100 ms; 70% use all of it, as
pathological rules do); 8 light orgs submit ordinary passes (0.05-2 ms, Poisson, 200 passes/s in
total). Reported: a light pass's wait from submission to start (p50, p99, max), and the heavy orgs'
share of the scan threads.

  rev 6:  one deficit-round-robin scheduler over all orgs, an org holds at most max(1, T-1) threads;
  rev 7:  the same, plus a FAST LANE: one of the T threads only runs passes of orgs whose recent pass
          cost (EWMA, weight 1/4) is under F = 5 ms, each capped at 2F of thread CPU; a fast-lane pass
          that reaches 2F stops (no refusal: it is not a final verdict) and is re-queued on the DRR
          lanes with its full budget, and its org's estimate rises above F. The DRR lanes are the
          other T - 1 threads, so heavy orgs together hold at most T - 1 threads.
"""
import heapq
import random
import statistics
import sys

B = 0.100
F = 0.005


def simulate(T: int, H: int, fast: bool, horizon: float = 30.0, seed: int = 1):
    R = random.Random(seed)
    light = [f"L{i}" for i in range(8)]
    heavy = [f"H{i}" for i in range(H)]
    ewma = {o: 0.001 for o in light}
    ewma.update({o: B for o in heavy})
    queues = {o: [] for o in light + heavy}  # FIFO of (submit time, cost, light?)
    for o in heavy:
        queues[o] = [(0.0, None, False)] * 10**6  # endless backlog; cost drawn at start
    events = []  # (time, kind, payload)
    t = 0.0
    while t < horizon:  # light arrivals
        t += R.expovariate(200)
        heapq.heappush(events, (t, "arrive", (R.choice(light), R.uniform(0.00005, 0.002))))
    deficit = {o: 0.0 for o in queues}
    running = {o: 0 for o in queues}
    order = light + heavy
    R.shuffle(order)
    ptr = [0]
    lanes = (["fast"] if fast else []) + ["drr"] * (T - 1 if fast else T)
    free = {i: lanes[i] for i in range(len(lanes))}
    waits = []
    heavy_cpu = 0.0
    cap = max(1, T - 1)
    now = 0.0

    def start(lane_id, org, item, budget):
        nonlocal heavy_cpu
        submit, cost, is_light = item
        if cost is None:
            cost = B if R.random() < 0.7 else R.uniform(0, B)
        run = min(cost, budget)
        if is_light:
            waits.append(now - submit)
        else:
            heavy_cpu += run
        running[org] += 1
        heapq.heappush(events, (now + run, "done", (lane_id, org, item, cost, run, budget)))

    def dispatch():
        for lane_id in sorted(free):
            kind = free[lane_id]
            if kind == "fast":
                cands = [o for o in order if queues[o] and ewma[o] < F]
                cands.sort(key=lambda o: queues[o][0][0])  # oldest submission first
                if cands:
                    o = cands[0]
                    del free[lane_id]
                    start(lane_id, o, queues[o].pop(0), 2 * F)
                continue
            # Deficit round robin: the org at the pointer runs passes while it has deficit left
            # (debited after each pass by its CPU); then the pointer moves on and the next org
            # gets a quantum Q = B. An org with nothing queued loses its deficit (standard DRR).
            for k in range(2 * len(order) + 1):
                o = order[ptr[0]]
                eligible = queues[o] and running[o] < cap and not (fast and ewma[o] < F)
                if eligible and deficit[o] > 0:
                    del free[lane_id]
                    start(lane_id, o, queues[o].pop(0), B)
                    break
                if not queues[o]:
                    deficit[o] = 0.0
                ptr[0] = (ptr[0] + 1) % len(order)
                o2 = order[ptr[0]]
                if queues[o2]:
                    deficit[o2] += B

    dispatch()
    while events:
        now, kind, p = heapq.heappop(events)
        if now > horizon:
            break
        if kind == "arrive":
            org, cost = p
            queues[org].append((now, cost, True))
        else:
            lane_id, org, item, cost, run, budget = p
            running[org] -= 1
            deficit[org] -= run
            ewma[org] += (run - ewma[org]) / 4
            free[lane_id] = lanes[lane_id]
            if run < cost and budget < B:  # stopped at the fast lane's cap: re-queue on the DRR lanes
                queues[org].insert(0, (item[0], cost, False))
                ewma[org] = max(ewma[org], F)
        dispatch()
    waits.sort()
    return (statistics.median(waits) * 1e3, waits[int(0.99 * (len(waits) - 1))] * 1e3, waits[-1] * 1e3,
            heavy_cpu / (horizon * T))


if __name__ == "__main__":
    print("| T | Heavy orgs | Scheduler | Light-pass wait p50 | p99 | max | Heavy orgs' share of the scan threads |")
    print("|---|---|---|---|---|---|---|")
    for T in (2, 4):
        for H in (1, 2, 3, 7):
            for fast in (False, True):
                p50, p99, mx, share = simulate(T, H, fast)
                print(f"| {T} | {H} | {'rev 7: fast lane' if fast else 'rev 6: DRR only'} | {p50:.1f} ms | "
                      f"{p99:.1f} ms | {mx:.1f} ms | {share:.0%} |", flush=True)
