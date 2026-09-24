"""E2E P14 (GW11/GW01 wire contract): provider 4xx/429 through rvproto vs direct, with the official OpenAI SDK
(openai==2.38.0, default max_retries=2).  Provider = fakeprov.py (counts calls per x-request-id)."""
import collections, json, sys, time, uuid
import openai
CALLS = sys.argv[1]
def calls(rid):
    return sum(1 for l in open(CALLS) if json.loads(l)["rid"] == rid)
targets = {"direct -> provider": ("http://127.0.0.1:47190/v1", "x"), "via rvproto": ("http://127.0.0.1:47400/v1", "sk-rv-org-b-0001")}
for mode in ("400", "429"):
    for name, (base, key) in targets.items():
        rid = f"p14-{mode}-" + uuid.uuid4().hex[:8]
        cli = openai.OpenAI(base_url=base, api_key=key)
        t0 = time.time()
        try:
            cli.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": "hi"}],
                                        extra_headers={"x-synth-mode": mode, "x-request-id": rid})
            res = "success?!"
        except openai.APIStatusError as e:
            res = f"{type(e).__name__} status={e.status_code} code={getattr(e, 'code', None)} retry-after={e.response.headers.get('retry-after')}"
        time.sleep(0.3)
        print(f"provider {mode} | {name:<18} | SDK saw: {res:<80} | provider calls={calls(rid)} | elapsed {time.time()-t0:.1f}s")
