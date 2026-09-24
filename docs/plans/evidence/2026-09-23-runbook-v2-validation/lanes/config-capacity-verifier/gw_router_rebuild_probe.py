"""Cost of the synchronous LiteLLM Router rebuild that reload_models() performs on the event
loop when reload_models_now fires from proxy_chat (llm_router.py reload_models -> LiteLLMRouter(...))."""
import time, statistics
from litellm import Router as LiteLLMRouter
for n in (10, 100, 400):
    ml = [{"model_name": f"org{i%20}::m{i}", "litellm_params": {"model": "openai/gpt-4o-mini", "api_key": "sk-test", "api_base": "http://127.0.0.1:9"}} for i in range(n)]
    ts = []
    for _ in range(3):
        t0 = time.perf_counter(); LiteLLMRouter(model_list=ml, num_retries=2, timeout=120); ts.append((time.perf_counter()-t0)*1000)
    print(f"deployments={n}: LiteLLMRouter(...) construction median {statistics.median(ts):.1f} ms (blocks the event loop; synchronous)")
