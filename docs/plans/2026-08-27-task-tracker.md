# Task tracker — evidence-based hot-path plan

> Companion to [`2026-08-27-FINAL-evidence-based-hot-path-plan.md`](./2026-08-27-FINAL-evidence-based-hot-path-plan.md).
> **Date:** 2026-08-27 · **Status:** not started — no task below is authorised until the plan is confirmed.

**Legend.** **P** = gate to start the next phase · **S** = security gate (blocks even if latency is green) · **$** = cost gate · **B** = blocked by another task.

**The rule.** No task is complete until its named test has been run and its evidence published. A green unit file alone is not evidence. `@skill-verification-before-completion` `@skill-test-driven-development`

**Ordering law.** G0 and G1 block everything. G2 is independent and can run in parallel (it is live-defect work, not performance work). G4 can run in parallel with G0/G1 because it is verdict-neutral. Nothing in Phase 2+ starts until G0, G1 and G5 have reported.

---

## Gate 0 — Measure what we are protecting

*Blocks every posture decision and every published SLO. Estimated: 1–2 days.*

| ID | Task | Why it exists | Files / artefacts | Test | Success | Exit criteria | Rollback | Blast radius |
|---|---|---|---|---|---|---|---|---|
| **G0.1** | Build a labelled detection corpus: ≥300 injections across ≥8 families + ≥300 benign, including a **paraphrase family with disjoint trigger vocabulary** and a **developer-traffic family** (inline code, tool descriptions, SQL, shell) | Every posture decision in this project has been made without knowing what any posture detects | new `tests/detection_corpus/` (JSONL, labelled, provenance per item) | corpus lints: no duplicates, no leakage between train/eval, label audit on a 10% sample | Corpus committed with a written sampling methodology | Two reviewers independently agree the paraphrase family is genuinely disjoint | n/a — additive | none (test-only) |
| **G0.2** | Publish recall/FPR per posture: Tier-1 only · Tier-1+policy · Tier-1+semantic | The plan was about to certify an SLO for a posture with unmeasured detection | `scripts/detection/score_postures.py`, report in `docs/perf/` | Run all three postures over G0.1 | A table exists with recall @1% FPR and per-family breakdown | **No posture may be recommended and no SLO published until this table exists** | n/a | none |
| **G0.3** | Fix `` r"`[^`]+`" `` in `ATTACK_PATTERNS["command_injection"]` | 5/5 benign inline-code prompts hard-blocked at confidence 1.00, terminal, never reaching Tier-2 `[M]` | `gateway/ai_mesh_gateway/scanner.py` | New FP suite from G0.1's developer-traffic family + full existing suite | Developer-traffic FP rate falls; **no** regression in the injection families | Scored and signed off as a **deliberate** behaviour change | revert single pattern | **Changes verdicts.** Tracked separately from G0.4 |
| **G0.4** | Fix the `data_leakage` over-match on tool descriptions | 3/12 ordinary agentic tool descriptions hard-blocked `[M]` | `scanner.py` | as G0.3 | Tool-description FP rate falls | Scored and signed off separately | revert | **Changes verdicts.** Must NOT be bundled with G0.3 — a verdict-equivalence gate would score both as regressions |
| **G0.5** | Decide the explanatory carve-out: should `command_injection`/`data_leakage` remain excluded (`scanner.py:104`, tested `:1201`)? | The exclusion is what makes these blocks terminal at `:1212-1219` | `scanner.py` | carve-out behaviour suite | Written decision in the changelog | Product sign-off | revert | verdicts |

**P:** G0.2 published. **S:** G0.3 + G0.4 scored, not silent.

---

## Gate 1 — Fix the instrument

*Blocks all measurement. Every streaming number produced before this is invalid.*

| ID | Task | Why it exists | Files | Test | Success | Exit criteria | Rollback | Blast radius |
|---|---|---|---|---|---|---|---|---|
| **G1.1** | Re-base stream metrics on the **request** epoch, not `provider_start_ts` | `ttft_ms` and `duration_ms` both derive from `provider_start_ts` (`:120-131`), `total_latency_ms = duration` (`:738,:778`), so the bench's `addon = total − model_output` **equals TTFT**. On streams we measure the customer's model, not ourselves | `pipeline_trace.py` | new `test_stream_addon_is_not_ttft.py` — assert addon ≠ TTFT on a stub with a deliberately slow first token | Addon tracks firewall work, not provider latency | A stub with 2 s TTFT and 0 firewall work reports addon ≈ 0 | revert | **All historical streaming numbers become invalid — say so publicly** |
| **G1.2** | Replace every reconciliation clamp with a **signed** residual + error histogram | Clamps hide the disagreement that would have surfaced G1.1 | `pipeline_trace.py:95,708,1512`, `stream_orchestration.py:773` | CI check | Reconciliation-error histogram exists | **CI fails at non-zero p50 residual** | revert | observability only |
| **G1.3** | Add `STREAM=1` + SSE parsing to the bench, with the assertion `\|wall − addon − model_output\| < ε` | The bench has never run a streaming path | `scripts/perf/gateway_pipeline_bench.py` | self-test | Assertion holds on a known-good stub | Streaming and non-streaming both benchable | revert | bench only |
| **G1.4** | Microsecond resolution + explicit `ran: bool`; **strike** "0 ms = skipped" | A 0 ms stage and a skipped stage are not the same event | `pipeline_trace.py` | trace-shape test | Skipped stages are distinguishable from fast ones | UI renders `ran=false` distinctly | revert | UI contract |
| **G1.5** | Split `capacity_eligible` from `tax_eligible` | A request can count for capacity but not for tax, and vice versa | `pipeline_trace.py`, bench | bench self-test | Two independent counters | Documented in the bench README | revert | bench semantics |
| **G1.6** | Emit the addon split from the **stream finaliser**; add per-stage `t_start_ms`/`t_end_ms` | **Without per-stage start/end, L2's overlapped-classification claim is unverifiable in principle** | `stream_orchestration.py`, `pipeline_trace.py` | overlap-verification test | Overlap is measurable | An overlapped design can be proven to overlap | revert | trace size — pair with G4.4 |

**P:** G1.1 + G1.3 + G1.6. **Nothing else may be measured until this gate is green.**

---

## Gate 2 — Live defects (independent; run in parallel)

*Not performance work. These are shipping today.*

| ID | Task | Why it exists | Files | Test | Success | Exit criteria | Rollback | Blast radius |
|---|---|---|---|---|---|---|---|---|
| **G2.1 (S)** | `PolicySync`: make `is_loaded` **per-org**; never set `_sync_completed` on zero accepted bundles; add periodic reconcile | A partial cold start leaves a subset of tenants **unenforced forever** with `/health` green. 16 workers × N nodes = independent SCANs, each able to stop at a different point — the same tenant is enforced on some workers and not others, with no way to tell which served a request | `policy_sync` module | new `test_policysync_partial_coldstart.py` — kill SCAN midway, assert no tenant is silently unenforced | Partial load never reports ready | Chaos test: interrupt SCAN 20× → zero unenforced tenants | revert | **Fail-open → fail-closed. May surface previously-unenforced tenants as newly blocked — communicate before deploy** |
| **G2.2 (S)** | `ConfigSync`: switch `main.py:6929` **and its five siblings** to `get_own_config` + explicit safe defaults | `build_redis_key()` returns `firewall:config:default` when org-less (`models.py:1368-1370`), so a platform-level `FirewallConfig` row publishes a key **every un-synced tenant inherits**. The fix already exists and was applied to the MCP path but not to chat | `main.py`, `core/models.py` | new `test_config_no_default_inheritance.py` — un-synced org must get safe defaults, never the platform row | Un-synced tenant gets defaults, not the platform posture | Cross-tenant canary never visible | revert | **Tenant posture changes. Direction undetermined → treat as fail-open today** |
| **G2.3 (S)** | Add `limit_concurrency` + 503 shedding **before** any `timeoutSec` increase; migrate off the deprecated worker class in the same change | `--worker-connections 20000` is **silently inert** (`uvicorn.workers.CONFIG_KWARGS` verified `[M]`). Rate limits bound rate, not concurrency. Raising `timeoutSec` first converts a routine upstream stall into a metastable failure | gunicorn config, worker class | load test to 3× the cap | Excess load sheds with 503, not OOM | Cap is demonstrably effective (not inert) | revert | **Ordering is mandatory: never raise timeouts before this lands** |
| **G2.4 (S)** | Make the output-path `scan_degraded` floor live | Dead code today → on breaker-OPEN, streams are delivered Tier-1-only, fail-open, **while telemetry attests they were scanned**. A false attestation in a compliance record | `output_guard.py`, `secure_streaming.py` | breaker-OPEN stream test | Degraded streams are flagged and enforced | Telemetry never attests a scan that did not happen | revert | **Compliance-record correctness** |
| **G2.5 (S)** | Resolve streaming "block": either withhold fully or document truncation semantics | Measured: 260 of 800 characters delivered, then an error frame. An operator who selected "block" did not get one | `secure_streaming.py` | block-mode stream test asserting zero content bytes released | Block releases nothing, or docs state truncation explicitly | Product sign-off on which | revert | **Operator-visible semantics** |
| **G2.6 (S)** | Separate input/output breaker keys; scope per tenant | One shared key counted globally across tenants | breaker module | cross-tenant breaker test | One tenant cannot open another's breaker | verified per-tenant | revert | isolation |
| **G2.7 (S)** | Telemetry: add retry/DLQ/spill; stop dropping the batch on Redis error | Evidence is discarded **precisely during the failures the plan expects** | telemetry path | failure-injection test | `audit_completeness_ratio` ≥ 0.999 | Loss is counted, never assumed zero | revert | audit integrity |
| **G2.8** | Periodic re-load for `ConfigSync`/`PolicySync`/`VectorPolicySync`; delete the false "will retry" comments | Staleness after a pub/sub disconnect is unbounded, while L8 signs 50 ms for the smallest key family | sync modules | disconnect test | Bounded staleness, published | `config_snapshot_age_seconds` exported | revert | propagation timing |

---

## Gate 5 — Price the rejected option

| ID | Task | Why it exists | Test | Success | Exit criteria |
|---|---|---|---|---|---|
| **G5.1 ($)** | Obtain GCP `asia-south1` accelerator rates (`g2-standard-4`, `nvidia-l4`) — on-demand, 1-yr/3-yr CUD, Spot | **GPU was rejected on an AWS price in a GCP-locked plan.** `[NF]` no GCP rate appears anywhere in the BOM. This is the only lever that closes the classifier gap outright | vendor price card, dated | A priced table exists | — |
| **G5.2 ($)** | Re-decide the guard-accelerator question on **D1's hop-tax argument**, not on price-from-the-wrong-cloud | The hop tax is the argument that will actually hold | decision doc | Written decision with the hop-tax arithmetic | Signed |
| **G5.3** | If GPU is affordable: measure PG2-86M **and** PG2-22M on L4, batch 1/4/8, seq 256/512/1024 | Everything measured so far is CPU | bench | p50/p99 per config | Feeds the posture decision |
| **G5.4** | Quota check: `nvidia-l4` on-demand quota is **8** in asia-south1; `CPUS` = 500, `IN_USE_ADDRESSES` = 69 | Quota binds before compute does | quota query | Filed or confirmed sufficient | — |

---

## Gate 4 — Zero-new-dependency wins (parallel; verdict-neutral)

*~10× available with no new dependencies. Each is independently revertable.*

| ID | Task | Why | Files | Test | Success | Exit | Blast radius |
|---|---|---|---|---|---|---|---|
| **G4.1** | `lru_cache` on `_deobfuscate_text` | **1,186.5 → 100.2 ms (11.8×), verdict byte-identical** `[M]`; 440 hits / 40 misses on ONE prompt, so the win is *intra*-request — no cross-tenant cache surface | `scanner.py` | verdict-equivalence over the full corpus | 0 verdict diffs; ≥10× on the 3,480-char prompt | Published before/after | **None — proven verdict-identical** |
| **G4.2** | Scrub `pipeline_trace` on the **allow** paths | 41,424 B with **21 prompt copies + 7 response copies** shipped to every client on every allowed request. `_scrub_trace_for_client` (`main.py:723`) is called **only** from the blocked path (`:955`). **24% of the $5k budget today** | `main.py:9091`, `:10979`, stream terminal frame | byte-size assertion + client-contract test | Terminal frame < 4 KiB | UI still renders Scan Detail (rehydrate by reference) | **Client-visible payload shrinks — check UI consumers first** |
| **G4.3** | Coalesce SSE writes (25–50 ms or 4 KB boundaries) | **11.6× egress reduction — the single largest cost lever.** Egress + LB is 94.6% of the bill | `secure_streaming.py` flush trigger | TTFT regression test + egress byte count | ≥8× byte reduction, TTFT p50 unchanged | Measured on a real stream | **Perceived streaming smoothness — needs product sign-off** |
| **G4.4** | Shrink the telemetry event; one content reference, not 25 copies | `redact_all` costs **8.19 ms p50 / 18.05 p99 per pass**, and the request runs **4–20** of them | telemetry builders | size + pass-count assertion | ≤2 `redact_all` passes/request | Event ≪ 52 KB | audit payload shape |
| **G4.5** | Detach the Redis log publisher; restore the `gateway` logger to INFO | `main.py:5999` forces DEBUG; `redis_log_handler.emit` does a **synchronous** `client.publish` with `socket_timeout=1.0` — a slow Redis stalls the event loop up to 1 s **per log line** | `main.py`, `shared/ai_mesh_shared/redis_log_handler.py` | event-loop-lag test | Zero sync publishes on the request path | loop lag p99 improves | log verbosity drops — confirm ops tooling |
| **G4.6** | Fix the flush trigger | `ceil(512/d) ≥ 64 ⟺ d ≤ 8 bytes` — verified by two reviewers | `secure_streaming.py` | flush-count test | Flush count matches the documented rule | — | with G4.3 |
| **G4.7** | Negative-cache the empty catalogue | Per-request Redis GET + router rebuild before returning 422 | `main.py:6930` | 422-path test | No per-request rebuild | — | none |
| **G4.8** | `orjson` for body parse | 1.1 µs vs 4.4 µs on a 2.3 KB body | gateway | parity test | byte-identical output | — | none |

**P:** G4.1 + G4.2 + G4.3 measured **after G1 is green** — before G1 the numbers are meaningless.

---

## Gate 3 — Restate the budget

| ID | Task | Why | Success |
|---|---|---|---|
| **G3.1** | Add the missing budget lines: **ASGI middleware** (1.00 ms p50 / 4.70 p99 `[M]`), **tokenisation** (4.3 ms/512 tok `[M]`), stream machinery, multi-turn double-scan, `tools`/`response_format` channels, snapshot refresh | Six stages had **no budget line at all** | Budget sums to the measured total ±10% |
| **G3.2** | Add a **"worker-count multiplier applied? Y/N"** column to every cache/state line | `compute_sizing(16.0, 64 GiB) → workers=16` `[M]`, no shared memory. Per-worker state lies at scale (the Tier-2 breaker's `MIN_CALLS=5` is effectively 80) | Every line answers the question |
| **G3.3** | Restate L1 as band + posture + percentile; convert Phase-1's absolute threshold to a **delta** test | An absolute threshold cannot fail when the instrument is broken | Gate text rewritten |
| **G3.4** | Re-baseline N with a declared prompt band | N ≈ 85 is a 32–64-token number; at 1024 tokens today's host does ~5 RPS | N published with its band |

---

## Phase 2 — Engine (blocked by G0, G1, G5)

**B: G0.2 must exist first.** The T1 fork cannot be chosen without a detection corpus, because byte-identical preservation of a detector measured at 0/10 paraphrase recall is not obviously the right goal.

| ID | Task | Why | Test | Success | Blast radius |
|---|---|---|---|---|---|
| **P2.1** | Decide the T1 fork: verdict-identical (**63.4 ms** @1024 tok) vs changed detector (floor **0.34–0.54 ms**) | Both measured `[M]`. The decision is a product decision informed by G0.2 | decision doc | Written, with the G0.2 table cited | defines everything downstream |
| **P2.2** | If rewriting: apply the **relaxed-superset** Hyperscan port — drop `\b`/`\B`, widen `\s`/`\d`/`\w` to Unicode classes, compile `UTF8\|UCP` | **A naive ASCII port is a silent Tier-1 bypass**: 162/174 patterns use Unicode classes that are ASCII in Hyperscan → 34 recall violations on 95 probes (`ignore all previous instructions` with a thin space evades). `HS_FLAG_UCP` alone breaks 73/174 (`\b` unsupported) | the 95-probe recall suite | **174/174 compile, 0 recall violations**, cost within 5% (34.5 µs vs 33.3 µs `[M]`) | **A regression here is an undetectable bypass — this suite is mandatory, not optional** |
| **P2.3** | RapidFuzz as a **prefilter**, difflib as the verifier | rapidfuzz ≥ thr is a **provable superset** of difflib ≥ thr: 136 crossings in 371,685 pairs and 176 in 222,166, **all in the safe direction, zero reverse** `[M]` | superset property test | 0 verdict diffs | safe by construction |
| **P2.4** | 9 patterns via `HS_FLAG_PREFILTER` + `re` verify | Documented superset; recall cannot drop | prefilter suite | 0 recall loss | none |
| **P2.5** | Single `EVALSHA` on `{org:<id>}`; bounded pool queueing (never raise) | Full Lua text crosses the wire per call today; pools raise instead of queueing → 503 storms | limiter suite | 1 RTT/request; queue under exhaustion | limiter semantics |
| **P2.6** | Admit snapshot ≤50 ms + `SSUBSCRIBE` (L8) | Removes ~4 RTTs; **verify Memorystore Valkey supports pub/sub** before relying on it | staleness test | ≤50 ms worst case; `admit_snapshot_age_seconds` exported | **Sign the availability consequence: every failover is a full-product 503** |

---

## Phase 3 — Zero Bedrock (D18) — ordering is non-negotiable

**Two laws: T5 before T9. T11 before T16.**

| ID | Task | Why the order matters | Success |
|---|---|---|---|
| **T1–T4** | Land replacements **first**; shadow-collect paired verdicts | Never delete a control before its replacement is proven | ≥100 k paired verdicts over ≥7 days of real traffic |
| **T5 (S)** | Fail-closed equivalence fixture — **HARD GATE for T11** | This is what makes T11 safe | All three pass **with the Bedrock path still present** |
| **T9 (S)** | Group A1: input Tier-2 cutover. **The fail-closed one.** Requires T5 green | Canary org first | Re-point `test_adv50_tier2_truncated_failclosed.py`, `test_pipeline_degraded_failclosed.py`, `test_pipeline_enforcement_authority.py`, `test_mcp_tier2_strict_unavailable.py`, `test_bedrock_tier2_breaker.py` → `test_tier2_breaker.py`, **plus T5's fixture as a merge gate** |
| **T10** | MCP Tier-2 — **no files change**; `mcp_scan_orchestrator.py:1205` calls `scan_prompt_with_tier2` and inherits T9 | free | inherits T9's suite |
| **T11** | Delete the Bedrock engines | Requires T5 green + N days at 100% | engines gone, tests green |
| **T16** | IAM + VPC endpoint removal | **MUST follow T11.** Revoking IAM while code still calls Bedrock converts a fail-open path into a per-request `AccessDeniedException` storm. This is the item that most directly serves L7 (GCP-only): after it the gateway needs no AWS principal at all | `infra/terraform/envs/prod/main.tf:90`, `deploy/ec2-instance-role-policy.json:30-38`, `infra/terraform/modules/network/main.tf:157`. **If the role carries secrets, rotate them here too** |

**Corrections to the earlier inventory — verified against the tree:**

| Earlier claim | Corrected |
|---|---|
| 32 BEDROCK_* env vars | **21** |
| `security_engines` is dead code | **Live at 3 verified call sites — do not delete** |
| boto3 leaves entirely | **boto3 stays** — `boto3.client("s3")` is live at `poc_views.py:69`. Only the Bedrock coupling goes |
| onnxruntime is a new dep | **Already in the image via chromadb** |

**Open miss:** after judge removal the RAG path has **no semantic layer at all** (`_embedding_vault = None`, verified). T7 as drafted is a security regression. Resolve before scheduling.

---

## Phase 4 — Fleet (blocked by everything above)

| ID | Task | Success | Cost gate |
|---|---|---|---|
| **P4.1** | Bake-off on the real SKU — **nothing in this corpus was measured on `c4a`/Axion**; every millisecond is Intel x86 | Published p50/p99 on the target SKU | — |
| **P4.2** | Re-find the thread cliff on 16 real cores (no SMT) | **8 threads was optimal on x86; 16 was 25% slower with 2.4× worse p99.** The cliff will sit elsewhere on Axion | documented |
| **P4.3** | Verify the L7 ALB does not buffer SSE and its backend timeout reaches ≥350 s | The live GCP env has a **60 s** HTTP backend timeout that would sever streams | go/no-go; L4 passthrough fallback documented |
| **P4.4 ($)** | Cost alarm at **$4,000/mo**; autoscale on in-flight | Alarm live | run-rate < $5k |
| **P4.5** | Publish the honest capacity claim | **C-B ≈ 1,000 RPS at $4,866/mo with SSE coalescing ×16** — measured, with prompt band, posture and percentile stated | — |

---

## Cross-cutting: what must never be claimed

Enforce in CI where possible; in review otherwise. Full list in the plan's §7. The four that most need a mechanical gate:

| Guard | Mechanism |
|---|---|
| No streaming latency number from a pre-G1 bench | CI refuses to publish if `reconciliation_error_p50 ≠ 0` |
| No SLO without posture + band + percentile | Lint the SLO string format |
| No target value tagged `[M]` | Provenance-tag lint on plan documents |
| No posture recommended without a G0.2 row | Cross-reference check |

---

## Dependency graph

```
G0 detection corpus ──┐
                      ├──► P2.1 T1 fork ──► P2.2..P2.6 ──► P3 (T1..T16) ──► P4
G1 instrument ────────┤
G5 GPU price ─────────┘

G2 live defects ───────────────────────────► (independent, ship as ready)
G4 zero-dep wins ──► (measure only after G1) ─► folds into P4.5
G3 budget restatement ──► gates the SLO text
```

**The single most important sequencing rule:** G1 before any measurement, G0 before any posture decision. Reversing either produces a confident, published, wrong answer — which is the failure mode this corpus caught twice already.
