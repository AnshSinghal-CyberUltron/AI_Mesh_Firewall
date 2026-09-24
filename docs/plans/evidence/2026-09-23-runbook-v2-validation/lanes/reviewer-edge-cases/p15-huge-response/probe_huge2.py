"""P15b: sample worker VmRSS every 10 ms while a large upstream JSON body passes through."""
import json, os, sys, threading, time
import httpx
OUT, PIDFILE = sys.argv[1], sys.argv[2]
launcher = int(open(PIDFILE).read().strip())
pids = [int(k) for k in os.popen(f"pgrep -P {launcher}").read().split()]
def rss():
    return sum(int(l.split()[1]) for p in pids for l in open(f"/proc/{p}/status") if l.startswith("VmRSS:")) // 1024
rows = []
for mb in (64, 192, 384):
    samples = []; stop = threading.Event()
    def smp():
        while not stop.is_set():
            samples.append(rss()); time.sleep(0.01)
    th = threading.Thread(target=smp); base = rss(); th.start()
    t = time.perf_counter()
    with httpx.Client(timeout=600) as c:
        r = c.post("http://127.0.0.1:8480/v1/chat/completions", json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]},
                   headers={"authorization": "Bearer sk-rv-org-b-0001", "x-synth-edge": "huge-json", "x-synth-mb": str(mb), "x-synth-ttft-ms": "0"})
        n = len(r.content)
    dt = time.perf_counter() - t; time.sleep(0.2); stop.set(); th.join()
    row = {"upstream_body_MiB": mb, "status": r.status_code, "client_body_MiB": round(n / 2**20, 1), "elapsed_s": round(dt, 2),
           "worker_rss_MiB_baseline": base, "worker_rss_MiB_peak_during": max(samples), "delta_MiB": max(samples) - base,
           "delta_over_body": round((max(samples) - base) / mb, 2)}
    rows.append(row); print(json.dumps(row), flush=True)
json.dump(rows, open(OUT, "w"), indent=1)
