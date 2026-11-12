"""F1a-3 plan revision 3: discrete-event fairness model of the revision-3 stream slots (plan §3.6.3).

Built on Plan Reviewer B's model (#121 rev 2, review 5871411489, `sim/stream_slots_sim.py`: same
scenarios, seeds, horizon and counting window) with the decisions taken by the prototype policy
`proto/slots_core.py` (the class the unit tests exercise), so the simulated rules are the specified rules.

Revision-3 structure modelled:
  * admission: a request holds one of Q = 14 shared admission slots only for its pipeline time (20 ms);
    a full admission queue sheds at the door (QUEUE_FULL). At the end of the pipeline the request is
    handed off: the admission slot is released whatever the stream-slot outcome.
  * stream slots: SlotPolicy(S, B, W = 14 total waiters, w per-org waiters, reserve R); a waiter expires
    after 2 s (429 upstream_inflight_limited); one slot per request for the whole response.
Scenarios (rates per second, durations in seconds, exponential unless stated):
  heavy   org A 200 RPS x 5 s (demand 1,000 > S = 470), org B 2 RPS x 5 s           (B's scenario)
  loris   org A opens S streams of 299 s over 0-5 s plus 2 RPS of 299 s, org B 2 RPS x 5 s (B's scenario;
          one event per 19.9 s keeps each inside the 20 s gap and the 299.94 s attempt bound)
  many    40 orgs, Zipf(1.1) shares of 1.5 x capacity (141 RPS x 5 s at S = 470)
  alone   org A alone at 120 RPS x 5 s (demand 600 > S): what the reserve costs an idle worker's one org
  bytes   S = 191, B = 191 MiB (plan §6.5 at per_worker_rss = 1 GiB): org A 50 RPS of max-size bodies
          (charge 1.0 + 4 x 2.56 = 11.24 MiB each), org B 2 RPS of 100 KiB bodies (charge 1.4 MiB)
Output: per scenario and variant, per org offered / served / shed at the door / 429 (wait full, expired).
Deterministic: seeded random.Random (seed 7, as B's).
"""
from __future__ import annotations

import heapq
import json
import math
import random
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "proto"))
from slots_core import SlotLimits, SlotPolicy

MIB = 2**20


def run(scenario: str, S: int = 470, Q: int = 14, W: int = 14, w: int = 1, R: int = 0,
        seed: int = 7, horizon: float = 300.0, pipeline_s: float = 0.020, wait_s: float = 2.0,
        b_rps: float = 2.0, alone_rps: float = 120.0) -> dict:
    rng = random.Random(seed)
    ev: list = []
    seq = 0

    def push(t, kind, payload):
        nonlocal seq
        heapq.heappush(ev, (t, seq, kind, payload))
        seq += 1

    budget = 10**12  # count-only scenarios: bytes never bind
    fixed = 1
    if scenario == "bytes":
        S, budget, fixed = 191, 191 * MIB, 1 * MIB

    def arrivals(org, rate, dur, start=0.0, charge=fixed):
        t = start
        while t < horizon:
            t += rng.expovariate(rate)
            push(t, "arrive", (org, dur if dur > 100 else rng.expovariate(1.0 / dur), charge))

    if scenario == "heavy":
        arrivals("A", 200.0, 5.0)
        arrivals("B", 2.0, 5.0)
    elif scenario == "loris":
        for i in range(S):  # opened over the first 5 s (B's model opened them at t = 0 with no pipeline
            push(i * 5.0 / S, "arrive", ("A", 299.0, fixed))  # time; with a 20 ms pipeline they still all land)
        arrivals("A", 2.0, 299.0, start=1.0)
        arrivals("B", b_rps, 5.0)
    elif scenario == "many":
        orgs = 40
        weights = [1 / (k + 1) ** 1.1 for k in range(orgs)]
        total = 1.5 * S / 5.0
        for k in range(orgs):
            arrivals(f"o{k:02d}", total * weights[k] / sum(weights), 5.0)
    elif scenario == "alone":
        arrivals("A", alone_rps, 5.0)
    elif scenario == "bytes":
        arrivals("A", 50.0, 5.0, charge=fixed + int(4 * 2.56 * MIB))
        arrivals("B", 2.0, 5.0, charge=fixed + 4 * 100 * 1024)
    else:
        raise SystemExit(scenario)

    reserve_bytes = min(budget - 1, R * fixed + (int(4 * 2.56 * MIB) if scenario == "bytes" else 0)) if R else 0
    pol = SlotPolicy(SlotLimits(slots=S, byte_budget=budget, wait_total=W, wait_per_org=w,
                                reserve=R, reserve_bytes=reserve_bytes))
    adm_used = 0
    waiting: dict[str, tuple[str, float, float, int]] = {}  # req -> (org, t, dur, charge)
    stats: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    held_time: dict[str, float] = defaultdict(float)
    lo, hi = 10.0, horizon - 10.0
    rid = 0

    def count(org, t, key):
        if lo <= t <= hi:
            stats[org][key] += 1

    def start(org, req, t, dur, charge):
        push(t + dur, "end", (org, charge))
        count(org, t, "served")
        if lo <= t <= hi:
            held_time[org] += min(dur, hi - t)

    while ev:
        t, _, kind, payload = heapq.heappop(ev)
        if t > horizon:
            break
        if kind == "arrive":
            org, dur, charge = payload
            rid += 1
            count(org, t, "offered")
            if adm_used >= Q:
                count(org, t, "shed_door_QUEUE_FULL")
                continue
            adm_used += 1
            push(t + pipeline_s, "handoff", (org, f"r{rid}", dur, charge))
        elif kind == "handoff":
            adm_used -= 1  # the admission slot is released at hand-off, whatever follows
            org, req, dur, charge = payload
            v = pol.request(org, req, charge)
            if v == "grant":
                start(org, req, t, dur, charge)
            elif v == "wait":
                waiting[req] = (org, t, dur, charge)
                push(t + wait_s, "expire", req)
            else:
                count(org, t, "429_wait_full")
        elif kind == "expire":
            req = payload
            if req in waiting:
                org, _, _, _ = waiting.pop(req)
                for g_org, g_req, g_charge in pol.cancel(org, req) or []:
                    o, _, d, c = waiting.pop(g_req)
                    start(o, g_req, t, d, c)
                count(org, t, "429_wait_expired")
        elif kind == "end":
            org, charge = payload
            for g_org, g_req, g_charge in pol.release(org, charge):
                o, _, d, c = waiting.pop(g_req)
                start(o, g_req, t, d, c)
    out = {}
    for org in sorted(stats):
        s = stats[org]
        off = s["offered"] or 1
        out[org] = {k: s[k] for k in ("offered", "served", "shed_door_QUEUE_FULL", "429_wait_full",
                                      "429_wait_expired")}
        out[org]["served_share"] = round(s["served"] / off, 3)
        out[org]["mean_slots_held"] = round(held_time[org] / (hi - lo), 1)
    return out


def summarise_many(res: dict, S: int = 470) -> dict:
    """Max-min check for the 40-org scenario: an org's mean holding against its water-filled fair share."""
    demand = {o: r["offered"] / (280.0) * 5.0 for o, r in res.items()}  # offered RPS x 5 s mean duration
    cap, left, fair = float(S), dict(demand), {}
    while left:
        share = cap / len(left)
        small = {o: d for o, d in left.items() if d <= share}
        if not small:
            for o in left:
                fair[o] = share
            break
        for o, d in small.items():
            fair[o] = d
            cap -= d
            del left[o]
    ratios = sorted(res[o]["mean_slots_held"] / fair[o] for o in res if fair[o] >= 5.0)  # >= 5 slots: noise
    served = sorted(r["served_share"] for r in res.values())
    return {"orgs": len(res), "held_over_fair_min": round(ratios[0], 2), "held_over_fair_median":
            round(ratios[len(ratios) // 2], 2), "held_over_fair_max": round(ratios[-1], 2),
            "served_share_min": served[0], "served_share_median": served[len(served) // 2],
            "top_org_served_share": res["o00"]["served_share"],
            "top3_held_vs_fair": [(o, res[o]["mean_slots_held"], round(fair[o], 1)) for o in ("o00", "o01", "o02")],
            "small_orgs_served_min": min(r["served_share"] for o, r in res.items() if demand[o] < 5.0)}


if __name__ == "__main__":
    rows = []
    variants = [("R0_w1", {"R": 0}), ("R1pct_w1", {"R": math.ceil(0.01 * 470)}),
                ("R2pct_w1", {"R": math.ceil(0.02 * 470)}), ("R5pct_w1", {"R": math.ceil(0.05 * 470)}),
                ("R2pct_w2", {"R": math.ceil(0.02 * 470), "w": 2})]
    for scen in ("heavy", "loris", "many", "alone", "bytes"):
        for label, kw in variants:
            if scen == "bytes":  # S = 191 in this scenario: the same reserve fractions of 191
                kw = {**kw, "R": math.ceil(kw.get("R", 0) * 191 / 470) if kw.get("R") else 0}
            res = run(scen, **kw)
            row = {"scenario": scen, "variant": label, "S": 191 if scen == "bytes" else 470, **kw, **res}
            if scen == "many":
                row = {"scenario": scen, "variant": label, **kw, **summarise_many(res)}
            rows.append(row)
            print(json.dumps(row), flush=True)
    # the reserve at small S: develop's pipeline cost (S = 5) and a post-#149 estimate (S = 50).
    # B offers 0.2 RPS x 5 s (one concurrent stream); "alone" offers 1.25 x capacity.
    for S in (5, 50):
        for R in sorted({0, 1, math.ceil(0.02 * S), math.ceil(0.05 * S)}):
            for scen in ("loris", "alone"):
                kw = {"S": S, "R": R, "b_rps": 0.2, "alone_rps": 1.25 * S / 5.0}
                res = run(scen, **kw)
                row = {"scenario": f"{scen}_S{S}", "variant": f"R{R}", **kw, **res}
                rows.append(row)
                print(json.dumps(row), flush=True)
