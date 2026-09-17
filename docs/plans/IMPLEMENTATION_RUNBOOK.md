# AI Mesh Firewall: implementation and verification runbook
## Haiku-free enforcement | below 20 ms firewall overhead | at least 1,064 evaluated requests/second

**Prepared:** 2026-09-10  
**Status:** implementation proposal and acceptance contract; no application, Docker, cloud, IAM, or production changes executed by preparing this file.  
**Primary deliverable:** a reproducible, security-gated path from the supplied plans to a measured release.  
**Audience:** backend/ML engineers, frontend engineers, QA, DevOps, security reviewer, product owner.  
**First task:** T00, read-only environment and release identification. The first application-code task is T03, measurement repair; T02, the labeled corpus, proceeds alongside it. T01 signs the behavior and SLO before either can certify a release.

> A fast classifier is not a fast, correct firewall. A green unit suite is not a live deployment. A visible dashboard badge is not proof that the provider received sanitized bytes. Every task below requires the appropriate code, live-container, network-boundary, and frontend evidence.

## Contents

1. Start here: the first working session
2. Source hierarchy and unresolved decisions
3. Target architecture and replacement map
4. Hard constraints
5. Exact measurement and throughput contracts
6. Action and failure acceptance matrix
7. Dependencies and task index
8. Detailed task cards T00-T28
9. Live Docker verification recipe
10. Frontend verification recipe
11. Reproducibility and evidence contract
12. Release, rollback, and stop rules
13. Source register and external implementation references

---

## 1. Start here: the first working session

### 1.1 What to do first

**T00 does not optimize or deploy anything.** Establish which checkout, images, running containers, policy versions, model artifact, and environment you will actually test. The old plans contain multiple environments and conflicting historical targets; do not infer the active machine from an old IP address or a notebook prompt.

1. Unpack this pack on an engineer's workstation or the authorized test VM.
2. Identify the actual repository root and the test Docker context. Use a staging environment and synthetic tenant, not a production customer.
3. Run the included read-only collector as described below. It writes evidence only; it does not build, restart, deploy, or alter containers.
4. Complete `templates/environment-manifest.example.json` using the observed information. Unknown values stay unknown, not guessed.
5. Complete the source-to-runtime map in T00: gateway container, console/frontend container, control API, policy source, database/cache, inference process, load generator, and test upstream.
6. Capture one sanitized browser Network record and one corresponding backend request ID. This establishes the frontend is talking to the intended gateway and control API, not an older deployment.
7. Review T01's decisions. Then implement T03's timing tests and prepare T02's corpus. Do not start at TensorRT tuning or remove Haiku first.

**Commands executable now on the authorized VM (replace the repository path):**

```bash
export REPO_ROOT=/absolute/path/to/the/verified/AI_Mesh_Firewall
export PACK_ROOT=/absolute/path/to/aimesh_implementation_pack
export EVIDENCE_DIR="$HOME/aimesh-evidence/T00-$(date -u +%Y%m%dT%H%M%SZ)"
bash "$PACK_ROOT/tools/collect_baseline.sh" "$REPO_ROOT" "$EVIDENCE_DIR"
```

The collector intentionally does not print complete environment variables, Docker inspect objects, rendered Compose configuration, API keys, authentication tokens, or request bodies. Its output still contains internal infrastructure metadata: treat it as internal, not public documentation.

**T00 succeeds when:** the manifest identifies the actual code/image versions and all live endpoints; mismatches are written down; a rollback image is identified; all unknowns blocking tests have owners. A working `nvidia-smi` alone is not sufficient.

**T00 fails when:** nobody can establish which build serves the browser, the local checkout does not match the image being tested, the test points at an unauthorized tenant, or the test environment cannot be isolated. Resolve that before code changes.

### 1.2 The first application-code change

Implement **T03 / source Gate G1**: prove that firewall latency is not provider time-to-first-token. In the controlled test upstream, vary first-token delay from 50 ms to 2,000 ms while leaving firewall work unchanged. The reported firewall overhead must not increase by approximately 1,950 ms. Add known firewall delays and prove those delays *do* appear.

At the same time implement **T02 / source Gate G0**: assemble and label the detection/redaction corpus. An instrumentation pass cannot authorize a detector with unacceptable false negatives or false positives. [S2: G0, G1, ordering law]

### 1.3 What this package contains, and what it does not

Included: this task specification, an offline HTML reading copy, editable Eraser diagram source, an evidence/status schema, example manifests, and a read-only baseline collector.

Not included: implemented product changes, an already-working benchmark adapter for the unseen repository, credentials, a new published Eraser workspace, or live Docker/frontend test results. Test files and CLI tools marked **NEW** below must be implemented before running their example commands. Existing paths are **source-reported locations** and must be confirmed at T00.

---

## 2. Source hierarchy and unresolved decisions

### 2.1 How to read the evidence

- **[SOURCE]**: behavior, constraint, or historical finding explicitly in the uploaded plans. A source saying something was measured does not mean it was re-measured while writing this runbook.
- **[USER-REPORTED]**: notebook/terminal outputs pasted in this conversation, including the approximately 2.14 ms TensorRT W=1 result. Full script, runtime placement, sample set, and end-to-end boundaries were not independently inspected here.
- **[PROPOSED]**: implementation detail or acceptance rule added to make the plan executable. It requires T01 sign-off where it changes a contract.
- **[VERIFY]**: a fact that must be established from the running checkout/deployment. No current repository or cloud session was audited in preparing this file.
- **[OPEN]**: a release blocker, not permission to choose a convenient default.

### 2.2 Source precedence

1. **S1, September 7 HLD** defines the final GCP target and points to S3/S4.
2. **S3, August 27 evidence plan, GPU update in section 11**, replaces older CPU-only conclusions. Its earlier sections remain diagnostic evidence, not the final GPU budget.
3. **S4, September 2 cost matrix**, supersedes the old response-only egress estimate and cost totals. Use its final corrected sections, not an earlier conflicting table in the same corpus.
4. **S2, August 27 task tracker**, supplies G0/G1/security/removal gates; old GPU/CPU and cost entries are subordinate to S1/S3/S4.
5. **S5, August 25 verification**, supplies surviving ordering, frontend, tenant-isolation, and SQL-query constraints. Its withdrawn detector fan-out and CPU-only target are not revived.
6. S6-S10 provide compatibility tests and historical behavior, not authorization to reintroduce Haiku, Kafka, or an older AWS topology.

**Do not silently merge all diagrams into one architecture.** The reference production design here is GCP `asia-south1`, four `g2-standard-24` nodes/eight L4 GPUs. The current AWS L4 is a development/bake-off environment unless T00 establishes and T01 authorizes a different deployment. Moving the final design to AWS requires a separate topology, capacity, and pricing decision. [S1; S3: 11.2; S4: 1-2]

### 2.3 Decisions that must be signed in T01

| ID | Decision | Proposed implementation position | Why it cannot remain implicit |
|---|---|---|---|
| D01 | Percentile and latency boundary | Interpret the new target as **p99 < 20 ms**, not only p50, for the declared input/output profile. Also retain p50/p95/p99. | User supplied <20 ms but no percentile; this is a stronger proposed acceptance rule, not an already signed source lock. |
| D02 | Meaning of 1,064+ RPS | At least 1,064 **successful fully evaluated allow/redact completions/s** in the qualified all-permitted workload; separate mixed-policy evaluation throughput; separate real-provider completion proof. | Policy blocks, 429s, 503s, scan-only calls, and /health cannot inflate the headline. |
| D03 | Action schema | Preserve the existing API; map observed behavior to explicit final action, enforcement mode, findings, and flag/review state through an adapter. | Final MONITOR/FLAG precedence is not fully specified by S1. Do not invent enum values in production. |
| D04 | Output semantics | Choose full-response withholding, pre-release chunk enforcement, or observational completion-time scanning, with separate names and SLOs. | Completion-time classification cannot retroactively block released content. |
| D05 | Output semantic detector | Pin the exact model, taxonomy, input representation, threshold, and failure behavior for required output policies. | S1's classifier-at-DONE label does not identify a fully validated replacement for every Haiku output category. |
| D06 | Window construction | Specify special-token overhead, overlap, byte/token caps, trust boundaries, and excess-length response. | Older plans use 64-token overlap; later tables call <=1,024 tokens two windows. Those may not be the same workload. Never silently truncate to make a table fit. |
| D07 | Quality gates | Set numerical recall/FPR and redaction error limits by family/language; freeze them before tuning. | The source mandates measurement but does not give a complete accepted threshold for every capability. Missing thresholds block promotion. |
| D08 | In-process inference ownership | Define the gateway process(es) embedding Triton/C API or ORT, GPU ownership, queues, and startup after fork. | Two separate pods joined by localhost HTTP/gRPC are not an in-process C API. A private per-worker queue is not one node-wide shared queue. |
| D09 | Budget and failures | Retain the historical $5,000 reference cap unless reapproved; define safety during failures separately from full-rate availability. | Four nodes over three zones can lose two nodes in the worst zone. Eight GPUs do not automatically preserve 1,064 RPS after that loss. |
| D10 | Zero Haiku rollout | Any old-engine comparison is a temporary, authorized migration activity; final platform enforcement has no Haiku fallback/auditor. | The older parity plan's permanent slow Haiku pool conflicts with the user's complete-removal objective and D18. |
| D11 | Stream buffering | State allowed buffering delay and whether it enters the advertised <20 ms. | S3's 120 ms coalescing timer and older 25-50 ms timers cannot be silently excluded from a <20 ms per-chunk release claim. Changing them changes cost. |
| D12 | Fault/degraded policy | Sign behavior for every missing detector, missing tenant config, rate-limit uncertainty, and audit exhaustion. | Masking known PII does not prove an unavailable injection detector would have allowed the request. |

---

## 3. Target architecture and replacement map

### 3.1 Final target from the sources

Two logical edges: tenant chat/SDK traffic enters the regional chat load balancer; the operator console uses the frontend/control edge. Gateway admission, policy, Tier-1, redaction, and enforcement stay local. Prompt Guard inference is on local L4 hardware. Tenant generation remains external BYOK. Cloud SQL is control-plane-only; Valkey remains quota/kill-switch state; audit and telemetry leave asynchronously. MCP uses per-organization sandboxes. [S1: 2-7]

The historical costed target is four G2 nodes/eight L4 GPUs, approximately $4,561/month, approximately 1,064 RPS. These are planning values; this runbook turns them into gates. Do not re-quote them as a new vendor price or a measured deployment. [S4: 1, 8]

### 3.2 What stays and what changes

| Concern | Keep | Replace or repair | Evidence needed |
|---|---|---|---|
| Nine named trace stages | `auth`, `rate_limit`, `policy`, `input_scan`, `kill_switch`, `model_routing`, `model_input`, `model_output`, `output_guardrail` | Correct timing and explicit ran/skip/degraded status; do not force physical execution into trace-label order | Stage/invariant tests plus real trace |
| Input semantics | The required Tier-2 role | Haiku generation -> pinned local PG2 engine and adapter | Exact-input fidelity, corpus quality, actual EP placement |
| Sensitive-data protection | Supported PII/secret/credential rules, G33/G53, fail-closed byte verification | Faster detection/segmentation and reduced duplicate work | Original-to-sanitized payload evidence at upstream boundary |
| Final action | Existing resolver and single-writer authority | Any mapping that equates BENIGN with unconditional ALLOW | Mode/precedence fixture matrix |
| Routing | Deterministic `select_model()` and compliance floor | Any residual adjudicator call, if actually present | Real catalog/settings tests; zero adjudicator network calls |
| Output controls | `enforce_output()` and supported output checks | Fail-open skips, misleading whole-response block claims | Raw SSE wire evidence, not only final JSON |
| Shared state | Tenant-scoped quota and kill-switch truth | Redundant round trips, wrong-org defaults, unbounded staleness | Multi-worker and multi-node failure tests |
| Telemetry | One authoritative decision record and operator visibility | Repeated prompt copies, sync Redis logging, loss without accounting | Audit ID reconciliation plus frontend Scan Detail |

[S1: 3-4; S2: G1/G2/G4; S5: 4; S6: 1; S7]

### 3.3 Pipeline invariants, not a new fan-out architecture

Preserve policy mutation before downstream scanning of `effective_prompt`; preserve terminal short-circuits before any provider dispatch. Prefetch independent state only when semantics and freshness are proved. Do not run existing policy/T1/T2/terminal-writer stages through a new `asyncio.gather()` simply because local inference is faster. The reviewed plan withdrew that transformation. [S5: 4.0-4.4]

An in-process runtime and a separately deployed inference service are different deployment choices. T12 must produce a process/queue ownership diagram before claiming the HLD's shared-queue and zero-RPC properties. A measured same-host RPC alternative is possible only as an explicitly approved change, not a silent reinterpretation. [S1: 2-3; X4]

---

## 4. Hard constraints

These are release-blocking unless a named source conflict requires a signed replacement contract. A performance improvement cannot waive a security gate.

| ID | Constraint | Mechanical proof |
|---|---|---|
| HC01 | No source/staging/prod mismatch hidden behind a local test | Commit, image digest, UI build and runtime manifest per run |
| HC02 | No raw sensitive content forwarded when policy requires redaction | Inspect authorized synthetic upstream capture; verify transformed fields, encoding variants and logs |
| HC03 | Input terminal BLOCK precedes provider dispatch | Upstream receives zero calls for each blocked request ID |
| HC04 | Exactly one authoritative final action/audit decision per request phase | Join request IDs, decisions, audit sink, UI and provider recorder |
| HC05 | Policy/body producers stay before consumers | Order tests including redact+block co-matches and model downgrade |
| HC06 | No silent skip of required semantic scan on eligible allow/redact/monitor traffic | Actual model-run counters joined to eligibility; no always-allow stubs |
| HC07 | Valid early block skips are recorded truthfully | `ran=false` plus skip reason; no fabricated nine nonzero durations |
| HC08 | Missing/unloaded tenant state cannot inherit another tenant's posture | Two-tenant and no-org canary tests |
| HC09 | Config/kill-switch freshness is bounded, including missed notifications | Monotonic age/epoch evidence across all workers; retained 50 ms KS lock or explicit reapproval |
| HC10 | No global input/output breaker that lets tenant A degrade B | Tenant/direction-isolation failure tests |
| HC11 | Redaction is a checked transformation, not a badge | Residual-detector and no-op-redaction tests; unmaskable mandatory data -> deny |
| HC12 | No unknown output semantic coverage advertised as preserved | Capability ledger names replacement, model and tests; unsupported mandatory policy -> blocked release |
| HC13 | No retroactive whole-response BLOCK after content release | Strict mode zero content bytes; streaming truncation labeled as such |
| HC14 | Do not send 1,024 tokens directly to a 512-token PG2 model | Shape/window coverage assertions, special-token and overlap accounting |
| HC15 | No raw prompt/response copies in general metrics or public evidence | Synthetic canaries, sanitization audit; retention and authorization on content refs |
| HC16 | No external database/analytics call quietly added to chat admit | Dependency trace and network counters; Cloud SQL remains control-only |
| HC17 | No synchronous telemetry publisher on the hot event loop | Delay sink and measure loop lag; inspect publisher call sites |
| HC18 | No model/engine download or compile on a ready replica's customer request | Cold-start/cache-miss tests; readiness stays false until validated warmup |
| HC19 | No unbounded queue, thread count, or worker-to-GPU replication | Cgroup/FD/GPU-memory budget; overload proof before raising timeouts |
| HC20 | No cache-hit/repeated-ping benchmark represented as unique-request capacity | Fixed corpus hash, unique variants, cache-hit counters and separate cached profile |
| HC21 | No /health, stub, scan-only, errors, or policy-block-heavy workload used as completed BYOK RPS | Independent tax/capacity/real-provider eligibility and result labels |
| HC22 | No timing clamp, averaged host percentile, or omitted stalled request hiding a tail | Raw records/histograms; accounting tests; invalid runs fail |
| HC23 | Security gates precede Haiku deletion; callers removed before IAM | T5-equivalent fixtures -> canary -> all callers removed -> scoped permission cleanup |
| HC24 | No dangerous rollback reintroduces cross-tenant exposure | Last known safe image or traffic containment; safety owner review |
| HC25 | No live destructive/fault/load testing without environment authorization | Staging approval, synthetic tenant, rate cap, abort handler, named operator |
| HC26 | No repository edits via `docker cp`, `pip install`, or manual patching inside running release containers | Bake immutable images; recreate authorized staging service and verify digest |
| HC27 | No production traffic/security mode changed by the simulator's implicit defaults | Explicit authorized scan-only contract; absent `max_tokens` does not silently bypass generation in the general API |
| HC28 | No unsupported new UI mode or arbitrary classifier threshold | Signed T01 schema mapping and frozen validation thresholds |

HC01, HC18, HC20, HC22, HC25-HC28 contain additional proposed operational safeguards; they are not claims that the source defines these exact identifiers. The underlying source safety requirements are in S2/S5/S6/S8/S9.

---

## 5. Exact measurement and throughput contracts

### 5.1 Three proofs; do not conflate them

| Gate | Workload | What a pass means | What it does not mean |
|---|---|---|---|
| F: firewall overhead | Live built Docker services, real detectors/policy/redaction/output controls, controlled token-emitting upstream | Qualified firewall work and buffering meet the approved latency gate | Public provider can generate 1,064 chats/s |
| G: gateway capacity | The same fully guarded live path at controlled arrival rate, with token-emitting upstream explicitly labeled synthetic | At least 1,064 successful qualified completions/s of the gateway workload | A scan-only, /health, or real-BYOK capacity claim |
| C: real-provider integration/capacity | Authorized real BYOK endpoint, real output, real guards | Integration correctness; capacity only at the rate actually tested | Extrapolated real-generation throughput from F/G |

This preserves the source separation of firewall tax from completed customer generations. The stronger p99 threshold and exact run durations below are proposed acceptance rules. [S10: F/G/C split; S2: G1.5]

### 5.2 Proposed headline to sign before release testing

> At least 1,064 successful, fully evaluated requests per second on the declared fleet and workload, with p99 firewall-added latency below 20 ms; applicable input and output controls enabled; exact token bands, policy mode, streaming release mode, provider type, and failure/error rates published.

The word **plus** means a measured ceiling above 1,064 can be reported after a rate sweep. It is not permission to assume unbounded growth or buy additional nodes without budget approval.

**Proposed primary workload:** <=1,024 input tokens counted on the actual effective full scan surface; record each model's special tokens and window count. Run a separate worst-band profile concentrated near the upper bound, not only mostly-short prompts. Use the historical 70% SSE / 30% JSON and 400-output-token shape as a named initial planning workload [S3/S4], then add the actual approved production distribution. Multi-turn/tools/context count toward the bound; they are not free channels. Do not silently apply old 64-token overlap and still call this two windows.

### 5.3 Required timestamps and accounting

Capture a request-scoped monotonic clock and record at least:

- complete request-body admission, gate start/end, inference enqueue/start/end, final input decision;
- provider dispatch start/end, first and last upstream content receipt, provider retries and wait;
- each output unit's upstream receipt, scan completion and downstream-send completion;
- final response completion/abort and the reason;
- policy/model/tokenizer version, actual window count, raw request/response byte counts, coalescing/gzip mode.

Keep `T_pre` (complete body -> permitted provider dispatch or terminal input rejection), `T_post` (last upstream content -> finalization), and output release/holdback metrics distinct. A stream's `T_pre + T_post` can miss work and withholding in the middle; it is not automatically total firewall tax.

**T03 must implement and validate `T_fw_addon` against the independent controlled producer.** Do not subtract `last_upstream_receive - provider_dispatch` blindly: output processing/backpressure can lengthen that interval and be incorrectly counted as provider time.

For a controlled producer, record **intrinsic token-ready duration**, independent of consumer backpressure, and separately record producer send-blocked time. A conservative diagnostic is gateway total duration minus intrinsic producer duration; it includes transport overhead and must be labeled accordingly. Calibrate paired direct-producer and guarded paths to distinguish transport from firewall work. Never subtract wall-clock timestamps from different machines without an established error bound; durations measured on each host are safer than cross-host timestamp differences.

For real BYOK, retain raw gateway/provider phases and state estimator limitations. If provider-intrinsic timing is unavailable, do not claim exact causal total tax by arithmetic alone. F's controlled proof remains labeled controlled; C is not silently upgraded.

**Required anti-cheating tests:**

1. Same firewall work with producer first-token delay 50/500/2,000 ms: TTFT changes, measured firewall work does not track that change.
2. Inject 5 ms before dispatch and 7 ms into output enforcement: approximately 12 ms extra must appear under the test's approved accounting tolerance.
3. Inject 30 ms coalescing/withholding during the middle of a stream: release-delay and advertised end-to-end estimator cannot hide it under upstream wait.
4. Pause downstream consumption and separately pause the producer: report different causes; do not credit downstream backpressure as model compute.
5. Overlap independent operations deliberately: elapsed time is the critical path/interval union, not the sum of all worker spans.
6. Delete a required timestamp, duplicate a request ID, or force a negative residual: reject the run, never clamp it green.

**Source clarification requiring explicit approval:** S2 says CI fails at any nonzero median reconciliation residual. Real instrumentation has finite precision. Start with exact equality for synthetic clock unit tests and propose a measured epsilon for live tests (initial candidate 0.25 ms, not a fact). Record this as a signed interpretation in D01, retain signed residuals, and do not loosen epsilon after seeing a failing candidate. [S2: G1.2-G1.6]

### 5.4 Release runs and sample accounting [PROPOSED]

- Warm models/engines and connection pools; measure cold startup separately.
- Three independent 30-minute steady-state F/G runs, each after at least 5 minutes of warm load; fixed artifacts/policies/workload; at least one process/container restart between independent runs.
- Run 1,066 scheduled requests/s for the target plateau, requiring **at least 1,064 successful qualified completions/s**, not merely 1,064 scheduled requests/s. This small surplus is a proposed test setting, not extra purchased capacity.
- Report offered/started/accepted/completed/correctly-blocked/incorrectly-blocked/failed/cancelled/dropped counts and per-second series. Tail analysis includes all started cohort requests after drain; completion-rate reporting uses the fixed steady-state wall window, not an extended drain denominator chosen afterward.
- In the all-permitted workload, 403/429/503, malformed outputs, skipped guards and partial streams are failures, not successes. In the mixed-policy correctness workload, expected policy blocks are correctly evaluated decisions, reported separately.
- Proposed infrastructure/application error ceiling: <=0.1% of started eligible requests, with zero safety-invariant breaches. Report deadline misses over all eligible started requests; timeout/cancelled requests cannot disappear from the latency accounting.
- p99 `T_fw_addon` <20 ms in every trial and required stratum. Also report `T_pre`, output holdback/release, and finalization separately. A profile that cannot support the approved total metric is **INCONCLUSIVE**, not PASS.
- Recompute percentiles from merged raw samples or compatible histograms. Never average per-worker/per-GPU/per-node p99 values. Freeze the quantile method in the harness.
- Zero load-generator schedule drops at the qualification plateau. Verify the client/producer are not resource-bound and can exceed the tested rate before interpreting a gateway failure.
- Two-hour steady soak at the qualified rate; watch RSS/VRAM/FD trends, queues, audit backlog, and policy freshness. Seven-day canary evidence is separate from this soak.
- Gate security and correctness independently of rate: a faster unsafe run is FAIL.

### 5.5 Honest failure expectations

A <20 ms target is not a guarantee for arbitrary input length, arbitrary semantic output policies, total model-generation time, or full-rate service during an AZ outage. Extra windows/output scans and stricter whole-response withholding must be measured and separately named. If a required profile misses, optimize or resize and rerun; do not remove its controls or reinterpret the metric after the run.

---

## 6. Action and failure acceptance matrix

**Contract table, not an assertion of today's exact response fields.** T01 maps these requirements to the existing API/`PipelineDecision` and signs MONITOR/FLAG semantics. Tests observe provider bytes and audit records as well as response labels.

| Case | Configuration/input | Required observation | Fail condition |
|---|---|---|---|
| A01 | Valid benign input, all required checks healthy | Allow; one permitted dispatch; correct policy/model versions | A detector score bypasses another blocking control |
| A02 | Policy BLOCK before scan | Terminal input response; no provider call; later stages marked skipped for this reason | GPU/provider executes despite terminal gate |
| A03 | Validated injection fixture, enforce mode | Final block before provider | Content reaches provider before verdict |
| A04 | Same fixture, monitorable rule in MONITOR | Record finding/recommended action and actual disposition; permit only as the signed monitor contract allows | Classified as benign, or required detector never ran |
| A05 | Supported PII with REDACT policy | Sanitized provider payload; original absent; response and audit agree | Badge says redacted but original value travels |
| A06 | Mandatory redact finds data but transformation is no-op/unmaskable | Deny under signed failure policy | Raw data forwarded after ineffective scrub |
| A07 | Co-matching redact and independent block | Preserve policy transformation order; terminal block still prevents dispatch; one decision | Redaction cancels independent block or duplicate audit |
| A08 | FLAG/review state with allowed or redacted outcome | Actual traffic outcome and review flag separately visible | FLAG has ambiguous transport outcome |
| A09 | Invalid auth, wrong org, unloaded policy | Deny or not-ready; no other tenant's defaults | Cross-tenant fallback or scanless allow |
| A10 | GPU timeout/unavailable; enforced semantic policy | Operational failure/deny; degraded telemetry; zero provider dispatch | Known PII redaction used as excuse to bypass absent injection check |
| A11 | GPU unavailable; monitor policy | Signed degraded-observation behavior; never invent successful scan | Degraded scan reported as a benign inference |
| A12 | Strict output block | No assistant content released before final safe decision; protocol-correct terminal handling | Any blocked content released then called whole-response block |
| A13 | Chunked output redact | Boundary-aware sanitized chunks; exact retained suffix/holdback measured | Secret split across chunks escapes or byte offsets corrupt text |
| A14 | Completion-time observational output classification | Earlier delivery acknowledged; finding/flag recorded | Late finding called preventive block |
| A15 | Kill switch/model isolation changed | All workers enforce within signed freshness bound; routing does not resurrect model | Stale worker or fallback bypass |
| A16 | Tenant A opens input breaker | Tenant B and unrelated output breaker unaffected | Cross-tenant/direction degradation |
| A17 | Missing max_tokens on ordinary chat | Existing API contract preserved; does not silently become scan-only | Accidental generation bypass |
| A18 | Explicit authorized scan-only simulator request | No generation; labeled scan-only; excluded from full-pipeline rate | Simulator promises no inference but bills provider |
| A19 | Audit sink unavailable | Bounded retry/spill or signed deny policy; visible degradation/loss accounting | Unbounded memory or unrecorded silent loss |
| A20 | Input beyond approved token/byte/window budget | Explicit length outcome under signed policy | Quiet head-tail truncation or first-window-only pass |
| A21 | Encoded/split sensitive fields and UTF-8 offsets | Required G33/G53/normalization behavior retained; correct original spans | Fast regex port changes coverage silently |
| A22 | Duplicate delivery/retry | Idempotent final-record handling and visible attempt IDs | Double quota/audit/dispatch unnoticed |
| A23 | Output guard exception after SSE headers sent | Protocol-correct termination/withholding according to mode | Attempts to change already-sent HTTP 200 to 403; leaks content |
| A24 | Optional surface disabled or unsupported | Truthful skipped/unsupported state and unchanged mandatory floor | All-nine-pass UI fabricates an execution |

---

## 7. Dependencies and task index

**Every status starts NOT_RUN.** No task is marked done because this document exists.

- T00 establishes the test identity. T01 signs the contract.
- T02 (G0 corpus) and T03 (G1 instrument) are the foundation. T04 makes the live reproducibility environment usable.
- Isolation, failure, frontend demand and verdict-neutral telemetry work can proceed in parallel once independently authorized; measured improvement claims still wait for T03.
- Engine/behavior changes wait for G0/G1 and a recorded G5 hardware/runtime/cost decision. A diagnostic benchmark report is evidence for that decision, not a posture approval.
- Haiku cutover waits for the replacement/fail-closed/capability gates. Removal and IAM cleanup happen last.

The task index and detailed cards follow. `Source path` means a location named in the uploaded documents, **not verified present in the current checkout**. `NEW` means a test, adapter, report or configuration to implement.

| Task | Work | Depends on | Owner | Status |
|---|---|---|---|---|
| T00 | Freeze the real environment and establish a baseline evidence bundle | None | Backend lead + DevOps + QA | NOT_RUN |
| T01 | Sign the SLO, action semantics, output mode and capability ledger | T00 | Product owner + security owner + backend/ML leads | NOT_RUN |
| T02 | Build the labeled security, redaction and compatibility corpus | T00, T01 | Security QA + ML engineer + independent reviewer | NOT_RUN |
| T03 | Repair timing, trace eligibility and frontend latency truth | T00, T01 | Backend instrumentation engineer + QA + frontend engineer | NOT_RUN |
| T04 | Create the immutable live Docker and test-upstream verification environment | T00, T01 | DevOps + backend QA | NOT_RUN |
| T05 | Close policy readiness, tenant isolation and refresh holes | T02, T04 | Backend/security engineer | NOT_RUN |
| T06 | Implement bounded admission and accurate degraded-mode handling | T01, T04, T05 | Backend + DevOps | NOT_RUN |
| T07 | Remove synchronous logging and duplicated telemetry work | T03, T04 | Backend + observability + frontend | NOT_RUN |
| T08 | Fix inline-code false blocks as a separately scored behavior change | T02, T03, T04 | Detection engineer + security reviewer | NOT_RUN |
| T09 | Fix tool-description leakage over-match separately | T02, T03, T04 | Detection engineer + security reviewer | NOT_RUN |
| T10 | Land the bounded native Tier-1 rewrite with original-span correctness | T02, T03, T08, T09, T11 | Performance/backend engineer + security QA | NOT_RUN |
| T11 | Reproduce PG2 fidelity, graph placement and L4 timing in the release container | T02, T03, T04 | ML/runtime engineer + QA | NOT_RUN |
| T12 | Implement inference ownership, engine cache and readiness lifecycle | T01, T04, T11 | Runtime engineer + backend lead + DevOps | NOT_RUN |
| T13 | Wire local findings into the existing enforcement authority | T01, T02, T05, T06, T10, T12 | Backend/security engineer | NOT_RUN |
| T14 | Reduce shared-state waits without changing gate order or quota truth | T03, T05, T06 | Backend/distributed systems engineer | NOT_RUN |
| T15 | Make redaction a verified single transformation per content version | T01, T02, T10, T13 | Backend/data protection engineer + QA | NOT_RUN |
| T16 | Close output guard coverage and streaming semantics | T01, T02, T03, T06, T12, T15 | Backend streaming engineer + ML/security owner | NOT_RUN |
| T17 | Remove remaining semantic dependency gaps on MCP and RAG | T01, T02, T05, T13, T16 | MCP/RAG engineer + security QA | NOT_RUN |
| T18 | Make action controls, simulator and Scan Detail verifiable in the real frontend | T01, T03, T04, T13, T15, T16 | Frontend engineer + browser QA | NOT_RUN |
| T19 | Prevent console analytics from destabilizing the serving fleet | T04, T05, T07 | Control/backend engineer + frontend engineer | NOT_RUN |
| T20 | Verify the real edge, SSE delivery and baked security configuration | T04, T16, T18 | DevOps/edge engineer + QA | NOT_RUN |
| T21 | Measure one serving unit and choose queue/batch settings from data | T03, T10, T12, T13, T14, T15, T16, T20 | Performance engineer + ML runtime engineer | NOT_RUN |
| T22 | Verify fleet topology, capacity procurement and cost envelope | T00, T01, T21 | DevOps + infrastructure owner + cost approver | NOT_RUN |
| T23 | Qualify below-20-ms latency and at least 1,064 RPS together | T02, T03, T16, T18, T19, T20, T21, T22 | Performance QA + independent reviewer | NOT_RUN |
| T24 | Prove failure safety, restart behavior and rollback under live load | T05, T06, T07, T16, T20, T23 | SRE + security QA | NOT_RUN |
| T25 | Collect migration comparisons and canary evidence before global cutover | T02, T13, T16, T17, T18, T24 | Security owner + backend release lead | NOT_RUN |
| T26 | Cut over all approved capabilities and prove Zero Haiku behavior | T17, T23, T24, T25 | Release lead + security owner | NOT_RUN |
| T27 | Remove obsolete engines, then narrowly scoped credentials and IAM | T26 | Backend + DevOps + security owner | NOT_RUN |
| T28 | Publish the reproducible release evidence and architecture handoff | T18, T19, T20, T23, T24, T27 | Independent verifier + release owner | NOT_RUN |

## 8. Detailed task cards

### T00 - Freeze the real environment and establish a baseline evidence bundle

**Owner:** Backend lead + DevOps + QA  
**Prerequisites:** None; read-only discovery  
**Source mapping:** S1 status/target; S2 completion rule; S7 deployment provenance  
**Files/areas:** Existing checkout, Compose files, image labels and frontend build; NEW environment-manifest.json and route-map.md.

**Implementation steps**

1. Run tools/collect_baseline.sh against the verified checkout and Docker context. Record dirty-file names; do not reset the worktree or assume an old branch is current.
2. Map every running service to role, image digest, image source revision, command, replica count, cgroup limits and mount/config provenance. Identify accidental source bind mounts. Confirm whether Jupyter and the serving container use the same Python environment; do not assume they do.
3. Record GPU UUID/type/driver, package/build versions in the actual inference container, model/tokenizer revisions and hashes, exact ONNX filename, precision/provider settings and cache key. Record the pasted 2.14 ms result as USER_REPORTED until reproduced.
4. Resolve actual browser hostnames, gateway/control API base URLs, auth method, tenant and audit lookup. Identify two isolated test tenants and authorized synthetic provider target. Record the last known safe rollback image and compatible policy/schema versions.
5. Assign D01-D12 owners. Create a task record and evidence directory. The collector is read-only except for writing this evidence; discovery is not authorization to mutate production.

**Verification: unit, live Docker, boundary and frontend**

1. Host/container: run the collector; compare checkout revision with gateway image OCI revision and frontend build. Missing OCI metadata requires an alternative reproducible build record, not an assumed match.
2. Frontend: open the actual test console, record build identifier and a sanitized network request URL/request ID. Match that ID to gateway/control logs. Do not export bearer tokens or session cookies.
3. Runtime: inspect GPU visibility inside the selected container and query package versions. Do not create an inference session on an active production process for discovery.
4. Review: a second engineer can locate the exact images, compose overrides and endpoints from the manifest without asking for an old IP address.

**Success:** Manifest is complete enough to reproduce the environment; all mismatches have explicit disposition; synthetic tenant/target authorization and rollback artifacts are recorded.

**Fail/stop:** Unknown serving revision, unauthorized target, untracked image patch, or impossible browser-to-backend correlation blocks progression.

**Rollback:** No application rollback needed. Keep evidence private; delete only accidental secret-bearing evidence under the incident process, not by hiding a failed test.

**Required evidence:** `T00/environment-manifest.json; collector outputs; route-map.md; sanitized browser correlation; baseline-unknowns.md.`

---

### T01 - Sign the SLO, action semantics, output mode and capability ledger

**Owner:** Product owner + security owner + backend/ML leads  
**Prerequisites:** T00  
**Source mapping:** S1 3-4; S2 G0/G3; S6 Haiku gap list  
**Files/areas:** Source: enforcement.py, output_guard.py, FirewallConfig serializers/UI; NEW acceptance-contract.json, capability-ledger.md and action-matrix.json.

**Implementation steps**

1. Inventory every Haiku consumer and every policy category from the current tree, not only scan_prompt_with_tier2. Separate injection, sensitive spans, routing, grounding, simulator and output taxonomy.
2. Map ALLOW/BLOCK/REDACT and existing MONITOR/FLAG terminology to actual API fields, stored enum values, UI labels and mode precedence. Record cases where final_action differs from recommended_action. Do not add new production enum values merely to match this runbook.
3. Choose D04/D05 output release mode and semantic backend; explicitly record capabilities with no replacement. An unsupported mandatory policy is a release blocker, not an automatic allow.
4. Approve D01-D12, including the proposed p99 <20 ms definition and >=1,064 qualified success RPS, the exact workload, resource budget, error limit and all numerical quality thresholds. Freeze signed JSON before candidate tuning.
5. Write an exception ledger for any source correction: live timing tolerance, old overlap/window math, monitor/flag semantics, streaming holdback, and in-process queue ownership. Unchanged source invariants remain binding.
6. Decide whether testing involves temporary paid old-engine calls. Final Zero Haiku excludes a permanent Haiku slow/audit fallback; the user generation model is not the firewall judge.

**Verification: unit, live Docker, boundary and frontend**

1. Code/contract: trace each A01-A24 row to a current field/path and a named test. Test two policy rules that conflict so precedence is explicit.
2. Frontend: save each supported mode on the real test console, reload, read control API persistence, and read the gateway policy version. Screenshot actual labels rather than assuming MONITOR/FLAG are independent buttons.
3. Review: numeric release limits are non-null; source-derived and proposed requirements are distinguished; unknown capabilities have owners and block status.

**Success:** Signed contract is versioned and hashed; no mandatory capability or action behavior remains implicitly undefined; no p50-only or scan-only substitution is possible.

**Fail/stop:** Undefined FLAG semantics, unchosen output guard, unsignable quality thresholds, or conflicting SLO/budget silently ignored -> BLOCKED.

**Rollback:** Do not alter production policies in this task. Restore test-tenant settings after collection; retain the signed decision and test mapping.

**Required evidence:** `T01/acceptance-contract.json; action-matrix.json; capability-ledger.md; signoffs; UI persistence evidence.`

---

### T02 - Build the labeled security, redaction and compatibility corpus

**Owner:** Security QA + ML engineer + independent reviewer  
**Prerequisites:** T00, T01  
**Source mapping:** S2 G0.1-G0.5; S6 1.2  
**Files/areas:** NEW tests/detection_corpus/, corpus_manifest.json, label_audit.md; source-reported scripts/detection/score_postures.py.

**Implementation steps**

1. Create at least 300 injection examples across at least eight families and at least 300 benign examples, retaining the source minimum. Include truly disjoint-vocabulary paraphrases and benign developer traffic: inline code, shell, SQL and tool descriptions.
2. Add separately labeled redaction fixtures with expected field paths/spans and authorized sanitized output, encoded/split PII (G33/G53), Unicode/markdown offsets, conflicting policies, data-free cases and redaction no-op failures. Use synthetic identifiers, never live customer secrets.
3. Include model limits, special-token boundaries, long-text middle attacks, multi-turn/tool/context inputs, supported languages, benign discussions of attacks and output chunk-split variants. Labels describe policy/trust context, not only text.
4. Split tuning/calibration from held-out evaluation before threshold selection. Deduplicate by normalized text and attack family/source, not just random IDs; keep a deliberate raw-text regression subset.
5. Two reviewers independently audit at least 10% and the disjoint-paraphrase group. Record adjudications. Expand held-out benign data when needed for the signed FPR confidence bound; 300 benign examples alone cannot substantiate a very small production FPR.
6. Run legacy T1, T1+policy and candidate T1+semantic under the same fixture semantics. Publish disagreement lists rather than using Haiku outputs as unquestionable ground truth.

**Verification: unit, live Docker, boundary and frontend**

1. Unit/data: schema lint, duplicate/leakage checks, source/license metadata, deterministic window coverage and expected policy outcomes.
2. Live Docker: replay a small held-out sample through the real staging gateway and synthetic upstream; confirm expected sensitive-span removal and block/no-call behavior.
3. Frontend: select representative corpus IDs in simulator/Scan Detail and confirm the operator sees the same action, findings and raw-versus-sanitized distinction without exposing raw canary secrets.
4. Statistical: report recall and FPR by family/language/request length with sample sizes and confidence intervals. Per-window FPR is not per-request FPR; do not assume independent window errors.

**Success:** Source minimum and label audit met; frozen held-out hash and numerical thresholds approved; every required policy capability has evaluation cases.

**Fail/stop:** Train/eval leakage, self-labeled benchmark-only wins, missing mandatory categories, incomplete span labels or insufficient evidence for claimed FPR -> FAIL/INCONCLUSIVE.

**Rollback:** Test-only addition; no runtime rollback. Never rewrite held-out labels just because the candidate model disagrees; adjudicate and version changes.

**Required evidence:** `T02/corpus_manifest.json; family reports; label audit; held-out hashes; raw verdicts; thresholds and disagreement list.`

---

### T03 - Repair timing, trace eligibility and frontend latency truth

**Owner:** Backend instrumentation engineer + QA + frontend engineer  
**Prerequisites:** T00, T01  
**Source mapping:** S2 G1.1-G1.6; S3 1.1  
**Files/areas:** Source: pipeline_trace.py, stream_orchestration.py, secure_streaming.py, scripts/perf/gateway_pipeline_bench.py; NEW timing tests and metric-contract adapter.

**Implementation steps**

1. Define one monotonic request epoch and capture the events in section 5.3. Keep network arrival/body readiness, provider intrinsic/wait, guard queue/compute, output holdback and total time distinguishable.
2. Replace silent nonnegative clamps with signed residuals and invalid-run markers. Implement exact synthetic clock checks and the signed live epsilon. Do not relabel provider TTFT as addon.
3. Emit per-stage ran/skip/degraded state and actual start/end intervals. Preserve nine canonical names while documenting actual order and pre-gate timings. Mark early blocked stages honestly.
4. Separate tax_eligible, gateway_capacity_eligible, real_provider_capacity_eligible and policy-correctness eligibility through an adapter to existing fields. Cached/short-circuited/stub paths have explicit flags.
5. Implement the delayed token-producer and middle-stream-delay tests. Verify producer intrinsic work is not expanded by guard backpressure. Include retries, cancellations, failed sends and partially emitted streams.
6. Update Scan Detail/Duration to show the correct backend metric, provider time separately, and skipped/degraded states. Retain compact traces/content references rather than embedding repeated sensitive payloads.

**Verification: unit, live Docker, boundary and frontend**

1. NEW unit tests: test_stream_addon_is_not_ttft.py; test_trace_reconciliation_signed.py; test_stage_ran_and_skip.py; test_stream_middle_holdback_visible.py; test_metric_eligibility.py.
2. Live Docker: run upstream TTFT variants 50/500/2,000 ms, fixed 5+7 ms guard delays, a 30 ms mid-stream hold, malformed SSE, cancellation and a slow consumer. Use raw captures and producer event records.
3. Frontend: open each matching request ID; compare backend values to UI rounding tolerance agreed in T01. A two-second provider delay must not become two-second firewall tax.
4. Negative control: intentionally omit a timestamp and fabricate a skipped scan; harness must reject the run and UI must not show nine successful stages.

**Success:** All accounting self-tests pass; elapsed/attribution boundaries documented; live residual within frozen tolerance; overhead visible without provider-time contamination.

**Fail/stop:** Old addon=TTFT behavior, clamped negative residual, hidden mid-stream delay, fabricated stage completion or UI/API divergence -> FAIL.

**Rollback:** Keep the last safe instrumentation build; block publication of performance numbers if reverting to an invalid instrument. Do not roll back into false claims.

**Required evidence:** `T03/timing-contract.md; JUnit; producer events; per-request traces; signed residual histograms; browser evidence; harness self-test report.`

---

### T04 - Create the immutable live Docker and test-upstream verification environment

**Owner:** DevOps + backend QA  
**Prerequisites:** T00, T01  
**Source mapping:** S2 completion/evidence rule; S8 scan-only diagnostics; S9 baked nginx configuration; X1/X2  
**Files/areas:** Source: actual Compose/deploy files, Dockerfiles and provider abstraction; NEW staging overlays and test-producer/recorder.

**Implementation steps**

1. Define a named isolated staging Compose project using images built from the candidate commit and lockfiles. Separate test data, credentials and ports from production. Use explicit service names discovered at T00.
2. Build images rather than patching running containers. Record base image digests, app revision, lockfile hashes, frontend asset hash, image IDs and registry digests where available. A source bind-mount development run is not a release-image proof.
3. Provide an authenticated staging-only OpenAI-compatible token producer/recorder with deterministic token schedule, input capture, request IDs, intrinsic token-ready time, send-blocked time and configurable safe output fixtures.
4. Make fault injection and producer-control endpoints unavailable in production builds/routes. Test configuration cannot select another tenant or arbitrary exfiltration target.
5. Expose only the intended application edge; no public Jupyter, GPU management or unauthenticated recorder. Keep sensitive synthetic fixture captures permission-restricted.
6. Design explicit scan-only authorization if needed for the simulator. Preserve the ordinary chat API defaults; do not turn omitted max_tokens into a hidden inference skip.

**Verification: unit, live Docker, boundary and frontend**

1. Docker: recreate staging from a clean checkout, verify actual images match build manifest, inspect GPU visibility in the container and verify no host notebook dependency is required.
2. Boundary: send one allow, redact and block case; recorder proves one sanitized call or zero calls as appropriate. A second build runs the same fixtures identically.
3. Frontend: load the baked production frontend against these live services (not a dev server), submit the three cases, reload Scan Detail and match request IDs.
4. Negative: attempt to reach staging fault endpoints from the production configuration and from the wrong tenant; both are denied/not installed.

**Success:** One documented command sequence rebuilds a known staging stack; browser, gateway, GPU and recorder evidence correspond to the same candidate images.

**Fail/stop:** Untracked docker cp/pip edits, stale SPA, implicit scan-only behavior, public fixture endpoints or mixed production data -> FAIL.

**Rollback:** Restore the previous staging image tuple and persisted test configuration. Do not run docker compose down -v on production or shared data.

**Required evidence:** `T04/build-manifest.json; compose-overlay hash; image verification; recorder schema; recreated-run comparison; browser route proof.`

---

### T05 - Close policy readiness, tenant isolation and refresh holes

**Owner:** Backend/security engineer  
**Prerequisites:** T02, T04  
**Source mapping:** S2 G2.1/G2.2/G2.8; S5 tenant-scoping constraints  
**Files/areas:** Source: PolicySync, ConfigSync, VectorPolicySync, main.py config readers, core/models.py; NEW partial-coldstart tests.

**Implementation steps**

1. Make readiness and accepted-bundle state per tenant. A zero/partial load cannot mark every tenant ready. Request gates deny or report not-ready when required policy is absent.
2. Replace source-reported default-organization inheritance with verified get_own_config behavior and explicitly approved safe defaults. Inventory every sibling call site.
3. Use versioned atomic policy swaps; pin a consistent snapshot for a request and preserve the signed handling of urgent kill-switch changes. Do not mix policies mid-request.
4. Add periodic reconcile after notification loss; measure freshness and last-good behavior separately from the stricter kill-switch bound. Sign the outage/availability consequence.
5. Scope queries, cache keys and audit lookups to the authenticated tenant. A superuser without an explicit authorized tenant scope does not inherit global data access on tenant dashboards.

**Verification: unit, live Docker, boundary and frontend**

1. NEW tests: test_policysync_partial_coldstart.py; test_config_no_default_inheritance.py; test_policy_epoch_consistency.py; cross-tenant lookup tests.
2. Live Docker: interrupt policy load 20 times; restart workers with two tenants having opposite policies; drop subscription connectivity; verify no silent allow and bounded recovery.
3. Frontend: tenant A changes a test rule; observe its version and behavior. Tenant B must neither see the change nor expose A request IDs/content. Reload and log in with no selected org to test scope failures.

**Success:** Zero cross-tenant canary exposure; no partial load reports universal readiness; all worker behavior matches an identified policy version and signed freshness bound.

**Fail/stop:** Any inherited foreign policy, stale version without visible degradation, or readiness green while mandatory policy is missing -> FAIL.

**Rollback:** Tenant isolation fixes are not reverted for convenience. Use traffic containment or last known secure build if availability suffers.

**Required evidence:** `T05/20-restart matrix; version timeline; per-worker traces; browser tenant-isolation evidence.`

---

### T06 - Implement bounded admission and accurate degraded-mode handling

**Owner:** Backend + DevOps  
**Prerequisites:** T01, T04, T05  
**Source mapping:** S2 G2.3/G2.4/G2.6; S1 fail-closed table  
**Files/areas:** Source: worker entrypoint, concurrency/admission code, output_guard.py, secure_streaming.py and breaker module.

**Implementation steps**

1. Budget in-flight requests, per-tenant queues, worker threads, file descriptors, resident model copies and GPU contexts from the real cgroup. Prove the selected worker/server exposes an effective concurrency control.
2. Set queue bounds and deadlines before increasing server/proxy timeouts. Preserve separate operational error and policy-block outcomes.
3. Scope breaker state by tenant and input/output direction as required. Define provider/global-health faults separately so isolation does not hide a truly global outage.
4. Make scan_degraded live on all streaming/non-streaming branches. Required unavailable semantic checks cannot be silently bypassed by masking known PII.
5. Implement cancellation and disconnect cleanup: cancel queued work, release leases safely, finalize telemetry once, and prevent a cancelled request from consuming unbounded GPU/worker resources.

**Verification: unit, live Docker, boundary and frontend**

1. Unit: deadline, queue-full, cancellation, lease settlement and breaker isolation tests.
2. Live Docker: 3x configured in-flight cap, stalled producer and temporarily unavailable GPU adapter; measure RSS/FD/queue recovery. Expected overload responses are not counted as target-rate success.
3. Frontend: errors show HTTP status/code and degraded state, not ALLOW. A fault in tenant A does not turn tenant B red or change its verdicts.

**Success:** Bound is effective; no OOM/resource leak; zero fail-open violations; overload recovery is bounded and observable; errors retain correct provenance.

**Fail/stop:** An inert worker-connections setting, unlimited queue, shared breaker cross-talk, leaked request work or success badge on missing scan -> FAIL.

**Rollback:** Restore safe limits or reduce admitted traffic. Never raise timeout/concurrency to conceal overload or restore an unsafe failure mode.

**Required evidence:** `T06/resource-budget.json; overload curves; cgroup/FD/GPU samples; failure traces; UI screenshots.`

---

### T07 - Remove synchronous logging and duplicated telemetry work

**Owner:** Backend + observability + frontend  
**Prerequisites:** T03, T04  
**Source mapping:** S2 G4.2/G4.4/G4.5; S3 repeated redact_all findings  
**Files/areas:** Source: redis_log_handler.py, main.py logging setup, telemetry builders, trace serializers; frontend Scan Detail reader.

**Implementation steps**

1. Detach request-thread/event-loop Redis PUBLISH; use a bounded asynchronous producer with retry/spill policy defined in T01. Restore appropriate non-debug production logging without losing critical security events.
2. Create one authoritative sanitized content reference and compact decision record; do not copy prompt/response into every stage. Retain compatibility for UI rehydration by authorized request ID.
3. Instrument redaction pass counts per content version. Meet source goals of <=2 redact_all passes/request for telemetry and terminal frame <4 KiB where compatible; new content introduced later still receives required scans.
4. Separate high-cardinality event data from fleet metrics. Do not label Prometheus metrics with request IDs, raw tenant names or content.
5. Test log-sink slowness/backpressure. Nonblocking enqueue is not durable storage; report enqueue, durable persistence, retry, spill and loss separately.

**Verification: unit, live Docker, boundary and frontend**

1. Unit: one writer, content-reference authorization, event schema compatibility, pass-count and terminal-size tests.
2. Live Docker: inject slow/unavailable audit sink and compare event-loop lag with valid T03 timing. Join expected decision IDs to persisted records after recovery; source audit completeness floor is >=0.999, with zero missing critical fixture records.
3. Frontend: Scan Detail works after page reload using the compact trace/reference; no secret appears in browser-visible stage metadata; temporarily unavailable detail is honest rather than fabricated.

**Success:** Zero synchronous log publishes on the request path; bounded producer; reduced duplicate work; correct action and audit correlation; browser remains functional.

**Fail/stop:** Dropped decisions without accounting, unbounded queue, raw content leaked, UI broken by compact trace or claimed durability at enqueue -> FAIL.

**Rollback:** Revert the schema through a version-compatible reader/writer pair, not by restoring raw secret copies. Retain safety logs and known secure redaction behavior.

**Required evidence:** `T07/event-size and pass-count report; publisher call audit; lag data; durable-ID reconciliation; Scan Detail reload proof.`

---

### T08 - Fix inline-code false blocks as a separately scored behavior change

**Owner:** Detection engineer + security reviewer  
**Prerequisites:** T02, T03, T04  
**Source mapping:** S2 G0.3/G0.5; S3 Tier-1 defects  
**Files/areas:** Source: scanner.py ATTACK_PATTERNS command_injection and explanatory carve-out; NEW regression fixtures.

**Implementation steps**

1. Reproduce the source-reported any-backtick-pair match in the current checkout. If already fixed, record the shipped revision and prove the fixture instead of changing it again.
2. Implement the approved narrowed behavior/demotion to signal without disabling unrelated command/transport checks.
3. Evaluate against full held-out injection and developer-traffic groups, and separately test attack text quoted for explanation.
4. Commit this behavior change separately from the tool-description correction and performance port. Update reason-code/UI compatibility only where necessary.

**Verification: unit, live Docker, boundary and frontend**

1. Unit and corpus: inline npm/shell/SQL examples no longer terminally block for backticks alone; signed attack recall floor holds.
2. Live Docker: send benign inline-code and malicious command contexts; verify provider-call behavior and final reason codes.
3. Frontend: Scan Detail does not retain stale command_injection BLOCK when backend now emits a signal; monitoring/flag display follows T01.

**Success:** Documented benign FP class improves; no unapproved attack-family regression; change is independently reviewed and reversible.

**Fail/stop:** Global disable of injection scanning, hidden threshold change, or labeling a changed detector as verdict-identical -> FAIL.

**Rollback:** Revert this isolated rule change if security quality fails; do not bundle with other fixes so the cause remains attributable.

**Required evidence:** `T08/before-after verdicts; FP/recall report; source diff; UI/action evidence.`

---

### T09 - Fix tool-description leakage over-match separately

**Owner:** Detection engineer + security reviewer  
**Prerequisites:** T02, T03, T04  
**Source mapping:** S2 G0.4/G0.5; S3 Tier-1 defects  
**Files/areas:** Source: scanner.py data_leakage patterns/carve-out; tool/context fixtures.

**Implementation steps**

1. Reproduce the source-reported ordinary-tool-description blocks; identify whether current code still has the same rule behavior.
2. Narrow or demote the offending generic pattern according to T01/G0 while retaining specific credential/exfiltration findings.
3. Test tool descriptions, tool arguments, returned data and actual exfiltration independently; do not treat tool metadata as inherently trusted.
4. Keep the patch, scores and release note separate from T08 and T10. Preserve field/trust boundaries and effective_prompt ordering.

**Verification: unit, live Docker, boundary and frontend**

1. Corpus: benign tool descriptions no longer block solely due to generic terminology; exfiltration and supported sensitive-data tests retain signed quality.
2. Live Docker: exercise chat tool definitions and MCP argument/result paths with synthetic destinations only; verify no unapproved provider/tool dispatch.
3. Frontend: correct category/reason/action shown for both benign tool metadata and actual blocking fixtures.

**Success:** Approved FP reduction and quality floors demonstrated separately; no silent relaxation of specific leakage controls.

**Fail/stop:** Deleting all leakage rules, exempting all tool content or hiding the result inside the native-port change -> FAIL.

**Rollback:** Revert the individual rule change; keep T08 independent. Deny unsafe traffic while a regression is resolved.

**Required evidence:** `T09/fixture report; tool-path captures; review and UI proof.`

---

### T10 - Land the bounded native Tier-1 rewrite with original-span correctness

**Owner:** Performance/backend engineer + security QA  
**Prerequisites:** T02, T03, T08, T09, T11  
**Source mapping:** S2 P2.1-P2.4; S3 1.5/11.2; S5 ordering constraints  
**Files/areas:** Source: scanner.py, patterns.py, segmentation/fuzzy code; NEW native matcher adapter and differential/property suites.

**Implementation steps**

1. Sign the changed-detector fork using G0 scores. The source measured verdict-identical Tier-1 as too slow; do not promise both unchanged defective behavior and the redesigned speed floor.
2. Inventory the actual pattern set; reconcile against the source count of 174. Compile reusable matcher databases outside the request path. Preserve necessary exact verification for prefilter matches.
3. Implement Unicode-aware relaxed-superset candidate matching and original-span verification. Normalization/decoding must retain a mapping to original fields/bytes; redaction cannot apply normalized offsets to raw UTF-8 blindly.
4. Replace per-cell difflib segmentation work with the approved native/dictionary-gated path. Treat RapidFuzz superset behavior as a property to verify for the actual algorithms/domains; finite earlier tests are not a universal mathematical proof.
5. Bound decode depth, expansion ratio, regex work and input length; retain encoded/split PII/secret tests and no-op-redaction denial. Avoid daemon-thread-per-pattern behavior.
6. Reuse a scan result only for the identical content version, tenant/policy/model context and trust boundary. New retrieved content or output is not a reusable input result.

**Verification: unit, live Docker, boundary and frontend**

1. Unit/property: full pattern compilation, Unicode thin spaces/confusables, original-byte spans, G33/G53, prefilter exact verification and adversarial expansion cases.
2. Corpus: differential output for semantics declared unchanged; separately scored behavior changes for T08/T09 and any approved new fork. No unapproved loss of sensitive-span coverage.
3. Live Docker: time actual unique short/512/1,024-token and long-cap text after T03, clean and dirty; inspect CPU profiles and bounded worker memory.
4. Frontend: masks, category counts and final action match captured sanitized provider payload; no broken multibyte rendering.

**Success:** All required patterns compile or have reviewed exact fallback; zero mandatory regression-fixture misses; signed quality achieved; actual Tier-1 latency recorded within its approved budget.

**Fail/stop:** ASCII-only silent bypass, unverified prefilter block, corrupted spans, unbounded decode or renamed target values presented as measurements -> FAIL.

**Rollback:** Keep a controlled old-engine comparator for tests and an approved safe runtime rollback; do not silently fall back to a slow engine while claiming the fast profile passed.

**Required evidence:** `T10/pattern manifest; property/differential results; full corpus scores; live profiles; spans and UI captures.`

---

### T11 - Reproduce PG2 fidelity, graph placement and L4 timing in the release container

**Owner:** ML/runtime engineer + QA  
**Prerequisites:** T02, T03, T04  
**Source mapping:** S2 G5.1-G5.4; S3 11.7; X3/X5  
**Files/areas:** Pinned model/tokenizer/ONNX artifacts; NEW scripts/perf/pg2_runtime_probe.py, runtime-manifest.json and parity tests.

**Implementation steps**

1. Pin a single checkpoint revision and tokenizer for reference and candidate. Verify label meaning from that revision. Preserve full file hashes and export metadata; model.quant.onnx and model.onnx are different candidates.
2. Reload a pristine FP32 reference from disk. Do not call model.float() on a model previously rounded to FP16 and call it the original reference. Feed identical token IDs/masks/optional fields to PyTorch FP32, reference ONNX, and TensorRT candidate.
3. Test clean/attack/synthetic-long/Unicode and held-out samples; record finite logits, scores, decisions, absolute/relative differences and threshold-crossing disagreements. Set numerical tolerances before evaluation; numerical parity and detection quality are separate gates.
4. Capture actual session providers, provider options and a separate ORT profiling/placement run. get_providers() is not proof every compute node used TensorRT. Review CPU residuals and forbid undocumented fallback. Turn profiling off for latency qualification.
5. Run valid window batches W=1,2,3,4,7 (plus supported larger batches), sequence 256/512 only, at least 50 warmups and 1,000 timed invocations/config. Report host-input-to-host-output and device-resident timings separately; include H2D/D2H boundaries and tokenization separately.
6. Separate cold model/session/engine build and cache reload from hot execution. Measure under sustained load; no assumed 4.1 ms equality. Keep source predicted-band check 2.7-4.0 separate from actual <=4.0 component ceiling; faster is not a performance failure.
7. Record current hardware cost/availability/approval as G5 evidence. Do not claim a current price or quota from the old plan. Initial candidate should be the demonstrated FP16 path; INT8 needs its own calibration and accuracy gate.

**Verification: unit, live Docker, boundary and frontend**

1. Live Docker: run the probe in the exact candidate container on the target L4, not only in Jupyter. Collect raw latencies for every supported shape and exact input metadata.
2. Correctness: canonical benign and injection tests plus the repeated-512-token case from the conversation. A benign result on the latter is not automatically an FP16 defect; compare pristine references.
3. Graph: profile proves the intended major subgraphs execute on GPU; cache artifacts tie to the recorded stack/GPU/shape settings.
4. Frontend: this task is a component benchmark, not a user SLO. Do not update production badges with the microbenchmark number; attach the report to the engineering evidence only.

**Success:** Repeatable candidate logits/decisions meet frozen tolerances and quality gates; real GPU placement known; valid shape matrix/raw samples and G5 decision published.

**Fail/stop:** Mismatched checkpoint/tokens, unsupported 1,024-token forward, silent model-file fallback, NaNs, unexplained decision regression or CPU run labeled TensorRT -> FAIL.

**Rollback:** Retain original artifacts; select last validated runtime candidate. No production cutover occurs here.

**Required evidence:** `T11/runtime-manifest.json; checkpoint hashes; parity.jsonl; placement profile; raw latency arrays; cold/hot reports; price/capacity decision.`

---

### T12 - Implement inference ownership, engine cache and readiness lifecycle

**Owner:** Runtime engineer + backend lead + DevOps  
**Prerequisites:** T01, T04, T11  
**Source mapping:** S1 2-3; S3 11.2; X3/X4  
**Files/areas:** Gateway startup/lifespan and worker config; model repository/cache volume; NEW runtime adapter and ownership ADR.

**Implementation steps**

1. Resolve D08 with an explicit process diagram: which process owns each GPU execution context, which requests share each queue, how multiple gateway workers reach it, and how much IPC/RPC is introduced.
2. For the source in-process target, embed the selected runtime through a supported application binding. Initialize GPU state after process creation/fork, not in a gunicorn preload parent. Tune process/model-instance count from measured resources, not one engine per CPU core by default.
3. If choosing a node-local service instead, mark it as an architecture deviation and measure the added call. Do not describe localhost HTTP/gRPC as C API. Reconcile shared-queue claims with actual ownership.
4. Key engine caches by model/export/tokenizer-relevant input contract, GPU/runtime version, precision and profiles. Use a persistent controlled cache, startup validation and file-lock/atomic creation to prevent competing builders.
5. Warm every advertised profile before readiness. Missing/corrupt/incompatible cache must leave the replica unready while rebuilding or fail startup; never compile on a ready customer request.
6. Expose readiness and diagnostic status without secrets. Bound input/output queues, model instance concurrency and cancellation behavior. GPU-locality controls must prevent an accidental cross-node hop.

**Verification: unit, live Docker, boundary and frontend**

1. Unit/integration: lifecycle failure, cache key invalidation, concurrent starts and cancellation tests.
2. Live Docker: cold start, cache hit restart, corrupt cache and changed precision/profile; inspect actual PIDs/contexts/VRAM and ready transitions. Repeat container restart at least five times.
3. Concurrent request proof: scheduled calls share the queue claimed by the ADR; no per-worker engine explosion or unmeasured hidden remote call.
4. Frontend: readiness/degraded indicators correspond to runtime state; unavailable replicas do not produce false benign findings.

**Success:** Process/queue topology matches the selected design; ready means validated hot profiles; no request-time compilation, hidden network hop or uncontrolled model duplication.

**Fail/stop:** GPU initialized before unsafe fork, cache reused across incompatible artifacts, ready before warmup, or shared-queue claim contradicted by processes -> FAIL.

**Rollback:** Roll back adapter/image and compatible cache namespace; never reuse corrupt or incompatible cache to restore availability.

**Required evidence:** `T12/ownership-adr.md; process/queue diagram; cache manifest; restart timelines; GPU-memory/process evidence; readiness tests.`

---

### T13 - Wire local findings into the existing enforcement authority

**Owner:** Backend/security engineer  
**Prerequisites:** T01, T02, T05, T06, T10, T12  
**Source mapping:** S1 3-4; S2 T5/T9; S6 recommended_action mapping  
**Files/areas:** Source: scan_prompt_with_tier2, enforcement.py, main.py, PipelineDecision; NEW local adapter and end-to-end action tests.

**Implementation steps**

1. Replace the selected Tier-2 invocation behind a versioned runtime switch, preserving the scanner API and early T1/policy BLOCK semantics. Inventory all callers; do not route the always-allow service scaffold.
2. Translate local classifier scores into the existing finding vocabulary with detector/version/window provenance. Use frozen validated thresholds; do not equate argmax BENIGN with unconditional allow.
3. Pass findings through resolve_and_enforce exactly once with tenant config. Preserve original-versus-effective payload semantics and single-writer final response/audit behavior.
4. Implement signed MONITOR and FLAG mappings; flag/recommended_action cannot obscure actual transport action. A monitorable injection rule does not bypass independent mandatory authentication or redaction.
5. Validate the selected runtime switch per tenant in staging. No customer provider dispatch occurs before every required input decision is complete.
6. Add T5-equivalent regression gates before any production cutover: terminal block/no dispatch, degraded required scanner behavior and tenant-scoped enforcement authority.

**Verification: unit, live Docker, boundary and frontend**

1. NEW tests: test_local_tier2_adapter.py, test_pipeline_enforcement_authority.py compatibility, test_local_tier2_unavailable.py, test_action_mode_matrix.py.
2. Live Docker: A01-A11/A15-A17/A20 with real classifier; recorder verifies actual call count and bytes; active rule version and detector version match.
3. Frontend: create/save/reload policies for supported actions; invoke simulator; compare final action, recommendation, flags, HTTP outcome, Scan Detail and durable audit for the same request ID.
4. Negative: point local adapter at missing artifacts/invalid scores; no benign default. Attempt policy block with routing enabled; no provider call.

**Success:** Signed action matrix passes end to end; no bypass, duplicate terminal writer or raw-data dispatch; correct mapping survives a restart.

**Fail/stop:** New enum silently breaks clients, recommendation mistaken for final action, missing scan maps to ALLOW, or provider called after BLOCK -> FAIL.

**Rollback:** Revert tenant runtime switch to a still-authorized validated legacy control during migration; after final Haiku removal use the last validated local build or deny, not a hidden Haiku fallback.

**Required evidence:** `T13/action matrix results; upstream recorder; JUnit; request-ID joins; frontend evidence; T5-equivalent fixture report.`

---

### T14 - Reduce shared-state waits without changing gate order or quota truth

**Owner:** Backend/distributed systems engineer  
**Prerequisites:** T03, T05, T06  
**Source mapping:** S2 P2.5/P2.6; S5 4.0c-4.4; S1 Valkey contract  
**Files/areas:** Source: rate limiter, kill_switch.py, config/policy cache, Redis pool, main.py.

**Implementation steps**

1. Profile actual Redis calls, time waiting for a connection, network time, script execution and event-loop lag. Do not attribute every delay to Redis server RTT.
2. Pipeline only independent state fetches. Evaluate policy and terminal gates in existing order; preserve producer/consumer dependencies and single writer.
3. Use the approved tenant-scoped EVALSHA/quota-lease contract. Make the first-load/NOSCRIPT case safe; distinguish idempotent fetch retries from ambiguous already-charged quota mutations.
4. Bound connection-pool wait and retries within the request budget. A pool that queues without a timeout is not an improvement. Shared TPM/global budgets cannot become N independent per-worker allowances.
5. Cache only with tenant/policy/version correctness and bounded staleness. Revalidate urgent model/kill-switch state at the approved boundary. Notification loss needs reconcile, not indefinite last-good use.
6. Record expected steady-state request-path shared-state count and exception counts. Authentication misses, reconciliation and startup loads are not silently omitted from overall capacity planning.

**Verification: unit, live Docker, boundary and frontend**

1. Unit: atomic quota, lease settlement/refund, duplicate retries, NOSCRIPT and cancellation; ordering and stale-snapshot tests.
2. Live Docker: at least two gateway processes sharing one cache; two tenants with distinct budgets; force pool exhaustion, reload scripts and interrupt notifications. Verify no multiplied budget or stale allow.
3. Frontend: rate-limit and kill-switch settings persist and take effect within the signed bound; 429 is distinguished from operational 503 and policy 403.

**Success:** The approved steady-state shared-state path is achieved; quota semantics and freshness remain correct; measured queue waits stay bounded.

**Fail/stop:** Per-worker quota multiplication, infinite pool waits, unbounded retry or stale kill-switch resurrection -> FAIL.

**Rollback:** Restore the previous safe shared-state implementation and reduce load; never fall open on required TPM/kill-switch uncertainty.

**Required evidence:** `T14/Redis call trace; Lua/version hashes; multi-worker quota tests; freshness timeline; UI status verification.`

---

### T15 - Make redaction a verified single transformation per content version

**Owner:** Backend/data protection engineer + QA  
**Prerequisites:** T01, T02, T10, T13  
**Source mapping:** S2 G4.4; S5 policy producer ordering; S6 byte verification  
**Files/areas:** Source: policy mutation, redact_all helpers, scanner span handling, output_guard.py; NEW original-span redaction tests.

**Implementation steps**

1. Create a content-version map tying parsed message/tool/result fields to original spans and transformed values. Preserve original metadata under restricted handling while ensuring only effective content can be dispatched.
2. Merge overlapping compatible spans deterministically and apply transformations once per affected field/version. Do not call redact_all from each trace serializer.
3. Validate required removal using original-span identity and retained detectors; markers must not trigger recursive masks or suppress unrelated findings. A no-op mandatory scrub denies.
4. Handle JSON escaping, Unicode codepoints/UTF-8 byte offsets, repeated values, mixed case, encoded/split variants and tool schemas. A tool argument with no safe allowed replacement is rejected, not guessed.
5. Ensure errors, audit, logs, UI and fallback routes never serialize the original synthetic secret after a redaction decision. Newly generated or retrieved content still gets its own scan.
6. Keep detection evidence distinct from re-identification secrets; this task does not authorize reversible token-vault behavior unless separately specified.

**Verification: unit, live Docker, boundary and frontend**

1. Unit/property: expected sanitized payload snapshots, overlapping entities, Unicode, no-op/partial redaction and idempotence.
2. Live Docker: recorder scans the actual outbound JSON and any fallback attempt for raw canaries; inject malformed replacements and verify deny.
3. Frontend: REDACT badge, displayed sanitized text and backend bytes agree; Scan Detail reload and error views never reveal raw canaries. Monitor injection mode does not undo mandatory masking.

**Success:** Every mandatory synthetic redaction fixture is transformed correctly at the network boundary; no unchanged secret, broken payload or duplicate transformation.

**Fail/stop:** Redact badge without byte change, destructive offset bug, invalid JSON forwarded, raw canary in telemetry or bypass via retry -> FAIL.

**Rollback:** Revert the isolated transformation implementation; deny inputs requiring a transformation that cannot be performed safely.

**Required evidence:** `T15/span fixtures; before/after hashes with private canaries; outbound recorder checks; UI/error/log secret scan.`

---

### T16 - Close output guard coverage and streaming semantics

**Owner:** Backend streaming engineer + ML/security owner  
**Prerequisites:** T01, T02, T03, T06, T12, T15  
**Source mapping:** S1 classifier at DONE; S2 G2.4/G2.5; S6 output taxonomy gaps  
**Files/areas:** Source: OUTPUT_GUARD.inspect, enforce_output, secure_streaming.py, stream_orchestration.py; NEW output-policy backend and wire tests as needed.

**Implementation steps**

1. Implement D04/D05 exactly: choose the supported output model/checks and map their taxonomy to actual policy needs. Do not use PG2 injection labels as generic harm/grounding coverage without a validated task model.
2. For strict full-response mode, hold content until the final required verdict; bound buffer bytes and include the holdback in the user-visible contract. For pre-release chunk mode, define lookbehind/overlap and what it can and cannot prevent.
3. If using completion-time observational classification, label it observational and do not count it as preventive whole-response BLOCK. Mandatory strict policies cannot be silently downgraded to this mode.
4. Implement stateful chunk redaction across SSE/UTF-8/JSON boundaries, finish/abort cleanup and complete-output coverage. Do not head-tail-drop the middle; enforce explicit resource/window caps.
5. On detector exceptions/deadline expiry, apply signed fail-closed behavior and truthful degraded flags. After SSE headers, return a protocol-correct terminal error/close, not a fictitious HTTP status change.
6. Count output model calls/windows in GPU capacity. If output model or holdback pushes p99 above 20 ms, fail that profile and revise implementation/resources or obtain an explicit changed contract; do not exclude it after the fact.

**Verification: unit, live Docker, boundary and frontend**

1. Unit: model-specific output fixtures, chunk splitter property tests, strict/observational mode contracts, disconnect and malformed SSE.
2. Live Docker: controlled producer places canaries at every chunk boundary and harmful fixtures at start/middle/end; capture all content bytes at the client edge and recorder timestamps.
3. Fault: fail the output classifier before content, mid-stream and at finalization; strict mode releases zero prohibited content, while any observational mode reports its limitation.
4. Frontend: BLOCK/REDACT/FLAG and partial-delivery labels agree with the wire; raw output never flashes briefly before a UI mask. Buffering/release delay is visible in evidence.

**Success:** Required output capabilities are genuinely implemented; strict and streaming semantics are byte-proven; failures never masquerade as successful scans; latency profile includes the real work.

**Fail/stop:** Unchosen output detector, skip-under-load, already-emitted data called withheld, blind middle, or hidden holdback -> FAIL/BLOCKED.

**Rollback:** Restore last validated output guard; stop/withhold affected streams if safe processing is unavailable. Never rollback to silent output fail-open.

**Required evidence:** `T16/output capability ledger; raw synthetic SSE captures; client timestamps; model/window counters; quality report; UI partial-delivery proof.`

---

### T17 - Remove remaining semantic dependency gaps on MCP and RAG

**Owner:** MCP/RAG engineer + security QA  
**Prerequisites:** T01, T02, T05, T13, T16  
**Source mapping:** S3 D18 groups A/B/C; S1 MCP path; S2 RAG open miss  
**Files/areas:** Source: mcp_scan_orchestrator.py, llm_judge.py, rag_pipeline/query_stage.py, bedrock_embedder.py, grounding_guard.py and simulator/catalog consumers.

**Implementation steps**

1. Re-audit actual call graph; the older assertion that MCP inherits input cutover with no files changed is a hypothesis to verify, not a completion shortcut.
2. Preserve per-org sandbox, RBAC, destination allowlist/SSRF checks and scanning of new arguments/results. Tenant-configured optional semantics keep their signed enable/disable behavior; required checks cannot disappear.
3. Replace platform Bedrock embedding/grounding dependencies with a selected local or otherwise approved Haiku-free implementation. An embedding similarity score alone is not an automatic guarantee of grounding parity; test the exact claimed behavior.
4. Preserve embedding dimension/schema compatibility for existing tenant data and any explicit tenant-provider contracts. Do not replace customer BYOK embedding services under the label of removing platform Bedrock.
5. Audit simulator, reserved model aliases, scheduled jobs and error/fallback paths. Zero Haiku means no hidden operational backdoor call after cutover.
6. Benchmark these surfaces separately; chat's 1,064 RPS token-band gate does not automatically cover arbitrary retrieval/tool/grounding work.

**Verification: unit, live Docker, boundary and frontend**

1. Unit/integration: test_mcp_tier2_strict_unavailable.py compatibility; two-org sandbox tests; retrieved-document and grounding cases; embedding dimension fixtures.
2. Live Docker: fake authorized MCP tool/RAG store with synthetic new malicious/sensitive content; capture both argument and result boundaries, including errors.
3. Frontend: MCP/RAG settings and Scan Detail show actual detector execution and scope; missing capabilities do not appear green or complete.

**Success:** Every platform-owned Haiku/Bedrock consumer has a tested replacement or explicitly removed/disabled feature; no mandatory semantic hole or tenant-contract violation remains.

**Fail/stop:** RAG judge simply deleted, silent embedding dimension/provider change, cross-org tool execution, or chat-only proof used for all product surfaces -> FAIL.

**Rollback:** Keep the validated capability path during migration; if no safe Haiku-free replacement exists, block the global removal task rather than silently weakening that feature.

**Required evidence:** `T17/callgraph ledger; MCP/RAG boundary tests; dimension compatibility; UI coverage; zero-call checks for each surface.`

---

### T18 - Make action controls, simulator and Scan Detail verifiable in the real frontend

**Owner:** Frontend engineer + browser QA  
**Prerequisites:** T01, T03, T04, T13, T15, T16  
**Source mapping:** S2 G1.4/G4.2; S8 Tasks 1-6; S7 routing UI; X6/X7  
**Files/areas:** Source: AttackSimulatorPanel.jsx, liveGateway.js, policy/routing forms and Scan Detail; NEW frontend/e2e/live-enforcement specs and stable locator contract.

**Implementation steps**

1. Discover actual routes and accessible labels using the live baked frontend. Add stable data-testid contracts only where necessary; record them instead of inventing selectors in this runbook.
2. Make the action UI distinguish final action, recommendation, mode, flag, skipped stage, degraded scan and operational error. Persist changes through the real control API and verify gateway policy version.
3. Correct simulator requested-versus-sent count/concurrency display and completion/in-flight state. Respect approved load ceilings; do not silently clamp or raise rates when every request invokes a paid provider.
4. Make scan-only a real explicit authorized server contract, not runInference=false in a browser object that the gateway ignores. Label scan-only results and exclude them from full-pipeline claims.
5. Render status/code/message for failures: 403 policy, 429 quota, 503 degraded/overload, transport failures and partial streams must remain distinct. Avoid success banners when infrastructure errors dominated the run.
6. Make Scan Detail work from compact trace/content references after reload with tenant authorization. Keep authentication/browser storage and traces private; redact secrets before sharing evidence.

**Verification: unit, live Docker, boundary and frontend**

1. Run a real Playwright suite against the built frontend and real Docker gateway/control/GPU. Do not mock browser routes for acceptance tests.
2. Exercise ALLOW, BLOCK, REDACT, each signed MONITOR/FLAG case, saved policy reload, injection fail-closed, unavailable service and strict output failure. Correlate all with recorder/audit/request ID.
3. Verify batch requested/sent/completed counts against gateway arrivals, plus real in-flight meter and completion order. Small browser smoke loads only; backend loadgen proves fleet rate.
4. Use Playwright traces/screenshots/network evidence for these functional runs; keep heavy browser tracing off the performance load generators.

**Success:** Operator-visible results match actual API/bytes/audit; settings survive reload and apply at gateway; no fake scan-only or hidden burst clamp.

**Fail/stop:** Mocked E2E success, stale frontend build, misleading FLAG/BLOCK semantics, raw-canary exposure, or simulator billing despite skip claim -> FAIL.

**Rollback:** Deploy last compatible frontend with the backend schema; preserve server-side security behavior. A UI rollback does not justify disabling enforcement.

**Required evidence:** `T18/browser traces; screenshots; sanitized network records; request-ID reconciliation; locator contract and frontend build hash.`

---

### T19 - Prevent console analytics from destabilizing the serving fleet

**Owner:** Control/backend engineer + frontend engineer  
**Prerequisites:** T04, T05, T07  
**Source mapping:** S5 3.1/3.4; T-C/T-F tests in verification plan  
**Files/areas:** Source: policy/security_views.py, dashboard queries, fetchWithAuth, useBackendHealth.js, overview/module data hooks, control entrypoint.

**Implementation steps**

1. Push aggregate analytics into bounded SQL Count/CASE/GROUP BY operations; avoid materializing unbounded raw rows/metadata in Python. Verify actual query plan and supported time-window input; reject invalid periods explicitly.
2. Bound analytics query time and thread/concurrency pools using the real control-container budget. Worker recycling is a safety mechanism, not a substitute for the query fix.
3. Pause hidden-tab polling; debounce and coalesce event-triggered refetch; abort superseded client requests and cap concurrent heavy analytics requests at the source target of two.
4. Keep request IDs and error surfaces faithful: return actual failed endpoint information, not a fixed banner that omits an error. Avoid stale responses overwriting a new filter view.
5. Recheck tenant scoping for every aggregate/detail query. An aborted browser fetch does not by itself guarantee the server query was cancelled; query timeouts still apply.
6. Run this track under gateway load if control/CPU share GPU nodes as the cost matrix proposes. Co-location must be measured, not justified solely by projected idle CPU.

**Verification: unit, live Docker, boundary and frontend**

1. Unit/query: aggregate oracle parity, missing-org denial, bounded periods and no full metadata projection.
2. Live Docker: four concurrent heavy dashboard calls on a populated synthetic dataset; worker survives, memory stays inside signed budget, query errors are bounded and meaningful.
3. Frontend: hidden tab for ten minutes produces zero analytics polling; event burst produces bounded refetch; lens changes abort obsolete requests; at most two heavy requests active.
4. Combined: while gateway runs a qualified load step, refresh dashboard and process policy/audit activity; measure gateway tail and control memory impact.

**Success:** No OOM or cross-tenant results; bounded query/client behavior; source T-F tests pass; co-location does not invalidate the measured gateway profile.

**Fail/stop:** Python row materialization, .iterator() assumed to solve disabled server-side cursors, unlimited event-trigger refetch, foreign data or gateway tail regression -> FAIL.

**Rollback:** Retain tenant-isolation fixes; restore compatible bounded queries/UI or reduce analytics traffic. Do not restore unbounded queries to hide an error banner.

**Required evidence:** `T19/query plans; memory/time reports; T-F browser proof; tenant canary results; concurrent gateway latency evidence.`

---

### T20 - Verify the real edge, SSE delivery and baked security configuration

**Owner:** DevOps/edge engineer + QA  
**Prerequisites:** T04, T16, T18  
**Source mapping:** S1 dual edges; S2 P4.3; S9 nginx headers/host isolation  
**Files/areas:** Source: deploy/Dockerfile.nginx, nginx.conf, nginx-ssl.conf, security-headers.inc, actual LB/backend settings.

**Implementation steps**

1. Resolve the deployed edge chain from T00. Final GCP ALB and old AWS NLB instructions are not interchangeable. Keep console/control routes separate from chat/SDK routes.
2. Bake header/host-isolation changes in images and validate nginx config before authorized recreation. Preserve forwarded-proto behavior; do not create an origin HTTPS redirect loop behind TLS termination.
3. Verify actual connection reuse, SSE proxy buffering, gzip negotiation and flush behavior. The HLD HTTP/2 label is an intent: do not claim HTTP/2 on every internal hop without verifying the selected proxy/server versions.
4. Run a stream longer than 350 seconds on the controlled producer only after T06 admission bounds. Validate the relevant LB/proxy/app idle/timeouts and safe connection drain.
5. Resolve D11: measure bytes and release delay with the approved coalescing setting. If it changes the source cost model, update cost evidence before declaring the budget pass.
6. Unknown hosts must not serve the console. Preserve named-host health checks and required security headers on success/error/static/API responses without exposing version details.

**Verification: unit, live Docker, boundary and frontend**

1. Live Docker: nginx -t and HTTP header tests on baked image; inspect appropriate named/default vhosts through actual staging edge.
2. Wire: gzip on/off SSE parsed incrementally; compare producer arrival and client release events; no full-response accidental buffering. Long stream completes with correct terminal event.
3. Frontend: login/dashboard/Scan Detail work through the approved host; no redirect loop, mixed-content failure, or stale cached SPA; unknown-host test is rejected.
4. Fault: drain/recreate a gateway replica; existing stream behavior matches signed policy and new traffic avoids an unready instance.

**Success:** Real edge behaves as documented; streams/headers/host isolation pass; buffering and egress are measured; no application bypass route.

**Fail/stop:** Only origin tested while public edge buffers, unknown Host serves SPA, insecure redirect fix, hidden 120 ms buffer or long stream severed unexpectedly -> FAIL.

**Rollback:** Restore known-safe baked proxy image and LB settings through approved change process; do not disable auth/host isolation to recover health checks.

**Required evidence:** `T20/nginx validation; sanitized header matrix; SSE wire/timing data; long-stream proof; route and version manifest.`

---

### T21 - Measure one serving unit and choose queue/batch settings from data

**Owner:** Performance engineer + ML runtime engineer  
**Prerequisites:** T03, T10, T12, T13, T14, T15, T16, T20  
**Source mapping:** S3 11.7; S2 G3/G5; S4 CPU/GPU budget  
**Files/areas:** NEW or extended scripts/perf/gateway_pipeline_bench.py, request-level recorder and per-node report; actual release Docker services.

**Implementation steps**

1. Implement the harness interface in section 9; make it fail closed on missing metrics/artifacts and record no-op/stub detector fingerprints. Test its own percentile, failure and eligibility logic before load.
2. Baseline idle and low-rate live pipeline with all required guards. Separate tokenization, Tier-1, GPU queue/execution, output checks, serialization, audit enqueue and edge time.
3. Sweep W/shape/batch/concurrent-execution settings; distinguish windows within one request from microbatching independent requests. Account for any max-batch-4 split of a seven-window request and all queue waits.
4. Sweep controlled arrival rate (for example 25,50,100,200,300 requests/s per serving unit, then data-led increments). Do not send a fleet-rate load to a single L4 and interpret overload as a model defect.
5. Choose the highest repeatable node rate that satisfies the approved p99, error, quality and resource constraints. Use raw windows/s and successful requests/s, not 1/p50 as throughput.
6. Measure with concurrent output work and the agreed dashboard/control workload, because they share resources in the costed design. Record engine/host thermal state, retries, thread counts and CPU affinity.
7. Profile in a separate run; disable heavy per-node profiling during final measurement. Keep enough lightweight events to verify eligibility and latency boundaries.

**Verification: unit, live Docker, boundary and frontend**

1. Harness self-test: dropped schedules, missing scan, expected block vs error, percentile known arrays, timestamp deletion, cancellation and incomplete SSE all classify correctly.
2. Live Docker: repeated rate sweeps, warm restarts and changed shape ordering; all-permitted and mixed-policy profiles reported separately.
3. Frontend: simultaneously run small policy/simulator/Scan Detail smoke cases; correlate IDs, versions and actions while backend load is active.
4. Review: required model calls/windows per request match observed counters; no browser or provider stub is called the customer generation fleet.

**Success:** A reproducible q_safe per node/GPU and matching p99/error/resource profile exist; every selected setting has measured evidence and a rollback value.

**Fail/stop:** Rates derived from medians, silent response truncation, missing output work, overloaded loadgen, unchecked dynamic engine builds or inflated block-heavy throughput -> FAIL.

**Rollback:** Restore prior validated batch/queue/thread settings; lower test rate automatically on safety/resource abort. Never reduce semantic coverage to recover a green chart.

**Required evidence:** `T21/per-node curves; raw requests/histograms; exact queue/batch settings; model-call counters; harness self-tests; concurrent UI proof.`

---

### T22 - Verify fleet topology, capacity procurement and cost envelope

**Owner:** DevOps + infrastructure owner + cost approver  
**Prerequisites:** T00, T01, T21  
**Source mapping:** S1 target HLD; S4 final cost model; S2 G5/P4  
**Files/areas:** Actual IaC/instance templates, image rollout settings, security groups/firewalls, GKE scheduling or approved alternative; NEW topology-and-cost.json.

**Implementation steps**

1. Verify actual availability, quota, instance types, zone placement, driver/container compatibility and network paths. Historical source quota and prices are not current facts.
2. For the reference target, verify four G2 nodes and eight L4s, actual GPU assignment and scheduling locality. For AWS deployment, write an approved equivalent topology/cost decision instead of reusing GCP prices.
3. Size active capacity from measured q_safe and real input/output window work, plus separately declared reserve. Four nodes over three zones often distribute 2/1/1; worst-zone loss can remove half the GPUs.
4. Do not claim full 1,064 RPS during node/AZ loss unless the surviving fleet is separately tested at that rate. Safety-under-failure and availability-under-failure are distinct acceptance rows.
5. Verify control/tenant-data/audit placement, shared-state HA, secret handling, private GPU access, health checks and rollout surge resources. Prevent unready/cold engines from receiving work.
6. Recompute egress from measured wire bytes and chosen coalescing/gzip; include prompt-to-provider leg, audit, snapshots, backups, IPs, data disks, load generation, AWS/GCP overlap and required support. Keep provider tokens separate but visible.
7. Obtain approval before any purchase/resize. If 1,064+ requires an extra node or different output detector, budget and quotas change; no silent $5k promise.

**Verification: unit, live Docker, boundary and frontend**

1. Read-only infrastructure inventory matches intended IaC; proposed apply plan reviewed separately. Image/GPU/node affinity verified on actual pods/containers.
2. Live warm staging/fleet: one request per replica/zone checks policy/model identity and actual locality; disconnect local inference route to detect accidental remote fallback.
3. Frontend: control plane remains reachable through intended edge; saved policy propagates to every ready replica.
4. Cost: second reviewer recomputes bill from measured workload and dated vendor inputs; flags discrepancies against S4 rather than concealing them.

**Success:** Provisioned, authorized topology is capable of the measured target with declared headroom/failure mode; current cost and quota evidence signed.

**Fail/stop:** Capacity unallocated, CPUs counted as GPUs, AWS billed at GCP CUD prices, understated output/egress, or asserted AZ survival without reserve proof -> FAIL/BLOCKED.

**Rollback:** No cloud mutation in this document. Any deployment rollback follows approved IaC/image plan and preserves data; scale-down only after draining and explicit approval.

**Required evidence:** `T22/topology-and-cost.json; current quota/capacity evidence; zone allocation; approved IaC diff; locality proof; pricing workbook/report.`

---

### T23 - Qualify below-20-ms latency and at least 1,064 RPS together

**Owner:** Performance QA + independent reviewer  
**Prerequisites:** T02, T03, T16, T18, T19, T20, T21, T22  
**Source mapping:** S2 G3/P4; S3 11.7; S10 F/G/C separation; X8  
**Files/areas:** Frozen live release fleet, separate in-region load generators and controlled producer; NEW merged release report.

**Implementation steps**

1. Freeze candidate images/config/model/tokenizer/corpus/policy hashes and the signed acceptance contract. Verify no change during a trial and all replicas ready/warm.
2. Prove the generator and producer can exceed the target independently. Allocate sufficient client concurrency for actual provider duration, not merely the 20 ms firewall budget.
3. Run fleet arrival-rate steps (for example 100,266,532,800,1,064,1,066) with abort criteria; enter the three 30-minute qualification trials only if safe. These are proposed initial levels, not measured capacity.
4. At the target plateau require >=1,064 successful fully evaluated completions/s in the all-permitted profile, p99 approved addon <20 ms, <=0.1% errors, zero critical safety violations and zero schedule drops. Mixed-action decisions are a separate report.
5. Run worst-band and representative-distribution profiles, 70/30 stream/JSON where approved, supported language strata and actual output controls. Include failed/timed-out/cancelled cohort members in failure and deadline accounting.
6. Merge raw data/histograms across all nodes before calculating tails. Preserve trial-level failures; do not pool them until one failing run disappears.
7. Run the two-hour soak, small live frontend regression during load, and authorized real-BYOK integration separately. Claim real-provider throughput only at the observed supported rate.
8. Above-target step sweep establishes the plus ceiling. Stop at first repeatable SLO miss and report highest qualified rate, not the peak one-second burst.

**Verification: unit, live Docker, boundary and frontend**

1. Independent reviewer recomputes counts/percentiles from immutable raw evidence and verifies all model-run/eligibility joins.
2. Frontend: real baked UI shows correct actions and timings for canary IDs during load; policy updates remain bounded and tenant isolated.
3. Negative control: run a deliberately disabled output guard or fake always-allow adapter in isolated staging; qualification must fail even if latency and HTTP 200 rate improve.
4. No security detection, redaction, comparison threshold or workload weighting changed after viewing the results.

**Success:** Every required trial and stratum passes both safety and performance; floor >=1,064 and p99 <20 ms achieved together; plus ceiling and provider qualification stated precisely.

**Fail/stop:** Any required trial/profile fails, erate >0.1%, schedule drops, missing guard, raw leakage, invalid timing or unknown output coverage -> FAIL/INCONCLUSIVE, never conditional PASS.

**Rollback:** Restore last qualified settings/image and lower capacity advertised. Do not lower scanner coverage, hide long inputs, count early blocks or relax thresholds to preserve the claim.

**Required evidence:** `T23/trial manifests; raw JSONL/histograms; recomputation command; plateau series; soak data; UI smoke; signed F/G/C result labels.`

---

### T24 - Prove failure safety, restart behavior and rollback under live load

**Owner:** SRE + security QA  
**Prerequisites:** T05, T06, T07, T16, T20, T23  
**Source mapping:** S2 G2; S5 failover/correctness tests; source output/tenant constraints  
**Files/areas:** Isolated staging/fleet fault orchestration and rollback image tuple; NEW fault-matrix report.

**Implementation steps**

1. Approve a staging-only fault plan and recovery operator. Bound load and fault duration; never kill shared production state from a generic command.
2. Inject GPU unavailability, corrupt cache, worker restart, policy sync interruption, Valkey disconnect/failover, full audit buffer, blocked upstream and slow client one at a time.
3. Check required-control failure closes safely and is not mislabeled as a malicious detection. Ensure independent tenants/directions retain their signed behavior.
4. Measure queue/FD/RSS/VRAM recovery and readiness gating; no resumed request uses an unverified or wrong-version engine.
5. Rehearse rollback with actual prior image digests/config/schema compatibility and stream drain. No source patching inside running containers.
6. Test node loss and, only where approved, zone-equivalent capacity loss. Report safe shedding separately from rate availability; do not claim target capacity through failure without passing that distinct gate.

**Verification: unit, live Docker, boundary and frontend**

1. For each fault, collect request-ID/byte/audit proof before/during/after, plus restore timestamps and resource trends.
2. Frontend: unavailable/degraded/partial-stream labels match backend; UI remains tenant scoped; no stale success or misleading all-green stages.
3. Rollback drill: redeploy known-safe image tuple in staging, verify T18 smoke matrix, runtime hash and no unexpected legacy-provider call.
4. All critical fixtures maintain zero prohibited provider dispatch/content leakage, even when throughput drops.

**Success:** Every declared fault has safe behavior and bounded recovery; rollback actually works; no unsafe policy/default/legacy fallback surfaces.

**Fail/stop:** Fail-open, wrong-tenant state, unbounded backlog, cold-ready model, irrecoverable image/schema mismatch or unavailable fault recovery -> FAIL.

**Rollback:** Abort load; isolate affected tenants/replicas; restore last known secure deployment. Never revert tenant isolation or enable silent bypass for availability.

**Required evidence:** `T24/fault-matrix.json; abort/recovery logs; security boundary captures; resource curves; rollback drill and frontend proof.`

---

### T25 - Collect migration comparisons and canary evidence before global cutover

**Owner:** Security owner + backend release lead  
**Prerequisites:** T02, T13, T16, T17, T18, T24  
**Source mapping:** S2 T1-T5/T9/T11; S6 comparison limitations  
**Files/areas:** Versioned per-tenant migration switch, comparison evaluator, policy audit and canary dashboard.

**Implementation steps**

1. Use the source requirement of at least 100,000 paired verdicts over at least seven days of authorized real traffic before removal. Approve data handling and any temporary Haiku costs; no new external call on sensitive content without authorization.
2. Compare identical content/policy context and keep legacy/candidate outputs plus human-adjudicated disagreements. Paired disagreement is not automatically a candidate error or legacy ground truth.
3. Run T5-equivalent fail-closed fixtures while the old engine is still present, as required by source ordering. Preserve a functioning rollback during this phase.
4. Canary approved tenant cohorts at proposed 1%,5%,25%,50%,100% rollout checkpoints, advancing only on evidence and product/security approval. Avoid randomized per-request semantics for one tenant without a declared policy.
5. Track quality, unexpected 403/429/503, output leakage, fallback calls, audit completeness, mode behavior, p99 and supported workload rate at each stage.
6. If real-traffic comparison is not authorized/available, mark source requirement unmet and obtain an explicit replacement evaluation decision; do not quietly label synthetic replay as seven days of live evidence.

**Verification: unit, live Docker, boundary and frontend**

1. Live Docker/fleet: current tenant engine version is observable; dual-run comparison cannot cause double provider dispatch or duplicate authoritative audit.
2. Frontend: canary tenant sees persisted settings, correct runtime tag and no changed semantics without sign-off; control plane cannot expose cross-tenant comparison records.
3. Security review: all critical mismatches resolved; signed family/language/redaction floors hold; 100k/seven-day requirement verified from timestamps and unique IDs.

**Success:** T5 equivalence and signed quality gates pass; required paired evidence exists; canary approvals recorded; no hidden permanent Haiku dependency.

**Fail/stop:** Skipped source gate, silent cohort downgrade, unresolved critical mismatch, false real-traffic evidence or legacy calls not tracked -> BLOCKED.

**Rollback:** Revert cohort to the still-authorized validated legacy path during migration if safe; otherwise contain traffic. This is temporary, not the final Zero Haiku architecture.

**Required evidence:** `T25/paired verdict archive; seven-day counts; adjudications; canary approvals; runtime switch/audit/UI correlation.`

---

### T26 - Cut over all approved capabilities and prove Zero Haiku behavior

**Owner:** Release lead + security owner  
**Prerequisites:** T17, T23, T24, T25  
**Source mapping:** S2 T9-T11; S3 D18  
**Files/areas:** Gateway/control/MCP/RAG/simulator callers, deployment configuration and runtime dependency audit.

**Implementation steps**

1. Promote the approved local enforcement path to all in-scope tenant cohorts without changing their mandatory policy meanings. Keep unsupported capabilities blocked from release rather than silently disabling them.
2. Inventory all platform Haiku/Bedrock inference entry points again, including scheduled tasks, output flushes, error handlers, simulator and fallback paths. Distinguish customer-selected model calls from platform guard calls.
3. Set the agreed full-cutover observation interval; proposed minimum seven days at 100% is an added operational gate, separate from source paired-comparison history.
4. Instrument outbound call boundaries and dependency counters; zero calls cannot be proved merely by absence of logs. Use a controlled blocked egress/denied test principal in staging to exercise every in-scope path.
5. Freeze a validated local-only rollback image. Before removing legacy code, prove reverting need not resurrect a Haiku dependency.
6. Rerun representative F/G qualification if canary image/config differs from T23. Browser and API behavior must remain consistent.

**Verification: unit, live Docker, boundary and frontend**

1. Run all A01-A24 applicable cases and MCP/RAG/simulator regression through live containers with platform Haiku unavailable; no accidental external fallback.
2. Frontend: same operator action flow works at 100% local runtime; relevant failure messages no longer suggest missing Bedrock credentials are a customer problem.
3. Audit: dependency inventory, egress records and boundary counters agree; no inference call from scheduled/background paths during observation.

**Success:** All required capabilities operate safely on the local-only path; observation period and zero-platform-call evidence complete; local rollback rehearsed.

**Fail/stop:** Any hidden Haiku call, unsupported mandatory feature, safety/quality regression or incomplete observation -> FAIL/BLOCKED.

**Rollback:** Roll back to validated local-only build or contain traffic. Do not revive Haiku after asserting global removal without an explicit change decision.

**Required evidence:** `T26/zero-call ledger; full observation evidence; denial-test traces; local rollback; qualification delta; frontend regression.`

---

### T27 - Remove obsolete engines, then narrowly scoped credentials and IAM

**Owner:** Backend + DevOps + security owner  
**Prerequisites:** T26  
**Source mapping:** S2 T11 before T16; S3 D18 inventory  
**Files/areas:** Source: bedrock client/logger/scanner/breaker modules, env vars, IaC roles/endpoints; preserve unrelated S3 boto3 consumers.

**Implementation steps**

1. Remove obsolete platform guard engines/callers/configuration and update tests/documentation. Use the discovered inventory, not an assumed fixed count of environment variables.
2. Preserve libraries needed by unrelated valid services; source explicitly notes boto3 may still be used for S3. Rename remaining breaker modules/tests to their actual non-Bedrock function where appropriate.
3. Build and deploy immutable local-only images. Run tests with no Bedrock runtime credentials accessible to the relevant process; verify artifacts and dependencies are complete.
4. Only after caller removal is proved, prepare a narrowly scoped IAM/endpoint/secret cleanup plan. Inventory shared principals before changes; do not revoke customer BYOK access or unrelated AWS permissions.
5. Execute cleanup only through approved infrastructure change control. Rotate exposed/retired secrets as required and record owners/rollback limitations.
6. Update SBOM, manifests, operational runbooks and monitor alerts. Finish with a full image/hash-to-runtime check and functional browser smoke.

**Verification: unit, live Docker, boundary and frontend**

1. Static: no reachable platform guard call site remains; accepted historical docs/tests are separated from live code in the scan.
2. Live Docker: no Bedrock credentials; all product surfaces and failure paths pass; preserved S3 and other dependencies still work.
3. Frontend: no orphaned settings or deceptive Haiku-backed status; action controls, routing and detail pages remain correct.
4. Infra: least-privilege diff reviewed, cleanup evidence saved, no permission outage on unrelated components.

**Success:** Code callers removed before permissions; local-only runtime functional; no residual credential dependency; unrelated services unaffected.

**Fail/stop:** IAM revoked first, hidden AccessDenied storm, deleting required S3 SDK usage or old frontend settings silently changing behavior -> FAIL.

**Rollback:** Use the approved local-only rollback image. Permission restoration is a separate authorized security decision, not an automatic application rollback.

**Required evidence:** `T27/static/runtime dependency report; image/SBOM; IAM diff/approval; zero-call proof; preserved-service tests; UI smoke.`

---

### T28 - Publish the reproducible release evidence and architecture handoff

**Owner:** Independent verifier + release owner  
**Prerequisites:** T18, T19, T20, T23, T24, T27  
**Source mapping:** S2 completion/provenance guards; S1 diagram/caveat sections  
**Files/areas:** NEW release-evidence.json, task-records/, operational runbook, final Eraser sources and approved workspace export.

**Implementation steps**

1. Run tools/validate_task_record.py against every task record; enforce that PASS requires evidence references, reviewer, timestamps and actual test commands. This metadata validator does not replace semantic review.
2. Have an independent engineer reproduce the selected staging build and one complete qualification trial from manifests without unpublished setup steps. Compare images, policies, artifact hashes and outcomes.
3. Publish observed p50/p95/p99, successful rate, error/deadline rate, raw sample location, work profile, actual output policy, hardware, cost assumptions and F/G/C labels. Keep projected or historical numbers visibly separate.
4. Update Eraser diagram from the accepted runtime ownership and output-mode decisions. Source diagram remains preserved; mark changes explicitly. Export/version it without putting credentials, private IPs or raw tenant examples in public workspaces.
5. Document operational ceilings, queue limits, policy freshness, failure behavior, scaling triggers, rollout/rollback procedures and evidence retention. Record which profiles are not qualified.
6. Do not mark release achieved until T23 and security gates are genuinely green. If the target is missed, publish the measured gap and next bottleneck instead of a success slogan.

**Verification: unit, live Docker, boundary and frontend**

1. Independent rebuild/live Docker + functional frontend replay of core cases.
2. Raw-data recomputation of qualification results; metadata schema check; trace/artifact provenance reconciliation.
3. Source-to-final diagram review: no omitted output guard, no invented in-process link, no historical 100k fleet claim, and MONITOR/FLAG behavior matches approved API.

**Success:** Complete signed reproducibility bundle, verified live frontend/Docker evidence and honest measured release claim with explicit exclusions.

**Fail/stop:** Missing raw data, unrepeatable environment, manual container patches, untested rollback or performance claim exceeding qualified profiles -> FAIL.

**Rollback:** Withhold publication and keep the last qualified release/capacity limit. Preserve failed results as engineering evidence.

**Required evidence:** `T28/release-evidence.json; reviewer sign-off; replay log; architecture source/export; operations handoff.`

---

## 9. Live Docker verification recipe

This section supplies runnable discovery commands and **proposed interfaces for test tools that T03/T04/T18 must implement**. Do not treat a command naming a NEW tool as evidence that it exists in your checkout. T00 records actual service names, Compose files, repo layout and deploy command. Do not substitute an old IP or silently run against production.

### 9.1 Test environments and levels of proof

| Level | What runs | What it proves | What it cannot prove |
|---|---|---|---|
| Unit/data tests | Pure functions, simulated clocks, test doubles where appropriate | Mapping, ordering, schemas, transformations and timing algebra | Live driver/container behavior or full throughput |
| Integration Docker | Real gateway image, real policy/cache services, real model runtime; controlled producer | Wiring, enforcement, actual model execution and byte-boundary behavior | Customer provider capacity |
| Live frontend E2E | Served production frontend build against those running APIs; no response mocks | Settings persist, real requests execute, UI reflects actual outcomes | A screenshot alone does not prove provider-byte safety |
| F/G qualification | Same release images, distributed arrival-rate driver, controlled token-emitting producer, full guards | Intrinsic firewall tax and gateway evaluation capacity for that profile | Real-BYOK completed chats |
| C live provider | Authorized real provider plus full guards | Actual completion behavior at that provider's tested quota | Capacity beyond the tested provider/workload |
| Failure/rollout | Authorized staging faults, canary cohorts and rehearsed rollback | Safety under failure and operational recovery | Full-rate AZ-loss availability without spare capacity |

### 9.2 Discover the actual Docker project before running commands

Run the packaged collector first. Then identify the relevant project from its service labels. On the authorized host, these are read-only:

```bash
docker context show
docker compose version
docker ps --no-trunc --format '{{.ID}} {{.Names}} {{.Image}} {{.Label "com.docker.compose.project"}} {{.Label "com.docker.compose.service"}}'
```

**Do not print `docker inspect` wholesale or `env`.** Both can expose credentials. Use selected fields, as the collector does. A Compose project may contain services unrelated to this experiment; do not stop them.

After review, populate the exact ordered file list. This is a template, not a claim about your deployment:

```bash
cd "$REPO_ROOT"
PROJECT=approved_staging_project
COMPOSE_FILES=(-f /absolute/path/to/verified/base-compose.yml \
               -f /absolute/path/to/approved/staging-overlay.yml)
DC=(docker compose --project-name "$PROJECT" "${COMPOSE_FILES[@]}")

"${DC[@]}" config --quiet
"${DC[@]}" config --services
"${DC[@]}" config --images
```

Rendered Compose configuration can contain substituted secrets. If needed for diagnosis, keep it mode 600 in restricted evidence and publish a redacted version plus a private hash only. A file existing on the host does not prove it was used to launch the containers; reconcile Compose labels, image identity and actual configuration. [X2]

### 9.3 Build immutable staging images

T00 must identify the actual build/deploy procedure. Where the repository uses local Compose builds, this is the intended sequence **only after the operator approves the staging change**:

```bash
# Names below must be filled from `config --services`, not copied literally.
GATEWAY_SERVICE=verified_gateway_service
FRONTEND_SERVICE=verified_frontend_service
CONTROL_SERVICE=verified_control_service

"${DC[@]}" build "$GATEWAY_SERVICE" "$FRONTEND_SERVICE" "$CONTROL_SERVICE"
"${DC[@]}" up -d --no-deps "$GATEWAY_SERVICE" "$FRONTEND_SERVICE" "$CONTROL_SERVICE"
"${DC[@]}" ps
```

For registry-based deployment, use the repository's verified build/push/pull flow with immutable digests instead. Do not replace it with the local example. Record the actual command and exit code. Do not use `down -v`, reset a shared database, or pull `latest` as a substitute for a versioned build.

Capture each selected container's image ID and app revision:

```bash
GATEWAY_CID=$("${DC[@]}" ps -q "$GATEWAY_SERVICE")
test -n "$GATEWAY_CID"
docker inspect --format '{{.Id}} {{.Image}} {{.State.Running}} {{.State.OOMKilled}} {{.RestartCount}}' "$GATEWAY_CID"
IMAGE_ID=$(docker inspect --format '{{.Image}}' "$GATEWAY_CID")
docker image inspect --format '{{.Id}} {{json .RepoDigests}} {{index .Config.Labels "org.opencontainers.image.revision"}}' "$IMAGE_ID"
```

If multiple IDs are returned, inspect each replica explicitly; do not pass a newline-joined list as one ID. Missing revision labels are a T04 task, not a reason to infer the revision from the checkout. Verify the served frontend asset hash through the actual console edge. [X2]

### 9.4 Verify GPU access inside the actual inference owner

The owner may be the gateway container for embedded ORT/Triton or a separately approved runtime container. Record which it is. A host-level `nvidia-smi` is not proof of container access.

```bash
INFERENCE_CID=verified_inference_container_id
docker exec "$INFERENCE_CID" nvidia-smi --query-gpu=index,name,uuid,driver_version,memory.total --format=csv
```

For an ORT owner, discover its Python executable and then run:

```bash
PYTHON_IN_CONTAINER=/absolute/path/to/verified/python
docker exec "$INFERENCE_CID" "$PYTHON_IN_CONTAINER" -c 'import sys, onnxruntime as o; print(sys.executable); print(o.__version__); print(o.get_available_providers())'
```

If the owner is a native C-API host without Python, use its build manifest and runtime diagnostics instead. Do not install Python or packages into the running release container to make this command work.

A minimal **NEW staging overlay example**, to be adapted in T04, is:

```yaml
services:
  verified_inference_service:
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              device_ids: ["0"]
              capabilities: [gpu]
```

Use either `device_ids` or `count`, not both. This only grants access; it does not establish CUDA/TensorRT compatibility, GPU exclusivity, process ownership, NUMA placement or correct graph execution. Validate those separately. [X1]

### 9.5 Runtime placement and model reproducibility

T11 records the ONNX file's SHA256, tokenizer revision, model revision, precision, engine-cache identity, input tensor types, actual shapes, active mask totals, configured providers and profiling evidence. `get_providers()` establishes priority/configuration, not node placement. Use ORT profiling or TensorRT engine inspection on a **separate diagnostic run**, with profiler overhead excluded from the timed qualification run. [X3, X5]

Required comparisons use identical token IDs and masks across the reference model and export. Reload the original FP32 checkpoint for the reference; `.half().float()` does not reconstruct the original FP32 weights. Compare logits, probabilities and policy decisions over the held-out corpus, not only two easy prompts.

Benchmark batch/window counts 1, 2, 3, 4 and 7 at the model's supported sequence sizes. A 1,024-token request is split according to D06; it is not a `[1,1024]` tensor fed to a 512-token PG2. Profile 256 and 512 sequence lengths independently. Use at least 50 warmup and 1,000 measured invocations for each isolated configuration, then a sustained-load run. T11 may increase those counts, but must not cherry-pick a favorable run.

Record both `meets_latency_ceiling` and `matches_historical_predicted_band`. A result faster than 2.7 ms can fail the latter without failing a 4.0 ms ceiling. Neither field proves the complete <20 ms target. H2D/D2H-included calls and device-resident calls are different profiles. [S3: 11.7; X3]

Engine cache keys and distribution must account for the compatible model/runtime/GPU/profile combination. A cache hit is not evidence that an incompatible engine is safe. Keep initialization and rebuild out of readiness-approved traffic. [X3]

### 9.6 Unit tests and live-container tests are different runs

Use the repository's actual test entry point after checking its lockfiles and scripts. Do not run an unpinned tool downloaded by `npx` or `pip` during qualification. Examples below illustrate invocation patterns, not a claim that NEW tests already exist:

```bash
# Test image: built from the candidate source with pinned test dependencies.
# TEST_SERVICE and test paths must be recorded after implementation.
"${DC[@]}" run --rm --no-deps "$TEST_SERVICE" \
  python -m pytest --collect-only -q path/to/implemented/tests

"${DC[@]}" run --rm --no-deps "$TEST_SERVICE" \
  python -m pytest -q path/to/implemented/tests \
  --junitxml=/evidence/unit-results.xml
```

The test image may contain pytest while the release image does not. The **application under live test must still be the release image**. Zero collected tests, unexpectedly skipped security fixtures, xfail hiding a required behavior, or tests that connect only to mocks do not pass the live gate. Mount `/evidence` only into the authorized test-runner location.

### 9.7 One real protocol request, with protected credentials

For synthetic staging requests, use the existing `/v1/chat/completions` route after verifying the correct gateway base URL. Save credentials in a local mode-600 curl configuration outside the repository, e.g. a single `header = "Authorization: Bearer ..."` entry. Never commit or attach that file. Do not enable shell tracing, curl verbose logs, or `--insecure` for the test.

```bash
# AUTH_CURL_CONFIG already exists privately; its contents are not printed.
test -f "$AUTH_CURL_CONFIG"
chmod 600 "$AUTH_CURL_CONFIG"

curl --config "$AUTH_CURL_CONFIG" \
  --silent --show-error --no-buffer \
  --connect-timeout 5 --max-time "$APPROVED_TEST_TIMEOUT_SECONDS" \
  --header 'Content-Type: application/json' \
  --header "X-Request-ID: $SYNTHETIC_REQUEST_ID" \
  --data-binary @"$SYNTHETIC_REQUEST_JSON" \
  --dump-header "$CASE_EVIDENCE/response.headers" \
  --output "$CASE_EVIDENCE/response.body" \
  --write-out '%{http_code}\n' \
  "$GATEWAY_BASE_URL/v1/chat/completions"
```

A known synthetic model/catalog entry must point at the controlled producer. Do not replace a production provider's URL. Header files can include sensitive session data depending on deployment; keep raw captures restricted and redact before sharing.

Do not use a generic `--fail` behavior to hide expected 403/429 bodies: the functional matrix needs the actual status and error code. Store the shell exit code separately from HTTP status. This curl recipe checks protocol and payload; **its wall time is not the firewall latency metric**.

For every action case, join four artifacts by request ID:

1. Client/API response and complete SSE events, if streaming.
2. Actual detector/decision execution record, including ran/skip/degraded reasons.
3. Controlled upstream's received payload/call count and produced content schedule.
4. Audit/Scan Detail record and frontend display.

The upstream recorder is staging-only, authenticated and restricted to synthetic fixtures. Redacted requests must be inspected at that boundary: a frontend badge cannot substitute for this proof.

### 9.8 NEW benchmark adapter interface to implement in T03/T04

The source names `scripts/perf/gateway_pipeline_bench.py`; its current flags were not inspected. **Do not paste the following invocation until the implementation exposes and documents this interface.** This is the required new test contract, not a statement of current CLI support:

```bash
python scripts/perf/gateway_pipeline_bench.py \
  --profile /evidence/signed/workload-profile.json \
  --contract /evidence/signed/acceptance-contract.json \
  --base-url "$GATEWAY_BASE_URL" \
  --credentials-file "$PRIVATE_BENCH_CREDENTIALS" \
  --arrival-rate 1066 \
  --warmup-seconds 300 \
  --duration-seconds 1800 \
  --run-id "$RUN_ID" \
  --out-dir "/evidence/$RUN_ID"
```

Implement `--help`, profile/contract validation, explicit refused/unsupported modes, nonzero exit on failed gates, and per-request evidence before relying on the command. The driver must consume SSE incrementally, follow split/coalesced frames, handle gzip correctly, record incomplete streams and retain every started request in the sample cohort. A driver that waits for a fully buffered response cannot prove chunk-release safety.

Arrival-rate testing must record scheduled starts, actual starts, dispatch lag and dropped schedules. A fixed closed-loop concurrency run is useful for exploration but not sufficient to prove a sustained specified arrival rate. k6's arrival-rate concepts are a reference; do not assume a standard buffered HTTP script can perform this package's SSE timing attribution. [X8]

Run an exploration sweep first: source-equivalent profiles at 25%, 50%, 75%, 100% of the intended node/fleet rate, then the signed qualification profile. Keep config fixed during each trial. T23 defines the three independent plateaus, soak, failure accounting and mixed-policy run. A single L4 notebook is not a four-node fleet test.

### 9.9 Source-reported regression tests to locate, not assume

Before renaming/removing integrations, locate these source-reported tests with the repository test collector. Record moved paths rather than quietly omitting them:

```text
test_stream_addon_is_not_ttft.py                 NEW/source-planned G1 test
test_policysync_partial_coldstart.py             NEW/source-planned G2 test
test_config_no_default_inheritance.py            NEW/source-planned G2 test
test_adv50_tier2_truncated_failclosed.py          source-reported existing test
test_pipeline_degraded_failclosed.py             source-reported existing test
test_pipeline_enforcement_authority.py          source-reported existing test
test_mcp_tier2_strict_unavailable.py             source-reported existing test
test_bedrock_tier2_breaker.py                    source-reported rename candidate
test_pipeline_block_shortcircuit.py             source-reported historical test
```

Renaming a file is not replacement validation. Its assertions must exercise the new adapter and still enforce the same approved boundary. T5's original "all three" fixtures are not fully enumerated in the tracker; recover their exact definitions from the real repository or specify/sign equivalent explicit cases before calling that source gate complete. [S2: P3; S10]

---

## 10. Frontend verification recipe

### 10.1 Required setup

Use the served release build, the real staging control API and gateway, and at least two isolated synthetic tenants. Discover current UI routes and accessible labels from the actual application. Record selectors in `T18/ui-selector-map.md`; the uploaded plans do not establish every current route/test ID.

For Playwright, use the repository's pinned local binary/test script and installed browser version. Run with a restricted authentication storage state outside the repo. Do not attach unredacted storage-state files, cookies or HAR authorization headers. [X6, X7]

Example **after NEW T18 tests are implemented and collected**:

```bash
cd "$VERIFIED_FRONTEND_ROOT"
./node_modules/.bin/playwright --version
./node_modules/.bin/playwright test path/to/implemented/live-tests \
  --project="$PINNED_BROWSER_PROJECT" --workers=1 --trace=retain-on-failure
```

The selected test configuration must consume the verified staging URL and secret storage path through the existing, audited mechanism. Browser mock routes/fixture API responses are forbidden for this live suite. Traces and video are for functional proof; they add overhead and must not be mixed into performance qualification numbers. Browser RTT includes client/network time; Scan Detail must label the backend firewall metric separately.

### 10.2 Live operator journey

| Step | Operator/test action | Backend and wire proof | UI success | Hard failure |
|---|---|---|---|---|
| UI01 | Log in to synthetic tenant A | API resolves tenant A, no global fallback | Tenant identity visible and correct | Tenant B records or policy visible |
| UI02 | Load approved policy, change one supported rule and save | Control persistence and gateway policy version advance | Reload shows persisted value; propagation state honest | Toast says saved while gateway uses old policy indefinitely |
| UI03 | Run benign allow fixture | Required detector ran, one dispatch, one final decision | ALLOW, correct request ID and version | Only mock/UI-local response or fabricated scan |
| UI04 | Run enforced injection fixture | Zero upstream dispatch; final policy BLOCK | BLOCK and reason, later stages skipped appropriately | Provider saw the blocked text |
| UI05 | Run supported PII redact fixture | Recorded upstream bytes contain approved placeholder, not original | REDACT and safe evidence; field matches | Raw value appears in provider payload/log/unauthorized details |
| UI06 | Run redact plus independent block | Transformation order preserved and no dispatch | One authoritative BLOCK with relevant findings | Competing writers, double event or allow |
| UI07 | Switch monitorable rule to MONITOR and replay | Detector still executes; actual disposition matches D03 | Finding/recommended action distinct from actual action | Monitor hides a skipped/dead detector |
| UI08 | Exercise FLAG/review state | Stored semantics match signed D03, traffic outcome remains explicit | Flag shown alongside allowed/redacted/blocked disposition | FLAG ambiguous or silently overridden |
| UI09 | Toggle kill switch/model isolation | All workers meet freshness contract; no forbidden fallback | Disabled state and runtime result agree | Old worker continues or routing resurrects model |
| UI10 | Make a required scanner unavailable in authorized staging | No unsafe dispatch; timeout bounded; degraded status | Operational error/degraded, not "safe" | UI says ALLOW with no scan |
| UI11 | Run controlled blocked output | Raw SSE capture satisfies selected release mode | Strict block or late flag/truncation accurately named | Content released then badge claims fully withheld |
| UI12 | Open Scan Detail after compact-trace change | Authenticated tenant-scoped rehydration by reference | All required fields render without fat inline payload | 404, cross-tenant reference or leaked raw data |
| UI13 | Use Burst Test at declared count/concurrency | Actual sent/request counts observed, no hidden clamp | Requested/sent/in-flight accurate; statuses/codes visible | UI accepts 500/100 but silently sends 100/25 |
| UI14 | Use explicit scan-only mode | Zero generation calls, guard path preserved as contracted | Scan-only label; not counted as full pipeline | Omitted max_tokens silently changes ordinary chat behavior |
| UI15 | Repeat with tenant B and no-org role | No tenant A policy/findings/content returned | Correct isolation and empty/denied response | Any cross-tenant record |

The burst-plan numbers above describe a historical defect to test for, not an instruction to set today's caps to those values. An explicit scan-only contract is required; preserve ordinary chat behavior when `max_tokens` is omitted unless a separately approved versioned API change says otherwise. [S8]

### 10.3 Frontend/control stress while the gateway is loaded

Run a bounded live operator session during T23 rather than proving the UI only when the gateway is idle. Use a fixed synthetic data set with realistic history and declared row counts.

- Hidden tab for ten minutes: zero new analytics polls after transition/in-flight cleanup, as the source test requires. Explicitly record any allowed authentication/health traffic separately.
- Cold Overview: at most two heavy analytics requests concurrently; changing the time lens cancels obsolete client requests; handlers ignore stale responses.
- Simulated enforcement-event burst: bounded debounced refetch, no one-request-per-event amplification.
- Controlled slow endpoint: timeout/error shown before the proxy's timeout; error names/statuses reflect actual failures.
- Four concurrent 30-day KPI requests: SQL aggregation remains bounded; no full metadata materialization; memory ceiling established from the actual cgroup, not an old machine's size.
- Unsupported period: explicit validation response, not a silent substitution that makes a large-window test vacuous.
- Tenant/no-org controls stay enforced during pagination, aggregation and detail lookup.

[S5: 3, 3.4, T-C/T-F tests]

Cancellation in the browser does not prove server-side database work stopped. T19 must independently verify query deadlines and server resource release. Worker recycling is not the root fix for unbounded query materialization. Do not use `.iterator()` as an assumed memory bound where server-side cursors are disabled. [S5: 3]

### 10.4 What to attach per UI case

Attach case ID, build/tenant/policy identifiers, exact steps, backend request IDs, sanitized API/network excerpts, upstream-call evidence reference, screenshot or trace location, expected/actual result, reviewer, and cleanup. Include actual timestamps and method used to correlate records.

Screenshots alone do not pass. A frontend label must reconcile to the stored decision and actual provider boundary. Browser artifacts are sensitive: raw captures belong in restricted evidence; publish sanitized copies or opaque references only.

---

## 11. Reproducibility and evidence contract

### 11.1 Suggested evidence layout

```text
aimesh-evidence/<release-id>/
  signed/
    acceptance-contract.json
    workload-profile.json
    quality-thresholds.json
    source-conflicts-and-decisions.md
  identity/
    environment-manifest.json
    collection-status.tsv
    image-identity.jsonl
    build-input-hashes.json
  T00/ ... T28/
    task-record.json
    exact-commands.redacted.txt
    tests.xml
    actual-expected.json
    reviewer.md
  qualification/<trial-id>/
    input-profile.json
    per-request.jsonl
    latency-histograms.json
    raw-outcomes.jsonl
    resource-timeseries.jsonl
    upstream-intrinsic-events.jsonl
    summary.json
  frontend/
    selector-map.md
    sanitized-network/
    screenshots/
    restricted-artifact-references.json
  rollout/
    paired-verdict-manifest.json
    cohort-results.json
    rollback-rehearsal.md
  release-report.md
  SHA256SUMS
```

This package deliberately includes only empty/non-PASS examples. Use actual results to fill them. Never copy example measurements into real evidence.

### 11.2 Record what can change a result

Freeze and record: full Git commit and dirty state; app/base image digests; Python and package lockfiles; GPU/driver/CUDA/TensorRT/ORT versions; tokenizer/model/export/engine hashes; precision and optimization options; process count, GPU ownership and thread limits; input/window/special-token policy; actual output model and scan frequency; hardware/cgroups; co-located workloads; policy/tenant versions; rate-limit configuration; loadgen capacity; arrival distribution; request corpus/seed; response length and stream schedule; warmup; cache state; metrics/quantile method; failure/timeout/drain accounting; exact command line with secrets removed.

A seed alone is not reproducibility. Store the actual generated workload or a hash-verifiable corpus with deterministic generation rules. An image tag alone is not immutable identity. Do not silently rebuild between trials with floating dependencies.

### 11.3 Evidence-record validator

The included `tools/validate_task_record.py` is runnable now with Python 3.10+. It checks metadata completeness, file existence and hashes, a distinct reviewer, timestamps, actual command-result references, and absence of unresolved blockers for a declared PASS.

```bash
python3 "$PACK_ROOT/tools/validate_task_record.py" --self-test

python3 "$PACK_ROOT/tools/validate_task_record.py" \
  "$RELEASE_EVIDENCE/T03/task-record.json" \
  --evidence-root "$RELEASE_EVIDENCE"
```

Exit codes: `0` structurally complete declared PASS; `2` valid but NOT PASS; `1` invalid record. The task-record template exits `2` by design. The validator **does not judge** whether the uploaded evidence really proves latency, security, screenshots or detector execution. Independent review remains required. Do not use a metadata PASS as a product release PASS.

Evidence paths are relative to the evidence root and must not escape it. Hash the actual files and list every referenced command log/check artifact. Any missing evidence prevents PASS.

### 11.4 Minimum reproducibility tests

An independent engineer must be able to rebuild or pull the exact candidate, initialize only the approved synthetic fixtures, identify runtime ownership, run the functional action suite and repeat one qualification trial. Include every required command; unpublished terminal state, notebook variables and manually modified containers fail this test.

Recompute summaries from raw data. Check count conservation and percentile arithmetic. Compare at least one raw request across client, gateway, producer, audit and frontend. Verify a deliberately bad candidate fails: detector skipped, latency timestamp missing, redaction no-op or loadgen saturated. A harness that only confirms good runs is not validated.

Repeated model inputs may appear in warmup and calibration, but the unique-request qualification workload must document cache misses and content diversity. Empty strings, mostly padding and repeated `ping` are not substitutes for a real declared token band.

---

## 12. Release, rollback, and stop rules

### 12.1 Release scorecard

A release is qualified only when all mandatory fields below are evidenced:

| Gate | Required result |
|---|---|
| T00/T01 identity and contract | Signed, complete, no source/runtime confusion |
| Detection and action safety | Approved per-family/language quality limits; all hard invariants pass |
| Instrument | Delayed producer and injected guard delays pass; signed residuals valid |
| Local engine | Correct artifact, profile, fidelity and observed execution placement |
| Live Docker | Immutable release image, real services/model, no hidden host patches |
| Live frontend | Settings persist; actual action and boundary proof agree; no cross-tenant exposure |
| Output | Required model/capabilities and release mode explicitly chosen and tested |
| Performance F/G | Every required independent trial meets p99 <20 ms and >=1,064 qualified completions/s together, for the signed profile |
| Soak and failures | No safety violations, bounded queues/resources, measured degradation and rehearsed rollback |
| Haiku migration | Source paired-evidence gate and canary complete; no hidden platform judge calls |
| Dependency cleanup | Callers gone before scoped permissions/endpoints removed; unrelated S3 still works |
| Cost and availability | Actual quote/run-rate and failure capacity match the signed plan |
| Reproducibility | Independent repeat plus raw-data recomputation |

A failed mandatory output capability cannot be excluded after the run to rescue the headline. A stricter new profile is a new qualification. Increasing throughput above 1,064 is a measured ceiling sweep, not an assumption that raising a quota achieves it.

### 12.2 Rollout and reversal

Use a canary tenant/cohort and maintain one active dispatch authority. A shadow evaluator may receive an authorized copy for comparison, but must not independently call the customer LLM or mutate the request twice.

The source gate requires at least 100,000 paired verdicts over at least seven days of real traffic before the relevant cutover. Any paid old-engine comparison needs explicit authorization and data-handling approval. If real traffic is insufficient, the source gate remains blocked; simulated traffic is not silently equivalent. Final architecture has zero permanent Haiku judge/auditor/fallback calls. [S2: P3]

Proposed rollout steps are 1%, 5%, 25%, 50%, 100%, with task-specific gates and observation windows signed in advance. The additional proposed seven-day period after full cutover is separate from the source's paired-comparison period. Safety regressions stop progression immediately; do not wait for a weekly performance review.

Rollback means restoring the last **safe** image/config/model combination or containing traffic. It does not mean restoring cross-tenant defaults, bypassing failed scanners, lying about incomplete scans, or dropping evidence. Before IAM cleanup, prove a local safe rollback that does not need removed Bedrock permissions. After cleanup, do not deploy an image that still invokes them.

### 12.3 Stop-and-investigate triggers

Any raw sensitive payload crossing a prohibited boundary; provider call after terminal input block; cross-tenant policy/data leak; strict output released early; false scan-complete telemetry; unexpected model artifact; missing timing attribution; silently dropped audit; uncontrolled memory/queue growth; unapproved production target; incomplete source capability; quality thresholds missing; unsafe rollback; or errors excluded to boost rate all stop the release.

When a performance trial fails, retain the raw result and inspect the largest measured contribution: Tier-1/segmentation, tokenization, GPU queue/service, output frequency/holdback, Redis pools, logging, frontend/control interference, or transport. Change one declared component per comparison. Do not lower scan coverage or input length without publishing a different profile.

### 12.4 Evidence status of this delivered package

Only the generated files, JSON structure, local helper self-tests, task dependency graph and source syntax checks are validated here. **No user Docker host, browser session, repository test suite, GPU service, cloud resource, live provider or Eraser workspace was accessed or changed while producing this pack.** Task statuses remain NOT_RUN.

The diagrams are editable Eraser source. No connected Eraser write tool was available in this chat, so no new workspace was created and no live Eraser renderer acceptance is claimed. Open a private authorized workspace and paste the supplied diagram-as-code; review syntax/rendering and update the signed design decisions before publishing.

---

## 13. Source register and external implementation references

The source register below is generated from the uploaded files' actual bytes and identifies the version used for this runbook. Section references throughout are portable; the original documents are not bundled to avoid duplicating potentially sensitive source content. Keep them in the approved repository/workspace.

The PDFs/HTML and the cutover spreadsheet are not treated as overriding sources. The canonical Markdown architecture, tracker and corrected cost plan drive this task sequence. Historical prose can conflict internally; D01-D12 records where an explicit decision is needed instead of silently inventing one.

### 13.1 Uploaded source versions

| ID | Uploaded filename | Use in this pack | SHA256 prefix |
|---|---|---|---|
| S1 | `2026-09-07-end-to-end-architecture-hld.md` | Sections 2-7 final topology, request sequence, ownership; section 9 caveats. | `7781f8017e10e920` |
| S2 | `2026-08-27-task-tracker.md` | G0/G1 ordering; G2 security; G4 efficiency; P2 rewrite; P3 T5/T9/T11/T16 removal gates. | `ba9deda773f05726` |
| S3 | `2026-08-27-FINAL-evidence-based-hot-path-plan.md` | Section 11 GPU update; sections 1, 3, 4 diagnostics and retained safety; old section 12 cost superseded. | `1a9078e33416632a` |
| S4 | `2026-09-02-hot-path-cost-matrix.md` | Sections 1-4 corrected historical cost/latency; section 8 final quota/budget correction. Not a current quote. | `0c42f5085dba6128` |
| S5 | `2026-08-25-FINAL-verification-and-platform-agnostic-scale-plan.md` | Sections 3/3.4 frontend and analytics; corrected section 4 prefetch-not-fan-out; source test obligations. | `3d2ff3b67a1abda3` |
| S6 | `2026-08-22-haiku-parity-longprompt-edge.md` | Section 1 category gaps and action mapping; section 2 windows; older Haiku slow pool not retained in final Zero Haiku. | `930307f53ae56625` |
| S7 | `2026-08-18-deterministic-model-routing.md` | Deterministic select_model replacement, compliance floor and UI/persistence tests; historical implementation status. | `71e52c899e0fc39f` |
| S8 | `2026-08-14-burst-test-debug.md` | Live burst discrepancies, error diagnostics and scan-only verification. Old missing-max_tokens proposal needs current-contract approval. | `71868998fc156ee4` |
| S9 | `2026-08-17-nginx-security-headers.md` | Baked config, header inheritance, unknown-host isolation and ALB-origin redirect caveat. | `91e5cd7d4c34cb85` |
| S10 | `2026-08-22-MASTER-hot-path-capacity-architecture.md` | F/G/C distinction and diagnostic history; superseded conclusions not used as final fleet/price. | `bb1b665fd26b4b48` |

Full hashes and byte lengths: `templates/source-register.json`. Original sources remain unchanged. No current cloud state or live repository is inferred from an old source status.

### 13.2 Official implementation references

Checked 2026-09-10. These references support tool semantics, not the internal performance/coverage claims. Pin and verify the versions used in your own deployment.

- **X1: Docker Compose GPU reservations.** https://docs.docker.com/compose/how-tos/gpu-support/
- **X2: Docker Compose config and build.** https://docs.docker.com/reference/cli/docker/compose/config/
- **X2b: Docker Compose build.** https://docs.docker.com/reference/cli/docker/compose/build/
- **X3: ONNX Runtime TensorRT EP / cache / options.** https://onnxruntime.ai/docs/execution-providers/TensorRT-ExecutionProvider.html
- **X4: Triton in-process C API.** https://docs.nvidia.com/deeplearning/triton-inference-server/user-guide/docs/client_guide/in_process.html
- **X5: ONNX Runtime profiling.** https://onnxruntime.ai/docs/performance/tune-performance/profiling-tools.html
- **X5b: ONNX execution provider priority.** https://onnxruntime.ai/docs/execution-providers/
- **X6: Playwright best practices.** https://playwright.dev/docs/best-practices
- **X7: Playwright trace viewer.** https://playwright.dev/docs/trace-viewer
- **X8: k6 constant arrival rate.** https://grafana.com/docs/k6/latest/using-k6/scenarios/executors/constant-arrival-rate/
- **X8b: k6 dropped iteration accounting.** https://grafana.com/docs/k6/latest/using-k6/scenarios/concepts/dropped-iterations/
- **X9: Eraser architecture diagram syntax.** https://docs.eraser.io/architecture-diagram-syntax
- **X9b: Eraser diagram as code.** https://docs.eraser.io/diagram-as-code

### 13.3 Acceptance without invented evidence

The delivered runbook is an implementation specification. The historical source numbers, user-reported isolated model numbers, proposed release limits and actual future measurements are four different evidence classes. Keep them separate in every dashboard, report and architecture export.
