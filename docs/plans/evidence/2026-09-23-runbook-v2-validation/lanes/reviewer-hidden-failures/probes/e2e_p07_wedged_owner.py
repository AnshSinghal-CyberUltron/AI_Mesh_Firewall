"""E2E P07: owner topology (benchmarked build), the guard owner process is alive but wedged (SIGSTOP stands in
for a hung CUDA/TRT call, a GIL-holding TRT session rebuild, or a host stall).  org-a (FAIL_CLOSED) and org-b
(FAIL_OPEN) requests with an 8 s client timeout; /readyz polled throughout; then SIGCONT."""
import concurrent.futures as cf, os, signal, sys, time
import httpx
OWNER = int(sys.argv[1])
def req(key):
    t0 = time.time()
    try:
        r = httpx.post("http://127.0.0.1:47400/v1/chat/completions", timeout=8.0,
                       headers={"authorization": f"Bearer {key}"},
                       json={"model": "gpt-4o-mini", "max_tokens": 2, "messages": [{"role": "user", "content": "hello"}]})
        return f"HTTP {r.status_code} {r.headers.get('x-rv-stages') or r.json().get('error', {}).get('code')} in {time.time()-t0:.2f}s"
    except httpx.TimeoutException:
        return f"client TIMEOUT after {time.time()-t0:.1f}s (no response)"
print("baseline org-a:", req("sk-rv-org-a-0001"))
os.kill(OWNER, signal.SIGSTOP); print(f"SIGSTOP guard owner pid {OWNER}")
with cf.ThreadPoolExecutor(8) as ex:
    futs = {ex.submit(req, k): k for k in ["sk-rv-org-a-0001", "sk-rv-org-b-0001"] * 3}
    time.sleep(2)
    rz = [httpx.get("http://127.0.0.1:47400/readyz", timeout=3).status_code for _ in range(4)]
    print("readyz during the wedge:", rz)
    for f in cf.as_completed(futs):
        print(f"  {futs[f][6:11]}: {f.result()}")
os.kill(OWNER, signal.SIGCONT); print("SIGCONT"); time.sleep(1)
print("after SIGCONT org-a:", req("sk-rv-org-a-0001"))
