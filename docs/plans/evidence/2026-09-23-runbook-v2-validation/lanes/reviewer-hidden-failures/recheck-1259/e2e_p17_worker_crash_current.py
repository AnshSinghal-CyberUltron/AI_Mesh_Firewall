"""E2E P17: one worker dies (e.g. kernel OOM kill -> SIGKILL).  Benchmarked serve.py launcher: any worker exit
with a non-zero code makes the launcher SIGTERM every other worker and exit; workers are never respawned.
Also: the org token budget drawn into worker-local leases is never returned (GW06 'lease TTL reclaims')."""
import os, signal, subprocess, sys, time
import httpx, redis
EV = sys.argv[1]; L = int(open(f"{EV}/e2e/.run/rvproto.pid").read())
r = redis.Redis.from_url("redis://127.0.0.1:36379/0")
r.set("rv:budget:org-a", 1_000_000)
with httpx.Client(timeout=10) as c:   # draw leases on both workers
    for i in range(20):
        with httpx.Client(timeout=10) as c2:
            c2.post("http://127.0.0.1:47400/v1/chat/completions", headers={"authorization": "Bearer sk-rv-org-a-0001"},
                    json={"model": "gpt-4o-mini", "max_tokens": 10, "messages": [{"role": "user", "content": "hi"}]})
before = int(r.get("rv:budget:org-a"))
workers = [l.split()[0] for l in subprocess.run(["ps", "-o", "pid=,cmd=", "--ppid", str(L)], capture_output=True, text=True).stdout.splitlines() if "--worker" in l]
print(f"launcher {L} workers {workers}; org-a budget left in Redis after 20 small requests: {before:,} of 1,000,000 "
      f"(={1_000_000-before:,} tokens now sitting in worker-local leases)")
os.kill(int(workers[0]), signal.SIGKILL); t0 = time.time(); print(f"SIGKILL worker {workers[0]} (simulated OOM kill)")
time.sleep(3)
alive = [w for w in workers if os.path.exists(f"/proc/{w}")]
print(f"+{time.time()-t0:.0f}s: surviving workers={alive}  launcher alive={os.path.exists(f'/proc/{L}')}")
try:
    resp = httpx.get("http://127.0.0.1:47400/healthz", timeout=3); print("healthz:", resp.status_code)
except Exception as e:
    print("healthz: connection failed ->", type(e).__name__)
print(f"org-a budget in Redis now: {int(r.get('rv:budget:org-a')):,} (leases of dead workers are never returned)")
