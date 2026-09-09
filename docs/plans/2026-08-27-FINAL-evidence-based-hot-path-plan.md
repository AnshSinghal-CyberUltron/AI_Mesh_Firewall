# FINAL — Evidence-based hot-path, detection, and capacity plan

> **Status:** PLAN ONLY. No code, config, or infrastructure change is authorised by this document.
> **Date:** 2026-08-27 · **Branch:** `ansh`
> **Supersedes:** the *conclusions* of `2026-08-22-MASTER-*` and `2026-08-25-FINAL-*`. Their reasoning is retained where it survived; every number they asserted has been re-measured.
> **Evidence base:** 18 agents across two workflows (`wf_3a825355-ff7`, `wf_f840f647-3ee`) — 7 evidence agents, 1 synthesis, 5 adversarial reviewers (CLAUDE.md Phase 3), 1 devil's advocate (Phase 4), 3 capacity/removal agents, 1 adjudicator. ~4.1 M subagent tokens, 1,178 tool calls. Reports in `scratchpad/final/`.

**Provenance tags.** `[M]` measured this session · `[R]` repo bench corpus · `[V]` vendor-published (URL + date) · `[I]` independent third party · `[D]` derived arithmetic (assumptions stated) · `[NF]` searched, not found.

---

## 0. Verdict

> **⚠ UPDATED 2026-08-29 — the user removed all architectural constraints (GPU/CPU/RAM/storage open, budget unchanged at $5,000/mo). That lifts the GPU prohibition, which was a signed lock rather than an evidence-based conclusion, and it changes the verdict. §0–§9 below remain correct **for the CPU-only case**. The new answer is [§11](#11--best-achievable-with-all-constraints-removed-2026-08-29).**
>
> **Short version:** ≤12 ms *is* reachable — **≈4.1 ms p50 at ≤512 tokens, ≈5.9 ms at ≤1,024 tokens** — with the classifier running **in-process on an L4 GPU node**. But **74% of that number has never been measured on any GPU we control**, a $7 bake-off converts it, and the design carries an unstated **1.99% per-request false-positive rate** that must be published before it is quoted.

**On CPU only, the locked SLO is NOT ACHIEVABLE.** The verdict is over-determined: three independent constraints each sink it alone.

| Constraint | Measured | Budget | Over by |
|---|---:|---:|---:|
| Semantic classifier (PG2-22M INT8 ONNX, seq 512) | **159.2 ms** `[M]` | 5 ms | **32×** |
| Tier-1, verdict-identical rewrite, 1024-token band | **63.4 ms** `[M]` | 0.50 ms | **127×** |
| Cost of 100 k RPS (coalesced SSE, best realistic case) | **$309,620/mo** `[M from V]` | $5,000/mo | **62×** |

Honest total with a transformer-class classifier: **≈119 ms p50**, p99 unmeasured.

**But the latency verdict is not the finding that matters.**

### 0.1 The finding that matters

The plan was about to recommend shipping **Tier-1-only as the default posture** and certifying 12 ms for it. **Nobody had ever measured what Tier-1-only detects.** The devil's advocate measured it against the real `InputScanner._scan_prompt_sync` at HEAD:

| Measured `[M]` | Result |
|---|---|
| Bare English prompt injections, same family as the shipped regexes | **3 / 10 detected** |
| The same injections paraphrased with disjoint trigger vocabulary | **0 / 10 detected** |
| Benign prompts containing inline-backtick code | **5 / 5 HARD-BLOCKED** — `command_injection`, confidence 1.00 |
| Ordinary agentic tool descriptions | **3 / 12 HARD-BLOCKED** — `data_leakage`, confidence 1.00 |

Root cause: `ATTACK_PATTERNS["command_injection"]` contains the literal pattern `` r"`[^`]+`" `` — **any backtick pair**. `command_injection` and `data_leakage` are excluded from the explanatory carve-out (`scanner.py:104`, tested at `:1201`), so the block is terminal at `:1212-1219`, tier_1, and never reaches Tier-2.

**The recommended default posture is near-blind on the primary threat and hostile to ordinary developer traffic, simultaneously.**

This inverts the project. It is not "12 ms vs 119 ms." It is: *the product's detection value lives almost entirely in the layer that costs 100× the SLO, and the layer that fits the SLO has never been measured and does not work.*

### 0.2 The consequence for the T1 rewrite

E5 proved a verdict-identical Tier-1 rewrite is achievable — 0 verdict diffs across 44 `_scan_prompt_sync` inputs, 1,432 `_segment_token` tokens, 28 `_fuzzy_scan` inputs `[M]`. It is a 32× win (2,038.9 ms → 63.4 ms at the 1024-token band).

**But byte-identical verdicts is now the wrong goal.** It is a gate that faithfully preserves a detector measured at 0/10 recall on paraphrase and 5/5 false-positive on inline code. We would be spending the quarter making a broken control fast.

The T1 fork is therefore:

| Option | Cost @1024 tok | Verdicts |
|---|---:|---|
| Verdict-identical rewrite | **63.4 ms** `[M]` | preserved — including the defects |
| Change the detector | irreducible floor **0.34–0.54 ms** `[M]` | different, and must be scored against a corpus that does not yet exist |

You cannot choose between these without a detection corpus. That is Gate 0.

---

## 1. What the evidence establishes

### 1.1 The instrument is broken — nothing should be measured until this is fixed

R4's finding, confirmed at source by the devil's advocate:

- `ttft_ms` and `duration_ms` are both derived from `provider_start_ts` (`pipeline_trace.py:120-131`)
- `setdefault("model_output_ms", duration − ttft)` at `:666` **does** fire, because `main.py:6330` initialises `stage_metrics` without that key (the only writer is `:9808`, the non-stream path)
- `total_latency_ms = duration` (`:738`, `:778`)
- The bench computes `addon = total − model_output`

**Therefore `addon = TTFT`.** On streaming requests the benchmark measures the customer's model, not the firewall. Every streaming latency number this project has ever produced is provider TTFT wearing our name — and Phase 1's "stop and revise" gate cannot fire, because the 12 ms gate passes unconditionally.

### 1.2 Measured stage costs

| Stage | Today `[M]` | Target design `[M]` | Note |
|---|---:|---:|---|
| 4× `BaseHTTPMiddleware` | 1.00 ms p50 / 4.70 p99 | — | **no budget line existed for the framework at all** |
| auth (`GET auth:apikey:*`) | 0.128 ms p50 | ~0 (RAM cache) | every request pays the RTT today |
| `redact_all`, **one** pass over 4 KB | **8.19 ms p50 / 18.05 p99** | 0 | the request runs **4–20** of them |
| All non-classifier stages | **≥ 35 ms** | **0.16 ms** | budgeted at 2.40 ms `[D]` — wrong in *both* directions |
| Tier-1 `_scan_prompt_sync` @ 4096 chars | **2,038.9 ms** | 63.4 ms (identical) / 0.34–0.54 ms (floor) | |
| `_deobfuscate_text` | 1,186.5 ms | **100.2 ms** with `lru_cache` | **11.8×, verdict byte-identical** `[M]` |
| Tier-2 (PG2-22M INT8, seq 512) | — | **159.2 ms** | 32× over budget |
| Terminal SSE frame `pipeline_trace` | **41,424 B** | by-reference | 21 prompt copies + 7 response copies |

The target design releases ~2.2 ms back into the envelope by making the non-classifier path Redis-RTT-insensitive — the only remaining Redis work is an amortised lease refill.

### 1.3 The classifier is dead on CPU, and batching does not save it

E3 obtained the **real** Meta checkpoint (`gravitee-io/Llama-Prompt-Guard-2-22M-onnx`), not a proxy.

| seq | measured p50 `[M]` | plan assumed `[D]` |
|---:|---:|---:|
| 256 | 59.5 ms | 2.5 ms |
| 512 | **159.2 ms** | 5 ms |

Three compounding causes:

1. **The model is 12 layers, not 6.** R2's spec was wrong; every downstream estimate inherited it.
2. **Batch-4 is net negative at the optimal thread count** (0.92× at 8 threads). The apparent 1.49× win exists only at 16 threads and is compensation for wasting cores on one small graph. **Strike L5's batch-4 mechanism.**
3. **INT8 does not rescue it** — 1.27× over FP32, and 1.73× *slower* at batch-4. ONNX Runtime's fusion optimiser's `MODEL_TYPES` dict has **no `deberta` entry** `[V]` → no fused Attention → no `QAttention` → loose `MatMulInteger` + dequant nodes. **Architectural, not a tuning knob.**

Two costs never budgeted: **SentencePiece tokenisation at 4.3 ms/512 tok** `[M]`, and the finding that a 16-core host **cannot supply the FLOPs for even 85 RPS** of 1024-token classification.

Useful for the real design: **8 threads (= physical cores) is optimal; 16 threads is 25% slower with 2.4× worse p99.** SMT actively harms this workload.

### 1.4 The embedding + trained-head design fails on a budget nobody modelled

This was my recommendation. The devil's advocate trained the head that nobody else had trained — disjoint documents, disjoint injection phrasings, length-controlled insertion, L2-regularised logistic probe:

| window | ≈tokens | AUC same-family | AUC disjoint-vocab | recall @1% FPR |
|---:|---:|---:|---:|---:|
| 16 w | ~21 | 0.9995 | 0.9498 | 0.377 |
| 48 w | ~62 | 0.9566 | 0.7708 | 0.060 |
| **200 w** | **~260 (deployed)** | **0.7397** | **0.6017** | **0.028** |

**AUC 0.60, recall 2.8% at the deployed window.** The mechanism is **dilution**, not order-invariance — it works at 21 tokens and collapses at 260.

And the arithmetic that kills it outright: **51 windows × 1% per-window FPR = 40% per-request FPR.** Cost was never potion's problem (~1.5–2 ms at the band). **The multiple-comparisons FPR budget is.** A cost-only gate admitted it; a quality gate excludes it.

### 1.5 Tier-1 rewrite: feasible, with a silent-bypass trap

E5, all `[M]` unless noted:

- **No wheel collapses.** hyperscan 0.8.2, rapidfuzz 3.14.5, ahocorasick-rs 1.0.3, onnxruntime 1.29.0 all ship linux **aarch64** manylinux wheels through CPython 3.14 `[V]`. The hyperscan aarch64 wheel was downloaded and its ELF header verified `EM_AARCH64` with Vectorscan 5.4.12 statically bundled.
- The real scan set is **174 regexes**, not 145. 165 compile plain, **9 need `HS_FLAG_PREFILTER`**, **0 hard failures**.
- **The crux is not lookaround — it is Unicode character classes, and the naive port is a silent Tier-1 bypass.** 162/174 patterns use `\s`/`\b`/`\d`/`\w`, which are **Unicode** in Python `re` on `str` and **ASCII** in Hyperscan. A naive ASCII-flagged DB produced **34 recall violations on 95 probes** — `ignore all previous instructions` with a thin space matches in Python and is **missed by Hyperscan**. `HS_FLAG_UCP` is not the fix: **73/174 fail to compile under UCP** (`\b` unsupported).
- **The fix is measured:** a mechanically *relaxed superset* — drop `\b`/`\B`, widen `\s`/`\d`/`\w` to Unicode equivalents, compile `UTF8|UCP` → **174/174 compile, 0 recall violations, same cost** (34.5 µs vs 33.3 µs at 4096 chars).
- **RapidFuzz is a provably sound prefilter, not a drop-in.** Adversarial search found 136 threshold crossings in 371,685 pairs at the `_segment_token` 0.80 threshold and 176 in 222,166 at the `_fuzzy_scan` 0.75 threshold — **every one in the "rapidfuzz matches, difflib does not" direction, zero in the reverse.** So `rapidfuzz ≥ thr` is a superset of `difflib ≥ thr`: shortlist with rapidfuzz batched, verify survivors with real `difflib`.

### 1.6 Cost — egress dominates, and my earlier estimate was low by 17×

I estimated ~$78 k/mo egress at 100 k RPS. W1 reproduced my arithmetic exactly and showed the **inputs** were wrong: SSE per-token framing, not 3 KB/request, is the real wire shape.

| RPS | per-token SSE | coalesced ×16 | coalesced + gzip `[D]` |
|---:|---:|---:|---:|
| **1 k** | $16,966 | **$4,866** | $3,549 |
| 10 k | $159,496 | $32,045 | $18,874 |
| 50 k | $793,670 | $155,494 | $89,637 |
| **100 k** | **$1,586,279** | **$309,620** | $177,905 |

Assumptions: `c4a-highcpu-16` 1-yr CUD, ≤60% CPU, ×1.5 AZ, min 3 hosts HA, no Cloud NAT, Premium Tier egress to Asia, 70% streaming, 400-token answers, 250 RPS/vCPU, no Cloud Armor, no Cloud Logging at request rate.

**Egress + LB data processing is 94.6% of the best case. Compute is 5.1%.**

The reframe: **making the code faster buys nothing. Making it emit fewer bytes buys 11.6×.** Both are code changes — but they are different code, and only one of them is in the old plan.

### 1.7 The RPS/vCPU number nobody agrees on

Same evidence corpus, five different figures:

| Source | RPS/vCPU | Per 16-vCPU host | Tag |
|---|---:|---:|---|
| MASTER D4's 12-host cell for 100 k | 520 | 8,320 | `[D]` — **no support anywhere. STRIKE IT.** |
| MASTER D4's stated header | 250 | 4,000 | `[D]` |
| SYNTHESIS §5.3 planning band | **9–25** | **150–400** | `[D]` |
| E3 FLOP wall, PG2-22M @1024 tok | 0.68 | 10.8 | `[D from M]` |
| Today, measured, @1024 tok | 0.3 | **~5** | `[R/D]` |

A 1,700× spread on the second-largest bill line. **N ≈ 85 RPS/host is a 32–64-token number**; at the 1024-token band today's host does ~5 RPS.

---

## 2. Revised locks

The eight signed locks are restated as they must be to remain truthful.

| # | As signed | As it must read |
|---|---|---|
| **L1** | 12 ms p50 total tax | **A band + posture + percentile:** "≤ X ms p50 and ≤ Y ms p99, for posture P, at prompt band B, at N RPS/host." Phase 1's absolute threshold becomes a **delta** test. |
| **L2** | Semantic classifier on output, overlapped | Retained *as an intent*. Must name a model class with a **GFLOP-per-window entry gate AND a published recall floor**, evaluated at the **deployed window size** with a **per-request** FPR budget. |
| **L3** | $5,000/mo | **Unchanged and not the binding constraint.** The BOM is careful; this is not where the plan fails. |
| **L5** | ≤1024 tok via batch-4 | **Batch-4 struck** (measured net-negative). Replace with concurrent independent sessions — and resolve the 16-process contradiction first. |
| **L6** | Measured-ceiling N | Retained, but **N must be re-baselined with a declared prompt band.** |
| **L7** | GCP asia-south1 only | Retained. Note nothing has been measured on `c4a` — every millisecond in this corpus is Intel x86. |
| **L8** | KS ≤50 ms + push | Retained, but **sign the availability consequence**: as written, every Memorystore failover — including scheduled maintenance — is a full-product 503. Either accept that or add a grace window. |
| **L9 (D18)** | Zero Bedrock | **Retained and reinforced.** See §4. |
| **L10** | 12 ms on all paths | Retained with the contract boundary in §5. |
| **L11** | 100 k RPS / 100 k in-flight | **Split into design ceiling / provisioned N / competitive parity.** See §6. |

---

## 3. The gates — what must happen before anyone writes feature code

Ordered by dependency, not by budget-table appeal.

### Gate 0 — Measure what we are protecting *(blocks everything)*

Nothing in this plan can be chosen without this, and it is an afternoon's work, not a research programme.

- Build a labelled detection corpus. Publish a **recall / FPR table per posture**: Tier-1 only, Tier-1 + policy pack, Tier-1 + semantic. Include a **paraphrase family with disjoint trigger vocabulary**.
- Fix `` r"`[^`]+`" `` and the `data_leakage` pattern as **two separately tracked changes with two separate gates** — a verdict-equivalence gate would otherwise score the fix as a regression.
- **No posture may be recommended, and no SLO published for a posture, until this table exists.**

### Gate 1 — Fix the instrument *(blocks all measurement)*

- Re-base stream metrics on the **request** epoch, not `provider_start_ts`.
- Replace every reconciliation clamp (`pipeline_trace.py:95,708,1512`, `stream_orchestration.py:773`) with a **signed** residual plus a reconciliation-error histogram that fails CI at non-zero p50.
- Add `STREAM=1` with SSE parsing to the bench, plus the assertion `|wall − addon − model_output| < ε`.
- Microsecond resolution and an explicit `ran: bool`. **Strike** "0 ms = skipped".
- Split `capacity_eligible` from `tax_eligible`.
- Emit the addon split from the **stream finaliser**; add per-stage `t_start_ms`/`t_end_ms` — without which L2's overlap is unverifiable in principle.

### Gate 2 — The live defects *(not performance work; these are shipping now)*

| ID | Defect | Evidence |
|---|---|---|
| **S-1** | `PolicySync` partial cold start leaves a subset of tenants **unenforced forever**, `/health` green, no reconciliation. 16 workers × N nodes = independent SCANs each able to stop at a different point. | R2 H-2 / R3 A2, confirmed at source |
| **S-2** | An un-synced tenant **inherits the platform-default org's firewall posture**. `models.py:1368-1370` → `build_redis_key()` returns `firewall:config:default` when org-less. Fix exists (`get_own_config`), applied to MCP but **not** the chat hot path. | R3 A3, confirmed reachable |
| **S-3** | `--worker-connections 20000` is **silently inert** (`uvicorn.workers.CONFIG_KWARGS`). No admission control anywhere; rate limits bound rate, not concurrency. | R2 H-3, verified `[M]` |
| **S-4** | Output-path `scan_degraded` floor is dead code → on a breaker-OPEN, streams are delivered Tier-1-only, fail-open, **while telemetry attests otherwise**. A false attestation in a compliance record. | three reviewers independently |
| **S-5** | Streaming "block" is a **truncation**, not a block — 260 of 800 characters delivered, then an error frame. | `[M]` |
| **S-6** | Telemetry drops the whole batch on a Redis error and the oldest events on overflow — no retry, DLQ, or spill — **precisely during the failures we expect**. | R2 H-7 |
| **S-7** | Input and output share one breaker key, counted globally across tenants. | S-2/S-3 |

### Gate 3 — Restate the budget honestly

Add the stages that have no budget line at all: **ASGI middleware, tokenisation, stream machinery, multi-turn double-scan, the `tools`/`response_format` channels, snapshot refresh.** Add R3's **"worker-count multiplier applied? Y/N"** column to every cache line in the tracker — `compute_sizing(16.0, 64 GiB) → workers=16` with no shared memory `[M]`.

### Gate 4 — Sequence by dependency risk

**Phase 1 = zero new dependencies** (memoise `_deobfuscate_text`, detach the logger, fix the flush trigger, shrink telemetry, scrub the trace on allow paths, coalesce SSE) ⇒ **~10× available now** `[M]`/`[D]`. Phase 2 = engine swaps. Phase 3 = decided against a 233 ms baseline, not a 2,039 ms one.

### Gate 5 — Price the option that was rejected on the wrong cloud

**GPU was rejected using an AWS price in a GCP-locked plan.** `[NF]` — no GCP accelerator rate appears anywhere in E2's BOM. GCP `asia-south1` has `nvidia-l4` in zones a/b/c with on-demand quota 8, and `g2-standard-4` machine types `[L, verified 2026-08-25]`.

This is **the only lever that closes the classifier gap outright**, and it has never been evaluated on the correct price list. Re-decide it on D1's hop-tax argument — which is the argument that will actually hold — not on a price from a different cloud.

---

## 4. D18 — Zero Bedrock *(retained; ordering is non-negotiable)*

"Bedrock" is four distinct workloads:

| Group | Call sites | Replacement |
|---|---|---|
| **A — injection classification** | `scanner.py` `scan_prompt_with_tier2`; `output_guard.py` `scan_output_with_tier2`; `llm_judge.py` (only from `rag_pipeline/query_stage.py:437`, **default ON** at `main.py:5943`); `mcp_scan_orchestrator.py:1205` (inherits A1) | **Pending Gate 0 + Gate 5** — PG2-22M is measured unaffordable on CPU |
| **B — hallucination grounding (D_G10)** | `rag_pipeline/bedrock_embedder.py` (`amazon.titan-embed-text-v2:0`) + `grounding_guard.py`, attached to OUTPUT_GUARD at `main.py:5822-5839`, `output_grounding_enabled` **default true** (`config.py:198`) | in-process static embedding — this is an *embedding* task, not classification |
| **C — simulator / catalogue** | `bedrock_inference.py` `SIMULATOR_BEDROCK_MODEL_ID = openai.gpt-oss-120b-1:0`; reserved aliases | retires O-7 |
| **D — plumbing** | `bedrock_client.py`, `bedrock_logger.py`, `bedrock_scanner.py`, `bedrock_tier2_breaker.py` | deleted last |

**Two ordering rules that cannot be violated:**

- **T5 before T9** — never remove the only fail-closed control without proving its replacement.
- **T11 before T16** — never revoke IAM while code still calls Bedrock; doing so converts a fail-open path into a per-request `AccessDeniedException` storm.

**Corrections to the earlier inventory** (W3, verified against the tree): **21** env vars, not 32. `security_engines` is **live at 3 verified call sites**, not dead code — do not delete it. `boto3.client("s3")` is live at `poc_views.py:69`, so **boto3 stays**; only the Bedrock coupling goes. `onnxruntime` is already in the image via chromadb.

**Two findings that change the budget:** there **is** a Bedrock Titan network call on the RAG output path today (plain chat skips it — `main.py:9901` takes a no-context branch); and `grounding_guard.py:158` **fails open** on circuit-open, so a Bedrock outage silently disables hallucination detection today.

**Known miss to resolve:** after judge removal the RAG path has **no semantic layer at all** (`_embedding_vault = None`, verified). T7 as drafted is a security regression, not a gain.

---

## 5. Per-path contract

All four paths meet 12 ms **in a Tier-1-only posture** — the posture Gate 0 must first prove is worth shipping.

| Path | Firewall tax, target posture `[M]/[D]` |
|---|---:|
| Chat | 2.39 ms — *and this figure is a sum of unbuilt targets mis-tagged `[M]`; honest range **3.0–5.5 ms p50**, p99 unestablished* |
| MCP | 1.5–3.5 ms |
| RAG | 0.5–0.6 ms |
| Vector | 0.7 ms |

**The contract boundary is who owns the endpoint:**

> **"≤ X ms of firewall tax + N declared tenant round trips, N ≤ 2."**

Today N = 3 on Pinecone — **and the third one is ours**. RAG/vector **wall clock is ≥124 ms and never lower**, because Pinecone has no `ap-south-1`/`asia-south1` region. Tenant-owned round trips are outside the contract on the same principle that BYOK model time is outside it.

**L10 does not multiply the problem by four — it collapses it to one.** All four paths share the scanner. That one question has a measured answer, and it is currently *no*.

---

## 6. Capacity — three separate claims

| Claim | Statement | Status |
|---|---|---|
| **C-A design ceiling** | No fixed bottleneck below 100 k RPS / 100 k in-flight; every tier scales horizontally with a named binding constraint | Achievable as a *design* property |
| **C-B provisioned N** | What $5,000/mo actually buys, measured, with HA | **≈1,000 RPS with SSE coalescing ×16** ($4,866/mo). ~122 RPS as the code stands today. In-flight is never the binding constraint at this budget. |
| **C-C competitive parity** | Added latency comparable to industry AI gateways | See below |

### 6.1 Competitive positioning — and why the published numbers are not comparable

| Gateway | Published | The caveat that matters |
|---|---|---|
| TrueFoundry | +3–5 ms *(blog)* | **+7 ms docs, +12 ms with tracing**; 350 RPS/vCPU; mock upstream; **auth + routing only, zero security scanning** |
| Kong | 96 k RPS, p99 9.75 ms | 16 vCPU; **"no policies, like caching or API key based authentication, were configured"** |
| LiteLLM Rust | 0.05 ms | **"no logging callbacks, spend tracking, or persistence enabled"** |
| Bifrost | 11 µs | "excludes upstream response time" |
| Envoy AI Gateway | **`[NF]`** | no published data-plane benchmark exists |

R1's own conclusion: **"None of the published overhead numbers are measured with streaming on."** Kong's docs state streaming cannot combine with response-phase plugins at all.

**The defensible claim is therefore not RPS parity. It is latency parity while running a security pipeline none of them run, measured with streaming on — which requires Gate 1 first.**

---

## 7. Honesty list — what we must not claim

**Latency**
1. No "12 ms" without a posture, a prompt band, and a percentile. Today's honest sentence: *"≈119 ms p50 with semantic classification on; a Tier-1-only posture is projected at 3–5 ms p50 once rewritten, with p99 unmeasured."*
2. **No streaming latency number from the current bench.** It measures the customer's model.
3. **No p99 for any posture.** L6's 40 ms clause is unbacked.
4. **No figure "on c4a / Axion."** Nothing was measured there.
5. **Not N ≈ 85 RPS/host.** That is a 32–64-token number; at the 1024-token band it is ~5.
6. **Not 100 k RPS.**

**Detection**
7. **We cannot claim the default posture detects prompt injection.** 3/10 same-family, **0/10 paraphrased** `[M]`.
8. **We cannot claim a low false-positive rate.** 5/5 benign inline-code prompts and 3/12 ordinary tool descriptions hard-blocked at confidence 1.00, terminally.
9. **No tool/function-calling support at the 1024-token band** — ~6–7 ordinary tools tip the request into `block/dos/context_length_exceeded`.
10. **No supported prompt length above ~1,970 tokens** (English; ~800 Hindi, ~750 Tamil — in an India-only deployment).
11. **Streaming "block" is not a block.** It is a truncation.
12. **Responses are not Tier-2 scanned end-to-end today** — and telemetry attests that they are. **A false attestation in a compliance record, live.**
13. **No per-tenant policy isolation** — an un-synced tenant inherits the platform default.
14. **Policy enforcement is not fail-closed** — a partial cold start leaves tenants unenforced with `/health` green.
15. **No bounded revocation or config-propagation time.** `auth:epoch` does not exist.
16. **No complete enforcement records.**
17. **Tier-2 does not see the whole response** — `_head_tail` (`bedrock_scanner.py:63-72`) silently drops the middle of any answer over 10,000 characters.
18. **No availability during cache maintenance** under L8 as signed.

**Process**
19. **Do not present target values as measurements.** The "2.39 ms `[M]`" row is a sum of unbuilt targets, two of which the same document calls unreachable.
20. **`potion-base-8M` is not a viable Tier-2 replacement.** AUC 0.60 / 2.8% recall at the deployed window.
21. **The off-host option was not evaluated.** It was rejected on a price from a different cloud.

---

## 8. Risks, ranked by what would actually kill this

1. **The default posture does not detect the threat and blocks ordinary traffic.** Measured, today, at HEAD. An existence risk for the product story, not a latency risk.
2. **Every gate is scored by an instrument that reports the firewall as free on streams.** Ship as-is and the team will believe it succeeded.
3. **A partial cold start silently disables the policy layer for a subset of tenants, forever, while `/health` returns 200.**
4. **An un-synced tenant inherits the platform-default posture.**
5. **No admission control, and the one operator cap is inert.** Raising `timeoutSec` without `limit_concurrency` converts a routine upstream stall into a metastable failure.
6. **Fail-closed kill switch × tens-of-seconds unplanned failover = a designed-in, unsized, all-tenant outage** that one tenant's pool exhaustion can trigger for everyone.
7. Config/policy staleness unbounded after any pub/sub disconnect.
8. `scan_degraded` dead code; shared breaker key counted globally.
9. p99 unestablished for every posture.
10. Nothing measured on the target ARM SKU.
11. Language × region mismatch: admission in characters, SLO in tokens, `len//4` under-charging Devanagari 4.1× — in an India-only deployment.
12. Telemetry discards enforcement evidence during exactly the failure the plan expects.

---

## 9. What survives unchanged

- **The verdict.** NOT ACHIEVABLE as locked, over-determined three ways.
- **The FLOP-supply argument** as a direction — hardware-independent on the demand side.
- **The rejection of speculative dispatch**, on E6's six grounds plus arithmetic.
- **The `difflib` / `_deobfuscate_text` diagnosis** — 1,186 ms measured on a 3,480-char prompt, 11.8× verdict-identical memoisation win.
- **E4's flush-trigger arithmetic** — `ceil(512/d) ≥ 64 ⟺ d ≤ 8 bytes`, verified independently by two reviewers.
- **Cost is not the binding constraint.** The BOM is careful and is not where this fails.
- **The non-classifier work is real and worth doing** — ~10× with no new dependencies. It is simply not, on its own, a shippable product.

---

## 10. For the person who signs this

The 12 ms lock cannot be met with a semantic classifier on this hardware inside this budget, and no priced path to it exists. That part survived five adversarial reviews and a devil's advocate.

But the recommendation built on top of it is not safe to ship. It proposes defaulting to the deterministic layer and certifying 12 ms for that default — and **nobody measured what the deterministic layer detects until this review**: 0 of 10 paraphrased prompt injections caught, 5 of 5 benign questions containing inline code hard-blocked.

The plan has spent seven agents optimising the latency of a control whose effectiveness has never been measured, and was about to publish an SLO for it.

**Fix the instrument, measure the detection, price the GPU, then choose the posture — in that order.** The engineering work is real and ~10× is available this quarter with no new dependencies. But the decision in front of you is a product decision about what this firewall actually stops, and it cannot be made from a latency table.

---

## 11 · Best achievable with all constraints removed (2026-08-29)

> Workflow `wf_1ccbbae1-69a` — 5 proof agents → assembler → independent verifier. The verifier audited the proof ledger and returned **15 CONFIRMED, 4 OVERSTATED, 3 INVALID**. What follows is the **post-audit** position, not the assembler's original.

### 11.1 The headline, as it must be stated

| Prompt band (**tokens**, not characters) | windows | p50 added latency | tag |
|---|---:|---:|---|
| ≤ 512 tok | 1 | **≈ 4.1 ms** | `[D]` |
| ≤ 1,024 tok | 2 | **≈ 5.9 ms** | `[D]` |
| 10,000-char deployed cap, prose | 4 | ≈ 10.8 ms | `[D]` |
| 10,000-char deployed cap, markdown/code | 7 | ≈ 17.2 ms | `[D]` |

p99 **18.3 ms** at 1,064 RPS; **13.3 ms** at 400 RPS `[D, queue-simulated]`. Convergence band on the 2-window p50: **5.4–6.9 ms**.

**Best RPS: 590–1,064, centre ≈ 750.** The binding constraint is **`NVIDIA_L4_GPUS = 8`, the regional quota** `[M, live gcloud 2026-08-29]` — **not the budget.** The design spends $3,737.58 and cannot usefully spend the rest.

**Cost: $3,737.58/mo is a floor, not a total.** See I1 below.

### 11.2 The architecture

**4 × `g2-standard-24`** in asia-south1, zones a/b/c, 3-year resource CUD — 96 vCPU + **8 × NVIDIA L4** total.

The unlock: **G2 bundles the GPU and the vCPUs in one VM** `[M, confirmed three ways]`, so **the classifier runs in-process with zero network hop**. Every alternative topology starts at **≥16.1 ms RTT** `[D]` — which is what MASTER D1 said a 12 ms budget cannot absorb. The GPU decision does not pay the hop tax; it *dissolves* it.

**Runtime choice is not a tuning knob.** ORT 1.29.0's `MODEL_TYPES` has no `deberta`, **and fusion is a graph-level pass applied *before* execution-provider assignment** `[M]` — so the ORT **CUDA EP would run the same unfused graph on the GPU**. Native **TensorRT** (or ORT with the TensorRT EP) is mandatory, served by **Triton with both GPUs behind one shared queue** (worth **8 ms of p99** at 1,064 RPS versus 8 × single-GPU nodes, at identical price), `max_batch` 4 windows, `max_queue_delay` ≈500 µs, over the C API on localhost.

**Tier-1 rebuilt** — all `[M]`:

| Change | From | To |
|---|---:|---:|
| One Hyperscan relaxed-superset gate over all 174 patterns, replacing both inline regex loops | 21.97 ms | **0.160 ms** |
| Native segmentation | 14.72 ms | **0.434 ms** |
| Dictionary admission filter (`DICT \ D_seg`) | — | 16.6× less DP work, **0.00% recall loss by construction** |
| Injection/jailbreak patterns **demoted from block to signal** | — | fixes the measured 5/5, 7/7 and 3/12 false positives |
| `sorted(vocab)` | — | removes proven per-process non-determinism |

PII/secret/credential/DoS/transport-anomaly value scanners stay, folded into the gate at 0.042 ms.

**Deliberate omissions, each load-bearing:** no Cloud NAT (at $0.045/GiB both directions it would make the otherwise-free 87 KiB provider response billable); no Cloud Armor ($15,180/mo ≈ 3× the entire budget); regional external ALB on **Standard** Network Tier (the global ALB is Premium-only); SSE with adaptive coalescing (16 tokens **or** 120 ms) + gzip-6 `Z_SYNC_FLUSH` per frame gated on `Accept-Encoding`; **no `pipeline_trace` frame** — emit a `trace_id`. Binary framing (WebSocket/gRPC) is **rejected**: the whole JSON `chat.completion.chunk` envelope costs 224 compressed bytes for a 400-token answer `[M]`.

### 11.3 The cost correction that moved the RPS answer

B1 originally put the 8×L4 design at 346–511 RPS by **linearising** W1's $4.866/RPS-month. With the trace frame deleted, SSE coalesced, gzip applied and the provider in-region, the **marginal** cost is **$0.449/RPS-month** `[D]` — a **10.8× correction**. Egress therefore stops binding until ~4,150 RPS, and the GPU quota binds first.

CPU does not bind either: 96 vCPU × 277 RPS/vCPU at a 60% cap = **26,592 RPS of CPU capacity** against 1,064 RPS of GPU capacity. The CPU estimate would have to be **25× too optimistic** to change the answer.

### 11.4 The RPS ladder, binding constraint named at each rung

| Posture | Fleet RPS | Binding constraint |
|---|---:|---|
| **A** — 100% semantic coverage | **1,064** | `NVIDIA_L4_GPUS = 8` quota |
| **A′** — same, quota raised to 14 L4 | 1,862 | budget, at $1.82/RPS-mo of GPU |
| **B** — risk-gated semantic coverage ≥26% | 4,151 | egress + LB |
| **C** — deterministic tier only | 7,200–9,400 | egress + LB, *contingent on an un-load-tested rewrite* |
| **D** — BYOK provider **out** of region | 1,731 | egress (the prompt leg becomes 65% of egress once response levers land) |

### 11.5 What the audit invalidated

**I1 — "$1,262 unspent, nothing left to buy" is INVALID.** A hot-path BOM was presented as a total BOM. **Cloud Logging is unpriced** — and its price page was sitting in the assembler's own evidence directory. At 2.758 billion requests/month, a realistic **1 KiB per-request audit record is $1,290/mo — more than the entire declared headroom, putting the plan at ~$5,028 and over budget at its own recommended operating point.** MongoDB (the live telemetry sink — 13 refs + `motor`), ChromaDB, and persistent disks are `[NF]`, unpriced entirely. Avoidable via Log Router exclusions or a GCS sink at $0.02/GiB — but that is an architectural decision that must be *made and stated*.

**I2 — no per-request FPR. The biggest finding, and structurally the same error that killed potion-8M.** The design quotes PG2-22M's **"88.7% recall @ 1% FPR"** `[V]`. That operating point is **per 512-token window**, and Meta's own model card prescribes an OR over segments. So per-request FPR = 1 − 0.99^W:

| windows | per-request FPR | false ENFORCE-blocks/s @1,064 RPS |
|---:|---:|---:|
| 1 | 1.00% | 10.6 |
| **2** (the headline band) | **1.99%** | **21.2** |
| **7** (deployed 10,000-char cap, markdown) | **6.79%** | 72.3 |

In an **ENFORCE** posture 2% is a product-defining number and belongs in the headline next to the 6 ms. Two further omissions: the model card publishes **multilingual AUC 0.942** against 0.995 English — the operative figure for a Mumbai deployment — with **no multilingual recall@1%FPR published `[NF]`**; and the 88.7% is on Meta's **private** benchmark, against an 8–16 point AUC collapse under leave-one-dataset-out in our own `[I]` citations.

**I3 — the band must be stated in tokens.** Measured chars/token is **3.04 (markdown) / 6.28 (prose)** `[M]` — never the 4.00 rule of thumb. 1,024 tokens is **3,158 characters** of this corpus, not 4,096. And the **deployed cap is 10,000 characters** (`scanner.py:74`), not 4,096 — so the 6 ms figure describes a *sub-band* of the deployed configuration.

**Overstated:** the compute price is `[I]` not `[V]` (the retained vendor artifacts are unrendered SPA shells containing zero occurrences of `g2-standard`; the number is right, verified against a third-party CSV to 7e-5, but the primary-source citation is not backed by the artifact). The batch-2 factor of 1.50 is measured on **BERT-base at seq384**; the honest band is **1.5–1.8**, i.e. GPU 4.50–5.40 ms at two windows.

### 11.6 The one sentence that can be said to a customer

> *"A full deterministic scan plus a 22M-parameter transformer injection verdict on **every** request — synchronously and block-capable before any byte reaches the provider — at a projected **4–6 ms p50 / ~18 ms p99** of added latency for prompts up to 1,024 tokens, and **590–1,064 RPS** on a single-region $5,000/month footprint; every GPU figure is derived from NVIDIA's published TensorRT measurements of this exact architecture and **none is yet measured on our own hardware**. We cannot yet quote a false-positive rate: the classifier's published 1% FPR is per 512-token window, which compounds to ~2% per request at two windows and ~7% at our deployed 10,000-character cap."*

Everything before the semicolon is strong. Everything after it is why this is not yet a product claim. **A defensible 10 ms with a published per-request FPR beats an indefensible 6 ms with none.**

### 11.7 The three actions, ordered by what the audit changed

1. **Measure the production prompt-length distribution in tokens.** One day of telemetry. It sets the window count, which sets the **latency headline, the RPS headline and the per-request FPR simultaneously.** Cheapest high-value action in the workstream.
2. **Run the `trtexec` bake-off** — $7, 30 minutes — sweeping `--optShapes` at **W = 1, 2, 3, 4, 7** (the deployed bands, not just powers of two). It must settle, in order: (a) is window-batch-1 inside 2.7–4.0 ms — if not the whole derivation dies; (b) where throughput actually saturates (the 490 vs 1,580 windows/s spread is 3.2× and directly sets $/RPS); (c) **p95/p99 over 1,000+ iterations**, because every published DeBERTa number is an average of 100 runs and **no tail data exists anywhere** — a 72 W card under sustained load is exactly where tails appear; (d) `--noDataTransfers` on/off; (e) **`--int8` with calibration — does DeBERTa INT8 work on TensorRT at all?** Currently `[NF]`, a coin-flip: if it works throughput roughly doubles; if it fails as it did on CPU, FP16 is the ceiling.
3. **Publish a per-request FPR budget before the demotion ships**, scored at the *deployed* window count and including multilingual.

### 11.8 What this does not change

The GPU answers latency. It does not touch the two findings that still gate the product:

- **The deterministic layer does not work** — 0/10 paraphrased injections, 5/5 benign inline-code prompts hard-blocked. The Tier-1 rebuild in §11.2 fixes the false positives by demoting injection patterns to signal; it does **not** create recall that was never there. Gate 0 still blocks.
- **The instrument is still broken** — `addon = TTFT` on streams. No number above can be validated end-to-end until Gate 1 lands.

---

## 12 · Complete cost matrix

> **⚠ SUPERSEDED 2026-09-02 by [`2026-09-02-hot-path-cost-matrix.md`](./2026-09-02-hot-path-cost-matrix.md).** The egress line below (**$460.46 / 4,197 GiB**) is **response-only**: `eb=1634 = 0.7×1,770 + 0.3×1,316` — B4's coalesce-×64 gzip **response** blend. It carries **no prompt leg** and silently assumes the tenant provider is in-region (ASSEMBLY lever 5, 1.90×). A BYOK gateway cannot pull that lever; the tenant chooses the provider, and OpenAI/Anthropic have no asia-south1 endpoint. **Do not reconcile other documents down to $460.** Live totals live in the linked file.
>
> **Operating point:** Posture A — 4 × `g2-standard-24`, semantic scan on **every** request, **1,064 RPS**, asia-south1, 3-year resource CUD.
> **Volume basis:** 1,064 RPS × 86,400 × 30 = **2,757,888,000 requests/month**.
> This corrects the assembler's $3,737.58, which the audit found was a **hot-path BOM presented as a total BOM** (finding I1).

### 12.1 The matrix

| # | Service | Spec | $/mo | Why this service | Tag |
|---|---|---|---:|---|---|
| **COMPUTE** |
| 1 | **GCE `g2-standard-24` × 4** | 96 vCPU · 384 GB · **8 × NVIDIA L4** · zones a/b/c | **2,736.76** | The whole architecture. G2 bundles GPU **and** vCPU in one VM, so the classifier runs in-process — **zero network hop**, versus ≥16.1 ms RTT for any split topology. 8 L4 is the entire regional quota. | `[V]`/`[I]` $684.19095678/node, 3-yr resource CUD |
| **DATA** |
| 2 | **Cloud SQL PostgreSQL** | Enterprise **HA**, 4 vCPU / 16 GiB / 100 GiB SSD | **273.91** | Orgs, keys, policies, users. Control-plane only — the chat path opens **zero** DB connections. HA because a policy store outage is a fail-closed outage. | `[V]` |
| 3 | **Memorystore for Valkey** | **HA**, 2 × 13 GB `highmem-medium` | **175.20** | The one shared-state call per request (quota `EVALSHA`, kill-switch truth). HA because the kill-switch fails closed — a single-node failover is a full-product 503. | `[V]` |
| 4 | **Persistent disks** | 650 GiB `pd-balanced` — 4 × 100 boot, 200 Mongo, 50 Chroma | **65.00** | Boot volumes plus the two self-hosted stores. Balanced, not SSD: neither is on the hot path. | `[D from V]` $0.000136986/GiB-hr × 730 |
| **PLATFORM** |
| 5 | **GKE** | 1 regional cluster management fee | **73.00** | Per-fleet autoscaling, multi-zone scheduling, GPU node pools. Flat fee — node cost is line 1. | `[V]` $0.10/cluster/hr |
| 6 | **Artifact Registry** | ~20 GB container images | **2.00** | Image storage for the gateway + TensorRT runtime. | `[D]` |
| **NETWORK** |
| 7 | **Regional external ALB** | forwarding rules (first 5) | **18.25** | TLS termination, SSE pass-through, ≥350 s backend timeout. **Regional** because the global ALB is Premium-Tier-only and Standard Tier saves 18% on egress. | `[V]` $0.025/hr |
| 8 | **Egress + LB data processing** | 4,197 GiB, Standard Tier, banded | **460.46** | **WRONG — response-only.** `eb=1634 = 0.7×1770 + 0.3×1316`. No prompt leg. Live planning $ in `docs/plans/2026-09-02-hot-path-cost-matrix.md` (~$1,090 egress / ~$4,561 all-in). $976 was the first honest correction — do not call it wrong. | `[D from V]` — superseded |
| **OBSERVABILITY** |
| 9 | **Cloud Monitoring** | Managed Service for Prometheus, cardinality-capped | **150.00** | Fleet + GPU metrics. **Must** use GMP: standard ingestion is $0.2580/MiB, which at naive per-request label cardinality exceeds the entire budget. | `[D from V]` — **risk line, needs a cardinality budget** |
| 10 | **Audit / verdict log → GCS** | ~400 GiB compressed, Standard, lifecycle to Nearline | **20.00** | A security firewall must retain a per-request verdict record. **GCS, not Cloud Logging** — see 12.2. | `[D from V]` |
| **CO-LOCATED — $0 INCREMENTAL COMPUTE** |
| 11 | MongoDB (telemetry sink) | self-hosted on spare vCPU | **0.00** | Live sink today (13 refs + `motor`). Disk is line 4. | `[D]` |
| 12 | ChromaDB (RAG connector) | self-hosted, profile-gated | **0.00** | Tenant RAG only — the chat path touches **zero** vector stores. Disk is line 4. | `[D]` |
| 13 | Control plane, Celery workers, MCP broker + sandboxes, frontend, nginx, PgBouncer | on spare vCPU | **0.00** | **CPU never binds:** 26,592 RPS of CPU capacity against 1,064 RPS of GPU capacity — ~25× spare. All of these fit in the headroom the GPU quota leaves stranded. | `[D from M]` |
| **DELIBERATELY NOT BOUGHT** |
| 14 | Cloud NAT | — | **0.00** | $0.045/GiB **both directions** would make the otherwise-free 87 KiB provider response billable. Nodes carry external IPs instead. | `[V]` |
| 15 | Cloud Armor | — | **0.00** | **$15,180/mo at these request rates — 3× the entire budget.** | `[V]` |
| 16 | ClickHouse / Kafka / BigQuery / Vertex Vector Search | — | **0.00** | Not needed at this volume; each is a new operational surface. | — |
| | | | | | |
| | **TOTAL** | | **$3,974.58** | | |
| | **Headroom to $5,000** | | **$1,025.42** | | |

**Unit economics: $1.44 per million requests.**

### 12.2 The one decision that breaks the budget

The audit sink is the difference between fitting and not fitting:

| Audit destination | Volume | $/mo | Verdict |
|---|---:|---:|---|
| **GCS, compressed** *(recommended)* | ~400 GiB | **20** | fits, $1,025 headroom |
| Cloud Logging @ 200 B/request | 526 GiB | 238 | fits, $807 headroom |
| Cloud Logging @ 1 KiB/request | 2,630 GiB | **1,290** | **$5,244.58 — OVER by $245** |
| Cloud Logging @ 2 KiB/request | 5,260 GiB | 2,605 | catastrophically over |

At 2.758 **billion** requests/month, Cloud Logging at a realistic 1 KiB structured audit line costs **more than the entire remaining headroom**. This is avoidable — Log Router exclusions plus a GCS sink — but it is an architectural decision that must be **made and stated**, not discovered on the first invoice.

### 12.3 Cost by posture

Non-hot-path lines (4, 6, 9, 10 = **$237**) apply to every rung.

| Posture | RPS | Hot-path | +Non-hot-path | **All-in** | Fits $5k? |
|---|---:|---:|---:|---:|---|
| **A — scan every request** *(recommended)* | **1,064** | 3,737.58 | 237 | **3,974.58** | **yes, $1,025 spare** |
| A′ — same, quota raised to 14 L4 | 1,862 | 4,742.76 | 237 | 4,979.76 | yes, but at the ceiling |
| B — risk-gated semantic ≥26% | 4,151 | 5,000 | 237 | 5,237 | **no** |
| C — deterministic only | 7,200–9,400 | 5,000 | 237 | 5,237 | **no** |

**The budget is not the binding constraint at the recommended point — `NVIDIA_L4_GPUS = 8` is.** File the quota increase before spending anything else; posture A′ nearly doubles throughput for $1,005/mo and is the best marginal purchase available.

### 12.4 What this matrix does not include

- **Customer BYOK model spend** — out of envelope by definition.
- **The `trtexec` bake-off** — $7 one-time, and it gates every latency number in §11.
- **Cloud SQL backup storage beyond the included allocation** — small, but unpriced `[NF]`.
- **Support plan**, if one is purchased.
- **Any AWS spend during the migration overlap** — the live fleet is still on AWS ap-south-1.
