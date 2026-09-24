"""CALIBRATION ONLY (not evidence for any latency/capacity claim): crude open-loop generator used to
pick the ladder range before the shared harness is ready. Constant arrivals, 70% SSE, unique prompts."""
import asyncio, json, random, sys, time
import aiohttp
key = open(sys.argv[1]).read().strip(); url = sys.argv[2]; rate = float(sys.argv[3]); dur = float(sys.argv[4]); out = sys.argv[5]
texts = [json.loads(l)["text"] for l in open(sys.argv[6])]
FILL = ("the committee reviewed the quarterly plan and agreed on a steady schedule for the garden project "
        "while volunteers organised tools seeds and water rotas for the spring season ").split()
rng = random.Random(7)
def prompt(i):
    n_words = rng.randint(80, 800)
    return f"[ref {i}] " + rng.choice(texts) + " " + " ".join(FILL[j % len(FILL)] for j in range(n_words))
recs = []
async def one(sess, i, sched, stream):
    late = time.perf_counter() - sched
    body = {"model": "synth-1", "stream": stream, "max_tokens": rng.randint(50, 400), "user": f"calib-{i}",
            "messages": [{"role": "user", "content": prompt(i)}]}
    t0 = time.perf_counter(); first = None; st = -1; err = ""
    try:
        async with sess.post(url, json=body, headers={"Authorization": "Bearer " + key}) as r:
            st = r.status
            if stream:
                async for line in r.content:
                    if line.startswith(b"data: ") and b'"content"' in line and first is None:
                        first = time.perf_counter()
            else:
                await r.read(); first = time.perf_counter()
    except Exception as e:
        err = type(e).__name__
    t1 = time.perf_counter()
    recs.append({"i": i, "stream": stream, "status": st, "err": err, "late_ms": late * 1000,
                 "first_ms": (first - t0) * 1000 if first else None, "total_ms": (t1 - t0) * 1000})
async def main():
    conn = aiohttp.TCPConnector(limit=0, keepalive_timeout=60)
    async with aiohttp.ClientSession(connector=conn, timeout=aiohttp.ClientTimeout(total=120)) as sess:
        start = time.perf_counter() + 0.5; tasks = []; n = int(rate * dur)
        for i in range(n):
            sched = start + i / rate
            d = sched - time.perf_counter()
            if d > 0: await asyncio.sleep(d)
            tasks.append(asyncio.create_task(one(sess, i, sched, (i % 10) < 7)))
        await asyncio.gather(*tasks)
    with open(out, "w") as f:
        for r in recs: f.write(json.dumps(r) + "\n")
    ok = [r for r in recs if r["status"] == 200]
    def pct(v, q):
        v = sorted(v); return round(v[min(len(v) - 1, int(q * len(v)))], 1) if v else None
    lat = [r["late_ms"] for r in recs]
    js = [r["total_ms"] for r in ok if not r["stream"]]; ss = [r["first_ms"] for r in ok if r["stream"] and r["first_ms"]]
    print(f"rate={rate} n={len(recs)} ok={len(ok)} errs={len(recs)-len(ok)} late_p99={pct(lat,.99)} late_max={max(lat):.1f}"
          f" | json_total p50={pct(js,.5)} p99={pct(js,.99)} | sse_first p50={pct(ss,.5)} p99={pct(ss,.99)}")
asyncio.run(main())
