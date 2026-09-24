"""P06 (GW19/GW08): discrete-event model of the benchmarked admission path on the GPU unit, driving the REAL
LoadGate class (admit/overload.py) and the owner shed rule (detect/guard/owner.py) with the unit's logged bounds:
18 workers, input_cap=2, guard_queue_tokens=535/worker (share of 2 owners), owner cap = pool_size(GUARD) at the
owner's measured 243k tokens/s = 4,865 tokens, owner FIFO single server at guard-bench's 2.15 ms per 512-window,
HEADLINE input 100..1024 PG2 tokens (510-token payload, 446 stride), tokenization 3 us/token (guard-bench, G2 vCPU),
Poisson arrivals, each request lands on a uniformly random worker (SO_REUSEPORT over many keep-alive connections).
Optimistic for the prototype: worker event loops never contend, IPC = 0.1 ms, resolve/dispatch = 0.1 ms.
Output: fraction of requests answered 503 'overloaded' (shed) — counted as infra errors (budget 0.1%)."""
import heapq, math, random, sys
sys.path.insert(0, sys.argv[1])
from rvproto.admit.overload import LoadGate, Shed
from rvproto.runtime.metrics import Registry
W, OWNERS, INPUT_CAP, GUARD_CAP, OWNER_CAP = 18, 2, 2, 535, 4865
WIN_MS, TOK_US, IPC_MS, POST_MS = 2.15, 3.0, 0.1, 0.1
def windows(n):
    return 1 if n <= 510 else 1 + math.ceil((n - 510) / 446)
def sim(rate, seconds=600, seed=1):
    rnd = random.Random(seed); ev = []; t = 0.0; seq = 0
    gates = [LoadGate(Registry(i), inflight_cap=10**6) for i in range(W)]
    for g in gates: g.configure_guard(input_cap=INPUT_CAP, guard_cap_tokens=GUARD_CAP, tokens_per_s=26778.0)
    owner_q = [0] * OWNERS; owner_free = [0.0] * OWNERS
    shed = {"inflight": 0, "input_queue": 0, "guard_queue": 0, "owner": 0}; done = 0; n = 0
    while t < seconds:
        t += rnd.expovariate(rate); n += 1
        heapq.heappush(ev, (t, seq, "arrive", None)); seq += 1
    while ev:
        t, _, kind, st = heapq.heappop(ev)
        if kind == "arrive":
            w = rnd.randrange(W); g = gates[w]; tok = rnd.randint(100, 1024); k = windows(tok)
            ticket = g.enter()
            if g.enter_input(ticket) is not None:
                shed["input_queue"] += 1; g.leave(ticket); continue
            heapq.heappush(ev, (t + tok * TOK_US / 1e6, seq, "guard", (w, ticket, k))); seq += 1
        elif kind == "guard":
            w, ticket, k = st; g = gates[w]; cost = 512 * k
            if g.take_guard(cost) is not None:
                shed["guard_queue"] += 1; g.leave(ticket); continue
            o = w % OWNERS
            if owner_q[o] and owner_q[o] + cost > OWNER_CAP:
                shed["owner"] += 1; g.give_guard(cost); g.leave(ticket); continue
            owner_q[o] += cost
            start = max(t + IPC_MS / 1e3, owner_free[o]); fin = start + k * WIN_MS / 1e3; owner_free[o] = fin
            heapq.heappush(ev, (fin + IPC_MS / 1e3, seq, "back", (w, ticket, cost, o))); seq += 1
        elif kind == "back":
            w, ticket, cost, o = st; owner_q[o] -= cost; gates[w].give_guard(cost)
            heapq.heappush(ev, (t + POST_MS / 1e3, seq, "out", (w, ticket))); seq += 1
        else:
            w, ticket = st; gates[w].leave_input(ticket); gates[w].leave(ticket); done += 1
    total = sum(shed.values())
    gpu_util = rate * sum(windows(x) for x in range(100, 1025)) / 925 * WIN_MS / 1e3 / OWNERS
    return n, total, shed, gpu_util
print(f"{'offered RPS':>11} {'GPU util':>8} {'503 shed %':>10}  by bound")
for rate in (25, 50, 100, 150, 200, 300, 400):
    n, total, shed, u = sim(rate)
    print(f"{rate:>11} {100*u:>7.1f}% {100*total/n:>9.3f}%  {shed}")
