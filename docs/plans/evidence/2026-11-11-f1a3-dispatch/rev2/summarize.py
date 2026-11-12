"""F1a-3 rev 2: summarise the paced benchmark (bench/out) and the dev-profile full path (devpath/out).

Prints the tables the plan's section 6 quotes. Pure arithmetic on the committed JSON; no network.
Cost basis: master runbook section 0.6 (Cloud Billing Catalog, asia-south1, on-demand, 730 h):
gateway c4-highcpu-16 + 60 GiB boot = $527.13 / month for 16 vCPU -> $32.95 per vCPU-month.
Utilisation cap 0.75 (ResourceContract.utilization_cap).
"""
from __future__ import annotations

import json
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
USD_PER_VCPU_MONTH = 527.13 / 16
UTIL = 0.75
# Planning event-count distribution (plan section 6.4): share -> events per streamed response
DIST = [(0.30, 24), (0.50, 200), (0.20, 600)]
E_MEAN = sum(s * e for s, e in DIST)


def load(label: str) -> dict[str, dict]:
    p = HERE / "bench" / "out" / f"{label}.json"
    if not p.exists():
        return {}
    d = json.loads(p.read_text())
    return {r["variant"]: r for r in d["results"]} | {"_meta": d}


def fit(points: list[tuple[float, float]]) -> tuple[float, float]:
    """Least squares c = a + b*E over (E, c) points."""
    n = len(points)
    mx = sum(e for e, _ in points) / n
    my = sum(c for _, c in points) / n
    sxx = sum((e - mx) ** 2 for e, _ in points)
    b = sum((e - mx) * (c - my) for e, c in points) / sxx
    return my - b * mx, b


def main() -> None:
    scen = {"P1_e24_g20_c64": (24, 64), "P2_e200_g20_c64": (200, 64), "P3_e600_g20_c64": (600, 64),
            "P5_e24_g20_c256": (24, 256), "P4_e200_g20_c256": (200, 256)}
    data = {k: load(k) for k in scen}
    variants = ["httpx_raw", "httpx_decode", "current", "aiohttp_raw", "aiohttp_decode", "aiohttp_design"]
    print("## Per scenario: main-thread CPU us per request (median of 2 runs) [util] (reads or events/req)")
    print("| scenario | E | C | " + " | ".join(variants) + " |")
    print("|---|---|---|" + "---|" * len(variants))
    for k, (e, c) in scen.items():
        d = data[k]
        if not d:
            continue
        cells = []
        for v in variants:
            r = d.get(v)
            cells.append("—" if r is None else
                         f"{r['thread_us_median']:,.0f} [{max(r['util_runs']):.2f}] ({r['events_per_req']:g})")
        print(f"| {k} | {e} | {c} | " + " | ".join(cells) + " |")
    print()
    print("## Ratios httpx/aiohttp (same scenario)")
    for k in scen:
        d = data[k]
        if not d or "aiohttp_design" not in d:
            continue
        hd, ad = d["httpx_decode"]["thread_us_median"], d["aiohttp_decode"]["thread_us_median"]
        hr, ar = d["httpx_raw"]["thread_us_median"], d["aiohttp_raw"]["thread_us_median"]
        de = d["aiohttp_design"]["thread_us_median"]
        extra = ""
        if "current" in d:
            extra = f", current/aiohttp_design {d['current']['thread_us_median'] / de:.1f}x"
        print(f"- {k}: decode {hd / ad:.1f}x, raw {hr / ar:.1f}x, httpx_decode/aiohttp_design {hd / de:.1f}x{extra}; "
              f"design overhead over aiohttp_decode {de - ad:+,.0f} us")
    print()
    print("## Linear model c(E) = a + b*E (us), per client, at a fixed per-worker event rate")
    fits = {}
    for rate, labels in (("3,200 ev/s (C=64, 20 ms)", ["P1_e24_g20_c64", "P2_e200_g20_c64", "P3_e600_g20_c64"]),
                         ("12,800 ev/s (C=256, 20 ms)", ["P5_e24_g20_c256", "P4_e200_g20_c256"])):
        for v in ("httpx_decode", "aiohttp_decode", "aiohttp_design", "httpx_raw", "aiohttp_raw"):
            pts = [(scen[k][0], data[k][v]["thread_us_median"]) for k in labels if data[k] and v in data[k]]
            if len(pts) < 2:
                continue
            a, b = fit(pts)
            fits[(rate, v)] = (a, b)
            print(f"- {rate} {v}: a = {a:,.0f} us per request, b = {b:,.1f} us per event "
                  f"(points {', '.join(f'E={e}:{c:,.0f}' for e, c in pts)})")
    print()
    print(f"## Planning distribution {DIST} -> mean E = {E_MEAN:g} events per streamed response")
    print("| rate | client | CPU ms / streamed request at mean E | vCPU at 100k RPS | $/month at 100k RPS | "
          "RPS per vCPU (dispatch scope) | RPS per $1,000-month (dispatch scope) |")
    print("|---|---|---|---|---|---|---|")
    for (rate, v), (a, b) in fits.items():
        if v not in ("httpx_decode", "aiohttp_design"):
            continue
        c_s = (a + b * E_MEAN) / 1e6
        vcpu = 100_000 * c_s / UTIL
        print(f"| {rate} | {v} | {c_s * 1e3:.2f} | {vcpu:,.0f} | ${vcpu * USD_PER_VCPU_MONTH:,.0f} | "
              f"{UTIL / c_s:,.0f} | {1000 / USD_PER_VCPU_MONTH * UTIL / c_s:,.0f} |")
    print()
    # Reviewer A reconciliation
    ra = HERE / "bench" / "out" / "A_reconcile.txt"
    if ra.exists():
        print("## Reviewer A harness (lock-step pacing, ~100 B events, process CPU, raw reads), aiohttp 3.14.5")
        rows: dict[tuple[str, str], list[float]] = {}
        reads: dict[tuple[str, str], float] = {}
        for line in ra.read_text().splitlines():
            gap = line.split()[0].split("=")[1]
            j = json.loads(line[line.index("{"):])
            rows.setdefault((gap, j["variant"]), []).append(j["cpu_us_per_req"])
            reads[(gap, j["variant"])] = j["reads_per_req"]
        for (gap, v), vals in sorted(rows.items()):
            print(f"- gap {gap} ms {v}: {', '.join(f'{x:,.0f}' for x in vals)} us/req, reads/req {reads[(gap, v)]}")
    # dev-profile full path
    dp = HERE / "devpath" / "out"
    if dp.exists():
        print()
        print("## develop dev-profile full path (gateway process CPU per qualified streamed request)")
        for f in sorted(dp.glob("D*_*.result.json")):
            r = json.loads(f.read_text())
            print(f"- {r['label']}: E={r['events']} ok={r['qualified_ok']}/{r['requests']} "
                  f"{r['cpu_ms_per_qualified_request']:.2f} ms/request ({r['cpu_us_per_event']:.0f} us/event), "
                  f"util {r['gateway_util']}, statuses {r['load']['statuses']}")
        for f in sorted(dp.glob("C1_e*.json")):
            d = json.loads(f.read_text())
            for r in d["results"]:
                print(f"- dispatch only, one stream at a time, {d['label']} {r['variant']}: "
                      f"{r['thread_us_median']:,.0f} us/request")


if __name__ == "__main__":
    main()
