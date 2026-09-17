# AI Mesh Firewall - implementation and verification runbook V3

**Prepared:** 2026-09-14. **Evidence baseline:** GitHub `ansh` at `e95f974dc500414b8f4db28038977fa9bf7deb44`.  
**Status:** repository-grounded implementation specification; no production modification or live test performed by creating this pack.  
**Outcome:** zero platform external AI; organization-controlled supported policies; complete firewall overhead below 20 ms at the approved percentile; >=1064 qualified gateway RPS; highest repeatable practical RPS/vCPU measured by profile.

## Read this first

This runbook replaces the executable ordering in V1/V2 where current source contradicts their historical assumptions. Use `REPOSITORY_REVALIDATION.md` and `SOURCE_MANIFEST.json` for evidence. All A00-A19 tasks start **NOT_RUN**. A read test definition is not an executed test. No GitHub write, cloud purchase, secret rotation, infrastructure mutation or production load test is authorized merely by this file.

**First action:** A00 credential triage plus A01 read-only source/runtime identity. A00 blocks new release deployment, not isolated analysis. The first application-code work is A03 correct measurement, in parallel with A04 corpus and A02 contract definition. Do not start by optimizing the disabled historical scanner body or deleting Bedrock imports.

## Evidence hierarchy

1. User's current requirements and approved V3 contract.
2. Current source at an identified immutable commit, subject to actual deployment identity.
3. Measured live release behavior on the approved workload.
4. Historical plans and benchmarks as hypotheses/regression evidence, not current facts.

Historical `main` and current `ansh` have different scanner behavior. Never mix their source line references or test outputs. Source and test snapshots do not override live evidence; a source comment saying 'sub-millisecond' is not a measurement.

## Hard constraints

| ID | Release-blocking constraint |
|---|---|
| H01 | No platform-owned external AI attempts, including fallbacks, workers, health checks, guard-driven rewrites and startup. Customer generation is explicitly separate. |
| H02 | No authentication or tenant-isolation bypass through an org content-policy toggle. |
| H03 | OFF/observation/enforcement are implemented consistently for each supported content rule and surface. No hidden optional content floor. |
| H04 | Empty valid policy differs from unavailable policy. No wrong-org or global-default inheritance. |
| H05 | Input terminal block prevents provider dispatch. Never speculate customer generation before an enforcing decision. |
| H06 | Policy/body producers precede scanners that consume the effective text. No blind stage-level fan-out or competing terminal writers. |
| H07 | Required detector unavailable is not successful Tier-1 clean or a benign score. No blanket redaction substitute for missing semantic evaluation. |
| H08 | Redaction is verified on original fields and actual outbound bytes, not a UI badge; no-op mandatory removal fails safely. |
| H09 | Output block cannot be downgraded across layers. Strict whole-response withholding and late stream truncation are separate modes. |
| H10 | Logs/metrics/audit artifacts do not disclose raw synthetic secret values or private credentials. Content-policy OFF does not disable safe log handling. |
| H11 | Complete latency includes pre-auth timing, input, output, queueing, serialization and declared delivery holds. No clamp hides invalid attribution. |
| H12 | All selected supported detectors truly execute on eligible traffic. Intentional early block/OFF is explicitly recorded, never fabricated as a scan. |
| H13 | Model/export/tokenizer/window versions and actual GPU placement known; no ready-request download/build or silent model-file fallback. |
| H14 | Input/output coverage honors supported token/byte bounds and overlap; no blind middle truncation to win timing. |
| H15 | Queues, threads, GPU owners, buffers and retry loops are bounded; cancellation releases resources. |
| H16 | Immutable release images and real frontend/APIs are tested. No docker cp/pip edits in serving containers. |
| H17 | Success rate excludes infrastructure failures, incomplete streams, skipped selected guards and scan-only responses from full-pipeline claims. |
| H18 | RPS/vCPU includes all declared serving CPU resources; purchased and consumed CPU ratios are distinct. Fixed GPU count across CPU sweeps. |
| H19 | No historical 277 RPS/vCPU or 2.14 ms component number advertised as current measured maximum or total overhead. |
| H20 | Security/quality and capability gates pass before cloud caller removal; credentials/IAM cleanup follows caller removal. |
| H21 | No uncontrolled live tests, secret disclosure, destructive volume cleanup or rollback to an exposed credential. |
| H22 | Release remains BLOCKED if any mandatory capability, quality threshold, latency interpretation or output-mode contract is unresolved. |

## Qualification definitions

**Proposed percentile:** p99 <20 ms, plus p50/p95 reported. This is stronger than an unspecified '<20 ms' and requires approval. Auth through input dispatch and output through delivery are in scope; customer model compute and client WAN are not silently counted as firewall service work. Actual edge tax is measured and named.

**Profiles:** freeze selected input/output rules, rule count/complexity, languages, effective input token band, output token/byte length, streaming mode/schedule, window/special-token policy, retries and caching. Initial historical reference: <=1024 effective input tokens, 400 output tokens, 70% SSE / 30% JSON, with an additional upper-band test. This is a proposed profile, not a measured production distribution. Explicit OFF configurations may have their own faster results but cannot stand in for full-security results.

**Three distinct proofs:** F = complete firewall overhead with a controlled token-emitting producer and real selected detectors; G = same gateway path goodput; C = real customer-provider integration and only its actually tested completion capacity. A controlled producer is allowed for F/G but is not a real-generation C result.

**Trials:** proposed 5-minute warmup, three independent 30-minute plateaus (restart between trials), 1066 scheduled RPS with >=1064 successful qualified completions/s, <=0.1% app/infrastructure errors, zero safety failures and zero loadgen schedule drops; two-hour soak; higher-rate sweep for the plus ceiling. All started requests belong to an accounted cohort, including timeouts/cancellations. Rates use a frozen wall window; drain does not manufacture a favorable denominator.

**Streaming:** qualify completion overhead, first-content added delay and per-request maximum release lag separately. A whole-response blocking mode can have fast completion overhead yet slow first content. No late classifier can undo content already released. Measure mid-stream backpressure/holdback with an intrinsic producer schedule rather than subtracting an expanded receive interval as provider work.

**Quality:** the accepted recall/FPR/span accuracy thresholds are deliberately NOT invented here. Sign them before tuning and expand the held-out corpus until its confidence intervals support the claim. Per-window FPR is not request FPR; independence cannot be assumed.

## Task index

| Task | Work | Depends on | Status |
|---|---|---|---|
| A00 | Contain potential credential exposure; freeze a secure audit baseline | None | NOT_RUN |
| A01 | Map GitHub source to real Docker, GPU and frontend builds | None | NOT_RUN |
| A02 | Approve one policy, failure and latency contract | A01 | NOT_RUN |
| A03 | Repair complete timing before interpreting latency | A01, A02 | NOT_RUN |
| A04 | Freeze the policy-aware detection/redaction corpus | A01, A02 | NOT_RUN |
| A05 | Compile a single authoritative organization execution plan | A02, A04 | NOT_RUN |
| A06 | Fix composed failure handling before replacing the backend | A02, A04, A05 | NOT_RUN |
| A07 | Create the local-only runtime and outbound operation boundary | A02, A05, A06 | NOT_RUN |
| A08 | Validate and package the local input detector on L4 | A03, A04, A07 | NOT_RUN |
| A09 | Implement a local backend for every selected output/grounding capability | A03, A04, A07, A08 | NOT_RUN |
| A10 | Optimize active policy, preprocessing and redaction work | A03, A04, A05, A08 | NOT_RUN |
| A11 | Remove synchronous logging and redundant trace work | A03, A05 | NOT_RUN |
| A12 | Make stream delivery and output actions match their promises | A03, A06, A09, A10 | NOT_RUN |
| A13 | Bound admission, shared state and policy freshness | A03, A05, A06 | NOT_RUN |
| A14 | Build the immutable live Docker verification stack | A00, A01, A02 | NOT_RUN |
| A15 | Verify organization controls in the real frontend under load | A05, A06, A12, A14 | NOT_RUN |
| A16 | Find one-unit practical capacity and CPU efficiency | A03, A04, A08, A09, A10, A11, A12, A13, A14 | NOT_RUN |
| A17 | Qualify below-20-ms overhead and 1064+ RPS together | A15, A16 | NOT_RUN |
| A18 | Cut over safely, remove cloud callers, then credentials | A00, A07, A09, A15, A17 | NOT_RUN |
| A19 | Independent reproducibility and release handoff | A17, A18 | NOT_RUN |

## Detailed task cards

### A00 - Contain potential credential exposure; freeze a secure audit baseline

**Owner:** Infrastructure owner + security owner  
**Dependencies:** None  
**Status:** NOT_RUN  
**Source evidence:** C22 in SOURCE_MANIFEST.json  
**Files/areas:** root ai-mesh-firewall header; authorized_keys / approved credential inventory; Git history and release artifacts. Existing named areas are source-reviewed; NEW tests/tools must be implemented before execution.

**Implementation steps**

1. Identify the corresponding credential through the authorized infrastructure inventory without publishing private-key bytes or trying the key from this assistant environment.
2. Revoke/rotate if genuine; review relevant access logs and downstream copies. Coordinate history cleanup after containment. A history rewrite changes audit refs; preserve a private mapping to the replacement secure commit.
3. Run approved secret scanning on tracked source, history and build artifacts; retain only sanitized rule/path/commit findings in general evidence. Do not include a source archive containing unreviewed secrets in the deliverable.

**Unit/static verification**

1. Verify the detection/reporting tool never prints secret bodies. Confirm the revocation procedure has an owner and change record.

**Live Docker and wire-boundary verification**

1. Authorized operator verifies old access no longer grants unintended privileges and the replacement deployment still has its intended access. Do not test against unrelated hosts.

**Real frontend verification**

1. Verify approved administration/login still works; no frontend build contains secret configuration.

**Success criteria**

1. Credential disposition recorded; genuine key revoked/rotated; no unexpected outage; replacement source and release identity pinned.

**Fail / stop criteria**

1. Unresolved potentially active credential blocks new deployment, not read-only analysis or isolated unit tests. Deleting a file without resolving access is insufficient.

**Rollback:** Use authorized replacement credentials; do not restore an exposed key for convenience.

**Required evidence:** `A00/manifest.json`, exact sanitized commands and exit codes, test results, expected/actual observations, source/image/model/policy identities, relevant wire/API/audit/browser request-ID joins, reviewer and unresolved issues. Screenshots alone cannot prove payload safety.

---

### A01 - Map GitHub source to real Docker, GPU and frontend builds

**Owner:** Backend lead + DevOps + QA  
**Dependencies:** None  
**Status:** NOT_RUN  
**Source evidence:** C05, C16, C17, C21 in SOURCE_MANIFEST.json  
**Files/areas:** SOURCE_MANIFEST.json; gateway/Dockerfile; actual Compose overlays / image registry; served frontend assets. Existing named areas are source-reviewed; NEW tests/tools must be implemented before execution.

**Implementation steps**

1. Record the inspected ansh SHA and older main SHA; select the actual candidate intentionally. Identify any source ZIP with its hash before treating it as equivalent.
2. Map gateway, inference owner, proxy, control, cache, workers and frontend to image digests, source revisions, CPU/memory quotas, replicas, endpoints and cloud/zone. Record dirty state without resetting it.
3. Identify an isolated test tenant pair, a staging-only producer/recorder, approved load ceiling and last safe rollback tuple. Do not infer current deployment from old plan IPs.
4. Compare notebook environment to release-container GPU/runtime. Freeze CUDA/ORT/TensorRT/model/tokenizer/export/engine identities. Record unknowns rather than guessing.

**Unit/static verification**

1. Run tools/source_contract_probe.py on a matching authorized checkout; it validates selected blob identities before executing isolated pure definitions.
2. Confirm live-test paths exist using the actual test collector; zero collected tests is not a pass.

**Live Docker and wire-boundary verification**

1. Read selected docker inspect fields and GPU identity inside the inference owner; correlate image labels with build logs. No full env/inspect dumps in shared evidence.

**Real frontend verification**

1. Open the baked staging frontend, record build ID and one request ID, then match the serving backend and policy version.

**Success criteria**

1. Every serving component and endpoint has reproducible identity; all mismatch decisions documented; safe test/rollback environment exists.

**Fail / stop criteria**

1. Unknown image revision, stale UI, wrong branch, unapproved production target, or mixed test/customer data.

**Rollback:** Read-only task. Resolve discrepancies before deploying or benchmarking.

**Required evidence:** `A01/manifest.json`, exact sanitized commands and exit codes, test results, expected/actual observations, source/image/model/policy identities, relevant wire/API/audit/browser request-ID joins, reviewer and unresolved issues. Screenshots alone cannot prove payload safety.

---

### A02 - Approve one policy, failure and latency contract

**Owner:** Product owner + organization administrator + security lead  
**Dependencies:** A01  
**Status:** NOT_RUN  
**Source evidence:** C06, C07, C08, C09, C17, C18 in SOURCE_MANIFEST.json  
**Files/areas:** V3_SCOPE_AND_ACCEPTANCE_CONTRACT.json; enforcement.py; config_sync.py; FirewallConfig.build_gateway_payload; OutputGuardrailControls.jsx. Existing named areas are source-reviewed; NEW tests/tools must be implemented before execution.

**Implementation steps**

1. Make zero platform external AI unconditional in the final release, including guard-driven repair calls, background consumers, health checks and simulator security inference. Keep authorized customer generation distinct.
2. Sign supported rules and per-surface OFF/observation/enforce behavior. Define explicitly empty valid policy, unavailable policy, deprecated fields and alias normalization.
3. Resolve the current unconditional input PII/secret floor against the new requested org control. This requires a versioned behavior migration, not silently disabling a safety rule during optimization.
4. Preserve output action enums and existing human_review-to-flag behavior unless an explicitly new approval workflow is approved. Separate desired rule action, final transport disposition and review status.
5. Sign p99 below 20 ms, >=1064 good gateway RPS, input/output/token/rule bounds, strict output modes and numeric quality limits before candidate tuning.
6. Define approved failure actions and minimum mandatory platform-integrity controls. A malformed or unsupported rule must be rejected at activation, not silently skipped at request time.

**Unit/static verification**

1. A table maps each UI field to serializer, payload, execution-plan dependency, resolver and final byte effect. Include default/null/false cases and conflicting rules.

**Live Docker and wire-boundary verification**

1. Small staging requests for opposite tenant policies establish expected wire behavior before migration.

**Real frontend verification**

1. Administrator saves, reloads and inspects each supported setting; unavailable/unsupported policies are visibly rejected rather than falsely enabled.

**Success criteria**

1. Versioned signed contract has no null mandatory quality/output/metric decisions; compatibility and behavior migration approved.

**Fail / stop criteria**

1. Undefined selected capability, ambiguous flag/monitor behavior, arbitrary unbounded policy admitted under a blanket SLO, or silent alteration of the denominator.

**Rollback:** No production settings changed. Preserve current secure behavior until migration is authorized.

**Required evidence:** `A02/manifest.json`, exact sanitized commands and exit codes, test results, expected/actual observations, source/image/model/policy identities, relevant wire/API/audit/browser request-ID joins, reviewer and unresolved issues. Screenshots alone cannot prove payload safety.

---

### A03 - Repair complete timing before interpreting latency

**Owner:** Instrumentation engineer + performance QA  
**Dependencies:** A01, A02  
**Status:** NOT_RUN  
**Source evidence:** C06, C11, C12, C20 in SOURCE_MANIFEST.json  
**Files/areas:** middleware.py; main.py proxy_chat; pipeline_trace.py compute_addon_split/finalize_stage_metrics; stream_orchestration.py; NEW controlled producer and accounting tests. Existing named areas are source-reviewed; NEW tests/tools must be implemented before execution.

**Implementation steps**

1. Add an outer monotonic timestamp before authentication; capture complete-body readiness separately. Preserve request IDs through retries and stream finalization.
2. Replace the old model_output=duration-ttft accounting shortcut for complete overhead. Record provider wait, input work, queue waits, output work, first release, per-output-unit arrival/release, and completion.
3. Retain signed residuals, valid/invalid measurement state and explicit ran/disabled/unavailable/early-block outcomes. Do not round a sub-ms stage into an implied skip.
4. Build a controlled producer whose intrinsic token-ready schedule is independent of consumer backpressure, with paired direct-path calibration. No cross-host wall-clock subtraction without an uncertainty bound.
5. Define complete overhead plus first-content added delay and per-request maximum output-release lag; avoid hiding middle-stream withholding inside provider duration.
6. Correct the existing frontend latency labels; preserve compact safe traces. Mark historical invalid metrics as unqualified, not retroactively corrected numbers.

**Unit/static verification**

1. NEW tests: pre_auth_delay_included; ttft_not_firewall; signed_residual_rejected; middle_stream_hold_visible; early_block_skip_truth; incomplete_stream_counted.
2. Use a fake monotonic clock for exact algebra; approve a live tolerance before trials.

**Live Docker and wire-boundary verification**

1. Vary producer TTFT 50/500/2000 ms with fixed firewall work. Inject 5 ms input and 7 ms output work; their critical-path impact must appear.
2. Inject 30 ms middle hold, downstream stall and upstream stall separately. Missing timestamps and negative residuals must make the run invalid.
3. Verify the real proxy/edge as well as origin so buffering is not omitted.

**Real frontend verification**

1. The same request ID shows backend-verified timing, provider time separately and truthful partial/degraded/skipped status; two-second TTFT cannot be displayed as two-second firewall compute.

**Success criteria**

1. All timing negative controls pass; all metric boundaries signed; auth and output included; no residual clamps create a false green result.

**Fail / stop criteria**

1. Provider TTFT counted as guard tax, missing auth, hidden buffering, averaged stage p99 sum, dropped failed samples, or frontend divergence.

**Rollback:** Revert instrument only with performance publication disabled; retain raw failing evidence.

**Required evidence:** `A03/manifest.json`, exact sanitized commands and exit codes, test results, expected/actual observations, source/image/model/policy identities, relevant wire/API/audit/browser request-ID joins, reviewer and unresolved issues. Screenshots alone cannot prove payload safety.

---

### A04 - Freeze the policy-aware detection/redaction corpus

**Owner:** Security QA + ML engineer + independent label reviewer  
**Dependencies:** A01, A02  
**Status:** NOT_RUN  
**Source evidence:** C05, C08, C09, C17 in SOURCE_MANIFEST.json  
**Files/areas:** NEW tests/detection_corpus and manifest; existing injection/PII/SDK/action tests. Existing named areas are source-reviewed; NEW tests/tools must be implemented before execution.

**Implementation steps**

1. Retain the historical minimum of 300 attacks across >=8 families plus 300 benign inputs. Add separate redaction span/field labels and supported output categories.
2. Include developer text, quoted attacks, multilingual text, long-middle attacks, encoded/split PII, tool/context boundaries, JSON/UTF-8 offsets, mixed rules and new output content.
3. Label in policy and trust context. A zero-content-policy tenant and a full-enforce tenant have different expected transport actions for the same text.
4. Freeze train/calibration/held-out partitions by family/source; deduplicate beyond request IDs. Independently audit >=10% and all disputed critical cases.
5. Set recall/FPR and false-redaction limits before optimization. Expand benign samples sufficiently for the approved confidence bound; a tiny sanity set proves no production FPR.
6. Record legacy/candidate disagreement without assuming the old judge is ground truth. No new paid AI labeling calls are authorized by this task.

**Unit/static verification**

1. Schema, duplicates, partition leakage, provenance, language coverage and expected sanitized fields checked. Keep changed behaviors separate from exact-preservation tests.

**Live Docker and wire-boundary verification**

1. Replay held-out synthetic samples through real Docker with provider recorder; validate both decisions and outbound bytes.

**Real frontend verification**

1. Representative cases visible under both tenant policies, with correct actual action, detector state, flags and safe detail rehydration.

**Success criteria**

1. Frozen hashed corpus, independently audited labels and signed numeric gates cover every selected capability.

**Fail / stop criteria**

1. Missing output labels, hand-picked repeated prompts, test leakage, unsupported quality claim, or changing labels merely to make a model pass.

**Rollback:** Test-only addition; adjudicate/version label changes and preserve old reports.

**Required evidence:** `A04/manifest.json`, exact sanitized commands and exit codes, test results, expected/actual observations, source/image/model/policy identities, relevant wire/API/audit/browser request-ID joins, reviewer and unresolved issues. Screenshots alone cannot prove payload safety.

---

### A05 - Compile a single authoritative organization execution plan

**Owner:** Control/backend engineer + frontend owner  
**Dependencies:** A02, A04  
**Status:** NOT_RUN  
**Source evidence:** C05, C06, C07, C09, C17, C18 in SOURCE_MANIFEST.json  
**Files/areas:** core/models.py build_gateway_payload; serializers and policy sync (locate current paths); config_sync.py; main.py input gate; output_guard.py enablement. Existing named areas are source-reviewed; NEW tests/tools must be implemented before execution.

**Implementation steps**

1. Resolve True/False/None/absent consistently for each input/output/MCP/RAG surface; preserve legitimate independent output selection through an explicit field, not accidental global inheritance.
2. Translate selected policies into required local detector dependencies and ordered transforms. OFF produces no decision and no detector call solely for that disabled rule; shared dependencies are accounted for.
3. Move optional content floors to that plan according to A02; do not make auth, isolation or resource bounds optional. Keep telemetry sanitization distinct from org-selected payload transformation.
4. Normalize aliases at control ingress and emit a canonical versioned payload. Reject conflicting/invalid fields; distinguish empty valid selection from missing/unloaded tenant state.
5. Atomically publish/swap revisions and key derived/cached state correctly. Preserve policy-first effective_prompt ordering and single terminal-writer authority.
6. Deprecate misleading legacy toggles with explicit migration/UI treatment rather than reintroducing multiple competing sources of truth.

**Unit/static verification**

1. NEW per-field truth table tests, empty-vs-missing tests, alias conflict tests, dependency-count tests and versioned cache invalidation.

**Live Docker and wire-boundary verification**

1. Two tenants: OFF->MONITOR->ENFORCE->OFF while keeping text constant. Verify actual input/output model call counts, payload changes, policy hash and every worker.
2. Repeat after worker restart and missed notifications; required unavailable configuration cannot become a default allow.

**Real frontend verification**

1. Save through /api/firewall/config/, reload the actual controls and verify runtime revision. No hidden calls after an OFF toggle; FLAG and disposition separately visible.

**Success criteria**

1. Every supported selection changes actual execution/action as signed; no cross-tenant effect and no required rule disappears.

**Fail / stop criteria**

1. False coerced to default true, raw None inherits a global model unexpectedly, input/output gate divergence, empty config treated as global default, or hidden optional-data floor.

**Rollback:** Roll back versioned policy adapter and compatible UI together; never revert tenant isolation.

**Required evidence:** `A05/manifest.json`, exact sanitized commands and exit codes, test results, expected/actual observations, source/image/model/policy identities, relevant wire/API/audit/browser request-ID joins, reviewer and unresolved issues. Screenshots alone cannot prove payload safety.

---

### A06 - Fix composed failure handling before replacing the backend

**Owner:** Security/backend engineer  
**Dependencies:** A02, A04, A05  
**Status:** NOT_RUN  
**Source evidence:** C05, C08, C09, C10, C19 in SOURCE_MANIFEST.json  
**Files/areas:** scanner.py scan_prompt_with_tier2/scan_output_with_tier2; output_guard.py inspect; enforcement.py; secure_streaming.py; test_gemini_output_failclosed.py. Existing named areas are source-reviewed; NEW tests/tools must be implemented before execution.

**Implementation steps**

1. Introduce explicit detector outcomes EVALUATED/DISABLED/UNAVAILABLE/INVALID, separate from attack score. No required missing runtime returns a clean Tier-1 result.
2. Close output breaker-open and absent-scanner branches; propagate incomplete evaluation through both output and input wrappers.
3. Preserve terminal BLOCK through the output resolver. Replace blanket degraded->redact with signed capability-specific failure policy; redacting known PII does not validate unknown semantics.
4. Unify errors for local runtime failure and preserve API-compatible operational status. Do not rewrite already-sent HTTP headers; SSE must carry a correct terminal error.
5. Keep normal output FLAG as delivery-plus-flag and human_review compatibility. Observational failure must not claim a completed clean scan.
6. Replace intermediate-only tests with full composition assertions and retain old tests where still appropriate.

**Unit/static verification**

1. NEW full-chain tests for breaker OPEN/HALF_OPEN, runtime None, crash, timeout, malformed logits, parse failure, selected block+degraded, no-op redaction and monitor failure.

**Live Docker and wire-boundary verification**

1. Inject each failure in staging; join scanner outcome, OutputGuard verdict, resolver action, actual client bytes and audit. Required input failure yields no provider dispatch.
2. Test both JSON and SSE; test failure after headers and after earlier content to ensure delivery completeness is truthful.

**Real frontend verification**

1. Operational unavailable/partial/review labels match final wire outcome. A block intermediate cannot be shown as full prevention after content was released.

**Success criteria**

1. Zero safety-invariant breaches; every failure branch has explicit tested behavior; no unsafe clean fallback and no inadvertent block downgrade.

**Fail / stop criteria**

1. Fail-open hidden as Tier-1 clean, degraded BLOCK coerced to nonblocking action without contract, missing selected scan, or only mocked intermediate verdict tested.

**Rollback:** Contain affected profiles or restore last safe complete chain; never use hidden cloud fallback to turn failure green.

**Required evidence:** `A06/manifest.json`, exact sanitized commands and exit codes, test results, expected/actual observations, source/image/model/policy identities, relevant wire/API/audit/browser request-ID joins, reviewer and unresolved issues. Screenshots alone cannot prove payload safety.

---

### A07 - Create the local-only runtime and outbound operation boundary

**Owner:** Runtime/backend engineer + infrastructure security  
**Dependencies:** A02, A05, A06  
**Status:** NOT_RUN  
**Source evidence:** C01, C02, C03, C04, C05, C06 in SOURCE_MANIFEST.json  
**Files/areas:** tier2_gemini_client.py; bedrock_client.py; scanner construction; jobs and control consumers (inventory); NEW local detector factory and operation ledger. Existing named areas are source-reviewed; NEW tests/tools must be implemented before execution.

**Implementation steps**

1. Inventory reachable external AI operations including startup, health checks, jobs, embeddings, reranking, guard-driven response repair, RAG judgment and simulator. Classify customer-requested operations separately.
2. Implement a local-only factory/capability registry; missing local artifacts fail readiness or selected operation safely, never construct cloud guard clients as a fallback.
3. Restrict the authority that can dispatch customer-provider requests; a caller-supplied purpose tag or an allowlisted shared vendor hostname is not sufficient separation.
4. Package pinned artifacts before serving. Build-time model acquisition is a controlled artifact process, not request-time download.
5. Define typed outcomes and version metadata for local input/output models. Supply a validated local-only rollback before deleting legacy integrations.
6. Keep any temporary old-engine comparison explicitly authorized and isolated; final background auditors and slow pools cannot retain remote AI.

**Unit/static verification**

1. Factory tests cover absent env values, old provider aliases, missing artifacts, no credentials and no network. Seed/restart tests must not recreate cloud runtime defaults.

**Live Docker and wire-boundary verification**

1. Deny platform AI egress using approved staging controls; run cold start and every selected surface while permitted customer generation still works. Observe attempted calls, not just successful API responses.
2. Exercise retries and health checks; inspect process-specific operation counters plus network evidence.

**Real frontend verification**

1. No orphaned Bedrock/Vertex-backed security controls appear usable after local cutover; unsupported capabilities are explicit rather than silently allowed.

**Success criteria**

1. Local serving is independent of platform AI credentials and remote guard availability; zero attempted external guard AI calls in covered lifecycle tests.

**Fail / stop criteria**

1. Any cloud fallback, unclassified AI operation, runtime download, hidden repair-generation call or default provider reverting to bedrock.

**Rollback:** Last validated local build or safe deny after final cutover; temporary legacy migration rollback only before final removal and with approval.

**Required evidence:** `A07/manifest.json`, exact sanitized commands and exit codes, test results, expected/actual observations, source/image/model/policy identities, relevant wire/API/audit/browser request-ID joins, reviewer and unresolved issues. Screenshots alone cannot prove payload safety.

---

### A08 - Validate and package the local input detector on L4

**Owner:** ML/runtime engineer  
**Dependencies:** A03, A04, A07  
**Status:** NOT_RUN  
**Source evidence:** C05, C15, C16 in SOURCE_MANIFEST.json  
**Files/areas:** NEW GPU runtime image and model manifest; scanner Tier-2 adapter; gateway/Dockerfile and pyproject/locks. Existing named areas are source-reviewed; NEW tests/tools must be implemented before execution.

**Implementation steps**

1. Pin checkpoint/tokenizer/export/precision and hashes. Reproduce the reported 2.14 ms result inside the actual release image; it is not deployment evidence until reproduced.
2. Compare pristine FP32 reference with candidate ONNX/TensorRT using identical IDs/masks. A .half().float() model is not the pristine reference. Quality and numerical fidelity are distinct gates.
3. Define <=512-token model windows including special tokens, overlap, trust boundaries and exact full-input coverage. A 1024-token request is not a [1,1024] tensor, and overlap may require more than two windows.
4. Warm supported profiles and verify actual graph placement separately from timed runs; get_providers is configuration, not proof all kernels ran on TensorRT.
5. Choose explicit GPU/process/queue ownership after fork; avoid one engine per CPU worker by default. Persist compatible versioned engine caches and validate readiness.
6. Start with the measured FP16 candidate. INT8/quantized exports need a separate quality and runtime-support gate.

**Unit/static verification**

1. Parity/finite-score tests, window coverage, long-middle attacks, artifact mismatch, cache incompatibility and missing profile.

**Live Docker and wire-boundary verification**

1. At least 50 warmups and 1000 timed component invocations per valid W=1,2,3,4,7 profile, then sustained mixed-shape load. Record transfers/tokenization separately and combined application boundary.
2. Cold/cached/corrupt-cache restarts; no ready-request builds or silent CPU/cloud fallback.

**Real frontend verification**

1. Show actual detector version/state on engineering evidence; do not replace product latency with this component timing.

**Success criteria**

1. Pinned artifact parity and signed detection quality pass, correct runtime placement known, measured shape matrix recorded in the release image.

**Fail / stop criteria**

1. Wrong checkpoint/tokens, unscanned middle, invalid sequence length, NaNs, CPU run labeled GPU, or cold engine marked ready.

**Rollback:** Restore last validated local artifact/runtime namespace; no unreviewed model substitution.

**Required evidence:** `A08/manifest.json`, exact sanitized commands and exit codes, test results, expected/actual observations, source/image/model/policy identities, relevant wire/API/audit/browser request-ID joins, reviewer and unresolved issues. Screenshots alone cannot prove payload safety.

---

### A09 - Implement a local backend for every selected output/grounding capability

**Owner:** ML/security engineer + output/RAG engineer  
**Dependencies:** A03, A04, A07, A08  
**Status:** NOT_RUN  
**Source evidence:** C03, C04, C06, C09, C17 in SOURCE_MANIFEST.json  
**Files/areas:** output_guard.py; rag_pipeline/bedrock_embedder.py; llm_judge.py; grounding attachment in main.py; NEW capability ledger. Existing named areas are source-reviewed; NEW tests/tools must be implemented before execution.

**Implementation steps**

1. Map each current category and action to exact local detectors, span extractors or deterministic checks. Prompt Guard is an injection detector, not a generic PII/grounding/harm replacement.
2. Choose and pin local output model(s) only after evaluating required languages/classes and measured compute. Do not declare a missing backend implemented by renaming an option.
3. Retain deterministic typed span redaction; local semantic span candidates require validation and original-field offsets. A binary score cannot identify what to redact.
4. Replace platform embedding/judge calls while retaining customer-owned embedding dimension/provider contracts. Similarity alone is not proof of grounded factual correctness.
5. Resolve guard-driven rewrite: use validated deterministic remediation or a local model within a separately qualified profile. Do not add a remote customer-model repair call under the local-only guard label.
6. Budget every output window/evaluation and any local grounding context work. Reject unsupported activation rather than silently disabling a requested policy.

**Unit/static verification**

1. Per-capability corpus scores, schema/spans, off/monitor/enforce behavior, reference numerical fidelity, dimension compatibility and output no-op cases.

**Live Docker and wire-boundary verification**

1. Run local-only tests through JSON/SSE and RAG/MCP sources with new output data; no cloud credentials or guard egress. Measure each profile with actual output work.

**Real frontend verification**

1. Enabled capability reflects real support/version; any changed rewrite/human-review behavior is explicitly described and persisted.

**Success criteria**

1. Every selectable capability in the release has a tested local implementation and quality/cost evidence; no silent loss of semantics.

**Fail / stop criteria**

1. Unchosen semantic model, generic PG2 score advertised as all output safety, deleted RAG judge without replacement, or hidden external rewrite.

**Rollback:** Keep unsupported profiles unavailable until a safe local backend exists; restore validated local backend or deny affected operations.

**Required evidence:** `A09/manifest.json`, exact sanitized commands and exit codes, test results, expected/actual observations, source/image/model/policy identities, relevant wire/API/audit/browser request-ID joins, reviewer and unresolved issues. Screenshots alone cannot prove payload safety.

---

### A10 - Optimize active policy, preprocessing and redaction work

**Owner:** Performance/backend engineer  
**Dependencies:** A03, A04, A05, A08  
**Status:** NOT_RUN  
**Source evidence:** C05, C11, C14, C15, C21 in SOURCE_MANIFEST.json  
**Files/areas:** policy_engine.py; patterns.py and redaction helpers (profile actual callers); scanner.py active T1 and Tier-2 preprocessing; pipeline_trace.py bounded redaction. Existing named areas are source-reviewed; NEW tests/tools must be implemented before execution.

**Implementation steps**

1. Capture active CPU profiles per selected policy count/input length/clean-vs-dirty workload. Do not attribute the old disabled default scanner body to current traffic.
2. Reuse compiled pattern databases and immutable rule plans. Validate incompatible/unsafe patterns before activation; exact verification stays where a relaxed prefilter can overmatch.
3. Retain existing Hyperscan, persistent worker and bounded trace improvements. Optimize measured remaining queueing, repeated work, deobfuscation and output scans instead of re-claiming historical wins.
4. Bound decode depth/expansion, rule count/complexity and input work. A one-second thread timeout is neither a <20 ms guarantee nor proof the underlying regex stopped.
5. Apply transformations once per changed field/content version; preserve required new scans for new retrieved/output content. Original-to-normalized offsets must be verified.
6. Score any behavior change independently. Finite RapidFuzz/Hyperscan examples are regression evidence, not universal mathematical equivalence.

**Unit/static verification**

1. Differential tests for unchanged behavior; separate scored changes for old false blocks; Unicode/prefilter/decode/offset/property fixtures; policy-count stress.

**Live Docker and wire-boundary verification**

1. Unique content, supported language and upper-bound size/rule profiles inside release Docker; record CPU-seconds, event-loop lag, threads, RSS and per-stage latency.

**Real frontend verification**

1. Observed action/masks match selected rules and captured provider bytes; no stale category or phantom redaction in Scan Detail.

**Success criteria**

1. Quality contract maintained; changed behavior approved; mean CPU work and tail contribution improve on the active path.

**Fail / stop criteria**

1. Faster disabled function only, silently skipped invalid rule, global rule removal, wrong spans, unbounded decode or hidden coverage reduction.

**Rollback:** Revert isolated optimization and lower advertised capacity; never conceal fallback to a slower/unqualified engine.

**Required evidence:** `A10/manifest.json`, exact sanitized commands and exit codes, test results, expected/actual observations, source/image/model/policy identities, relevant wire/API/audit/browser request-ID joins, reviewer and unresolved issues. Screenshots alone cannot prove payload safety.

---

### A11 - Remove synchronous logging and redundant trace work

**Owner:** Observability/backend engineer  
**Dependencies:** A03, A05  
**Status:** NOT_RUN  
**Source evidence:** C06, C11, C13 in SOURCE_MANIFEST.json  
**Files/areas:** redis_log_handler.py; main.py logger setup; telemetry builders and Scan Detail reader. Existing named areas are source-reviewed; NEW tests/tools must be implemented before execution.

**Implementation steps**

1. Detach synchronous Redis publication from request execution; use bounded handoff and deliberate production logging levels.
2. Keep one authoritative decision per phase with typed supplementary findings/events; deduplicate durable records by request/phase/attempt rather than suppressing useful degradation evidence.
3. Store compact sanitized references instead of repeated prompt/response copies. Telemetry sanitization is a separate platform control, not a hidden change to the provider payload.
4. Define enqueue versus durable persistence, retry/spill and behavior on queue exhaustion. Nonblocking enqueue does not prove durability.
5. Keep cardinality bounded in metrics; request IDs belong in events, not unbounded Prometheus labels.

**Unit/static verification**

1. Schema/version tests, request-ID joins, no-op redaction representation, authorized detail lookup and queue bounds.

**Live Docker and wire-boundary verification**

1. Slow/disconnect audit/log sink; measure loop lag and p99. Recover and reconcile IDs: proposed completeness >=0.999, zero missing critical test decisions.

**Real frontend verification**

1. Reload Scan Detail with compact references; cross-tenant access denied, no raw synthetic secret leaks, honest unavailable detail.

**Success criteria**

1. Zero synchronous request-path Redis log publishes, bounded resources and accurate durable audit accounting.

**Fail / stop criteria**

1. Drop without accounting, raw data exposure, unbounded queue, duplicate authoritative decisions, or UI broken after trace shrink.

**Rollback:** Restore compatible sanitized event schema, not unsafe raw copies or synchronous high-volume logs.

**Required evidence:** `A11/manifest.json`, exact sanitized commands and exit codes, test results, expected/actual observations, source/image/model/policy identities, relevant wire/API/audit/browser request-ID joins, reviewer and unresolved issues. Screenshots alone cannot prove payload safety.

---

### A12 - Make stream delivery and output actions match their promises

**Owner:** Streaming/backend engineer + frontend QA  
**Dependencies:** A03, A06, A09, A10  
**Status:** NOT_RUN  
**Source evidence:** C08, C09, C10, C12, C17 in SOURCE_MANIFEST.json  
**Files/areas:** secure_streaming.py; output_guard.py sanitizer; enforce_output; OutputGuardrailControls.jsx. Existing named areas are source-reviewed; NEW tests/tools must be implemented before execution.

**Implementation steps**

1. Declare strict whole-response withholding, pre-release chunk enforcement and completion-time observation as distinct supported modes.
2. Retain boundary-aware redaction with explicit span/overlap contracts. No bounded context can guarantee arbitrary future-dependent whole-response semantics.
3. Preserve chosen BLOCK/REDACT/FLAG behavior through the full chain and handle headers-already-sent correctly. A late block is not a retroactive HTTP 403.
4. Verify no-op redaction separately from valid pre-masked data. Whole-response replacements are emitted once, and human_review remains delivery+flag unless an explicit new workflow is implemented.
5. Measure first content, every release unit and completion. Count per-flush model work and partial streams. Lowering flush latency changes output work and egress costs.
6. Do not certify strict full-response mode under an unchanged first-token SLO merely because completion overhead is small.

**Unit/static verification**

1. NEW property tests split every synthetic secret/Unicode/JSON boundary; output outages at start/middle/end; rewrite latch; first-content/complete-body contracts.

**Live Docker and wire-boundary verification**

1. Capture raw SSE through the real edge and compare to independent producer schedule; selected strict mode releases zero original content before final approval.
2. JSON and stream masks/actions agree where modes promise equivalent behavior; partial delivery is recorded as partial.

**Real frontend verification**

1. Correct misleading universal HTTP-403/no-content hints. Actual wire, final action and review/partial badges agree and sensitive data never flashes before UI masking.

**Success criteria**

1. Signed stream-mode contract is satisfied byte-for-byte; no hidden holdback or output compute; policy outcomes remain distinct.

**Fail / stop criteria**

1. Already-delivered content described as withheld, missing midstream scan, silent no-op scrub, duplicated replacement, or unsupported strict promise.

**Rollback:** Restore known-safe output mode or withhold/deny; no fail-open performance workaround.

**Required evidence:** `A12/manifest.json`, exact sanitized commands and exit codes, test results, expected/actual observations, source/image/model/policy identities, relevant wire/API/audit/browser request-ID joins, reviewer and unresolved issues. Screenshots alone cannot prove payload safety.

---

### A13 - Bound admission, shared state and policy freshness

**Owner:** Distributed-systems engineer + SRE  
**Dependencies:** A03, A05, A06  
**Status:** NOT_RUN  
**Source evidence:** C06, C07, C20 in SOURCE_MANIFEST.json  
**Files/areas:** middleware.py; config_sync.py / policy sync; rate limiter and kill-switch modules (locate); worker/GPU queue ownership. Existing named areas are source-reviewed; NEW tests/tools must be implemented before execution.

**Implementation steps**

1. Measure connection-pool wait, Redis execution, network wait and logging separately. Prefetch independent reads without reordering body-producing or terminal stages.
2. Maintain tenant-scoped atomic quota or validated lease accounting; retry mutations safely and handle NOSCRIPT without double charging.
3. Set bounded admission, connection pools, GPU queues and cancellation cleanup before increasing proxy/server timeouts. Rate limits do not substitute for concurrency limits.
4. Publish and enforce policy/model/kill-switch freshness, including missed updates and cold partial loads. Retain the historical 50 ms kill-switch target only if signed and measured on this deployment.
5. Separate tenant/direction failure state from genuinely shared runtime health. Do not multiply quotas or replicate uncontrolled GPU contexts with worker count.

**Unit/static verification**

1. Lease/retry/cancel, stale snapshot, empty-vs-missing, partial load, breaker isolation and quota exhaustion tests.

**Live Docker and wire-boundary verification**

1. At least two workers and two tenants; interrupt updates, exhaust pools, stall producer/client, reload scripts and restart GPU owner. Prove safe bounded recovery and actual cap effectiveness.

**Real frontend verification**

1. Saved quotas/rules and kill switches take effect within the signed bound on all ready workers. 429/503/content block remain distinct.

**Success criteria**

1. No stale unsafe allow, cross-tenant side effects, quota multiplication or unlimited queue; measured waits within declared budgets.

**Fail / stop criteria**

1. Invisible stale state, ineffective concurrency option, infinite retry or missing selected scan on overload.

**Rollback:** Reduce admitted traffic or restore safe coordination; never loosen tenant isolation or fail-open required budgets.

**Required evidence:** `A13/manifest.json`, exact sanitized commands and exit codes, test results, expected/actual observations, source/image/model/policy identities, relevant wire/API/audit/browser request-ID joins, reviewer and unresolved issues. Screenshots alone cannot prove payload safety.

---

### A14 - Build the immutable live Docker verification stack

**Owner:** DevOps + QA  
**Dependencies:** A00, A01, A02  
**Status:** NOT_RUN  
**Source evidence:** C15, C16, C17 in SOURCE_MANIFEST.json  
**Files/areas:** actual Compose files and overlays; gateway/Dockerfile; frontend build; NEW authorized producer/recorder image. Existing named areas are source-reviewed; NEW tests/tools must be implemented before execution.

**Implementation steps**

1. Build from the approved secure candidate, immutable image digests and pinned dependencies; record source-to-image and served-asset identity.
2. Use a named isolated staging project, synthetic tenants/data and a staging-only authenticated token producer/recorder. No production recorder/fault endpoints.
3. Grant the GPU only to the selected owner and verify from inside the container. A host notebook or nvidia-smi outside the container is not release proof.
4. Recreate from the same artifacts twice; preserve persistent test configuration deliberately. No docker cp, pip mutation, uncontrolled latest pulls or destructive shared-volume cleanup.
5. Ensure source-code secret scans occur before building images. Keep raw captures, credentials, cookies and auth storage out of shared artifacts.

**Unit/static verification**

1. Collect actual tests, build config validation and no secret-bearing release artifact checks.

**Live Docker and wire-boundary verification**

1. Real release gateway/control/cache/local models and proxy serve allow/redact/block fixtures; recorder validates one sanitized dispatch or zero dispatch as required.
2. Offline-ready restart and correct container/source/GPU identity proved.

**Real frontend verification**

1. Serve the baked production frontend, not a dev server; login and request correlation show the same image tuple.

**Success criteria**

1. Clean reproducible live staging stack exists, with protected evidence, actual local model execution and matching frontend/backend builds.

**Fail / stop criteria**

1. Manual runtime patch, wrong deployment, source bind mount called a release, host-only GPU proof or unauthorized data/target.

**Rollback:** Recreate the last compatible safe staging image tuple; never down -v shared production.

**Required evidence:** `A14/manifest.json`, exact sanitized commands and exit codes, test results, expected/actual observations, source/image/model/policy identities, relevant wire/API/audit/browser request-ID joins, reviewer and unresolved issues. Screenshots alone cannot prove payload safety.

---

### A15 - Verify organization controls in the real frontend under load

**Owner:** Frontend engineer + browser QA  
**Dependencies:** A05, A06, A12, A14  
**Status:** NOT_RUN  
**Source evidence:** C06, C07, C09, C17, C18 in SOURCE_MANIFEST.json  
**Files/areas:** OutputGuardrailControls.jsx; actual policy forms/simulator/Scan Detail; NEW Playwright live suite. Existing named areas are source-reviewed; NEW tests/tools must be implemented before execution.

**Implementation steps**

1. Map real routes and accessible labels; add stable test IDs only where needed. Preserve current /api/firewall/config/ PUT/GET contract and exact field vocabulary.
2. For each supported rule, test OFF, monitoring action, enforcement actions and OFF again across save/reload/restart. Include policy conflicts and a second tenant.
3. Show detector evaluated/disabled/unavailable separately from recommendation, final action, review status and delivery completeness.
4. Verify simulator explicit max_tokens=0 scan-only behavior without redefining omitted max_tokens. Simulator/burst is functional evidence, not fleet throughput.
5. Exercise control/analytics demand while gateway is loaded. Measure actual current polling/query behavior before assuming historical OOM defects remain.

**Unit/static verification**

1. Component mapping tests plus API validation; no browser network mocks in the acceptance suite.

**Live Docker and wire-boundary verification**

1. Join browser request ID, API response, detector call counters, provider capture and durable decision. Include outage and wrong-tenant attempts.
2. Functional browser tracing is separate from heavy benchmark clients.

**Real frontend verification**

1. Every visible action/control persists and affects bytes; strict output copy matches mode; no stale green scan on failure or secret flash.

**Success criteria**

1. All selected-rule transitions verified through actual services; authorized org control is real, and disabled work is absent where not shared by another selected rule.

**Fail / stop criteria**

1. Toast-only success, conflicting aliases, hidden detector, API/bytes/UI mismatch, mocked E2E or scan-only throughput marketed as full pipeline.

**Rollback:** Deploy compatible safe frontend/schema; do not bypass backend policy to fix a UI mismatch.

**Required evidence:** `A15/manifest.json`, exact sanitized commands and exit codes, test results, expected/actual observations, source/image/model/policy identities, relevant wire/API/audit/browser request-ID joins, reviewer and unresolved issues. Screenshots alone cannot prove payload safety.

---

### A16 - Find one-unit practical capacity and CPU efficiency

**Owner:** Performance engineer + independent verifier  
**Dependencies:** A03, A04, A08, A09, A10, A11, A12, A13, A14  
**Status:** NOT_RUN  
**Source evidence:** C05, C10, C11, C15, C16 in SOURCE_MANIFEST.json  
**Files/areas:** NEW arrival-rate harness and CPU sampler; actual gateway/inference/proxy cgroups; model queue/batch settings. Existing named areas are source-reviewed; NEW tests/tools must be implemented before execution.

**Implementation steps**

1. Freeze three separate profiles: selected fast rules; full declared local-security reference; explicitly minimal org-policy profile. Never mix their outcomes into one advertised maximum.
2. Hold GPU count, model, output mode, policy complexity and input/output corpus fixed during CPU sweeps. Record effective cpuset, quota period, ancestor constraints and throttling.
3. Sweep feasible 1/2/4/8/... serving CPU allocations and worker/native thread counts. Include inference-owner and proxy CPU rather than only the thin API cgroup.
4. At each point use open-loop scheduled arrivals and find the highest repeatable p99/error/resource-compliant rate; retain the adjacent failing point.
5. Sweep batching separately: within-request windows versus inter-request batching are different. Include GPU queue wait and simultaneous output scans; select settings from actual tails.
6. Report qualified RPS per allocated serving vCPU, per purchased vCPU and requests per consumed CPU-second. State mean CPU demand and failure work; never turn 1/GPU p50 into throughput.

**Unit/static verification**

1. Harness negative controls: missing selected scan, wrong counter denominator, known percentile arrays, schedule drops, partial stream, duplicate IDs and disabled-output fake win.

**Live Docker and wire-boundary verification**

1. Actual immutable container on target hardware, all required detectors, valid warmup and raw per-request/cgroup/GPU data. Separate diagnostic profiling from final timed trials.

**Real frontend verification**

1. A bounded real UI/control session runs alongside the serving workload; policy changes stay correct without destabilizing tails.

**Success criteria**

1. Reproducible one-unit q_safe and CPU/GPU/queue bottlenecks measured; denominator and selected profile explicit.

**Fail / stop criteria**

1. Overloaded producer/loadgen, changing policies mid-trial, excluding output/inference CPU, no failing neighbor, or ignored latency/error miss.

**Rollback:** Restore last passing settings and lower advertised rate; never disable chosen rules for a better ratio.

**Required evidence:** `A16/manifest.json`, exact sanitized commands and exit codes, test results, expected/actual observations, source/image/model/policy identities, relevant wire/API/audit/browser request-ID joins, reviewer and unresolved issues. Screenshots alone cannot prove payload safety.

---

### A17 - Qualify below-20-ms overhead and 1064+ RPS together

**Owner:** Performance QA + SRE + independent reviewer  
**Dependencies:** A15, A16  
**Status:** NOT_RUN  
**Source evidence:** C05, C10, C11, C12, C17 in SOURCE_MANIFEST.json  
**Files/areas:** frozen fleet manifest; separate in-region load generators/producer; signed profiles and metrics. Existing named areas are source-reviewed; NEW tests/tools must be implemented before execution.

**Implementation steps**

1. Size the fleet from measured q_safe, output work and approved failure reserve. Reprice current deployment; old GCP CUD costs are historical, not an AWS quote.
2. For a proposed primary band <=1024 actual effective input tokens, declare output length/schedule, rule counts/languages and stream/JSON mix; test both representative and upper-band workloads.
3. Prove producer/loadgen capacity above target before interpreting failures. After >=5 minute warmup, run three independent 30-minute trials, with a restart between trials.
4. A proposed 1066 scheduled RPS plateau must deliver >=1064 successful qualified gateway completions/s, with <=0.1% application/infrastructure errors, zero safety failures and zero schedule drops.
5. Require p99 complete firewall overhead <20 ms in every required trial/profile, plus the separately signed streaming first-content/release-lag constraints. Invalid attribution is INCONCLUSIVE, not PASS.
6. Run a two-hour soak; then higher arrival-rate steps establish the plus ceiling. Qualify real BYOK integration separately and claim its throughput only at the tested rate.
7. Merge raw samples/histograms before percentiles; preserve failed trials and drain accounting. Do not count policy-block-heavy traffic or 503 shedding as successful allowed completions.

**Unit/static verification**

1. Independent recomputation from raw evidence, hash consistency, counter conservation and estimator negative controls.

**Live Docker and wire-boundary verification**

1. Full guard path under immutable release configuration. Measure edge buffering, memory, FD/VRAM, audit lag and selected detector coverage.
2. Failure availability requires separate surviving-fleet testing; a four-node 2/1/1 layout can lose half capacity on worst-zone failure.

**Real frontend verification**

1. Run a small functional browser suite during each plateau; corresponding cases retain correct settings, actions and trace values.

**Success criteria**

1. Every declared profile/trial passes safety, quality, complete latency and goodput simultaneously; maximum passing rate and excluded profiles published.

**Fail / stop criteria**

1. Any required miss, skipped guard, hidden holdback, insufficient upstream capacity, dropped schedule, incorrectly counted error or arbitrary threshold relaxation.

**Rollback:** Advertise only the last passing profile/rate or withhold the release claim; retain failure evidence.

**Required evidence:** `A17/manifest.json`, exact sanitized commands and exit codes, test results, expected/actual observations, source/image/model/policy identities, relevant wire/API/audit/browser request-ID joins, reviewer and unresolved issues. Screenshots alone cannot prove payload safety.

---

### A18 - Cut over safely, remove cloud callers, then credentials

**Owner:** Release lead + security owner + DevOps  
**Dependencies:** A00, A07, A09, A15, A17  
**Status:** NOT_RUN  
**Source evidence:** C01, C02, C03, C04, C06, C17, C19 in SOURCE_MANIFEST.json  
**Files/areas:** Gemini/Bedrock factories and callers; RAG/MCP/jobs/simulator/control seeds; Compose/IAM/secret configuration. Existing named areas are source-reviewed; NEW tests/tools must be implemented before execution.

**Implementation steps**

1. Run approved canary comparisons with human-adjudicated discrepancies; no remote paid comparison is authorized automatically by this document.
2. Retain the previous source requirement of >=100000 paired verdicts over >=7 days where applicable, or explicitly approve a revised local/offline evaluation contract. Do not pretend synthetic replay is real traffic.
3. Promote local-only cohorts only after all selected capabilities are covered. Prove a local-only rollback build before final removal.
4. Remove reachable cloud guard engines, health checks, retries and seed defaults; distinguish unrelated customer/S3 dependencies rather than deleting boto3 wholesale.
5. Only after code cutover and observation, remove narrowly scoped platform AI credentials/IAM/endpoints through authorized change control. Customer generation access must stay intact.
6. Repeat offline-ready startup, worker schedules and full functional matrix after deletion and restart. Monitor attempted operations, not absence of log messages.

**Unit/static verification**

1. Source inventory and regression suites confirm removed callers; static findings are triaged by reachability/purpose rather than bare SDK import names.

**Live Docker and wire-boundary verification**

1. All selected product surfaces run with platform AI egress denied and no platform AI credentials; no AccessDenied retry storms or seed resurrection.

**Real frontend verification**

1. No ghost vendor-backed options or misleading health status; all selected action controls remain usable and accurate.

**Success criteria**

1. Selected capabilities work on the local-only path; zero platform external AI attempts; observation/quality gates met; credential cleanup sequenced after callers.

**Fail / stop criteria**

1. Hidden API fallback/auditor, required capability removed, unauthorized shadow calls, IAM revoked before callers or customer provider broken.

**Rollback:** Validated local-only rollback or containment after final cutover; any credential restoration is a separate security decision.

**Required evidence:** `A18/manifest.json`, exact sanitized commands and exit codes, test results, expected/actual observations, source/image/model/policy identities, relevant wire/API/audit/browser request-ID joins, reviewer and unresolved issues. Screenshots alone cannot prove payload safety.

---

### A19 - Independent reproducibility and release handoff

**Owner:** Independent verifier + product/release owners  
**Dependencies:** A17, A18  
**Status:** NOT_RUN  
**Source evidence:** C06, C08, C10, C17 in SOURCE_MANIFEST.json  
**Files/areas:** release evidence; Eraser diagram sources; operational runbook and approved source manifest. Existing named areas are source-reviewed; NEW tests/tools must be implemented before execution.

**Implementation steps**

1. A second engineer rebuilds the same candidate and repeats the functional action suite and one full qualification trial without hidden terminal/notebook state.
2. Publish raw-data hashes, source/image/model/policy identities, test commands, measured profiles, goodput/error rates and CPU/GPU denominator.
3. Update diagrams to actual process/queue ownership and output-mode choice. Pending local output capability cannot appear as a verified working box.
4. Document source changes from this audit, selected slow/unqualified profiles, stop triggers, policy freshness, resource bounds, cold start and local-only rollback.
5. Keep source-review evidence separate from executed unit tests, live Docker/UI proof and final performance qualification.

**Unit/static verification**

1. Manifest/file hash validation, complete task evidence, independent percentile/count recomputation.

**Live Docker and wire-boundary verification**

1. Independent clean replay verifies runtime state, egress boundary and release performance.

**Real frontend verification**

1. Independent operator repeats save/reload/actions/errors under both tenants on the actual baked UI.

**Success criteria**

1. Complete signed evidence, reproducible live outcome and truthful qualified claim; every mandatory task genuinely complete.

**Fail / stop criteria**

1. Only source comments/tests read, screenshots without bytes, unrepeatable image, missing raw data or max-RPS claim without a passing workload.

**Rollback:** Withhold certification and retain the last qualified release; do not mark NOT_RUN as PASS.

**Required evidence:** `A19/manifest.json`, exact sanitized commands and exit codes, test results, expected/actual observations, source/image/model/policy identities, relevant wire/API/audit/browser request-ID joins, reviewer and unresolved issues. Screenshots alone cannot prove payload safety.

---

## Live Docker and frontend execution recipe

### 1. Read-only identity, before any change

Use the actual authorized checkout. Do not run checkout/reset/clean on a deployment to match this audit. After an authorized credential-history cleanup, use the mapped safe successor commit.

```bash
cd "$REPO_ROOT"
git rev-parse HEAD
git status --short
docker context show
docker compose version
docker ps --no-trunc --format '{{.ID}} {{.Names}} {{.Image}} {{.Label "com.docker.compose.project"}} {{.Label "com.docker.compose.service"}}'
```

Do not publish full environment variables, docker inspect objects or substituted Compose config. Fill the evidence manifest with selected safe fields. A host checkout does not identify an existing container's source automatically.

### 2. Source-only baseline probe included in this pack

```bash
python3 "$PACK_ROOT/tools/source_contract_probe.py" --self-test
python3 "$PACK_ROOT/tools/source_contract_probe.py" \
  --repo "$REPO_ROOT" \
  --manifest "$PACK_ROOT/SOURCE_MANIFEST.json" \
  --out "$EVIDENCE_DIR/source-probe.json"
```

The probe reads pinned source and executes selected pure definitions only; it does not import the application, create cloud clients, run Docker or benchmark the model. It reports the current timing/failure/mode behavior. Exit 3 means observed V3 contract conflicts; this is expected on the reviewed baseline. Exit 2 means identity/scope mismatch. Exit 0 is a completed probe without those detected conflicts, NOT a live release pass. Changing a source hash is not how a failing finding is closed: rerun reviewed candidate tests and the full live gate.

### 3. Build and recreate the authorized isolated project

Fill service names and ordered Compose files from A01. These are invocation patterns, not a statement that a new GPU/test overlay already exists:

```bash
PROJECT=approved_staging_project
COMPOSE_FILES=(-f /absolute/path/base.yml -f /absolute/path/staging-gpu.yml)
DC=(docker compose --project-name "$PROJECT" "${COMPOSE_FILES[@]}")
"${DC[@]}" config --quiet
"${DC[@]}" config --services
"${DC[@]}" config --images
# Only after explicit staging-change approval:
"${DC[@]}" build "$GATEWAY_SERVICE" "$CONTROL_SERVICE" "$FRONTEND_SERVICE"
"${DC[@]}" up -d --no-deps "$GATEWAY_SERVICE" "$CONTROL_SERVICE" "$FRONTEND_SERVICE"
"${DC[@]}" ps
```

Use the repository's actual immutable registry deployment flow instead when appropriate. No destructive down -v, no floating latest substitution and no manual package installation inside a release container. Record the actual GPU owner and all companion services needed by the chosen architecture.

### 4. Test collection and execution

Existing inspected test example: `gateway/ai_mesh_gateway/tests/test_gemini_output_failclosed.py`. It tests intermediates and is not sufficient alone. New local/full-chain tests must exist and collect before their invocation is a gate. Use a pinned test image with dependencies; the live app under test is the release image.

```bash
"${DC[@]}" run --rm --no-deps "$TEST_SERVICE" \
  python -m pytest --collect-only -q "$IMPLEMENTED_TEST_PATH"
"${DC[@]}" run --rm --no-deps "$TEST_SERVICE" \
  python -m pytest -q "$IMPLEMENTED_TEST_PATH" --junitxml=/evidence/tests.xml
```

Zero collected tests, unexpected skip/xfail, or only mocked backend calls fail the live acceptance requirement. Do not install unpinned test tools during qualification.

### 5. Byte-boundary action matrix

Use synthetic values and a protected credentials file. Do not put API keys in this document or shell tracing. For each case, correlate: request/attempt ID; API response/SSE bytes; selected policy revision; actual detector-run status; upstream request bytes/call count; output producer schedule; durable decision; frontend state.

| Case | Wire and runtime expectation |
|---|---|
| All optional content rules OFF | No optional rule influence or model call solely for them; platform integrity still enforced |
| Injection ENFORCE | Selected model actually evaluates; validated blocking fixture has no provider dispatch |
| Same injection MONITOR | Finding recorded, no block/mutation from that monitor-only rule |
| Input PII REDACT | Approved fields masked before dispatch; actual provider bytes verified |
| PII OFF + different class REDACT | Only the selected class changes; broad redact_all cannot override another OFF rule |
| Redact plus independent block | Approved transformation order, no dispatch, one authoritative phase decision |
| Required local runtime unavailable | Explicit unavailable/operational failure; no clean Tier-1 success substitution |
| Output block+degraded | Terminal failure remains terminal through resolver and stream writer |
| Output breaker denies | Selected scan absent is visible and handled under its failure contract |
| Output FLAG / human_review | Actual delivery plus review flag per existing compatible behavior; not an approval queue |
| Strict whole-response BLOCK | No original content released before final allowed decision |
| Chunk-boundary REDACT | Every split canary masked in raw stream, not merely after UI rendering |
| Invalid/no-op required mask | Explicit safe failure; no phantom redaction |
| User disables a rule | Saved value, compiled version, every worker and actual call counts agree after reload/restart |
| Tenant mismatch / missing state | No wrong-tenant data/policy or silent global inheritance |
| Cloud guard access denied | All local selected policies work without any attempted cloud guard call |

### 6. Real browser acceptance

The inspected output controls use `/api/firewall/config/` and real fields such as `output_pii_enabled`, `output_pii_action`, `output_credential_enabled`, `output_policy_action`, `factuality_check_enabled`. Discover actual routing/page selectors in the current frontend and record them; add stable test IDs where necessary. Do not invent a working selector in a document.

Run the repository's pinned Playwright binary against the actual baked staging UI and APIs, no response mocks. Save state transitions for two tenants, reload settings, submit fixtures and inspect Scan Detail while the backend load test runs. Protect authentication storage and trace/HAR secrets. Browser wall time includes network/provider work and is not the internal firewall metric.

### 7. Resource and rate qualification

The load driver must consume SSE incrementally and record scheduled/actual starts, dropped schedules, completed/partial/error outcomes and raw per-request timings. Standard buffered HTTP timing is insufficient for release-lag proof. Use a separately resourced, capacity-tested producer/loadgen.

Before testing a 1064+ fleet target, measure a single serving unit at data-led arrival-rate steps. Record inference windows/s, actual output calls, model queue waits, CPU usage/throttling and effective CPU budget. Use `templates/capacity-profile.json` and the retained failing neighbor to choose the practical point.

## Reproducibility and final evidence

For every task/run retain full commit, dirty state, base/app image digests, lockfiles, served UI asset identity, cloud/SKU/CPU topology, all cgroups and sibling budgets, GPU UUID/driver/runtime, model/tokenizer/export/engine hashes, policy revision and enabled-rule plan, input/output corpus and seed, shape/overlap, precision, queue/batch/worker parameters, cache state, timing definitions, raw outcome cohort, error/drop/drain accounting and actual sanitized commands.

A seed alone is not a workload archive. A tag alone is not an image identity. A screenshot alone is not provider-byte evidence. A unit assertion on OutputGuard alone is not proof that enforce_output and the stream writer preserved it.

All release claims remain unqualified until the signed live evidence exists. This package's helper self-tests and document validation only prove that the delivered tools/documents are internally usable; they do not certify the user's service.
