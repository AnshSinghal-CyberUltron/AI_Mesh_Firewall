"""F2 plan rev 2: discrete-event model of the GPU guard tier (plan §5.1, §5.6, §7).

Answers Reviewer A's A1 and Reviewer B's K2/K3 for the revised design. Standard library only,
fixed seeds, no model and no network. It is a MODEL, calibrated against measured L4 numbers;
every input is labelled in the JSON it writes.

What is modelled
- A request: tenant, posture (degrade | fail_closed), prompt tokens n, windows
  W = 1 if n <= 510 else 1 + ceil((n - 510) / 446)   (510-token payload, overlap 64).
- Gateway: tokenise (n * 1.7 us * 1.5 contention [M-sep C4 1.74 ms per 1,024 tokens; x1.5 is E]),
  pick an owner, send (one-way hop 0.25 ms + Exp(0.05 ms) [M-sep: TCP RR p50 84 / p99 126 us for
  16 KB round trip; app-level ~0.6 ms per call, runbook C42]), await the reply.
- Owner (one process per L4, runbook C7): serial batch-1 windows, service per window
  2.15 ms x LogNormal(cv 0.08) [M-sep: W1 2.15 ms p50; serial W2 4.69 = 2.35 per window;
  466 windows/s closed-loop saturation]. 2.15 ms reproduces the measured one-L4 curve with the
  measurement's own windowing (ceil(n/510)); a 2.30 ms run is kept as a sensitivity check.
- Scheduling policies compared:
  fifo   tenant-blind FIFO of windows (the RC1/C23 baseline);
  drr    per-tenant queues, round robin with a quantum of one window (deficit round robin with
         unit-cost windows), so a long request holds the GPU for one window at a time;
  edf    size-aware earliest-deadline-first over windows: a request's scheduling key is
         arrival + 10 ms + 2.6 ms x max(0, W - 3), so requests of up to 3 windows are served in
         arrival order and a long request yields to short ones in proportion to its own size.
         Fairness against floods comes from the per-tenant backlog cap plus the gateway's
         per-org rate limit, not from the queue order.
- Routing: random owner, or P2C (two random owners, pick the smaller reported backlog; reports
  refresh every 5 ms, a stand-in for backlog piggybacked on replies, runbook C43).
- Admission at the owner (both policies): predicted finish of this request's windows under the
  owner's policy vs the request's remaining deadline. degrade requests are admitted for the
  largest newest-first prefix of windows that fits (partial coverage, A3); fail_closed requests
  are admitted whole or shed. A shed is retried once on the other P2C choice. A degrade request
  is tokenised newest-first only up to the token budget its deadline allows
  (degrade_token_cap); the rest of a longer prompt is recorded as not scanned (partial). Before each window
  the owner drops windows that can no longer finish by the deadline (cancellation granularity is
  one window, about 2.3 ms).
- Optional per-tenant backlog cap at each owner (windows queued), shedding that tenant only.

Outputs per scenario: per class (victim degrade, victim fail_closed, attacker) the share fully
scanned, partially scanned, unscanned (forwarded), refused (503), and stage-latency p50/p99/max
(stage = tokenise -> findings back at the gateway).

Run: python -I guard_tier_sim.py [--out PATH]   (about 40-60 s of CPU on this host)
"""

from __future__ import annotations

import argparse
import heapq
import json
import math
import random
import statistics
import sys
import time
from collections import deque
from dataclasses import dataclass, field

PAYLOAD, OVERLAP, MAX_WINDOWS = 510, 64, 16
S_WIN_MS, S_CV = 2.15, 0.08  # calibrated: reproduces the measured one-L4 Poisson curve (see calibration)
EDF_SLOPE_MS = 2.6  # scheduling-deadline growth per window beyond 3 (size-aware EDF)
HOP_BASE_MS, HOP_EXP_MS = 0.25, 0.05
TOK_US_PER_TOKEN, TOK_CONTENTION = 1.7, 1.5
SNAP_MS = 5.0


def windows(n: int) -> int:
    return 1 if n <= PAYLOAD else 1 + math.ceil((n - PAYLOAD) / (PAYLOAD - OVERLAP))


def degrade_token_cap(d_ms: float, owners: int, s_ms: float, fanout: bool) -> int:
    """Newest-first tokenisation budget for a degrade request (plan §5.6): the largest n with
    tokenise(n) + round trip + ceil(W(n)/k) windows + 1 ms queue margin <= the deadline."""
    tok = TOK_US_PER_TOKEN * TOK_CONTENTION / 1000.0
    best = 0
    for n in range(1, 7201):
        k = owners if fanout else 1
        t = n * tok + 2 * (HOP_BASE_MS + HOP_EXP_MS) + math.ceil(windows(n) / k) * s_ms + 1.0
        if t <= d_ms:
            best = n
    return best


@dataclass
class Req:
    rid: int
    tenant: str
    posture: str
    n: int
    w: int
    t_arr: float
    deadline: float
    cls: str
    tried: list[int] = field(default_factory=list)
    admitted: int = 0
    done: int = 0
    cancelled: bool = False
    outcome: str = ""
    t_end: float = 0.0
    w_full: int = 0  # windows of the whole prompt (coverage denominator)
    parent: Req | None = None
    pending: int = 0  # fan-out: children not yet finalised


class Owner:
    def __init__(self, idx: int, sched: str, tenant_cap: int | None, s_mean: float,
                 quantum: int = 1) -> None:
        self.idx, self.sched, self.cap, self.s, self.quantum = idx, sched, tenant_cap, s_mean, quantum
        self.cur: str | None = None  # drr: tenant holding the current quantum
        self.left = 0
        self.fifo: deque[Req] = deque()  # one entry per admitted window (fifo)
        self.q: dict[str, deque[Req]] = {}  # per tenant (drr)
        self.rr: deque[str] = deque()
        self.heap: list[tuple[float, int, Req]] = []  # edf
        self.seq = 0
        self.busy_until = 0.0
        self.busy = False
        self.queued = 0
        self.per_tenant: dict[str, int] = {}
        self.busy_ms = 0.0
        self._key = 0.0

    def backlog(self) -> int:
        return self.queued + (1 if self.busy else 0)

    def predicted_finish(self, now: float, tenant: str, k: int) -> float:
        """Predicted finish time of the k-th window this tenant would add now."""
        resid = max(0.0, self.busy_until - now) if self.busy else 0.0
        if self.sched == "fifo":
            return now + resid + (self.queued + k) * self.s
        if self.sched == "edf":
            key = self._key
            ahead = sum(1 for kk, _, r in self.heap if kk <= key and not r.cancelled)
            return now + resid + (ahead + k) * self.s
        own = self.per_tenant.get(tenant, 0) + k
        q = self.quantum
        rounds = math.ceil(own / q)
        ahead = sum(min(b, rounds * q) for t, b in self.per_tenant.items() if t != tenant and b > 0)
        return now + resid + (ahead + own) * self.s

    def enqueue(self, req: Req, k: int) -> None:
        self.queued += k
        self.per_tenant[req.tenant] = self.per_tenant.get(req.tenant, 0) + k
        if self.sched == "fifo":
            self.fifo.extend([req] * k)
        elif self.sched == "edf":
            key = sched_key(req)
            for _ in range(k):
                self.seq += 1
                heapq.heappush(self.heap, (key, self.seq, req))
        else:
            dq = self.q.setdefault(req.tenant, deque())
            if not dq and req.tenant != self.cur:
                self.rr.append(req.tenant)
            dq.extend([req] * k)

    def pop(self) -> Req | None:
        if self.sched == "fifo":
            if not self.fifo:
                return None
            req = self.fifo.popleft()
        elif self.sched == "edf":
            if not self.heap:
                return None
            req = heapq.heappop(self.heap)[2]
        else:
            if self.cur is not None and self.left > 0 and self.q.get(self.cur):
                t = self.cur
            else:
                if self.cur is not None and self.q.get(self.cur):
                    self.rr.append(self.cur)
                if not self.rr:
                    self.cur = None
                    return None
                t = self.rr.popleft()
                self.cur, self.left = t, self.quantum
            dq = self.q[t]
            req = dq.popleft()
            self.left -= 1
            if not dq:
                self.cur, self.left = None, 0
        self.queued -= 1
        self.per_tenant[req.tenant] -= 1
        if self.per_tenant[req.tenant] == 0:
            del self.per_tenant[req.tenant]
        return req


def sched_key(r: Req) -> float:
    return r.t_arr + 10.0 + EDF_SLOPE_MS * max(0, r.w - 3)


def pct(xs: list[float], p: float) -> float:
    if not xs:
        return float("nan")
    xs = sorted(xs)
    i = min(len(xs) - 1, max(0, math.ceil(p / 100.0 * len(xs)) - 1))
    return round(xs[i], 3)


def run(cfg: dict, seed: int) -> dict:
    rnd = random.Random(seed)
    s_mean = cfg.get("s_win_ms", S_WIN_MS)
    sigma = math.sqrt(math.log(1 + S_CV**2))
    mu = math.log(s_mean) - sigma**2 / 2

    def svc() -> float:
        return rnd.lognormvariate(mu, sigma)

    def hop() -> float:
        return HOP_BASE_MS + rnd.expovariate(1.0 / HOP_EXP_MS)

    owners = [Owner(i, cfg["sched"], cfg.get("tenant_cap"), s_mean, cfg.get("quantum", 1))
              for i in range(cfg["owners"])]
    snap = [0] * len(owners)
    ev: list[tuple[float, int, str, object]] = []
    seq = 0

    def push(t: float, kind: str, obj: object) -> None:
        nonlocal seq
        seq += 1
        heapq.heappush(ev, (t, seq, kind, obj))

    # Arrivals: background tenants (Poisson split) + optional attacker
    dur = cfg["duration_ms"]
    deg_cap = degrade_token_cap(cfg["d_deg_ms"], cfg["owners"], s_mean, bool(cfg.get("fanout")))
    rid = 0
    reqs: list[Req] = []
    bg_rate = cfg["rps"] / 1000.0  # per ms
    t = 0.0
    n_t = cfg["tenants"]
    while True:
        t += rnd.expovariate(bg_rate)
        if t > dur:
            break
        idx = rnd.randrange(n_t)
        ten = f"t{idx}"
        fc = idx < round(cfg["fail_closed_share"] * n_t)
        if rnd.random() < cfg.get("long_share", 0.0):
            n = rnd.randint(1025, 7200)
        else:
            n = rnd.randint(cfg["tok_lo"], cfg["tok_hi"])
        posture = "fail_closed" if fc else "degrade"
        d = cfg["d_fc_ms"] if fc else cfg["d_deg_ms"]
        n_scan = n if fc else min(n, deg_cap)
        reqs.append(Req(rid, ten, posture, n_scan, windows(n_scan), t, t + d, "victim_" + posture,
                        w_full=windows(n)))
        rid += 1
    if cfg.get("atk_rps", 0) > 0:
        t = 0.0
        while True:
            t += rnd.expovariate(cfg["atk_rps"] / 1000.0)
            if t > dur:
                break
            n = cfg.get("atk_tokens", 7200)
            reqs.append(Req(rid, "atk", "fail_closed", n, windows(n), t, t + cfg["d_fc_ms"], "attacker",
                            w_full=windows(n)))
            rid += 1
    for r in reqs:
        push(r.t_arr + r.n * TOK_US_PER_TOKEN * TOK_CONTENTION / 1000.0, "send", r)
    t_snap = 0.0
    while t_snap <= dur + 1000:
        push(t_snap, "snap", None)
        t_snap += SNAP_MS

    def choose(r: Req) -> int | None:
        cands = [i for i in range(len(owners)) if i not in r.tried]
        if not cands:
            return None
        if cfg["route"] == "random" or len(cands) == 1:
            return rnd.choice(cands)
        a, b = rnd.sample(cands, 2)
        return a if snap[a] <= snap[b] else b

    def finalize(r: Req, now: float) -> None:
        r.t_end = now
        if r.done >= r.w and r.w >= max(r.w_full, 1):
            r.outcome = "full"
        elif r.posture == "degrade":
            r.outcome = "partial" if r.done > 0 else "unscanned"
        else:
            r.outcome = "refused_503"
        p = r.parent
        if p is not None:
            p.done += min(r.done, r.w)
            p.pending -= 1
            if p.pending == 0:
                finalize(p, now)

    def start_next(o: Owner, now: float) -> None:
        while True:
            req = o.pop()
            if req is None:
                o.busy = False
                return
            if req.cancelled:
                continue
            if now + s_mean > req.deadline - HOP_BASE_MS:  # cannot finish this window in time
                req.cancelled = True
                push(now + hop(), "reply", req)
                continue
            s = svc()
            o.busy, o.busy_until = True, now + s
            o.busy_ms += s
            push(now + s, "win_done", (o, req))
            return

    max_w = cfg.get("max_windows", MAX_WINDOWS)
    while ev:
        now, _, kind, obj = heapq.heappop(ev)
        if kind == "snap":
            for i, o in enumerate(owners):
                snap[i] = o.backlog()
        elif kind == "send":
            r = obj  # type: ignore[assignment]
            if r.w > max_w:
                r.outcome = "refused_413" if r.posture == "fail_closed" else "unscanned"
                r.t_end = now
                continue
            if cfg.get("fanout") and r.w > 1 and r.parent is None:
                # one sub-request per window, each routed on its own (P2C); the parent completes
                # when every child has completed. Children carry the parent's tenant, posture and
                # deadline. Distinct owners are preferred while there are enough.
                kids = []
                for _ in range(r.w):
                    c = Req(-1, r.tenant, r.posture, 0, 1, r.t_arr, r.deadline, "_child", parent=r)
                    kids.append(c)
                r.pending = len(kids)
                used: list[int] = []
                for c in kids:
                    c.tried = list(used) if len(used) < len(owners) - 1 else []
                    i = choose(c)
                    if i is None:
                        c.tried = []
                        i = choose(c)
                    c.tried = [i]
                    used.append(i)
                    push(now + hop(), "rx", (owners[i], c))
                continue
            i = choose(r)
            if i is None:
                finalize(r, now)
                continue
            r.tried.append(i)
            push(now + hop(), "rx", (owners[i], r))
        elif kind == "rx":
            o, r = obj  # type: ignore[misc]
            # tenant backlog cap
            if o.cap is not None and o.per_tenant.get(r.tenant, 0) + 1 > o.cap:
                k = 0
            else:
                limit = r.w if o.cap is None else min(r.w, o.cap - o.per_tenant.get(r.tenant, 0))
                k = 0
                o._key = sched_key(r)
                for j in range(limit, 0, -1):
                    if o.predicted_finish(now, r.tenant, j) <= r.deadline - HOP_BASE_MS:
                        k = j
                        break
            if r.posture == "fail_closed" and k < r.w:
                k = 0
            if k == 0:
                push(now + hop(), "shed", r)
                continue
            r.admitted = k
            o.enqueue(r, k)
            if not o.busy:
                start_next(o, now)
        elif kind == "shed":
            r = obj  # type: ignore[assignment]
            if len(r.tried) < 2 and now + 2 * HOP_BASE_MS + s_mean < r.deadline:
                i = choose(r)
                if i is not None:
                    r.tried.append(i)
                    push(now + hop(), "rx", (owners[i], r))
                    continue
            finalize(r, now)
        elif kind == "win_done":
            o, r = obj  # type: ignore[misc]
            r.done += 1
            if r.done >= r.admitted and not r.cancelled:
                push(now + hop(), "reply", r)
            start_next(o, now)
        elif kind == "reply":
            r = obj  # type: ignore[assignment]
            if not r.outcome:
                finalize(r, now)

    warm = cfg.get("warmup_ms", 2000.0)
    out: dict[str, dict] = {}
    for cls in sorted({r.cls for r in reqs}):
        rs = [r for r in reqs if r.cls == cls and r.t_arr >= warm]
        if not rs:
            continue
        lat = [r.t_end - r.t_arr for r in rs]
        cnt = {k: sum(1 for r in rs if r.outcome == k) for k in
               ("full", "partial", "unscanned", "refused_503", "refused_413")}
        out[cls] = {
            "n": len(rs),
            **{f"share_{k}": round(v / len(rs), 5) for k, v in cnt.items()},
            "stage_ms_p50": pct(lat, 50), "stage_ms_p99": pct(lat, 99), "stage_ms_max": pct(lat, 100),
            "stage_ms_p99_full_only": pct([r.t_end - r.t_arr for r in rs if r.outcome == "full"], 99),
            "coverage_mean": round(statistics.mean(min(r.done, r.w) / max(r.w_full, r.w, 1) for r in rs), 5),
        }
    busy = [round(o.busy_ms / dur, 4) for o in owners]
    out["_gpu_utilisation"] = {"per_owner": busy, "mean": round(statistics.mean(busy), 4)}
    out["_degrade_token_cap"] = deg_cap
    return out


def calibrate(seed: int, wps: float, s_ms: float = S_WIN_MS, dur_ms: float = 300_000.0) -> dict:
    """One owner, FIFO, no hop or tokeniser, headline mix: p99 of queue + service (the measured
    'guard stage + its queue' quantity). Windows as the measurement made them: ceil(n / 510)."""
    rnd = random.Random(seed)
    sigma = math.sqrt(math.log(1 + S_CV**2))
    mu = math.log(s_ms) - sigma**2 / 2

    def wmeas(n: int) -> int:
        return math.ceil(n / PAYLOAD)

    mean_w = statistics.mean(wmeas(n) for n in range(100, 1025))
    lam = wps / mean_w / 1000.0
    t, free, lat = 0.0, 0.0, []
    while t < dur_ms:
        t += rnd.expovariate(lam)
        w = wmeas(rnd.randint(100, 1024))
        start = max(t, free)
        s = sum(rnd.lognormvariate(mu, sigma) for _ in range(w))
        free = start + s
        lat.append(free - t)
    return {"window_ms": s_ms, "offered_windows_per_s": wps, "mean_windows": round(mean_w, 3),
            "p50": pct(lat, 50), "p99": pct(lat, 99), "utilisation": round(wps * s_ms / 1000, 3)}


BASE = {"owners": 4, "tenants": 50, "fail_closed_share": 0.5, "tok_lo": 100, "tok_hi": 1024,
        "d_deg_ms": 12.0, "d_fc_ms": 250.0, "duration_ms": 60_000.0, "warmup_ms": 2_000.0}
# Plan §5.6: window-granular DRR (quantum 1), per-tenant cap of 32 queued windows per owner, P2C
# routing on reported backlog, and fan-out of a request's windows across owners.
PROPOSED = {"sched": "drr", "quantum": 1, "route": "p2c", "tenant_cap": 32, "fanout": True}
DRR_NO_FANOUT = {"sched": "drr", "quantum": 1, "route": "p2c", "tenant_cap": 32, "fanout": False}
EDF_FANOUT = {"sched": "edf", "route": "p2c", "tenant_cap": 32, "fanout": True}
BASELINE = {"sched": "fifo", "route": "random", "tenant_cap": None, "fanout": False}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    t0 = time.process_time()
    res: dict[str, object] = {
        "inputs": {
            "window_service_ms": [S_WIN_MS, f"lognormal cv {S_CV}",
                                  "[M-sep] L4 TRT fp16 exact-shape: W1 2.15 p50 / 2.20 p99; serial 2.35/window; 466 w/s"],
            "hop_one_way_ms": [HOP_BASE_MS, f"+Exp({HOP_EXP_MS})",
                               "[M-sep] TCP RR 16 KB p50 84 / p99 126 us; app-level ~0.6 ms per call (C42)"],
            "tokenise_us_per_token": [TOK_US_PER_TOKEN, f"x{TOK_CONTENTION} contention [E]", "[M-sep] C4 1.74 ms / 1,024 tokens"],
            "windowing": "payload 510, overlap 64, max 16 windows (7,200 tokens)",
            "deadlines_ms": {"degrade": BASE["d_deg_ms"], "fail_closed": BASE["d_fc_ms"]},
            "headline_mix": "tokens U[100,1024] (runbook HARNESS_SPEC §4), mean windows 1.63",
            "routing_snapshot_ms": SNAP_MS,
        },
        "calibration_one_owner_fifo": {
            "measured_[M-sep]": "guard stage + its queue, Poisson, headline mix, one L4: p99<=10 ms at 122 w/s (3x300 s); "
                                "p99<=20 ms at ~284 w/s (ladder only; ladders overstated the knee ~30%); saturation 466 w/s",
            "model": [calibrate(11, w) for w in (122, 162, 203, 284)],
            "model_sensitivity_2.30ms": [calibrate(11, w, 2.30) for w in (122, 162, 284)],
        },
    }
    scen: dict[str, object] = {}
    # (1) A1/K2: load ladder for the proposed design (50/50 postures), plus all-fail_closed (Q7)
    for per_l4 in (50, 75, 100, 125, 150):
        scen[f"ladder/proposed/mixed/{per_l4}_rps_per_l4"] = run({**BASE, **PROPOSED, "rps": 4 * per_l4}, 101)
    for per_l4 in (100, 150):
        scen[f"ladder/proposed/all_fail_closed/{per_l4}_rps_per_l4"] = run(
            {**BASE, **PROPOSED, "fail_closed_share": 1.0, "rps": 4 * per_l4}, 101)
    for sd in (202, 303):
        scen[f"ladder/proposed/mixed/100_rps_per_l4/seed{sd}"] = run({**BASE, **PROPOSED, "rps": 400}, sd)
    scen["sensitivity/window_2.30ms/proposed/mixed/100_rps_per_l4"] = run(
        {**BASE, **PROPOSED, "rps": 400, "s_win_ms": 2.30}, 101)
    scen["sensitivity/degrade_deadline_10ms/proposed/mixed/100_rps_per_l4"] = run(
        {**BASE, **PROPOSED, "rps": 400, "d_deg_ms": 10.0}, 101)
    # (2) policy contrast at the planning point
    for name, pol in (("baseline_fifo_random", BASELINE), ("drr_no_fanout", DRR_NO_FANOUT),
                      ("edf_fanout", EDF_FANOUT)):
        scen[f"contrast/{name}/mixed/100_rps_per_l4"] = run({**BASE, **pol, "rps": 400}, 101)
    # (3) K3: one tenant sends fail_closed 16-window (7,200-token) prompts on top of 300 RPS of
    #     victims from 50 tenants (half degrade, half fail_closed)
    for atk in (0, 10, 20, 40, 80):
        for name, pol in (("proposed", PROPOSED), ("drr_no_fanout", DRR_NO_FANOUT),
                          ("edf_fanout", EDF_FANOUT), ("baseline_fifo_random", BASELINE)):
            scen[f"k3/{name}/attacker_{atk}_rps"] = run({**BASE, **pol, "rps": 300, "atk_rps": atk}, 404)
    # (4) N+1: one of four guards lost at the 400 RPS planning load
    scen["n_plus_1/proposed/3_owners_400_rps"] = run({**BASE, **PROPOSED, "owners": 3, "rps": 400}, 505)
    # (5) long-prompt tail: 10% of requests 1,025-7,200 tokens at 100 RPS per L4
    scen["long_tail_10pct/proposed/100_rps_per_l4"] = run(
        {**BASE, **PROPOSED, "rps": 400, "long_share": 0.10}, 606)
    res["scenarios"] = scen
    res["cpu_seconds"] = round(time.process_time() - t0, 1)
    text = json.dumps(res, indent=1, sort_keys=False)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    else:
        sys.stdout.write(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
