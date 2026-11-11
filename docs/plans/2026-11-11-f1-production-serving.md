# F1: production gateway serves real completions (GW36 S5 + per-org providers)

Status: 11 Nov 2026, **revision 3**.
- Revision 1 (`8484c571`) was challenged by five reviewers.
- Revision 2 (`4d23ed07`) was challenged by two more (Plan Reviewer A: completeness; Plan Reviewer B: security, scale and operability). Both requested changes.

Revision 3 changes how the plan is organised:
- **§5 is the implementable design of F1a-1 only.** Both revision-2 reviewers judged F1a-1 releasable once its own blockers were fixed, and §5 fixes them.
- **§6 and §7 are the requirements for F1a-2 and F1a-3.** Every finding from all seven reviews is captured there as a binding requirement. Each of those slices gets its own design revision and its own review on its own PR before any code. They carry too much rollout and security risk to be designed in the same pass as F1a-1.
- **§10** records each revision-2 finding and where it is handled.

## 1. Goal and exit

`AMF_PROFILE=production`, with **no dev flags**, serves chat completions for every organization, to that organization's own model connections, with the full firewall pipeline on:
- input scan, plan resolver, output scan, GW33 output detectors, customer rules;
- audit to the console;
- the kill switch and routing.

**Exit (L36-1, extended), at the end of F1a-3:**
- A valid console key gets a completion, streaming and non-streaming, from a recording upstream for each provider family shipped.
- An invalid key gets 401 `invalid_api_key`.
- A blocked prompt makes **zero** upstream calls.
- The trace, the metrics delta and the console row agree, joined by a per-run nonce in the prompt.

The run uses the production compose service.

## 2. Owner decisions (11 Nov 2026)

| # | Decision | Source |
|---|---|---|
| D1 | Feature 1 ships **all v1 providers**: openai, anthropic, azure, google, aws_bedrock, mistral, cohere, deepseek, huggingface, meta, ollama, custom | owner |
| D2 | Model API keys: "the industry standard that scales to 100k RPS while keeping security". Direction: envelope-encrypted credentials in signed state, with no control-plane call on the request path. The detailed design is part of F1a-3 (§7) | owner delegated |
| D3 | Model changes in the console reach gateways **live, within seconds** (signed-state feed) | owner |
| D4 | Models whose key comes from a server env var (`api_key_env_var`) are **refused in production** | owner |
| D5 | Build locally first, scale to this machine's limit, and keep the design hardware-independent (throughput scales with cores/workers); cloud tests later | owner |

## 3. Current behaviour (re-checked for this revision)

Production profile today (`edge/production_profile.py`, `edge/serve.py`):

| Dependency | Production today | Consequence |
|---|---|---|
| identity, plans, kill switch, admission, quota, org limits, incident gate | real, from signed state | — |
| `provider` | empty `StubProviderClient` (`serve.py:175,237`) | no completion |
| `routing`, `catalog` | unset / `empty_catalog_source` | `/v1/models` is `[]` |
| `input_detector` | `no_findings_detector` (`serve.py:242`) | **fail-open hazard** once a provider is wired |
| `error_scanner` / `output_detector` / `phase_resolvers` | `build_app` defaults: `no_error_findings`, `None`, `None` (`app.py:400,415,416`) | no base output scan. Plan rules are not enforced (`MinimalOutputResolver`). RAG stages refuse with `RAG_INTERNAL_ERROR` (`rag_stages.py:110-112`) |
| audit | `IncidentAuditTap(downstream=None)` (`incident_wiring.py:77-86`) | every record dropped |
| Publisher | `_NoAudit`; publishes the 44-series re-hydrator family (N14) | `amf_store_memory_reachable 0` is false, and the series are duplicated |
| response store | per-worker memory (`openai_responses.py:407`) | `/v1/responses` breaks across workers |
| GW35 unauth audit | unwired (no `unauth_audit` dep) | failed-credential records dropped |
| boot log | survivors of the fail-closed table, such as `input_detector -> no_findingsors_detector (fail-closed; identity/admission refuse)` (`serve.py:256`), plus a stale "every other line is fail-closed (S4/S5)" header (`production_profile.py:545`) | the log misdescribes production |

**Customer conditions (GW26).**
- **How they reach detectors.**
  - In dev only, through the `request_org` ContextVar (`dev_plan_feed.py:59`; set at `dev_components.py:177,188,213`).
  - `run_in_executor` does not copy it (`executor.py:115`). So on the off-loop paths a peer org is scanned with the primary org's program (N5). Those paths are `chat_stream.py:271`, `chat_stream.py:435` (STRICT_WITHHOLD with a family), `rag_stages.py:135` and the error frame (`chat_stream.py:388`).
  - `feed.conditions()` returns the latest program, not the request's pinned plan.
- **The regex engine** is Python `re`, which is Unicode-aware. It runs in a new daemon thread per match, joined with `budget_s` (`conditions.py:190-208`).
  - `re` holds the GIL, so the join cannot interrupt a catastrophic match.
  - `scan_conditions` gives every rule the **full** budget (`conditions.py:86`).
  - The worst case per request is deltas × rules × budget.
- **Customer regex runs in two more places.**
  - At compile, `rewrite_template.py:91-98` scans with a literal 0.25 s budget for REWRITE rules. A timeout there rejects the whole plan.
  - At publish, control runs `re.search(customer_pattern, template)` (`firewall_v2_publish.py:586-593`).
- **The timeout posture.** A timeout or a refused pattern refuses the request with `policy_regex_timeout` (400) (`conditions.py:84-88`, `input_enforcement.py:185`).
- **`bind_surface` call sites:** 9 calls in 6 modules:
  - `openai_chat:641`
  - `openai_nonstream:297,318`
  - `chat_stream:271,435`
  - `mcp:453,516`
  - `embeddings_input:128`
  - `rag_stages:135`

  `phase_resolvers.py:103` calls a different function, `_bind_surface`, on an `OutputResolver`. Moderations call the detector directly (`openai_misc.py:787`).
- **The streaming per-delta path runs on the event loop.** In non-strict modes the base detector runs synchronously in `_process_delta` (`egress/stream.py:548`). This is documented as a GW13 follow-up (`executor.py:37-40`). GIL release therefore does not protect the loop on that path.

## 4. Slices and rollout model

| Slice | Scope | Design status |
|---|---|---|
| **F1a-1 Pipeline safety** | Detector factory (N5), customer-regex engine with a per-request scan deadline, production resolvers and base detectors, bounded audit sink, N14, GW35, response store, per-worker process hardening, honest boot log. Provider stays a stub | **§5, ready for implementation** |
| **F1a-2 MODEL state** | Forward-compatible stamp (its own release first), MODEL kind, projector, per-org applier, catalogue, GW29 semantics, alias grammar, filters | **§6 requirements**; own design and review before code |
| **F1a-3 OpenAI-compatible dispatch** | Credentials, SSRF, pools, parameter shaping, error mapping, observability, compose `gateway-v2-prod` | **§7 requirements**; own design and review before code |
| F1b | Azure OpenAI and Anthropic native adapters | after F1a-3 |
| F1c | AWS Bedrock (SigV4, event stream) | after F1b |
| F1d | Load and scaling curves, Valkey sharding | last |

Every slice ships as its own PR into `develop`, with:
- the full gateway_v2 suite, control suites (ratchet) and CI;
- two agent reviews and an agent merge;
- a live check where the slice is user-visible.

## 5. F1a-1 design: pipeline safety

**Exit criterion.** A production boot with no provider shows:
- **BLOCK:** a blocked prompt gets its declared refusal, makes zero provider calls, and produces a console row (nonce-joined).
- **No fail-open:** an allowed prompt reaches the stub and gets the declared operational error the stub produces today (the live test pins the exact code).
- **N5:** an off-loop scan of a peer org uses the peer's program.
- **Tenant isolation:** one tenant's catastrophic rule cannot stall another tenant on the same worker beyond a bounded slice.
- **Honest output:** `/metrics` and the boot log are honest.

No new error codes are introduced in F1a-1.

### 5.1 Detector factory (N5)

- **New production module `edge/detectors.py`** holds:
  - `DeterministicFindings`, `bind_surface` and `phase_resolver_factory`, moved out of `dev_components.py`. `dev_components` re-exports the **same objects**, because `bind_surface` checks the class with `isinstance`;
  - the shared `BASE_DETECTORS` tuple (already introduced by the GW34 calibration work), so calibration and serving time the same detectors.

  The module does not import `edge.app`, which avoids the import cycle that forces today's lazy imports.
- **Shape:** `DetectorFactory.for_request(plan, phase) -> RequestDetector`.
  - The result is a callable bound to the request's **pinned** `ExecutionPlan`, its compiled `ConditionProgram`, and a per-request `ScanDeadline` (§5.2).
  - `bind_surface(detector, surface, stage)` keeps its signature and binds onto that object.
  - No ContextVar is used anywhere. `request_org` and `DevPlanFeed.conditions()` are deleted, and dev uses the same factory.
- **Program lifecycle (Reviewer 3 m1).**
  - The program is compiled when the PLAN record is applied, next to `plan_from_record`. It is stored in a per-worker `ConditionPrograms` map keyed by org, holding `(plan version, program)`.
  - It follows the plan store's lifecycle and bound: one entry per org present in the plan store, removed when the org is offboarded, and never replaced by an older version.
  - The request path only reads it, and the pinned plan's version selects the entry. If the stored entry is newer than the pinned plan, the request recompiles from the pinned plan, which is rare and bounded by the publish-time limits in §5.2.
- **Call sites that gain a plan parameter.** These signatures change: `scan_embeddings` (`embeddings_input.py:100-106`), `rag_stages.detector_for` (`:116-123`), and the error-frame scan (`chat_stream.py:388-394`). Their callers already hold the plan:
  - `openai_misc.py:368`;
  - `Session.plan` (`rag_common.py:129`);
  - `chat_stream.py:324`.
- **`plan=None` sites** (`openai_nonstream.py:290,309`, where `collect_and_enforce` defaults to `None`): pass the plan from `routes.py:298`, which already has it. No call site keeps `None` on a tenant request.
- **Moderations.** Behaviour is unchanged from dev: the org's plan is resolved (as `openai_misc.py:368` does for embeddings) and customer conditions run phase-only. Revision 2's `plan=None` would have dropped customer rules from moderations; it is withdrawn.

### 5.2 Customer-regex engine and the per-request scan deadline

The goal is to keep today's semantics exactly while making every customer pattern pre-emptible and bounded.

- **Engine: the `regex` package (mrab) for every customer pattern, in VERSION0 mode.** That mode is compatible with `re`: `\d`, `\w`, `\s` and `\b` are Unicode-aware, exactly as today.
  - **RE2 is not used.** Its ASCII-only classes would let an attacker bypass a card-number rule by switching digit scripts. Both revision-2 reviewers measured this.
  - Matching uses `pattern.finditer(text, timeout=<remaining>, concurrent=True)`. `concurrent=True` releases the GIL; this was measured three times, with 90–93 ticks with it and 1–2 without.
  - The thread created per match is removed (63 µs each).
- **Per-request `ScanDeadline`.**
  - One monotonic deadline is created when the request is admitted. Its length is `contract.guard_wait_budget_s()`, the value each single call gets today.
  - Every customer-regex call on that request draws from it. This replaces today's "full budget per rule per call".
  - On the inline streaming path (`egress/stream.py:548`), each call is also capped at `contract.scan_window_s()`. So one call can hold the loop for at most one scan window (10 ms at the default p99), and a pathological pattern fails on its first delta.
- **Timeout posture: unchanged.** A timeout, or a pattern that cannot compile, refuses the request with the existing `policy_regex_timeout`. It does not route through `on_unavailable`; revision 2's change to that is withdrawn.
- **Publish-time limits (control).** `policy/serializers.py` `validate_condition` keeps its length ≤ 1000 and ReDoS-shape checks, and adds:
  - a compile with `regex` (not `re`);
  - refusal of a counted repetition above 1000, and of nested counted repetitions whose product is above 1000. `(?:a{1000}){1000}` compiles in 471 ms, and `regex` compile cost grows with that product;
  - a per-plan cap on regex rules, whose value is taken from the existing plan rule limits.

  Control's own `_matches_own_pattern` (`firewall_v2_publish.py:586-593`) switches to `regex` with a timeout, so publish can no longer hang a control worker.
- **The compile-time scan** (`rewrite_template.py:91-98`): its literal `_CHECK_BUDGET_S = 0.25` is replaced by a contract-derived value, through the same `regex` path.
- **Projector:** `validate_plan` memoises by body hash, so the projector does not recompile every org's rules every round (Reviewer B). `refused_conditions` counts patterns that `regex` refuses.
- **Dependency.** `regex` goes into the gateway_v2 **base** dependencies, pinned to a release at least two weeks old (2026.9.29). The image installs no extras by default. mypy needs a typed shim or an `ignore_missing_imports` override. Control already depends on `regex`.
- **What does not change.** `_redos_shape` keeps refusing the shapes it refuses today. That is an existing fail-closed behaviour, and loosening it is out of scope.

### 5.3 Resolvers and base detectors

- `phase_resolvers = phase_resolver_factory()` in production, so plan rules are enforced and RAG stages work.
- `input_detector`, `error_scanner` and `output_detector` come from the factory. The base detectors are the GW09 deterministic set.

### 5.4 Audit

1. **Build order.** `_build_admission` moves above `build_wiring(runtime)` (`production_profile.py:493-496`). It depends only on contract, env, clock and `q_safe`, and it supplies `bounds.audit_depth`.
2. **Sink.** `AuditSink(ValkeyAuditStore(runtime.store_client, runtime.store_keys), budget, queue_depth=bounds.audit_depth)`. `build_incident(..., audit=sink.emit)` makes the sink the tap's downstream. `deps["audit"]` stays the `IncidentAuditTap`, because the risk tracker needs an `AttemptSink`. `DevAudit`'s tested pieces are promoted to the production module rather than duplicated.
3. **The budget's tenant count (Reviewer 4 B2, Reviewer A B4).**
   - `StreamBudget`'s `tenants` is the cardinality of the signed PLAN index (`StoreKeys.index(StateKind.PLAN)`, one `ZCARD`). It is read by the sampler owner each tick and shared, never taken from a worker's local plan cache.
   - **Unknown rule:** until the first decision exists, the budget is the existing declared fallback, `stream_maxlen` per org, with `startup_check` logging the warning it logs today.
4. **Sampler owner.** Production workers have no index, so ownership is a Valkey lease: `SET {ns}:audit:sampler <worker-id> NX PX <ttl>`, renewed by the holder.
   - The TTL is contract-derived from the sampler period.
   - The holder runs `MemorySampler` and publishes its `BudgetDecision` to a key. Every worker adopts it (`StreamBudget.adopt`).
   - Losing the lease stops sampling; any worker can take it over.
   - Every worker runs `startup_check`, as today.
5. **Lifecycle.**
   - An `AuditLifespan` hook starts the writer and the sampler task.
   - At shutdown, `drain(timeout)` runs before the runtime closes the client. `StateServedApp` already closes the client after the hooks. `run()` is cancelled before `drain()`, because the two read the same queue.
   - **A hook's start failure fails readiness.** `StateServedApp._run_hooks` records failures, and the readiness probe reports them. Today failures are only logged (`state_runtime.py:475-481`).
   - The one-tuple unpack in `test_lgw29_production_wiring` changes, because there are now two hooks.
6. **Metrics (Reviewer 4 M4).** The gateway publishes **producer and bound** series only: queue depth, drops, written-by-org count and budget bytes. The durability series (`completeness_ratio`, `records_lost`, `store_memory_reachable`) belong to audit_control and are not published by the gateway.
7. **Record attribution.** Each audit record carries the worker id it already uses (`host:pid`) in its detail. No index is invented.

### 5.5 GW35, response store, N14

- `unauth_audit = build_unauth_gate(contract, clock)`, and Publisher `unauth=UnauthAuditCounters(gate)` (+2 series).
- **Control side (Reviewer 4 M5).** Ingest drops `_unauthenticated` rows when there is no org map (`v2_audit_ingest.py:110`), so the invalid-key test would have no console row. F1a-1 states the reconciliation: unauthenticated rows are counted by the gateway series and land in the console only for orgs that have an alias mapping. The live test asserts the counter, not a row.
- `response_store = ValkeyResponseStore(runtime.store_client, ttl_s=contract.response_store_ttl_s(), keys=runtime.store_keys)`.
- **N14.** `Publisher` takes `rehydration: FlatProducer | None`.
  - `_rehydration_families` returns `([], 0)` for `None`, following the existing `_unauth_families` pattern.
  - `series_count` drops the 44-series term.
  - The gateway passes `None`.

### 5.6 Process hardening (per worker, Reviewer B 7)

- `RLIMIT_CORE=0` and `prctl(PR_SET_DUMPABLE, 0)` are applied **in each worker's app factory** (`serve.serving_app`). A setting made in the parent does not survive uvicorn's spawn, which was measured.
- The test checks `PR_GET_DUMPABLE == 0` from inside a spawned worker.
- The projector hardening and `_FILE` secrets belong to F1a-3, where the projector starts holding provider keys.

### 5.7 Boot log

- Each line states what is real.
- `apply_production` replaces the `input_detector`, `provider` and `catalog` survivors with true lines:
  - provider: still a stub, declared;
  - detectors: production factory;
  - audit, response store and unauth audit: their real wiring.
- The stale header goes, and the GW34 `tool_bounds` line stays.
- A test pins the full production line set.

### 5.8 F1a-1 tests

- **Unit and property tests:**
  - **N5:** an org-B request through each off-loop path uses org B's program: `chat_stream:271`, STRICT_WITHHOLD with a family at `:435`, RAG stages and the error frame. These fail on HEAD.
  - **Pinning:** a mid-request plan update does not change the program a request uses.
  - **Semantics against `re`:** each customer pattern in a corpus is matched by `re` and by the production path on a Unicode corpus. The corpus covers Nd digits from three or more scripts, U+00A0 and the U+2000 spaces, and Latin-1 letters. The results must be identical.
  - **Regex DoS:**
    - both reviewers' patterns;
    - a two-tenant test: tenant A's pathological rule runs on the non-strict streaming path while tenant B streams on the same worker. B's per-delta latency stays within one scan window, and A is refused with `policy_regex_timeout`;
    - the deadline is shared across rules and deltas.
  - **Publish limits:** counted-repetition refusals in control. `_matches_own_pattern` cannot hang.
  - **Audit:**
    - sink lifecycle: start, drain at shutdown, and a hook start failure fails `/readyz`;
    - a lease takeover between two fake workers;
    - the tenant count comes from the PLAN index.
  - **Metrics:** the Publisher series count without the re-hydrator family; the gateway publishes no durability series.
  - **Response store:** a cross-worker `/v1/responses` round trip, with two app instances on one store.
  - **Hardening:** `PR_GET_DUMPABLE` inside a spawned worker.
  - **Boot log:** the full production line set.
- **Live production boot** (postgres, redis, control, projector, re-hydrator, gateway in production, one worker):
  1. A BLOCK prompt gives its declared refusal, zero provider calls, and an `EnforcementEvent` row matched by nonce.
  2. An allowed prompt gives the stub's declared error and no fail-open pass.
  3. An invalid key gives 401, and `amf_audit_unauthenticated_total` increments.
  4. `amf_audit_budget_bytes > 0`, and `XLEN ≤ maxlen` for the org stream.
  5. Δproduced = Δwritten + dropped + failed, measured on a single-worker gateway.
  6. `/metrics` series count equals `SERIES_COUNT`, with `AMF_FD_LIMIT` stated.
  7. `/v1/responses` store-then-retrieve across two workers.
- **Regression:** full gateway_v2 suite, control suites and CI.

## 6. F1a-2 requirements: MODEL state (binding; design and review before code)

1. **Forward-compatible stamp, as its own release.**
   - The stamp verifies over the raw cursor map, ignores unknown kinds, and requires only the kinds this build applies.
   - The re-hydrator's held-stamp comparison also ignores missing kinds.
   - The re-hydrator names MODEL, and the projector emits it, only behind explicit flags. This follows the `project_mcp_key_policy` precedent (`domain/state_knobs.py:199`).
   - The rollout floor is documented: every gateway image that could be deployed (rollbacks included) runs step 1 before any step-2 re-hydrator starts. Rolling back a re-hydrator after step-2 gateways are deployed is forbidden.
   - Tests cover a mixed-build re-hydrator pair and stamp time never moving backwards.
2. **MODEL applier contract.**
   - Signature and index failures are round-fatal and raise an alert.
   - Body failures (decode, unknown provider or field, decrypt, unknown kid) are caught per record. They set an org-level `unavailable(version)` marker and the cursor advances.
   - An unknown provider or field omits only that entry, and is counted.
   - Readiness never depends on one org's record.
   - Rollback refusal applies per warm org. For cold fetch, the bound is stated as in the KEY model (signed index score plus deep repair).
   - `StateWriter.rollback` refuses MODEL.
3. **Request path O(1) in orgs.**
   - `serves_alias(org, alias)`, `preflight` and `_plan` read a per-org `(org, casefolded alias)` dict.
   - Client and validation checks run per org entry when the entry is applied.
   - `all_candidates()` stays off both the request path and the apply path.
   - A test shows the cost does not change between 1k and 100k orgs.
4. **Bounded caches.**
   - The MODEL view is a contract-derived LRU with an eviction metric and KEY's `_touched` race retry. Holding only `{kid, nonce, ct}` and decrypting per request (0.8 µs) is to be evaluated, so plaintext is not resident for every org a worker has served.
5. **Absence.**
   - Every active org has a record, possibly an empty one. An empty list goes through the existing `no_provider_configured` seam (`catalogue.py:54`).
   - Offboarding writes a tombstone.
   - An omitted row is carried as `{alias, omitted: reason}`, so the gateway can answer with a declared code instead of `model_not_found`.
6. **Projector cost and safety.**
   - Changes are detected by a projector-private memo per `row_id`: a digest of the Fernet token, the AAD tuple and the published `{kid, nonce, ct}`. A row is decrypted only when it changes.
   - Plan validation is memoised by body hash.
   - Removals and real per-row key changes go before the heartbeat. Kid re-wraps are a bounded backlog after it.
   - **Breaker:** when the omission ratio for one reason crosses a threshold, the previous records are kept and an alarm is raised. This prevents one wrong control key from emptying every catalogue.
   - A mixed console edit (one model removed, one added) is written as two versions.
   - A round-time test runs at 100k orgs × 5 models, with a budget well under `lag_ms`.
7. **Source columns.**
   - `core_llmmodelconfig` has no `weight`, `api_version` or `mode` column, and `unique_together(organization, model_name)` allows one candidate per alias.
   - The design either lists a control migration or drops those fields, and states the real number of candidates per alias.
   - Filters: `default_model` is an org field (`FirewallConfig`); `allowed_models` exists on keys and on the org, and both are retired in `firewall_v2_map.py:75-84`.
   - The design names the signed kind that carries each filter, and un-retires the fields.
8. **One alias grammar.** `[A-Za-z0-9._:/-]{1,128}`, the wire and gate grammar, is used in four places:
   - the wire;
   - the gate;
   - routing, where the cap is raised from 64 to 128;
   - the console serializer.

   `@` and `+` are not allowed. Case-fold collisions are omitted with `alias_collision`. The upstream id cap is 256, matching control.
9. **GW29 semantics.**
   - `om:` and per-org isolation key on `(org, alias)`.
   - Fleet `model:`, `iso:` and auto-isolation also match the resolved `(provider, upstream_model)`, so a fleet `model:gpt-4o` refuses org A's alias `prod-chat → gpt-4o`.
   - New key shapes go behind `KS_SCOPE_GENERATION` / `AMF_GATEWAY_MIN_SCOPE_GENERATION`.
   - The MODEL view is pinned with the plan **before** the phase-4b gate, and the gate reads the pinned view.
   - Tests: isolate A→B then delete B; an old-generation gateway keeps serving.
10. **Freshness.** The bound is projector `lag_ms` plus `fresh_ms`, which is 21 s with the defaults. It is asserted in the disable and rotation tests.
11. **Storage.** MODEL bodies are key-stored, not in `HASH_KINDS`.

## 7. F1a-3 requirements: dispatch and credentials (binding; design and review before code)

1. **Credentials.**
   - AES-256-GCM with a fresh `os.urandom(12)` nonce.
   - The AAD is a canonical length-prefixed encoding of `(namespace, org, row_id, alias, provider, base_url, kid)`.
   - The whole `{kid, nonce, ct}` triple is reused only when the plaintext and the AAD are byte-identical.
   - The encrypt kid is explicit, and switching it is gated on every gateway exporting `amf_gateway_cred_kids{kid}`.
   - A kid is retired only when nothing uses it and the fleet cursor has passed the re-wrap.
   - Kid rotation has a fixed cadence, so destroying a kid makes old log entries unreadable.
   - The projector now holds control's key and the gateway keys, so it is hardened like the gateways and reads its keys from `_FILE` secrets.
   - `GatewayAPIKey` secrets need a separate control key.
   - The Fernet builder's hash-derivation of a malformed key is refused in production.
2. **Leaks.**
   - The secret type drops the dataclass protocol, or an AST gate bans `asdict`/`astuple` on snapshot types (precedent: `vector_credentials.py:98`).
   - The projector validates the key charset (printable ASCII, no whitespace) and omits a bad row with `credential_invalid_format`.
   - Dispatch logs and traces exception type names only.
   - A planted-key grep covers logs, traces, audit rows and error bodies.
3. **SSRF.**
   - An address is refused when it is not `is_global` **or** `_blocked(...)` refuses it.
   - Embedded IPv4 addresses are decoded and re-checked (`64:ff9b::/96`, `64:ff9b:1::/48`, `2002::/16`, Teredo, `::/96`).
   - Private targets are allowed only through **per-org** `(org, host[:port])` entries, enforced at the projector and re-checked at connect.
   - Base URLs with userinfo, a query, a fragment, backslashes or dot segments are rejected.
   - Paths are joined structurally, and the final path is asserted to be `<base path>/<known endpoint>`.
   - The same grammar applies to `api_version`, Bedrock `region` and path segments in F1b/F1c.
   - The recorder is reachable only through a test overlay.
   - `http` is allowed only for org-scoped private entries.
4. **Pools.**
   - Pools are keyed by origin `(scheme, host, port)`, and default-provider origins are never evicted.
   - Distinct custom origins per org are capped.
   - The pool-acquire wait is bounded by the request deadline.
   - The client uses `trust_env=False`, an explicit `PinnedTransport` and `follow_redirects=False`, and has no default auth header.
   - Each org has an in-flight cap per origin.
   - The pool table is a refcounted LRU.
5. **Codes table (Reviewer 4 B1, Reviewer A B3).**
   - Every code used gets a row with HTTP status, error type, failure class and console stage/action, plus a `stages_to_console` test.
   - Existing codes are reused: `upstream_auth_error` (502) for BYOK 401/403 with `x-should-retry: false`, and `provider_unreachable`.
   - `PinnedRefused` gets a declared code.
   - The refusal keeps its candidate.
   - `routing_internal_error`, `model_not_found` and the D4 code are classified.
6. **v1 parity.**
   - Per-provider parameter shaping: a `drop_params` equivalent; `max_completion_tokens` for o-series models; `developer`, `seed` and `logit_bias` dropped where a provider rejects them; `include_usage`.
   - The upstream SSE decoder accepts comment keep-alives and `\r\n\r\n` framing.
   - Google's bad-key 400 maps to an upstream auth error.
   - The reasoning-model first-event timeout is contract-derived up to v1's 120 s.
   - Embeddings get a non-SSE upstream path.
   - Keyless Bedrock is refused like D4.
   - Inactive and keyless rows are omitted.
   - Vendor SSE quirks are tested with replayed captures.
7. **Observability.**
   - `pipeline_trace.routing` = `{requested_model, selected_model, route_destination=provider}`. It never includes `base_url`, and it needs a schema change in `console_schema.py:51`.
   - `amf_upstream_failures_total{provider,code}` uses closed vocabularies and is pre-seeded.
   - L36-1 metric deltas come from a single-worker gateway.
   - `routing_decision` logs carry `request_id`.
8. **Trust boundary (Reviewer B 13).**
   - The gateway's Valkey user is read-only on the signed-state key families, which needs ACLs and a separate user for the gateway.
   - Ed25519 state signatures are a follow-up.
9. **Compose `gateway-v2-prod`:**
   - `_FILE` secrets;
   - `ulimits.core: 0`;
   - port bound to 127.0.0.1;
   - `depends_on` healthy state services;
   - a stated `AMF_FD_LIMIT`.

## 8. F1d note (Reviewer B 14)

- The request-path Valkey families (audit, responses, budget) share the `{rv2}` hash tag, so in a Valkey Cluster they land on one slot.
- F1a-1 keeps the existing key layout. Every current deployment is a single Valkey node, and changing the audit key would also change control's ingest prefix.
- F1d moves the request-path families to per-org hash tags, migrates the control prefix with them, and reports Valkey operations per request.

## 9. Risks (F1a-1)

| Risk | Mitigation |
|---|---|
| Engine swap changes what customer rules match | `regex` VERSION0 has `re` semantics; Unicode corpus agreement test against `re` |
| Inline streaming scans hold the loop | Per-call cap of one scan window; per-request deadline; a pathological rule fails on its first delta |
| Detector factory touches every scan seam | Off-loop N5 test per path; the same objects re-exported for dev; full suite |
| A legitimately heavy plan exhausts the per-request regex budget | Same budget as one call today; publish-time limits; timeouts are counted, and the refusal is declared and visible |
| Audit lease flaps between workers | Contract-derived TTL; the takeover test; adopting a decision is idempotent |

## 10. Disposition of the revision-2 reviews

| Finding | Where handled |
|---|---|
| A-B1 / B-10: RE2 drops Unicode semantics; timeout posture loosened | §5.2: `regex` for every pattern, no RE2, timeout keeps refusing, Unicode agreement test against `re` |
| B-5: inline streaming path on the loop | §5.2: inline cap of one scan window plus the per-request deadline; two-tenant streaming test |
| B-10: per-match thread, compile cost, no deadline | §5.2: thread removed; publish-time repetition limits; shared deadline; projector memo |
| A-B4 / R4-B2: audit tenants, sampler owner, assertions | §5.4 (PLAN-index tenant count, Valkey lease, fallback rule); §5.8 live assertions 4–5 |
| A-B3 / R4-B1: codes | F1a-1 adds none; §7.5 table for F1a-3 |
| A-B2 / R2-B2: GW29 semantics, rollout guard, pin before the gate | §6.9 |
| B-1: `serves_alias` O(orgs) | §6.3 |
| B-2: per-org failure isolation | §6.2 |
| B-3 / A-S4: projector cost, re-wrap, breaker, two-version edit | §6.6 |
| B-4: fleet-wide private allowlist, URL shape | §7.3 |
| B-6 / A-N3: NAT64 and embedded IPv4 | §7.3 |
| B-7: dumpable does not survive spawn; env-held keys | §5.6 (workers); §7.1 (projector, `_FILE` secrets) |
| B-8: unbounded caches, `_touched` race | §5.1 (program lifecycle); §6.4 |
| B-9: leak paths | §7.2 |
| B-11 / A-S1: rollout gate, rollback | §6.1 |
| B-12: pool keying | §7.4 |
| B-13: state integrity | §7.8 |
| B-14: single hash slot | §8 |
| A-S2: four alias grammars | §6.8 |
| A-S3: missing source columns, filter carriers | §6.7 |
| A-S5: freshness number, rollback scope | §6.2, §6.10 |
| A-S6: observability mis-mappings | §5.4.6 and §5.5 (gateway side); §7.7 |
| A-S7: v1 parity gaps | §6.5, §7.6 |
| A-S8: untested design items | §5.8 for F1a-1. F1a-2 and F1a-3 tests are part of their design reviews |
| A-N1: `bind_surface` count | §3: 9 calls in 6 modules |
| A-N2: pool divisor | §7.4 (per-origin pools replace the divisor model) |
| A-N4: detector cache | §5.1 program lifecycle |
| A-N5: moderations | §5.1: behaviour unchanged |
| A-N6: `_redos_shape` | §5.2: unchanged |
| A-N7: dependencies | §5.2: `regex` in base deps, pinned |
| A-N8, A-N9, B-15…B-19 | §6.11, §7.1, §7.3, §7.7 |

Revision-1 dispositions (reviewers 1–5) are unchanged except where the table above supersedes them.

---
_Generated by [Claude Code](https://claude.ai/code/session_01SDCVQVj8LRKW3CZ99zfdW1)_
