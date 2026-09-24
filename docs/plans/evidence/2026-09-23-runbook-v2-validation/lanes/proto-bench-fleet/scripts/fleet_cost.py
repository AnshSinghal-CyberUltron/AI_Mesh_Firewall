#!/usr/bin/env python3
"""Line-by-line monthly ON-DEMAND cost of ONE fleet at a sustained qualified rate (Mumbai prices).

  fleet_cost.py --units g2-standard-24=2 --rps 80 --b-lb2c 57213 --b-c2lb 8629 --b-u2p 9223 [--opq 1.0]
  [--redis-alt]   also price the bundle with Memorystore Standard HA M3 13 GiB instead of M1 1 GiB

bytes are IP-layer bytes per QUALIFIED request (measured with iptables ACCT; include TCP/IP headers + ACKs).
"""
import argparse
import json

from fleet_select import REQ_PER_MONTH_PER_RPS, load_prices, tiered


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--units", action="append", required=True, help="SHAPE=N")
    ap.add_argument("--rps", type=float, required=True)
    ap.add_argument("--b-lb2c", type=float, required=True)
    ap.add_argument("--b-c2lb", type=float, required=True)
    ap.add_argument("--b-u2p", type=float, required=True)
    ap.add_argument("--opq", type=float, default=1.0, help="offered per qualified (1.0 if bytes already per qualified)")
    ap.add_argument("--redis-alt", action="store_true")
    a = ap.parse_args()
    pr = load_prices()
    lines = []
    total = 0.0
    for name, usd in pr["fixed_items"].items():
        lines.append((f"fixed: {name}", usd))
        total += usd
    for spec in a.units:
        shape, n = spec.split("=")
        n = int(n)
        vm = pr["shapes"][shape]
        lines.append((f"compute: {n} x {shape} VM on-demand 730 h (${vm}/mo each)", n * vm))
        lines.append((f"compute: {n} x 200 GiB pd-balanced boot (${pr['boot']}/mo each)", n * pr["boot"]))
        lines.append((f"compute: {n} x external IPv4 on VM (${pr['ip']}/mo each)", n * pr["ip"]))
        total += n * (vm + pr["boot"] + pr["ip"])
    req = a.rps * a.opq * REQ_PER_MONTH_PER_RPS
    eg_gib = req * (a.b_lb2c + a.b_u2p) / 2**30
    in_gib = req * a.b_c2lb / 2**30
    out_gib = req * a.b_lb2c / 2**30
    res = {"requests_per_month": req, "egress_gib": round(eg_gib, 1), "lb_in_gib": round(in_gib, 1),
           "lb_out_gib": round(out_gib, 1)}
    lbp = in_gib * pr["lb_in"] + out_gib * pr["lb_out"]
    lines.append((f"LB data processing: {in_gib:,.0f} GiB in x ${pr['lb_in']} + {out_gib:,.0f} GiB out x ${pr['lb_out']}", lbp))
    total += lbp
    for tier, tiers in (("premium_india", pr["premium_india"]), ("standard", pr["standard"])):
        e = tiered(eg_gib, tiers)
        res[f"egress_usd_{tier}"] = round(e, 2)
        res[f"total_usd_{tier}"] = round(total + e, 2)
    if a.redis_alt:
        res["redis_alt_delta_usd"] = round(550.42 - 80.3, 2)
    for k, v in lines:
        print(f"  {v:10.2f}  {k}")
    print(f"  egress: {req:,.0f} req/mo x ({a.b_lb2c:,.0f} + {a.b_u2p:,.0f}) B = {eg_gib:,.1f} GiB -> "
          f"Premium(India) ${res['egress_usd_premium_india']:,.2f} | Standard ${res['egress_usd_standard']:,.2f}")
    print(f"  TOTAL Premium ${res['total_usd_premium_india']:,.2f} | Standard ${res['total_usd_standard']:,.2f}")
    print(json.dumps(res))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
