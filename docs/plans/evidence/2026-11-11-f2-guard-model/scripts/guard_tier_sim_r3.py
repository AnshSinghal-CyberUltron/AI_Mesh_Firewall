"""F2 plan rev 3: the guard-tier model extended with the rev-2 reviewers' failure modes and the
rev-3 mitigations (plan §5.5, §5.5a, §5.7). Standard library only, fixed seeds, no weights.

It imports the rev-2 model (`guard_tier_sim.py`, unchanged) and re-states its `run()` as `run3()`
with OPTIONAL extensions. With every extension off, `run3()` reproduces `run()` exactly; `main()`
checks that first on the planning point and on the K3 scenario and refuses to report otherwise.
Extensions use their own RNG, so the rev-2 random sequence is untouched when they are off.

Reviewer workloads reproduced here (Reviewer A rev-2 review: S1-S5, S7, S8, N1; Reviewer B rev-2
review: B2, B3, N1, N7, S7):
  fault=(kind, owner, t_ms)  kind "error": from t_ms the owner answers every frame with `error`
                             after 0.1 ms (sticky CUDA / TensorRT error after readiness);
                             kind "blackhole": the owner stops answering (hung VM, partition,
                             host maintenance without a drain); frames and queued windows vanish.
  n_sybil, atk_posture      the attacker's requests spread round-robin over n_sybil tenant keys.
  arrivals="batch"          compound Poisson: Geometric(batch_mean) same-tenant requests at once.
  arrivals="mmpp"           rate alternates (1 +/- mmpp_a) x with exponential dwell.
  s_win_ms, s_cv, tail_p, tail_mult, pred_ms, tok_factor: service-time and tokeniser perturbations.

Rev-3 mitigations (each a switch; PROPOSED3 turns all on):
  classes=True       owner serves `degrade`-class windows (short deadline) before `fail_closed`-class
                     windows, with a minimum share of 1 in `fc_every` dispatches for the
                     fail_closed class when both are backlogged; per-key DRR (quantum 1 window)
                     inside each class; the per-key cap of 32 queued windows is kept.
  size_classes=True  the fail_closed class is split by request size: FS (<= 3 windows, the
                     headline band) before FL (longer); guard slots alternate FL / FS, so a flood
                     of 16-window requests from any number of keys cannot delay short requests.
  pool_sybil=True    trial / unverified orgs share ONE scheduling key (signed control state), so N
                     Sybil trial orgs are one DRR key and one 32-window cap.
  win_budget=(r, b)  per-org GPU-window budget at the gateway (token bucket, windows/s, burst);
                     debited when windows are formed, never refunded; over budget: fail_closed
                     -> 429, degrade -> forwarded and recorded (policy).
  views="per_worker" each of `gw_workers` gateway workers keeps its own owner view, refreshed only
                     by the replies and sheds it receives (backlog piggybacked) and a heartbeat
                     every hb_ms (rev 2 modelled a 5 ms global snapshot of true backlogs).
  local_inflight     P2C scores an owner by (piggybacked backlog + this worker's own windows in
                     flight to it), so a black hole's score grows with every unanswered frame.
  eject=(k, ms)      outlier ejection per worker: k consecutive failures (error reply or attempt
                     timeout) eject the owner for ms, doubling up to 30 s; a success resets it.
  ack_timeout_ms     the owner acknowledges every admitted frame at once with its predicted finish
                     time. No ack (or shed, or reply) within ack_timeout_ms means the owner is
                     unresponsive; an ack sets a completion timeout at the predicted finish plus
                     slack. Either timeout counts as a failure for ejection, sends a cancel frame
                     and, with retry=True, retries the window once on another usable owner when
                     the deadline still allows it. Error replies are retried the same way. A busy
                     owner therefore never looks dead: it acks with a long prediction.
  gw_timer           the gateway finalises every request at its own deadline (degrade: partial or
                     unscanned; fail_closed: 503) and sends cancel frames for its outstanding
                     windows (Reviewer B N7, Reviewer A N9). Rev 2 had no gateway-side timer.
  newest_retry       a shed newest window (index 0) is retried once more, taking the owner slot of
                     the request's oldest still-queued window, which is cancelled (Reviewer A S7).
  tok_lane=(T, C)    per gateway worker: T dedicated tokenizer threads, newest-first chunks of C
                     tokens, per-org DRR over chunks, an org uses at most max(1, T - 1) threads
                     (rev-2 model: tokenisation was a pure delay; Reviewer A S5, Reviewer B B2b).
  tok_fifo=T         contrast: T threads per worker, whole-request jobs, FIFO (Reviewer B's case).

Outputs per class: shares (full, partial, unscanned, refused_503, refused_413, refused_429,
error), the split of `degrade` U2 into capacity (sheds, timeouts, owner errors, windows dropped at
the deadline) and policy (token cap, over_length, org budget), latency p50/p99/max, the share of
multi-window `degrade` requests whose NEWEST window went unscanned, GPU utilisation, retries,
ejections and windows that ran after the gateway had given up on them.

Run: python -I guard_tier_sim_r3.py [--group all|selftest|design|stress|fail|k3|long] [--out PATH]
"""

from __future__ import annotations

import argparse
import heapq
import importlib.util
import json
import math
import random
import statistics
import sys
import time
from collections import deque
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("guard_tier_sim", _HERE / "guard_tier_sim.py")
g = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
sys.modules["guard_tier_sim"] = g
_spec.loader.exec_module(g)  # type: ignore[union-attr]

Req, Owner, windows, pct = g.Req, g.Owner, g.windows, g.pct
HOP_BASE_MS, HOP_EXP_MS = g.HOP_BASE_MS, g.HOP_EXP_MS
TOK_US = g.TOK_US_PER_TOKEN
INF = float("inf")


SHORT_W = 3  # windows of a headline-band request (<= 1,024 tokens)


def wclass(r: Req) -> str:
    """Owner class of a frame, set by the gateway: D = degrade (short deadline); FS = fail_closed
    request of at most SHORT_W windows; FL = longer fail_closed request."""
    if r.posture == "degrade":
        return "D"
    p = r.parent or r
    return "FS" if p.w <= SHORT_W else "FL"


class Owner3:
    """Class-priority owner. Two classes: degrade first, fail_closed at least 1 in fc_every
    dispatches. Three classes (size_classes): D > FS > FL, and every fc_every-th dispatch is a
    guard slot that alternates between FL and FS, so neither starves. Per-key DRR (the rev-2
    Owner, quantum one window) inside each class; the per-key cap counts all classes."""

    def __init__(self, idx: int, tenant_cap: int | None, s_mean: float, fc_every: int,
                 size_classes: bool = False) -> None:
        self.idx, self.cap, self.s, self.m = idx, tenant_cap, s_mean, fc_every
        self.size = size_classes
        self.D = Owner(idx, "drr", None, s_mean, 1)
        self.F = Owner(idx, "drr", None, s_mean, 1)
        self.FL = Owner(idx, "drr", None, s_mean, 1)
        self.guard = 0
        self.busy, self.busy_until, self.busy_ms = False, 0.0, 0.0
        self.queued = 0
        self.per_tenant: dict[str, int] = {}
        self.disp = 0
        self._key = 0.0

    def backlog(self) -> int:
        return self.queued + (1 if self.busy else 0)

    def sub(self, r: Req) -> Owner:
        c = wclass(r)
        if c == "D":
            return self.D
        return self.FL if (self.size and c == "FL") else self.F

    def predict(self, now: float, r: Req, k: int) -> float:
        resid = max(0.0, self.busy_until - now) if self.busy else 0.0
        q = self.sub(r)
        if q is self.D:
            ahead = self.D.predicted_finish(now, r.sk, k) - now
            if self.F.queued + self.FL.queued > 0:
                ahead *= self.m / (self.m - 1)
            return now + resid + ahead
        before = self.D.queued + (self.F.queued if q is self.FL else 0)
        return now + resid + before * self.s + (q.predicted_finish(now, r.sk, k) - now)

    def enqueue(self, r: Req, k: int) -> None:
        self.queued += k
        self.per_tenant[r.sk] = self.per_tenant.get(r.sk, 0) + k
        self.sub(r).enqueue(r, k)

    def pop(self) -> Req | None:
        self.disp += 1
        r = None
        if self.disp % self.m == 0 and self.F.queued + self.FL.queued > 0:
            self.guard ^= 1
            for c in ((self.FL, self.F) if self.guard else (self.F, self.FL)):
                if c.queued:
                    r = c.pop()
                    break
        if r is None:
            for c in (self.D, self.F, self.FL):
                if c.queued:
                    r = c.pop()
                    break
        if r is None:
            return None
        self.queued -= 1
        self.per_tenant[r.sk] -= 1
        if self.per_tenant[r.sk] == 0:
            del self.per_tenant[r.sk]
        return r


def _rm_drr(o: Owner, a: Req) -> int:
    """Remove every queued copy of attempt `a` (by identity: Req is a dataclass, so `in` and
    `remove` would compare field values) from a rev-2 DRR owner."""
    dq = o.q.get(a.tenant)
    if not dq:
        return 0
    keep = deque(x for x in dq if x is not a)
    n = len(dq) - len(keep)
    if n:
        o.queued -= n
        o.per_tenant[a.tenant] -= n
        if o.per_tenant[a.tenant] == 0:
            del o.per_tenant[a.tenant]
        if keep:
            o.q[a.tenant] = keep
        else:
            del o.q[a.tenant]
            if a.tenant in o.rr:
                o.rr.remove(a.tenant)
            if o.cur == a.tenant:
                o.cur, o.left = None, 0
    return n


def remove_queued(o, a: Req) -> int:
    """A cancel frame drops the attempt's queued windows (plan §5.5, A8)."""
    if isinstance(o, Owner3):
        n = _rm_drr(o.sub(a), a)
        if n:
            o.queued -= n
            o.per_tenant[a.tenant] -= n
            if o.per_tenant[a.tenant] == 0:
                del o.per_tenant[a.tenant]
        return n
    if o.sched == "drr":
        return _rm_drr(o, a)
    return 0


def run3(cfg: dict, seed: int) -> dict:  # one event loop, kept in one place
    rnd = random.Random(seed)
    xr = random.Random(seed * 7919 + 13)  # extensions only
    s_mean = cfg.get("s_win_ms", g.S_WIN_MS)
    s_cv = cfg.get("s_cv", g.S_CV)
    pred = cfg.get("pred_ms", s_mean)
    tail_p, tail_mult = cfg.get("tail_p", 0.0), cfg.get("tail_mult", 1.0)
    sigma = math.sqrt(math.log(1 + s_cv**2))
    mu = math.log(s_mean) - sigma**2 / 2
    tokf = cfg.get("tok_factor", g.TOK_CONTENTION)
    classes = bool(cfg.get("classes"))
    per_worker = cfg.get("views") == "per_worker"
    n_workers = cfg.get("gw_workers", 32)
    hb_ms = cfg.get("hb_ms", 1000.0)
    local_inflight = bool(cfg.get("local_inflight"))
    eject = cfg.get("eject")
    ack_to = cfg.get("ack_timeout_ms")
    retry_on = bool(cfg.get("retry"))
    gw_timer = bool(cfg.get("gw_timer"))
    newest_retry = bool(cfg.get("newest_retry"))
    tok_lane = cfg.get("tok_lane")
    tok_fifo = cfg.get("tok_fifo")
    budget = cfg.get("win_budget")
    fault = cfg.get("fault")
    f_kind, f_owner, f_t = fault if fault else ("", -1, INF)
    any_ext = any((classes, per_worker, local_inflight, eject, ack_to, retry_on, gw_timer,
                   newest_retry, tok_lane, tok_fifo, budget, fault))

    def svc() -> float:
        x = rnd.lognormvariate(mu, sigma)
        if tail_p and xr.random() < tail_p:
            x *= tail_mult
        return x

    def hop() -> float:
        return HOP_BASE_MS + rnd.expovariate(1.0 / HOP_EXP_MS)

    n_own = cfg["owners"]
    if classes:
        owners: list = [Owner3(i, cfg.get("tenant_cap"), pred, cfg.get("fc_every", 4),
                               bool(cfg.get("size_classes"))) for i in range(n_own)]
    else:
        owners = [Owner(i, cfg["sched"], cfg.get("tenant_cap"), pred, cfg.get("quantum", 1))
                  for i in range(n_own)]
    snap = [0] * n_own
    view = [[0] * n_own for _ in range(n_workers)]
    inflight = [[0] * n_own for _ in range(n_workers)]
    fails = [[0] * n_own for _ in range(n_workers)]
    ej_until = [[0.0] * n_own for _ in range(n_workers)]
    ej_period = [[(eject[1] if eject else 0.0)] * n_own for _ in range(n_workers)]
    stats = {"retries": 0, "ejections": 0, "wasted_windows": 0, "cancel_frames": 0}
    ev: list = []
    seq = 0

    def push(t: float, kind: str, obj: object) -> None:
        nonlocal seq
        seq += 1
        heapq.heappush(ev, (t, seq, kind, obj))

    dur = cfg["duration_ms"]
    deg_cap = g.degrade_token_cap(cfg["d_deg_ms"], n_own, pred, bool(cfg.get("fanout")))
    if tokf != g.TOK_CONTENTION:
        tk = TOK_US * tokf / 1000.0
        deg_cap = max([n for n in range(1, 7201) if n * tk + 2 * (HOP_BASE_MS + HOP_EXP_MS)
                       + math.ceil(windows(n) / (n_own if cfg.get("fanout") else 1)) * pred + 1.0
                       <= cfg["d_deg_ms"]] or [0])
    rid = 0
    reqs: list[Req] = []
    n_t = cfg["tenants"]
    lam = cfg["rps"] / 1000.0

    def make(t: float, idx: int) -> None:
        nonlocal rid
        ten = f"t{idx}"
        fc = idx < round(cfg["fail_closed_share"] * n_t)
        if rnd.random() < cfg.get("long_share", 0.0):
            n = rnd.randint(1025, 7200)
        else:
            n = rnd.randint(cfg["tok_lo"], cfg["tok_hi"])
        posture = "fail_closed" if fc else "degrade"
        d = cfg["d_fc_ms"] if fc else cfg["d_deg_ms"]
        n_scan = n if fc else min(n, deg_cap)
        r = Req(rid, ten, posture, n_scan, windows(n_scan), t, t + d, "victim_" + posture,
                w_full=windows(n))
        r.n_full = n  # type: ignore[attr-defined]
        reqs.append(r)
        rid += 1

    arr = cfg.get("arrivals", "poisson")
    t = 0.0
    if arr == "poisson":
        while True:
            t += rnd.expovariate(lam)
            if t > dur:
                break
            make(t, rnd.randrange(n_t))
    elif arr == "batch":
        b = cfg.get("batch_mean", 4.0)
        while True:
            t += xr.expovariate(lam / b)
            if t > dur:
                break
            idx = xr.randrange(n_t)
            k = 1
            while xr.random() > 1.0 / b:
                k += 1
            for j in range(k):
                make(t + 0.05 * j, idx)
    elif arr == "mmpp":
        a, dwell = cfg.get("mmpp_a", 0.5), cfg.get("mmpp_dwell_ms", 1000.0)
        hi, t_sw = True, xr.expovariate(1.0 / dwell)
        while True:
            t += xr.expovariate(lam * ((1 + a) if hi else (1 - a)))
            while t > t_sw:
                t, hi = t_sw, not hi
                t_sw = t + xr.expovariate(1.0 / dwell)
                t += xr.expovariate(lam * ((1 + a) if hi else (1 - a)))
            if t > dur:
                break
            make(t, xr.randrange(n_t))
    if cfg.get("atk_rps", 0) > 0:
        t = 0.0
        ns = cfg.get("n_sybil", 1)
        k_s = 0
        apost = cfg.get("atk_posture", "fail_closed")
        while True:
            t += rnd.expovariate(cfg["atk_rps"] / 1000.0)
            if t > dur:
                break
            n = cfg.get("atk_tokens", 7200)
            if apost == "degrade":
                n = min(n, deg_cap)
            ten = "atk" if ns == 1 else f"atk{k_s % ns}"
            k_s += 1
            d = cfg["d_fc_ms"] if apost == "fail_closed" else cfg["d_deg_ms"]
            r = Req(rid, ten, apost, n, windows(n), t, t + d, "attacker", w_full=windows(n))
            r.n_full = n  # type: ignore[attr-defined]
            reqs.append(r)
            rid += 1
    pool = bool(cfg.get("pool_sybil"))
    buckets: dict[str, list[float]] = {}
    for r in reqs:
        r.org = r.tenant  # type: ignore[attr-defined]  # the org: budgets are per org
        r.sk = "pool" if (pool and r.tenant.startswith("atk")) else r.tenant  # type: ignore[attr-defined]
        r.tenant = r.sk  # owners schedule on the key signed control state assigns
        r.worker = xr.randrange(n_workers) if any_ext else 0  # type: ignore[attr-defined]
        r.newest_done = False  # type: ignore[attr-defined]
        r.kids = []  # type: ignore[attr-defined]
        r.policy = ""  # type: ignore[attr-defined]

    def tok_cost(r: Req) -> float:
        return r.n * TOK_US * tokf / 1000.0
    gw: list[dict] = []
    if tok_lane or tok_fifo:
        T = tok_lane[0] if tok_lane else tok_fifo
        gw = [{"free": T, "rr": deque(), "q": {}, "running": {}} for _ in range(n_workers)]
        for r in reqs:
            r.tok_left = r.n  # type: ignore[attr-defined]
            push(r.t_arr, "tok_arrive", r)
    else:
        for r in reqs:
            push(r.t_arr + tok_cost(r), "send", r)
    if gw_timer:
        for r in reqs:
            push(r.deadline, "gw_deadline", r)
    if per_worker:
        for w in range(n_workers):
            ts = xr.uniform(0, hb_ms)
            while ts <= dur + 1000:
                push(ts, "hb", w)
                ts += hb_ms
    else:
        t_snap = 0.0
        while t_snap <= dur + 1000:
            push(t_snap, "snap", None)
            t_snap += g.SNAP_MS

    def tok_dispatch(w: int, now: float) -> None:
        gg = gw[w]
        T = tok_lane[0] if tok_lane else tok_fifo
        cap = max(1, T - 1) if tok_lane else T
        tried = 0
        while gg["free"] > 0 and gg["rr"] and tried < len(gg["rr"]):
            ten = gg["rr"].popleft()
            q = gg["q"][ten]
            if gg["running"].get(ten, 0) >= cap:
                gg["rr"].append(ten)
                tried += 1
                continue
            r = q[0]
            if tok_lane:
                c = min(tok_lane[1], r.tok_left)
                r.tok_left -= c
                if r.tok_left <= 0:
                    q.popleft()
                dt = c * TOK_US * tokf / 1000.0
            else:
                q.popleft()
                r.tok_left = 0
                dt = tok_cost(r)
            if q:
                gg["rr"].append(ten)
            else:
                del gg["q"][ten]
            gg["free"] -= 1
            gg["running"][ten] = gg["running"].get(ten, 0) + 1
            push(now + dt, "tok_done", (r, ten, r.tok_left <= 0))
            tried = 0

    def usable(w: int, i: int, now: float) -> bool:
        return not eject or ej_until[w][i] <= now

    def score(w: int, i: int) -> float:
        v = view[w][i] if per_worker else snap[i]
        return v + (inflight[w][i] if local_inflight else 0)

    def choose(r: Req, now: float) -> int | None:
        w = r.worker  # type: ignore[attr-defined]
        cands = [i for i in range(n_own) if i not in r.tried and usable(w, i, now)]
        if not cands:
            return None
        if cfg["route"] == "random" or len(cands) == 1:
            return rnd.choice(cands)
        a, b = rnd.sample(cands, 2)
        return a if score(w, a) <= score(w, b) else b

    def fail(w: int, i: int, now: float) -> None:
        if not eject:
            return
        fails[w][i] += 1
        if fails[w][i] >= eject[0]:
            ej_until[w][i] = now + ej_period[w][i]
            ej_period[w][i] = min(ej_period[w][i] * 2, 30_000.0)
            fails[w][i] = 0
            stats["ejections"] += 1

    def succeed(w: int, i: int) -> None:
        if eject:
            fails[w][i] = 0
            ej_period[w][i] = eject[1]

    def finalize(r: Req, now: float) -> None:
        if r.outcome:
            return
        r.t_end = now
        if getattr(r, "err", False) and r.posture == "fail_closed" and r.done < r.w:
            r.outcome = "error"
        elif r.done >= r.w and r.w >= max(r.w_full, 1):
            r.outcome = "full"
        elif r.posture == "degrade":
            r.outcome = "partial" if r.done > 0 else "unscanned"
        else:
            r.outcome = "refused_503"
        p = r.parent
        if p is not None:
            if getattr(r, "idx", -1) == 0 and r.done > 0:
                p.newest_done = True  # type: ignore[attr-defined]
            if p.outcome:
                return
            p.done += min(r.done, r.w)
            if getattr(r, "err", False):
                p.err = True  # type: ignore[attr-defined]
            p.pending -= 1
            if p.pending == 0:
                finalize(p, now)

    def send_to(u: Req, i: int, now: float, attempt: Req | None = None) -> None:
        a = attempt or u
        a.owner = i  # type: ignore[attr-defined]
        w = u.worker  # type: ignore[attr-defined]
        inflight[w][i] += a.w
        a.tgen = getattr(a, "tgen", 0) + 1  # type: ignore[attr-defined]
        a.acked = False  # type: ignore[attr-defined]
        push(now + hop(), "rx", (owners[i], a))
        if ack_to:
            push(now + ack_to, "a_timeout", (a, a.tgen))

    def retry(u: Req, now: float, avoid: int) -> bool:
        """A new attempt for unit u (w == 1) on another usable owner, deadline permitting."""
        if u.w != 1 or now + 2 * HOP_BASE_MS + pred >= u.deadline:
            return False
        if avoid not in u.tried:
            u.tried.append(avoid)
        i = choose(u, now)
        if i is None:
            return False
        u.tried.append(i)
        a = Req(u.rid, u.tenant, u.posture, u.n, 1, u.t_arr, u.deadline, u.cls, w_full=u.w_full)
        a.sk, a.org, a.worker, a.orig = u.sk, u.org, u.worker, u  # type: ignore[attr-defined]
        u.cur = a  # type: ignore[attr-defined]
        stats["retries"] += 1
        send_to(u, i, now, a)
        return True

    def abandon(a: Req, now: float, count_fail: bool) -> None:
        if getattr(a, "abandoned", False) or a.outcome:
            return
        a.abandoned = True  # type: ignore[attr-defined]
        i = getattr(a, "owner", None)
        if i is None:
            return
        remove_queued(owners[i], a)
        w = a.worker  # type: ignore[attr-defined]
        inflight[w][i] = max(0, inflight[w][i] - a.w)
        stats["cancel_frames"] += 1
        if count_fail:
            fail(w, i, now)

    def faulty(i: int, now: float) -> bool:
        return i == f_owner and now >= f_t

    def start_next(o, now: float) -> None:
        while True:
            req = o.pop()
            if req is None:
                o.busy = False
                return
            if req.cancelled:
                continue
            if getattr(req, "abandoned", False):
                continue  # cancel frame arrived: dropped from the queue
            if now + pred > req.deadline - HOP_BASE_MS:
                req.cancelled = True
                push(now + hop(), "reply", (req, o.idx, o.backlog(), False))
                continue
            s = svc()
            o.busy, o.busy_until = True, now + s
            o.busy_ms += s
            push(now + s, "win_done", (o, req))
            return

    def unit_of(a: Req) -> Req:
        return getattr(a, "orig", None) or a

    def try_budget(r: Req, now: float) -> bool:
        rate, burst = budget
        b = buckets.setdefault(r.org, [float(burst), now])
        b[0] = min(float(burst), b[0] + (now - b[1]) * rate / 1000.0)
        b[1] = now
        if b[0] >= r.w:
            b[0] -= r.w
            return True
        return False

    max_w = cfg.get("max_windows", g.MAX_WINDOWS)
    while ev:
        now, _, kind, obj = heapq.heappop(ev)
        if kind == "snap":
            for i, o in enumerate(owners):
                snap[i] = o.backlog()
        elif kind == "hb":
            for i, o in enumerate(owners):
                if not (f_kind == "blackhole" and faulty(i, now)):
                    view[obj][i] = o.backlog()
        elif kind == "tok_arrive":
            r = obj
            gg = gw[r.worker]
            if r.tenant not in gg["q"]:
                gg["q"][r.tenant] = deque()
                gg["rr"].append(r.tenant)
            gg["q"][r.tenant].append(r)
            tok_dispatch(r.worker, now)
        elif kind == "tok_done":
            r, ten, done = obj
            gg = gw[r.worker]
            gg["free"] += 1
            gg["running"][ten] -= 1
            if done:
                push(now, "send", r)
            tok_dispatch(r.worker, now)
        elif kind == "gw_deadline":
            r = obj
            if r.outcome:
                continue
            for c in (r.kids or [r]):
                a = getattr(c, "cur", c)
                if not c.outcome and getattr(a, "owner", None) is not None:
                    abandon(a, now, count_fail=not getattr(a, "acked", False))
            r.t_end = now
            finalize(r, now)
            for c in r.kids:
                c.outcome = c.outcome or "late"
        elif kind == "send":
            r = obj
            if r.outcome:
                continue
            if any_ext and now >= r.deadline - HOP_BASE_MS:
                finalize(r, now)
                continue
            if r.w > max_w:
                r.outcome = "refused_413" if r.posture == "fail_closed" else "unscanned"
                r.policy = "over_length"
                r.t_end = now
                continue
            if budget and not try_budget(r, now):
                r.outcome = "refused_429" if r.posture == "fail_closed" else "unscanned"
                r.policy = "budget"
                r.t_end = now
                continue
            if cfg.get("fanout") and r.w > 1 and r.parent is None:
                kids = []
                for j in range(r.w):
                    c = Req(-1, r.tenant, r.posture, 0, 1, r.t_arr, r.deadline, "_child", parent=r)
                    c.sk, c.org, c.worker, c.idx = r.sk, r.org, r.worker, j  # type: ignore[attr-defined]
                    kids.append(c)
                r.kids = kids
                r.pending = len(kids)
                used: list[int] = []
                for c in kids:
                    c.tried = list(used) if len(used) < n_own - 1 else []
                    i = choose(c, now)
                    if i is None:
                        c.tried = []
                        i = choose(c, now)
                    if i is None:
                        finalize(c, now)
                        continue
                    c.tried = [i]
                    used.append(i)
                    send_to(c, i, now)
                continue
            i = choose(r, now)
            if i is None:
                finalize(r, now)
                continue
            r.tried.append(i)
            send_to(r, i, now)
        elif kind == "rx":
            o, r = obj
            if getattr(r, "abandoned", False):
                continue
            if f_kind == "blackhole" and faulty(o.idx, now):
                continue
            if f_kind == "error" and faulty(o.idx, now):
                push(now + 0.1 + hop(), "reply", (r, o.idx, 0, True))
                continue
            if o.cap is not None and o.per_tenant.get(r.sk, 0) + 1 > o.cap:
                k = 0
            else:
                limit = r.w if o.cap is None else min(r.w, o.cap - o.per_tenant.get(r.sk, 0))
                k = 0
                pf = 0.0
                o._key = g.sched_key(r)
                for j in range(limit, 0, -1):
                    pf = o.predict(now, r, j) if classes else o.predicted_finish(now, r.sk, j)
                    if pf <= r.deadline - HOP_BASE_MS:
                        k = j
                        break
            if r.posture == "fail_closed" and k < r.w:
                k = 0
            if k == 0:
                push(now + hop(), "shed", (r, o.idx, o.backlog()))
                continue
            r.admitted = k
            o.enqueue(r, k)
            if ack_to:
                push(now + hop(), "ack", (r, r.tgen, pf))
            if not o.busy:
                start_next(o, now)
        elif kind == "shed":
            r, oi, bl = obj
            w = r.worker
            if per_worker:
                view[w][oi] = bl
            inflight[w][oi] = max(0, inflight[w][oi] - r.w)
            if getattr(r, "abandoned", False):
                continue
            r.tgen = getattr(r, "tgen", 0) + 1  # type: ignore[attr-defined]
            u = unit_of(r)
            if u.outcome:
                continue
            if len(u.tried) < 2 and now + 2 * HOP_BASE_MS + s_mean < u.deadline:
                i = choose(u, now)
                if i is not None:
                    u.tried.append(i)
                    send_to(u, i, now, r if r is not u else None)
                    continue
            if newest_retry and getattr(u, "idx", -1) == 0 and u.parent is not None \
                    and now + 2 * HOP_BASE_MS + s_mean < u.deadline:
                p = u.parent
                older = [c for c in p.kids if c.idx > 0 and not c.outcome
                         and not getattr(getattr(c, "cur", c), "abandoned", False)
                         and getattr(getattr(c, "cur", c), "owner", None) is not None]
                if older:
                    victim = max(older, key=lambda c: c.idx)
                    va = getattr(victim, "cur", victim)
                    slot = va.owner
                    abandon(va, now, count_fail=False)
                    finalize(victim, now)
                    u.tried.append(slot)
                    stats["retries"] += 1
                    send_to(u, slot, now, r if r is not u else None)
                    continue
            finalize(u, now)
        elif kind == "ack":
            a, gen, pf = obj
            if gen != a.tgen or getattr(a, "abandoned", False) or getattr(a, "replied", False):
                continue
            a.acked = True
            a.tgen += 1
            # A fail_closed window behind a backlogged degrade class is only guaranteed 1 in
            # fc_every dispatches, so its completion bound is the prediction times fc_every.
            mult = 1
            if classes and a.posture == "fail_closed":
                mult = cfg.get("fc_every", 4) * (2 if cfg.get("size_classes") else 1)
            span = (pf - now) * mult
            push(now + span + 2 * HOP_BASE_MS + max(5.0, 0.5 * span), "a_timeout", (a, a.tgen))
        elif kind == "a_timeout":
            a, gen = obj
            u = unit_of(a)
            if gen != a.tgen or u.outcome or getattr(a, "abandoned", False) \
                    or getattr(a, "replied", False):
                continue
            i = a.owner
            abandon(a, now, count_fail=True)
            if not (retry_on and retry(u, now, i)):
                u.err = True  # type: ignore[attr-defined]
                finalize(u, now)
        elif kind == "win_done":
            o, r = obj
            if getattr(r, "abandoned", False):
                stats["wasted_windows"] += 1
            r.done += 1
            if r.done >= r.admitted and not r.cancelled:
                if f_kind == "blackhole" and faulty(o.idx, now):
                    pass
                else:
                    push(now + hop(), "reply", (r, o.idx, o.backlog(), False))
            start_next(o, now)
        elif kind == "reply":
            r, oi, bl, err = obj
            w = r.worker
            if per_worker:
                view[w][oi] = bl
            if getattr(r, "abandoned", False):
                continue
            r.replied = True  # type: ignore[attr-defined]
            inflight[w][oi] = max(0, inflight[w][oi] - r.w)
            u = unit_of(r)
            if err:
                fail(w, oi, now)
                if u.outcome:
                    continue
                if retry_on and retry(u, now, oi):
                    continue
                u.err = True  # type: ignore[attr-defined]
                finalize(u, now)
                continue
            if any_ext:
                succeed(w, oi)
            if u is not r:
                u.done, u.admitted = r.done, r.admitted
            if not u.outcome:
                finalize(u, now)

    warm = cfg.get("warmup_ms", 2000.0)
    lo = cfg.get("report_from_ms", warm)
    hi = cfg.get("report_to_ms", INF)
    out: dict[str, object] = {}
    keys = ("full", "partial", "unscanned", "refused_503", "refused_413", "refused_429", "error")
    for cls in sorted({r.cls for r in reqs}):
        rs = [r for r in reqs if r.cls == cls and lo <= r.t_arr < hi]
        if not rs:
            continue
        lat = [r.t_end - r.t_arr for r in rs]
        cnt = {k: sum(1 for r in rs if r.outcome == k) for k in keys}
        if any_ext:
            n = len(rs)
            cap_u2 = sum(1 for r in rs if r.posture == "degrade" and not r.policy
                         and r.outcome in ("partial", "unscanned") and r.done < r.w)
            pol_u2 = sum(1 for r in rs if r.posture == "degrade" and r.outcome != "full"
                         and (r.policy or (r.done >= r.w and r.w < r.w_full)))
            multi = [r for r in rs if r.posture == "degrade" and r.w > 1 and not r.policy]
            head = [r for r in rs if r.posture == "degrade" and getattr(r, "n_full", 0) <= 1024]
            cap_head = sum(1 for r in head if r.outcome in ("partial", "unscanned") and r.done < r.w)
            row = {"n": n, **{f"share_{k}": round(v / n, 5) for k, v in cnt.items()},
                   "u2_capacity": round(cap_u2 / n, 5), "u2_policy": round(pol_u2 / n, 5),
                   "u2_capacity_headline_band": round(cap_head / len(head), 5) if head else None,
                   "newest_window_unscanned_of_multiwindow": (
                       round(sum(1 for r in multi if not r.newest_done) / len(multi), 5)
                       if multi and cfg.get("fanout") else None),
                   "stage_ms_p50": pct(lat, 50), "stage_ms_p99": pct(lat, 99),
                   "stage_ms_max": pct(lat, 100)}
            out[cls] = row
        else:
            out[cls] = {
                "n": len(rs),
                **{f"share_{k}": round(v / len(rs), 5) for k, v in cnt.items() if k in keys[:5]},
                "stage_ms_p50": pct(lat, 50), "stage_ms_p99": pct(lat, 99), "stage_ms_max": pct(lat, 100),
                "stage_ms_p99_full_only": pct([r.t_end - r.t_arr for r in rs if r.outcome == "full"], 99),
                "coverage_mean": round(statistics.mean(min(r.done, r.w) / max(r.w_full, r.w, 1) for r in rs), 5),
            }
    busy = [round(o.busy_ms / dur, 4) for o in owners]
    out["_gpu_utilisation"] = {"per_owner": busy, "mean": round(statistics.mean(busy), 4)}
    out["_degrade_token_cap"] = deg_cap
    if any_ext:
        out["_counters"] = dict(stats)
    return out


def summary(res: dict) -> dict:
    d = res.get("victim_degrade", {})
    f = res.get("victim_fail_closed", {})
    a = res.get("attacker", {})
    line: dict[str, object] = {"gpu": res["_gpu_utilisation"]["mean"]}
    if d:
        line.update({
            "deg_full_pct": round(100 * d["share_full"], 3),
            "deg_partial_plus_unscanned_pct": round(100 * (d["share_partial"] + d["share_unscanned"]), 3),
            "deg_u2_capacity_pct": round(100 * d["u2_capacity"], 3) if "u2_capacity" in d else None,
            "deg_u2_capacity_headline_pct": (round(100 * d["u2_capacity_headline_band"], 3)
                                             if d.get("u2_capacity_headline_band") is not None else None),
            "deg_u2_policy_pct": round(100 * d["u2_policy"], 3) if "u2_policy" in d else None,
            "deg_unscanned_pct": round(100 * d["share_unscanned"], 3),
            "deg_newest_missed_pct": (round(100 * d["newest_window_unscanned_of_multiwindow"], 3)
                                      if d.get("newest_window_unscanned_of_multiwindow") is not None else None),
            "deg_p99": d["stage_ms_p99"]})
    if f:
        line.update({
            "fc_scanned_pct": round(100 * f["share_full"], 3),
            "fc_refused_pct": round(100 * (f.get("share_refused_503", 0) + f.get("share_error", 0)), 3),
            "fc_413_pct": round(100 * f.get("share_refused_413", 0), 3),
            "fc_p50": f["stage_ms_p50"], "fc_p99": f["stage_ms_p99"], "fc_max": f["stage_ms_max"]})
    if a:
        line.update({"atk_refused_pct": round(100 * (a.get("share_refused_503", 0) + a.get("share_refused_429", 0)
                                                     + a.get("share_error", 0)), 2),
                     "atk_429_pct": round(100 * a.get("share_refused_429", 0), 2)})
    if "_counters" in res:
        line["counters"] = res["_counters"]
    return line


B, P = g.BASE, g.PROPOSED
# Rev-3 design (plan §5.5, §5.5a): class-priority owner (D > FS > FL), per-worker views with local in-flight, outlier
# ejection, fail_closed attempt timeout and retry, gateway timer with cancel frames, newest-window
# retry, dedicated tokenizer lane (2 threads per worker, 384-token chunks, per-org DRR).
PROPOSED3 = {**P, "classes": True, "size_classes": True, "fc_every": 4, "views": "per_worker", "gw_workers": 32,
             "hb_ms": 1000.0, "local_inflight": True, "eject": (3, 1000.0), "ack_timeout_ms": 5.0,
             "retry": True, "gw_timer": True, "newest_retry": True, "tok_lane": (2, 384)}
REV2_REALISTIC = {**P, "views": "per_worker", "gw_workers": 32, "hb_ms": 1000.0, "gw_timer": True}


def selftest() -> dict:
    out = {}
    for name, cfg, sd in (("planning_100_rps_per_l4", {**B, **P, "rps": 400}, 101),
                          ("k3_attacker_80", {**B, **P, "rps": 300, "atk_rps": 80}, 404)):
        ref, mine = g.run(cfg, sd), run3(cfg, sd)
        out[name] = "identical" if ref == mine else {"rev2": ref, "run3": mine}
    return out


def group_design() -> dict:
    sc: dict[str, object] = {}
    for per in (50, 75, 100, 125, 150):
        sc[f"ladder/rev3/mixed/{per}_rps_per_l4"] = summary(run3({**B, **PROPOSED3, "rps": 4 * per}, 101))
    for sd in (202, 303):
        sc[f"ladder/rev3/mixed/100_rps_per_l4/seed{sd}"] = summary(run3({**B, **PROPOSED3, "rps": 400}, sd))
    for per in (100, 150):
        sc[f"ladder/rev3/all_fail_closed/{per}_rps_per_l4"] = summary(
            run3({**B, **PROPOSED3, "fail_closed_share": 1.0, "rps": 4 * per}, 101))
    for per in (75, 100):
        sc[f"ladder/rev2_design_realistic_views/mixed/{per}_rps_per_l4"] = summary(
            run3({**B, **REV2_REALISTIC, "rps": 4 * per}, 101))
    for rps in (200, 225, 250, 275):
        sc[f"n_plus_1/rev3/3_owners_{rps}_rps/seed505"] = summary(run3({**B, **PROPOSED3, "owners": 3, "rps": rps}, 505))
    for per in (100, 110):
        sc[f"ladder/rev3/mixed/{per}_rps_per_l4/5_owners"] = summary(run3({**B, **PROPOSED3, "owners": 5, "rps": 5 * per}, 101))
    sc["n_plus_1/rev3/4_of_5_owners_400_rps/seed505"] = summary(run3({**B, **PROPOSED3, "owners": 4, "rps": 400}, 505))
    for sd in (505, 606, 707):
        sc[f"n_plus_1/rev3/3_owners_400_rps/seed{sd}"] = summary(run3({**B, **PROPOSED3, "owners": 3, "rps": 400}, sd))
        sc[f"n_plus_1/rev3/3_owners_300_rps/seed{sd}"] = summary(run3({**B, **PROPOSED3, "owners": 3, "rps": 300}, sd))
    return sc


def group_stress() -> dict:
    base = {**B, **PROPOSED3, "rps": 400}
    S = {
        "window_2.30ms": {"s_win_ms": 2.30},
        "window_2.46ms_cv0.12": {"s_win_ms": 2.46, "s_cv": 0.12},
        "window_2.77ms_cv0.12": {"s_win_ms": 2.77, "s_cv": 0.12},
        "window_2.77ms_cv0.12_owner_predicts_2.15": {"s_win_ms": 2.77, "s_cv": 0.12, "pred_ms": 2.15},
        "1pct_windows_x5": {"tail_p": 0.01, "tail_mult": 5.0},
        "tokeniser_x3": {"tok_factor": 3.0},
        "mmpp_50pct_dwell_1s": {"arrivals": "mmpp", "mmpp_a": 0.5, "mmpp_dwell_ms": 1000.0},
        "batch_mean_2": {"arrivals": "batch", "batch_mean": 2.0},
        "batch_mean_4": {"arrivals": "batch", "batch_mean": 4.0},
        "degrade_deadline_10ms": {"d_deg_ms": 10.0},
        "combined_moderate": {"s_win_ms": 2.46, "s_cv": 0.12, "tail_p": 0.005, "tail_mult": 4.0,
                              "arrivals": "mmpp", "mmpp_a": 0.3, "mmpp_dwell_ms": 2000.0, "tok_factor": 2.0},
    }
    sc: dict[str, object] = {}
    for name, ext in S.items():
        sc[f"stress/rev3/100_rps_per_l4/{name}"] = summary(run3({**base, **ext}, 101))
    for name in ("window_2.46ms_cv0.12", "batch_mean_2", "batch_mean_4", "combined_moderate"):
        sc[f"stress/rev3/75_rps_per_l4/{name}"] = summary(run3({**base, **S[name], "rps": 300}, 101))
    sc["stress/rev3/50_rps_per_l4/batch_mean_4"] = summary(run3({**base, **S["batch_mean_4"], "rps": 200}, 101))
    sc["stress/rev3/n_plus_1_300_rps/combined_moderate"] = summary(
        run3({**base, **S["combined_moderate"], "owners": 3, "rps": 300}, 505))
    return sc


def group_fail() -> dict:
    sc: dict[str, object] = {}
    win = {"duration_ms": 32_000.0, "report_from_ms": 20_000.0, "report_to_ms": 30_000.0}
    for kind in ("error", "blackhole"):
        flt = {"fault": (kind, 0, 20_000.0)}
        sc[f"fault_{kind}/rev2_routing_per_worker_views"] = summary(
            run3({**B, **REV2_REALISTIC, "rps": 400, **win, **flt}, 101))
        sc[f"fault_{kind}/rev3"] = summary(run3({**B, **PROPOSED3, "rps": 400, **win, **flt}, 101))
        sc[f"fault_{kind}/rev3_without_ejection"] = summary(
            run3({**B, **PROPOSED3, "eject": None, "rps": 400, **win, **flt}, 101))
        sc[f"fault_{kind}/rev3/all_fail_closed"] = summary(
            run3({**B, **PROPOSED3, "fail_closed_share": 1.0, "rps": 400, **win, **flt}, 101))
    return sc


def group_k3() -> dict:
    sc: dict[str, object] = {}
    k3 = {**B, "rps": 300, "atk_rps": 80}
    budget = {"win_budget": (100.0, 64.0)}
    for ns in (1, 2, 4, 8, 16):
        sc[f"k3/fc_attacker_80rps/sybil_{ns}/rev2_design"] = summary(run3({**k3, **P, "n_sybil": ns}, 404))
        sc[f"k3/fc_attacker_80rps/sybil_{ns}/rev3_two_classes_only"] = summary(
            run3({**k3, **PROPOSED3, "size_classes": False, "n_sybil": ns}, 404))
        sc[f"k3/fc_attacker_80rps/sybil_{ns}/rev3_owner_only"] = summary(run3({**k3, **PROPOSED3, "n_sybil": ns}, 404))
        sc[f"k3/fc_attacker_80rps/sybil_{ns}/rev3_trial_pool_key"] = summary(
            run3({**k3, **PROPOSED3, "n_sybil": ns, "pool_sybil": True}, 404))
        sc[f"k3/fc_attacker_80rps/sybil_{ns}/rev3_paid_orgs_window_budget_100ps"] = summary(
            run3({**k3, **PROPOSED3, "n_sybil": ns, **budget}, 404))
    for ns in (1, 8):
        sc[f"k3/degrade_attacker_80rps/sybil_{ns}/rev3_owner_only"] = summary(
            run3({**k3, **PROPOSED3, "n_sybil": ns, "atk_posture": "degrade"}, 404))
        sc[f"k3/degrade_attacker_80rps/sybil_{ns}/rev3_paid_orgs_window_budget_100ps"] = summary(
            run3({**k3, **PROPOSED3, "n_sybil": ns, "atk_posture": "degrade", **budget}, 404))
    # the attacker's best response to size classes: short (headline-band) fail_closed requests
    short = {**B, "rps": 300, "atk_rps": 400, "atk_tokens": 1024}
    for ns in (1, 8):
        sc[f"k3/fc_short_attacker_400rps/sybil_{ns}/rev2_design"] = summary(run3({**short, **P, "n_sybil": ns}, 404))
        sc[f"k3/fc_short_attacker_400rps/sybil_{ns}/rev3_owner_only"] = summary(
            run3({**short, **PROPOSED3, "n_sybil": ns}, 404))
        sc[f"k3/fc_short_attacker_400rps/sybil_{ns}/rev3_paid_orgs_window_budget_100ps"] = summary(
            run3({**short, **PROPOSED3, "n_sybil": ns, **budget}, 404))
    sc["k3/fc_attacker_40rps/sybil_4/rev2_design"] = summary(run3({**B, **P, "rps": 300, "atk_rps": 40, "n_sybil": 4}, 404))
    sc["k3/fc_attacker_40rps/sybil_4/rev3_owner_only"] = summary(
        run3({**B, **PROPOSED3, "rps": 300, "atk_rps": 40, "n_sybil": 4}, 404))
    # tokenizer head-of-line (Reviewer B B2b): single attacker, FIFO 1 thread vs the rev-3 lane
    sc["tok/k3_80/rev2_owner_tok_fifo_1_thread"] = summary(run3({**k3, **P, "tok_fifo": 1, "gw_workers": 32}, 404))
    sc["tok/k3_80/rev3_tok_lane_2x384_drr"] = summary(run3({**k3, **PROPOSED3}, 404))
    sc["tok/k3_80/rev3_but_tok_fifo_1_thread"] = summary(run3({**k3, **PROPOSED3, "tok_lane": None, "tok_fifo": 1}, 404))
    return sc


def group_long() -> dict:
    sc: dict[str, object] = {}
    for ls in (0.10, 0.20, 0.30):
        for per in (100, 75):
            sc[f"long/mixed/long_{int(ls*100)}pct/{per}_rps_per_l4"] = summary(
                run3({**B, **PROPOSED3, "rps": 4 * per, "long_share": ls}, 606))
    for ls in (0.10, 0.30):
        for per in (100, 50):
            sc[f"long/all_fail_closed/long_{int(ls*100)}pct/{per}_rps_per_l4"] = summary(
                run3({**B, **PROPOSED3, "fail_closed_share": 1.0, "rps": 4 * per, "long_share": ls}, 606))
    return sc


def group_curve() -> dict:
    """Provisioning curve for R2 (Reviewer A S2): the highest load per L4 that keeps headline-band
    degrade capacity U2 under 0.1 % depends on the sustained window time T-GPU1 measures."""
    sc: dict[str, object] = {}
    for s_ms, cv in ((2.15, 0.08), (2.30, 0.08), (2.46, 0.12)):
        for per in (50, 60, 75, 90):
            sc[f"curve/window_{s_ms}ms/{per}_rps_per_l4"] = summary(
                run3({**B, **PROPOSED3, "rps": 4 * per, "s_win_ms": s_ms, "s_cv": cv}, 101))
    for d in (14.0, 16.0):
        sc[f"curve/window_2.46ms/100_rps_per_l4/degrade_deadline_{int(d)}ms"] = summary(
            run3({**B, **PROPOSED3, "rps": 400, "s_win_ms": 2.46, "s_cv": 0.12, "d_deg_ms": d}, 101))
    return sc


GROUPS = {"design": group_design, "stress": group_stress, "fail": group_fail, "k3": group_k3,
          "long": group_long, "curve": group_curve}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--group", default="all")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    t0 = time.process_time()
    st = selftest()
    res: dict[str, object] = {"selftest_run3_all_extensions_off_vs_rev2_run": st}
    if any(v != "identical" for v in st.values()):
        sys.stdout.write(json.dumps(res, indent=1) + "\n")
        return 1
    res["proposed3"] = {k: (list(v) if isinstance(v, tuple) else v) for k, v in PROPOSED3.items()}
    for name, fn in GROUPS.items():
        if args.group in ("all", name):
            res[name] = fn()
    res["cpu_seconds"] = round(time.process_time() - t0, 1)
    text = json.dumps(res, indent=1)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    else:
        sys.stdout.write(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
