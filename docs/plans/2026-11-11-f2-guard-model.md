# F2: local guard model (PG2-22M ONNX on CPU), manifest-verified, off-loop semantic stage

Status: plan revision 1, 12 November 2026 (the 11 November text was an unfinished draft).
Branch `plan/f2-guard-model`, PR into `develop`. Every code claim below was re-read at `develop`
@ `943a5f21`. The branch base `25c33c86` differs from `develop` only in
`gateway_v2/edge/scan_calibration.py`, GW28/GW34 tests and CI files, none of which this plan cites.

This is a DESIGN PLAN (repo `CLAUDE.md` Phase 2). It ships no product code: only this document and
evidence scripts under
[`docs/plans/evidence/2026-11-11-f2-guard-model/`](evidence/2026-11-11-f2-guard-model/).

**Evidence labels used throughout.** Numbers carry one of these labels so a reader can see what
backs them:

| Label | Meaning |
|---|---|
| **[M-nov]** | Measured 11 Nov with the real weights on this host class (shared 2-vCPU Xeon @ 2.10 GHz, AVX-512 VNNI, AMX-INT8, 7 GB; load average 5–9 from other agents). The weights were on the previous host. They cannot be re-run here until they are downloaded again (`HF_TOKEN` is not set). |
| **[M-sep]** | Measured 23 Sep with the real weights on dedicated GCP VMs: `c4-highcpu-16`, 16 vCPU = 8 cores. Source: `docs/plans/evidence/2026-09-23-runbook-v2-validation/lanes/guard-bench/summary/cpu_latency.csv` and `cpu_throughput.csv`. |
| **[M-proxy]** | Measured 12 Nov on this host with a random-weight model of the identical architecture (§4.7). Latency only. |
| **[P-weights]** | Pending: needs the gated weights. The full list is in §3.2. |
| **[E]** | Estimate: arithmetic from the measurements above. It is replaced by a measurement before any capacity is bought. |

## 0. Summary

**Scope: v2 only.** By owner decision (12 Nov), v1 production (`gateway/`) is not touched until the
v2 revamp is complete. F2 changes `gateway_v2/` and, only where §5.10 and Q6/Q7 say so, control's
rule publishing. v1 appears in this plan only as a benchmark that v2 must beat (§9.1: V1-INJ-125
and V1-PII-21).

**Where things run:**
- tests and every measurement in §3.2 run on **GitHub-hosted runners**: a standard-runner
  baseline plus a larger-runner job (§9.4);
- this shared 2-vCPU host was used only for the evidence already committed and for the
  weight-free proxy in §4.7.

**What F2 delivers (implementation PRs, after this plan is approved).**
- The sha256 stand-in in `local_onnx` is replaced by Llama Prompt Guard 2 22M (PG2-22M):
  - an fp32 ONNX export with parity to the PyTorch reference (proved [M-nov]), plus an INT8
    derivative that serves once gate M6 passes (D2);
  - loaded only from bytes that match a manifest whose digest is pinned in source;
  - run by a semantic stage that runs off the event loop, once per request, with token-based windows.
- N12 and N13 are fixed:
  - the guard no longer wedges or fails to bootstrap;
  - there is one classify per request;
  - admission uses measured cost;
  - UNAVAILABLE is distinguishable from a real BLOCK in trace, console, audit and metrics.
- L36-2 publishes measured recall, FPR and per-window latency. No floor is signed (C16), so nothing
  claims a pass.

**The finding that shapes the design: the program's latency target and PG2-22M on CPU do not fit
together for long prompts.**
- Best measured CPU latency for one full 512-position window: **74 ms p50** (INT8, 8 threads on a
  dedicated 8-core C4) [M-sep].
- Single-thread: 206 ms (INT8) and 292 ms (fp32) [M-sep].
- No thread count, dtype or batching measured so far brings a full window under the 20 ms p99
  total-overhead target, or the 12 ms p50 tax of #93.
- Only short content fits a 12 ms budget [E, §5.6]:
  - INT8 on a dedicated core: about 60 tokens;
  - fp32: about 14 tokens.
  - Explicit injections are p50 14 / p95 61 tokens. Jailbreak prompts are p50 372 tokens.
- The plan therefore makes the budget explicit and per org (§5.6, §5.8):
  - **`degrade` orgs** get a 12 ms stage budget. What does not fit is forwarded and recorded,
    visible in the trace, console, audit row and a fixed-name counter.
  - **`fail_closed` orgs** get everything scanned before forwarding, up to a 2 s cost cap. Their
    latency grows with prompt length. Beyond the cap they get a distinct 413.

**Decisions (each argued in §5, with owner questions in §12):**

| # | Decision | Where |
|---|---|---|
| D1 | Inference runs **in-process**: one ONNX Runtime session per gateway worker, on a dedicated guard thread, intra-op 1 by default. A per-host sidecar is the defined fallback behind the same `GuardBackend` protocol, triggered by T-G7 (RSS) or T-CPU1 failing. | §5.1 |
| D2 | **F2 builds both and ships INT8 to serve, behind a gate:**<br>• fp32 ONNX is always built: it is the parity anchor against PyTorch (M1, proved [M-nov]) and the reference INT8 is judged against;<br>• INT8 (#98 3.1, the owner lock) becomes the serving artifact when gate **M6** passes on the real weights: ≥ 99 % decision agreement with fp32 on every L36-2 evaluation row and on V1-INJ-125, recall and FPR inside fp32's 95 % interval on every headline set, and no V1-INJ-125 case that fp32 catches lost;<br>• until M6 has run, and if it fails, fp32 serves.<br>Why INT8: 1.7–2.0× faster at ≤ 128 positions and 1.3–1.4× at 512 [M-proxy, M-sep]. That roughly doubles the prompt length that fits the budget and cuts guard vCPU by 20–48 %. Why the gate: the "int8 flips 2–6 %" claim has no per-row source in the repo, and the external "lossless" claim is not ours. | §5.3, Q5 |
| D3 | Semantic findings are **governed by the org's existing settings rows** (`fw-prompt_injection-input`, `fw-threat-*`), which control already documents as the operator floor over the semantic detector. The join becomes threshold-aware. It is not a new platform rule (this replaces the draft's identity join). | §5.9 |
| D4 | **Failure posture is per org**, taken from the governing rows' `on_unavailable`:<br>• `fail_closed` scans everything up to a cost cap before forwarding, and refuses with distinct codes: 503 `guard_unavailable` when the guard is down or overloaded, 413/422 when the request will not be scanned;<br>• `degrade` scans what fits 12 ms, then forwards and records the rest.<br>Q7 recommends making `fail_closed` an explicit opt-in. A platform switch keeps the stage in **shadow** until L36-2 and the capacity gates pass. | §5.6, §5.8, §10 |
| D5 | The "task 3.1" floor question is handled **either way**, in v2 only. The stage runs if and only if the org's compiled plan contains a rule that would govern its findings. Whether v2 has a default injection floor is decided by which rows control publishes, not by the gateway. v1 is not changed. | §5.10, Q6 |
| D6 | Windows are **token-based**: 510-token payload, overlap 64. Request score = max over windows, with a stateful dilution check (T-G3) chosen by measurement. Window size and aggregation rule are [P-weights] choices. | §5.4, §5.5 |

**Evidence pending the weights:** §3.2 lists the thirteen items. Everything else in this plan is
either measured or labelled [E].

## 1. Requirements and the conflicts between sources

### 1.1 Sources

| ID | Requirement (quoted or condensed) | Source |
|---|---|---|
| S6 | "Real ONNX guard + async off-loop semantic stage + corpus scorer"; exit "**L36-2** recall/FPR on eval split, latency per window" | `docs/plans/2026-11-02-gw36-production-boot.md` §6, §9.4 |
| G9 | Guard backend scores `sha256(window)`; no onnxruntime/tokenizers; semantic detector unwired; `classify_input` emits nothing when `context.rules=()` | GW36 §4 |
| G10 | PG2-22M on CPU ≈ 88 ms/window (8 threads) vs `guard_wait_budget_s()` 0.100 s; input detector is a sync callable invoked per delta | GW36 §4 |
| N12 | Guard wedges after 5 s idle; manifest self-certifies; one classify per rule; budget pre-check assumes 100 windows/s; JAILBREAK can never fire; `und`/CJK → UNAVAILABLE. Fix: background probe, manifest required, ONE classify per request, measured windows/s, one shared session with small intra-op threads, semantic rules default `on_unavailable` flag, semantic spans never feed REDACT | GW36 §9.2 |
| N13 | Trace/console cannot tell a detector UNAVAILABLE from a real FLAG/BLOCK. Fix: per-stage `unavailable` in the trace + control adapter passthrough; fixed-name guard-unavailable counter | GW36 §9.2 |
| L36-2 | "Labelled injection corpus through the guard: recall and FPR at or above the T01-signed floors". No floor is signed (C16): publish measured numbers, claim no pass | GW36 §1, §7 R4 |
| §9.3a | PG2 **22M**; `local_onnx` CPU "development/CI only", production = L4 TensorRT (GW25); fp32 ONNX with parity, "not int8 … flips 2–6 % of decisions"; identity = hash of loaded bytes vs signed manifest; truncation/padding disabled; semantic action FLAG until a signed floor; thresholds per window count | GW36 §9.3a (runbook answers, 3 Nov) |
| C6 / C35 | PG2 is a narrow instruction-override detector; scope by role; language posture; default FLAG / hash the bytes actually loaded and compare with a signed manifest | runbook v3 corrections register |
| #93 | Owner locks of 25 Aug: infra ≤ **$5,000/month**; gateway tax **`T_addon_pre` p50 ≤ 12 ms**; **CPU only: PG2-22M INT8 ONNX, no GPU, no 86M**; do not provision 100k RPS inside $5k | issue #93; scale plan §13 |
| #98 | Phase 3: 3.1 "PG2-22M INT8; BLAS pin; sidecar if RSS × workers OOMs (T-G7)"; 3.2 stateful window aggregation (Prompt Overflow), UTS-39 before the classifier; 3.4 publish a recall table (no parity claim). Success: p50 ≤ 12 ms; T-H5/T-H9 **degraded injection fail-closed**; T-G2/G3/G4/G6 | issue #98; scale plan §6.3, §8, §14 |
| Program | p99 < 20 ms total firewall overhead; $5k/month envelope; horizontally scalable; CPU only | this track's brief |
| Owner, 12 Nov | (1) v1 `gateway/` untouched until the v2 revamp completes; F2 is v2-only. (2) The 125 v1 golden injection cases that relied on the Tier-1 floor, and the 21 obfuscated/encoded/split PII inputs v1 misses (F4 register R16 on #115), become explicit v2 recall benchmarks that v2 must beat. (3) State which precision F2 ships and what evidence decides it. (4) Tests and measurements run on GitHub runners; a larger-runner job is the venue for L36-2 latency, beside a standard-runner baseline | coordinator note relaying the owner, 12 Nov |

### 1.2 Where the sources disagree, and what this plan does

| Topic | One source says | Another says | This plan |
|---|---|---|---|
| Production runtime | GW36 §9.3a: CPU is dev/CI only, production is L4 TensorRT (GW25) | #93: GPU never; the brief: CPU only | The CPU stage **is** the production path (Q4). GW25's TensorRT backend stays possible behind the same protocol but is out of scope |
| dtype | GW36 §9.3a and SESSION_MEMORY: fp32 export with parity; "int8 flips 2–6 %" (ledger `report/FINDINGS_LEDGER.md:260`; the per-row file behind it is not in the repo) | #98 3.1 (owner lock): INT8; MASTER D2 cites a third-party "22M INT8 lossless (0.9579 vs 0.9564)" [external] | D2: build both. fp32 is the parity anchor (M1). INT8 serves once **our** gate M6 passes on the real weights [P-weights]; fp32 serves until then or if M6 fails.<br>INT8 speed-up:<br>• 1.25× throughput and 1.42× single-thread at 512 [M-sep];<br>• 1.7–2.0× at ≤ 128 positions [M-proxy].<br>Not an order of magnitude, but it doubles the inline coverage (§5.6) |
| Latency | Brief: p99 < 20 ms total; #93: p50 ≤ 12 ms | Measured CPU cost per window (§4.4) | An explicit stage budget and length coverage (§5.6). Long content is not inline-classified; the org posture applies |
| Semantic action | C6/GW36: FLAG until a signed floor | Control publishes `fw-prompt_injection-input` with **BLOCK** at threshold 0.80 for every org with content filtering on (§2.4) | D3/D4: org settings govern; platform `shadow` until gates pass; enforcement per org thereafter (Q1, Q7) |
| Failure posture | GW36 N12: semantic rules default `on_unavailable` flag (fail-open) | #98 T-H5/T-H9: degraded injection fail-closed; control default `tier2_fail_closed_enabled = True` | D4: the existing per-org field decides, with distinct refusal codes; never conflated with `input_blocked` (T-G5: "503 not 451") |
| Aggregation | GW10/draft: max over windows | #98 3.2 / T-G3: "`max()` alone is a 100 % bypass" for Prompt Overflow [external claim] | D6: max plus a measured dilution rule, chosen on a planted set [P-weights] |

## 2. Current behaviour (verified at `943a5f21`)

The probe `scripts/current_behaviour_probe.py` drives today's classes with no model. Its output is
`results/current_behaviour_probe.jsonl` [M-nov]. Line numbers below are from `943a5f21`.

### 2.1 Guard backend: `gateway_v2/gateway_v2/detect/guard/local_onnx.py`

| # | Behaviour | Evidence |
|---|---|---|
| B1 | The score is `sha256(window)` mapped to [0,1]. There is no onnxruntime, tokenizer or model | `local_onnx.py:115-129`; probe `score_is_sha256_of_text` |
| B2 | The manifest hash defaults to the hash of the blob it loaded, so it always matches itself | `local_onnx.py:192-195`; probe `manifest_self_certifies` |
| B3 | After the first reply, `_can_serve` refuses once `now − last_ok > 5 s`. Only a classify refreshes `last_ok`, so a backend idle for 5 s is wedged for ever | `local_onnx.py:109-113,144-146,196`; probe `backend_idle_6s_then_classify` |
| B4 | The budget pre-check uses a DECLARED `AMF_GUARD_WINDOWS_PER_SECOND`, default 100.0 | `local_onnx.py:149-153,197` |
| B5 | Only `PROMPT_INJECTION` is emitted. The detector drops results whose category is not the rule's, so a JAILBREAK rule can never fire | `local_onnx.py:125-129`; `semantic/aggregate.py:53-55`; `semantic/detector.py:318-319` |
| B6 | `select_backend` defaults to `local_onnx` | `detect/registry.py:36,117` |

### 2.2 Semantic detector: `gateway_v2/gateway_v2/detect/semantic/`

| # | Behaviour | Evidence |
|---|---|---|
| B7 | **The detector can never bootstrap.** It calls `readiness()` before `classify` and returns UNAVAILABLE when not ready. `ReadinessTracker.is_ready` is False until a reply exists, and only `classify` produces a reply. So a fresh backend is UNAVAILABLE for ever, not only after 5 s idle (N12 understated this). `_can_serve` (`local_onnx.py:97-113`) was written to allow the first call, but the detector's own readiness gate defeats it. The GW10 smoke test hides this by calling `backend.classify` directly first, with a 10⁶ s staleness bound | `semantic/detector.py:269-272`; `guard/backend.py:104-111`; `tests/detect/test_semantic_detection_gw10.py:841-870`; probe `detector_first_call → unavailable` |
| B8 | One `classify` per rule: a PI rule and a JB rule mean two inferences of the same text. `rules=()` produces no findings at all | `semantic/detector.py:220-223`; probe `detector_rules_empty → 0` |
| B9 | Windows are sized in characters (`ceil(512 × 3.04)` = 1,557 chars), not tokens. A window can exceed 512 positions for dense scripts (CJK: 1,381 tokens in 1,557 chars, §4.4) | `semantic/windowing.py:79-81,155-162,220-245` |
| B10 | The threshold mapping is a placeholder, `threshold = 1 − target_fpr` ("the real measured curve is deferred"). *The draft cited `fpr_budget.py:359-372`; the file has 163 lines. Corrected here.* | `semantic/fpr_budget.py:114-127` |
| B11 | The mid-batch timeout is checked after `classify` returned, so an overrun has already spent the CPU | `semantic/detector.py:283-287` |
| B12 | Language: all Latin script is tagged `en`; `ru` (Cyrillic) is "supported"; CJK/Arabic/Devanagari → UNAVAILABLE. A clear Russian injection scores 0.0008 on the real model (`results/language_probe.json` [M-nov]): a silent miss labelled "supported" | `semantic/language.py:66-77,200-226` |
| B13 | Role scope defaults to USER only | `semantic/roles.py:59` |
| B14 | The confusable fold runs BEFORE language identification | `semantic/detector.py:241-245` |

### 2.3 Request path

| # | Behaviour | Evidence |
|---|---|---|
| B15 | Phase 7, the input scan, is SYNCHRONOUS on the event loop. The detector is a sync `Callable[[str], Sequence[Finding]]`, run once per delta and once per content-part join run, then resolved once | `edge/openai_chat.py:298-304,614-643`; `edge/chat_deps.py:60`; `edge/input_enforcement.py:141-189,238-258,322-350` |
| B16 | Nothing in `edge` imports `SemanticDetector`. The dev profile wires `DeterministicFindings`. The production boot wires `no_findings_detector` until S5/N5 (F1a-1) | `edge/dev_profile.py:358-360`; `edge/serve.py:252,266` |
| B17 | Roles are recoverable per delta: `CanonicalRequest.input_message_index` maps each delta to `messages[i]`, and `upstream_template.messages[i].role` holds the role | `edge/wire/openai.py:98-145`; `edge/wire/openai_template.py:115-121` |
| B18 | `ScanExecutor` (a thread pool of ⌊cpu_quota × utilization_cap⌋) exists for off-loop scans | `edge/executor.py:83-118`; `runtime/resources.py:349-365` |
| B19 | `guard_wait_budget_s()` = max(100, 60 + 40) ms = **0.100 s** | `runtime/resources.py:397-406` |
| B20 | The production profile refuses to start without a DECLARED guard service rate `AMF_Q_SAFE`. It sizes every admission depth (request, guard, dispatch, egress) | `edge/production_profile.py:70-72,134-150`; `admit/quota.py:96-136` |
| B21 | `per_worker_rss` defaults to 400 MiB and decides `workers = min(cpu, ram)` | `runtime/resources.py:25,303-313` |

### 2.4 Resolution: which rules would govern a semantic finding (corrects the draft's B17)

| # | Behaviour | Evidence |
|---|---|---|
| B22 | A finding joins the rule whose id equals `finding.detector` (customer conditions). Otherwise it joins every selected rule of its category whose condition is None or of kind DETECTOR. Customer keyword and regex rules are identity-joined. **So, contrary to the draft, a customer's deterministic PI rule cannot govern a semantic finding** | `resolve/conflict.py:78-106` |
| B23 | Control publishes these input rows for **every org** with content filtering on (default True). They have no condition, so they category-join:<br>• `fw-prompt_injection-input`: action **block**, `threshold = prompt_injection_threshold` (default **0.80**);<br>• `fw-jailbreak-input`: action **block**.<br>Both get `on_unavailable = fail_closed` when `tier2_fail_closed_enabled` (default **True**), and `surfaces: ["chat"]`.<br>With threat intel on, control adds `fw-threat-{prompt_injection,jailbreak,command}-input`, documented as "an operator confidence floor over the semantic detector" | `control/ai_mesh_control/core/firewall_v2_map.py:275-278,386-427,505-522`; `core/firewall_v2_controls.py:132-160`; `core/models.py:975-978,1012-1023,1120-1124` |
| B24 | The resolver applies no confidence threshold: thresholds are applied only inside `SemanticDetector`, per rule. An UNAVAILABLE finding under any ENFORCE `fail_closed` rule becomes a **BLOCK**, the same disposition (and the same `input_blocked` refusal) as a real detection | `semantic/detector.py:305-309`; `resolve/resolver.py:52-66,210-221` |
| B25 | **Consequence:** wiring today's detector would let these happen in a default org:<br>• a score ≥ 0.80 BLOCKs;<br>• **every** over-budget, timeout or not-ready result BLOCKs.<br>On CPU, every prompt over ~126 tokens is over a 0.100 s budget (§4.4), so this would be an availability incident. | B23 + B24 + §4.4 |
| B26 | **v2 already has a default Tier-1 injection floor:**<br>• `DeterministicFindings` runs `BASE_DETECTORS`, which include `InjectionSignalDetector` (detector `deterministic.injection`, category-joined);<br>• `fw-prompt_injection-input` BLOCK governs its findings.<br>v1, the live production data plane, has no such floor: `_scan_prompt_sync` no longer iterates `ATTACK_PATTERNS`, and a zero-policy org gets no built-in injection block (PIPELINE-0032 restored only the PII/DoS floor).<br>**Two things are called "task 3.1":**<br>• the coordinator's note of 12 Nov names issue #98 task 3.1 (PG2-22M INT8 replacing Haiku on v1). No PG2 or onnxruntime code exists in v1 (`grep` of `gateway/ai_mesh_gateway` at `943a5f21`), so that task has not landed;<br>• the v1 code and changelog attribute the floor removal to the **policy-driven-detection** spec's task 3.1. The F4 register R16b on #115 says the same.<br>Either way the open decision is the same: should v2 keep a default injection floor? §5.10 shows F2 works under either answer. | `detect/deterministic/detectors.py:190-208`; `edge/dev_components.py:334-350`; `gateway/ai_mesh_gateway/scanner.py:1210-1217`; `docs/pipeline/CHANGELOG.md:261-283` (PIPELINE-0032); F4 `REGISTER.md` R16a/R16b |
| B27 | An EXECUTED finding may carry `spans=()`. REDACT and REWRITE build transformations only from `finding.spans` | `domain/finding.py:23-40`; `resolve/decision.py:57-80` |
| B28 | The compiler refuses `fail_closed` on a FLAG-only rule in incremental streaming | `plan/compiler.py:193-199` |

### 2.5 Observability

| # | Behaviour | Evidence |
|---|---|---|
| B29 | The audit row already carries `unavailable_detectors` and every finding's status. It never stores evidence | `audit/record.py:106-121,155-160` |
| B30 | The console `input_scan` stage has no notion of unavailable: it shows `allow`. The schema rejects keys outside `{name, action, ran, deciding_rule}` | `edge/console_stages.py:152-160`; `audit/console_schema.py:53,202-206` |
| B31 | There is no guard metric. The Publisher asserts a fixed `series_count()` | `edge/series_count.py:44-60` |

### 2.6 v1 and the earlier prototype

v1 has no local guard model:
- Tier-1 is regex (`gateway/ai_mesh_gateway/scanner.py`);
- Tier-2 is a remote LLM (`scanner.py:1137-1138,2146`);
- `services/guardrails/src/main.py:13-20` is an always-allow stub;
- control's `security_engines/` are regex plus the same remote scanner.

The semantic finding is therefore a v2-only addition: an expected diff for the GW02 parity harness.

The September prototype (`docs/plans/evidence/2026-09-23-runbook-v2-validation/rvproto/`) supplies:
- the exporter shape;
- token windowing;
- `disable_fallback()` and the provider assertion.

Its two parity files disagree on one benign score (0.002474 vs 0.000592). The cause is that the first
run padded every row to 512 with the attention mask set to 1 on the pads. With a correct mask,
padding is exactly neutral (§4.1).

## 3. Evidence inventory

### 3.1 What is in this PR and what backs it

| Evidence | Label | File (under `docs/plans/evidence/2026-11-11-f2-guard-model/`) | Re-runnable here today? |
|---|---|---|---|
| fp32 export, checker, op histogram | [M-nov] | `results/export.json` | needs weights |
| Export determinism (byte-identical re-export) | [M-nov] | `results/export_determinism.json` | needs weights |
| Parity on 811 rows plus 60 full windows; padding neutrality | [M-nov] | `results/parity.json`, `results/parity_full_windows.json` | needs weights |
| Tokenizer divergence (transformers 5 vs `tokenizer.json`) | [M-nov] | `results/tokenizer_divergence.json`, `results/golden_token_ids.json` | needs `tokenizer.json` (gated repo) |
| Manifest build, boot verify, 7-case tamper matrix | [M-nov] | `results/guard-manifest.json`, `guard-manifest.sha256`, `manifest_verify.json` | needs weights. **The digest was re-computed today from the committed manifest and matches**: `a7f29c2002bd69e7d7af090244340629d90d603fc3abf6ca56cd91b867c80527` |
| Per-window process CPU and wall, cold start, tokenizer cost (contended host) | [M-nov] | `results/bench_window.json` | needs weights |
| Off-loop prototype, GIL, terminate, overload, RSS | [M-nov] | `results/offloop.json`, `results/ort_bytes_rss.json` | needs weights |
| Language probe; injection token lengths | [M-nov] | `results/language_probe.json`, `results/injection_lengths.json` | needs weights / tokenizer |
| Dataset pins (revision + sha256 + licence) | [M-nov] | `results/datasets.json` | yes (public) |
| Clean single-window latency and saturated throughput, fp32 and INT8, 512 positions | [M-sep] | `…/2026-09-23-runbook-v2-validation/lanes/guard-bench/summary/cpu_latency.csv`, `cpu_throughput.csv` | — |
| Thread-CPU and wall p50/p99 per window for fp32, INT8 and fixed-shape fp32 | **[M-proxy]**, new | `results/proxy_latency.json`, `scripts/proxy_latency.py` | **yes, run today** (§4.7) |
| Current behaviour probe | [M-nov] | `results/current_behaviour_probe.jsonl` | yes (gateway_v2 venv) |

### 3.2 Evidence pending the weights (do not cite until produced)

Each item is produced by a committed script in the GitHub `guard-model` job (§9.4), never on this
shared host:
- the job runs on a larger runner, with a standard-runner baseline for latency;
- `HF_TOKEN` is an environment secret of that job only, never written to a file, log or artifact;
- the toolchain is that of `results/venv-freeze.txt`.

| # | Pending evidence | Script | Blocks |
|---|---|---|---|
| P1 | **L36-2 recall and FPR** per dataset and split, with Wilson 95 % intervals, AUC, per family (in-scope vs out-of-scope) | `eval_datasets.py` (written 11 Nov, never run: `eval_summary.json` is absent) | Exit; Q1 |
| P2 | **Threshold calibration per window count**, at target per-request FPR | `eval_datasets.py` (`fpr_by_window_count`) | §5.5; the manifest `decision` block (prototype value 0.5 is a placeholder) |
| P3 | **INT8 vs fp32 decision flips** and recall/FPR deltas on the same rows (McNemar) | new `int8_flips.py` in the implementation PR (quantise exactly as `proxy_latency.py` does) | D2; Q5 |
| P4 | **Window size and overlap** (510/254/126 payload; overlap 0/32/64/128) on the planted long-document test | `eval_datasets.py` `planted_test` (overlap 0/64/128 written; sizes added) | D6 |
| P5 | **Dilution (Prompt Overflow, T-G3)**: k ∈ {4, 16, 64} malicious tokens per 512-token window, for `max`, noisy-OR and top-2 mean | new `dilution_test.py` | D6; T-G3 |
| P6 | **Per-language recall** (es/fr/de/it/pt/ru from `yanismiraoui/prompt_injections`) | `eval_datasets.py` | §5.4 language posture; Q12 |
| P7 | **Real-bytes per-window thread-CPU p50/p99** at 16/64/128/256/512 positions, fp32 and INT8; must agree with [M-proxy] within ±10 % or the proxy is dropped | `bench_window.py` (switch to `thread_time_ns`) | Exit (L36-2 latency half) |
| P8 | Golden token ids reproduced via `tokenizers` (M2) | `tok_dump.py`, `tok_divergence.py` | M2 |
| P9 | Manifest re-build on this host: digest identical to `a7f29c20…` (same toolchain) or a new pin with a reason | `manifest.py` | R1 |
| P10 | Adversarial whitespace, homoglyph and zero-width rows score as their clean form after the fold (T-G4) | `language_probe.py` plus new rows | T-G4 |
| P11 | Benign system-prompt and tool-output FPR (C6: "benign confidentiality system prompts 0.998") | `eval_datasets.py` with role-tagged rows | Q9 |
| P12 | **V1-INJ-125**: v2's detection on the 125 v1 golden injection cases (§9.1), per family and per v2 component (deterministic signal / semantic), fp32 and INT8 | new `v1_golden_bench.py` | AC21; M6; Q15 |
| P13 | **V1-PII-21**: v2's result on the 21 obfuscated/encoded/split PII inputs (§9.1). Reported by F2's harness. PG2 is an injection classifier, so this measures v2's deterministic PII path, which F2 does not change | `v1_golden_bench.py` | AC22; F3 gate (Q15) |

## 4. Evidence detail

### 4.1 Export and parity [M-nov]

| Item | Value |
|---|---|
| Source | `model.safetensors` sha256 `5120e30bcd536ce285345d9ec104bea6bd6e8f94365b99a340c764f417ea5fa1`, 70,830,722 parameters (21.3 M encoder + 49.2 M of 128,100 × 384 embeddings) |
| ONNX | 284,233,017 bytes, sha256 `fe637bbc12fad24abf9dbbb0bf0dbb9f3bfa7ec568d5d7c34934b0141984d76a`, IR 8, opset imports `{"": 17}` only, 4,599 nodes, `onnx.checker` full check passed |
| Exporter | `torch.onnx.export(dynamo=False, opset_version=17, do_constant_folding=True)`, dynamic batch/seq axes, inputs `input_ids`, `attention_mask` → `logits` |
| Toolchain | Python 3.13.18, torch 2.15.0+cpu, transformers 5.22.2 (model loading only), onnx 1.23.2, onnxruntime 1.31.1, numpy 2.5.4 |
| Determinism | A second export in a fresh process is byte-identical. The September export with torch 2.14 is `2bf20e0f…`: the bytes depend on the exporter version, so the manifest pins the ONNX hash and the toolchain, and parity is re-proved on any toolchain change |
| Parity, 811 rows | max \|Δlogit\| **1.34 × 10⁻⁵**, max \|ΔP\| 4.3 × 10⁻⁶. Decisions **811/811** identical at 0.5, 0.7, 0.8, 0.9, 0.95 and 0.99. The rows are 748 corpus rows, 12 edge cases (empty, CJK, Hindi, zero-width, 510/511 tokens) and 24 long documents |
| Parity, 60 full 512-position windows | max \|Δlogit\| 4.8 × 10⁻⁶. Decisions 60/60. A batch of 8 equals the same rows run singly (max \|ΔP\| 0.0) |
| Padding | 400 rows in mixed-length batches, right-padded with a correct mask: max \|ΔP\| **0.0** |

### 4.2 The tokenizer is part of the model [M-nov]

| Comparison over 752 fixed texts | Rows with different ids |
|---|---|
| `tokenizers` 0.22.2 vs 0.23.3 on `tokenizer.json` | 0 |
| transformers 4.57.1 `AutoTokenizer` vs `tokenizer.json` | 0 |
| transformers **5.22.2** `AutoTokenizer` vs `tokenizer.json` | **752** |

Using the transformers-5 ids on the in-repo corpus at threshold 0.5:
- recall falls from 75/323 to **33/323**;
- FPR moves from 1/425 to 0/425;
- 45 decisions flip.

The cause: the shipped normalizer drops inner whitespace, which is Meta's adversarial-whitespace
hardening. Spaced, tab, NBSP and em-space variants of an injection all produce the clean ids and
score 0.999. transformers 5 substitutes its own normalizer.

The runtime therefore tokenizes with `tokenizers` from the pinned bytes only, never via
`transformers`, and golden ids are pinned (M2).

### 4.3 Manifest and boot verification [M-nov]

`scripts/manifest.py` stages the bundle as an image would carry it:
`model.onnx`, `tokenizer.json`, `config.json`, `LICENSE`, `USE_POLICY.md`, `NOTICE`, `golden.json`.
It then writes:
- `results/guard-manifest.json` (schema `amf.guard.manifest/v1`);
- its canonical digest: sha256 of sorted-key compact JSON = `a7f29c2002bd69e7…c80527`, re-computed today.

Boot verification and the tamper matrix:

| Case | Refused? | Reason | Time |
|---|---|---|---|
| Happy path (hash 7 files, session from the hashed bytes, 32-row golden self-test) | loads | — | 9.7 s wall in total, under load ≈ 6 |
| One bit flipped in `model.onnx` | yes | `sha256:model.onnx` | 1.3 s |
| One byte added to `tokenizer.json` | yes | `sha256:tokenizer.json` | 1.6 s |
| `LICENSE` missing | yes | `missing:LICENSE` | < 1 ms |
| Manifest threshold edited, files intact | yes | `manifest_digest` | < 1 ms |
| Wrong pinned digest | yes | `manifest_digest` | < 1 ms |
| Model swapped AND manifest rewritten to match | yes | `manifest_digest` (only the out-of-manifest pin stops this) | < 1 ms |
| Honest re-pinned manifest whose golden scores disagree with the model | yes | `golden_decision` | 8.2 s |

The prototype manifest says `max_windows: 32` and `threshold: 0.5`. Both are placeholders: the
implementation's manifest carries the values decided in §5.4 and §5.5 (P2, P4).

### 4.4 Per-window cost

**Contended host, real weights, process CPU per call, batch 1** [M-nov] (`results/bench_window.json`;
load average 4.9–7.6, so wall p99 is mostly other agents' work):

| Positions | intra-op 1: CPU p50 / p99 (ms) | wall p50 / p99 (ms) | intra-op 2: CPU p50 (ms) | wall p50 / p99 (ms) |
|---|---|---|---|---|
| 16 | 23.1 / 25.3 | 25 / 77 | 25.5 | 61 / 136 |
| 64 | 47.9 / 60.8 | 53 / 153 | 54.9 | 54 / 173 |
| 128 | 82.7 / 128.3 | 263 / 546 | 87.9 | 209 / 371 |
| 256 | 181.5 / 256.0 | 380 / 874 | 188.3 | 449 / 784 |
| 512 | **471 / 615** | 1,104 / 2,699 | 481 | 727 / 1,371 |
| 2 × 512 per run | 513 per window | — | 523 per window | — |
| 4 × 512 per run | 561 per window | — | 538 per window | — |

**Dedicated `c4-highcpu-16`, real weights, one 512-position window, wall per call** [M-sep]
(`cpu_latency.csv`):

| Threads | fp32 p50 / p99 (ms) | INT8 p50 / p99 (ms) |
|---|---|---|
| 1 | 292 / 295 | 206 / 208 |
| 2 | 173 / 176 | 129 / 137 |
| 4 | 123 / 125 | 98 / 100 |
| 8 | 88 / 90 | **74 / 75** |
| 16 | 91 / 95 | 76 / 85 |

**Saturated throughput on the same VM, all 16 vCPU busy, 512 positions** [M-sep]
(`cpu_throughput.csv`):

| processes × threads | fp32 windows/s (per vCPU) | INT8 windows/s (per vCPU) |
|---|---|---|
| 16 × 1 | 20.8 (1.30) | 26.1 (1.63) |
| 8 × 2 | 20.9 (1.30) | 27.2 (1.70) |
| 4 × 4 | 19.4 (1.21) | 24.8 (1.55) |
| 2 × 8 | 16.5 (1.03) | 20.8 (1.30) |
| 1 × 16 | 11.1 (0.70) | 13.2 (0.82) |

What these tables say:
- **Cost per window grows faster than linearly in positions.**
  - Per position it is cheapest at 128 (0.65 ms CPU per position) and dearest at 512 (0.92).
  - There is a fixed cost of roughly 15–20 ms per call even at 16 positions.
  - §4.7 shows that cost is neither shape glue nor the relative-position projections: ORT folds
    those, and a fixed-shape export gives no gain. It is the dense layers at small sequence
    length, which INT8 halves.
- **INT8's gain depends on length.**
  - At 512 positions: 1.42× single-thread and 1.25× saturated throughput [M-sep]; 1.29×
    [M-proxy].
  - At 16–128 positions: 1.7–2.0× [M-proxy, §4.7].
  - It is not the order of magnitude a 12 ms tax on long prompts would need, but it decides how
    many short prompts fit the budget.
- **Intra-op threads trade throughput for latency.**
  - 8 × 2 costs nothing against 16 × 1 and roughly halves latency (292 → 173 ms fp32).
  - 4 × 4 costs 7 %.
  - Beyond the physical core count (8) latency does not improve.
- **Batching does not reduce CPU per window** [M-nov]. It only reduces calls.

**Cold start per worker** [M-nov]:
- read and sha256 of 284 MB: 0.43 s;
- `InferenceSession(bytes)`: 1.39 s;
- first 512 run: 0.53 s;
- 4.8 s of CPU in total.

**Tokenizer** (`tokenizers`, thread CPU, 64 KiB inputs) [M-nov]:

| Text | Chars per token | CPU cost | Tokens in a 1,557-char "512-token" window (B9) |
|---|---|---|---|
| English prose | 4.76 | 0.57 ms per KiB | 426 |
| Python code | 3.23 | — | 452 |
| CJK | 1.13 | — | **1,381** |

### 4.5 Off-loop prototype [M-nov] (`results/offloop.json`, `scripts/offloop.py`)

| Measurement | Result |
|---|---|
| Event-loop lag while 6 requests × 2 full windows run **inline on the loop** | p99 = max = **16,811 ms** |
| Same, offloaded to a 1-thread / 2-thread executor sharing one session | max 28 / 45 ms, p99 8 / 23 ms (host load 6–7) |
| `Tokenizer.encode(64 KiB)` × 10 on an executor thread | loop lag p99 **183 ms**: `encode` holds the GIL |
| Same with `Tokenizer.encode_batch([text])` | p99 11 ms (idle baseline 3.8 ms): `encode_batch` releases the GIL |
| `RunOptions.terminate` on a running 8 × 512 batch | stops 16–40 ms after being set; the session stays usable |
| Overload: 40 requests × 2 windows, cap 8 in flight, 2 workers, budget 0.25 s | 36 answered UNAVAILABLE(overloaded) in < 1 ms; 4 UNAVAILABLE(timeout) at 0.25 s with runs terminated; in-flight counter back to 0 |
| RSS: Python + onnxruntime imported | 52 MB |
| First session warm at 1 × 512 / a second session in the same process | **+482 MB / +350 MB** |
| After one 8 × 512 batch (arena growth) | +279 MB more |
| Process-pool alternative (one child with its own session) | start 5.8 s; child RSS 1,051 MB; IPC overhead not measurable against a 128-token run (163.7 vs 183.8 ms p50) |
| Session from verified BYTES vs from path (`ort_bytes_rss.json`) | path +482 MB; bytes **+753 MB**; bytes with onnxruntime's retained copy released +482 MB |

onnxruntime's Python wrapper keeps the bytes it was given (`self._model_bytes`) so it can rebuild
the session for provider fallback. With `disable_fallback()` that copy is dead. The loader releases
it (the onnxruntime version is pinned by the manifest, and a unit test asserts the attribute exists
and is cleared), so the no-TOCTOU load uses the same memory as a path load.

### 4.6 L36-2 recall and FPR: pending (P1–P6, P11–P13)

Nothing is published yet. `eval_datasets.py` exists and has never been run, and
`results/eval_summary.json` does not exist. §9 is the protocol the run must follow. The table it
must produce:
- per dataset and split: n, TP/FN/FP/TN, recall and FPR with Wilson 95 % intervals at the chosen
  threshold and on the grid 0.3…0.995, plus AUC;
- in-repo corpus by family, with in-scope families (prompt_injection, jailbreak, paraphrase,
  goal_hijacking) separated from out-of-scope ones (sql/command injection, path traversal, data
  leakage, tool overreach, vector injection), which PG2 is not built to detect;
- FPR by window count on benign long text;
- per-language recall;
- the planted, window-size and dilution tests;
- INT8 vs fp32 flips;
- **V1-INJ-125 and V1-PII-21: v2 against v1, per id** (§9.1, M7);
- the count of rows excluded as over-length.

### 4.7 Weight-free latency proxy [M-proxy] (`scripts/proxy_latency.py`, `results/proxy_latency.json`)

Latency of dense fp32 and INT8 kernels depends on the graph and the shapes, not on the weight
values. The script:
- builds `DebertaV2ForSequenceClassification` with the DeBERTa-v3-xsmall configuration from random
  weights;
- **refuses to run unless the parameter count equals the real export's 70,830,722**;
- exports with the arguments of `export_onnx.py`;
- measures thread CPU (`time.thread_time_ns`, valid at intra-op 1) and wall per call.

No gated file and no token is involved. Accuracy is not measured (random weights).

**The run.**
- Command: `nice -n 10 <venv>/bin/python -I .../scripts/proxy_latency.py`.
- 80 s of CPU (user 71.6 s + sys 9.3 s), under the guide's ~2-minute serialisation threshold.
  It ran outside the heavy lock because about 7 jobs were queued on it.
- Load average 4.0 at start, 4.95 at end, on 2 vCPU. Other agents' suites (control tests, a
  golden run, a regex exhaustive check) were running, so wall times are about 2× thread CPU.
- Toolchain: identical to `results/venv-freeze.txt` (torch 2.15.0+cpu, onnxruntime 1.31.1).
- Settings: batch 1, intra-op 1, `ORT_ENABLE_ALL`.

| Positions | fp32 thread CPU p50 / p99 (ms) | fp32 wall p50 / p99 | INT8 thread CPU p50 / p99 (ms) | INT8 wall p50 / p99 | INT8 speed-up (CPU p50) | Real weights, process CPU p50 [M-nov] |
|---|---|---|---|---|---|---|
| 16 | 19.5 / 24.3 | 40 / 51 | **9.9 / 14.9** | 21 / 31 | 1.97× | 23.1 |
| 64 | 40.7 / 52.1 | 82 / 110 | 22.5 / 33.8 | 54 / 96 | 1.81× | 47.9 |
| 128 | 72.9 / 104.3 | 148 / 250 | 43.5 / 62.3 | 109 / 183 | 1.68× | 82.7 |
| 256 | 169.1 / 209.7 | 344 / 424 | 103.1 / 134.9 | 211 / 299 | 1.64× | 181.5 |
| 512 | 478.0 / 546.3 | 974 / 1,102 | 370.7 / 444.2 | 778 / 1,014 | 1.29× | 471 |

Runs per point: 120, 80, 50, 25 and 15 after 5 warm-ups. p99 at 512 is the maximum of 15 runs.

| Check | Result |
|---|---|
| Architecture identity | 70,830,722 parameters, equal to the real export. The exported graph has 4,721 nodes against the real 4,599: same weights and ops, slightly different shape glue from loading from a config rather than `from_pretrained` |
| Proxy vs real bytes, fp32 | −16 % at 16 positions, −15 % at 64, −12 % at 128, −7 % at 256, +1.5 % at 512, against the [M-nov] process-CPU column. That is within the noise of two contended runs, and P7 re-checks it on the real bytes |
| Fixed-shape export (`fp32_static_16`, `_64`) | 19.2 ms at 16 and 43.3 ms at 64 positions: **no gain** over the dynamic graph (19.5 / 40.7), although the optimised graph shrinks from 1,732 to 587 nodes. Shape glue is not the cost |
| Relative-position projections | The exported graph holds 146 MatMul/Gemm, 12 per layer plus 2, including `query_proj`/`key_proj` of the 512 relative embeddings. **ORT's optimiser folds those 24 input-independent products at session creation**: the optimised graph holds 122 = 146 − 24. The draft's hypothesis that they cost every call is therefore wrong |
| What the short-row cost is | The dense layers at small M. Every call streams about 85 MB of fp32 weights, which bounds a 16-position call by memory traffic. INT8 streams about a quarter of that, which is why it halves the cost at 16–64 positions but saves only 22 % at 512, where compute dominates. This explanation is consistent with the numbers but is an inference, not a profile |
| INT8 artifact | `quantize_dynamic(QInt8)` quantises MatMul and the embedding Gather: **86.9 MB** against 284 MB. The optimised graph uses 38 `DynamicQuantizeMatMul` + 36 `MatMulIntegerToFloat` |

Dedicated-core estimates [E] come from scaling these contended numbers by the [M-sep]/[M-proxy]
ratio at 512:
- fp32: 478 / 292 = 1.64;
- INT8: 371 / 206 = 1.80.

| Positions | fp32 per row on a dedicated core [E] | INT8 per row on a dedicated core [E] |
|---|---|---|
| 16 | ≈ 12 ms | ≈ 5.5 ms |
| 64 | ≈ 25 ms | ≈ 12.5 ms |
| 128 | ≈ 45 ms | ≈ 24 ms |
| 256 | ≈ 103 ms | ≈ 57 ms |
| 512 | 292 ms [M-sep] | 206 ms [M-sep] |

## 5. Design

### 5.0 Dependencies and alignment

- **S5 / F1a-1 (plan #103):** the production detector factory (N5) and the tenant-fair scan
  scheduler that moves the deterministic input scan off the loop. F2 adds one more per-request
  pass built from the request's compiled plan. Inference is NOT put on F1a-1's regex scheduler: one
  full window costs about 0.3–0.5 s of CPU, far above the regex budget, and would dominate every
  round. It gets its own guard thread with the same per-org-share rule. F2 implementation lands
  after F1a-1's factory, or behind `AMF_SEMANTIC_STAGE=off` before it.
- **Budget units:** admission and over-budget checks use predicted thread CPU (intra-op 1 means the
  calling thread does all the work), as F1a-1 does for regex. The deadline is wall clock, because
  the request waits on it.
- **S2 projector:** publishes the org plans that carry the governing rows (§5.9).
- **F1a-2 / F1a-3:** independent. F2 touches no model state and no dispatch code.
- **v1 is the live production data plane; v2 is profile-gated off in production compose.** F2
  therefore has no production effect until v2 serves production (F3). The rollout in §10 starts
  in dev compose.
- **GW25:** a GPU/TensorRT backend stays possible behind the same `GuardBackend` protocol and
  manifest schema, but #93 forbids GPU, so it is out of scope.

### 5.1 Where inference runs (D1)

| Option | For | Against | Evidence |
|---|---|---|---|
| **A. In-process**: one session per gateway worker on a dedicated guard thread, intra-op `t` (default 1) | No IPC and no extra process to supervise or roll. Fits the existing per-worker isolation (each worker builds its own app after the fork, `edge/serve.py:510-524`). 16 × 1 and 8 × 2 are the two throughput-optimal shapes. ORT releases the GIL in `run` | Memory per worker (§5.14). Fairness only per worker. Thread oversubscription if `workers × t` > cores | [M-sep] throughput table; [M-nov] off-loop and RSS |
| B. Per-host sidecar: one process with one session, over a Unix socket in the same pod | Memory once per host. Host-wide per-tenant fairness. Intra-op without oversubscription. Restart independent of workers | A new process, protocol, supervisor and readiness path. A shared failure domain per host. A second serialisation of token ids. For latency it buys nothing that option A with `t = 2` does not | [M-nov] process-pool row: IPC not measurable against inference |
| C. Separate guard service (network) | Independent scaling | **Rejected by #93** ("anything that adds a network hop on the admit path"). Adds RTT | — |

**Decision: A**, with these rules:
- `t` = `AMF_GUARD_INTRA_OP` (default 1, allowed 1–4).
- The boot refuses `workers × (1 + t) > ⌈cpu_quota⌉ × 2`: the event loop plus guard threads must
  not oversubscribe more than SMT allows.
- **B is the fallback, with no other design change** (the `GuardBackend` protocol and manifest
  are the same). It is triggered if:
  - T-G7 fails on the chosen SKU: guard RSS × workers does not fit the cgroup at the utilisation
    cap; or
  - T-CPU1 shows event-loop lag p99 regressing by more than 2 ms with the guard thread busy on
    the same cores.
- The scale plan already names this escape (#98 3.1, scale plan §6 and §13.2 T-G7).

### 5.2 Components (implementation PRs, not this one)

| Component | Module (new or changed) | Notes |
|---|---|---|
| Bundle loader and manifest verify | `detect/guard/pg2_bundle.py` (new) | `verify(bundle_dir, manifest_bytes, pinned_digest) -> LoadedBundle`; pure, no global state |
| Pinned digest | `detect/guard/pg2_manifest.lock` (new, one line) | changed only by PR; no env override in the production profile |
| Real backend | `detect/guard/local_onnx.py` (rewritten) | `LocalOnnxBackend(bundle, executor, clock, limits)`. The sha256 seam moves to `tests/` as an explicit double (GW36 §5.6) |
| Token windowing | `detect/semantic/windowing.py` (rewritten) | `plan_windows(ids, payload, overlap, max_windows)` returns rows plus char spans. The chars-per-token constants are deleted |
| Calibration | `detect/semantic/fpr_budget.py` | the placeholder `_threshold_for_fpr` (B10) is replaced by the measured table carried in the manifest |
| Semantic stage | `edge/semantic_stage.py` (new) | async. Builds role-scoped text from the canonical request (B17). One classify per request. Returns `tuple[Finding, ...]`, never raises |
| Lifecycle | `edge/openai_chat.py` phase 7, `edge/input_enforcement.py` | `scan_input(..., extra_findings=semantic)` merges before the ONE resolve |
| Threshold-aware join | `resolve/conflict.py` | §5.9 |
| Posture and refusal codes | `resolve/resolver.py`, `edge/input_enforcement.py`, `edge/posture` codes | §5.8 |
| Console | `edge/console_stages.py`, `audit/console_schema.py`, control `core/v2_audit_adapter.py` | an `unavailable` list and `semantic_mode` on `input_scan` |
| Metrics | `detect/guard/metrics.py` (new) plus `edge/series_count.py` | fixed series |
| Boot | `edge/production_profile.py`, `edge/dev_profile.py`, `edge/serve.py` | §5.11, §5.14 |
| Control | `core/firewall_v2_map.py` | only if Q6 or Q10 change which rows are published by default (§5.10) |

### 5.3 Export pipeline and manifest

1. **Fetch** (CI job `guard-model`, manual dispatch plus nightly):
   - Download the pristine files at the pinned revision `11614a155199674a0a95e6602d6ab0417b790ed0`,
     with `HF_TOKEN` from an Actions secret.
   - Verify sha256 against `tools/guard/sources.lock`: safetensors `5120e30b…`, tokenizer
     `6eb98335…`, config `1dc4742d…`.
   - Verify Meta's `checklist.chk`.
2. **Export fp32** exactly as `scripts/export_onnx.py`: TorchScript exporter, `dynamo=False`,
   opset 17. Moving to the `torch.export` exporter is a separate change that must re-pass M1.
3. **Derive INT8:** `onnxruntime.quantization.quantize_dynamic(weight_type=QInt8)` on the fp32 file,
   the same call `proxy_latency.py` times. Result: 86.9 MB; MatMul and the embedding Gather are
   quantised.
4. **Gate:**
   - M1 parity (fp32 vs PyTorch);
   - M2 golden ids;
   - M3 golden decisions.

   Any failure stops the job. For INT8 there is also **M6**, run on the same rows as L36-2:
   - ≥ 99 % decision agreement with fp32 at the calibrated threshold, on every evaluation row and
     on V1-INJ-125;
   - recall and FPR inside fp32's Wilson 95 % interval on every headline set;
   - no V1-INJ-125 case that fp32 detects lost by INT8.

   The bound is Q5. M6 decides which artifact the pin PR points at: INT8 if it passes, fp32
   otherwise. Both bundles are built and signed every run.
5. **Manifest:** `manifest.json` (schema `amf.guard.manifest/v1`) plus its canonical digest. Fields:
   - model and upstream: name, revision, licence, attribution;
   - `files`: sha256 and size of the 7 shipped files;
   - `source`: pristine hashes;
   - `export`: exporter, opset, IR version, toolchain;
   - `runtime`: onnxruntime and tokenizers versions, EP, **dtype**;
   - `io`: names, positive index 1, 512 positions, CLS 1, SEP 2, PAD 0, "truncation and padding
     disabled";
   - `windowing`: payload, overlap, max windows, aggregation rule;
   - `decision`: calibrated threshold table per window count;
   - `parity` and `golden` summaries.
6. **Sign and publish:**
   - The CI job pushes an OCI artifact `amf-guard-bundle:<digest16>` containing the 7 files plus
     `manifest.json`, read-only.
   - It signs the artifact and attaches SLSA build provenance with keyless Sigstore (GitHub OIDC).
   - The gateway image copies the bundle `--from=<digest>`.
7. **Pin:** a PR updates `pg2_manifest.lock`.
   - A required check verifies the cosign signature and provenance of the referenced artifact.
   - Review and merge of that PR is the trust decision.
   - At boot the gateway trusts only the in-source pin: no network, no key material on the host.

**Boot verification** (prototype `manifest.py:verify`, measured in §4.3) runs in this order:
1. the manifest digest equals the pin;
2. the schema validates;
3. each file is read once, and its size and sha256 checked;
4. the onnxruntime version equals the manifest's;
5. the tokenizer and session are built FROM THOSE BYTES (`disable_fallback()`, then the retained
   copy released);
6. the golden self-test: ids hash, score within 1e-4, same decision.

Any failure raises `ManifestMismatch(reason)`:
- **production profile with the stage on:** the process refuses to start, and the boot log names
  the reason;
- **dev profile:** the process starts with `amf_guard_ready 0`, and every request that the stage
  would serve gets UNAVAILABLE(manifest).

### 5.4 Runtime stage (one per worker process)

```
phase 7 ─┬─ start semantic task on the guard thread ──────────────────────┐
         │                                                                 │ tokenize once (encode_batch),
         ├─ deterministic scan (F1a-1: off-loop)                           │ predict cost, admit or refuse,
         │                                                                 │ ≤ 4 rows per session.run,
         └─ await semantic (≤ remaining stage budget) ◄────────────────────┘ aggregate → findings
            merge findings → ONE resolve → stage_upstream (unchanged)
```

- **When the stage runs at all:** only if the request's compiled plan contains at least one
  selected INPUT rule that a semantic finding would join (§5.9). The set is computed once per plan
  at compile time as `plan.semantic_rules`. Otherwise the stage is skipped and no SKIPPED finding is
  emitted (zero CPU). This is what makes §5.10 work either way.
- **Session:** one per worker:
  - intra-op `t`, inter-op 1, sequential, no spinning, `ORT_ENABLE_ALL`;
  - `disable_fallback()`, CPU EP asserted;
  - built from the verified bytes inside each worker after the fork, never in the supervisor.
- **Guard thread:** a dedicated `ThreadPoolExecutor(max_workers=1)` per worker, separate from
  `ScanExecutor`, so long prompts cannot starve error-frame and holdback scans. Tokenization runs
  on this thread with `encode_batch`, which releases the GIL (plain `encode` holds it, §4.5).
- **Overlap with the deterministic scan:** the semantic task starts first and is awaited after the
  deterministic scan, so their CPU overlaps. The single resolve is unchanged.
- **Text:** deltas on `message_content`, `content_part_text` and `completion_prompt`, with the role
  from `upstream_template.messages[input_message_index[i]].role` (completions `prompt` = USER).
  - Admitted by the governing rules' role scope: USER by default, TOOL opt-in (Q9), SYSTEM never.
  - Content parts of one message are joined with the empty separator, exactly as
    `_message_part_join_runs` does (T-G6). Admitted messages are joined with `"\n"`.
- **Canonicalisation before tokenization** (T-G4), in this order:
  1. identify the language on the unfolded text (fixes the order in B14);
  2. NFKC;
  3. strip Unicode `Cf`, which removes zero-width and tag characters (P10 verifies);
  4. the confusable fold, extended to the UTS-39 skeleton table (#98 3.2).

  The shipped tokenizer normalizer already collapses inner whitespace (§4.2).
- **Language posture:**
  - Latin script → classify, and record a heuristic `lang` in the evidence;
  - Cyrillic, CJK, Arabic, Devanagari and other scripts → UNAVAILABLE(unsupported_language).
  - `ru` leaves the supported set: B12 shows a clear Russian injection scoring 0.0008 [M-nov].
    Per-language recall for es/fr/de/it/pt is P6; until it is published those languages are
    classified and labelled.
- **Windowing:**
  - tokenize ONCE with `add_special_tokens=False`;
  - rows are `[CLS] + payload + [SEP]` with payload 510 tokens;
  - **overlap 64**: 95.5 % of explicit injections are ≤ 64 tokens (`injection_lengths.json`: p50
    14, p95 61, p99 153), so they appear whole in at least one window, at a cost of 14 % more
    windows on long text;
  - `max_windows` default 16 (510 + 15 × 446 = 7,200 tokens); more → UNAVAILABLE(over_length)
    without inference.

  Window size (510/254/126) is P4. The 128-position window is the cheapest per position (§4.4) and
  concentrates diluted payloads, so it is the expected alternative if P4 and P5 favour it.
- **Batching:** equal-length rows run together, at most 4 rows per `session.run` (this bounds the
  activation arena, §5.14). No padding. Batching does not lower CPU per window.
- **Aggregation:**
  - request score = max over windows, from the owning window;
  - plus a dilution rule (T-G3), chosen by P5 among noisy-OR over windows (`1 − Π(1 − pᵢ)`) and
    top-2 mean, at the same calibrated FPR;
  - until P5 is published: max only, with the gap stated in product docs (#98 3.4, MASTER D3 gap
    list).
- **Finding:** `detector = "semantic"`, category `PROMPT_INJECTION`, `confidence = score`, and
  **`spans = ()`** (B27).
  - Window char spans go to the audit-side stage record for the console highlight only.
  - So a REDACT or REWRITE rule that governs a semantic finding can produce no edit and falls into
    the existing unmaskable/BLOCK path: "semantic spans never feed REDACT" (N12).
  - PG2 has one malicious label for injection and jailbreak, so no JAILBREAK finding is emitted.
    `fw-jailbreak-input` is fed only by the deterministic signal detector (declared in the boot
    log; B5 stops mattering).

### 5.5 Threshold and calibration

- The manifest carries `decision.thresholds[W]`: the per-window threshold that holds the
  per-request FPR at the target for a request of `W` windows. It is measured on the benign
  calibration split, never on the evaluation split (P2).
  - The target is 1 % (the ledger measured 2.4–4.4 % per long window at 0.5,
    `FINDINGS_LEDGER.md:264`).
  - `fpr_budget.derive_threshold` keeps its formula and replaces only the placeholder inverse
    (B10) with a lookup in that table.
- The effective threshold for a rule is `max(thresholds[W], rule.threshold)`, as the detector does
  today (`detector.py:305-309`). With control's default `prompt_injection_threshold` of 0.80, the
  org setting is the operative bar unless the calibrated value is higher.
- The stage emits a finding when `score ≥ min over governing rules of that bar`. The resolver's
  threshold-aware join (§5.9) then selects only the rules whose own bar the score meets.

### 5.6 Budget policy and the latency target

- **Two budgets, chosen by the org's posture (§5.8):**
  - **`degrade` orgs:** `B = AMF_SEMANTIC_BUDGET_MS`, default **12 ms** (the #93 tax; 60 % of a
    20 ms p99 total). Content that does not fit is not classified inline: it is forwarded and
    recorded as UNAVAILABLE(over_budget). This holds the latency target by construction, and it
    also caps the guard's CPU per request.
  - **`fail_closed` orgs:** `B_fc = AMF_SEMANTIC_FAIL_CLOSED_BUDGET_MS`, default **2,000 ms**.
    Everything up to that predicted cost is classified before the request is forwarded. Above it,
    the request is refused (413, §5.8). Latency grows with prompt length; that is the trade the
    org chose.
  - Each budget is also the request's deadline for the stage. Both are owner decisions (Q8). The
    boot log prints the coverage each implies on this host.
- **Admission by predicted cost:**
  - At boot the warm-up measures thread CPU for one row at 16/64/128/256/512 positions on this
    host. That table is exported, and `c(n)` is interpolated from it.
  - A request with rows `r₁…r_W` is admitted only if `Σ c(len rᵢ)` is within its org's budget
    (`B` or `B_fc`). Otherwise it gets UNAVAILABLE(over_budget) at once, without inference.
  - This replaces the declared `AMF_GUARD_WINDOWS_PER_SECOND` (B4).
- **In-flight bound per worker:** one guard thread runs the work in order.
  - A request is admitted only if the predicted CPU already admitted and unfinished on this worker,
    plus its own cost, fits its own budget. Otherwise it gets UNAVAILABLE(overloaded) at once
    instead of timing out.
  - So a `degrade` request never waits behind a long `fail_closed` scan; while one runs, that
    worker's `degrade` traffic is forwarded unscanned and counted.
  - The total in flight is bounded by `Q = B_fc`.
  - Per-org share (work-conserving): while another org has work in flight, one org may hold at most
    half of `Q`; alone, it may use all of `Q`.
  - The counter is a dict keyed by org with entries removed at zero, so its size is bounded by the
    requests in flight.
- **Deadline:** when it expires, `RunOptions.terminate = True`, the worker thread is freed, and the
  request gets UNAVAILABLE(timeout) (§4.5: the run stops 16–40 ms after the flag).
- **Coverage on CPU:** what fits the 12 ms budget inline, single row, intra-op 1.

| Positions (payload tokens) | fp32, this host [M-proxy] p50 / p99 | INT8, this host [M-proxy] p50 / p99 | fp32, dedicated core [E] | INT8, dedicated core [E] |
|---|---|---|---|---|
| 16 (≤ 14) | 19.5 / 24.3: no | 9.9 / 14.9: p50 only | ≈ 12: borderline | ≈ 5.5: **yes** |
| 64 (≤ 62) | 40.7 / 52.1: no | 22.5 / 33.8: no | ≈ 25: no | ≈ 12.5: borderline |
| 128 (≤ 126) | 72.9 / 104.3: no | 43.5 / 62.3: no | ≈ 45: no | ≈ 24: no |

  - **Conclusion [E]:** with a 12 ms budget on CPU,
    - INT8 classifies prompts up to about **60 tokens** inline on a dedicated core (about 14 on
      this contended host);
    - fp32 classifies up to about **14 tokens**.
  - Explicit injections are short: p50 14, p95 61 tokens. Jailbreak prompts are long: p50 372,
    p90 1,046 tokens (`results/injection_lengths.json`). **So for `degrade` orgs the CPU stage
    catches short instruction-override attempts, and almost never a typical jailbreak prompt,
    inline.**
  - Jailbreaks are scanned for `fail_closed` orgs, at their latency cost. Deferred classification
    for `degrade` orgs is Q8.
  - Two intra-op threads (the 8 × 2 shape costs no throughput [M-sep]) cut single-row latency to
    0.59× at 512 [M-sep]. At short lengths the gain is unmeasured (P7 grid).

- **AMF_Q_SAFE (B20).** The production profile sizes all admission depths from a declared guard
  rate. With F2 that rate is no longer a declaration:
  - the boot computes `q_safe = workers × 1000 / c(n_p50)` (requests/s at the median prompt length
    `n_p50`, a measured traffic input, `AMF_GUARD_MEDIAN_TOKENS`, default 64);
  - it logs the result next to the declared value;
  - it refuses to start if the declared value exceeds the computed one by more than 2×.

### 5.7 Readiness

Readiness fixes B3 and B7.
- **Probe:**
  - A background task runs one short golden row on the guard thread at boot.
  - It runs again whenever no inference has succeeded for `AMF_GUARD_PROBE_INTERVAL_S` (default
    2 s).
  - One probe costs about one 16-position call (§4.4, §4.7), so an idle worker spends about 1 % of
    a core and a busy one nothing.
- **Ready** means all three: the manifest is verified, a run (probe or request) succeeded within
  `AMF_GUARD_STALENESS_S` (default 6 s), and the last probe reproduced its golden score within
  1e-4.
- **A hung inference thread** makes the probe late, so readiness turns false within one staleness
  bound. Readiness is never derived from "thread alive" (C35).
- **`/readyz`:**
  - It is gated on the guard **only at boot**: a worker with the stage on reports not-ready until
    its first probe passes. This keeps a rolling deploy from sending traffic to cold workers, which
    would answer `fail_closed` orgs with 503s.
  - After that first success, guard readiness never changes `/readyz`. A guard fault must not drain
    the fleet, because a bad bundle would take every host out. `amf_guard_ready` and alerts carry it
    instead (Q14).

### 5.8 Failure posture per org (D4)

UNAVAILABLE reasons fall into three classes. All of them become a finding with `status=UNAVAILABLE`
and the reason in its evidence.

| Class | Reasons | Meaning | `fail_closed` org | `degrade` org |
|---|---|---|---|---|
| U1 guard down | `not_ready`, `manifest`, `error` | the model cannot run | refuse **503 `guard_unavailable`** with `Retry-After: 2` | forward; record |
| U2 over capacity | `overloaded`, `timeout` | the model runs, but not for this request now | refuse **503 `guard_unavailable`** with `Retry-After: 1` | forward; record |
| U3 unscannable by policy | `over_length` (W > `max_windows`), `over_budget` (predicted cost > the org's budget, §5.6), `unsupported_language` | this request will not be scanned on retry either | refuse **413 `prompt_too_long_for_guard`** / **422 `unsupported_language_for_guard`**, no retry | forward; record (`over_budget` is the common case for long prompts) |

- **Where the org's posture comes from:** the governing rows' `on_unavailable`. `fail_closed`
  means refuse; `fail_open` is the `degrade` column.
  - Today control derives it from `tier2_fail_closed_enabled` (model default True, B23), so with
    no change every org would be `fail_closed`. Every request would then carry a full-length CPU
    scan, up to 2 s.
  - v1's fail-closed Tier-2 scan was gated by an org tier AND an env flag that defaults to false
    (`2026-08-25-FINAL-verification-and-platform-agnostic-scale-plan.md:604`), so v1's effective
    default was fail-open.
  - Q7 therefore recommends that control derive the semantic-governing rows' `on_unavailable` from
    an explicit opt-in field (default off). T-H5 needs the fail-closed path to exist and be proven
    by fixture (AC12), not to be every org's default.
  - The gateway design is the same either way.
- **Refusal codes are new posture codes**, never `input_blocked`:
  - the resolver marks a BLOCK that came only from UNAVAILABLE findings with
    `codes = ("guard_unavailable",)` or the U3 code, so `_proceed` renders the distinct status;
  - this is T-G5 ("503 not 451"), and it fixes N13 at the client;
  - zero provider calls are made in every refusal case (L36-1).
- **U3 must refuse for `fail_closed` orgs.** Otherwise any of these bypasses a fail-closed org:
  - padding a prompt past `max_windows` or past the budget;
  - writing the injection in an unsupported script.

  For `degrade` orgs that bypass is visible but not blocked, and product docs state it.
- **Platform switch** `AMF_SEMANTIC_ENFORCEMENT ∈ {shadow, enforce}`, default **shadow**:
  - in shadow, the stage runs with the org's budget, and its findings go to the audit row, console
    and metrics with `semantic_mode: "shadow"`, plus the code it would have returned
    (`would_refuse`). They are NOT passed to the resolver: no refusal and no block from the
    semantic stage. This is how R2 measures the refusal rate before anyone sees one;
  - `enforce` hands them to the resolver, and the org rows then decide;
  - the gates for the switch are in §10;
  - an allow-list `V2_SEMANTIC_ENFORCE_ORGS` (same pattern as `V2_CANARY_ORGS`) allows per-org
    canaries.

### 5.9 Rules and the join (D3)

- **No new rule type.** Semantic findings join the org's existing category rows (B22, B23):
  `fw-prompt_injection-input`, and `fw-threat-prompt_injection-input` when threat intel is on.
  Their action, threshold and `on_unavailable` come from the org's settings, which is what control
  documents them for. This replaces the draft's identity join with a new
  `platform.semantic.prompt_guard` rule, which would have made the existing org settings dead for
  semantic findings.
- **Threshold-aware join** (`conflict.py:_rules_for`): a category-joined rule with a non-null
  `threshold` selects an EXECUTED finding only if `finding.confidence ≥ rule.threshold`.
  - Deterministic EXECUTED findings carry confidence 1.0 (`detect/deterministic/detectors.py:82-86`),
    so their joins are unchanged. U6 pins this.
  - UNAVAILABLE findings carry no confidence, so they still join every governing rule, and the
    posture applies.
- **Surfaces:** control publishes these rows with `surfaces: ["chat"]` (`firewall_v2_map.py:521`).
  The stage therefore runs only for chat until control widens them. That is a control change
  tracked with F4 R1 (#115) and Q10, not a gateway default.
- **Monitor orgs** (`enforcement_mode = monitor`): the rows are MONITOR, so findings are recorded
  and contribute nothing (`resolver.py:52-66`).

### 5.10 "Task 3.1": the plan works either way (D5)

Facts (B26):
- v1 has no built-in injection floor;
- v2 has one by default (the deterministic signal detector plus the default-on BLOCK row);
- PG2 adds a semantic signal under the same rows.

v1 is not changed in either case (owner, 12 Nov).

| Owner answer | What control publishes for v2 | What the v2 gateway does | Change needed |
|---|---|---|---|
| **No default floor** (injection is policy-only, opt-in, as v1 is today) | `fw-prompt_injection-input` only when the org explicitly turned injection detection on. That is a new default in `_input_drafts` (`firewall_v2_map.py:400-427`) and a data migration for existing orgs | The stage is skipped for orgs whose plan has no governing row (§5.4: zero CPU). Deterministic injection signals stop blocking for those orgs too. V1-INJ-125 is then measured for an org that opted in | control only |
| **A default floor** (v2 keeps what it publishes today) | unchanged: the row is on by default | The stage runs for every org with content filtering on; capacity in §7 assumes all traffic. V1-INJ-125 is measured for a default org | none |

The gateway design is identical in both rows. Only the default of one control field and the
capacity line differ. The recommended default is in Q6.

### 5.11 Configuration (read once at boot, validated, logged)

| Variable | Default | Production profile |
|---|---|---|
| `AMF_SEMANTIC_STAGE` | `off` | `on` requires the bundle and an explicit backend |
| `AMF_SEMANTIC_ENFORCEMENT` | `shadow` | `enforce` only after the §10 R3 gate |
| `AMF_GUARD_BACKEND` | `local_onnx` (unchanged) | must be explicit |
| `AMF_GUARD_BUNDLE_DIR` | — | required; read-only mount |
| `AMF_GUARD_INTRA_OP` (`t`) | 1 | 1–4; oversubscription check (§5.1) |
| `AMF_SEMANTIC_BUDGET_MS` (`B`, `degrade` orgs) | 12 | > 0; the boot log prints the coverage it implies |
| `AMF_SEMANTIC_FAIL_CLOSED_BUDGET_MS` (`B_fc`) | 2,000 | ≥ `B` |
| `AMF_GUARD_OVERLAP_TOKENS` | 64 | 0–255 |
| `AMF_GUARD_MAX_WINDOWS` | 16 | ≥ 1 |
| `AMF_GUARD_PROBE_INTERVAL_S` / `AMF_GUARD_STALENESS_S` | 2 / 6 | > 0; staleness ≥ 2 × interval |
| `AMF_GUARD_MEDIAN_TOKENS` | 64 | > 0 (for the `q_safe` cross-check) |
| `V2_SEMANTIC_ENFORCE_ORGS` | empty | per-org canary allow-list |
| pinned manifest digest | in source | no override |

Threshold, window size and aggregation rule come from the manifest, versioned with the bytes.

### 5.12 Metrics (fixed names, closed label sets, counted in `series_count`)

| Series | Type | Labels |
|---|---|---|
| `amf_guard_ready` | gauge 0/1 | — |
| `amf_guard_requests_total` | counter | `outcome ∈ {executed_hit, executed_clean, unavailable, skipped}` (4) |
| `amf_guard_unavailable_total` | counter | `reason ∈ {over_length, over_budget, overloaded, timeout, not_ready, manifest, unsupported_language, error}` (8): the N13 fixed-name counter |
| `amf_guard_windows` | histogram, buckets 1, 2, 4, 8, 16 | — (3 native series) |
| `amf_guard_stage_seconds` | histogram (wall, stage entry → findings) | — (3) |
| `amf_guard_row_cpu_seconds` | gauge (boot warm-up `c(512)`) | — |
| `amf_guard_inflight_cpu_seconds` | gauge | — |
| `amf_guard_probe_age_seconds` | gauge | — |

There is no org, key, model or language label; per-org detail goes to the audit row only.
Fixed total: 1 + 4 + 8 + 3 + 3 + 1 + 1 + 1 = **22** series, added to `series_count()` as a
constant.

### 5.13 Audit, trace and console (N13)

- **Finding:** shape unchanged. `detector_version = "semantic:{manifest16}:{tokenizer16}:{ruleset}"`.
  The evidence (never stored, B29) carries `W`, tokens, the argmax window, `lang`, the reason and
  the threshold used.
- **Audit row:** gains a `semantic` block. It holds mode (`shadow|enforce`), outcome, reason, `W`,
  the stage wall time, and the window char span for the console highlight. It holds no text and
  no score beyond what the finding already carries.
- **Console:** the `input_scan` stage gains `"unavailable": ["semantic"]` and `"semantic_mode"`
  (added to `_STAGE_KEYS`, B30). Control's `v2_audit_adapter` passes both through into
  `metadata.pipeline_trace`.
- **Refusals:** U1/U2/U3 refusals render `input_scan` as `error` with the refuse code. They never
  render as `block`.
- **Boot log:** names the backend, the manifest digest, the dtype, `t`, `B` and the coverage it
  implies, the warm-up cost table, `Q`, the enforcement mode, and `guard_routing -> N/A
  (local_onnx)` (G14).

### 5.14 Memory per worker and boot

- **fp32:** +482 MB for the session after the first 512 run [M-nov], plus up to +279 MB of arena
  after an 8 × 512 batch. The stage caps a run at 4 rows, so the plan budgets **0.8 GB per worker**
  for fp32.
- **INT8:** the file is 86.9 MB against 284 MB [M-proxy], so the resident session should be roughly
  a third. P7 measures it; until then the fp32 budget is used.
- **`per_worker_rss`** must include this (B21): 400 MiB + 800 MB ≈ 1.2 GB per worker when the stage
  is on. On a 16-vCPU / 64 GiB node, 16 workers need ≈ 19 GB, which is within the cap: T-G7 is
  expected to pass in-process [E]. On 32 GiB nodes it is still within the cap at the default
  utilisation.
- **Boot:**
  - Each worker spends ≈ 5–6 s of CPU on hash, session, warm-up table and golden test.
  - All workers of a host start together, so boot is CPU-bound for about `n × 5 s / cores`.
  - `/readyz` waits for the first probe (§5.7).

## 6. Security

### 6.1 Model supply chain

| Step | Control |
|---|---|
| Source | Meta's gated repo `meta-llama/Llama-Prompt-Guard-2-22M` at revision `11614a15…`. The licence must be accepted by an account acting for the company (Q2) |
| Pristine bytes | Pinned sha256: safetensors `5120e30b…5fa1`, tokenizer `6eb98335…89c1`, config `1dc4742d…`. These equal the bytes pinned by the September prototype on a different machine, and Meta's `checklist.chk` md5s match [M-nov] |
| Format | safetensors: no pickle, no code on load. `config.json` has no `auto_map`, and `trust_remote_code=False` is pinned in the exporter |
| Export | A dedicated CI job, not the gateway build, with a hash-locked toolchain; network limited to PyPI, PyTorch and HF. The export is byte-deterministic for a fixed toolchain [M-nov], so CI can re-export and compare |
| Graph | `onnx.checker` full check. Opset imports are default-domain v17 only: no custom ops, no external-data file. CPU EP only, `disable_fallback()` |
| Artifact | An OCI artifact addressed by digest, cosign-signed with SLSA provenance (§5.3). The gateway trusts only the in-source pin |
| HF token | An Actions secret used only by the fetch step. Never in the image, logs, manifest, this repository or any PR. This plan's evidence used no token (the files were already on the previous host) |
| Toolchain CVEs | onnxruntime, tokenizers and numpy versions are pinned by the manifest and in the dependency scan (OSV), and bumped only with M1–M3 re-run |

### 6.2 Licence obligations (Llama 4 Community License; `LICENSE` and `USE_POLICY.md` are in the bundle)

Shipping the weights or the ONNX conversion in an image that anyone outside the company can pull is
distribution (§1.b.i). The obligations are:
1. Ship the Agreement (`LICENSE`).
2. Ship a `NOTICE` file with exactly "Llama 4 is licensed under the Llama 4 Community License,
   Copyright © Meta Platforms, Inc. All Rights Reserved.". The prototype writes it, and its hash is
   in the manifest.
3. Display "Built with Llama" on a related website, UI or documentation (Q2).
4. Prefix a derived model name with "Llama": name the artifact `Llama-Prompt-Guard-2-22M-onnx-fp32`
   (and `-int8`).
5. Follow the Acceptable Use Policy, which flows to customers. It includes disclosing known dangers
   of the AI system (publish the measured limits) and the military/espionage exclusions (Q3).
6. The 700 M MAU clause does not apply.
7. No trademark use beyond "Built with Llama".
8. The base model (DeBERTa-v3-xsmall) is MIT. Whether an MIT notice is also owed is Q3; the bundle
   can carry it at no cost.
9. Weights and datasets are never committed. Only ids, revisions, hashes and scores are committed.
   `databricks-dolly-15k` is CC BY-SA 3.0 and is used for measurement only.

### 6.3 Model identity and tampering

- **The pin lives outside the manifest.** The "swap the model AND rewrite the manifest" forgery is
  refused only because the digest is pinned in source (`manifest_verify.json`). An env var
  carrying the hash would re-create B2, so the production profile accepts none.
- **No TOCTOU:** each file is read once, hashed, and the session and tokenizer are built from those
  bytes.
- **The golden self-test at boot** catches a matching file on a runtime that changes decisions:
  identity matched, behaviour changed (C35).
- **`detector_version` on every finding** ties each decision to exact bytes.

### 6.4 Adversarial inputs

| Threat | Mitigation | Evidence or test |
|---|---|---|
| CPU exhaustion with long prompts | token cap, predicted-cost admission, per-worker in-flight bound with per-org share, deadline plus terminate. The excess becomes UNAVAILABLE, never an unbounded queue | §4.5 overload row; U8, U9 |
| Forcing UNAVAILABLE to slip past | U1/U2/U3 refuse for `fail_closed` orgs (§5.8). For `degrade` orgs it is visible (trace, counter, audit). Deterministic detectors still run | U7, L4.4 |
| Prompt Overflow / dilution (T-G3) | 64-token overlap; dilution rule chosen by P5; window size by P4 | P4, P5, M4 |
| Homoglyphs, zero-width and tag characters, whitespace (T-G4) | NFKC, `Cf` strip, UTS-39 skeleton fold before tokenization; the shipped normalizer handles whitespace | P10; M3 rows `ws-1..3`, `nfc-1` |
| Content-array split (T-G6) | parts of one message joined before windowing; messages joined | U2 |
| Translation | non-Latin → UNAVAILABLE(unsupported_language), which a `fail_closed` org refuses; Latin non-English is measured (P6) | P6 |
| Tokenizer cost | 0.57 ms CPU per KiB, linear [M-nov]; counted in the predicted cost; request bytes already bounded by the contract | U8 |
| Score oracle | the client sees no score and no semantic-specific detail; refusal codes carry only the class | U7 |
| Benign system prompts flagged (C6: 0.998) | role scope excludes SYSTEM | P11 |
| Indirect injection via tool output | TOOL is opt-in (Q9); PG2's indirect-injection FNR is high (MASTER D3 gap list), stated in product docs | P11 |
| Malicious bundle | in-source pin, signature and provenance checked in CI, golden self-test, CPU EP only, no custom ops | U3, L4.5 |

## 7. Capacity and cost against the $5k envelope

All figures are **[E]** unless stated, built from [M-sep] and the cost ratios in §4.4/§4.7. Every
one is re-measured on the purchase candidate by T-CPU1 before money is spent.

- **Cost per window at saturation**, `c_sat(n)` in vCPU-ms:
  - 512 positions: fp32 **769**, INT8 **614** [M-sep: 1000 / 1.30 and 1000 / 1.63].
  - Shorter windows scale by the measured CPU ratio `c(n)/c(512)` (§4.7 table; §4.4 if the proxy
    is unavailable).
- **Guard vCPU per 1,000 requests/s** = `Σ_windows c_sat / ρ`. Two useful properties:
  - At 1k RPS, vCPU-ms per request equals busy vCPUs.
  - ρ is the target utilisation for p99 headroom; ρ = 0.6 is used below.
- **Price:** the scale plan's own figure is $400–500 per 16-vCPU node-month (§13.1: "1–3 × 16-vCPU …
  $400–1,500"), i.e. **$25–31 per vCPU-month**. That figure is from the plan, not a fresh quote.

Each row's per-window cost is `c_sat(512)` × the [M-proxy] ratio for its length. All columns are
[E].

| Request shape (admitted tokens) | `c_sat` per request fp32 / INT8 (vCPU-ms) | Guard vCPU per 1k RPS at ρ = 0.6, fp32 / INT8 | $/month per 1k RPS, fp32 / INT8 | RPS per 100 guard vCPU, fp32 / INT8 |
|---|---|---|---|---|
| ≤ 14 (one 16-position row) | 31 / 16 | 52 / 27 | $1.3–1.6k / $0.7–0.8k | 1,920 / 3,660 |
| ≤ 62 (one 64-position row) | 66 / 37 | 109 / 62 | $2.7–3.4k / $1.6–1.9k | 920 / 1,610 |
| ≤ 126 (one 128-position row) | 117 / 72 | 196 / 120 | $4.9–6.1k / $3.0–3.7k | 510 / 830 |
| ≤ 254 (one 256-position row) | 272 / 171 | 454 / 285 | $11–14k / $7.1–8.8k | 220 / 350 |
| ≤ 510 (one full window) | 769 / 614 | 1,282 / 1,023 | $32–40k / $26–32k | 78 / 98 |
| 2,000 (5 full windows at overlap 64) | 3,845 / 3,070 | 6,408 / 5,117 | $160–199k / $128–159k | 16 / 20 |
| **`degrade` org, any length** (the 12 ms budget caps it; ×2.6–3.0 saturated vs single-core [M-sep]) | ≤ 32 / ≤ 36 | ≤ 53 / ≤ 60 | ≤ $1.3–1.6k / ≤ $1.5–1.9k | ≥ 1,880 / ≥ 1,670 |

Reading the table:
- **About 100 guard vCPU (≈ $2.5–3.1k/month, roughly the whole gateway line) serves:**

  | Prompts | fp32 | INT8 |
  |---|---|---|
  | ≤ 62 tokens | ≈ 900 RPS | ≈ 1,600 RPS |
  | ≤ 126 tokens | ≈ 500 RPS | ≈ 830 RPS |
  | full 512-token windows | ≈ 80 RPS | ≈ 100 RPS |
  | `degrade` orgs at any prompt length (bounded by the budget) | ≈ 1,700–1,900 RPS | ≈ 1,700–1,900 RPS |

  - `fail_closed` orgs pay for full-length scans, so the posture mix (Q7) is a capacity input.
  - Production peaks today at 30–85 requests/s (`2026-08-22-MASTER-hot-path-capacity-architecture.md`
    §D2), so the envelope is ample now.
  - #93 already says not to provision 100k RPS inside $5k.
- **The guard is not the whole bill.**
  - The non-gateway lines of §13.1 (control, Postgres, Valkey, GKE, LB, NAT) take $0.8–1.5k.
  - T-COST1 alerts at $4k.
  - So **≈ $2.5–3.2k/month is available for gateway nodes**, i.e. 80–130 vCPU, shared with TLS,
    parsing, deterministic scans and streaming.
- **Horizontal scaling:**
  - Each worker owns its session and guard thread and shares nothing on the request path, so guard
    capacity is linear in vCPUs.
  - The per-worker admission bound (§5.6) makes overload a fast, visible UNAVAILABLE(overloaded)
    rather than a queue.
  - The fleet scales on `amf_guard_inflight_cpu_seconds` and the overloaded rate.
- **INT8 saves 20 % of guard vCPU at 512 positions and 38–48 % at ≤ 126 tokens** [E]. It also
  roughly doubles the prompt length that fits the 12 ms budget (§5.6). That saving is what Q5
  weighs against the flip rate (P3).
- **Multi-turn traffic re-scans the whole conversation every turn (Q11)**, so a 10-turn chat costs
  about 10× its last message.
  - A shared verdict cache keyed by `org + classifier digest + sha256(message)` would remove that.
  - In-process it would hit only when a turn lands on the same worker, so it is deferred to F5,
    where a shared store's hit rate can be measured.
  - It caches scores, not decisions, so no policy version is needed (T-H4).

## 8. Acceptance criteria (checklist mapped to tests and evidence)

Status key: ✅ evidence in this PR · ⏳ pending the weights (§3.2) · ☐ implementation PR.

**Model artifact**
- [x] AC1 fp32 export, opset 17, checker passed, deterministic: `export.json`, `export_determinism.json` ✅ (M1 re-proves in CI ☐)
- [x] AC2 parity with PyTorch fp32: max \|Δlogit\| < 1e-4, decisions 100 % on 811 + 60 rows: `parity*.json` ✅ (M1 ☐)
- [x] AC3 tokenizer pinned to `tokenizer.json` via `tokenizers`; transformers 5 divergence shown: `tokenizer_divergence.json` ✅ (M2 ⏳ P8)
- [x] AC4 manifest refuses all 7 tamper cases; digest pinned outside the manifest: `manifest_verify.json` ✅ (U3, L4.5 ☐)
- [ ] AC5 serving precision decided by evidence: INT8 serves only if M6 passes (P3, P12); otherwise fp32 serves. Both bundles are built and signed: M6 ⏳ P3, Q5

**Stage behaviour (N12, N13, G9, G10)**
- [ ] AC6 bootstraps without a request; never wedges when idle (B3, B7): U4, L4.7 ☐
- [ ] AC7 one classify per request, whatever the number of governing rules (B8): U5 ☐
- [ ] AC8 token windows: every row ≤ 512 positions; any span ≤ overlap lies inside one row (B9): U1 ☐
- [ ] AC9 admission by measured cost, not a declared rate (B4); over-budget, overloaded and timeout answered within budget + 5 ms with the run terminated: U8, U9 ☐
- [ ] AC10 off-loop: event-loop lag p99 while the guard is saturated ≤ the GW28 bound: U12, L4.6 ☐ (prototype: §4.5 ✅)
- [ ] AC11 threshold-aware category join; deterministic joins unchanged; semantic findings carry no spans: U6 ☐
- [ ] AC12 per-org posture: U1/U2 → 503 `guard_unavailable` with Retry-After, U3 → 413/422 for `fail_closed`; forward and record for `degrade`; never `input_blocked`; zero provider calls on refusal: U7, L4.4 ☐
- [ ] AC13 shadow mode: findings recorded, resolver untouched, no refusal: U13, L4.2 ☐
- [ ] AC14 trace, console, audit and metrics agree per request (`unavailable` list, `semantic_mode`, counter +1): U7, U10, U11, L4.8 ☐
- [ ] AC15 `series_count()` unchanged across every reason: U10 ☐
- [ ] AC16 language posture: non-Latin → UNAVAILABLE; `ru` removed: U14 ☐; Latin per-language recall published ⏳ P6

**L36-2 exit**
- [ ] AC17 recall and FPR published per dataset and split with 95 % CIs, from the merged code with the pinned manifest: M4 ⏳ P1, P2
- [ ] AC18 per-window thread-CPU p50/p99 and wall p50/p99, real bytes, this hardware class: M5 ⏳ P7 (proxy ✅ §4.7)
- [ ] AC19 stage latency p50/p99 in the live gateway at a fixed load, with what else ran: L4.9 ☐
- [ ] AC20 capacity table re-computed from measured `c(n)` on the purchase SKU (T-CPU1): ☐

**v2 must beat v1 (owner, 12 Nov)**
- [ ] AC21 **V1-INJ-125**: v2 (deterministic signal plus semantic stage, default org rows, enforce) detects strictly more of the 125 cases than v1's 0/125. Every miss is listed with its family and the reason. The headline target is Q15: M7 ⏳ P12
- [ ] AC22 **V1-PII-21**: v2's result on the 21 published with every L36-2 run. It must reach 21/21 before v2 replaces v1 (F3 gate, Q15). F2 does not own the fix: PG2 does not detect PII: M7 ⏳ P13
- [ ] AC23 **No regression against v1**: every golden INPUT case v1 passes today (the 515 passing tests, input-side subset), v2 also passes, including the benign "not blocked" cases: M7 ☐

**Program gates touched (#98)**
- [ ] T-G2 recall table vs the outgoing engine, published with no parity claim (= AC17) ⏳
- [ ] T-G3 dilution (= P5) ⏳ · T-G4 canonicalisation (= P10) ⏳ · T-G6 content-array split (= U2) ☐
- [ ] T-G7 RSS × workers fits (§5.14) ☐ on the target SKU
- [ ] T-H5 / T-H9 a `fail_closed` org refuses when the guard is down (= AC12) ☐

### 8.1 Unit tests (gateway_v2, no model needed; run in the normal suite)

| ID | Test | Pass condition |
|---|---|---|
| U1 | Token windowing property test (hypothesis): 0–40,000 tokens, overlap 0–255 | every row ≤ 512 positions; rows start `[CLS]` and end `[SEP]`; the union of payloads = all tokens in order; any span ≤ overlap lies inside one row; `W = 1 + ⌈(n−510)/(510−overlap)⌉`; over `max_windows` raises before inference |
| U2 | Role and part assembly | parts of one message joined with "" and messages with "\n"; SYSTEM never admitted; TOOL only when opted in; char spans map back across deltas |
| U3 | Manifest tamper matrix (7 cases) with a tiny fake bundle | each refused with the named reason; the happy path builds one session from the hashed bytes and leaves `_model_bytes is None` |
| U4 | Readiness | ready after the first probe with no request (B7); idle 10× staleness stays ready (B3); a hung probe → not ready within one bound; `/readyz` gated only until the first success |
| U5 | One classify per request | PI row, threat row, JB row and a customer rule → exactly one classify and at most one semantic finding |
| U6 | Threshold-aware join | a 0.85 finding selects a 0.80 row and not a 0.90 row; deterministic findings (confidence 1.0) join as before; a semantic finding governed by a REDACT rule yields no transformation and falls to BLOCK |
| U7 | Posture matrix | each of the 8 reasons × {fail_closed, degrade} gives the code in §5.8, one counter increment, and `unavailable` in the console; never `input_blocked` |
| U8 | Cost admission and deadline | predicted cost > `B` → over_budget with no `run`; a blocking fake backend → timeout within budget + 5 ms with terminate called |
| U9 | In-flight bound and per-org share | org A saturating `Q` cannot starve org B; alone, A uses all of `Q` |
| U10 | Series count | `/metrics` equals `series_count()` before and after every reason fires |
| U11 | Console schema and adapter | `unavailable` and `semantic_mode` accepted and passed through; old records still validate |
| U12 | Loop lag | a fake 200 ms CPU-bound `run` on the guard thread leaves the loop-lag p99 within the GW28 bound |
| U13 | Shadow | with `shadow`, findings are in the audit row but the Decision equals the no-stage Decision |
| U14 | Language | Cyrillic, CJK, Arabic and Devanagari → unsupported_language; Latin → classified |
| U15 | Skip | a plan with no governing row → the stage is not called (zero inference) |

### 8.2 Model tests (CI job `guard-model`; needs the bundle; nightly and on any change under `detect/guard/` or the pin)

| ID | Test | Pass condition |
|---|---|---|
| M1 | Parity (`parity.py` + `parity_full_windows.py`) | max \|Δlogit\| < 1e-4; decision agreement 100 % at 0.5…0.99 on 811 + 60 rows |
| M2 | Golden token ids | the 752 texts tokenize to the committed ids via `tokenizers` |
| M3 | Golden decisions | the 32 manifest rows within 1e-4 and the same decision; whitespace and NFC rows flag |
| M4 | L36-2 re-run | `eval_datasets.py` reproduces the published table within ±1 row per cell (deterministic) |
| M5 | Latency | `bench_window.py` thread-CPU per row within 1.5× of the published table on the runner class (recorded, not gating) |
| M6 | INT8 vs fp32 | ≥ 99 % decision agreement on eval rows and V1-INJ-125; recall and FPR inside fp32's 95 % CI on every headline set; no V1-INJ-125 case lost (bound: Q5). Pass means the pin PR points at INT8 |
| M7 | v1 golden benchmark (`v1_golden_bench.py`) | AC21–AC23. Payloads come from `gateway/tests/golden` by pytest collection (read-only; v1 is not changed), go through the v2 input scan end to end, and are compared per id with v1's outcome at `943a5f21` |

The M-tests run on GitHub-hosted runners in the `guard-model` workflow (§9.4). The U-tests run in
the normal `structural-gates` CI.

### 8.3 Live compose test (L4.1–L4.9; the GW36 L36-2 live half)

Setup: `gateway-v2` with `AMF_PROFILE=production` (after S5), the bundle mounted read-only, the
provider recorder `staging/t02/recorder.py`, and two orgs: A with `fail_closed`, B with `degrade`.
1. **Boot.**
   - The boot log names backend, digest, dtype, `t`, `B`, coverage and `guard_routing -> N/A`.
   - `/readyz` turns ready only after the first probe.
   - `amf_guard_ready 1`.
2. **Shadow mode.**
   - The SDK injection prompt returns 200.
   - The audit row has a `semantic` EXECUTED finding with `semantic_mode: shadow`.
   - The Decision has no semantic-driven disposition.
3. **Enforce mode for org A.**
   - The injection is refused `input_blocked` by `fw-prompt_injection-input`.
   - A benign prompt is allowed.
   - The recorder shows zero provider calls for the refused request.
4. **Over length and over budget.**
   - A 40,000-token prompt: org A gets 413 `prompt_too_long_for_guard`; org B gets 200.
   - Both: counter `{reason="over_length"}` +1 and console `unavailable: ["semantic"]`.
   - A 1,000-token prompt: org A is scanned before forwarding (within 2,000 ms) and gets 200 with
     an EXECUTED finding. Org B gets 200 with UNAVAILABLE(over_budget) recorded.
   - With the guard thread saturated, org A gets 503 `guard_unavailable` with `Retry-After`.
5. **Tampered bundle.** A one-bit-flipped bundle file makes the process refuse to start with
   `guard manifest mismatch: sha256:model.onnx`.
6. **Fairness.** 50 concurrent prompts from org A plus 1 from org B: B's request is EXECUTED,
   not overloaded.
7. **Guard fault.**
   - A worker whose guard thread is stopped (fault hook) shows `amf_guard_ready 0` within the
     staleness bound.
   - Org A gets 503 `guard_unavailable`; org B is forwarded.
   - `/readyz` is unchanged.
8. **Agreement.**
   - The `/metrics` series count is unchanged across steps 2–7.
   - Trace, console, audit row and counters agree per request, via the per-run nonce of L36-1.
9. **Stage latency.**
   - Measured under a fixed RPS with the prompt-length mix of §9.3.
   - Report `amf_guard_stage_seconds` p50/p99 and loop lag, plus what else ran.

## 9. L36-2 measurement protocol

### 9.1 Datasets, licences and use

All are pinned by revision and file sha256 in `results/datasets.json`. Licences are as declared in
each dataset's metadata at that revision; legal confirmation is part of Q3.

| Dataset | Licence | Labels used | Role |
|---|---|---|---|
| In-repo `tests/detection_corpus` | ours | 323 malicious (269 train / 54 eval), 425 benign (336 / 89), by family | calibration = train; **headline = eval split** (the only set guaranteed unseen by Meta) |
| `deepset/prompt-injections` | Apache-2.0 | label 1/0 | calibration = train; headline = test |
| `Lakera/gandalf_ignore_instructions` | MIT | all malicious | headline = validation + test (recall) |
| `jackhhao/jailbreak-classification` | Apache-2.0 | jailbreak / benign | seeded sample of 150 (recall; FPR on its benign rows) |
| `TrustAIRLab/in-the-wild-jailbreak-prompts` | MIT | jailbreak / regular | seeded 150 jailbreak, 400 regular |
| `databricks/databricks-dolly-15k` | CC BY-SA 3.0 | benign | FPR, including long-context rows (window-count FPR); measurement only, never redistributed |
| `OpenAssistant/oasst1` (validation) | Apache-2.0 | benign prompter turns | FPR |
| `yanismiraoui/prompt_injections` | Apache-2.0 | multilingual malicious | per-language recall (P6) |
| `microsoft/llmail-inject-challenge` `emails_for_fp_tests` | MIT | benign emails | FPR on email-like (indirect) context |
| **V1-INJ-125** (v1 golden suite) | ours | malicious | **v2-must-beat-v1 recall set.** The 125 input injection/jailbreak cases among the 153 `golden-freeze` failures that encode the Tier-1 floor (F4 register R16b). Defined by id as: the 153 ids in `docs/plans/evidence/2026-11-11-f4-defects/v1_golden_failures.txt` (#115), minus V1-PII-21, minus the 5 output cases (`test_g10_*`, `test_g35_*`, `test_g88_*`), minus the 2 stale fixtures (`test_plain_pii_secret_is_redacted[plain_email…]`, `test_g101_parenthesized_digit_pii_detected_and_masked`). The largest families are g96 styled Unicode (13), g29 (8) and g28 (7) semantic jailbreak, g108 compound obfuscation (8), g32 encoded (6), g99 forged turn (5) and g95 Greek homoglyph (5). v1 detects 0/125 at `943a5f21` (with Tier-2 off, as the suite runs) |
| **V1-PII-21** (v1 golden suite) | ours | malicious (PII/secret) | **v2-must-beat-v1 PII set** (F4 R16a): `test_g33_encoded_pii_secret_blocks` (5), `test_g53_input_obfuscated_pii_blocked` (4), `test_g69_cross_turn_split_value_detected` (4), `test_g71_cross_role_split_value_detected` (3), `test_g87_input_cf_encoded_pii_is_blocked` (4), `test_g91_input_markdown_entity_blocked` (1). v1 detects 0/21. These test v2's deterministic PII path and canonicalisation, not PG2 |
| v1 golden input cases v1 passes | ours | malicious and benign | no-regression set (AC23). The benign "not blocked" cases (`test_benign_input_not_blocked`, `test_g29_benign_not_blocked`, `test_g96_benign_styled_prose_not_blocked`, …) are its FPR side |

### 9.2 Method

- **Path:** every item goes through the production code path: canonicalisation, role assembly
  (USER), tokenizer, windows, the aggregation rule, and the manifest threshold. It is not a
  bypass script. After the implementation, M4 runs `eval_datasets.py` against `edge/semantic_stage`.
- **Calibration** uses calibration splits only:
  - per-window-count thresholds (P2) at a 1 % per-request FPR target;
  - window size and overlap (P4) and the aggregation rule (P5), chosen by recall at that FPR;
  - headline numbers come only from the held-out splits;
  - seeds are fixed (`20261111`).
- **Metrics:**
  - per set: n, TP/FN/FP/TN, recall and FPR with Wilson 95 % intervals at the chosen threshold
    and the grid, plus AUC;
  - FPR by window count (1, 2–3, 4–7, 8–16);
  - per language;
  - per in-repo family, with in-scope and out-of-scope reported separately and out-of-scope
    excluded from the headline;
  - over-length rows counted and excluded.
- **Planted and dilution tests:**
  - 60 benign two-window documents with a short held-out injection planted at random offsets,
    across overlap 0/64/128 and window sizes;
  - the k-token dilution test across aggregation rules.
- **INT8:** the same rows through the INT8 file. Report the flip count with a McNemar test, and
  recall and FPR deltas with CIs. This is gate M6.
- **v1 golden sets (M7):**
  - Payloads are taken from `gateway/tests/golden` by pytest collection. That is read-only:
    nothing in `gateway/` changes.
  - Multi-turn cases (g69, g71, g99) keep their message structure.
  - Each case goes through the v2 input scan end to end: deterministic detectors, canonicalisation
    and the semantic stage, for a benchmark org with control's default rows, in `enforce`.
  - Reported per id:
    - v1's outcome at `943a5f21`;
    - v2's disposition;
    - which v2 component caught it (`deterministic.*` or `semantic`), with the semantic score;
    - fp32 and INT8 side by side.
  - V1-INJ-125 recall is the headline v2-vs-v1 number of L36-2.
  - V1-PII-21 is reported every run and gates F3, not F2.
- **Honesty notes, published with the table:**
  - public sets may overlap Meta's training data, so public-set recall can be optimistic;
  - PG2 is not built for SQL, command or path injection;
  - the MASTER D3 gap list (indirect injection, multi-turn, over-defence) ships in product docs.

### 9.3 Per-window latency

- **Grid:** rows of 16, 64, 128, 256 and 512 positions × {fp32, INT8} × intra-op {1, 2, 4}.
- **Runs:** warm-up 5, then ≥ 100 runs per point (≥ 30 at 512).
- **Recorded per call:**
  - thread CPU via `time.thread_time_ns` (valid at intra-op 1; process CPU at intra-op > 1, labelled);
  - wall via `perf_counter_ns`.
- **Reported:** p50 and p99 of both.
- **Context recorded with every run:** load average at start and end, a process snapshot (what
  else ran), the CPU model and flags, onnxruntime version and session options.
- **Venues (owner, 12 Nov: GitHub compute, not this host):**
  - (a) a **standard GitHub-hosted runner** (`ubuntu-24.04`) as the baseline: shared tenancy,
    small;
  - (b) a **GitHub larger runner**, Linux x64 16-core, as the L36-2 latency venue: its vCPUs are
    not shared with other agents, and the saturated processes × threads grid of §4.4 can run there;
  - (c) the same on a **Linux arm64 larger runner**, as the closest GitHub analogue of #93's ARM
    target (`c4a-standard-16`);
  - (d) the purchase SKU itself (T-CPU1), before money is spent.

  GitHub does not pin the CPU model of a hosted runner, so every run records `lscpu`, and results
  are compared only within one CPU model. The numbers already in this PR come from this shared
  host and remain historical evidence.
- **The proxy must agree with the real bytes within ±10 % (P7).** If it does, toolchain bumps can
  be latency-checked without the gated weights. If not, the proxy is dropped.
- **Stage latency in the gateway (L4.9):**
  - fixed RPS;
  - a prompt-length mix taken from production audit token counts (a histogram only, no text), or
    from `injection_lengths.json` plus dolly if none is available;
  - report p50/p99 of `amf_guard_stage_seconds` and loop lag.

### 9.4 The `guard-model` workflow on GitHub runners

Proposed here; it is built in the implementation PR, which also needs MERGE_POLICY rule 6 sign-off
for `.github/`.

| Job | Runner | Secrets | Trigger | What it runs | Duration [E] |
|---|---|---|---|---|---|
| `proxy-latency` (matrix of 3) | `ubuntu-24.04` standard; larger runners x64 16-core and arm64 16-core (labels created by an org admin, Q16) | none | PRs touching `detect/guard/**`, `detect/semantic/**`, `edge/semantic_stage.py`, the pin or `tools/guard/**`; nightly | `proxy_latency.py` plus the intra-op 1/2/4 grid → `proxy_latency.json` | ≈ 5 min per venue |
| unit | standard (inside `structural-gates`) | none | every PR | U1–U15 | in the existing suite |
| `model-gates` | larger x64 | `HF_TOKEN` from the `guard-model` environment (required reviewer, `develop` only) | dispatch; push to `develop` on the guard paths; nightly (M1–M3 only) | fetch and verify, fp32 export, M1–M3, INT8 derive, both manifests | ≈ 10 min |
| `l36-2` | larger x64 (latency grid repeated on arm64) | same environment | dispatch; every pin PR | P1–P13: eval, M6, M7, the P7 latency grid and the saturated processes × threads grid of §4.4 | ≈ 30–40 min |
| `publish` | standard | `id-token: write`, `packages: write` (no HF token) | dispatch on `develop` after `model-gates` | cosign keyless signature, SLSA provenance, push of the fp32 and INT8 bundles to a **private** GHCR package | ≈ 5 min |

- **The repo is public today, so logs and artifacts are readable.**
  - Artifacts carry results only: JSON and CSV with ids, scores, hashes and timings.
  - Weights, ONNX files and dataset text are never uploaded as artifacts. Doing so would
    distribute Llama Materials and third-party text.
  - Bundles go only to the private package.
- **Secrets:**
  - `HF_TOKEN` exists only in the `guard-model` environment;
  - jobs from forks never receive it;
  - no step prints the environment;
  - actions are pinned to full SHAs (#114), and each job's `permissions:` is least-privilege.
- **Spend:** larger runners are billed per minute, even on a public repo, and are not covered by
  the included minutes. Estimate: proxy on ≈ 10 PRs a week across 2 larger venues, nightly
  `model-gates`, and ≈ 4 `l36-2` runs a month, which comes to **≈ 900 larger-runner
  minutes/month** [E]. The rate and the cap are the owner's (Q16).
- **The proxy job needs no secret.** It can therefore run on every relevant PR, and it re-checks
  latency on toolchain bumps without the gated weights, once P7 has validated it against the real
  bytes.

## 10. Rollout and rollback

| Stage | What changes | Gate to the next stage | Rollback |
|---|---|---|---|
| R0 | Code merged with `AMF_SEMANTIC_STAGE=off` (default). No bundle in the image; boot log `semantic -> off`. Behaviour identical to today | U1–U15 green in CI | revert the PR |
| R1 | The `guard-model` workflow publishes both bundles. The pin PR lands pointing at INT8 if M6 passed, fp32 otherwise. Dev compose mounts it with `AMF_SEMANTIC_STAGE=on`, `shadow` | M1–M7 green on GitHub runners; L4.1–L4.8 green; L36-2 table published, including V1-INJ-125 and V1-PII-21 (AC17, AC18, AC21–AC23) | unset `AMF_SEMANTIC_STAGE` |
| R2 | v2 production (after F3) runs the stage in `shadow` | Measured on real traffic for ≥ 7 days:<br>• U2 rate (overloaded + timeout) < 0.1 % of requests at the provisioned load;<br>• stage p99 within `B`;<br>• no `/readyz` or loop-lag regression;<br>• shadow hit rate reviewed by the owner (expected blocks per day) | `AMF_SEMANTIC_STAGE=off` |
| R3 | `enforce` for the orgs in `V2_SEMANTIC_ENFORCE_ORGS`, then for all | owner answers Q1 and Q7; R2 gates hold for the canaries for 7 days | remove the org from the list, or set `AMF_SEMANTIC_ENFORCEMENT=shadow` (no restart of state; one env change and a rolling restart) |
| R4 | Precision switch after R1, in either direction (for example INT8 once a later M6 passes) | M6 on the new pair; Q5 bound | re-pin the other manifest (one PR) |

- **Model upgrades:** a new bundle plus a new pin in one PR, re-running M1–M6 and L36-2. The
  previous bundle stays addressable by digest, so rollback is reverting that PR.
- **A manifest mismatch never silently becomes "no guard":**
  - the production profile refuses to start;
  - the dev profile reports UNAVAILABLE(manifest), which the org posture handles.

## 11. What this plan does not do

- **No product code.** The stage, loader, probe, posture codes, metrics and console fields are
  designed here and prototyped in `scripts/`, not implemented in `gateway_v2/`.
- **No L36-2 numbers.** P1–P13 are pending the weights, and nothing claims a pass.
- **No measured INT8 accuracy.** The draft's and GW36's "int8 flips 2–6 %" is a ledger claim whose
  per-row source is not in the repo.
- **No GPU or TensorRT path** (#93), no remote guard pool, no sidecar (the fallback is specified but
  not built).
- **No verdict cache** (deferred to F5 with measured hit rates) and no "last turn only" scanning
  (Q11).
- **No change to v1**, the live production data plane (owner, 12 Nov: untouched until the v2
  revamp is complete). v1 is read only, to collect the golden payloads for M7.
- **No change to control's default rows.** That waits for Q6 and Q10.
- **Not measured or designed beyond the role hook:** output-side semantic scanning; RAG, MCP and
  embeddings surfaces; indirect injection through tool output.
- **No live compose run.** The production profile does not wire detectors until S5, and the bundle
  has no published location until Q13 is answered.
- **No workflow file.** The `guard-model` workflow (§9.4) is specified, not added. This PR changes
  nothing under `.github/`.

## 12. Owner questions (one batch; each has a recommended default)

| # | Question | Why it matters | Recommended default |
|---|---|---|---|
| Q1 | Sign, or decline to sign, a recall/FPR floor for the semantic stage (C6/C16)? | Without one, L36-2 only publishes; enforcement rests on the org settings alone | Decline for now. Publish L36-2. Move to `enforce` per org after R2, with the measured numbers in the console docs |
| Q2 | Which company account accepts the Llama 4 licence on Hugging Face? Where is "Built with Llama" shown? Is the gateway image distributed outside the company (customer on-prem, public registry)? | Distribution triggers `LICENSE`, `NOTICE` and attribution duties (§6.2) | Private registry only; `LICENSE` and `NOTICE` in the bundle; attribution in product docs and the console About page |
| Q3 | Legal review of the Acceptable Use Policy for defence, government and critical-infrastructure customers; MIT notice for DeBERTa; dataset licences for measurement | The AUP flows to customers | Review before any such deployment; ship the MIT notice anyway |
| Q4 | Confirm that the CPU `local_onnx` stage is the PRODUCTION path, superseding GW36 §9.3a's "dev/CI only" | #93 forbids GPU; §9.3a predates nothing that changes this | Yes |
| Q5 | Confirm the M6 bound under which INT8 (#98) replaces fp32 for serving | INT8 saves 20–48 % of guard vCPU and doubles inline coverage [M-proxy, M-sep], at a flip rate not yet measured | INT8 serves if ≥ 99 % decision agreement with fp32 on eval rows and V1-INJ-125, recall and FPR inside fp32's 95 % CI on every headline set, and no V1-INJ-125 case lost. Otherwise fp32 serves. Both are always built |
| Q6 | Should v2 keep a default injection floor? v1 has none (the removal attributed to "task 3.1", B26). v2 publishes `fw-prompt_injection-input` BLOCK by default | Decides whether control's row stays default-on, which decides the guard's share of capacity | **Keep the floor in v2.** T-H9 requires injection detection in degraded mode, and the owner now makes V1-INJ-125 a must-beat set. F2 works either way (§5.10). v1 unchanged |
| Q7 | Should the existing `tier2_fail_closed_enabled` (model default True; v1's fail-closed Tier-2 was gated by an env flag defaulting to false) make every org `fail_closed` for PG2? | `fail_closed` puts full-length CPU scans, up to 2 s, on the request path and turns guard faults into 503s | **No: make it an explicit opt-in.** Control derives the semantic-governing rows' `on_unavailable` from a new field (default off; existing orgs migrate to off unless they are in the strict tier where v1's fail-closed Tier-2 was live). T-H5 is met by the proven opt-in path (AC12). Enforce only from R3 |
| Q8 | Stage budgets: 12 ms for `degrade` orgs and 2,000 ms for `fail_closed` orgs? Or a "deferred" mode for `degrade` orgs that classifies over-budget prompts concurrently with the provider call (no added latency; recorded only; never blocks)? | On CPU the budget decides which lengths are scanned inline (§5.6). Long jailbreak prompts (p50 372 tokens) are otherwise never classified for `degrade` orgs | 12 ms / 2,000 ms. Deferred mode is not built in F2; reconsider after R2 shadow data shows how much traffic is over budget |
| Q9 | Admit TOOL-role messages (indirect injection) by default? | Coverage vs FPR on tool output (P11) | USER only; TOOL opt-in per org |
| Q10 | Which surfaces? Control's rows say `["chat"]` only | Completions and Responses are unscanned by these rows until control widens them | chat, completions and responses, via a control change with F4 R1 (#115) |
| Q11 | Scan the whole conversation every turn, or only content after the last assistant turn? | History is client-supplied, so skipping it is a bypass; scanning it multiplies cost | Whole conversation. A shared cache is evaluated in F5 |
| Q12 | Languages: classify Latin-script non-English (pending P6) and refuse or degrade the rest (including Russian)? | B12 shows a silent Russian miss | Yes, as in §5.4 |
| Q13 | Bundle hosting? | Needed for R1. The repo is public, so artifacts cannot carry the bundle | GHCR private package, cosign-signed (§5.3) |
| Q14 | Should `/readyz` ever be gated on guard readiness after boot? | A bad bundle would drain the fleet | No: boot only. Alerts on `amf_guard_ready` |
| Q15 | Targets for the v2-must-beat-v1 sets | AC21 says only "strictly more than v1's 0"; a real bar needs a number | Set the V1-INJ-125 target after the first M7 run, with per-family misses in hand. Proposal: ≥ 90 % before R3 enforce. V1-PII-21: 21/21 before v2 replaces v1 (F3), owned by F4/v2 PII, not F2 |
| Q16 | Create the larger runners (Linux x64 16-core and arm64 16-core) and a runner group for this repo; approve ≈ 900 larger-runner minutes/month; create the `guard-model` environment with a required reviewer and the `HF_TOKEN` secret | Larger runners are billed per minute even on a public repo, and only an org admin can create them | Yes, with a spending limit and a monthly usage review |

---

Evidence scripts: `docs/plans/evidence/2026-11-11-f2-guard-model/scripts/` (README there). The
weight-free proxy added in this revision is `proxy_latency.py`; its output is
`results/proxy_latency.json`.
