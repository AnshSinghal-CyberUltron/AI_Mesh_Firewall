"""GW14: drive the REAL stream_orchestration.build_stream_trace_frame (-> _rebuilt_stream_trace,
line 683 model_output_ms = duration_ms - ttft_ms) with synthetic timestamps:
  gateway pre-provider work 20 ms, provider TTFT 900 ms, token streaming after first token 300 ms.
Also drive pipeline_trace.PipelineStageTimer the way proxy_chat does (start at main.py:7085,
first mark 'auth' at :7691) with a simulated body parse.
"""
import json
import time

from pipeline_trace import PipelineStageTimer, build_pipeline_trace, finalize_stage_metrics
from stream_orchestration import StreamLaunchContext, StreamRunMetrics, build_stream_trace_frame

stage_metrics = {"auth_ms": 2.0, "kill_switch_ms": 1.0, "rate_limit_ms": 1.0,
                 "policy_ms": 4.0, "tier1_ms": 8.0, "tier2_ms": 0.0}
kwargs = dict(prompt="hello", forwarded_prompt="hello", stage_metrics=stage_metrics,
              final_action="allow", http_status=200, zeroshield={}, requested_model="gpt-4o-mini")
base = build_pipeline_trace(response_text="", **kwargs)
build_pipeline_trace(response_text="warm", **kwargs)  # warm caches so wall-clock below is not polluted

now = time.perf_counter()
provider_start = now - 1.200          # provider call opened 1.2 s ago
first_token = provider_start + 0.900  # TTFT = 900 ms
req_start = provider_start - 0.020    # 20 ms of gateway work before the provider call

ctx = StreamLaunchContext(body={"model": "gpt-4o-mini"}, redacted_prompt=None, org_slug="acme",
                          model="gpt-4o-mini", start_time=req_start)
ctx.trace_build_kwargs = kwargs
m = StreamRunMetrics(provider_start_ts=provider_start, first_token_ts=first_token)
m.output_snippet = "Hello there"

frame = json.loads(build_stream_trace_frame(ctx, m, {}, pipeline_trace_base=base)[len("data: "):])
pt = frame["pipeline_trace"]
print("synthetic truth: gateway pre-provider 20 ms | provider TTFT 900 ms | streaming after 1st token 300 ms")
print("stage latencies (ms):", {s["name"]: s.get("latency_ms") for s in pt["stages"]})
print("total_latency_ms:", pt["total_latency_ms"], "| stage_latency_sum_ms:", pt["stage_latency_sum_ms"],
      "| overhead_ms:", pt["overhead_ms"], "| ttft_ms:", pt.get("ttft_ms"))
print("addon split:", {k: pt.get(k) for k in pt if k.startswith("t_addon") or k.startswith("t_")})

# Timer semantics: PipelineStageTimer(start) with start taken inside the handler.
t = PipelineStageTimer(time.perf_counter())
time.sleep(0.004)  # stands in for `await request.json()` + body validation (main.py:7132-7690)
auth_ms = t.mark_segment_end("auth")
print("PipelineStageTimer first segment labelled 'auth' measured:", auth_ms, "ms (only body parse happened in it)")
fm = finalize_stage_metrics({"auth_ms": auth_ms}, time.perf_counter() - 0.05, ptimer=t)
print("finalize_stage_metrics overhead_ms clamp: max(0, wall - stages - telemetry) ->", fm["overhead_ms"])
