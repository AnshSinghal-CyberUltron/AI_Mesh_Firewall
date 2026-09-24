"""Functional smoke for v1 SSE: headers, frame shapes, terminal pipeline_trace. Not a measurement."""
import http.client, json, sys, time
key = open(sys.argv[1]).read().strip()
host, port = sys.argv[2], int(sys.argv[3])
body = {"model": "synth-1", "stream": True, "max_tokens": 30, "user": "rvsmoke-s-0001",
        "stream_options": {"include_usage": True},
        "messages": [{"role": "user", "content": "Write two sentences about keeping a tidy workshop."}]}
c = http.client.HTTPConnection(host, port, timeout=30)
t0 = time.perf_counter()
c.request("POST", "/v1/chat/completions", json.dumps(body), {"Authorization": "Bearer " + key,
          "Content-Type": "application/json", "x-request-id": "rvsmoke-s-0001"})
r = c.getresponse()
print("status", r.status)
for k, v in r.getheaders():
    if k.lower().startswith(("x-", "content-type")):
        print("  hdr", k, "=", v[:100])
buf = b""; frames = []; text = ""
while True:
    ch = r.read1(65536)
    if not ch:
        break
    buf += ch
    while b"\n\n" in buf:
        fr, buf = buf.split(b"\n\n", 1)
        frames.append((time.perf_counter() - t0, fr))
print("frames", len(frames))
for t, fr in frames[:3] + frames[-4:]:
    s = fr.decode(errors="replace")
    print("  %.1fms" % (t * 1000), s[:160].replace("\n", "\\n"))
for t, fr in frames:
    s = fr.decode(errors="replace")
    if s.startswith("data: ") and s[6:].strip() != "[DONE]":
        try:
            d = json.loads(s[6:])
        except Exception:
            print("  NONJSON", s[:80]); continue
        for chh in d.get("choices") or []:
            text += (chh.get("delta") or {}).get("content") or ""
        if d.get("pipeline_trace"):
            pt = d["pipeline_trace"]
            print("  TRACE final_action", pt.get("final_action"), "total", pt.get("total_latency_ms"),
                  "addon_pre", pt.get("t_addon_pre_ms"), "addon_post", pt.get("t_addon_post_ms"), "ttft", pt.get("ttft_ms"))
            print("   stages", [(s2.get("name"), s2.get("action"), s2.get("latency_ms")) for s2 in pt.get("stages") or []])
            print("   frame keys", sorted(d.keys()))
print("text", repr(text[:200]))
