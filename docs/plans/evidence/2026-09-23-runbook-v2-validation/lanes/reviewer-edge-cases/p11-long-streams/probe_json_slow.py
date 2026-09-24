"""P11b: NON-stream completion whose provider needs 125 s (long output / reasoning) — via gateway vs direct."""
import json, sys, time, httpx
rows = []
def call(base, key, label):
    t = time.perf_counter()
    try:
        r = httpx.post(f"{base}/v1/chat/completions", json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "long essay"}]},
                       headers={"authorization": f"Bearer {key}", "x-synth-ttft-ms": "125000", "x-synth-tokens": "4"}, timeout=300)
        return {"label": label, "status": r.status_code, "elapsed_s": round(time.perf_counter() - t, 2), "body_head": r.text[:200]}
    except Exception as e:
        return {"label": label, "exception": f"{type(e).__name__}: {e}", "elapsed_s": round(time.perf_counter() - t, 2)}
import concurrent.futures as cf
with cf.ThreadPoolExecutor(2) as ex:
    fs = [ex.submit(call, "http://127.0.0.1:8480", "sk-rv-org-b-0001", "json-125s-via-gw"), ex.submit(call, "http://127.0.0.1:18481", "direct", "json-125s-direct")]
    rows = [f.result() for f in fs]
for r in rows: print(json.dumps(r))
json.dump(rows, open(sys.argv[1], "w"), indent=1)
