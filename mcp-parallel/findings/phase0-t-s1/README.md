# Phase 0.3 T-S1 — MB per in-flight stream

**Published number: 1.1 MB per in-flight stub stream (conservative cap 2 MB).**

This is **not** a capacity RPS claim. `capacity_eligible` is false (`stub_llm`, sidecar auth off, Tier-2 off). Do not quote `/health` throughput as RPS.

## Method

- **Where:** docker-compose local network `ai_mesh_firewall_default`. Sidecar `phase0-t-s1-gw` on `127.0.0.1:18300`. Shared `ai_mesh_firewall-gateway-1:8300` was **not** recreated and was **not** the load target.
- **Image:** `ai_mesh_firewall-gateway` with the working-tree `gateway/ai_mesh_gateway` bind-mounted (live image is 7 days old and lacks `loadtest_stub_duration_s`).
- **Stub:** `GATEWAY_LOADTEST_STUB_LLM=1`, `DURATION_S=3`, `TOK_PER_S=50`, `stream=true`. Completions used `id=chatcmpl-loadtest-stub`.
- **Caps:** `WEB_CONCURRENCY=1`, `--memory 1g`, `--cpus 1`, in-flight 4 then 8. Abort on OOM or error rate > 20%. `GATEWAY_AUTH_ENABLED=false`, `ENABLE_TIER2=false` (no Bedrock bill, isolate stream-hold memory).
- **Samples:** `docker stats --no-stream` plus cgroup `memory.current`.
- **Formula:** `(peak_cgroup_MB − idle_median_cgroup_MB) / inflight`.

Driver: `soak_inflight.py` (sidecar torn down in `finally`).

## Results [M]

| Run | In-flight | OK | Hold wall | Idle median | Peak | Delta | MB/stream | OOM |
|---|---|---|---|---|---|---|---|---|
| n4 | 4 × 3 waves | 12/12 | 3.77–4.23 s | 281.91 MB | 286.48 MB | 4.57 MB | **1.14** | no |
| n8 | 8 × 2 waves | 16/16 | ~3 s stub | 257.10 MB | 261.51 MB | 4.41 MB | **0.55** | no |

Absolute delta stayed ~4.5 MB at both concurrencies, so much of it is worker warmup/noise rather than a linear per-stream slab. SSE bodies were ~52 KiB/stream. Shared gateway cgroup after n4 was **1212 MB** (idle compose gateway, not the soak victim).

**Interpretation:** treat **1.1 MB/stream** as the n=4 observation and **2 MB/stream** as a conservative cap for later fleet sketches. Do not start Phase 4 on this as a precision constant.

## 0.1 live honesty (same folder)

`_health_honesty_live.json` — `MODE=health` against shared `http://127.0.0.1:8300`: 20/20 OK, `full_nine_stages: false`, `capacity_eligible: false`, reasons `mode_not_chat`, `not_full_nine_stages`, `not_unique_prompt`. The JSON contains a `/health` request rate; **that rate is not a capacity RPS**.

Live compose gateway still has `GATEWAY_LOADTEST_STUB_LLM=0` and an image without the 0.1 duration stub; stub ineligibility is proven by the honesty harness plus this sidecar (`stub_id_seen` 12 and 16).
