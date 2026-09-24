#!/usr/bin/env python3
"""Per run, measurement-phase requests split into: ok (HTTP 200 complete), policy blocks (4xx content_filter;
benign corpus => every one is a false positive), and infra errors by class (timeouts, 5xx by code, 422 by code,
transport). Plus v1 audit dispositions. usage: error_strata.py RUN [RUN...]"""
import json, sys
from collections import Counter
from pathlib import Path
sys.path.insert(0, "/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/harness")
from analyze import open_any, find_raw, loads  # noqa: E402
E = Path("/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/v1-bench/runs")
print("| run | measured | ok (200 complete) | policy blocks (benign FP) | infra errors | infra error classes | v1 audit dispositions |")
print("|---|---|---|---|---|---|---|")
for run in sys.argv[1:]:
    R = E / run
    lgd = next(R.glob("rv-v1-lg-*/lg"))
    n = ok = pb = 0
    infra = Counter()
    for line in open_any(find_raw(lgd, "requests.jsonl")):
        if not line.strip():
            continue
        r = loads(line)
        if r.get("ph") != 2:
            continue
        n += 1
        st, err, det = r.get("status"), r.get("err"), (r.get("err_detail") or "")
        if st == 200 and not err and r.get("done_seen"):
            ok += 1
        elif st in (400, 403) and "content_filter" in det:
            pb += 1
        else:
            code = det.split("code=")[-1].split()[0] if "code=" in det else ""
            infra[f"{err or 'http_'+str(st)}{(':' + code) if code else ''}"] += 1
    ad = R / "lg-v1aug" / "v1_adapter_report.json"
    disp = json.loads(ad.read_text()).get("dispositions") if ad.exists() else {}
    print(f"| {run} | {n} | {ok} | {pb} | {sum(infra.values())} ({100*sum(infra.values())/max(n,1):.2f}%) | "
          f"{', '.join(f'{k} {v}' for k, v in infra.most_common())} | {disp} |")
