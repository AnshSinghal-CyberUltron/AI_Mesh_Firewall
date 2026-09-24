"""Send N corpus prompts (harness JSONL, {{RVNONCE}} substituted) through v1 as JSON with tiny
max_tokens; tabulate dispositions and the policy rules that fired. Functional FP check."""
import asyncio, json, sys, collections
import aiohttp
key = open(sys.argv[1]).read().strip(); url = sys.argv[2]; corpus = sys.argv[3]; out = sys.argv[4]
rows = [json.loads(l) for l in open(corpus)]
async def one(sess, sem, i, r):
    async with sem:
        msgs = [dict(m, content=m["content"].replace("{{RVNONCE}}", f"(ref-fp-check-{i})")) for m in r["messages"]]
        body = {"model": "synth-1", "max_tokens": 8, "messages": msgs}
        async with sess.post(url, json=body, headers={"Authorization": "Bearer " + key}) as resp:
            d = await resp.json(content_type=None)
            pt = d.get("pipeline_trace") or {}
            err = d.get("error") if isinstance(d.get("error"), dict) else {}
            if not pt and isinstance(err, dict):
                pt = err.get("pipeline_trace") or {}
            pol = [s for s in pt.get("stages") or [] if s.get("name") == "policy"]
            p0 = pol[0] if pol else {}
            rules = p0.get("matched_rules") or []
            names = sorted({(x.get("name") or x.get("rule_name") or x.get("rule_id")) if isinstance(x, dict) else str(x) for x in rules})
            return {"id": r["id"], "status": resp.status, "final": pt.get("final_action"),
                    "policy_action": p0.get("action"), "rules": names[:8],
                    "policies": [x.get("name") if isinstance(x, dict) else str(x) for x in (p0.get("matched_policies") or [])][:6]}
async def main():
    sem = asyncio.Semaphore(8)
    async with aiohttp.ClientSession() as sess:
        res = await asyncio.gather(*(one(sess, sem, i, r) for i, r in enumerate(rows)))
    with open(out, "w") as f:
        for x in res: f.write(json.dumps(x) + "\n")
    print("n", len(res), "dispositions:", dict(collections.Counter((x["status"], x["final"]) for x in res)))
    rc = collections.Counter(n for x in res if x["final"] != "allow" for n in x["rules"])
    pc = collections.Counter(n for x in res if x["final"] != "allow" for n in x["policies"])
    print("rules firing on non-allow:", rc.most_common(12))
    print("policies firing on non-allow:", pc.most_common(12))
asyncio.run(main())
