"""Diagnostic: per-stage latency (pipeline_trace) for different prompt shapes at idle (sequential)."""
import http.client, json, random, sys, time
key = open(sys.argv[1]).read().strip()
host = sys.argv[2] if len(sys.argv) > 2 else "127.0.0.1"
words = open("/usr/share/dict/words").read().split() if False else None
rng = random.Random(3)
VOC = [w for w in ("time person year way day thing man world life hand part child eye woman place work week case point "
       "government company number group problem fact river garden window market music paper science summer winter "
       "morning evening village forest mountain ocean library museum kitchen bridge station harbor meadow orchard "
       "lantern pencil ribbon basket blanket candle ladder button mirror pillow saddle tunnel valley canyon island").split()]
FILL = ("the committee reviewed the quarterly plan and agreed on a steady schedule for the garden project "
        "while volunteers organised tools seeds and water rotas for the spring season ").split()
def natural(nw):
    out = []
    for i in range(nw):
        out.append(rng.choice(VOC))
        if i % 12 == 11: out[-1] += "."
    return " ".join(out)
shapes = {
    "short": "Summarize the benefits of regular code review in two sentences.",
    "rep_4k": " ".join(FILL[j % len(FILL)] for j in range(650)),
    "nat_4k": natural(650),
    "rep_1k": " ".join(FILL[j % len(FILL)] for j in range(160)),
    "nat_1k": natural(160),
}
for rep in range(2):
    for name, text in shapes.items():
        for stream in (False,):
            body = {"model": "synth-1", "max_tokens": 16, "stream": stream, "user": f"diag-{name}-{rep}",
                    "messages": [{"role": "user", "content": f"(case {name} {rep} {time.time()}) " + text}]}
            c = http.client.HTTPConnection(host, 8300, timeout=60)
            t0 = time.perf_counter()
            c.request("POST", "/v1/chat/completions", json.dumps(body), {"Authorization": "Bearer " + key, "Content-Type": "application/json"})
            r = c.getresponse(); d = json.loads(r.read()); wall = (time.perf_counter() - t0) * 1000
            pt = d.get("pipeline_trace") or {}
            st = {s["name"]: s.get("latency_ms") for s in pt.get("stages") or []}
            print(f"{name:7s} rep{rep} chars={len(text):5d} http={r.status} wall={wall:7.1f} total={pt.get('total_latency_ms')} "
                  f"pre={pt.get('t_addon_pre_ms')} post={pt.get('t_addon_post_ms')} overhead={pt.get('overhead_ms')} "
                  f"policy={st.get('policy')} input_scan={st.get('input_scan')} out_guard={st.get('output_guardrail')} model_out={st.get('model_output')}")
