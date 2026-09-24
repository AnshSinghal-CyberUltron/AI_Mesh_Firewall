#!/usr/bin/env python3
"""Aggregate every experiment from evidence/raw (recomputed from raw samples) into summary JSON + CSV tables.

usage: analyze_all.py <evidence-dir>
writes: summary/latency_table.csv, summary/throughput_qsafe.csv, summary/cpu_latency.csv, summary/cpu_throughput.csv,
        summary/network_rtt.json, summary/summary.json
"""
import csv
import glob
import json
import re
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import analyze_ladder as AL  # noqa: E402
import pg2common as C  # noqa: E402


def model_of(p):
    m = re.search(r"Llama-Prompt-Guard-2-(22M|86M)", str(p))
    return f"PG2-{m.group(1)}" if m else "?"


def write_csv(path, rows, fields):
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def latency(ev):
    rows = []
    for f in sorted(glob.glob(str(ev / "raw/A/**/lat_*.json"), recursive=True)):
        d = json.load(open(f))
        tag = Path(f).stem.replace("lat_", "")
        for c in d["configs"]:
            h = C.summarize_ms(np.asarray(c["host_ns"]) / 1e6)
            dv = C.summarize_ms(np.asarray(c["devio_ns"]) / 1e6) if c.get("devio_ns") else {}
            rows.append({"exp": "A in-process ORT", "host": Path(f).parts[-3] if "g2-" in Path(f).parts[-3] else "",
                         "model": model_of(f), "backend": tag, "W": c["W"], "seq": c["seq"], "n": h["n"],
                         "p50": h["p50"], "p90": h["p90"], "p99": h["p99"], "max": h["max"], "devio_p50": dv.get("p50"),
                         "engine_build_s": c.get("cold_ready_s"), "file": str(Path(f).relative_to(ev))})
    for f in sorted(glob.glob(str(ev / "raw/[BC]/**/lat_rtt.json"), recursive=True)):
        d = json.load(open(f))
        exp = "B Triton loopback gRPC" if "/raw/B/" in f else "C Triton off-box gRPC (c4 -> G2, same zone)"
        for r in d["rows"]:
            h = C.summarize_ms(np.asarray(r["raw_ns"]) / 1e6)
            rows.append({"exp": exp, "model": model_of(f), "backend": "triton 26.05 TRT10.16 plan (1 profile 1-64, opt 16)",
                         "W": r["W"], "seq": 512, "n": h["n"], "p50": h["p50"], "p90": h["p90"], "p99": h["p99"],
                         "max": h["max"], "file": str(Path(f).relative_to(ev))})
    for f in sorted(glob.glob(str(ev / "raw/B/**/trtexec_times_W*.json"), recursive=True)):
        try:
            t = json.load(open(f))
            comp = np.asarray([x["computeMs"] for x in t])
        except Exception:
            continue
        W = int(re.search(r"_W(\d+)\.json$", f).group(1))
        h = C.summarize_ms(comp)
        rows.append({"exp": "trtexec GPU compute only", "model": model_of(f), "backend": Path(f).parent.name,
                     "W": W, "seq": 512, "n": h["n"], "p50": h["p50"], "p90": h["p90"], "p99": h["p99"], "max": h["max"],
                     "file": str(Path(f).relative_to(ev))})
    return rows


def throughput(ev):
    rows = []
    dirs = sorted({str(Path(p).parent) for p in glob.glob(str(ev / "raw/**/raw_*step*.npz"), recursive=True)})
    for d in dirs:
        r = AL.analyze(d)
        rel = Path(d).relative_to(ev)
        exp = {"A": "A in-process", "B": "B Triton loopback", "C": "C Triton off-box"}.get(rel.parts[1], rel.parts[1])
        best = max((s["achieved_wps"] for s in r["steps"] if s["completed"] == s["n"]), default=0)
        row = {"exp": exp, "model": model_of(d), "config": "/".join(rel.parts[3:]), "steps": len(r["steps"]),
               "max_achieved_wps_all_complete": best}
        for k, v in r["q_safe"].items():
            row[f"q_safe_{k}"] = v["q_safe_wps"]
            row[f"first_fail_{k}"] = v["first_failing_wps"]
        s0 = r["steps"][0] if r["steps"] else None
        row["lowest_step_wps"] = s0["offered_wps"] if s0 else None
        row["lowest_step_p50"] = s0["lat_ms"]["p50"] if s0 and s0["lat_ms"] else None
        row["lowest_step_p99"] = s0["lat_ms"]["p99"] if s0 and s0["lat_ms"] else None
        row["dir"] = str(rel)
        rows.append(row)
    return rows


def cpu(ev):
    lat, thr = [], []
    for f in sorted(glob.glob(str(ev / "raw/D/**/cpu_latency.json"), recursive=True)):
        d = json.load(open(f))
        host = Path(f).relative_to(ev).parts[2]
        for r in d["rows"]:
            s = C.summarize_ms(np.asarray(r["raw_ns"]) / 1e6)
            lat.append({"host": host, "model": model_of(f), "backend": r["backend"], "threads": r["threads"], "W": r["W"],
                        "n": s["n"], "p50": s["p50"], "p99": s["p99"], "max": s["max"]})
    for f in sorted(glob.glob(str(ev / "raw/D/**/cpu_throughput.json"), recursive=True)):
        d = json.load(open(f))
        host = Path(f).relative_to(ev).parts[2]
        for r in d["rows"]:
            thr.append({"host": host, "model": model_of(f), "backend": r["backend"], "W": r["W"], "procs": r["procs"],
                        "threads": r["threads"], "windows_per_s": r["windows_per_s"],
                        "windows_per_s_per_vcpu": r["windows_per_s_per_vcpu"],
                        "call_p50": r["call_latency_ms"]["p50"], "call_p99": r["call_latency_ms"]["p99"]})
    return lat, thr


def network(ev):
    out = {}
    for d in sorted(glob.glob(str(ev / "raw/C/**/net"), recursive=True)):
        res = {}
        p = Path(d) / "ping.txt"
        if p.exists():
            m = re.search(r"rtt min/avg/max/mdev = ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+)", p.read_text())
            if m:
                res["icmp_ping_ms"] = dict(zip(["min", "avg", "max", "mdev"], map(float, m.groups())))
        for f in sorted(Path(d).glob("netperf_tcprr_*.txt")):
            lines = [l for l in f.read_text().splitlines() if l and l[0].isdigit()]
            if lines:
                v = lines[-1].split(",")
                res[f.stem] = {"min_us": float(v[0]), "mean_us": float(v[1]), "p50_us": float(v[2]), "p90_us": float(v[3]),
                               "p99_us": float(v[4]), "max_us": float(v[5]), "req_bytes": int(v[8]), "resp_bytes": int(v[9])}
        for f in sorted(Path(d).glob("sockperf_*.txt")):
            t = f.read_text()
            pc = {k: float(v) for k, v in re.findall(r"percentile (\d+\.\d+) =\s+([\d.]+)", t)}
            res[f.stem] = {"p50_us": pc.get("50.000"), "p99_us": pc.get("99.000"), "p99.9_us": pc.get("99.900")}
        out[str(Path(d).relative_to(ev))] = res
    return out


def main():
    ev = Path(sys.argv[1])
    outd = ev / "summary"
    outd.mkdir(exist_ok=True)
    lat = latency(ev)
    write_csv(outd / "latency_table.csv", lat, ["exp", "host", "model", "backend", "W", "seq", "n", "p50", "p90", "p99", "max",
                                                "devio_p50", "engine_build_s", "file"])
    thr = throughput(ev)
    tf = ["exp", "model", "config", "steps", "lowest_step_wps", "lowest_step_p50", "lowest_step_p99",
          "max_achieved_wps_all_complete"] + [f"{a}_p99<={t}ms" for t in (5, 8, 10, 15, 20) for a in ("q_safe", "first_fail")] + ["dir"]
    write_csv(outd / "throughput_qsafe.csv", thr, tf)
    cl, ct = cpu(ev)
    write_csv(outd / "cpu_latency.csv", cl, ["host", "model", "backend", "threads", "W", "n", "p50", "p99", "max"])
    write_csv(outd / "cpu_throughput.csv", ct, ["host", "model", "backend", "W", "procs", "threads", "windows_per_s",
                                                  "windows_per_s_per_vcpu", "call_p50", "call_p99"])
    net = network(ev)
    (outd / "network_rtt.json").write_text(json.dumps(net, indent=1))
    (outd / "summary.json").write_text(json.dumps({"latency": lat, "throughput": thr, "cpu_latency": cl,
                                                   "cpu_throughput": ct, "network": net}, indent=1))
    print(f"latency rows {len(lat)}, ladder dirs {len(thr)}, cpu lat {len(cl)}, cpu thr {len(ct)}, net {len(net)}")


if __name__ == "__main__":
    main()
