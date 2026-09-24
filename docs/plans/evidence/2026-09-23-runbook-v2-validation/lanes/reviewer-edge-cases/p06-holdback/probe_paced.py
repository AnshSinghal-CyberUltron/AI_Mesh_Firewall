"""P06b: same holdback shape at a realistic provider pace (5 ms/chunk): 16 KiB base64 blob in 4-char
chunks (~20 s at the provider) vs the same length of benign words; bystander (org-b) latency
distribution on the same worker, and worker CPU."""
import asyncio, json, sys
sys.argv = [sys.argv[0], "http://127.0.0.1:8480", sys.argv[1], sys.argv[2]]
import probe_holdback as P
async def main():
    rows = []
    for scen, n in (("words", 16384), ("base64-long", 16384)):
        r = await P.one(scen, n, 4, 5, True)
        rows.append(r); print(json.dumps(r), flush=True)
    json.dump(rows, open(sys.argv[3], "w"), indent=1)
asyncio.run(main())
