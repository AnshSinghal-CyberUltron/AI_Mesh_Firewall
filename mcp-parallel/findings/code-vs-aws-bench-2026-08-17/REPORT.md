# Production isolation benchmark — is gateway code the RPS wall?

**Date:** 2026-08-17 05:33–05:40 UTC  
**Target:** prod EC2 `i-07a9d65b103ac1a87` (c8g.2xlarge, 8 vCPU, ~16 GiB), public IP 35.154.124.71  
**Direct:** `http://ec2-35-154-124-71.ap-south-1.compute.amazonaws.com:8300`  
**NLB:** `https://aimeshgateway.zeroshield.ai`  
**Driver:** GCP box → `scripts/perf/gateway_pipeline_bench.py` (unique `TOK-<hex>` prompts, `model=auto`, `max_tokens=16` except scan-only)  
**Evidence:** `mcp-parallel/findings/code-vs-aws-bench-2026-08-17/`  
**Verdict:** **Live 9-stage RPS is not limited by FastAPI/gunicorn/Python request handling.** It is limited by **AWS Bedrock wait time** (Tier-2 input scan + output-guard Bedrock). Subtracting those waits raises completed-pipeline ok_rps by **~11× at the same inflight**. The HTTP stack on the same process serves **7,631 RPS** on `/health`. **100k RPS of live 9-stage is not claimed.**

Gateway was restored after the run: `GATEWAY_LOADTEST_STUB_LLM=0`, `ENABLE_TIER2=true`, `GATEWAY_TIER2_SAMPLE_RATE=1.0`, `GATEWAY_OUTPUT_GUARD_ENABLED=true`, `ROUTING_ADJUDICATOR_ALWAYS=true`, `GATEWAY_CIRCUIT_BREAKER_ENABLED=true`, `WEB_CONCURRENCY=6` (pre-bench value). Operator Haiku kill-switch keys were **not** deleted.

---

## 1. What was measured (dependency subtraction)

Same client, same unique prompts, same prod box. Only the **wait sources** changed.

| Run | What still runs | What is removed |
|---|---|---|
| **H** `/health` | gunicorn + Starlette + JSON | auth, Redis policy, scan, LLM |
| **L** live 9-stage | everything | nothing (official path) |
| **S** `max_tokens=0` scan-only | auth, Redis, policy, **Tier-2 Bedrock** | BYOK inference, output guard |
| **B** `GATEWAY_LOADTEST_STUB_LLM=1` | T2 Bedrock + routing + **stub** completion + output-guard Bedrock | customer BYOK RTT only |
| **C** code-only | auth, Redis, policy, **Tier-1 regex**, deterministic routing, stub completion | T2, adjudicator Bedrock, output-guard Bedrock, BYOK |

**C recreate env (then restored):** `WEB_CONCURRENCY=16`, `GATEWAY_LOADTEST_STUB_LLM=1`, `ENABLE_TIER2=false`, `GATEWAY_TIER2_SAMPLE_RATE=0`, `ROUTING_ADJUDICATOR_ALWAYS=false`, `GATEWAY_OUTPUT_GUARD_ENABLED=false`, circuit breaker off for the sat window. Redis org TPM/burst lifted for B/C only, then snap restored. `risk_score` kept at 0.

Live **L** ran on the pre-bench worker pin (`WEB_CONCURRENCY=6`). That does **not** explain the live RPS wall: at 32 in-flight the pipeline is waiting ~3.2 s on AWS, so extra workers cannot create RPS (Little’s Law: RPS ≈ inflight / latency).

---

## 2. Headline numbers (measured today)

| Run | Inflight | ok_rps | error_rate | p50 wall | Dominant stage p50 |
|---|---:|---:|---:|---:|---|
| **H** health direct | 64 | **7630.59** | 0 | 7.2 ms | n/a (no pipeline) |
| **H** health direct | 256 | 6164.21 | 0 | 32.4 ms | n/a |
| **H** health NLB TLS | 64 | **7592.30** | 0.01% | 6.9 ms | n/a |
| **L** live 9-stage | 32 | **7.95** | 0 | **3196 ms** | input_scan 1550 + model 638 + guard 901 |
| **S** scan-only (T2 live) | 32 | 17.57 | 1.6% | 1541 ms | input_scan 1518 (T2); model/guard skip |
| **S** scan-only | 128 | 41.51 | 0.85% | 2519 ms | input_scan 2151 |
| **B** stub BYOK, T2+guard live | 32 | **10.63** | 0 | 2481 ms | input_scan 1537 + guard 882; model_output **0** |
| **B** stub BYOK | 128 | **31.91** | 0.41% | 3276 ms | T2 1864 + guard 942 |
| **C** code-only | 32 | **84.85** | 0.68% | **289 ms** | T1 input_scan 105; model_output 0; guard 0 |
| **C** code-only | 128 | 80.51 | 3.2% | 1300 ms | T1 351; **422 catalog** starts |
| **C** code-only NLB | 128 | 80.87 | 2.5% | 1363 ms | same as direct |
| **C** code-only | 512–2048 | 71–76 | 14–21% | 4.2–15.5 s | queue + **422** `no_provider_configured` |

**Ratio at matched inflight=32:** code-only **84.85 / 7.95 = 10.7×** live ok_rps.  
**Health vs live:** **7631 / 7.95 ≈ 960×**.

---

## 3. Stage-level proof (single unique-prompt probes, HTTP 200)

These are idle/low-load traces (not saturation). They show **where the milliseconds go**.

### L — live 9-stage (AWS on)

| Stage | ms | Where |
|---|---:|---|
| auth | 1.1 | local Redis |
| rate_limit | 2.1 | local Redis |
| policy | 4.8 | local Redis/CPU |
| **input_scan** | **1770.4** | **Bedrock Tier-2** |
| kill_switch | 0.9 | local Redis |
| model_routing | 4.0 | local (fastpath) |
| model_input | 0.5 | local |
| **model_output** | **902.2** | **org BYOK** |
| **output_guardrail** | **999.5** | **Bedrock** |
| **Local code (auth+rate+policy+KS+routing+model_in)** | **~13 ms** | |
| **AWS waits (T2+BYOK+guard)** | **~3672 ms** | |

After restore, a second live probe was still AWS-shaped: T2 1591 + BYOK 946 + guard 897.

### S — scan-only (`max_tokens=0`)

model_input / model_output / output_guardrail = **skip 0 ms**. input_scan still **1342 ms** (Tier-2). Completing the pipeline without BYOK/guard **does not** remove the live wall; T2 remains.

### B — stub BYOK, Bedrock T2 + output guard still on

model_output **0.0–0.2 ms** (stub works). input_scan **1566 ms**, output_guardrail **950 ms**. Removing customer inference **does not** unlock RPS; Bedrock still holds the request ~2.5 s.

### C — AWS subtracted

| Stage | ms |
|---|---:|
| auth | 0.9 |
| rate_limit | 0.0 |
| policy | 4.5 |
| **input_scan (Tier-1 only)** | **68.5** |
| kill_switch | 0.8 |
| model_routing | 12.5 |
| model_output (stub) | 0.2 |
| output_guardrail | 0.0 |

Wall drops from **~3.2 s → ~87 ms** on a quiet probe. Under 32 in-flight, T1 queues to p50 **105 ms** input_scan / **289 ms** client — still an order of magnitude below live.

---

## 4. Little’s Law (why live cannot be “slow Python”)

`RPS ≈ in_flight / latency`.

| Path | Inflight | p50 | Predicted RPS | Measured ok_rps |
|---|---:|---:|---:|---:|
| Live L | 32 | 3.196 s | 10.0 | **7.95** |
| Scan-only S | 32 | 1.541 s | 20.8 | **17.57** |
| Stub-BYOK B | 32 | 2.481 s | 12.9 | **10.63** |
| Code-only C | 32 | 0.289 s | 110.8 | **84.85** |
| Health H | 64 | 0.0072 s | 8889 | **7631** |

Measured RPS tracks **wait time**, not a hidden O(n²) Python tax. If FastAPI/gunicorn were the 8 RPS ceiling, `/health` on the **same workers** could not do 7.6k RPS.

---

## 5. Postgres / control plane are a different plane

Chat hot path does **not** query Postgres per completion. Control and PG were probed on-box during the window:

| Probe | Idle | After C@512 |
|---|---|---|
| `SELECT 1` (postgres-1) | **0.128 ms** | **0.122 ms** |
| Control `:8100/api/health/` (20×) | p50 **1.71 ms**, 20/20 200 | p50 **1.44 ms**, 20/20 200 |
| postgres RSS | 358 MiB / 2.5 GiB | 420 MiB / 2.5 GiB |
| redis RSS | 13 MiB / 1 GiB | 75 MiB / 1 GiB |
| gateway RSS | 1.3 GiB / 12 GiB | 3.4 GiB / 12 GiB |

Postgres is not the chat limiter. Redis is on the hot path (auth + config) and stayed small. Gateway RAM at 2048 in-flight was **3.47 / 12 GiB** — not RAM-bound. (docker `CPUPerc` samples here are **after** each wave, so they are cooldown, not peak-in-wave. Do not treat 4–13% as a CPU-saturation proof.)

---

## 6. What *is* still “code” (honest residuals)

### 6.1 Tier-1 scan ~70–105 ms

With Bedrock skipped, remaining local work is Tier-1 regex/policy (~70 ms quiet, ~105 ms at 32 in-flight). That caps **code-only** completed chat around **~85 ok_rps** on this 8-vCPU instance. That is **not** the live 8 RPS story. Live spends **~1.5 s in Tier-2 Bedrock** on the same `input_scan` stage.

Code-only **did not scale** with more in-flight (80–85 ok_rps plateau at 32→2048). Extra inflight only grew queue delay (p50 0.29 s → 15 s) plus errors. So after AWS is removed, the next ceiling is **local T1/policy + worker/catalog issues**, still ~11× the live number at equal inflight.

### 6.2 HTTP 422 `no_provider_configured` (real code bug)

Some gunicorn workers boot with an empty catalog (`config_sync_loaded: false` was visible on `/health` after restore on the hit worker). Last-good is not applied on those workers → 422 under concurrency.

| C inflight | 422 count |
|---:|---:|
| 32 | 0 |
| 128 | 13 |
| 512 | 150 |
| 1024 | 300 |
| 2048 | 317 |

This **is** a code/sync defect. It is **not** what produces 8 RPS on healthy 200s. Healthy 200s are wait-bound on Bedrock.

### 6.3 HTTP 503 `kill_switch_active` Model `Haiku`

Operator kill-switch (ttl=-1) was left intact. At C@512: 9; C@1024: 21. Not a performance root cause.

### 6.4 HTTP 400 `content_filter`

Small unique-prompt FP (6/878 at C@32). Noise, not the wall.

---

## 7. Comparison to the earlier live peak (2026-08-14)

Wave-2 unique 9-stage on this same host: **28.92 ok_rps @ 128 in-flight**, **31.02 @ 512** with 11.5% errors. Today’s **B @ 128 = 31.91 ok_rps** with BYOK stubbed. That matches: once T2+guard are live, stubbing the customer model barely moves the ceiling, because BYOK was only ~0.6–0.9 s of a ~3.2 s pipeline.

Today’s **L @ 32 = 7.95** is the low-inflight, zero-error point (Little: 32/3.2 s ≈ 10). Raising inflight to 128 on the **live** path is how you reach ~29 RPS — and you pay ~3 s latency. Code-only at 32 already beats that 29 RPS **and** cuts latency to 0.29 s.

---

## 8. What this does **not** prove

- **Not 100k RPS live 9-stage.** 100k × 3.5 s ≈ 350k in-flight; this 16 GiB box cannot hold that. Even `/health` is **7.6k**, not 100k, on one c8g.2xlarge.
- **Not “Tier-1 is free.”** T1 is ~70 ms and ~85 RPS locally. Optimizing T1 is a later, smaller lever than Bedrock quota/concurrency.
- **Not “NLB is the bottleneck.”** NLB `/health` 7592 vs direct 7631; NLB code-only @128 80.9 vs direct 80.5.
- **Not a Postgres finding.** Chat does not wait on `SELECT`. Control health stayed ~1.5 ms.

---

## 9. Conclusion

On production, the completed 9-stage pipeline is **I/O-wait on AWS Bedrock (Tier-2 + output guard)** plus a smaller org-BYOK wait. Local gateway stages that are actually Python/Redis (auth, rate limit, policy, kill-switch, routing, stubbed completion) sum to **~13 ms** on a live probe versus **~3.2 s** end-to-end.

**Proof statement:** the same gateway process that delivers **7.95 ok_rps** live 9-stage at 32 in-flight delivers **84.85 ok_rps** when Bedrock T2/guard/adjudicator and BYOK are subtracted, and **7631 RPS** on `/health`. Code is not the live RPS blocker. AWS model wait (and quota/concurrency behind it) is. Remaining code work that *is* real: empty-catalog 422 on a subset of workers, and ~70 ms Tier-1 CPU.

---

## 10. Restore check

Post-run `docker exec` env: stub **0**, T2 **true**, sample **1.0**, output guard **true**, adjudicator **true**, CB **true**, workers **6**. Direct and NLB `/health` **200**. Live unique probe after restore: T2 1591 + BYOK 946 + guard 897 (AWS path back).
