"""Send every benign.jsonl text through v1 (JSON, tiny max_tokens) and tabulate dispositions.
Functional check of the v1 profile's false-positive behaviour on the harness's benign source."""
import asyncio, json, sys, collections
import aiohttp
key = open(sys.argv[1]).read().strip(); url = sys.argv[2]; corpus = sys.argv[3]; out = sys.argv[4]
rows = [json.loads(l) for l in open(corpus)]
async def one(sess, sem, r):
    async with sem:
        body = {"model": "synth-1", "max_tokens": 8, "user": "fp-" + r["id"],
                "messages": [{"role": "user", "content": r["text"]}]}
        async with sess.post(url, json=body, headers={"Authorization": "Bearer " + key}) as resp:
            d = await resp.json(content_type=None)
            pt = d.get("pipeline_trace") or {}
            err = d.get("error") if isinstance(d.get("error"), dict) else {}
            if not pt and isinstance(err, dict):
                pt = err.get("pipeline_trace") or {}
            acts = {s.get("name"): s.get("action") for s in pt.get("stages") or []}
            pol = [s for s in pt.get("stages") or [] if s.get("name") == "policy"]
            rules = (pol[0].get("matched_rules") if pol else None) or []
            return {"id": r["id"], "family": r["family"], "status": resp.status,
                    "final": pt.get("final_action") or (d.get("zeroshield") or {}).get("action"),
                    "policy": acts.get("policy"), "input_scan": acts.get("input_scan"),
                    "rules": [x.get("name") if isinstance(x, dict) else str(x) for x in rules][:5],
                    "text": r["text"]}
async def main():
    sem = asyncio.Semaphore(8)
    async with aiohttp.ClientSession() as sess:
        res = await asyncio.gather(*(one(sess, sem, r) for r in rows))
    with open(out, "w") as f:
        for x in res: f.write(json.dumps(x) + "\n")
    c = collections.Counter((x["status"], x["final"]) for x in res)
    print("dispositions:", dict(c))
    for x in res:
        if x["final"] not in ("allow",):
            print("  NON-ALLOW", x["status"], x["final"], x["policy"], x["rules"], "|", x["text"][:90])
asyncio.run(main())
