# 10. Gateway backend replacement: clean-room rebuild and single-cutover migration

!!This section supersedes the repair-in-place assumption. Sections 1–8 defined *what* the gateway must do; §4's T00–T26 assumed the current `gateway/ai_mesh_gateway` package would be hardened into compliance. That assumption is withdrawn. The backend is rebuilt clean as `gateway-v2`, developed in parallel against a frozen wire contract, proven by shadow replay, and cut over in one event. §10.13 maps every T-task onto the new track so nothing in §4 is lost.!!

Baseline re-pinned for this section: `ansh @ 2a657fad` ("fix(t01): honor PII-off as a true bypass and classify AKIA as secret"), re-fetched and re-audited on 17 September 2026. Note that `revamp @ 877c27a8` is a *later* snapshot by commit time and is the branch carrying this runbook, the V3 runbook and `staging/t02/`. GW00 resolves which of the two is the true baseline before any code is written.

## 10.1 Why the backend is replaced rather than repaired

The decision does not rest on code aesthetics. It rests on five defects reproduced by executing the real production functions in this repository, plus a structural measurement that explains why fixing them individually does not converge.

### 10.1.1 Defects reproduced by execution

Each row below was produced by importing `gateway/ai_mesh_gateway/enforcement.py` at `ansh @ 2a657fad` and calling the shipped function directly. These are not readings of the code; they are its output.

| ID | Probe executed | Result | What it means |
|---|---|---|---|
| P1 | `resolve_enforcement("block", org_policy_action="allow")` | `"block"` | An organization's explicit ALLOW cannot suppress a scanner recommendation. Same result for `"flag"` and `"monitor"`. |
| P2 | `max_action("allow","block","allow")` | `"block"` | The mechanism is a severity maximum across `[policy, recommendation, default]`, not the precedence the module docstring promises at `enforcement.py:7-11` ("Precedence (highest wins): 1. Org policy"). |
| P3 | `enforce_output(verdict_action="block", scan_degraded=True)` | `"redact"` | A terminal output BLOCK is silently downgraded whenever the output scan is degraded. Control with `scan_degraded=False` returns `"block"`. |
| P6 | Full `enforce_output` matrix across `verdict_action × scan_degraded` | see below | When the output scan degrades, the emitted action becomes **independent of the verdict**. |
| P7 | `enforce_output(verdict_action="block", enforcement_mode="monitor")` | `"block"` | MONITOR does not neutralize enforcement on the output path. A tenant who selected observation-only still blocks. |
| P8 | `re.search(r"`[^`]+`", …)` against five benign inline-code prompts | 5 / 5 match | `ATTACK_PATTERNS["command_injection"]` contains the literal pattern `` `[^`]+` `` — any backtick pair. |

The P6 matrix is the single most damaging result, because it converts a security control into a coin flip:

| verdict_action | scan_degraded | resolved action | assessment |
|---|---|---|---|
| block | False | block | correct |
| block | True | **redact** | security downgrade — content is released that policy said to withhold |
| flag | False | flag | correct |
| flag | True | **redact** | false redaction — content is mutated that policy said to pass |
| allow | False | allow | correct |
| allow | True | **redact** | false redaction on clean traffic |

`enforcement.py:388-402` returns the degraded decision **before** `verdict_action` is read at `:404`. So during any Bedrock/Gemini flap, every response gets the same treatment regardless of what was actually found. The same prompt, sent twice, receives two different dispositions depending on the health of a third-party API. **This is the reported symptom "sometimes the prompt passes, sometimes not", located precisely.**

### 10.1.2 The label mismatch that disarms the fail-closed contract

`main.py:5447-5454` defines `_is_tier2_degraded_verdict` to return true when `threat_type == "bedrock_degraded"` or `reason_code` starts with `"degraded"`. The scanner emits its degraded verdict at `scanner.py:2464-2477` with `threat_type = "scanner_degraded"` and a `reason_code` drawn from `meta["decision_reason"]` — typically `client_error`, `parse_failed` or `empty_response` (`scanner.py:2119-2126`).

Neither condition is satisfied. `tier2_degraded=False` is therefore passed to the resolver at `main.py:9450`, the PIPELINE-0006 fail-closed contract at `enforcement.py:309-314` never arms, and the verdict is then discarded entirely by the adapter described next. A semantic-scanner outage produces a **silent full pass** on the input path and a **blanket redact** on the output path, simultaneously.

### 10.1.3 The adapter that discards findings before they reach the resolver

`_scanner_kwargs_for_enforcement` (`main.py:895-948`) returns an all-`None` dictionary unless one of three predicates holds. A Tier-2 verdict whose threat type falls outside `_PLATFORM_FLOOR_SCANNER_THREATS` or `_TIER2_INJECTION_THREATS`, and whose action is `flag` or `monitor`, is dropped before the resolver ever sees it — and `resolve_and_enforce` then resolves `allow` on an empty input.

The two threat-type sets are also divergent, which we confirmed by comparing them directly:

| Set | Members |
|---|---|
| `main._TIER2_INJECTION_THREATS` | `goal_hijacking`, `injection`, `jailbreak`, `prompt_injection` |
| `enforcement._INJECTION_THREAT_TYPES` | `goal_hijacking`, `jailbreak`, `prompt_injection` |
| Present in main only | **`injection`** |

A Tier-2 verdict labelled `injection` is admitted by the adapter and then bypasses the confidence/threshold gate at `enforcement.py:272-279` that the other three labels are subject to. Two spellings of the same concept, two different security outcomes.

### 10.1.4 Structural measurement: why individual fixes do not converge

Each defect above is individually a small patch. The reason they keep recurring is structural, and it is measurable.

| Measurement | Value at `ansh @ 2a657fad` |
|---|---|
| `main.py` | 17,452 lines / 808 KB, 213 top-level symbols, 35 routes |
| `proxy_chat` — one function | **5,027 lines** |
| — exit points | 95 `return` statements |
| — branch points | 339 `if`/`elif` |
| — exception handlers | 50, of which 17 are broad `except Exception` |
| — maximum nesting depth | 12 |
| — in-place mutations of `body` | 28 |
| — writes to the shared `stage_metrics` dict | 20 |
| Block/terminal-decision sites | **283 across 25 modules** |
| Distinct Tier-2 enablement resolvers | **4 implementations, 6 call paths** |
| Distinct environment variables read | 144 across 232 call sites |
| Test corpus | 496 Python test files, 105,248 lines |
| Test files actually run by CI | **2** |

A 5,027-line function with 339 branches and 95 exits has no reviewable state space. There is no point at which a reader can say what is true about a request. The 283 decision sites mean a correctness fix applied at one of them is silently contradicted at another — which is exactly the history recorded in this repository's own plan documents, where the same classes of defect have been found, fixed and refound across five review cycles.

The 105,248 lines of tests are not a counter-argument; they are the proof. That corpus coexists with every defect in §10.1.1. Tests cannot rescue an architecture that has no invariants for them to assert.

### 10.1.5 Configuration and isolation defects the rebuild must not inherit

| Finding | Evidence | Consequence |
|---|---|---|
| Tenants inherit the platform default posture | `config_sync.py:687` builds per-org config as `{**self._config, **data}`; `get_config` falls back to `_config_by_org["default"]` then the global config (`:295-301`) | Every key a tenant did not set silently takes the platform value, including `enforcement_mode` and `tier2_enabled`. A non-inheriting `get_own_config` exists at `:303-316` and the chat path does not use it. |
| "Unavailable" is indistinguishable from "selected nothing" | `PolicySync.is_loaded` is true if *any* org's bundle is cached (`policy_sync.py:228-233`) | Org B is reported ready on the strength of org A's bundle, evaluates against an empty rule set, and receives `allow`. |
| Unset Tier-2 means opposite things on input and output | Input: `resolve_tier2_enabled` → `value is True`, default **OFF** (`config_sync.py:111-128`). Output: `_enabled("output_tier2_enabled", True)` → different key, default **ON** (`output_guard.py:1010-1015`) | `tier2_enabled=None` disables input scanning and enables output scanning on the same request. |
| `/v1/vector/query` can never run Tier-2 | `_RAG_GUARDRAIL_KEYS` (`vector_routes.py:210-215`) copies four keys; `rag_tier2_enabled` is never propagated | A silent policy hole relative to `/v1/rag/query`, invisible from the console. |
| Same fault, opposite postures | Redis error → rate limiter returns `True` / allow (`rate_limiter.py:116,169,230`); circuit breaker returns `should_block=True` (`circuit_breaker.py:397-400`) | One outage simultaneously removes rate limiting and blocks all traffic. |
| Streaming BLOCK is a truncation | `secure_streaming.py:551-570` emits an error frame after earlier flushes already reached the client | An operator who selected BLOCK received partial delivery. |
| Output guard runs per flush, not once | `secure_streaming.py:451-460`; header comment at `:44-58` records 29 guard passes for 300 token deltas | Output cost scales with chunk count, not response count. |
| Unbounded buffer on the rewrite path | `secure_streaming.py:511-513` returns without clearing on every non-DONE rewrite flush | Memory linear in stream length and O(N²) guard cost. |
| Firewall-triggered generation | the output rewrite helper calls `LLM_ROUTER.acompletion` on the guard's behalf | The firewall makes model calls that are not the customer's request. |
| Raw text to an external model, on by default | `llm_judge.py:166` `invoke_model`, reached from `rag_pipeline/query_stage.py:452`, default true at `main.py:6674`, **fails open to regex** | Un-redacted tenant content leaves the platform by default on the RAG path. |

### 10.1.6 Capacity defects the rebuild must not inherit

A genuine cgroup-aware resource detector exists at `shared/ai_mesh_shared/resource_budget.py` (507 lines) and is well built: cgroup v2 `cpu.max`/`memory.max`, cgroup v1 fallbacks, `sched_getaffinity`, `RLIMIT_NOFILE`, taking the minimum of all applicable signals.

**It is never imported by gateway runtime Python.** It is invoked only by shell entrypoints, and `docker-compose.prod.yml:176` sets `WEB_CONCURRENCY: ${WEB_CONCURRENCY:-16}` while `gateway/entrypoint.sh:17` gives the environment unconditional priority. In production the detector is bypassed entirely and the worker count is the literal 16.

Hard clamps inside the detector itself cap large hosts regardless: `asgi_threads` clamped to 32 (`:326`), `scanner_pool` to 16 (`:339`), `vault_pool` to 8 (`:346`), `redis_pool` to 256 (`:351`). Fully hardcoded pools sit outside it: `mcp_proxy.py:149` `max_connections=64`, `rate_limiter.py:44` `max_connections=100`, `mcp_oauth.py:477` `max_connections=50`, and four separate `DEFAULT_THREAD_POOL_SIZE` constants of 4, 2, 2 and 4 in `vector_client.py:20`, `embedding_vault.py:45`, `llm_judge.py:25` and `context_guard.py:35`.

At the edge, `deploy/nginx.conf` contains **no `upstream` block at all**, so no `keepalive` directive is possible and nginx opens a fresh TCP connection to `gateway:8300` for every request. This is a hard connection-churn ceiling that no amount of application tuning removes.

Finally, `shared/ai_mesh_shared/redis_log_handler.py` attaches a **synchronous** Redis `publish` to the `gateway` logger, and `main.py:6716-6730` sets that logger to `DEBUG`. Every log line from every child logger performs a blocking round trip on the event-loop thread.

### 10.1.7 The conclusion, stated plainly

The gateway does not have an architecture that is under-maintained. It has no architecture: there is no layer boundary, no single decision authority, no one place where a request's fate is determined, and no invariant a test could protect. The 5,027-line function is a symptom; the 283 decision sites are the disease.

Repairing this in place would require changing the same code that is simultaneously the only running implementation — which is how the last five review cycles produced the same findings. A clean-room rebuild behind a frozen wire contract is cheaper, because it is the only option in which "done" is checkable.

## 10.2 Replacement doctrine

### 10.2.1 The one contract that is frozen

!!The only externally visible contract that may not change is this: an application already using the OpenAI SDK must work against AI Mesh by changing `base_url` and the API key, and nothing else. No custom client, no patched parser, no SDK fork, no header the SDK does not send, no response field the SDK cannot ignore. Every other interface in the backend — internal modules, database schema, trace payload, telemetry shape, config keys, Redis key layout — is explicitly unfrozen and expected to change.!!

This is a narrower contract than "preserve the current behaviour", and deliberately so. It is also a *stronger* one, because it is executable: the repository already contains its definition.

`gateway/ai_mesh_gateway/tests/test_openai_sdk_compat.py` and its five siblings are 1,979 lines and 77 tests, driving the real application through `openai==2.38.0` pinned exactly at `gateway/pyproject.toml:48`. They assert typed parsing of non-streaming completions, streaming chunk parsing, the terminal trace frame's SDK-acceptability, `data: [DONE]` termination, `APIStatusError`/`BadRequestError` for blocks, streaming blocks raising *before* any SSE byte, `AuthenticationError` on bad keys, `RateLimitError` on upstream 429, the `/v1/responses` typed event sequence, tool-call forwarding and reconstruction, `response_format` json_schema, usage blocks, embeddings, model listing, legacy `/v1/completions`, and error-envelope `param`/`code`/`request_id` population across every error class. `test_openai_sdk_compat_live_uvicorn.py` boots real uvicorn on a loopback TCP port to catch wire-level behaviour that `ASGITransport` masks.

**This suite is the specification of gateway-v2's public surface.** GW01 lifts it out of the old package, makes it implementation-agnostic, and puts it in CI — where it has never run, because the single existing workflow never installs `openai`.

### 10.2.2 What the rebuild is allowed to change, and must

Because the fidelity bar is "fix the measured defects, hold equivalence only where measured good", the following behaviours change deliberately and are scored against the detection corpus, not against v1's output:

| Area | v1 behaviour | v2 behaviour | Scored by |
|---|---|---|---|
| Injection/jailbreak detection | 3/10 same-family, 0/10 paraphrased | measured against corpus; published recall/FPR per posture | GW02 corpus, expected-diff ledger |
| Benign inline code | 5/5 backtick prompts hard-blocked at confidence 1.00 | injection/command patterns emit **signal**, not terminal block | expected-diff ledger |
| Degraded scan | action becomes independent of verdict | explicit `UNAVAILABLE` finding status; organization's signed failure posture applies | GW07 |
| Org policy precedence | severity maximum | signed rule priority; org intent is authoritative within its scope | GW07 |
| Unset tri-state | OFF on input, ON on output | one plan, one resolution, identical on every surface | GW05 |
| Missing tenant config | inherits platform default | explicit `PLAN_UNAVAILABLE`; never another tenant's posture | GW05 |
| Streaming block | truncation after partial delivery | withhold-before-first-byte, or the explicitly selected strict mode | GW12, GW13 |

Every row is a **deliberate, published behaviour change** with its own gate. A parity differ that scored these as regressions would be measuring the wrong thing, which is why §10.7 requires an expected-diff ledger rather than a pass/fail diff.

### 10.2.3 Structural rules for the new package

| Rule | Enforcement |
|---|---|
| Dependencies point one way only, edge → audit. A lower layer may never import a higher one. | `import-linter` contract in CI; GW00 |
| No module outside `resolve/` may produce a terminal security outcome. Detectors return findings; they do not raise HTTP responses, mutate the body, or short-circuit. | Static gate: no `HTTPException`, `JSONResponse` or `status_code=403` outside `edge/` and `resolve/`; GW07 |
| No function exceeds 120 lines; no module exceeds 800. | CI lint gate; GW00 |
| No capacity value is a literal. Every bound derives from the `ResourceContract`. | Static gate on numeric literals in pool/queue/worker positions; GW03 |
| One decision record per request phase, emitted from one place. | GW07, GW14 |
| The resolver is pure: no I/O, no clock, no globals. Given the same findings and plan, it returns the same decision. | Property test; GW07 |
| Request-scoped state is immutable after construction. Stages return new values; they do not mutate a shared dict. | Frozen dataclasses; GW04 |
| No module-level mutable state touched per request. | Static gate; GW00 |

The 120-line and 800-line limits are not style preferences. They are the mechanism that prevents `proxy_chat` from reconstituting itself, and they are checkable by a machine, which matters because this track is executed by agents.
## 10.3 Target backend architecture

### 10.3.1 Layer model

gateway-v2 is a single deployable process — not a microservice fan-out. Splitting auth, policy and scanning across HTTP hops would add round trips to a path with a 20 ms p99 budget. What changes is not the number of processes but the number of *boundaries inside* the process, and the direction in which they may be crossed.

```
  ┌──────────────────────────────────────────────────────────────────┐
  │ edge/          ASGI, HTTP, SSE framing, OpenAI wire types        │
  │                the ONLY layer that may construct a response      │
  └───────────────────────────────┬──────────────────────────────────┘
                                  │  RequestContext (frozen)
  ┌───────────────────────────────▼──────────────────────────────────┐
  │ admit/         identity, tenant, quota lease, kill-switch        │
  │                bounded shared-state access; 1 round trip         │
  └───────────────────────────────┬──────────────────────────────────┘
                                  │  Principal + ResourceGrant
  ┌───────────────────────────────▼──────────────────────────────────┐
  │ plan/          pinned ExecutionPlan snapshot for this org        │
  │                compiled off the hot path, versioned, immutable   │
  └───────────────────────────────┬──────────────────────────────────┘
                                  │  ExecutionPlan (frozen)
  ┌───────────────────────────────▼──────────────────────────────────┐
  │ detect/        deterministic + semantic detectors                │
  │                emit Finding[] ONLY — no I/O decisions, no 4xx    │
  │                guard/ backend interface: CPU, GPU, or remote     │
  └───────────────────────────────┬──────────────────────────────────┘
                                  │  Finding[]
  ┌───────────────────────────────▼──────────────────────────────────┐
  │ resolve/       THE single authority. Pure function.              │
  │                (Finding[], ExecutionPlan) -> Decision            │
  └───────────────────────────────┬──────────────────────────────────┘
                                  │  Decision (frozen)
  ┌───────────────────────────────▼──────────────────────────────────┐
  │ dispatch/      provider client; applies Decision transformations │
  │                never originates a model call of its own          │
  └───────────────────────────────┬──────────────────────────────────┘
                                  │  ProviderStream | ProviderResponse
  ┌───────────────────────────────▼──────────────────────────────────┐
  │ egress/        SSE state machine, bounded buffers, backpressure  │
  │                output detect → resolve → emit, same types        │
  └───────────────────────────────┬──────────────────────────────────┘
                                  │  DecisionRecord
  ┌───────────────────────────────▼──────────────────────────────────┐
  │ audit/         async, bounded, lossless-or-counted               │
  └──────────────────────────────────────────────────────────────────┘
```

Two properties follow mechanically from this shape, and both are the direct answer to a defect in §10.1:

**A finding cannot be lost.** `detect/` has exactly one consumer, `resolve/`, and the type it emits is the type the resolver consumes. There is no adapter in between, so there is no `_scanner_kwargs_for_enforcement` able to return all-`None` and erase a verdict. The adapter existed because the two sides spoke different vocabularies; v2 has one vocabulary.

**A decision cannot be overridden.** `resolve/` is the only module permitted to produce a `Decision`, and `egress/` consumes the `Decision` it is given. There is no second branch that can turn BLOCK into REDACT, because there is no second place where an action is computed.

### 10.3.2 Module tree

```
gateway_v2/
├── edge/
│   ├── app.py                  ASGI app assembly, lifespan, routers
│   ├── openai_chat.py          /v1/chat/completions, /v1/completions
│   ├── openai_responses.py     /v1/responses
│   ├── openai_misc.py          /v1/embeddings, /v1/models, /v1/moderations
│   ├── mcp.py                  MCP JSON-RPC + REST surface
│   ├── rag.py                  /v1/rag/*, /v1/vector/*
│   ├── wire/                   OpenAI request/response types, SSE codec
│   └── errors.py               the ONLY place an error envelope is built
├── admit/
│   ├── identity.py             key → Principal, with epoch-based revocation
│   ├── quota.py                local GCRA + shared lease
│   ├── killswitch.py           snapshot + fail-closed refresh
│   └── grant.py                ResourceGrant: what this request may consume
├── plan/
│   ├── model.py                ExecutionPlan, Rule, Mode, Action, FailurePosture
│   ├── compiler.py             validate + compile (control plane, off hot path)
│   ├── store.py                versioned snapshot distribution
│   └── snapshot.py             per-replica immutable cache + freshness age
├── detect/
│   ├── base.py                 Detector protocol, Finding, FindingStatus
│   ├── deterministic/          PII, secrets, credentials, transport, size
│   ├── semantic/               injection/jailbreak via guard backend
│   ├── windowing.py            tokenizer-aware segmentation + FPR accounting
│   └── guard/
│       ├── backend.py          GuardBackend protocol
│       ├── local_onnx.py       CPU dev/default
│       ├── local_trt.py        in-process TensorRT
│       ├── triton_grpc.py      node-local Triton
│       └── remote_http.py      off-box burst
├── resolve/
│   ├── resolver.py             PURE: (Finding[], ExecutionPlan) -> Decision
│   ├── decision.py             Decision, Disposition, Transformation
│   └── conflict.py             signed rule priority, no severity max
├── dispatch/
│   ├── provider.py             ProviderClient protocol
│   ├── routing.py              deterministic selection from the plan
│   └── transform.py            applies Decision.transformations, verifies bytes
├── egress/
│   ├── stream.py               SSE state machine + bounded coalescer
│   ├── backpressure.py         credit-based flow control
│   ├── output_guard.py         output detect → resolve → emit
│   └── strict.py               whole-response withhold mode
├── audit/
│   ├── record.py               DecisionRecord (one per phase)
│   ├── sink.py                 bounded async producer
│   └── metrics.py              Prometheus collectors + timing instrument
├── runtime/
│   ├── resources.py            ResourceContract (see §10.6)
│   ├── lifecycle.py            startup, readiness, drain
│   └── clock.py                injectable time source
└── contracts/
    └── openai_conformance/     the frozen suite, implementation-agnostic
```

### 10.3.3 The dependency contract, enforced

The layer order is `edge > admit > plan > detect > resolve > dispatch > egress > audit > runtime > contracts`. A module may import from strictly lower layers and from `runtime`/`contracts`. It may never import upward or sideways.

This is declared in `pyproject.toml` as `import-linter` layers and checked in CI. It is the single most important gate in the track, because it is what prevents the 283 decision sites from re-forming: `detect/` cannot import `edge/`, so a detector *cannot* return a 403 even if someone tries.

### 10.3.4 What is deliberately not in the architecture

| Excluded | Why |
|---|---|
| Separate auth/policy/scan microservices | Each hop costs a round trip against a 20 ms p99 budget. TrueFoundry's published 3–12 ms hop is in-process for exactly this reason. |
| An ORM on the request path | `dispatch/` and `admit/` open zero database connections. Control-plane state arrives as compiled snapshots. |
| A message broker in front of the model call | Acking before generation means the chat is not complete; acking after adds a hop to the same wait. |
| Any platform-owned external AI | §1's locked position. The `GuardBackend` protocol has no remote-model implementation that is not the tenant's own. |
| A firewall-initiated generation call | REWRITE, if supported, uses a local model through `GuardBackend`, never `dispatch/`. |
| Framework middleware for security | Four `BaseHTTPMiddleware` layers measured at 1.00 ms p50 / 4.70 p99. v2 uses pure-ASGI middleware and does security work in `admit/`, where it is timed. |

## 10.4 The request lifecycle

```
  client (OpenAI SDK)
        │  POST /v1/chat/completions
        ▼
  ① edge/     t0 ← clock.now()        ← the timer starts HERE, before identity
        │      parse wire types, build frozen RequestContext
        ▼
  ② admit/    identity → Principal    (RAM cache; shared store only on miss)
        │      kill-switch snapshot   (RAM, age-bounded, fail-closed on stale)
        │      quota: local GCRA + one shared lease op
        │      → ResourceGrant  |  reject: 401 / 429 / 503
        ▼
  ③ plan/     pin ExecutionPlan for this org, by version
        │      PLAN_UNAVAILABLE is a distinct outcome, never "no rules"
        ▼
  ④ detect/   plan-selected input detectors run
        │      deterministic detectors: always, in-process
        │      semantic detectors: only if the plan selects one
        │      each returns Finding[] with status EXECUTED | SKIPPED | UNAVAILABLE
        ▼
  ⑤ resolve/  Decision = resolve(findings, plan)          ← PURE
        │      disposition ∈ ALLOW | FLAG | REDACT | BLOCK
        │      + transformations[] + provenance
        ▼
     BLOCK ──────────────────────────────► ⑧ (zero provider calls, by construction)
        │
  ⑥ dispatch/ apply transformations, VERIFY transformed bytes,
        │      then one provider call with the verified payload
        ▼
  ⑦ egress/   per released chunk: output detectors → resolve → emit
        │      bounded coalescer, credit-based backpressure
        │      cancellation propagates to provider and guard
        ▼
  ⑧ audit/    exactly one DecisionRecord per phase, enqueued, never awaited
        │
        ▼
  client
```

Three things in this sequence are load-bearing and each replaces a named defect:

**The timer starts in `edge/` at ①, before identity resolution.** In v1, `start = time.perf_counter()` sits at `main.py:7085` *inside* the handler, after `AuthMiddleware` has already performed its Redis round trip — so authentication latency is invisible to every trace, and `stage_metrics["auth_ms"]` at `:7691` actually measures body parsing. §1.2's `T_fw_addon` cannot be measured honestly from a timer that starts after the work begins.

**`model_output_ms` is measured, never derived.** v1 computes it by subtraction (`stream_orchestration.py:683`: `duration_ms - ttft_ms`, explicitly excluding TTFT), so provider time-to-first-token lands in `overhead_ms` and is reported as firewall overhead. v2 timestamps provider-stream open and close directly. The reconciliation residual `|wall − Σstages|` is recorded **signed**, and a non-zero p50 fails CI.

**BLOCK short-circuits structurally, not by convention.** `dispatch/` is only reachable from a `Decision` whose disposition permits dispatch. There is no code path in which a blocked request can reach a provider, because the provider client's entry point requires a `DispatchAuthorization` value that `resolve/` only mints for non-blocking dispositions.

## 10.5 Core interface specifications

These are the contracts agents implement against. They are deliberately small.

### 10.5.1 Findings — what detectors say

```python
class FindingStatus(StrEnum):
    EXECUTED   = "executed"     # the detector ran and reports this result
    SKIPPED    = "skipped"      # the plan did not select it; no work was done
    UNAVAILABLE = "unavailable" # selected, but could not run (model down, timeout)

@dataclass(frozen=True, slots=True)
class Finding:
    detector: str               # stable id, e.g. "pii.email"
    detector_version: str       # model or ruleset hash — in the audit record
    category: str               # taxonomy term, ONE spelling, from plan/model.py
    status: FindingStatus
    confidence: float | None    # None iff status is not EXECUTED
    spans: tuple[Span, ...]     # byte offsets into the scanned text
    evidence: str | None        # redacted, bounded
```

A detector returns `Finding[]` and nothing else. It cannot block, cannot mutate, cannot log a decision, cannot raise an HTTP error. `UNAVAILABLE` is a first-class result: it is never coerced to a clean pass, which is the defect at §10.1.2 removed by construction.

`category` is drawn from a single enum in `plan/model.py`. The `injection` vs `prompt_injection` divergence in §10.1.3 cannot recur, because there is one spelling and it fails to compile otherwise.

### 10.5.2 The execution plan — what the organization decided

```python
@dataclass(frozen=True, slots=True)
class Rule:
    rule_id: str
    category: Category
    mode: Mode                  # OFF | MONITOR | ENFORCE
    action: Action              # ALLOW | FLAG | REDACT | BLOCK | REWRITE
    threshold: float | None
    priority: int               # explicit; conflicts resolve by this, never by severity
    scope: RuleScope            # INPUT | OUTPUT | BOTH, and which surfaces
    on_unavailable: FailurePosture   # FAIL_OPEN | FAIL_CLOSED | DEGRADE_TO(action)

@dataclass(frozen=True, slots=True)
class ExecutionPlan:
    org_id: str
    version: str                # monotonic; appears in every audit record
    compiled_at: float
    rules: tuple[Rule, ...]
    required_detectors: frozenset[str]   # derived at compile time
    streaming_mode: StreamingMode        # INCREMENTAL | STRICT_WITHHOLD
    integrity: PlatformIntegrity         # NOT tenant-editable
```

The plan is compiled by the control plane, validated once, versioned, and distributed. The hot path never interprets raw configuration, never reads a tri-state, and never applies a default — those decisions happened at compile time where they can be validated and rejected.

`required_detectors` is derived: if no enabled rule needs the semantic model, the model is not invoked, and that is a property of the compiled plan rather than a runtime `if`. This is what makes OFF genuinely mean "no work was done for that rule".

Three plan states are distinct and must stay distinct:

| State | Meaning | Hot-path behaviour |
|---|---|---|
| `ExecutionPlan` with `rules=()` | The organization is authorized and selected no optional content rules. | Platform integrity applies; no content rules run. |
| `PLAN_UNAVAILABLE` | The plan could not be loaded, is stale beyond its freshness bound, or failed validation. | Signed not-ready behaviour. **Never** another tenant's plan, never the platform default. |
| `PLAN_UNKNOWN_TENANT` | The principal resolved but has no plan. | Explicit onboarding error. Never inherited defaults. |

§10.1.5's inheritance defect is a direct consequence of v1 collapsing all three into one dictionary lookup with a fallback chain.

### 10.5.3 The resolver — the single authority

```python
def resolve(
    findings: Sequence[Finding],
    plan: ExecutionPlan,
    phase: Phase,               # INPUT | OUTPUT
) -> Decision:
    """Pure. No I/O, no clock, no globals, no logging side effects.
    Same inputs → same Decision, always."""
```

```python
@dataclass(frozen=True, slots=True)
class Decision:
    disposition: Disposition          # ALLOW | FLAG | REDACT | BLOCK
    transformations: tuple[Transformation, ...]
    findings: tuple[Finding, ...]     # ALL of them, including SKIPPED
    plan_version: str
    deciding_rules: tuple[str, ...]   # which rule_ids produced this, in priority order
    unavailable_detectors: tuple[str, ...]
```

Resolution order, and it is total:

1. Partition findings by the rule that selected the detector. A finding with no selecting rule is recorded and has **no** effect on disposition.
2. Drop `SKIPPED`. Route `UNAVAILABLE` through that rule's `on_unavailable` posture — never through a generic degraded branch.
3. For `MONITOR` rules, record the finding and contribute **nothing** to disposition.
4. For `ENFORCE` rules, the rule's configured `action` is the candidate.
5. Resolve candidate conflicts by explicit `priority`, then by a documented tie-break. **There is no severity maximum.**
6. `BLOCK` is terminal: no later stage may alter it.

Because the function is pure, it is property-testable: the track requires a Hypothesis suite asserting that no `(findings, plan)` pair produces a disposition not derivable from an ENFORCE rule, and that adding a `MONITOR` rule never changes a disposition. Both properties are false in v1 today, as P1, P6 and P7 demonstrate.

### 10.5.4 The guard backend — pluggable, no topology assumed

```python
class GuardBackend(Protocol):
    async def classify(self, windows: Sequence[str], budget: Budget) -> Sequence[GuardResult]: ...
    async def readiness(self) -> Readiness: ...   # includes model_hash
    def capacity_hint(self) -> CapacityHint: ...  # windows/s, queue depth, batch profile
```

`detect/semantic/` calls this and knows nothing about CPU, GPU, TensorRT, Triton, batch size or replica count. The selection is deployment configuration. This is precisely what "no capacity assumption tied to the present budget" means in code: moving from one L4 to forty is a config change and a `capacity_hint` change, not a source edit.

`readiness()` returning not-ready means selected semantic rules are `UNAVAILABLE`, which routes through `on_unavailable`. It never means "clean".

### 10.5.5 The egress pipeline

```python
class StreamPipeline:
    async def run(self, upstream: ProviderStream, decision_ctx: OutputContext) -> AsyncIterator[bytes]:
        """Bounded. Cancellable. Backpressure-aware.
        Buffer high-water mark derives from ResourceContract, never a literal."""
```

Requirements, each a named v1 defect:

| Requirement | Replaces |
|---|---|
| A single bounded coalescer; buffer ceiling from `ResourceContract`, not `4096` | unbounded rewrite-path buffer, `secure_streaming.py:511-513` |
| Output detectors run on a **content-defined schedule**, not per flush; per-request invocation count is a recorded metric with a plan-derived ceiling | 29 guard passes per 300 deltas |
| Cancellation propagates to provider and guard within a bounded interval | polled `is_disconnected` that ends the generator but never aborts upstream |
| `BLOCK` in `INCREMENTAL` mode is only expressible before the first content byte; after that the only truthful outcomes are terminate-with-error or the tenant's selected `STRICT_WITHHOLD` | truncation-presented-as-block |
| Backpressure is credit-based; a slow consumer slows upstream reads rather than growing memory | no backpressure at all |

The third row is a product statement, not an implementation detail: once bytes have left, they cannot be unsent. v2 makes the tenant choose the mode up front and then tells the truth about which one they got.

## 10.6 Capacity from the environment

### 10.6.1 The contract

```python
@dataclass(frozen=True, slots=True)
class ResourceContract:
    cpu_quota: float            # cgroup v2 cpu.max → v1 cfs_quota → sched_getaffinity
    memory_limit: int           # cgroup memory.max → v1 limit → /proc/meminfo
    fd_limit: int               # RLIMIT_NOFILE
    guard_capacity: CapacityHint | None   # from GuardBackend.capacity_hint()
    target_p99_ms: float        # deployment-declared SLO, not a constant
    utilization_cap: float      # deployment-declared headroom

    def workers(self) -> int: ...
    def queue_depth(self, service_rate: float) -> int: ...
    def pool_size(self, kind: PoolKind) -> int: ...
    def stream_buffer_bytes(self, active_streams: int) -> int: ...
```

Every bound in the process is a method call on this object. `runtime/resources.py` is the **only** module permitted to contain a numeric literal in a capacity position, and the CI gate at GW03 enforces that by AST inspection of pool/queue/worker constructor arguments across the package.

### 10.6.2 What this replaces, specifically

The detector at `shared/ai_mesh_shared/resource_budget.py` is good work and its *detection* half is carried forward — the cgroup v2 → v1 → affinity → RLIMIT chain with minimum-of-signals is correct and tested. Three things change:

1. **It is imported in-process**, not shelled out from an entrypoint. Pools can then resize on a `SIGHUP` without a container restart.
2. **The clamps are removed.** `asgi_threads` capped at 32, `scanner_pool` at 16, `vault_pool` at 8, `redis_pool` at 256 are exactly the "capacity assumption in code" the objective forbids. Bounds become functions of measured service rate and the declared p99 target.
3. **`WEB_CONCURRENCY` stops overriding it.** `docker-compose.prod.yml:176` and the unconditional env priority at `gateway/entrypoint.sh:17` are removed; an explicit override remains available but is logged at startup as a deviation, with the detected value alongside it.

The seven hardcoded pools listed in §10.1.6 — `mcp_proxy.py:149`, `rate_limiter.py:44`, `mcp_oauth.py:477`, and the four `DEFAULT_THREAD_POOL_SIZE` constants — have no counterpart in v2; those call sites take a `ResourceContract` and ask it.

### 10.6.3 Scale-out as a deployment action

The §7 table remains the contract. What §10.6 adds is the mechanism that makes it true: because every bound is derived and every fleet boundary is an interface (`GuardBackend`, `ProviderClient`, `StateStore`, `AuditSink`), moving from four nodes to forty changes replica counts and a `capacity_hint`. There is no source file in which the number of GPUs, the number of workers, or the target RPS appears.

## 10.7 Parity and cutover machinery

Greenfield-parallel with a single cutover has one failure mode: the new system is beautiful and wrong in a way nobody noticed. The entire risk budget of this track goes into making that detectable before the cutover, not after.

### 10.7.1 The three-corpus method

| Corpus | Source | Size target | Purpose |
|---|---|---|---|
| **C1 — wire conformance** | the existing 77 SDK conformance tests, lifted and made implementation-agnostic | 77 tests, grown as gaps appear | v2's public contract. Must pass 100%, always. |
| **C2 — recorded production shape** | sanitized capture from staging/production-shaped traffic across all four surfaces | ≥50,000 requests, all surfaces, both streaming modes | Replay corpus for the differ. Establishes that v2 handles the shapes that actually occur. |
| **C3 — labelled detection** | §4's T04 corpus: ≥300 injections across ≥8 families incl. a disjoint-vocabulary paraphrase family, ≥300 benign incl. a developer-traffic family | as T04 | Scores the deliberate behaviour changes. **Neither v1 nor v2 is the oracle; the labels are.** |

C3 is what makes "fix the defects" executable. Without it, v2's improved injection recall is indistinguishable from a regression, and its refusal to block backticks looks like a hole.

### 10.7.2 The differ and the expected-diff ledger

Replaying C2 through v1 and v2 produces a diff per request. Every diff lands in exactly one bucket:

| Bucket | Meaning | Gate |
|---|---|---|
| **IDENTICAL** | Same disposition, same transformations, same provider bytes. | Target for the majority of C2. |
| **EXPECTED** | Matches a pre-registered entry in the expected-diff ledger, with a rule id, the §10.2.2 row it implements, and its C3 score. | Allowed, counted, reviewed. |
| **UNEXPECTED** | Everything else. | **Blocks cutover. No exceptions.** |
| **WIRE** | Any difference visible to the OpenAI SDK. | **Blocks cutover.** C1 should have caught it; a WIRE diff means C1 has a gap and C1 gets extended. |

The ledger is written *before* the differ runs, as part of the task that changes the behaviour. An entry added after a diff is observed is a post-hoc rationalization and the GW21 gate rejects ledger entries whose git timestamp is later than the diff run.

### 10.7.3 Shadow execution

v2 runs in shadow against live-shaped traffic before it serves any: v1 serves the response; v2 receives a copy of the request, executes fully against the synthetic provider recorder, and its decision is recorded, never delivered. Shadow must reach a stable window — the track uses ≥72 hours and ≥5 million requests with zero UNEXPECTED diffs — before the cutover rehearsal is scheduled.

Shadow also measures what a replay cannot: real concurrency, real tenant mix, real plan-update-under-load, and v2's actual resource curve beside v1's.

### 10.7.4 Why big-bang, and what makes it survivable

A single cutover was chosen over route-by-route because the two systems disagree deliberately (§10.2.2) — and running both simultaneously on live traffic would mean the same tenant gets different security dispositions depending on which gateway served the request. That is worse than either system alone, and it is unauditable.

What makes one cutover survivable is that it is a **routing change, not a deployment**: v2 is already deployed, already warm, already shadowing, and the switch is an edge weight. Rollback is the same weight moved back, with v1 still running and still warm. The rehearsal in GW22 executes the full sequence — cut, observe, roll back — on staging under load, and the measured rollback time becomes the published number.
## 10.8 Backend rebuild task index

| Task | Title | Phase | Depends on |
|---|---|---|---|
| GW00 | Re-pin the baseline, resolve the credential exposure, stand up the v2 workspace and dependency gates | Foundation | — |
| GW01 | Freeze the OpenAI wire contract as an implementation-agnostic conformance suite in CI | Foundation | GW00 |
| GW02 | Build the parity harness: recorded corpus, replay differ, expected-diff ledger | Foundation | GW00, GW01 |
| GW03 | Implement the ResourceContract and prohibit capacity literals | Foundation | GW00 |
| GW04 | Define the core domain types and prove request state is immutable | Core | GW00 |
| GW05 | Build the plan compiler, distribution and snapshot with three distinct plan states | Core | GW04 |
| GW06 | Build the admission layer with bounded shared state and one round trip | Core | GW03, GW04 |
| GW07 | Build the single pure resolver and prove nothing else can decide | Core | GW04, GW05 |
| GW08 | Build the detector framework and the GuardBackend interface | Detection | GW04, GW07 |
| GW09 | Implement deterministic detectors with byte-verified redaction | Detection | GW08 |
| GW10 | Implement semantic detection, windowing and the per-request FPR budget | Detection | GW08 |
| GW11 | Build provider dispatch with verified transformation and no firewall-initiated generation | Dispatch | GW07 |
| GW12 | Build the SSE egress pipeline with bounded buffers, backpressure and real cancellation | Protocol | GW03, GW11 |
| GW13 | Implement output enforcement and the two explicit streaming modes | Protocol | GW07, GW12 |
| GW14 | Build async bounded audit and the honest timing instrument | Observability | GW04, GW12 |
| GW15 | Ship the chat surface on v2 and pass the full conformance suite | Surface | GW01, GW11, GW13, GW14 |
| GW16 | Ship embeddings, models, moderations and legacy completions | Surface | GW15 |
| GW17 | Ship the MCP surface on the same plan and resolver | Surface | GW15 |
| GW18 | Ship RAG and vector surfaces on the same plan and resolver | Surface | GW15 |
| GW19 | Implement admission control, overload semantics and graceful drain | Hardening | GW06, GW12 |
| GW20 | Measure one v2 serving unit and prove horizontal scaling | Performance | GW15–GW19 |
| GW21 | Run shadow execution to ledger closure | Migration | GW20, GW02 |
| GW22 | Rehearse cutover and rollback under load | Migration | GW21 |
| GW23 | Execute the production cutover | Migration | GW22 |
| GW24 | Decommission v1, remove external-AI paths and credentials, publish the evidence pack | Release | GW23 |

Parallelism for agent execution: GW01, GW02 and GW03 are independent of each other and of GW04. GW09 and GW10 are independent. GW16, GW17 and GW18 are independent once GW15 lands. Everything else is a strict chain.

## 10.9 Backend rebuild task cards

### GW00 — Re-pin the baseline, resolve the credential exposure, stand up the v2 workspace and dependency gates

| Field | Value |
|---|---|
| Phase | Foundation |
| Depends on | None |
| Primary owner | Backend lead + security owner |
| Objective | Establish which commit is truth, remove a live credential exposure, and create a workspace whose structural rules are machine-enforced from the first commit. |

#### Why this task exists

Three branches disagree and the newest by commit time is not the one previously audited: `main @ 6cec3e28` (stale, 2026-08-10), `ansh @ 2a657fad` (2026-09-17 12:35), `revamp @ 877c27a8` (2026-09-17 13:39, carries this runbook, the V3 runbook and `staging/t02/`). Separately, `ai-mesh-firewall` at the repository root is a tracked **OpenSSH private key** — verified header, `ssh-ed25519` per the adjacent `.pub`, 399 bytes — present on **all three branches**. `.gitignore` covers `*.pem`, `*.key`, `*.secret` but the file has no extension, so no pattern matched it.

#### Implementation work

Classify the key privately. Treat it as compromised: revoke the authorization it grants and rotate anything reachable by it **before** any new staging credential is issued. Open a history-rewrite ticket separately; deleting the file does not revoke access. Add an extensionless-private-key rule to `.gitignore` and to `scripts/ralph/precommit-secret-scan.sh`, and add a CI job that greps every tracked file for PEM and OpenSSH headers.

Diff the three branches and record which is truth, which images production actually runs, and the rollback tuple. Publish the decision; every later task cites this SHA.

Create `gateway_v2/` as a new package with the §10.3.2 tree, empty modules, and the gates live from commit one: `import-linter` layer contract; function-length ≤120 and module-length ≤800 lints; an AST gate rejecting `HTTPException`, `JSONResponse` and `status_code=4xx` outside `edge/` and `resolve/`; an AST gate rejecting module-level mutable state; `ruff` and `mypy --strict`.

Wire the CI workflow that will gate this track. The existing `.github/workflows/chat-pipeline-golden.yml` runs two test files on six paths and never installs `openai`; the new workflow runs the full v2 suite plus the conformance suite on every PR.

#### Live acceptance tests — required to exit

| Test | Live procedure | PASS | FAIL / stop |
|---|---|---|---|
| LGW00-1 | Attempt authentication with the exposed key against every system it could reach. | Access denied everywhere; rotation record signed by the infrastructure owner. | Any system still accepts it. |
| LGW00-2 | Commit a file containing an OpenSSH private-key header with no extension; push to a branch. | Pre-commit blocks locally; CI fails the PR. | Either gate passes it. |
| LGW00-3 | Add `from gateway_v2.edge import app` inside `gateway_v2/detect/base.py`; run CI. | `import-linter` fails with the violated contract named. | CI green. |
| LGW00-4 | Add a 130-line function and an 850-line module; run CI. | Both lints fail with file and line. | Either passes. |
| LGW00-5 | Add `raise HTTPException(403)` inside `gateway_v2/detect/`; run CI. | AST gate fails naming the file. | CI green. |
| LGW00-6 | Build the v2 image from the pinned SHA twice on different hosts. | Byte-identical digests. | Digests differ; the build is not reproducible. |

#### Failure scenarios that must be handled

Key is genuine and still authorized → revoke before anything else, and treat every credential in the same blast radius as rotated. Branch truth is contested → do not proceed on assumption; the deployed image digest decides. Reproducible build fails → fix the build before any measurement, because every later number is attributed to a SHA.

#### Exit criteria

Credential classified and, if genuine, revoked and rotated with a signed record. Baseline SHA published. `gateway_v2/` exists, empty, with all five structural gates demonstrably failing on deliberately bad input and passing on the empty tree. CI workflow runs on every PR.

#### Evidence package

Rotation record · branch diff and deployed-image correlation · CI run showing each gate failing on its negative fixture · two identical image digests · pinned SHA in the runbook header.

#### Rollback / stop rule

No rollback — nothing is deployed. **Stop** if the key cannot be classified within one working day: escalate rather than proceed, because every subsequent staging credential is issued into a possibly-compromised perimeter.

#### Agent execution notes

Fully agent-executable except the credential classification and revocation, which require a human with infrastructure authority. Agents must not print key material into logs, PR descriptions or task output.

---

### GW01 — Freeze the OpenAI wire contract as an implementation-agnostic conformance suite in CI

| Field | Value |
|---|---|
| Phase | Foundation |
| Depends on | GW00 |
| Primary owner | Protocol engineer |
| Objective | Turn the one frozen contract into an executable specification that both v1 and v2 must satisfy, and that runs on every commit. |

#### Why this task exists

The single contract that may not change (§10.2.1) is already written: 1,979 lines and 77 tests against `openai==2.38.0`. It currently imports the v1 application directly and **never runs in CI**, because the only workflow does not install `openai`. A frozen contract that is not executed on every change is not frozen.

#### Implementation work

Move the six conformance files into `gateway_v2/contracts/openai_conformance/`. Replace direct imports of the v1 app with a fixture that resolves the application under test from an environment variable, so the identical suite runs against v1, v2-in-process, and v2 over real TCP.

Keep `test_openai_sdk_compat_live_uvicorn.py`'s real-socket variant and make it mandatory, not optional: SSE framing over chunked transfer, typed event ordering, content-type on a streamed block, and mid-stream client disconnect are all invisible to `ASGITransport`.

Add the Node SDK. Install the pinned `openai` npm package and run the equivalent streaming, tool-call and error-class assertions, because §6's release gate names a Python **and** Node matrix and only Python exists today.

Resolve the two `xfail(strict=False)` markers (D4 `models.retrieve`, D5 typed responses stream) — either they pass and become strict, or they are real gaps and enter the ledger as known v1 deviations.

Extend coverage to the surfaces the suite does not reach: MCP JSON-RPC framing, `/v1/rag/*` and `/v1/vector/*` request and error shapes.

#### Live acceptance tests — required to exit

| Test | Live procedure | PASS | FAIL / stop |
|---|---|---|---|
| LGW01-1 | Run the suite against v1 in CI. | Result recorded as the baseline, including any failures, which enter the ledger. | Suite cannot run against v1. |
| LGW01-2 | Run the real-socket variant against v1 behind the staging nginx. | SSE framing, event order and disconnect behaviour recorded at the wire. | Suite only runs in-process. |
| LGW01-3 | Node SDK matrix against staging. | Streaming iterator completes; tool calls reconstruct; typed errors raise. | Any test needs a custom parser or a patched client. |
| LGW01-4 | Break the wire deliberately: emit one malformed `data:` frame. | Suite fails and names the frame. | Suite passes — it is not testing the wire. |
| LGW01-5 | Run the suite twice against the same build. | Identical results; no flakes. | Non-deterministic result. |

#### Failure scenarios that must be handled

v1 fails a conformance test → it is a recorded v1 deviation in the ledger, not a licence to weaken the test. Node and Python disagree → the stricter behaviour is the contract. A test only passes in-process → it is not a wire test; fix or delete it.

#### Exit criteria

Suite runs against an app resolved by configuration. Python and Node both in CI on every PR. Real-socket variant mandatory. `xfail` markers resolved. v1 baseline recorded with every deviation in the ledger.

#### Evidence package

CI runs for v1 in-process, v1 over TCP, Node matrix · pinned SDK versions and lockfiles · recorded v1 baseline · ledger entries for deviations · negative-fixture run.

#### Rollback / stop rule

None — additive. **Stop** if a conformance assertion cannot be satisfied without an SDK-side workaround: that is a contract violation and the design changes, not the test.

#### Agent execution notes

Fully agent-executable. The agent must not modify an assertion to make it pass; a failing assertion is a finding and goes to the ledger.

---

### GW02 — Build the parity harness: recorded corpus, replay differ, expected-diff ledger

| Field | Value |
|---|---|
| Phase | Foundation |
| Depends on | GW00, GW01 |
| Primary owner | QA/perf engineer |
| Objective | Make "v2 behaves correctly" a measured claim rather than an opinion, and make the deliberate behaviour changes visible and pre-registered. |

#### Why this task exists

Greenfield-parallel with one cutover is only safe if divergence is detectable in advance. Because §10.2.2 changes behaviour on purpose, a plain pass/fail differ would report every improvement as a regression. The ledger is what separates the two.

#### Implementation work

Build the C2 recorder: capture request shape, headers relevant to routing, plan version, disposition, transformations, provider payload and client bytes across all four surfaces, both streaming modes, streaming and non-streaming, and both tenants of the opposite-policy pair. Sanitize at capture, never after. Target ≥50,000 requests covering every surface and both modes.

Reuse `staging/t02/` from the `revamp` branch — a deterministic token-emitting upstream recorder with nginx, provisioning and teardown scripts already exists and is exactly the controlled provider this needs. Cherry-pick it rather than rebuilding it.

Build the replayer: feed C2 through v1 and v2 against the same recorder, with the same plan versions and the same clock source, and classify each result into IDENTICAL / EXPECTED / UNEXPECTED / WIRE per §10.7.2.

Build the ledger as a version-controlled file. Each entry carries a rule id, the §10.2.2 row it implements, the C3 score that justifies it, the diff signature it authorizes, and its commit timestamp. The differ rejects any entry whose timestamp post-dates the run it would excuse.

Wire C3 scoring: recall and FPR per posture per family, with the paraphrase and developer-traffic families reported separately, because those two are where v1 is known to fail in opposite directions.

#### Live acceptance tests — required to exit

| Test | Live procedure | PASS | FAIL / stop |
|---|---|---|---|
| LGW02-1 | Replay C2 through v1 twice. | 100% IDENTICAL against itself. | v1 is non-deterministic on replay — investigate before comparing anything to it. |
| LGW02-2 | Inject a deliberate disposition change into a v2 stub with no ledger entry. | UNEXPECTED; run fails. | Differ misses it. |
| LGW02-3 | Add the matching ledger entry with a back-dated commit; re-run. | Rejected on timestamp. | Post-hoc rationalization accepted. |
| LGW02-4 | Change a response field the SDK parses. | WIRE bucket; run fails; C1 gap logged. | Classified as EXPECTED. |
| LGW02-5 | Score v1 against C3. | Recall/FPR table per posture and family published, reproducing the known backtick and paraphrase results. | Corpus cannot reproduce known v1 behaviour — the corpus is wrong. |

#### Failure scenarios that must be handled

C2 under-covers a surface → coverage is reported per surface and a gap blocks GW21, not GW02. Replay is non-deterministic because of time or ordering → inject the clock and fix the ordering before trusting any diff. Sanitization removes something the differ needs → change what is captured, never un-sanitize.

#### Exit criteria

C2 ≥50,000 requests with per-surface coverage published. Replayer classifies into four buckets. Ledger enforces pre-registration. C3 scoring reproduces v1's known behaviour. v1 replays identically to itself.

#### Evidence package

C2 manifest with coverage by surface and mode · v1 self-replay report · four negative-fixture runs · v1 C3 scorecard · ledger schema and CI gate.

#### Rollback / stop rule

None — harness only. **Stop** if v1 cannot replay identically to itself: comparing v2 to a non-deterministic oracle produces meaningless diffs, and the non-determinism must be located first.

#### Agent execution notes

Fully agent-executable. The agent may not add ledger entries; those require the human who authorizes the behaviour change, and the timestamp gate enforces it.

---

### GW03 — Implement the ResourceContract and prohibit capacity literals

| Field | Value |
|---|---|
| Phase | Foundation |
| Depends on | GW00 |
| Primary owner | Platform engineer |
| Objective | Make every bound in the process a function of the environment, so that scaling is a deployment action and never a source edit. |

#### Why this task exists

This is the objective's central requirement expressed in code. Today a correct cgroup detector exists and is bypassed in production by `WEB_CONCURRENCY: 16`, its own output is clamped at 32/16/8/256, and seven further pools are hardcoded outside it entirely.

#### Implementation work

Port the detection half of `shared/ai_mesh_shared/resource_budget.py` verbatim — the cgroup v2 → v1 → `sched_getaffinity` → `RLIMIT_NOFILE` chain with minimum-of-signals is correct, tested, and worth keeping. Keep its injectable-root test seams.

Delete every clamp. Replace `workers`, `queue_depth`, `pool_size` and `stream_buffer_bytes` with functions of CPU quota, memory limit, fd limit, measured service rate and the deployment-declared `target_p99_ms` and `utilization_cap`.

Implement the CI gate: an AST pass that flags integer literals appearing as pool sizes, worker counts, queue depths, semaphore limits, buffer sizes and connection limits anywhere outside `runtime/resources.py`.

Make the contract reloadable on `SIGHUP` so pools resize without a restart, and log the detected values and any explicit override at startup as a named deviation.

#### Live acceptance tests — required to exit

| Test | Live procedure | PASS | FAIL / stop |
|---|---|---|---|
| LGW03-1 | Run the same image at 2, 4, 8 and 16 CPU quota. | Worker count, pool sizes and queue depths differ at each; all logged. | Any value constant across all four. |
| LGW03-2 | Halve the memory limit at fixed CPU. | Buffer and queue ceilings fall; worker count responds per contract. | Memory has no effect. |
| LGW03-3 | Lower `RLIMIT_NOFILE` below the derived connection budget. | Connection bounds fall accordingly; startup logs the binding signal. | Process exceeds the fd limit under load. |
| LGW03-4 | Add `max_connections=64` to a module outside `runtime/`. | CI gate fails naming file and line. | Gate passes. |
| LGW03-5 | Change CPU quota and `SIGHUP`. | Pools resize; no restart; no dropped in-flight request. | Requires restart, or drops requests. |
| LGW03-6 | Set an explicit `WEB_CONCURRENCY` override. | Honoured, and logged as a deviation with the detected value beside it. | Silently honoured. |

#### Failure scenarios that must be handled

cgroup files unreadable → documented fallback order, logged, never a silent default. Detected capacity below the minimum to serve → refuse to start with a clear message rather than start degraded. Guard capacity unknown → guard-derived bounds are explicitly unset, not guessed.

#### Exit criteria

Every bound derives from the contract. Literal gate live and demonstrated. Reload without restart. Four CPU points produce four configurations. Override logged as deviation.

#### Evidence package

Four-point capacity table · memory and fd sensitivity runs · negative-fixture CI run · SIGHUP reload under load · startup log showing detected values and binding signal.

#### Rollback / stop rule

Revert to the previous contract revision; bounds are versioned with the image. **Stop** if a bound cannot be derived and a literal is proposed as a workaround — that literal is the defect this task exists to remove.

#### Agent execution notes

Fully agent-executable. The agent must not introduce a literal "temporarily"; the gate will reject it and the task is not complete until the derivation exists.
### GW04 — Define the core domain types and prove request state is immutable

| Field | Value |
|---|---|
| Phase | Core |
| Depends on | GW00 |
| Primary owner | Backend architect |
| Objective | Establish one vocabulary — Finding, Decision, ExecutionPlan, RequestContext — and make order-dependent mutation structurally impossible. |

#### Why this task exists

v1's `proxy_chat` mutates `body` in 28 places and writes a shared `stage_metrics` dict in 20, across 5,027 lines and 339 branches. `effective_prompt` is reassigned at five separate points, and `scan_text` is derived from it *after* the policy stage — so whether the scanner sees raw or redacted text depends on whether an earlier conditional ran. `check_resp` defaults to `{}` and `_input_decision` to `None`, making "the stage was skipped" indistinguishable from "the stage found nothing". Every one of those is an order-dependent bug waiting for a new branch.

#### Implementation work

Define the §10.5 types as frozen slotted dataclasses. Every stage takes a context and returns a new one; nothing mutates in place.

Define the `Category` enum as the single taxonomy. Every detector, rule and audit field draws its category from it. This is what makes the `injection` versus `prompt_injection` divergence in §10.1.3 a compile error.

Make absence explicit. There is no `None`-means-maybe: a stage that did not run yields a `Finding` with status `SKIPPED`, and a stage that could not run yields `UNAVAILABLE`. No default dictionary, no sentinel empty value.

Add the CI gate that rejects a non-frozen dataclass or a mutable default anywhere in `plan/`, `detect/`, `resolve/`.

Write the property suite: for any stage sequence, the context after stage *n* is derivable from the context after stage *n−1* and the stage's declared inputs alone.

#### Live acceptance tests — required to exit

| Test | Live procedure | PASS | FAIL / stop |
|---|---|---|---|
| LGW04-1 | Attempt to mutate a `RequestContext` field at runtime. | `FrozenInstanceError`. | Mutation succeeds. |
| LGW04-2 | Run the full stage sequence in a deliberately shuffled order where dependencies permit. | Identical outcome, or an explicit dependency error; never a silently different verdict. | Outcome depends on order. |
| LGW04-3 | Construct a `Finding` with a category not in the enum. | Rejected at construction. | Accepted as a string. |
| LGW04-4 | Skip the semantic stage entirely. | `SKIPPED` findings present and distinguishable from a clean `EXECUTED` result in the audit record. | The two are indistinguishable. |
| LGW04-5 | Property run, 10,000 generated stage sequences. | No sequence produces a verdict not derivable from declared inputs. | Any hidden dependency found. |

#### Failure scenarios that must be handled

A stage genuinely needs to modify content → it returns a `Transformation`, which `dispatch/` applies and verifies; it never edits the body itself. Two detectors report the same category → both findings are retained and the resolver decides; neither overwrites the other.

#### Exit criteria

All core types frozen. One category enum. `SKIPPED` and `UNAVAILABLE` are first-class and visible in audit. Immutability gate live. Property suite green.

#### Evidence package

Type definitions · frozen and enum gate runs · order-shuffle report · property-suite output · sample audit record showing all three statuses.

#### Rollback / stop rule

Revert the type module; nothing depends on it yet. **Stop** if a stage cannot express its work as a returned value — that stage's design is wrong and it is redesigned, not exempted.

#### Agent execution notes

Fully agent-executable. The agent must not add `unsafe_hash`, `eq=False` or a mutable field to work around immutability; those are gate violations.

---

### GW05 — Build the plan compiler, distribution and snapshot with three distinct plan states

| Field | Value |
|---|---|
| Phase | Core |
| Depends on | GW04 |
| Primary owner | Backend + control-plane engineer |
| Objective | Move all configuration interpretation off the hot path into one validated, versioned artifact, and make "unavailable" impossible to confuse with "nothing selected". |

#### Why this task exists

v1 resolves Tier-2 enablement in four separate implementations across six call paths, with opposite defaults on input and output for the same unset value. Per-org config is built as `{**platform_config, **org_data}`, so every key a tenant did not set silently takes the platform value. `PolicySync.is_loaded` is a single global flag, so org B is reported ready on org A's bundle and then evaluates against an empty rule set. These are not separate bugs; they are one missing artifact.

#### Implementation work

Build the compiler in the control plane. It takes the organization's selections, validates them against the supported detector catalogue and the declared threshold ranges, rejects unsupported combinations with an actionable error, derives `required_detectors`, and emits a versioned immutable `ExecutionPlan`. An unsupported selection fails **at save time, in the console**, not silently at request time.

Build distribution: push on change, plus a periodic reconcile so a missed notification has a bounded lifetime rather than an unbounded one. Every replica exports `plan_snapshot_age_seconds` per org.

Implement the three states of §10.5.2 as distinct types, not as an empty dictionary. `PLAN_UNAVAILABLE` and `PLAN_UNKNOWN_TENANT` cannot be constructed from a missing key; they are returned explicitly.

Make every surface — chat, MCP, RAG, vector, embeddings — consume the same pinned plan. There is exactly one `resolve_*_enabled` function in the codebase, and it is the plan lookup.

Pin the plan version once per request and carry it in the context. Input and output resolve against the same version even if a push lands mid-request. Record the version in every audit record.

#### Live acceptance tests — required to exit

| Test | Live procedure | PASS | FAIL / stop |
|---|---|---|---|
| LGW05-1 | Tenant authorized, zero optional rules selected. | Valid plan, `rules=()`; platform integrity still applies; no content detector runs. | Treated as unavailable, or platform defaults appear. |
| LGW05-2 | Make the plan store unreachable for a known tenant. | `PLAN_UNAVAILABLE`; signed not-ready behaviour; **never** another tenant's or the platform posture. | Any inherited configuration. |
| LGW05-3 | Set one rule to ENFORCE; leave every tri-state unset. | Identical resolution on input and output and on all four surfaces. | Any surface disagrees. |
| LGW05-4 | Change a rule action at 70% load. | All ready replicas converge within the declared freshness bound; `plan_snapshot_age_seconds` proves it. | Any replica serves the old plan past the bound. |
| LGW05-5 | Push a plan mid-request. | That request completes entirely on its pinned version; input and output agree. | Input and output use different versions. |
| LGW05-6 | Two tenants with opposite plans under concurrency. | No cross-tenant leakage of config, findings or version. | Any leakage. |
| LGW05-7 | Save an unsupported detector/action combination in the console. | Rejected at save with an actionable message. | Accepted and silently ignored at request time. |

#### Failure scenarios that must be handled

Compiler rejects a plan a tenant already had → the previous valid version keeps serving and the tenant is told; never fail to an empty plan. Store flaps → last-known-good serves within the freshness bound, then not-ready; never silent staleness. Two replicas hold different versions → both are valid within the bound and both are recorded.

#### Exit criteria

One compiler, one plan type, one lookup. Three states distinct and tested. All surfaces on the pinned plan. Convergence bound measured under load. Version in every audit record. Console rejects invalid selections at save.

#### Evidence package

Compiler validation matrix · three-state tests · cross-surface parity report · convergence measurement · mid-request pin proof · two-tenant isolation run.

#### Rollback / stop rule

Roll back the plan schema version; replicas serve the last valid compiled version. **Stop** if any surface needs its own enablement logic — that is the defect returning and the surface is brought onto the plan instead.

#### Agent execution notes

Agent-executable. The agent must not add a per-surface default "for compatibility"; §10.2.2 authorizes the semantic change and the ledger records it.

---

### GW06 — Build the admission layer with bounded shared state and one round trip

| Field | Value |
|---|---|
| Phase | Core |
| Depends on | GW03, GW04 |
| Primary owner | Backend engineer |
| Objective | Resolve identity, quota and kill-switch truthfully within a single shared-state round trip, with one coherent failure posture. |

#### Why this task exists

v1 performs an authentication round trip per request, a kill-switch read with no cache, an unmetered `model_state` read that is not a named stage, and a serial burst/RPM multi. Worse, one Redis fault produces opposite postures simultaneously: `rate_limiter.py:116,169,230` return allow, while `circuit_breaker.py:397-400` returns block on any exception. A single outage removes rate limiting and blocks all traffic at the same time.

#### Implementation work

Identity: RAM cache keyed by key hash, invalidated by a monotonic `auth_epoch` so revocation is bounded and does not require a per-request read. Shared store is consulted on miss only.

Quota: local GCRA for burst and rate; a shared **lease** for org-level token budget so N replicas cannot admit N× the limit. Lease chunk size derives from the `ResourceContract`, not a constant.

Kill-switch: age-bounded RAM snapshot with an explicit staleness ceiling. Past the ceiling, it is `UNAVAILABLE` and the signed posture applies. Kill-switch remains fail-closed and that is a platform-integrity property a tenant cannot disable.

Declare one failure posture table for shared state, covering identity, quota, kill-switch and plan, and implement it in one place so two components cannot disagree about the same outage.

Every shared-state operation is bounded: timeout, pool size and retry budget all derive from the contract, and every one is timed into the trace.

#### Live acceptance tests — required to exit

| Test | Live procedure | PASS | FAIL / stop |
|---|---|---|---|
| LGW06-1 | Steady load, warm caches. | ≤1 shared-state round trip per request, proven by store-side operation counters. | More than one in the steady state. |
| LGW06-2 | Revoke a key. | Rejected within the declared epoch bound on **every** replica. | Any replica still accepts past the bound. |
| LGW06-3 | Four replicas against one org quota. | Aggregate admission ≤ limit + declared lease overshoot; overshoot published. | Quota multiplies by replica count. |
| LGW06-4 | Kill the shared store entirely. | Exactly the declared posture; identical across quota, kill-switch and plan; no component disagrees. | Two components take opposite actions. |
| LGW06-5 | Inject 200 ms store latency. | Bounded timeout; declared posture; no unbounded pool growth. | Latency propagates to request latency without a bound. |
| LGW06-6 | Fail over the store. | Recovery within the declared window; posture holds throughout. | Undefined behaviour during failover. |

#### Failure scenarios that must be handled

Store slow but alive → timeout is the boundary, not liveness. Epoch bump missed → periodic reconcile bounds staleness. Lease held by a crashed replica → lease TTL reclaims; TTL derives from the contract.

#### Exit criteria

One round trip warm. Bounded revocation, proven multi-replica. No quota multiplication. One posture table, implemented once, verified under total outage. All operations timed and bounded.

#### Evidence package

Store operation counters · revocation timing per replica · four-replica quota report with overshoot · outage matrix · latency-injection run · failover run.

#### Rollback / stop rule

Revert to the previous admission revision. **Stop** if any fault produces two different postures in two components — that is the v1 defect and it is fixed before proceeding.

#### Agent execution notes

Agent-executable. The posture table is signed by a human before implementation; the agent implements it, it does not choose it.

---

### GW07 — Build the single pure resolver and prove nothing else can decide

| Field | Value |
|---|---|
| Phase | Core |
| Depends on | GW04, GW05 |
| Primary owner | Security engineer |
| Objective | Concentrate every security outcome in one pure function and make any second decision site a build failure. |

#### Why this task exists

This is the task that removes the defect class of §10.1.1. Today 283 terminal-decision sites exist across 25 modules; org intent is a severity maximum rather than a precedence (P1, P2); a degraded output scan makes the action independent of the verdict (P3, P6); MONITOR does not neutralize enforcement on output (P7); and an adapter can erase a finding before the resolver sees it.

#### Implementation work

Implement `resolve()` exactly as §10.5.3 specifies, in strict step order, with no severity maximum anywhere. Conflicts resolve by explicit `priority` with a documented tie-break.

Route `UNAVAILABLE` through the owning rule's `on_unavailable` posture. There is no generic degraded branch. This is the direct removal of P3 and P6: there is no code path in which the emitted action is computed from anything other than the findings and the plan.

Make `MONITOR` structurally non-enforcing: monitor findings are recorded and are not candidates. This removes P7.

Make the resolver pure and enforce it: no imports of I/O modules, no clock, no globals. A CI gate checks the import set of `resolve/`.

Mint `DispatchAuthorization` only for non-blocking dispositions, and make it the required argument of the provider client's entry point. BLOCK then cannot reach a provider, by type.

Add the property suite: no disposition arises that is not derivable from an ENFORCE rule; adding a MONITOR rule never changes a disposition; adding a SKIPPED finding never changes a disposition; the function is deterministic across 10,000 generated inputs.

#### Live acceptance tests — required to exit

| Test | Live procedure | PASS | FAIL / stop |
|---|---|---|---|
| LGW07-1 | Enabled semantic injection returns BLOCK, no regex rule matches. | Zero provider calls; audit names the deciding rule and model version. | Finding discarded, request allowed — the v1 adapter defect. |
| LGW07-2 | Same finding, organization action FLAG. | Provider called; finding present; transport allow + flagged. | Silently becomes BLOCK. |
| LGW07-3 | Org action ALLOW, detector recommends BLOCK. | Resolves per signed rule semantics, not a severity max. Result matches the ledger entry. | Reproduces P1. |
| LGW07-4 | Output semantic detector `UNAVAILABLE`, rule posture FAIL_CLOSED. | BLOCK. Control with FAIL_OPEN → the rule's declared action. | Any generic degraded path emits REDACT regardless of verdict — P3/P6. |
| LGW07-5 | Rule in MONITOR with a high-confidence finding. | Disposition unchanged; finding recorded. | MONITOR enforces — P7. |
| LGW07-6 | PII=REDACT and Credentials=BLOCK on one prompt. | BLOCK by priority; both findings retained; no provider call. | Redaction cancels the block, or one finding is lost. |
| LGW07-7 | Add a `status_code=403` in `dispatch/`. | CI AST gate fails. | Gate passes. |
| LGW07-8 | Property suite, 10,000 generated cases. | All four properties hold. | Any violation. |

#### Failure scenarios that must be handled

Two ENFORCE rules with equal priority → documented deterministic tie-break, recorded in the decision. A detector returns an unmapped category → construction fails at GW04; it cannot reach the resolver. A transformation cannot be applied → `dispatch/` reports failure and the request takes the rule's unmaskable path; it is never dispatched unverified.

#### Exit criteria

One resolver, pure, gated. No severity max. `UNAVAILABLE` routed per rule. MONITOR structurally non-enforcing. BLOCK unreachable by a provider, by type. All eight tests and the property suite green.

#### Evidence package

Full action matrix across disposition × mode × availability · provider call counts per case · purity and AST gate runs · property output · audit records naming deciding rules.

#### Rollback / stop rule

Revert the resolver revision. **Never** route around the resolver to restore availability — that reconstructs the defect this task exists to remove.

#### Agent execution notes

Agent-executable, and the highest-scrutiny card in the track: human review is mandatory before merge. The agent must not add a convenience branch that returns an action outside the six documented steps.

---

### GW08 — Build the detector framework and the GuardBackend interface

| Field | Value |
|---|---|
| Phase | Detection |
| Depends on | GW04, GW07 |
| Primary owner | Backend engineer |
| Objective | Make detectors uniform, replaceable and incapable of deciding anything, and make the semantic backend a deployment choice. |

#### Why this task exists

v1's detectors variously mutate the body, return HTTP responses, short-circuit the pipeline and disagree about the same concept's name. The semantic backend is bound into scanner semantics, so changing accelerator topology means editing security code — the opposite of the scalable-code doctrine in §2.1.

#### Implementation work

Define the `Detector` protocol: takes text and plan context, returns `Finding[]`, declares its `required_capabilities` and `detector_version`. It cannot import `edge/`, cannot raise HTTP errors, cannot mutate.

Define `GuardBackend` per §10.5.4 with four implementations behind it: `local_onnx` (CPU, the default for development and CI), `local_trt` (in-process TensorRT), `triton_grpc` (node-local), `remote_http` (off-box burst). Selection is deployment configuration and the detector cannot observe which is active.

Build the registry: `required_detectors` from the plan resolves to instances at snapshot load, not per request.

Implement readiness. A backend that is not ready makes its detectors `UNAVAILABLE`; it never makes them clean. Readiness includes `model_hash`, and the hash appears in every finding and every audit record.

Implement `capacity_hint()` so `runtime/resources.py` can derive guard-dependent bounds from measured service rate rather than a constant.

#### Live acceptance tests — required to exit

| Test | Live procedure | PASS | FAIL / stop |
|---|---|---|---|
| LGW08-1 | Run the identical detection suite on all four backends. | Same findings within the declared tolerance; no semantic difference attributable to topology. | Any backend changes the security outcome. |
| LGW08-2 | Swap backend by configuration only, no code change. | Works; `model_hash` changes; audit reflects it. | Requires a source edit. |
| LGW08-3 | Kill the backend mid-load. | Selected detectors go `UNAVAILABLE`; rule posture applies; zero fabricated clean results. | Any benign result from an unavailable detector. |
| LGW08-4 | Corrupt the engine cache. | Not-ready; refuses to serve those detectors; no silent CPU fallback presented as the selected model. | Silent substitution. |
| LGW08-5 | Add an HTTP raise inside a detector. | CI gate fails. | Gate passes. |
| LGW08-6 | Backend degrades to half throughput. | `capacity_hint` changes; admission bounds respond; no unbounded queue. | Bounds unchanged; queue grows. |

#### Failure scenarios that must be handled

Backend is slow rather than down → budget timeout yields `UNAVAILABLE`, which is a different finding from a clean pass. Model hash changes under a running process → treated as a new version; caches keyed by hash are invalidated.

#### Exit criteria

Detectors emit findings only. Four backends behind one interface, swappable by config. `UNAVAILABLE` never becomes clean. `model_hash` in findings and audit. `capacity_hint` feeds the contract.

#### Evidence package

Four-backend equivalence report · config-only swap · kill and corruption runs · AST gate run · degradation and bounds response.

#### Rollback / stop rule

Revert to the previously validated backend by configuration; no code change required, which is the point of the interface. **Stop** if a backend cannot express unavailability distinctly from a clean result.

#### Agent execution notes

Agent-executable. `local_onnx` is the default for CI so the track never depends on GPU availability to make progress.
### GW09 — Implement deterministic detectors with byte-verified redaction

| Field | Value |
|---|---|
| Phase | Detection |
| Depends on | GW08 |
| Primary owner | Security engineer |
| Objective | Fast, deterministic PII/secret/credential/transport detection whose redaction is verified on the bytes that actually leave, and whose false positives are scored rather than assumed. |

#### Why this task exists

Two separate v1 problems meet here. First, the decision is made against one redactor (`INPUT_SCANNER.redact_pii` on a flattened prompt) while the wire is produced by a different one (`llm_router._apply_redaction` → `patterns.redact_all`, whose own docstring calls it "by construction WEAKER"). The system can therefore decide REDACT and emit something the decision never inspected. Second, `ATTACK_PATTERNS["command_injection"]` contains `` `[^`]+` `` — any backtick pair — which we confirmed matches 5 of 5 benign inline-code prompts, and `command_injection` is outside the explanatory carve-out, so those blocks are terminal.

#### Implementation work

One canonicalization pass with a declared decode budget, then one multi-pattern match. No per-pattern loop over the text.

Exactly one redactor. The bytes the resolver inspected are the bytes `dispatch/` sends, and `dispatch/` re-verifies the transformed payload against the finding spans before the provider call. A redaction that did not remove what it claimed is a failure, not a pass.

Demote injection and command patterns from terminal block to **signal**. They contribute a finding; the plan decides the action. This is a §10.2.2 behaviour change and requires a ledger entry and a C3 score.

Score every detector against C3 and publish recall and FPR per family, with the developer-traffic family reported separately. This is where the backtick fix is proven rather than asserted.

Keep byte-verified fail-closed semantics for unmaskable content: if a transformation cannot be applied safely, the rule's unmaskable path runs; the payload is never dispatched unverified.

#### Live acceptance tests — required to exit

| Test | Live procedure | PASS | FAIL / stop |
|---|---|---|---|
| LGW09-1 | Five benign inline-code prompts from the developer-traffic family. | Zero terminal blocks; findings present as signal only. | Any terminal block — the v1 defect survives. |
| LGW09-2 | PII fixture with REDACT, streaming and non-streaming. | Recorder shows sanitized bytes; raw value absent from provider payload, logs, traces and error bodies. | Raw value anywhere. |
| LGW09-3 | Secret split across a chunk and a UTF-8 boundary. | No leak; protocol stays valid. | Leak or malformed frame. |
| LGW09-4 | Obfuscated/encoded PII fixtures. | Detected within the declared decode budget, or explicitly rejected on budget. | Silent miss. |
| LGW09-5 | Transformation deliberately made a no-op. | Detected pre-dispatch; unmaskable path runs; nothing dispatched. | Dispatched as if redacted. |
| LGW09-6 | C3 scoring run. | Recall/FPR per family published; developer-traffic FPR materially below v1's. | Not measured, or no improvement. |

#### Failure scenarios that must be handled

Decode budget exhausted → explicit reject, never silent truncation. Pattern is a superset prefilter → verified by the precise matcher before a finding is emitted. Fix reduces recall on a real family → ledger records the trade and it is signed, not absorbed.

#### Exit criteria

One canonicalization, one matcher, one redactor. Wire bytes verified against spans. Injection demoted to signal with ledger entry and C3 score. Per-family recall/FPR published.

#### Evidence package

C3 scorecard per family · wire captures for redact cases · boundary tests · no-op detection run · ledger entries · decode-budget report.

#### Rollback / stop rule

Revert the detector ruleset version; rulesets are versioned and hashed. **Stop** if the wire bytes cannot be verified against the spans the decision used.

#### Agent execution notes

Agent-executable. The agent must not tune a pattern to make a corpus case pass; corpus scores are outcomes, not targets.

---

### GW10 — Implement semantic detection, windowing and the per-request FPR budget

| Field | Value |
|---|---|
| Phase | Detection |
| Depends on | GW08 |
| Primary owner | ML/security engineer |
| Objective | Add semantic injection detection with a published, per-request false-positive rate and no silent truncation of long inputs. |

#### Why this task exists

The model's published operating point is per window, not per request. A model quoted at 1% FPR at 512 tokens compounds across windows: at two windows a request sees 1 − 0.99² ≈ **1.99%**, and at seven windows ≈ **6.79%**. In an ENFORCE posture at 1,000 RPS that is roughly 20 false blocks per second at two windows. This number has never been published alongside the latency figure, and §10.2.2 requires it before any customer-facing claim.

#### Implementation work

Tokenizer-aware windowing with declared overlap. Window count is a function of measured characters-per-token for the actual traffic, not the 4.0 rule of thumb — measured values in this corpus are 3.04 for markdown and 6.28 for prose, so a 1,024-token band is about 3,158 characters of markdown, not 4,096.

Batch windows into one backend call; never loop serially. Aggregate by the declared rule, and record the window count on every request.

Implement the FPR budget: the plan declares a per-request FPR ceiling; the resolver's confidence threshold is derived from it and the actual window count. Exceeding the ceiling is a recorded condition with a declared behaviour, not a surprise.

Handle over-length inputs explicitly: either every window is covered, or the request is rejected on length with a clear error. Head-and-tail truncation that silently drops the middle is prohibited — that is v1's `_head_tail` behaviour at `bedrock_scanner.py:63-72` and it is a silent hole.

Publish multilingual behaviour separately. Published multilingual AUC is materially below English, and this is a Mumbai-region deployment.

#### Live acceptance tests — required to exit

| Test | Live procedure | PASS | FAIL / stop |
|---|---|---|---|
| LGW10-1 | Attack in the first, middle and last window of a long input. | All three detected, or explicit length rejection. | Middle-window miss. |
| LGW10-2 | Measure FPR at 1, 2, 4 and 7 windows on the benign corpus. | Measured curve published and within the declared ceiling. | Not measured, or ceiling exceeded silently. |
| LGW10-3 | Batch versus serial at equal window count. | Batched; p99 within budget; serial path absent. | Serial loop on the hot path. |
| LGW10-4 | Multilingual corpus. | Per-language recall/FPR published. | English-only claim. |
| LGW10-5 | Input exceeding the maximum window count. | Explicit reject with an actionable error. | Silent truncation. |
| LGW10-6 | Backend timeout mid-batch. | `UNAVAILABLE`; rule posture applies. | Partial results presented as complete. |

#### Failure scenarios that must be handled

Window count varies with tokenizer version → tokenizer hash is pinned and recorded. FPR ceiling forces a threshold that collapses recall → the trade is published and signed, never silently resolved in favour of the latency headline.

#### Exit criteria

Windowing measured, not assumed. Per-request FPR published at every deployed window count. Batched inference. No silent truncation. Multilingual published separately.

#### Evidence package

Window-count distribution from real traffic · FPR curve · batch-versus-serial latency · multilingual scorecard · over-length rejection · tokenizer and model hashes.

#### Rollback / stop rule

Disable semantic rules by plan; deterministic detection continues. **Stop** if the per-request FPR at the deployed window count cannot be published.

#### Agent execution notes

Agent-executable against `local_onnx`. GPU-specific throughput belongs to GW20; this card is about correctness and the FPR budget.

---

### GW11 — Build provider dispatch with verified transformation and no firewall-initiated generation

| Field | Value |
|---|---|
| Phase | Dispatch |
| Depends on | GW07 |
| Primary owner | Backend engineer |
| Objective | One place where a provider is called, reachable only with authorization, sending only verified bytes. |

#### Why this task exists

v1 has multiple dispatch paths with independently assembled arguments, and the output rewrite helper calls `LLM_ROUTER.acompletion` on the guard's behalf — a model call the customer did not request, made by the firewall. Separately, `reload_models_now` runs on the request path and mutates the shared router's model list under a per-org lock, which the function's own docstring records as having previously caused `422 no_provider_configured` under concurrency.

#### Implementation work

One `ProviderClient` entry point, requiring a `DispatchAuthorization` that only `resolve/` mints. Routing is deterministic from the pinned plan; no adjudicator call, no shared mutable router state on the request path.

Apply `Decision.transformations` and verify the result against the finding spans before the call. Verification failure is terminal.

Prohibit firewall-initiated generation: `dispatch/` is callable only from the request path. REWRITE, where supported, runs through `GuardBackend` against a local model.

Catalogue resolution is a snapshot, like the plan. An empty catalogue is an explicit provisioning error distinguishable from a control-plane outage, so a store blip never surfaces to a tenant as "connect your provider".

Bound the connection pool from the `ResourceContract` and export pool wait time.

#### Live acceptance tests — required to exit

| Test | Live procedure | PASS | FAIL / stop |
|---|---|---|---|
| LGW11-1 | Attempt dispatch without an authorization value. | Type error at build time; no runtime path exists. | Runtime bypass possible. |
| LGW11-2 | BLOCK decision. | Recorder shows zero calls. | Any call. |
| LGW11-3 | REDACT decision. | Recorder shows sanitized bytes; verification logged. | Raw bytes, or unverified dispatch. |
| LGW11-4 | REWRITE with the local model. | Zero provider calls attributable to the firewall. | Firewall-initiated generation. |
| LGW11-5 | Empty catalogue versus store outage. | Two distinct errors; neither says "connect your provider" for an outage. | Conflated. |
| LGW11-6 | 500 concurrent requests across 20 orgs. | Zero spurious provisioning errors; no shared-state corruption. | Any `no_provider_configured` not caused by actual absence. |

#### Failure scenarios that must be handled

Provider returns a malformed body → protocol error, not success. Provider slow → bounded timeout from the contract. Pool exhausted → bounded queueing with backpressure, never an exception storm.

#### Exit criteria

Single authorized entry point. Verified transformations. No firewall-initiated generation. Snapshot catalogue. Bounded, instrumented pool.

#### Evidence package

Type-level authorization proof · recorder call counts per disposition · verification logs · concurrency run · error-distinction tests · pool metrics.

#### Rollback / stop rule

Revert the dispatch revision. **Stop** if any code path can reach a provider without authorization.

#### Agent execution notes

Agent-executable. The `DispatchAuthorization` type must not be constructible outside `resolve/`; the agent must not add a public constructor for convenience.

---

### GW12 — Build the SSE egress pipeline with bounded buffers, backpressure and real cancellation

| Field | Value |
|---|---|
| Phase | Protocol |
| Depends on | GW03, GW11 |
| Primary owner | Protocol engineer |
| Objective | A streaming implementation whose memory is bounded, whose cancellation is real, and whose frames the official SDKs parse. |

#### Why this task exists

v1's rewrite path returns without clearing the buffer on every non-DONE flush, so the buffer accumulates the whole response and each flush re-inspects all of it — memory linear in stream length and O(N²) guard cost. Client disconnect is polled and ends the generator but never aborts the upstream request, so a stalled provider is never noticed and inference continues for a client that has gone. There is no backpressure anywhere.

#### Implementation work

One SSE state machine producing frames the official SDKs parse, including the terminal marker and the error frame shape.

One bounded coalescer. High-water mark from the `ResourceContract` as a function of active stream count and memory limit. Exceeding it applies backpressure; it never grows.

Credit-based flow control: a slow consumer slows upstream reads. Memory is bounded by active streams × ceiling, and that product is a derived, exported number.

Real cancellation: client disconnect cancels the provider request and any in-flight guard work within a bounded interval, and the interval is measured.

Never splice a fallback into a stream that has already emitted content. Before the first byte a signed retry may run; after it, the only truthful outcomes are clean termination or a declared error frame.

Export per-request output-detector invocation count, release lag and buffer high-water.

#### Live acceptance tests — required to exit

| Test | Live procedure | PASS | FAIL / stop |
|---|---|---|---|
| LGW12-1 | 1,000 concurrent streams, 4,000-token responses. | Memory plateaus at the derived bound; no growth with stream length. | Linear growth. |
| LGW12-2 | Consumer reads at 1 chunk/second. | Backpressure upstream; bounded buffer; no fleet memory growth. | Unbounded buffering. |
| LGW12-3 | Client disconnects after N chunks. | Provider and guard work stop within the bound; resources return to baseline. | Work continues. |
| LGW12-4 | Provider fails before first content. | Only the signed safe retry runs. | Unsafe retry. |
| LGW12-5 | Provider fails after partial delivery. | Clean termination; no spliced fallback. | Two responses concatenated. |
| LGW12-6 | Malformed upstream SSE. | Protocol error or declared degraded outcome; never success. | Reported as success. |
| LGW12-7 | Full conformance suite over real TCP. | All streaming tests pass, both SDKs. | Any failure. |
| LGW12-8 | Measure output-detector invocations per request. | Matches the plan-derived schedule; recorded. | Per-flush invocation as in v1. |

#### Failure scenarios that must be handled

Client vanishes without TCP close → idle timeout from the contract. Provider stalls mid-stream → bounded inter-chunk timeout. Guard slower than the stream → backpressure, not buffering.

#### Exit criteria

Bounded memory proven at 1,000 streams. Backpressure demonstrated. Cancellation measured. No post-byte splicing. Conformance green over real TCP. Invocation count matches schedule.

#### Evidence package

Memory plateau chart · slow-consumer run · cancellation timing · failure-injection matrix · TCP conformance run · release-lag and high-water distributions.

#### Rollback / stop rule

Revert the egress revision. **Stop** if memory cannot be bounded independently of response length.

#### Agent execution notes

Agent-executable. Buffer sizes come from `ResourceContract`; the agent must not introduce a literal high-water mark.

---

### GW13 — Implement output enforcement and the two explicit streaming modes

| Field | Value |
|---|---|
| Phase | Protocol |
| Depends on | GW07, GW12 |
| Primary owner | Security + protocol engineer |
| Objective | Make the output path use the same resolver as the input path, and make the two streaming modes honestly distinct at the wire and in the latency claim. |

#### Why this task exists

v1's output path has its own enforcement function with its own downgrade rules (P3, P6, P7), and a streaming "block" is a truncation: `secure_streaming.py:551-570` emits an error frame after earlier flushes have already reached the client. An operator who selected BLOCK received partial delivery. §1.2 already states that strict withholding and incremental streaming are different products; this card makes the code agree.

#### Implementation work

Output uses `resolve()` with `phase=OUTPUT`. There is no `enforce_output`; the same six steps apply, so a degraded output scan can no longer make the action independent of the verdict.

Implement `INCREMENTAL`: detectors run on a content-defined schedule; BLOCK is expressible only before the first content byte; after that the truthful outcomes are terminate-with-declared-error or redaction of not-yet-released content.

Implement `STRICT_WITHHOLD`: no content released until the whole response is inspected. It is selected per plan, and its latency profile is qualified **separately** — it may not inherit the incremental TTFT claim.

Make the mode visible: the response carries which mode produced it, and the console shows the tenant which they selected and what it costs.

Qualify each mode independently: release lag and first-content delay for incremental, completion overhead for strict.

#### Live acceptance tests — required to exit

| Test | Live procedure | PASS | FAIL / stop |
|---|---|---|---|
| LGW13-1 | Unsafe whole response, `STRICT_WITHHOLD`. | Zero prohibited assistant bytes before the terminal result. | Any prohibited byte. |
| LGW13-2 | Unsafe content mid-stream, `INCREMENTAL`. | Declared termination semantics; no claim that an already-delivered prefix was blocked. | Truncation presented as a block. |
| LGW13-3 | Output BLOCK with the semantic detector `UNAVAILABLE`. | Rule's `on_unavailable` posture; never a blanket redact. | Reproduces P3/P6. |
| LGW13-4 | Output rule in MONITOR. | Content delivered; finding recorded. | MONITOR enforces — P7. |
| LGW13-5 | Latency for both modes at the same load. | Two separate profiles published; strict never quoted as incremental. | One number for both. |
| LGW13-6 | Secret spanning a chunk boundary in both modes. | No leak in either. | Leak in either. |

#### Failure scenarios that must be handled

Provider emits everything in one chunk → incremental degenerates to strict for that response and the trace says so. Detector slower than generation → backpressure from GW12, not buffer growth.

#### Exit criteria

One resolver for both phases. Two modes implemented, selectable, visibly distinct. Separate latency qualification. No truncation-as-block. Boundary safety in both.

#### Evidence package

Wire captures for both modes · UNAVAILABLE matrix · MONITOR run · two latency profiles · boundary tests · mode-visibility screenshots.

#### Rollback / stop rule

Revert to `STRICT_WITHHOLD` as the safe default for affected tenants. **Stop** if BLOCK cannot be made truthful in incremental mode — then incremental does not offer BLOCK, and that is documented rather than faked.

#### Agent execution notes

Agent-executable. The agent must not implement BLOCK-after-first-byte as truncation; if the mode cannot support it, the mode declares it unsupported.

---

### GW14 — Build async bounded audit and the honest timing instrument

| Field | Value |
|---|---|
| Phase | Observability |
| Depends on | GW04, GW12 |
| Primary owner | Backend engineer |
| Objective | Exactly one truthful decision record per phase, produced without blocking the request, and a timing instrument that cannot report the provider's latency as ours. |

#### Why this task exists

Two defects meet here. The timer starts at `main.py:7085` inside the handler, after auth middleware has already run, so `stage_metrics["auth_ms"]` at `:7691` measures body parsing rather than authentication. And `model_output_ms` is derived by subtraction — `stream_orchestration.py:683` computes `duration_ms − ttft_ms`, explicitly excluding TTFT — so provider time-to-first-token lands in `overhead_ms` and is reported as firewall overhead. Separately, `redis_log_handler.py` performs a synchronous Redis `publish` inside `logging.Handler.emit()`, and `main.py:6716-6730` sets the `gateway` logger to DEBUG, so every log line blocks the event loop.

#### Implementation work

Start the request clock in `edge/` before identity resolution. Timestamp provider-stream open and close directly; never derive `model_output_ms` by subtraction.

Record the reconciliation residual `wall − Σstages − provider` **signed**. Export it as a histogram and fail CI at a non-zero p50. A clamp to zero is prohibited; it is what hid this class of error in v1.

Separate `T_input`, `T_release_lag`, `T_finalize` and `T_fw_addon` per §1.2, each measured, none derived from the others.

Emit exactly one `DecisionRecord` per phase, carrying plan version, deciding rules, all findings including SKIPPED and UNAVAILABLE, detector and model hashes, and the transformation verification result.

Audit is a bounded async producer. On backpressure it applies a declared policy — spill or drop — and **counts** what it did; `audit_completeness_ratio` is exported and loss is never assumed zero.

Replace the synchronous log publisher with the same bounded async producer, and restore the gateway logger to INFO.

#### Live acceptance tests — required to exit

| Test | Live procedure | PASS | FAIL / stop |
|---|---|---|---|
| LGW14-1 | Recorder configured with 2,000 ms TTFT and zero firewall work. | `T_fw_addon` ≈ 0. | Addon tracks TTFT — the v1 defect. |
| LGW14-2 | Inject 5 ms auth, 5 ms input, 7 ms output delays. | Each appears in its own metric, within tolerance. | Any lands in the wrong bucket. |
| LGW14-3 | Inject a 30 ms mid-stream hold. | Appears in release lag; a sub-20 ms claim fails. | Hidden in provider time. |
| LGW14-4 | Force a reconciliation mismatch. | Signed residual recorded; CI fails. | Clamped to zero. |
| LGW14-5 | Stall the audit sink. | Bounded queue; declared policy; loss counted; gateway stable. | Unbounded growth, or silent loss. |
| LGW14-6 | Measure event-loop lag with logging at INFO and DEBUG. | No synchronous publish on the request path in either. | Blocking publish present. |
| LGW14-7 | Join 10,000 records against provider and client bytes. | One record per phase; all joins resolve. | Duplicate, missing or contradictory records. |

#### Failure scenarios that must be handled

Sink unavailable at startup → gateway starts, records queue, completeness is reported. Clock adjusted mid-request → monotonic source; injectable for tests.

#### Exit criteria

Clock starts pre-identity. Provider time measured. Signed residual with a CI gate. Four §1.2 metrics separately measured. One record per phase. Bounded audit with counted loss. No synchronous logging on the request path.

#### Evidence package

TTFT-independence run · injected-delay attribution · mid-stream hold detection · residual histogram · sink-stall run · loop-lag comparison · join report.

#### Rollback / stop rule

Revert the instrument revision; measurement stops until it is restored. **Stop** all performance claims if the residual p50 is non-zero — every number produced under a broken instrument is void.

#### Agent execution notes

Agent-executable, and a prerequisite for every performance card. No GW20 measurement may be reported before this card exits.

---

### GW15 — Ship the chat surface on v2 and pass the full conformance suite

| Field | Value |
|---|---|
| Phase | Surface |
| Depends on | GW01, GW11, GW13, GW14 |
| Primary owner | Backend + protocol engineer |
| Objective | The first complete request path end to end on v2, satisfying the one frozen contract. |

#### Why this task exists

This is where the architecture stops being a diagram. Every layer is exercised by real traffic through the official SDKs, and the base-URL-swap promise becomes testable.

#### Implementation work

Implement `/v1/chat/completions` streaming and non-streaming, `/v1/completions` and `/v1/responses`, as thin `edge/` adapters over the shared pipeline. Route-specific logic lives in `edge/wire/`; security logic lives nowhere in `edge/`.

Wire the full lifecycle of §10.4, including the pre-identity clock and the one shared-state round trip.

Run C1 against v2 in-process and over real TCP, Python and Node.

Run the base-URL-swap acceptance directly: take an unmodified application written against OpenAI, change `base_url` and the key, and run it.

#### Live acceptance tests — required to exit

| Test | Live procedure | PASS | FAIL / stop |
|---|---|---|---|
| LGW15-1 | Full C1 suite, in-process and over TCP, Python and Node. | 100% pass, no `xfail`. | Any failure or workaround. |
| LGW15-2 | Unmodified OpenAI application, base URL and key swapped only. | Works unchanged, streaming and non-streaming. | Any code change needed — the frozen contract is broken. |
| LGW15-3 | S01–S07 from §5 against v2. | All pass. | Any failure. |
| LGW15-4 | Two-tenant opposite-policy concurrency. | No leakage of config, findings, request IDs or audit. | Any leakage. |
| LGW15-5 | Tool-call streaming with fragmented arguments. | SDK reconstructs the exact call. | Corruption. |
| LGW15-6 | Replay 10,000 C2 chat requests through v1 and v2. | Only IDENTICAL and ledger-registered EXPECTED. | Any UNEXPECTED or WIRE. |

#### Failure scenarios that must be handled

An SDK version newer than the pin behaves differently → the pin is the contract; a bump is a deliberate change with a full re-run. A C2 shape v2 has not seen → it is a coverage finding, and it blocks GW21 rather than being waived.

#### Exit criteria

C1 100% on both SDKs and both transports. Base-URL swap proven with an unmodified application. §5 S01–S07 pass. Tenant isolation proven. C2 chat replay clean.

#### Evidence package

Conformance runs · unmodified-application transcript · scenario results · isolation run · tool-call captures · replay report.

#### Rollback / stop rule

v2 serves no production traffic; failures are development state. **Stop** if the base-URL swap requires any client change.

#### Agent execution notes

Agent-executable. This is the first card whose completion is externally meaningful; human review before it is marked done.

---

### GW16 — Ship embeddings, models, moderations and legacy completions

| Field | Value |
|---|---|
| Phase | Surface |
| Depends on | GW15 |
| Primary owner | Backend engineer |
| Objective | Complete the OpenAI-compatible surface on the same plan and resolver. |

#### Why this task exists

`/v1/embeddings` in v1 runs Tier-1 only, by design, with no semantic option — a gap invisible from the console because the plan and the surface disagree about what is available. In v2 the plan is authoritative for every surface, so a surface either supports a rule or the compiler rejects selecting it.

#### Implementation work

Implement `/v1/embeddings` on the shared pipeline, with the same plan, the same detectors and the same resolver. Batch inputs are scanned per item with per-item findings.

Implement `/v1/models` and `/v1/moderations` from the pinned catalogue and the plan.

Implement legacy `/v1/completions` through the same path.

Where a surface genuinely cannot support a rule, the **compiler** rejects selecting it, with an actionable message, rather than the surface silently ignoring it.

#### Live acceptance tests — required to exit

| Test | Live procedure | PASS | FAIL / stop |
|---|---|---|---|
| LGW16-1 | Embeddings batch with PII in item 3 of 10. | Per-item findings; only item 3 transformed; recorder confirms. | Whole batch redacted, or item missed. |
| LGW16-2 | Select a semantic rule scoped to embeddings. | Either it runs, or the compiler rejected it at save. | Accepted and silently ignored. |
| LGW16-3 | C1 embeddings and models tests. | Pass on both SDKs. | Any failure. |
| LGW16-4 | Legacy completions with a firewall block. | Correct error envelope; zero provider calls. | Wrong shape, or a call made. |
| LGW16-5 | C2 replay for these surfaces. | IDENTICAL or ledger-registered EXPECTED only. | Any UNEXPECTED. |

#### Failure scenarios that must be handled

Very large batch → bounded by the contract with a clear error, never partial silent processing. Model catalogue empty → the GW11 distinction between provisioning and outage applies here too.

#### Exit criteria

All surfaces on the shared pipeline. Per-item embedding findings. Compiler rejects unsupported scopes. C1 green. C2 replay clean.

#### Evidence package

Per-item wire captures · compiler rejection transcript · conformance runs · replay report.

#### Rollback / stop rule

Surfaces ship independently; an incomplete surface stays on v1 until the cutover. **Stop** if a surface needs its own enablement logic.

#### Agent execution notes

Fully agent-executable and parallelizable with GW17 and GW18.
### GW17 — Ship the MCP surface on the same plan and resolver

| Field | Value |
|---|---|
| Phase | Surface |
| Depends on | GW15 |
| Primary owner | Backend + security engineer |
| Objective | Bring 6,250 lines of independently-evolved MCP proxy onto the shared plan and resolver without losing its genuinely good isolation work. |

#### Why this task exists

`mcp_proxy.py` is 6,250 lines with a single 1,016-line handler and 72 block-decision sites. It has its own Tier-2 enablement requiring **two** independent flags (`tier2_ctrl.enabled` **and** `mcp_tier2_enabled`), its own scan orchestrator with a duplicated resolver, and its own floor logic. Its default Tier-1 detection is also effectively off: `_mcp_default_detection_enabled` reads a config key that `config.py` never sets, so it falls through to an environment variable defaulting to OFF.

The isolation work, by contrast, is good and must survive: per-org sandboxes via the broker, SSRF DNS-resolution checks against loopback, link-local and metadata ranges, host allowlists, command allowlists for stdio, and body caps.

#### Implementation work

Re-implement the MCP surface as an `edge/` adapter: JSON-RPC and REST framing in `edge/mcp.py`, all security through the shared plan, detectors and resolver. Tool arguments and tool results both become scanned text with findings; the plan decides the action.

Collapse the double gate. One plan, one enablement, identical to chat.

Port sandbox, broker, SSRF and allowlist logic substantially as-is. It is the strongest isolation work in the repository. What changes is that its decisions become findings routed to the resolver rather than independent block sites.

Close the known gap: tool-description prompt injection is explicitly not covered in v1. In v2 tool descriptions are scanned text like any other, and whether that produces an action is the plan's decision.

Keep per-org sandbox isolation and prove it under concurrency.

#### Live acceptance tests — required to exit

| Test | Live procedure | PASS | FAIL / stop |
|---|---|---|---|
| LGW17-1 | Identical injection fixture via chat and via MCP tool arguments. | Identical disposition under an identical plan. | Surfaces disagree. |
| LGW17-2 | Enable one semantic rule; make no other change. | MCP scanning active — no second flag required. | Still requires a second enable. |
| LGW17-3 | SSRF attempts at loopback, link-local and cloud metadata. | All blocked; parity with v1 or better. | Any regression in isolation. |
| LGW17-4 | Two orgs, concurrent tool calls to the same upstream. | Separate sandboxes; no credential, result or audit leakage. | Any leakage. |
| LGW17-5 | Tool description carrying an injection payload. | Scanned; finding emitted; plan decides. | Unscanned as in v1. |
| LGW17-6 | Secret split across two tool-result blocks. | Detected; parity with v1's split-secret floor. | Regression. |
| LGW17-7 | C2 MCP replay. | IDENTICAL or ledger-registered EXPECTED only. | Any UNEXPECTED. |

#### Failure scenarios that must be handled

Upstream MCP server unreachable → declared error, never a fabricated empty tool list. Sandbox exhausted → bounded queueing and a clear error; never direct dialling as a fallback.

#### Exit criteria

MCP on the shared plan and resolver. Single enablement. Isolation at parity or better. Tool descriptions scanned. Concurrency isolation proven. Replay clean.

#### Evidence package

Cross-surface parity report · single-flag proof · SSRF matrix · two-org concurrency run · description-scan results · split-secret tests · replay report.

#### Rollback / stop rule

MCP stays on v1 until its replay is clean; surfaces cut over together at GW23 but are developed independently. **Stop** if any isolation control regresses.

#### Agent execution notes

Agent-executable, largest single porting card. Isolation code is ported, not rewritten; a rewrite here is a security risk with no architectural payoff.

---

### GW18 — Ship RAG and vector surfaces on the same plan and resolver

| Field | Value |
|---|---|
| Phase | Surface |
| Depends on | GW15 |
| Primary owner | Backend + security engineer |
| Objective | Close the retrieved-content gaps and remove the last default-on external AI call. |

#### Why this task exists

Three concrete gaps. `/v1/vector/query` cannot run semantic scanning at all — `_RAG_GUARDRAIL_KEYS` at `vector_routes.py:210-215` copies four keys and `rag_tier2_enabled` is never among them — and it also lacks the PII and injection backstops that `/v1/rag/query` has, so retrieved documents are returned without either. Second, `rag_generator_enabled` and `rag_ranker_enabled` both default false, so the stages holding retrieved-content redaction do not run by default. Third, `llm_judge.py:166` sends **raw, un-redacted** query text to an external model, is on by default at `main.py:6674`, and fails open to a regex fallback.

#### Implementation work

Bring RAG ingest, RAG query, vector query, vector upsert and retrieved-chunk handling onto the shared plan, detectors and resolver.

Make retrieved content first-class scanned text. A document returned from a vector store is untrusted input and is scanned before it enters a prompt or reaches a client, on every RAG and vector path, not only on the one that has backstops today.

Remove `llm_judge` and the Titan grounding embedder. Grounding, if selected, runs through `GuardBackend` against a local model. This is the last default-on external AI path and its removal is a §1 locked position, not an optimization.

Bring the cross-tenant collection guard forward; it is sound. Fix the empty-`org_slug` path that coerces to `"default"` at `pipeline.py:490-494`, which evaluates one tenant against another's compiled policies — the same defect class as §10.1.5, on a different surface.

#### Live acceptance tests — required to exit

| Test | Live procedure | PASS | FAIL / stop |
|---|---|---|---|
| LGW18-1 | Poisoned document in the store; retrieve via `/v1/rag/query` and `/v1/vector/query`. | Identical handling on both; injection detected on both. | Vector path unprotected, as in v1. |
| LGW18-2 | PII in a retrieved chunk, both paths. | Redacted before reaching prompt or client on both. | Raw PII returned. |
| LGW18-3 | Deny all external AI egress; run every RAG path. | Zero attempted external calls; all paths function. | Any attempt. |
| LGW18-4 | Token with empty `org_slug`. | Explicit error; never evaluated against another org's policies. | Coerced to default. |
| LGW18-5 | Cross-tenant collection name. | Blocked; parity with v1. | Regression. |
| LGW18-6 | Enable a semantic rule scoped to retrieval. | Active on every retrieval path. | Any path unaffected. |
| LGW18-7 | C2 RAG and vector replay. | IDENTICAL or ledger-registered EXPECTED only. | Any UNEXPECTED. |

#### Failure scenarios that must be handled

Vector provider unreachable → declared error, never empty results presented as a clean search. Very large retrieved set → bounded by the contract with a clear error.

#### Exit criteria

All RAG and vector paths on the shared plan. Retrieved content scanned everywhere. Zero external AI. Tenant coercion removed. Replay clean.

#### Evidence package

Cross-path parity · redaction captures · egress-deny run · empty-org test · cross-tenant tests · replay report.

#### Rollback / stop rule

RAG and vector stay on v1 until replay is clean. **Stop** if grounding cannot be delivered locally — then grounding is unsupported and the compiler rejects selecting it, rather than quietly calling a cloud model.

#### Agent execution notes

Agent-executable. Removing `llm_judge` also removes the RAG path's only semantic layer in v1; the local replacement lands **before** the removal, never after.

---

### GW19 — Implement admission control, overload semantics and graceful drain

| Field | Value |
|---|---|
| Phase | Hardening |
| Depends on | GW06, GW12 |
| Primary owner | Platform engineer |
| Objective | Make behaviour under overload explicit and bounded rather than emergent. |

#### Why this task exists

v1 has no admission control. `--worker-connections 20000` is silently inert because `uvicorn.workers.UvicornWorker` does not read it, and rate limits bound arrival rate, not concurrency. The only `asyncio.Semaphore` in the codebase is in the stdio adapter. Under overload the system therefore has no defined behaviour: queues grow, p99 collapses, memory climbs.

#### Implementation work

Concurrency admission derived from the `ResourceContract` and the measured guard service rate — not a connection count, which is not the scarce resource.

Explicit overload response: a declared status with `Retry-After` and a request id, emitted **before** latency collapses, with a measured `q_safe` threshold.

Bounded queues everywhere — request, guard, dispatch, egress, audit — each with an exported depth and age.

Graceful drain: on `SIGTERM`, stop accepting, finish in-flight streams within a bounded window, flush audit, then exit. Streams exceeding the window get declared termination, not a truncated frame.

Remove the inert `--worker-connections` and migrate off the deprecated worker class in the same change, so the cap is demonstrably effective rather than cosmetic.

#### Live acceptance tests — required to exit

| Test | Live procedure | PASS | FAIL / stop |
|---|---|---|---|
| LGW19-1 | Offer 3× measured `q_safe`. | Explicit shedding; p99 for admitted requests stays within budget; no OOM. | Unbounded queueing or OOM. |
| LGW19-2 | Ramp arrival rate open-loop to failure. | Highest passing and first failing rate both recorded. | Only the passing point recorded. |
| LGW19-3 | `SIGTERM` during 500 active streams. | Bounded drain; audit flushed; declared termination past the window. | Truncated frames or lost audit. |
| LGW19-4 | Verify the concurrency cap is effective. | Cap binds and is observable. | Cap is inert, as in v1. |
| LGW19-5 | Guard service rate halved mid-load. | Admission responds; queue age bounded. | Queue grows unbounded. |
| LGW19-6 | Slow consumers on 50% of streams. | Backpressure; bounded memory; fast consumers unaffected. | Fleet-wide degradation. |

#### Failure scenarios that must be handled

Shedding threshold too aggressive → tuned from measured service rate, not guessed. Drain window too short → declared and measured, and the number is published.

#### Exit criteria

Admission from measured capacity. Explicit overload response. All queues bounded and exported. Drain bounded and proven. Cap demonstrably effective.

#### Evidence package

3× overload run · open-loop sweep with both points · drain run · cap-effectiveness proof · degradation response · queue metrics.

#### Rollback / stop rule

Revert admission parameters; bounds are versioned with the image. **Stop** if overload produces OOM or unbounded queue growth.

#### Agent execution notes

Agent-executable. `q_safe` is measured in GW20 and fed back here; the agent must not hardcode a provisional value.

---

### GW20 — Measure one v2 serving unit and prove horizontal scaling

| Field | Value |
|---|---|
| Phase | Performance |
| Depends on | GW15–GW19 |
| Primary owner | Performance engineer |
| Objective | Establish what one v2 unit does, and prove that adding units adds capacity without changing the application. |

#### Why this task exists

Every RPS-per-vCPU figure in this project's history is derived, and they span 1,700× — from 0.17 measured today to a struck 520 planning figure. This card replaces all of them with a measurement taken through the GW14 instrument.

#### Implementation work

Measure one unit under the signed full-security profile with unique prompts: p50 and p99 of `T_fw_addon`, `T_release_lag`, `T_finalize`; qualified RPS; CPU ms per request from cgroup accounting, not `docker stats`; RPS per allocated serving vCPU with a complete denominator including guard CPU; RPS per accelerator; guard queue time; `q_safe`.

Use open-loop arrival-rate load generation. A closed-loop client slows its own offered rate as the server slows and hides overload. Record schedule drops and prove the generator is not the bottleneck.

Sweep CPU allocation across at least four points, holding plan, workload, guard capacity and output mode fixed. Report the highest repeatable passing rate **and** the first repeatable failing rate.

Then scale 1 → 2 → 4 units with the identical image and configuration contract and measure capacity growth and the binding constraint at each step.

Report the three efficiency denominators separately and never interchangeably: RPS per allocated serving vCPU, RPS per purchased node vCPU, and requests per consumed CPU-second.

#### Live acceptance tests — required to exit

| Test | Live procedure | PASS | FAIL / stop |
|---|---|---|---|
| LGW20-1 | Single-unit sweep, full profile, unique prompts. | p99 `T_fw_addon` < 20 ms at the qualified rate; both boundary points recorded. | Budget missed with no published gap. |
| LGW20-2 | Reconciliation residual across the run. | p50 zero; distribution published. | Non-zero — the run is void. |
| LGW20-3 | 1 → 2 → 4 units. | Material capacity growth; binding constraint named at each step; zero application changes. | Sub-linear with no named constraint, or a code change needed. |
| LGW20-4 | Same image at 2, 4, 8, 16 vCPU. | Four distinct configurations from the contract; capacity tracks. | Any constant value. |
| LGW20-5 | Loadgen headroom check. | Generator sustains the schedule; drops recorded as zero. | Generator is the bottleneck. |
| LGW20-6 | Compare against v1 on identical hardware and workload. | Both measured through the same instrument; difference attributed. | Only v2 measured. |

#### Failure scenarios that must be handled

Guard saturates before CPU → named as the binding constraint and reported, not worked around. Scaling is sub-linear → locate the centralized bottleneck before adding hardware; that is the finding.

#### Exit criteria

Single-unit numbers through the honest instrument. Three denominators separate. Open-loop with zero drops. 1→2→4 proven with no code change. v1 comparison on identical hardware.

#### Evidence package

Sweep with both boundary points · residual distribution · scaling table with binding constraints · four-point contract table · loadgen headroom · side-by-side comparison · raw data for independent recomputation.

#### Rollback / stop rule

No rollback — measurement only. **Stop** and void every number if GW14's residual is non-zero at any point in the run.

#### Agent execution notes

Agent-executable. The agent must not report a rate that passed once; the criterion is the highest **repeatable** rate, and the adjacent failing point is part of the result.

---

### GW21 — Run shadow execution to ledger closure

| Field | Value |
|---|---|
| Phase | Migration |
| Depends on | GW20, GW02 |
| Primary owner | Backend lead + QA |
| Objective | Prove on live-shaped traffic that v2 differs from v1 only where the ledger says it should. |

#### Why this task exists

This is the card that makes a single cutover defensible. Replay covers shapes; shadow covers concurrency, tenant mix, plan updates under load and real resource curves — everything a replay cannot reproduce.

#### Implementation work

Run v2 in shadow: v1 serves; v2 receives a copy, executes fully against the recorder, records its decision and delivers nothing.

Classify every shadow request into the four buckets. Track UNEXPECTED and WIRE to zero. Each one is either a v2 defect, fixed, or a legitimate behaviour change, which requires a ledger entry authorized **before** the next run.

Run to the declared window: ≥72 hours and ≥5 million requests, covering all four surfaces, both streaming modes, both tenants of the opposite-policy pair, and at least one plan update under load.

Measure v2's resource curve beside v1's on the same traffic, and confirm shadow load does not distort v1's service.

Close C2 coverage gaps found in shadow by extending the corpus, then re-replay.

#### Live acceptance tests — required to exit

| Test | Live procedure | PASS | FAIL / stop |
|---|---|---|---|
| LGW21-1 | Full shadow window. | Zero UNEXPECTED, zero WIRE across the whole window. | Any of either. |
| LGW21-2 | Ledger review. | Every EXPECTED entry has a rule id, a §10.2.2 row, a C3 score and a pre-dating commit. | Any post-hoc entry. |
| LGW21-3 | Plan update at 70% load, observed in shadow. | v1 and v2 converge within the same bound. | v2 diverges. |
| LGW21-4 | Surface coverage. | Every surface and both modes exercised above the declared minimum. | Any surface under-covered. |
| LGW21-5 | Shadow overhead on v1. | Within the declared budget; v1 p99 unaffected. | Shadow degrades production. |
| LGW21-6 | Resource comparison. | v2's curve measured and published beside v1's. | Not measured. |

#### Failure scenarios that must be handled

A rare shape appears once with an UNEXPECTED diff → investigated to root cause; rarity is not a waiver. Shadow falls behind → it is bounded and drops are counted; a dropped shadow request is not a clean one.

#### Exit criteria

Zero UNEXPECTED and zero WIRE across the full window. Ledger complete and pre-dated. Coverage met. Shadow overhead within budget. Resource curves published.

#### Evidence package

Shadow report by bucket, surface and day · ledger with timestamps · convergence run · coverage matrix · overhead measurement · resource comparison.

#### Rollback / stop rule

Stop shadow; v1 is unaffected throughout. **Stop the cutover** if UNEXPECTED cannot be driven to zero. There is no threshold of acceptable unexplained divergence.

#### Agent execution notes

Agent-executable for running and triage. Ledger authorization is human. The agent's output is a triaged diff report, not a decision to accept one.

---

### GW22 — Rehearse cutover and rollback under load

| Field | Value |
|---|---|
| Phase | Migration |
| Depends on | GW21 |
| Primary owner | DevOps + backend lead |
| Objective | Execute the entire cutover and rollback on staging under production-shaped load, and measure the rollback time that will be published. |

#### Why this task exists

A single cutover is only survivable if it is a routing change against an already-warm system, and if rollback has been performed rather than assumed. The rehearsal converts both from plan to measurement.

#### Implementation work

Bring staging to a production-shaped state: both versions deployed and warm, real edge, shared state, guard fleet, production-shaped load.

Execute the full runbook: pre-checks, freeze, shift traffic, observe, declare. Time every step.

Execute rollback under the same load, starting from a deliberately injected failure rather than a clean state. Measure from decision to full restoration.

Rehearse each documented failure mode: v2 unhealthy after shift; a v2 defect visible only at full traffic; shared-state contention from both versions; in-flight streams at the moment of the shift.

Verify in-flight stream behaviour explicitly: a stream that began on v1 completes on v1 or terminates per the declared contract; it is never spliced onto v2.

Publish the measured rollback time. It becomes the number the cutover decision is made against.

#### Live acceptance tests — required to exit

| Test | Live procedure | PASS | FAIL / stop |
|---|---|---|---|
| LGW22-1 | Full cutover under load. | Completes within the declared window; error rate within budget throughout. | Any window or budget exceeded. |
| LGW22-2 | Rollback from an injected failure. | Full restoration within the declared window; measured and published. | Not achievable, or not measured. |
| LGW22-3 | In-flight streams at shift. | Complete on the original version or terminate per contract; zero splices. | Any spliced stream. |
| LGW22-4 | Both versions on shared state under load. | No quota multiplication, no plan corruption, no cross-version leakage. | Any contention effect. |
| LGW22-5 | v2 unhealthy immediately after shift. | Automatic detection; rollback triggered within the declared window. | Manual detection only. |
| LGW22-6 | Full rehearsal, twice. | Same results both times. | Non-reproducible. |

#### Failure scenarios that must be handled

Rollback needed after v2 has written state v1 cannot read → state compatibility is verified in this rehearsal, not discovered in production. Edge caches the routing decision → verified and bounded.

#### Exit criteria

Cutover and rollback both executed under load, twice, reproducibly. Rollback time measured and published. In-flight semantics proven. No shared-state contention. Automatic unhealthy detection.

#### Evidence package

Timed runbook execution ×2 · rollback timing · in-flight captures · contention report · automatic-detection run · signed go/no-go checklist.

#### Rollback / stop rule

Staging only; no production exposure. **Stop** if rollback cannot be completed within the declared window under load — the cutover is not authorized without a proven exit.

#### Agent execution notes

Rehearsal execution is agent-assisted; the go/no-go decision is human and recorded.

---

### GW23 — Execute the production cutover

| Field | Value |
|---|---|
| Phase | Migration |
| Depends on | GW22 |
| Primary owner | Backend lead + DevOps + on-call |
| Objective | Move production to v2 in one routing change, with v1 warm and rollback proven. |

#### Why this task exists

Everything above exists to make this step boring.

#### Implementation work

Re-verify every precondition on the day: shadow still clean, conformance green, GW20 numbers current for the deployed build, rollback rehearsed within the declared freshness window.

Execute the rehearsed runbook. Do not improvise: a deviation is a stop, not an adaptation.

Observe against pre-declared thresholds for error rate, p99 addon, security disposition distribution, provider call ratio and audit completeness. The disposition distribution matters as much as the latency: a sudden shift in block rate is a defect signal even when latency looks excellent.

Hold v1 warm and rollback-ready for the declared period after cutover.

#### Live acceptance tests — required to exit

| Test | Live procedure | PASS | FAIL / stop |
|---|---|---|---|
| LGW23-1 | Cutover at low traffic. | Thresholds hold through the observation window. | Any breach → roll back immediately. |
| LGW23-2 | Ramp to full production traffic. | Thresholds hold at every step. | Any breach → roll back. |
| LGW23-3 | Disposition distribution versus shadow. | Within the declared tolerance. | Material shift → investigate before proceeding. |
| LGW23-4 | Real OpenAI SDK clients, Python and Node. | Work unchanged, no client changes. | Any client breaks. |
| LGW23-5 | Audit completeness during and after. | ≥ declared ratio throughout. | Loss above the bound. |
| LGW23-6 | 24-hour post-cutover observation. | All thresholds hold; resource curve matches GW20. | Any drift → roll back. |

#### Failure scenarios that must be handled

A defect appears only at production scale → roll back first, diagnose second. A tenant reports a behaviour change → check the ledger; an entry means expected and the tenant is informed, absence means roll back.

#### Exit criteria

Cutover complete. Thresholds held through 24 hours. No client changes required. Audit complete. v1 warm through the declared window.

#### Evidence package

Timed execution log · threshold dashboards · disposition comparison · SDK verification · audit completeness · 24-hour report.

#### Rollback / stop rule

The rehearsed rollback, executed on any threshold breach, without debate. Diagnosis happens after restoration.

#### Agent execution notes

Human-led. Agents may monitor and prepare artifacts; the shift and any rollback are executed by a human on-call.

---

### GW24 — Decommission v1, remove external-AI paths and credentials, publish the evidence pack

| Field | Value |
|---|---|
| Phase | Release |
| Depends on | GW23 |
| Primary owner | Backend lead + security owner |
| Objective | Make the old system unreachable, prove the local-only boundary, and leave behind evidence someone else can reproduce. |

#### Why this task exists

An un-deleted v1 is a path back to every defect in §10.1. And credential removal must follow code removal: revoking IAM while code still calls Bedrock converts a fail-open path into a per-request `AccessDeniedException` storm.

#### Implementation work

Order is fixed. Remove v1 routes, then v1 code, then external-AI client code, then credentials and network permissions. Never reverse the last two.

Delete the Bedrock, Vertex and Gemini client modules, the 27 `BEDROCK_*` environment variables, `llm_judge`, `bedrock_embedder`, `bedrock_scanner`, `bedrock_client`, `bedrock_logger` and `bedrock_tier2_breaker`. Note that `boto3` itself stays: `poc_views.py:69` uses `boto3.client("s3")` and that is unrelated.

Prove the local-only boundary with egress denial across every surface, every failure path, startup, restart, background jobs and retries. The criterion is zero **attempted** platform external-AI calls, not zero successful ones.

Delete the dead code v1 carried: `embedding_vault` (497 lines, hard-disabled), the always-allow `services/guardrails` scaffold and its `GUARDRAILS_SERVICE_URL` wiring, `_scan_prompt_sync_disabled_builtin_default`, and the unused `opentelemetry` dependencies.

Publish the evidence pack: every measurement, its method, its raw data and the commands to reproduce it.

Publish the honest capacity statement: measured qualified RPS at the measured p99, with posture, prompt band, percentile, window count and per-request FPR stated together. A defensible number with its caveats beats an indefensible one without.

#### Live acceptance tests — required to exit

| Test | Live procedure | PASS | FAIL / stop |
|---|---|---|---|
| LGW24-1 | Deny all external AI egress; exercise every surface, failure path and restart. | Zero attempted calls anywhere. | Any attempt. |
| LGW24-2 | Revoke credentials after code removal; run the full suite. | No behaviour change; no error storm. | Any dependency remains. |
| LGW24-3 | Request a v1 route. | Not found; no v1 process reachable. | Any v1 path alive. |
| LGW24-4 | Independent reviewer reproduces the headline numbers from the pack. | Reproduced within tolerance from the documented commands. | Not reproducible. |
| LGW24-5 | Full conformance and §5 scenario suite on the final build. | All pass. | Any failure. |
| LGW24-6 | Rollback drill to the last v1 image. | Documented as unavailable past the declared date, with the reason. | Implied but untested. |

#### Failure scenarios that must be handled

A dependency on a removed module surfaces late → found by egress denial before credential revocation, which is why the order is fixed. Evidence cannot be reproduced → the claim is withdrawn until it can.

#### Exit criteria

v1 unreachable and deleted. External AI removed in the correct order and proven by egress denial. Dead code gone. Evidence pack reproducible by a third party. Capacity statement published with all caveats.

#### Evidence package

Egress-denial matrix · credential-revocation run · route verification · independent reproduction report · final conformance run · published capacity statement.

#### Rollback / stop rule

Past this card there is no rollback to v1; that is the point. **Stop** if egress denial shows any attempt — fix before revoking anything.

#### Agent execution notes

Agent-executable except credential revocation. The agent must not remove a credential before its code path is gone, even when the code appears unused.

## 10.10 Backend rebuild live scenario catalog

These extend §5 rather than replacing it. §5's S01–S40 apply to v2 unchanged and are re-run on the candidate build. The scenarios below exist only because a rebuild and a cutover create failure modes a single-system runbook has no reason to describe.

| ID | Scenario | Live setup | Pass condition |
|---|---|---|---|
| SB01 | Finding survival | Semantic BLOCK with no matching deterministic rule. | Reaches the resolver; zero provider calls. Directly falsifies the v1 adapter defect. |
| SB02 | Degraded independence | Semantic detector UNAVAILABLE across all four dispositions. | Emitted action derives from the rule's posture, never a blanket redact. Falsifies P3/P6. |
| SB03 | Monitor inertness | MONITOR rule, high-confidence finding, both phases. | Disposition unchanged; finding recorded. Falsifies P7. |
| SB04 | Org precedence | Org ALLOW against a detector BLOCK recommendation. | Signed rule semantics, matching the ledger entry. Falsifies P1/P2. |
| SB05 | Developer traffic | The five backtick prompts plus the tool-description family. | Zero terminal blocks; findings as signal. Falsifies P8. |
| SB06 | Plan-state trichotomy | Empty plan, unavailable plan, unknown tenant. | Three distinct outcomes; no inheritance in any. |
| SB07 | Cross-surface identity | One injection fixture via chat, MCP args, RAG retrieval, vector query, embeddings. | Identical disposition under an identical plan on all five. |
| SB08 | Capacity derivation | Same image at 2/4/8/16 vCPU and two memory limits. | Every bound changes; none constant. |
| SB09 | Literal gate | Introduce a capacity literal outside `runtime/`. | CI fails naming file and line. |
| SB10 | Layer gate | Introduce an upward import and a detector-side 403. | Both gates fail. |
| SB11 | Resolver purity | Property suite, 10,000 generated cases. | All four properties hold; function deterministic. |
| SB12 | Base-URL swap | Unmodified OpenAI application, key and base URL changed only. | Works, streaming and non-streaming, Python and Node. |
| SB13 | Shadow closure | Full shadow window, all surfaces. | Zero UNEXPECTED, zero WIRE. |
| SB14 | Ledger integrity | Attempt a post-hoc ledger entry. | Rejected on timestamp. |
| SB15 | Cutover rehearsal | Full cut plus rollback under load, twice. | Both within declared windows, reproducible. |
| SB16 | In-flight at shift | Active streams at the routing change. | Complete on origin or terminate per contract; zero splices. |
| SB17 | Dual-version state | Both versions on shared state under load. | No quota multiplication, no plan corruption, no leakage. |
| SB18 | Instrument honesty | Recorder at 2,000 ms TTFT with zero firewall work. | `T_fw_addon` ≈ 0; residual p50 zero. |
| SB19 | Mid-stream hold | 30 ms hold injected mid-stream. | Appears in release lag; a sub-20 ms claim fails. |
| SB20 | FPR publication | Benign corpus at 1/2/4/7 windows. | Per-request FPR published at every deployed window count. |
| SB21 | Removal order | Revoke credentials before code removal, in staging. | Failure is detected and the order is enforced by the runbook. |
| SB22 | Reproduction | Third party rebuilds the headline numbers from the pack. | Reproduced within tolerance. |

## 10.11 Cutover gate and stop conditions

§6's release gate applies in full to the v2 candidate. The rows below are additional and specific to replacing a running system.

| Gate | PASS requirement |
|---|---|
| Wire contract | C1 100% on Python and Node, in-process and over real TCP, on the candidate build. An unmodified OpenAI application works with only `base_url` and key changed. |
| Parity | Zero UNEXPECTED and zero WIRE diffs across the full shadow window. |
| Ledger | Every EXPECTED diff pre-registered with a rule id, a §10.2.2 row, a C3 score and a pre-dating commit. |
| Detection | C3 recall and FPR published per posture and family, including paraphrase and developer traffic. Every §10.2.2 change scored. |
| FPR | Per-request false-positive rate published at every deployed window count, not per window. |
| Structure | All five structural gates green: layers, function and module length, no decision outside `resolve/`, no capacity literal, no module-level mutable state. |
| Instrument | Reconciliation residual p50 zero. Every performance number produced under it. |
| Rollback | Rehearsed twice under load; measured restoration time published and current. |
| Local only | Zero attempted platform external-AI calls across all surfaces and failure paths. |
| Credentials | The GW00 exposure resolved and the rotation record signed. |

### 10.11.1 Stop conditions specific to the rebuild

In addition to §6.1, the cutover stops if any of the following is true:

Any UNEXPECTED diff remains unexplained. There is no acceptable threshold of unexplained divergence.

Any ledger entry post-dates the run it excuses.

Any structural gate is disabled, waived or has an exemption list.

Any capacity literal is merged with a comment promising later derivation.

Any surface retains its own enablement logic, plan lookup or resolver.

Any performance number is quoted from a run whose reconciliation residual was non-zero.

Rollback has not been executed under load within the declared freshness window.

The published capacity claim omits posture, prompt band, percentile, window count or per-request FPR.

## 10.12 Reconciliation with the T00–T26 track

Nothing in §4 is discarded. Each T-task is absorbed, retained or superseded, and this table is the authority when the two sections appear to disagree.

| T-task | Disposition under the rebuild |
|---|---|
| T00 identity and security unknowns | **Retained, strengthened** as GW00. Adds three-branch resolution and the structural gates. |
| T01 sign the contracts | **Retained.** §1–§2 stand. §10.2 narrows the frozen contract to the wire and explicitly unfreezes the internals. |
| T02 Docker staging and recorder | **Retained** and reused by GW02, which cherry-picks `staging/t02/` rather than rebuilding it. |
| T03 repair timing | **Superseded by GW14.** v2 measures rather than repairs; no derived `model_output_ms` exists to fix. |
| T04 capability corpus | **Retained** as C3 and made load-bearing: it scores every deliberate behaviour change. |
| T05 compile the org plan | **Absorbed into GW05**, extended with three distinct plan states and one lookup for all surfaces. |
| T06 one enforcement resolver | **Absorbed into GW07**, extended with purity, type-level BLOCK unreachability and a property suite. |
| T07 fast deterministic Tier-1 | **Absorbed into GW09**, extended with single-redactor byte verification and the injection-to-signal demotion. |
| T08 zero external AI boundary | **Retained**, executed across GW18 (removal) and GW24 (proof and credential order). |
| T09 local input inference | **Absorbed into GW08 and GW10**, extended with the pluggable backend and the per-request FPR budget. |
| T10 output security and modes | **Absorbed into GW13**, extended by using the same resolver for both phases. |
| T11 SSE correctness | **Absorbed into GW12**, extended with credit-based backpressure and real cancellation. |
| T12 SDK compatibility | **Elevated to GW01** and promoted from a task to the frozen contract that gates every commit. |
| T13 routing, retry, cancellation | **Absorbed into GW11 and GW12.** |
| T14 shared-state latency | **Absorbed into GW06**, extended with one posture table implemented once. |
| T15 telemetry off the hot path | **Absorbed into GW14**, extended with counted loss and removal of the synchronous log publisher. |
| T16 edge hardening | **Retained** as deployment work, plus the nginx `upstream`/`keepalive` gap from §10.1.6. |
| T17 admission and backpressure | **Absorbed into GW19.** |
| T18 measure one unit | **Absorbed into GW20**, extended with the three separate denominators and open-loop generation. |
| T19 horizontal scaling | **Absorbed into GW20.** |
| T20 qualify the budget envelope | **Retained** and re-run on the v2 candidate. |
| T21 resilience and chaos | **Retained**, extended by GW22's dual-version and in-flight scenarios. |
| T22 frontend control under load | **Retained**, and now runs against v2 as part of UI12. |
| T23 final qualification | **Retained.** Runs on the v2 candidate before GW23. |
| T24 real-provider canary | **Retained.** Runs after GW23 on v2. |
| T25 remove legacy paths | **Absorbed into GW24** with the fixed removal order. |
| T26 publish evidence | **Absorbed into GW24.** |

Sequencing: T00→T02 and T04 run first and serve both tracks. The GW track then replaces T03 and T05–T19. T20–T24 run on the v2 candidate. UI00–UI13 proceed in parallel throughout and must reach UI12 against v2, not v1 — the frontend's backend contract is set by GW05 and GW14, so UI06 and UI07 depend on those two cards rather than on T-track equivalents.

## 10.13 Agent execution protocol

This track is executed by coding agents with human gate review. The cards are written accordingly, and four rules govern how they are worked.

**A card is one unit of work.** An agent takes one card, works only within its declared file scope, and stops at its exit criteria. Cards do not spill: a defect discovered in another card's scope is reported, not fixed in passing.

**Machine-checkable exit criteria only.** Every card exits on a command that returns a status, not on a judgement. That is why §10.2.3's structural rules are lints and AST gates rather than review conventions — an agent cannot be trusted to remember a convention across a 25-card track, and a human reviewer cannot be expected to catch the 283rd decision site by eye.

**The gates are not negotiable by the agent.** An agent that cannot satisfy a gate reports the obstruction. It does not add an exemption, widen an allowlist, weaken an assertion, mark a test `xfail`, or introduce a literal with a comment promising later derivation. Every one of those has a specific prohibition in a card above because every one of them is how the current codebase reached its present state.

**Human authority is reserved at four points**, and only four: the credential decision in GW00; every ledger entry in GW02 and GW21; the merge review for GW07, which is the one card where a subtle error is a security failure rather than a bug; and the go/no-go and any rollback in GW22 and GW23.

Recommended lane assignment for parallel agents after GW00: one lane takes GW01 → GW02 (contract and harness); one takes GW03 → GW04 → GW06 (runtime and admission); one takes GW05 → GW07 (plan and resolver, the critical path); one takes GW08 → GW09/GW10 (detection). The lanes converge at GW15, after which GW16, GW17 and GW18 fan out again and rejoin at GW19.
