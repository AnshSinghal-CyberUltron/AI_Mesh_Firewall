"""P19: unauthenticated key spray -> shared-state amplification. Invalid keys are never cached
(identity.py caches only found principals), so every request with a random key costs one Redis HGET
before the 401. Measures Redis-side HGET calls (INFO commandstats) per invalid-key request."""
import json, sys, uuid, asyncio, time
import aiohttp, redis
R = redis.Redis(port=16479)
def hget_calls():
    s = R.info("commandstats").get("cmdstat_hget", {})
    return s.get("calls", 0)
async def main(n):
    h0 = hget_calls(); t = time.perf_counter(); codes = {}
    async with aiohttp.ClientSession() as s:
        async def one():
            async with s.post("http://127.0.0.1:8480/v1/chat/completions", json={"model": "m", "messages": [{"role": "user", "content": "x"}]},
                              headers={"authorization": f"Bearer sk-{uuid.uuid4().hex}"}) as r:
                codes[r.status] = codes.get(r.status, 0) + 1
        for i in range(0, n, 100):
            await asyncio.gather(*[one() for _ in range(100)])
    dt = time.perf_counter() - t; h1 = hget_calls()
    out = {"invalid_key_requests": n, "statuses": codes, "redis_hget_calls": h1 - h0, "hget_per_request": round((h1 - h0) / n, 3), "wall_s": round(dt, 2), "rps": round(n / dt)}
    print(json.dumps(out)); json.dump(out, open(sys.argv[1], "w"), indent=1)
asyncio.run(main(5000))
