"""Functional look at v1 SSE release pattern (client arrival times of content frames). Not a measurement."""
import http.client, json, sys, time
key = open(sys.argv[1]).read().strip()
for rep in range(int(sys.argv[2]) if len(sys.argv) > 2 else 3):
    body = {"model": "synth-1", "stream": True, "max_tokens": 60, "user": f"rvsmoke-t-{rep}",
            "messages": [{"role": "user", "content": f"Write a short paragraph about gardening, variant {rep}."}]}
    c = http.client.HTTPConnection("127.0.0.1", 8300, timeout=30)
    t0 = time.perf_counter()
    c.request("POST", "/v1/chat/completions", json.dumps(body), {"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    r = c.getresponse()
    buf = b""; arr = []
    while True:
        ch = r.read1(65536)
        if not ch: break
        now = (time.perf_counter() - t0) * 1000
        buf += ch
        while b"\n\n" in buf:
            fr, buf = buf.split(b"\n\n", 1)
            s = fr.decode(errors="replace")
            if s.startswith("data: ") and s[6:].strip() != "[DONE]":
                try: d = json.loads(s[6:])
                except Exception: continue
                txt = "".join(((x.get("delta") or {}).get("content") or "") for x in d.get("choices") or [])
                if txt: arr.append((round(now, 1), len(txt)))
    gaps = [round(arr[i][0] - arr[i-1][0], 1) for i in range(1, len(arr))]
    print(f"rep{rep}: n_content_frames={len(arr)} first={arr[0][0] if arr else None} last={arr[-1][0] if arr else None}")
    print("   arrivals(ms,chars):", arr[:12], "...")
    print("   gaps:", gaps[:40])
