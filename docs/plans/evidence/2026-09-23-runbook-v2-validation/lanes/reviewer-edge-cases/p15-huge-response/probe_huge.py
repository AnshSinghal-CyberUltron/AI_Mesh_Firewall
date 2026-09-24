"""P15: non-stream provider response size is unbounded (a BYOK upstream is tenant-chosen).
Worker VmHWM (peak RSS) and wall time for 16 / 64 / 192 MiB JSON bodies from the upstream."""
import json, os, sys, time
import httpx
OUT, PIDFILE = sys.argv[1], sys.argv[2]
launcher = int(open(PIDFILE).read().strip())
pids = [int(k) for k in os.popen(f"pgrep -P {launcher}").read().split()]
def mem(field):
    return sum(int(l.split()[1]) for p in pids for l in open(f"/proc/{p}/status") if l.startswith(field)) // 1024
rows = []
with httpx.Client(timeout=600) as c:
    for mb in (16, 64, 192):
        h0, r0 = mem("VmHWM:"), mem("VmRSS:")
        t = time.perf_counter()
        r = c.post("http://127.0.0.1:8480/v1/chat/completions", json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]},
                   headers={"authorization": "Bearer sk-rv-org-b-0001", "x-synth-edge": "huge-json", "x-synth-mb": str(mb), "x-synth-ttft-ms": "0"})
        dt = time.perf_counter() - t
        row = {"upstream_body_MiB": mb, "status": r.status_code, "client_body_MiB": round(len(r.content) / 2**20, 1),
               "elapsed_s": round(dt, 2), "worker_peak_rss_MiB_before": h0, "worker_peak_rss_MiB_after": mem("VmHWM:"),
               "worker_rss_MiB_before": r0, "worker_rss_MiB_after": mem("VmRSS:")}
        rows.append(row); print(json.dumps(row), flush=True)
json.dump(rows, open(OUT, "w"), indent=1)
