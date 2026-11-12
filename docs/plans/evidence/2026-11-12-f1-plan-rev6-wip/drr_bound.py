"""Plan 5.2.5: the DRR scan scheduler's latency bound, checked by a discrete-event simulation.

T scan threads; A active orgs other than tenant B, each with an endless backlog of passes; deficit
round robin with quantum Q = b_max, debited AFTER a pass with its measured CPU (so an org may run up
to Q + b_max in one round); an org holds at most max(1, T-1) threads. Tenant B submits one pass at a
random time. Reported: the worst time from B's submission until its pass STARTS, against the bound

    wait <= (T*b + (A)*(Q + b)) / T      (the passes running when B arrives, then one DRR round)

which for Q = b and T = 2 is (A + 1) * b, i.e. about (active orgs) x (per-pass budget).
Time unit: thread CPU of the passes (wall time when the scan threads have a core each).
"""
import heapq, random, sys

def simulate(T, A, b, Q, trials=3000, seed=1):
    R = random.Random(seed); worst = 0.0
    for _ in range(trials):
        cap = max(1, T - 1)
        orgs = list(range(A))
        deficit = {o: 0.0 for o in orgs}; running = {o: 0 for o in orgs}
        order = list(orgs); R.shuffle(order); ptr = 0
        free = T; events = []; now = 0.0
        b_arrival = R.uniform(0, 20 * b); b_started = None
        def cost():
            return b if R.random() < 0.7 else R.uniform(0, b)
        def dispatch():
            nonlocal free, ptr
            # B is an org too: inserted at the END of the order (worst case) once it has arrived
            while free > 0:
                cands = order + (["B"] if now >= b_arrival and b_started is None else [])
                picked = None
                for k in range(len(cands)):
                    o = cands[(ptr + k) % len(cands)]
                    if o == "B":
                        picked = o; break
                    if running[o] < cap:
                        if deficit[o] <= 0:
                            deficit[o] += Q
                        picked = o; break
                if picked is None:
                    return
                ptr = (cands.index(picked) + 1) % len(cands)
                if picked == "B":
                    return "B"
                c = cost(); running[picked] += 1; free -= 1
                heapq.heappush(events, (now + c, picked, c))
            return None
        while True:
            r = dispatch()
            if r == "B":
                b_started = now; break
            t_next = events[0][0] if events else float("inf")
            if b_started is None and b_arrival < t_next and now < b_arrival:
                now = b_arrival; continue
            t, o, c = heapq.heappop(events); now = t
            running[o] -= 1; free += 1; deficit[o] -= c
        worst = max(worst, b_started - b_arrival)
    return worst

if __name__ == "__main__":
    print("| Scan threads T | Active other orgs A | Per-pass budget b | Simulated worst wait | Bound (T*b + A*(Q+b))/T, Q = b | A x b |")
    print("|---|---|---|---|---|---|")
    for T in (2, 4):
        for A in (1, 3, 7, 15, 31):
            for label, b in (("100 ms (32 KiB, 128 KiB)", 0.1), ("400 ms (512 KiB)", 0.4)):
                w = simulate(T, A, b, Q=b, trials=600)
                bound = (T * b + A * 2 * b) / T
                print(f"| {T} | {A} | {label} | {w*1e3:.0f} ms | {bound*1e3:.0f} ms | {(A+1)*b*1e3:.0f} ms |")
