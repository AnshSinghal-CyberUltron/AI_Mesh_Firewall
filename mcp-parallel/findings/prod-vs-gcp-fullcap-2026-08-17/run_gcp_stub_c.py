#!/usr/bin/env python3
"""GCP code-only ladder after injecting loadtest stub into the running image."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_compare import (  # noqa: E402
    CODE_LEVELS,
    GCP_GW,
    OUTDIR,
    PY,
    ensure_gcp_key,
    lift,
    recreate,
    restore_limits,
    run_bench,
    run_probe,
    wait_health,
)

SNAP_PATH = OUTDIR / "gcp_stub_redis_snap.json"


def main() -> int:
    gcp_key = ensure_gcp_key()
    extra = {
        "API_KEY": gcp_key,
        "GATEWAY": GCP_GW,
        "MODEL": "auto",
        "UNIQUE_PROMPT": "1",
        "PROMPT": "What is the capital of France? Reply with the city name only.",
        "TIMEOUT_S": "180",
        "MAX_TOKENS": "16",
    }
    snap = lift("gcp", gcp_key, True)
    SNAP_PATH.write_text(json.dumps(snap), encoding="utf-8")
    os.chmod(SNAP_PATH, 0o600)
    restored = False
    try:
        probe = run_probe("gcp_stub", extra)
        recs = probe.get("records") or []
        stages = (recs[0].get("stages") if recs else None) or []
        model = next((s.get("latency_ms") for s in stages if s.get("stage") == "model_output"), None)
        cid = recs[0].get("id") if recs else None
        print(f"stub_probe id={cid} model_output_ms={model} status={[r.get('status') for r in recs]}", flush=True)
        if model is None or float(model) > 50:
            raise RuntimeError(f"stub not active: model_output={model} id={cid}")
        if cid and "loadtest-stub" not in str(cid):
            print(f"WARN completion id is not loadtest-stub: {cid}", flush=True)
        results = []
        for inflight, w, c, dur in CODE_LEVELS:
            r = run_bench(f"gcp_C_stub_if{inflight}", "gcp", "chat", w, c, dur, extra)
            results.append(r)
            if (r.get("error_rate") or 0) >= 0.6:
                print("stop escalate", flush=True)
                break
        (OUTDIR / "gcp_stub_c_summary.json").write_text(
            json.dumps(
                [
                    {
                        "label": r.get("_label"),
                        "inflight": r.get("_inflight"),
                        "ok_rps": r.get("ok_rps"),
                        "error_rate": r.get("error_rate"),
                        "p50": (r.get("client_latency_ms") or {}).get("p50"),
                        "codes": r.get("codes"),
                        "p50_input_scan": ((r.get("stage_latency_ms") or {}).get("input_scan") or {}).get("p50"),
                        "p50_model_output": ((r.get("stage_latency_ms") or {}).get("model_output") or {}).get("p50"),
                    }
                    for r in results
                ],
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    finally:
        print("RESTORE gcp live WEB=16", flush=True)
        try:
            restore_limits("gcp", snap)
            recreate("gcp", "live")
            wait_health(GCP_GW, 25)
            restored = True
        except Exception as exc:
            print(f"gcp restore err {exc!r}", flush=True)
        print(json.dumps({"restored": restored}), flush=True)
    return 0 if restored else 1


if __name__ == "__main__":
    raise SystemExit(main())
