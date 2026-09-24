"""Multi-worker propagation of the GW05 plan push and GW06 kill-switch on a live unit.
  python tools/hook_check.py <base> <ssh-prefix-for-unit>   (tools run on the unit against its Redis)"""
import json
import subprocess
import sys
import time

import httpx

base, ssh = sys.argv[1], sys.argv[2]
REMOTE = "cd ~/rv/rvproto && ~/rv/venv/bin/python tools/{} redis://127.0.0.1:6379/0 {}"


def remote(tool: str, args: str) -> dict:
    out = subprocess.run(f"{ssh} '{REMOTE.format(tool, args)}'", shell=True, check=True, capture_output=True, text=True)
    return json.loads(out.stdout.strip().splitlines()[-1])


def probe(key: str) -> tuple[float, httpx.Response]:
    with httpx.Client(base_url=base, timeout=30) as c:  # fresh connection => any of the workers
        r = c.post("/v1/chat/completions", headers={"authorization": f"Bearer {key}", "x-synth-ttft-ms": "0",
                                                    "x-synth-itl-ms": "0"},
                   json={"model": "rv-synth-1", "max_tokens": 2, "messages": [{"role": "user", "content": "hi"}]})
    return time.time(), r


def last_stale(key: str, since: float, is_new, window_s: float = 6.0) -> tuple[float | None, int, int]:
    """Probe continuously; return (time of the LAST response still showing the old state, relative to
    `since`), probes, stale probes. That is an upper bound on convergence across all workers probed."""
    last, n, stale = None, 0, 0
    while time.time() - since < window_s:
        t, r = probe(key)
        n += 1
        if not is_new(r):
            stale += 1
            last = t - since
    return last, n, stale


def workers() -> int:
    return int(httpx.get(f"{base}/_rv/contract", timeout=10).json()["workers"])


out = {"workers": workers()}
pub = remote("plan_update.py", "org-b b-2 --rule B.pii.in --mode enforce --action redact")
last, n, stale = last_stale("sk-rv-org-b-0001", pub["published_at"],
                            lambda r: r.headers.get("x-rv-plan-version") == "b-2")
out["plan_push"] = {"last_old_version_response_s": last, "probes": n, "stale_probes": stale}
remote("plan_update.py", "org-b b-1 --rule B.pii.in --mode monitor --action redact")
time.sleep(2)
flip = remote("killswitch.py", "org org-b on")
last, n, stale = last_stale("sk-rv-org-b-0001", flip["set_at"], lambda r: r.status_code == 503)
out["org_killswitch"] = {"last_served_response_s": last, "probes": n, "stale_probes": stale}
out["org_a_unaffected"] = all(probe("sk-rv-org-a-0001")[1].status_code == 200 for _ in range(24))
remote("killswitch.py", "org org-b off")
per_worker = httpx.get(f"{base}/metrics/all", timeout=10).json()["per_worker"]
out["per_worker_plan_age_s_max"] = round(max(w["gauge"]["plan_snapshot_age_seconds"] for w in per_worker), 3)
out["per_worker_ks_age_s_max"] = round(max(w["gauge"]["killswitch_snapshot_age_seconds"] for w in per_worker), 3)
out["per_worker_reporting"] = len(per_worker)
print(json.dumps(out))
