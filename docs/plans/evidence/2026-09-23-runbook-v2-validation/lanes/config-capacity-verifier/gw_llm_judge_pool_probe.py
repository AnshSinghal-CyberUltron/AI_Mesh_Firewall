"""llm_judge.py:25 DEFAULT_THREAD_POOL_SIZE=2 (main.py:6677 constructs LLMJudge(model=...) with the default):
10 concurrent RAG-query judge calls against a Bedrock stub that takes 200 ms each."""
import asyncio, io, json, time
from llm_judge import LLMJudge

class SlowBedrock:
    def invoke_model(self, **kw):
        time.sleep(0.2)
        body = json.dumps({"content": [{"text": json.dumps({"is_injection": False, "confidence": 0.1, "attack_type": "none", "reasoning": "ok"})}]})
        return {"body": io.BytesIO(body.encode())}

async def main():
    j = LLMJudge(); j._client = SlowBedrock()
    t0 = time.perf_counter()
    res = await asyncio.gather(*[j.judge(f"query {i}") for i in range(10)])
    dt = (time.perf_counter() - t0) * 1000
    print(f"pool max_workers={j._executor._max_workers}; 10 concurrent judge() x 200 ms Bedrock -> {dt:.0f} ms wall "
          f"(parallel would be ~200 ms); verdict models: {sorted(set(r.judge_model for r in res))}")
asyncio.run(main())
