"""E2E P13 (GW12 'client vanishes without TCP close -> idle timeout', GW19 'bounded drain'):
a client opens an SSE stream, reads only the response headers, then stops reading (never closes).
1) Is the stream (and its provider connection) ever released?  2) Does SIGTERM drain within a bound?"""
import json, os, signal, socket, subprocess, sys, time
import httpx
EV = sys.argv[1]; LAUNCHER_PID = int(open(f"{EV}/e2e/.run/rvproto.pid").read())
body = json.dumps({"model": "gpt-4o-mini", "stream": True, "max_tokens": 200000,
                   "messages": [{"role": "user", "content": "tell me a long story"}]}).encode()
req = (b"POST /v1/chat/completions HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n"
       b"Authorization: Bearer sk-rv-org-b-0001\r\nx-synth-tokens: 200000\r\nx-synth-itl-ms: 0.2\r\nx-synth-ttft-ms: 1\r\n"
       b"x-request-id: p13-stalled\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
s = socket.create_connection(("127.0.0.1", 47400)); s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
s.sendall(req); hdr = s.recv(512); print("got headers:", hdr.split(b"\r\n")[0], "| now the client stops reading (socket stays open)")
def gauges():
    m = httpx.get("http://127.0.0.1:47400/metrics/all", timeout=5).json()
    pw = m["per_worker"]; pw = pw.values() if isinstance(pw, dict) else pw
    out = {}
    for w in pw:
        for k in ("active_streams", "inflight_requests"):
            out[k] = out.get(k, 0) + (w.get("gauge") or {}).get(k, 0)
    return out
for t in (5, 30, 60):
    time.sleep(t - (0 if t == 5 else (5 if t == 30 else 30)))
    print(f"t={t:>3}s gauges (sum over workers): {gauges()}")
workers = subprocess.run(["pgrep", "-P", str(LAUNCHER_PID)], capture_output=True, text=True).stdout.split()
print("SIGTERM -> launcher", LAUNCHER_PID, "workers", workers)
os.kill(LAUNCHER_PID, signal.SIGTERM); t0 = time.time()
while time.time() - t0 < 90:
    alive = [w for w in workers if os.path.exists(f"/proc/{w}")]
    if not alive: break
    time.sleep(1)
print(f"after {time.time()-t0:.0f}s: workers still alive = {alive}; launcher alive = {os.path.exists(f'/proc/{LAUNCHER_PID}')}")
s.close(); t1 = time.time()
while time.time() - t1 < 30 and any(os.path.exists(f"/proc/{w}") for w in workers): time.sleep(0.5)
print(f"client socket closed -> workers exited {time.time()-t1:.1f}s later: {[w for w in workers if os.path.exists(f'/proc/{w}')] or 'all exited'}")
