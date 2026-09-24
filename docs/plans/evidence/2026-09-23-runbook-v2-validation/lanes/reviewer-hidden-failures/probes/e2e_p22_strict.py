"""E2E P22 (GW13): tenant selects streaming_mode=strict_withhold ('no content released until the whole response
is inspected').  The plan compiles and is served (version a-strict) but egress never reads plan.streaming_mode."""
import json, sys, time
sys.path.insert(0, sys.argv[1])
import openai, redis
from rvproto.plan.fixtures import org_a
from rvproto.plan.compiler import compile_plan
r = redis.Redis.from_url("redis://127.0.0.1:36379/0")
doc = org_a("a-strict"); doc["streaming_mode"] = "strict_withhold"
print("compile_plan accepted:", compile_plan(doc).streaming_mode)
r.set("rv:plan:org-a", json.dumps(doc)); r.hset("rv:plan_versions", "org-a", "a-strict"); r.publish("rv:plan:updates", "org-a"); time.sleep(1.5)
cli = openai.OpenAI(base_url="http://127.0.0.1:47400/v1", api_key="sk-rv-org-a-0001", max_retries=0)
raw = cli.chat.completions.with_raw_response.create(model="gpt-4o-mini", messages=[{"role": "user", "content": "hi"}], stream=True)
print("x-rv-plan-version:", raw.headers.get("x-rv-plan-version"))
t0 = time.perf_counter(); arrivals = []
for ev in raw.parse():
    for c in ev.choices:
        if c.delta.content:
            arrivals.append((round(1e3 * (time.perf_counter() - t0), 1), c.delta.content))
print(f"content events: {len(arrivals)}; first at {arrivals[0][0]} ms, last at {arrivals[-1][0]} ms "
      f"-> content released incrementally while the response was still being generated")
r.set("rv:plan:org-a", json.dumps(org_a())); r.hset("rv:plan_versions", "org-a", "a-1"); r.publish("rv:plan:updates", "org-a")
