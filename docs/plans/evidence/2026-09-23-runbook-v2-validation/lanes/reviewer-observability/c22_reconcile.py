#!/usr/bin/env python3
"""reviewer-observability: correctness run c22-035 — client x-rv-disposition vs gateway disposition counters vs
provider canary hits (all phases), and x-rv-output / canaries at the client for output injections."""
import sys, json
from collections import Counter
from pathlib import Path
import orjson
sys.path.insert(0, str(Path(__file__).resolve().parent))
import recompute as R
run = Path.home() / "rv-evidence-raw/proto-bench-unit/runs/c22-035"
by_rid, calls, n2r, n = R.load_provider(sorted(run.glob("*/prov")))
cnt = Counter()
for d in sorted(run.glob("*/lg")):
    with R.open_any(R.find(d, "requests.jsonl")) as fh:
        for line in fh:
            c = orjson.loads(line)
            disp = c.get("disp") or "none"
            cnt[f"disp_{disp}"] += 1
            p = by_rid.get(c["rid"])
            cls = c.get("cls") or "?"
            if p is not None:
                cnt[f"prov_call_disp_{disp}"] += 1
                if p.get("canary_hits"):
                    cnt[f"prov_canary_disp_{disp}_cls_{cls}"] += 1
            elif disp in ("ALLOW", "REDACT"):
                cnt[f"no_prov_call_but_disp_{disp}_status_{c.get('status')}"] += 1
            if disp == "BLOCK" and p is not None:
                cnt["BLOCK_with_provider_call"] += 1
            out_hits = [h for h in (c.get("canary_hits") or []) if str(h).startswith("out.")]
            if out_hits:
                cnt["client_output_canary"] += 1
            if c.get("inject"):
                cnt[f"inject_{c['inject']}_status_{c.get('status')}"] += 1
gw = json.loads((Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent / "out/unit/c22-035.gw.whole.json").read_text())
print(json.dumps({"client": dict(sorted(cnt.items())), "provider_records": n,
                  "gw_counts": {k: v for k, v in gw["worker"]["count"].items() if k.startswith(("disposition", "provider_calls", "audit", "admitted", "shed", "redaction", "guard_unavail"))}}, indent=1))
