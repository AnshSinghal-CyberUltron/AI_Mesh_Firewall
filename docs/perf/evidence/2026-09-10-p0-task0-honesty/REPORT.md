# P0.0 REPORT

This pack overwrites on each `p0_run.sh` invocation.

## Instrument notes (this SHA)

- Firewall_Tax is non-stream `pipeline_trace.total_latency_ms − model_output_ms`.
- Streaming was **not** run. On this SHA stream `total_latency_ms` is `now − provider_start_ts` (pre-model stages excluded). Stream `total − model_output` **aliases** `ttft_ms` (rounding / setdefault). That is not Firewall_Tax.
- `stage_sum` may exceed `total_latency_ms`. Neither stage-sum nor the TTFT alias is the 20 ms comparator.
- `honesty.full_nine_stages` is two timers (`input_scan` p50>0 and `output_guardrail` p50>0), not proof all nine stages ran. Counted_Samples require each of nine `action != skip`.
- Wall is `pipeline_trace.total_latency_ms`, labelled N/A-not-tax. Not httpx wall_ms.
- Grounding is **pinned off** (`GATEWAY_OUTPUT_GROUNDING_ENABLED=false`).
- Stub RPS is invalid for capacity (`capacity_eligible=false`).
- Do **not** quote aimesh-dev 13.6 RPS / p99 17.60 ms as this SHA’s result.
- Block / redact / size / negative tax (if present) is **N/A-not-20ms**.
- Size cell must be 4096 non-repetitive letters (digit runs → PCI; repeated tokens → input_scan DoS).
- Org inference catalog is harness-seeded (`gpt-4o-mini` dummy key). Stub still intercepts LiteLLM; empty routing is 422 `no_provider_configured`.
- Negative cell (scans-off / empty policies) is **not run** in this pack.
