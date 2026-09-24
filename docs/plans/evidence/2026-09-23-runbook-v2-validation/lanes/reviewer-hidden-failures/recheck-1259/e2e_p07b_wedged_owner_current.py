"""E2E P07b on the CURRENT tree: SIGSTOP the (real) guard owner; requests + /readyz over 10 s; then SIGCONT."""
import os, signal, sys, time
import httpx
OWNER = int(sys.argv[1])
def req(key):
    t0 = time.time()
    try:
        r = httpx.post("http://127.0.0.1:47400/v1/chat/completions", timeout=8.0, headers={"authorization": f"Bearer {key}"},
                       json={"model": "gpt-4o-mini", "max_tokens": 2, "messages": [{"role": "user", "content": "hello"}]})
        return f"HTTP {r.status_code} disp={r.headers.get('x-rv-disposition')} stages={r.headers.get('x-rv-stages')} in {time.time()-t0:.2f}s"
    except httpx.TimeoutException:
        return f"client TIMEOUT after {time.time()-t0:.1f}s"
print("baseline:", req("sk-rv-org-a-0001"))
os.kill(OWNER, signal.SIGSTOP); t0 = time.time(); print(f"SIGSTOP owner {OWNER}")
for k in range(int(sys.argv[2]) if len(sys.argv) > 2 else 10):
    ry = [httpx.get("http://127.0.0.1:47400/readyz", timeout=3).status_code for _ in range(4)]
    print(f"t={time.time()-t0:4.1f}s readyz={ry} | org-a: {req('sk-rv-org-a-0001')} | org-b: {req('sk-rv-org-b-0001')}")
    time.sleep(max(0, (k + 1) * 1.0 - (time.time() - t0)))
os.kill(OWNER, signal.SIGCONT); time.sleep(2); print("after SIGCONT:", req("sk-rv-org-a-0001"))
