# Prod vs this VM — full-uncap comparison

**Date:** 2026-08-17  
**Question:** If we run the same benchmark and load test at **full capacity, no software caps**, on production and on this 16-vCPU / 60 GiB / claimed-100k-IOPS VM, is **the prod server the only blocker**?

**Short answer:**  
- **Live 9-stage chat (Tier-2 + routing + BYOK + output guard): no.** Both boxes wait on AWS. This VM is **slower** on live unique-prompt traces because the routing adjudicator is a Bedrock round-trip from outside `ap-south-1` (~3.1 s vs ~4 ms on prod).  
- **AWS-subtracted code-only chat (stub LLM, T2 off, guard off): partly.** The 8-vCPU / 16 GiB prod instance is the local ceiling (~**85 ok_rps**). This 16-vCPU VM reaches ~**124 ok_rps** at the same 32 in-flight (~**1.47×**). That is still far below `/health` (7–9k RPS) and is **not** 100k RPS.  
- **HTTP `/health`:** FastAPI/gunicorn is not the live 8–29 RPS wall on either host.

**100k RPS of live 9-stage is not claimed on either machine.**

Evidence: this directory plus `mcp-parallel/findings/code-vs-aws-bench-2026-08-17/` (same day, prod isolation). Driver: `scripts/perf/gateway_pipeline_bench.py` with unique `TOK-<hex>` prompts, `model=auto`, `max_tokens=16`. Client always this GCP box.

---

## 1. Machines (measured, not brochure)

| | **Prod** | **This VM (GCP)** |
|---|---|---|
| Role | Official live gateway | Dev / driver host |
| Instance | `c8g.2xlarge` `i-07a9d65b103ac1a87` ap-south-1 | 16 vCPU guest |
| CPU | 8× Graviton, `aarch64` | 16× `INTEL(R) XEON(R) PLATINUM 8581C @ 2.30GHz`, `x86_64` |
| Host RAM | ~15 GiB, **no swap** | ~58 GiB |
| Gateway cgroup | NanoCpus=0, **Memory=12 GiB** | NanoCpus=0, **Memory=0 (uncapped)** |
| Disk | NVMe ~150 G | NVMe 512 G; **100k IOPS claimed, not fio-measured** |
| `nproc` in gateway | 8 | 16 |
| Live `WEB_CONCURRENCY` | **6** (at-rest pin) | **16** |
| Bypass `WEB_CONCURRENCY` | **16** | **16** |
| Direct gateway | `http://ec2-35-154-124-71.ap-south-1.compute.amazonaws.com:8300` | `http://127.0.0.1:8300` |
| Public | `https://aimeshgateway.zeroshield.ai/` (NLB TLS) | local compose only |

Chat hot path does **not** wait on disk. IOPS cannot explain live 9-stage RPS. Do not treat 100k IOPS as a throughput proof.

---

## 2. What “full bypass” means (applied on both)

Same env on both gateways for code-only:

```
WEB_CONCURRENCY=16
GATEWAY_LOADTEST_STUB_LLM=1
ENABLE_TIER2=false
GATEWAY_TIER2_SAMPLE_RATE=0
ROUTING_ADJUDICATOR_ALWAYS=false
GATEWAY_OUTPUT_GUARD_ENABLED=false
GATEWAY_CIRCUIT_BREAKER_ENABLED=false
```

Plus Redis: org TPM/burst lifted (or `rate_limit_enabled=false`), `output_scan_enabled=false`, API-key `risk_score=0`. Unique prompts. Restored after.

**Gotcha (this VM):** compose `environment:` must pin `WEB_CONCURRENCY` in an override YAML. A shell `export` does not enter the container because base `docker-compose.yml` has no `WEB_CONCURRENCY` key. Prod overlay does.

**Gotcha (this VM):** the local Docker gateway **image did not contain** `loadtest_stub_llm_enabled()`. Setting `GATEWAY_LOADTEST_STUB_LLM=1` was a no-op until `llm_router.py` was patched into the running container. First GCP “code-only” waves (`gcp_C_if32.json` etc.) still called real BYOK (~0.8–1.0 s `model_output`) and are **invalid as bypass**. Valid GCP bypass files are `gcp_C_stub_if*.json` and `probe_gcp_stub.json` (`id=chatcmpl-loadtest-stub`, `model_output≈0`).

Prod image already had the stub (`probe_prod_bypass.json`: `model_output=0.0`, `id=chatcmpl-loadtest-stub`).

---

## 3. Live 9-stage (AWS ON) — unique-prompt probes

Idle/low-load traces. They show **where milliseconds go**, not saturation RPS.

| Stage | Prod (ms) | This VM (ms) | Where |
|---|---:|---:|---|
| auth + rate_limit + policy + KS + model_input | **~7** | **~18** | local Python/Redis |
| **input_scan (Tier-2)** | **1716** | **878** | **Bedrock** |
| **model_routing** | **3.8** | **3131** | prod = weighted fastpath; **GCP = Policy Adjudicator RTT to Bedrock in ap-south-1** |
| **model_output (BYOK)** | **899** | **836** | org inference |
| **output_guardrail** | **863** | **839** | **Bedrock** |
| Wall | **~3.5 s** | **~5.7 s** | |

Local Python/Redis on a live probe is **~7–18 ms** on both. The rest is AWS (and on this VM, a long adjudicator hop).

**Live saturation (prod, 2026-08-14 + this morning):**

| Run | Inflight | ok_rps | p50 |
|---|---:|---:|---:|
| Live 9-stage L (this morning) | 32 | **7.95** | 3196 ms |
| Wave-2 unique 9-stage (2026-08-14) | 128 | **28.92** | healthy |
| Wave-2 unique 9-stage (2026-08-14) | 512 | **31.02** | 11.5% errors |

Little’s Law: `RPS ≈ inflight / latency`. 32 / 3.2 s ≈ 10; measured 7.95. Extra workers cannot manufacture RPS while each request holds AWS for ~3 s.

**This bigger VM cannot win live 9-stage** while the adjudicator and guards are Bedrock in Mumbai and this host is not. A 16-vCPU box in another region is the **wrong** place to prove “prod hardware is the live wall.”

---

## 4. `/health` — HTTP stack ceiling (full uncap, WEB=16)

Client = this VM. Prod `/health` is cross-Internet; GCP `/health` is localhost **and** shares the same 16 CPUs with the server.

| Inflight | Prod (isolation, 2026-08-17 morning) | This VM (this compare) |
|---:|---:|---:|
| 64 | **7631** ok_rps, p50 7.2 ms (direct). NLB TLS **7592** | **7047** ok_rps, p50 7.2 ms |
| 256 | 6164, p50 32 ms | 6380, p50 28 ms |

An earlier same-day prod `/health` wave from this client peaked at **8939 ok_rps @ 64** (first compare remnant in `run.log`). Same order of magnitude.

**A later overlapping compare corrupted prod `/health` JSON** (845 → 35 ok_rps while the gateway was recovering / 12 GiB workers cycling). Those files (`prod_H_if64.json` now 845.81, etc.) are **not** capacity. Use the isolation numbers above.

Neither host is anywhere near 100k RPS even on `/health`.

---

## 5. Code-only chat — AWS subtracted (the hardware comparison)

Stub LLM, T2 off, adjudicator off, output guard off, burst/TPM lifted, WEB=16, unique prompts.

### Quiet probe (HTTP 200)

| | Prod bypass | GCP stub (patched image) |
|---|---|---|
| Completion id | `chatcmpl-loadtest-stub` | `chatcmpl-loadtest-stub` |
| `model_output` | **0.0 ms** | **0.0–0.2 ms** |
| `output_guardrail` | 0 | 0 |
| `input_scan` (Tier-1 only) | 111–153 ms | 36–66 ms |
| Wall | ~300 ms | ~80–90 ms |

### Saturation (ok_rps = completed HTTP 200 / wall)

| Inflight | **Prod** | **This VM** | Ratio GCP/prod |
|---:|---:|---:|---:|
| **32** | **84.85** (isolation) / **85.6** (first compare) | **124.42** | **1.47×** |
| 128 | 82.73 (this dir) / 80.51 (isolation) | **116.75** | ~1.41× |
| 512 | 72.17 / 75.95 | **96.92** | ~1.3× |
| 1024 | 67.96 / 71.46 | **89.86** | ~1.3× |
| 2048 | 76.71 / 73.35 | 79.92 | ~1.1× |
| 4096 | 77.03 | 89.91 | ~1.2× |

Matched inflight=32 (low error, honest p50):

| | Prod | This VM |
|---|---:|---:|
| ok_rps | **84.85** | **124.42** |
| error_rate | 0.68% | 0.55% |
| client p50 | **289 ms** | **147 ms** |
| T1 `input_scan` p50 | ~105 ms | **77 ms** |
| `model_output` p50 | **0** | **0** |
| HTTP 200 / 400 | 872 / 6 | 1269 / 7 |

Doubling vCPU (8→16) and removing the 12 GiB cap does **not** double code-only RPS. Tier-1 regex/policy is CPU-heavy and does not scale linearly. Extra in-flight past 32 **does not raise ok_rps**; it only grows queue delay (p50 0.15 s → 21 s on GCP) and **422 `no_provider_configured`**.

### Burst-limit trap (this VM only)

GCP had **no** `firewall:config:zeroshield` key (fallback = `default` burst **150/s**). First stubbed wave hit **4962× HTTP 429** at 32 in-flight (`gcp_C_stub_burst150_if32.json`) even though env bypass was on — the stub is so fast this VM **attempts ~595 chat/s**, which **exceeds prod’s 85 rps** and trips the 150 burst. After writing a zeroshield config with `rate_limit_enabled=false`, 429s disappeared. That file was **deleted on restore** (it did not exist before).

Prod code-only never needed that because ~85 ok_rps **fits under burst 150**.

---

## 6. Little’s Law check (code-only @ 32)

| Host | p50 | Predicted RPS | Measured ok_rps |
|---|---:|---:|---:|
| Prod | 0.289 s | 111 | **84.85** |
| GCP | 0.147 s | 218 | **124.42** |

Same shape as live: measured RPS tracks wait/CPU time, not a hidden FastAPI tax. `/health` on the same processes is 7k+.

---

## 7. What is still “code” (both hosts)

1. **Tier-1 scan ~70–80 ms** at modest concurrency. Caps code-only in the **80–125 ok_rps** band. Not the live 8 RPS story.  
2. **HTTP 422 `no_provider_configured`** at high in-flight (empty catalog on some gunicorn workers). Same defect on prod and GCP. Not hardware.  
3. **HTTP 503 kill_switch** on operator Haiku (`ttl=-1`, left intact).  
4. **HTTP 400 `content_filter`** on a few unique prompts. Noise.

---

## 8. Can we say “prod is the only blocker”?

| Claim | Verdict | Evidence |
|---|---|---|
| Prod **instance size** is why live 9-stage is ~8–29 RPS | **False** | Live wait is Bedrock T2+guard (+ BYOK). Local stages ~10 ms. This 16c/60G VM is **worse** live (3.1 s routing). |
| Prod **instance size** is why **code-only** sits at ~85 RPS | **Mostly true** | Same bypass: 85 vs **124** ok_rps @ 32. 8 vCPU + 12 GiB gateway cap vs 16 vCPU uncapped. Residual is T1 CPU, not FastAPI. |
| FastAPI/gunicorn is the live wall | **False** | `/health` **7.6k–8.9k** on prod, **7.0k** on this VM. |
| Disk / 100k IOPS is the live wall | **False** | No disk on the 9-stage hot path. IOPS not measured; would not move T2/BYOK/guard. |
| This VM would deliver 100k live 9-stage if prod were bigger | **False** | 100k × 3.5 s ≈ **350k in-flight**. Prod 16 GiB cannot hold that; this VM’s RAM does not remove AWS wait. Even `/health` is &lt;10k on one box. |
| NLB is the wall | **False** | Isolation: NLB `/health` 7592 vs direct 7631; NLB code-only @128 ≈ direct. |

**Accurate statement:**  
The **live** completed pipeline is blocked by **AWS model wait** (and quota/concurrency behind it), on **both** hosts. After subtracting AWS, **prod’s 8 vCPU / 16 GiB / 12 GiB gateway cap** is the next ceiling (~85 completed chats/s). This 16-vCPU / 60 GiB VM raises that local ceiling to ~124/s — proof that **hardware size matters for Tier-1**, and proof that **it is not the live 9-stage limiter**.

---

## 9. Restore

| Host | After this work |
|---|---|
| **Prod** | `WEB_CONCURRENCY=6`, stub **0**, T2 **true**, sample **1.0**, output guard **on**, CB **on**, `/health` **200**. Unique-prompt live: T2 ~1631 + BYOK ~1039 + guard ~923. Bench API key `risk_score` was driven to **1.0** by auto-block telemetry during an overlapping 403 wave; **Postgres + Redis reset to 0** (`prefix dMs8qliW`). Operator Haiku kill-switch **not** deleted. |
| **This VM** | `WEB_CONCURRENCY=16`, stub **0**, T2 **true**, adjudicator **true**, guard **on**, CB **on**, `/health` **200**. Synthetic `firewall:config:zeroshield` **deleted** (pre-bench keys were only `default` + `testing`). Live unique-prompt after restore: T2 ~1003 + routing ~3199 + BYOK ~1144 + guard ~949. |

---

## 10. Files

| Path | Use |
|---|---|
| `probe_prod_live.json` / `probe_gcp_live.json` | AWS-on stage split |
| `probe_prod_bypass.json` / `probe_gcp_stub.json` | stub proof (`model_output≈0`) |
| `gcp_H_if64.json` | GCP `/health` 7047 @64 |
| `gcp_C_stub_if32.json` … `if4096.json` | **valid** GCP code-only |
| `gcp_C_stub_burst150_if32.json` | 429 burst=150 before zeroshield config existed |
| `gcp_C_if32.json` etc. | **invalid** (stub missing from image; real BYOK) |
| `prod_C_if128.json` … `if4096.json` | valid prod code-only (first compare; `model_output=0`) |
| `../code-vs-aws-bench-2026-08-17/` | prod L/S/B/C/H isolation, including C@32 **84.85** and H@64 **7631** |
| `run_compare.py` / `run_gcp_stub_c.py` | harness |

Do not use `verdict.json` from the overlapping second compare (`peak_code_prod=3.49` was HTTP 403 `threat_intel_blocked`).
