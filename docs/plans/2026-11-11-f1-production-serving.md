# F1: production gateway serves real completions (GW36 S5 + per-org providers)

Status: 11 Nov 2026, **revision 2 (after the Phase 3 reviews and the Phase 4 devil's-advocate pass)**. Branch `feat/f1a-production-serving`. Revision 1 (`8484c571`) was challenged by five independent reviewers. §7 records every finding and what this revision did with it. Nothing here is implemented until this revision is accepted on PR #103.

## 1. Goal and exit

`AMF_PROFILE=production` with **no dev flags** serves chat completions for every organization, to that organization's own model connections, with the full firewall pipeline on. "Full pipeline" covers:
- input scan, plan resolver, output scan, GW33 output detectors, customer rules;
- audit to the console;
- the kill switch and routing.

**Exit (L36-1, extended):**
- A valid console key gets a completion, streaming and non-streaming, from a recording upstream for each provider family shipped in the slice.
- An invalid key gets 401 `invalid_api_key`.
- A blocked prompt makes **zero** upstream calls.
- The trace, the metrics and the console row agree. They are joined by a per-run nonce in the prompt, not by "the latest row".

The run uses the production compose service.

## 2. Owner decisions (11 Nov 2026)

| # | Decision | Source |
|---|---|---|
| D1 | Feature 1 ships **all v1 providers**: openai, anthropic, azure, google, aws_bedrock, mistral, cohere, deepseek, huggingface, meta, ollama, custom | owner |
| D2 | Model API keys: "the industry standard that scales to 100k RPS while keeping security". Resolved in §4.4 as envelope-encrypted credentials distributed with signed state and decrypted in gateway memory once per record version, with no control-plane call on the request path | owner delegated; plan |
| D3 | Model changes in the console reach gateways **live, within seconds** (signed-state feed) | owner |
| D4 | Models whose key comes from a server env var (`api_key_env_var`) are **refused in production** | owner |
| D5 | Build locally first, scale to this machine's limit, and keep the design hardware-independent (throughput scales with cores/workers); cloud tests later | owner |

## 3. Current behaviour (evidence from the Phase 1 explorations and the Phase 3 reviews)

Production profile today, as built by `edge/production_profile.py` and `edge/serve.py`:

| Dependency | Production today | Consequence |
|---|---|---|
| identity, plans, kill switch, admission, quota, org limits, incident gate | real, from signed state (S2–S4, GW27, GW29) | — |
| `provider` | empty `StubProviderClient` (`serve.py:175,237`) | no completion can be served |
| `routing` | unset; `build_routed_provider` imported, never called | — |
| `catalog` | `empty_catalog_source` | `/v1/models` is `[]` |
| `input_detector` | `no_findings_detector` | **fail-open hazard**: admission is now real, so the old "identity refuses first" argument (`chat_deps.py:194-196`) no longer holds |
| `phase_resolvers` | unset, so `MinimalOutputResolver` is used for both phases (`phase_resolvers.py:100-102`) | plan rules not enforced |
| `output_detector` / `error_scanner` | unset / `no_error_findings` | no output scan base |
| audit | `IncidentAuditTap(downstream=None)` drops every record (`incident_wiring.py:84-86`) | console sees nothing |
| Publisher | `_NoAudit`; still publishes the 44-series re-hydrator family (N14) | false `amf_store_memory_reachable 0`, duplicate series |
| response store | per-worker memory store | `/v1/responses` breaks across workers |
| unauth audit (GW35) | unwired | failed-credential records dropped |
| compose | no production gateway service; `gateway-v2` is dev-profile only | L36-1 cannot run from compose |

Further facts the Phase 3 reviews established, re-checked for this revision:

- **N5 (confirmed).** Customer conditions (GW26) reach detectors only through a dev `ContextVar`. `run_in_executor` does not copy it (`executor.py:115`), so on four off-loop paths a peer org's request is scanned with the primary org's rules. `feed.conditions()` also returns the latest program, not the request's pinned plan.
- **Customer regex is not pre-emptible (confirmed, measured).** `detect/conditions.py:190-208` runs Python `re` in a thread with `join(budget_s)`. `re` holds the GIL. `^(\w+\s?)+$` on 25 characters took 1.31 s against a 100 ms budget and froze every thread in the worker.
- **Signed state.** It has kinds PLAN, KEY, KS and BUDGET only (`domain/state.py:43-49`). Two places hard-code that set:
  - `_stamp_cursors` (`runtime/state_sig.py:410-437`) raises on an **unknown** kind and on a **missing** kind. A gateway built before a new kind refuses every stamp that names it, so the whole fleet fails closed.
  - The re-hydrator's `ALL_KINDS` (`state_control/rehydrate.py:63`).
- **The writer assigns the record version** after the body is built, so a body cannot bind its own version.
- **SSRF guard gap.** `dispatch/url_guard.py:73` refuses `is_private` addresses only. `100.64.0.0/10` (carrier-grade NAT) is neither private nor reserved in Python's `ipaddress`, so it passes today.
- **Routing table.** `RoutingTable.candidates_for` scans every org linearly (`routing_types.py:203-207`).
- **GW29 keys by alias only.** The kill switch, model isolation and the incident catalogue key on the bare alias and load the boot-time `AMF_MODEL_CATALOG` file (`edge/incident_catalog.py:56`). Two orgs that both call a model `gpt-4o` share one switch, and a console change never reaches them.
- **`bind_surface` has 11 call expressions in 7 edge modules.** Revision 1 said 12.
- **Control source of truth:** `core_llmmodelconfig` holds a Fernet-encrypted key, `api_base`, the LiteLLM `model_id`, the provider enum, sensitivity, tags and `is_active`.
  - Some rows have a NULL org.
  - Some rows are v1-internal guard models.
  - Some rows were encrypted with the `SECRET_KEY`-derived Fernet fallback.

## 4. Design

### 4.1 Slices (one feature at a time; each is its own PR into `develop` with full validation)

Revision 1 had one F1a covering pipeline wiring, a new signed kind and dispatch. The reviewers showed that each of the three has its own rollout risk, so F1a is now split into three slices. Each slice leaves `develop` releasable.

| Slice | Scope | Exit |
|---|---|---|
| **F1a-1 Pipeline safety** | Detector factory (N5), linear-time customer regex, phase resolvers, base input/output detectors, bounded audit sink + lifespan, N14, GW35 unauth audit, `ValkeyResponseStore`, honest boot log, core dumps off. Provider still unwired | Production boot: a BLOCK prompt gives 403 plus a console row (nonce-joined); an allowed prompt gives a **declared** 503 `provider_unavailable`, never a fail-open pass; the N5 regression test is green on every off-loop path; one tenant's catastrophic regex does not stall another tenant |
| **F1a-2 MODEL state** | Forward-compatible stamp (shipped first, see §4.3), MODEL kind, projector rows and filters, gateway applier with per-org cold fetch, catalogue, per-key `allowed_models` / `default_model` / `auto`, sensitivity filtering, GW29 re-keyed to `(org, alias)` | Console model changes are visible on `/v1/models` and in the routing snapshot within the freshness bound; an older gateway keeps serving through a stamp that names MODEL |
| **F1a-3 OpenAI-compatible dispatch** | Envelope credentials, pinned per-base-URL pools with per-org fairness, SSRF, per-provider param shaping, error mapping, `pipeline_trace.routing`, upstream metrics, compose `gateway-v2-prod` | L36-1 live for openai, deepseek, mistral, ollama, huggingface, meta, cohere (compat), google (Gemini OpenAI-compat) and custom |
| **F1b** | Native adapters: Azure OpenAI (deployment path, `api-version`, `api-key` header) and Anthropic Messages (request and SSE translation) | L36-1 for azure and anthropic through protocol-faithful fake upstreams |
| **F1c** | AWS Bedrock Converse / ConverseStream (SigV4, AWS event-stream framing) | L36-1 for aws_bedrock through a SigV4-verifying fake |
| **F1d** | Load and scaling on this machine, with a hardware-scaling curve | Published curves; no regression in p99 overhead |

### 4.2 Pipeline safety (F1a-1)

- **Detector factory (N5).**
  - Shape: `DetectorFactory(plan, phase, surface, stage) -> Callable[[str], Sequence[Finding]]`.
  - It compiles the customer `ConditionProgram` once per `(org, plan.version)`, cached one entry per org under a lock. The plan is the one **pinned on the request**, not `feed.conditions()`.
  - The closure captures the immutable program by value, with no `ContextVar`.
  - Both profiles use it, and `request_org` is deleted.
  - Every `bind_surface` call site takes the factory (11 call expressions in `openai_chat`, `openai_nonstream`, `chat_stream`, `embeddings_input`, `mcp`, `rag_stages` and `phase_resolvers`). Moderations pass `plan=None` (built-ins only).
- **Customer regex is pre-emptible (blocker; measured on this host for this revision):**

  | Engine | Result on the reviewers' patterns |
  |---|---|
  | `google-re2` | `^(\w+\s?)+$` on the failing input in **155 µs**; it rejects back-references and look-around at compile time |
  | `regex` with `timeout=` and **`concurrent=True`** | `(a\|aa)+$` raised `TimeoutError` at 100 ms, and another thread ran 93 ticks during it |
  | `regex` without `concurrent=True` | Timed out too, but the other thread ran **1 tick**: the GIL stayed held |

  - Policy: compile with RE2 when the pattern is RE2-compatible. Otherwise use `regex` with `concurrent=True` and `timeout` set to the request's remaining scan budget, which is derived from `ResourceContract.scan_window_s()`, never a literal.
  - A timeout yields an UNAVAILABLE finding, routed through the rule's `on_unavailable` posture (never empty-as-clean).
  - A pattern that neither engine compiles is refused at publish time with `policy_regex_invalid`.
  - Tests: both reviewer patterns; a two-tenant throughput test (tenant A's pathological pattern while tenant B's p99 stays inside its budget); a fuzz test that the RE2 and `regex` paths agree on match/no-match for RE2-compatible patterns.
- **Resolvers and base detectors:**
  - Production uses `phase_resolvers=phase_resolver_factory()`.
  - The base input and output detectors are the deterministic GW09 set.
  - These helpers move out of `dev_components.py` into a production module. `dev_components` re-exports them, so dev keeps working.
- **Audit:**
  1. Build admission before the incident wiring, so `bounds.audit_depth` exists.
  2. Build `AuditSink(ValkeyAuditStore(store_client, store_keys), StreamBudget(...), queue_depth=bounds.audit_depth)`, promoting `DevAudit`'s tested pieces rather than writing a second sink.
  3. Pass `audit=sink.emit` into `build_incident`.
  4. Add a lifespan hook that starts the writer at startup and drains then cancels it at shutdown, before the runtime closes the client.
     - A start failure fails **readiness**, not just a log line.
  5. Give the stream budget a memory sampler, adopted from the existing worker sampler.
  6. Give the audit record a worker index, so a drop is attributable to a worker.
  7. Publish a live audit metrics adapter instead of `_NoAudit`.
- **N14:** `Publisher.rehydration` becomes optional and the gateway passes `None`.
- **GW35 unauth audit:** wire `build_unauth_gate` and its counters.
- **Response store:** `ValkeyResponseStore` on the runtime store client.
- **Process hardening:** `RLIMIT_CORE=0` and `PR_SET_DUMPABLE=0` at boot, because decrypted provider keys live in worker memory from F1a-3 on.
- **Boot log:** each line states what is real and what is declared-unavailable. No line says "ready" for a seam that is a stub.

### 4.3 MODEL signed kind (F1a-2)

**Step 1, shipped alone before any MODEL record exists: a forward-compatible stamp.**
- `_stamp_cursors` verifies the HMAC over the raw cursor map.
- It ignores kinds this build does not know, and requires every kind this build **applies**.
- The re-hydrator derives its kinds from the same "applied kinds" set instead of `ALL_KINDS`.
- Rollout order: re-hydrators first, then gateways, and only then does the projector start emitting MODEL. An older gateway then keeps serving through a stamp that names MODEL, instead of failing closed fleet-wide.
- Tests:
  - a stamp with an unknown kind verifies;
  - a stamp missing an applied kind is still refused;
  - a tampered unknown-kind entry still fails the HMAC.

**Step 2: the kind.**
- **Domain.**
  - Add `StateKind.MODEL`. The record is keyed by org and holds that org's model catalogue as one document.
  - Each entry carries:
    - `alias`, the client-facing `model_name`, case-folded for lookup.
      - Today control accepts any non-empty name up to 128 characters, spaces included (`llm_model_serializer.py:79-82`). The gateway's alias pattern `[A-Za-z0-9._:-]` rejects `/` (`routing_types.py:72-74`).
      - Both sides now share one grammar: at most 128 characters of `[A-Za-z0-9._:/@+-]`.
      - The console refuses other names at save.
      - The projector omits existing non-conforming rows with reason `invalid_identifier`.
    - `provider` and `upstream_model`. The provider prefix is stripped by a per-provider table; it is **never** stripped for `custom` or OpenRouter-style routes.
    - `base_url`, `api_version` (Azure) and `region` (Bedrock).
    - `sensitivity`, `tags`, `weight`, `priority`, `rate_limit_rpm` and `mode` (chat or embeddings).
    - `cred` (§4.4).
  - Routing limits: at most 8 candidates per alias, with a unique `(provider, base_url, upstream_model)` destination per candidate.
- **Absence semantics.**
  - Every active org has a MODEL record, possibly with an empty list. Offboarding writes a **tombstone**.
  - A record that fails to verify or parse affects only that org: its models return 503 `model_state_unavailable`, and every other org keeps serving.
  - The BUDGET-style "absent means default" reading is wrong here, because absent must not mean "no models, nothing to refuse".
- **Projector.**
  - Reads `core_llmmodelconfig` with its org slug in the same read-only snapshot as the other kinds.
  - Rows that cannot be served are omitted **per row**. Each omission is named in a projector note and counted in `amf_projector_model_rows_omitted_total{reason}`, with `# HELP` and `# TYPE`. The omission reasons are:
    - `api_key_env_var` set (D4);
    - NULL org;
    - v1-internal guard model;
    - unknown provider;
    - invalid identifier;
    - `custom` / `ollama` base URL fails the static SSRF checks (§4.5);
    - the key cannot be decrypted. This includes `SECRET_KEY`-fallback rows, and a control management command re-encrypts those rows.
  - One bad row never fails the org or the fleet, unlike `RoutingConfigError` at boot.
  - **Round order:**
    1. Removals and credential changes, which are safety-relevant, are published **before** the safety heartbeat, with plans and key revocations.
    2. Additions follow the heartbeat, bounded per round.
    3. A MODEL backlog drains last, so it can never delay a revocation.
- **Gateway.**
  - A snapshot and applier modelled on `admit/budget_state.py`, gated in `READY_KINDS`.
  - **Cold start** follows the KEY pattern: an org's MODEL record is fetched on first use, single-flight per org, and then kept current by the feed. A worker does not load every org at boot.
  - **Rollback refusal:** a MODEL version older than the one applied is refused, so a replayed record cannot restore a revoked credential.
  - The applier feeds a **live** routing source and the `CatalogSource` from the same snapshot, so the listed models are exactly the routable ones (v1 lesson, `llm_router.py:2017-2037`).
  - Lookup is a dict keyed by `(org, casefolded alias)`, which replaces the linear `candidates_for`.
- **Pinning.**
  - A request pins the MODEL version together with the plan at admission, and uses it for the whole request.
  - A MODEL view older than the freshness bound returns 503 `model_state_stale`.
  - Production uses `fallback_set=None`, so there is no hidden boot-time fallback list.
  - The trace records the pinned `feed_seq`.
- **Per-key and policy filters (v1 parity):**
  - per-key `allowed_models`, `default_model` and `auto`;
  - sensitivity and compliance filtering;
  - per-model `rate_limit_rpm`.
- **GW29 re-keyed.** The kill switch, model isolation and the incident catalogue key on `(org, alias)` and are fed from MODEL. The boot `AMF_MODEL_CATALOG` file becomes dev-only. Reroute targets are vetted against the **org's** catalogue, not the boot file.

### 4.4 Credentials (D2, F1a-3)

**Requirement:** 100k RPS with no secret-store call on the request path, keys never in logs or responses, rotation and revocation within the signed-state freshness bound, and least privilege.

**Chosen: envelope encryption in signed state.**

- **Projector, which already reads control's DB:**
  1. Decrypts the Fernet ciphertext with control's key.
  2. Re-encrypts it with AES-256-GCM under a **gateway credential key**:
     - `AMF_PROVIDER_CRED_KEYS` holds the `kid:base64key` pairs;
     - `AMF_PROVIDER_CRED_ENCRYPT_KID` names the kid to encrypt with explicitly, never "the newest".
  3. Publishes `{kid, nonce, ct}` in the entry.
- **AAD binds what the key is for:** `("amf-provider-cred/v1", org, row_id, alias, provider, base_url, kid)`.
  - A ciphertext cannot be moved to another org, model, provider or endpoint.
  - The record version is **not** in the AAD, because the writer assigns it after the body is built.
  - Rollback refusal (§4.3) covers replaying an older version.
- **Ciphertext reuse:** when the decrypted key and the AAD are unchanged, the projector reuses the previous `{kid, nonce, ct}`. A fresh random nonce every round would rewrite every org's record every round.
- **Gateway:**
  1. Decrypts once per record version in the applier, into the existing secret type from `domain/mcp_credential.py`: constant `repr`, no pickling or copying.
  2. Holds it in the immutable snapshot.
  3. Attaches the `Authorization` (or `api-key`) header **per request**. Pooled clients never carry a default auth header.
- **Rotation:**
  1. Add the new kid on both sides.
  2. Switch `AMF_PROVIDER_CRED_ENCRYPT_KID`.
  3. Retire the old kid only when `amf_projector_cred_records{kid}` shows zero records still using it.
- Removing or changing a key in the console publishes a MODEL version without it, ahead of the heartbeat (§4.3).
- **Why not "fetch from control on use":**
  - It puts control on the cold path of every worker × model.
  - It fails uncached models when control is down.
  - It needs a separate invalidation channel.
  - The signed feed already gives versioned, ordered, freshness-bounded delivery.
- **Why not copy the Fernet ciphertext as-is:** that would put control's DB key on every gateway (the v1 trust model). Re-encryption keeps control's key in control.
- **Production refusals:**
  - no `SECRET_KEY`-derived Fernet fallback;
  - empty or malformed `AMF_PROVIDER_CRED_KEYS` means refuse to start;
  - an encrypt kid that is not in the key set means refuse to start.

### 4.5 Providers and dispatch (F1a-3: OpenAI-compatible family)

- **Default base URLs** (overridable per model with `api_base`):

  | Provider | Default base URL |
  |---|---|
  | openai | `api.openai.com/v1` |
  | deepseek | `api.deepseek.com/v1` |
  | mistral | `api.mistral.ai/v1` |
  | cohere | `api.cohere.ai/compatibility/v1` |
  | google | `generativelanguage.googleapis.com/v1beta/openai` |
  | huggingface | `router.huggingface.co/v1` |
  | meta | `api.llama.com/compat/v1` |
  | ollama | requires `api_base` |
  | custom | requires `api_base` |

- **SSRF, in two layers:**
  - **Projector (static only):**
    - `https` only;
    - host syntax checked;
    - a literal IP must be `is_global`.
    - The projector does no DNS, because a DNS answer at projection time says nothing about connect time.
  - **Gateway (at connect):**
    - Resolve, then pin the address for the connection, using `pinned_transport.py` (the GW17/GW32 precedent) with a bounded TTL cache.
    - The address must be `is_global`. This closes `100.64.0.0/10` and every other non-global range, not just `is_private`.
    - Loopback and private targets are reachable **only** through an explicit `AMF_UPSTREAM_PRIVATE_ALLOW` list: ollama on a private network, and the test recorder.
    - `PinnedRefused` maps to the declared 502 `upstream_address_refused`.
- **Pools:**
  - One pooled `httpx.AsyncClient` per worker per base URL. Pool size derives from `ResourceContract`, with no literals.
  - The pool table is a **bounded LRU**, because custom base URLs are tenant-controlled. Each pool is refcounted: an evicted pool closes only after its last in-flight request.
  - **Per-org fairness:** a per-`(org, base_url)` in-flight cap, derived from the pool size, so one org cannot take a shared provider's whole pool.
  - The per-request header removes the "more keys than workers refuses to boot" constraint (`routing_boot.py:121-133`).
- **Per-provider parameter shaping (v1 parity):**
  - a `drop_params` equivalent for parameters a provider rejects;
  - `max_tokens` becomes `max_completion_tokens` for o-series models;
  - `stream_options.include_usage` where it is supported.
- **Error mapping:**
  - A provider 401 on a customer's own key (BYOK) is **not** the caller's auth failure. It becomes 502 `upstream_auth_failed` with `x-should-retry: false`.
  - Upstream timeouts, refusals and 5xx map to declared codes.
- **Streaming:** SSE comment keep-alives while the upstream is silent and the output scan is withholding.
- **Embeddings:** routed the same way when the entry's `mode` is embeddings.

### 4.6 Observability (all slices)

- `pipeline_trace.routing = {requested_model, selected_model, route_destination, model_feed_seq}`.
- Scan Detail labels provider faults as provider faults, not as a scan stage failure. Today a provider error reads like a detector failure.
- `amf_upstream_failures_total{provider, code}`, plus the projector omission and kid metrics above. All of these get `# HELP` / `# TYPE` and bounded labels; an org is never a label.
- Control runs the reconciliation loop with `WEB_CONCURRENCY=1`, or behind a lock, so two control workers never double-ingest.
- L36-1 evidence joins the trace, the metrics delta and the console row by a per-run nonce.

### 4.7 Compose and deploy

`gateway-v2-prod`:
- profile `gateway-v2-prod`, `AMF_PROFILE=production`;
- requires `AMF_STATE_VALKEY_URL`, `AMF_STATE_HMAC_KEY`, `AMF_PROVIDER_CRED_KEYS`, `AMF_PROVIDER_CRED_ENCRYPT_KID` and `AMF_Q_SAFE`;
- sets `AMF_FD_LIMIT` (the S3 boot showed 786,657 metric series under Docker's default nofile);
- binds its port to 127.0.0.1;
- sets `ulimits.core: 0`;
- `depends_on`: a healthy re-hydrator and projector.

The projector gets `AMF_PROVIDER_CRED_KEYS`, the encrypt kid and control's model key. Deploy order per §4.3.

## 5. Test and acceptance plan

**F1a-1:**
- N5 regression: an org-B request on every off-loop path uses org B's program. It fails at HEAD.
- Regex: the two reviewer patterns; the two-tenant isolation throughput test; the RE2-vs-`regex` agreement fuzz.
- Audit sink lifecycle: start, drain at shutdown, and a start failure fails readiness.
- Publisher series count without the re-hydrator family.
- Live, production boot with no provider:
  - a BLOCK prompt gives 403 plus a nonce-joined console row;
  - an allowed prompt gives a declared 503;
  - `/metrics` is consistent.

**F1a-2:**
- Forward-compatible stamp: unknown kind tolerated, missing applied kind refused, tamper refused.
- An older gateway build serves through a MODEL-bearing stamp. This is a live two-version test.
- Per-row omission for every reason.
- Tombstone; per-org bad-record isolation; rollback refusal; cold fetch is single-flight under concurrency.
- O(1) lookup; alias grammar and casefold.
- `allowed_models` / `default_model` / `auto`; sensitivity filtering.
- GW29 two orgs with the same alias: switching one leaves the other serving.
- Freshness: a console disable is visible within the bound; stale beyond it gives 503 `model_state_stale`.

**F1a-3:**
- Envelope round-trip; AAD binding (a swapped org, alias, provider or base URL fails); kid rotation and retirement gate; ciphertext reuse when unchanged.
- SSRF: `169.254.169.254`, `10/8`, `100.64/10`, `::ffff:10.0.0.1`, NAT64 and DNS rebinding between resolve and connect.
- Pools: per-org fairness, LRU eviction while a request is in flight, and no default auth header.
- Live stack: postgres, pgbouncer, redis, control, projector, re-hydrator, `gateway-v2-prod`, and a recording OpenAI-compatible upstream with per-org keys.
  1. Console key → completion (stream and non-stream); invalid key → 401.
  2. A BLOCK prompt makes 0 upstream calls (recorder `/calls`).
  3. A PII prompt is redacted before the upstream; the recorder sees the mask.
  4. Two orgs with different models and keys: each upstream sees only its own org's key.
  5. A console model disable stops serving within the freshness bound; re-enable restores it.
  6. Key rotation in the console reaches the upstream header within the bound.
  7. An `api_key_env_var` model is listed nowhere and refused with a declared code.
  8. A BYOK 401 gives 502 `upstream_auth_failed` with `x-should-retry: false`.
  9. The audit row reaches the console (`EnforcementEvent`), and the trace matches it by nonce.
  10. `/metrics` is consistent; no re-hydrator family on the gateway.
  11. Restart and Valkey-blip scenarios.

**Every slice:**
- full gateway_v2 suite, control suites (ratchet), frontend, and the CI pipelines on the PR;
- two agent reviews before merge.

**Load (smoke per slice, full in F1d):** a fixed-rate open-loop run against the recorder at increasing RPS on this machine, measuring p50/p99 overhead, error rate and CPU per request.

## 6. Risks

| Risk | Mitigation |
|---|---|
| A new signed kind fails an old fleet closed | Forward-compatible stamp ships first (§4.3, step 1); a live two-version test gates F1a-2 |
| Ciphertext persisted in the append-only state log | AES-GCM under a gateway-only key, with AAD bound to org, model and endpoint; rotation by kid; retiring and destroying a kid makes old log entries unreadable |
| A worker's memory holds decrypted keys | Core dumps off; secret type with constant `repr`; no pickling |
| Detector factory refactor touches 11 call sites | Off-loop regression test per site; full suite |
| Provider coverage depends on vendor OpenAI-compat endpoints (cohere, google, meta) | F1b/F1c add native adapters where compat is partial; L36-1 per family with a protocol-faithful fake |
| A catastrophic customer regex stalls a worker for every tenant | RE2, or `regex` with `concurrent=True` and a budget-derived timeout, before production detectors go live (§4.2) |
| A cold-fetch storm after a fleet restart | Single-flight per org per worker; fetch bounded by the guard budget, then a retryable 503 |
| Tenant-controlled base URLs exhaust pools or memory | Bounded LRU pool table; per-`(org, base_url)` in-flight cap |
| The CPU guard model is not in F1 | The semantic stage stays FLAG/UNAVAILABLE-declared until F2 (GW36 S6) |

## 7. Phase 3 review disposition and Phase 4 devil's-advocate pass

Every finding from the five reviewers was re-checked against the code at `8484c571`. Three were also re-measured on this host. A finding is **accepted** only when the evidence holds. A **modified** finding is real, but the reviewer's fix was replaced by a better one.

| # | Finding (reviewer) | Evidence re-checked | Disposition |
|---|---|---|---|
| 1 | AAD cannot bind the record version (writer assigns it later); a random nonce rewrites every record every round | Writer flow confirmed | **Accepted.** AAD = org, row, alias, provider, base URL, kid; ciphertext reused when unchanged; rollback refusal covers replay (§4.4) |
| 2 | Explicit encrypt kid; retire a kid only when unused | — | **Accepted** (§4.4) |
| 3 | BUDGET-style absence is wrong for MODEL | Absent must not mean "nothing to refuse" | **Accepted.** Record per active org, tombstone on offboard, per-org failure isolation (§4.3) |
| 4 | The all-kinds stamp check fails an old fleet closed on a new kind | `state_sig.py:418-437` raises on unknown **and** missing kinds | **Accepted, and made a separate first step.** Revision 1 called lock-step rollout "acceptable pre-cutover"; the devil's-advocate pass rejected that, because every later kind would repeat it (§4.3, step 1) |
| 5 | Round order: removals and credential changes before the heartbeat | — | **Accepted** (§4.3) |
| 6 | Cold start should follow the KEY pattern | 100k orgs × every worker at boot is the wrong shape | **Accepted**, with single-flight added (§4.3) |
| 7 | Pin the catalogue with the plan; 503 when stale; no production fallback set; `feed_seq` in the trace | — | **Accepted** (§4.3, §4.6) |
| 8 | GW29 keys by bare alias from the boot catalogue; reroute vetting mismatched | `incident_catalog.py:56` | **Accepted** (§4.3) |
| 9 | SSRF: `is_private` misses 100.64/10 | Python `ipaddress`: 100.64.0.1 is neither private nor reserved | **Accepted.** `is_global` at connect; explicit allowlist for private targets; projector checks are static only (§4.5) |
| 10 | Pools: fairness, bounded LRU, no default auth header, refcounted close, `PinnedRefused` code | — | **Accepted** (§4.5) |
| 11 | Routing limits: 8 candidates, unique destination; unify the alias grammar | Control accepts any 128-character name (`llm_model_serializer.py:79-82`); the gateway rejects `/` (`routing_types.py:72-74`) | **Modified:** one shared grammar (128 characters of `[A-Za-z0-9._:/@+-]`) enforced at console save. The reviewer's fix only rejected `/`, which would have refused names the console already accepts |
| 12 | v1 parity: per-key models, sensitivity, param shaping, keep-alives, BYOK 401 mapping, `rate_limit_rpm`, embeddings | — | **Accepted**, spread over F1a-2 and F1a-3 |
| 13 | LiteLLM prefix stripping must be per provider, never for custom/OpenRouter | — | **Accepted** (§4.3) |
| 14 | v1 sync filters: NULL-org rows, guard-named rows, `SECRET_KEY`-fallback ciphertext | — | **Accepted** as named omission reasons plus a re-encryption command (§4.3) |
| 15 | Observability: Scan Detail mislabels provider faults, routing trace, upstream metrics, `# TYPE`, audit memory sampler and worker index, boot-log honesty, `WEB_CONCURRENCY=1`, nonce-joined evidence | — | **Accepted** (§4.2, §4.6) |
| 16 | Reuse `mcp_credential`, `DevAudit` and `ValkeyResponseStore` instead of duplicating | — | **Accepted** |
| 17 | A lifespan start failure must fail readiness; disable core dumps | — | **Accepted** (§4.2) |
| 18 | `bind_surface` site count | 11 call expressions in 7 modules | **Corrected** in §4.2 |
| 19 | Customer regex: "`regex` with `timeout=` releases the GIL" (revision 1's own claim) | Measured: without `concurrent=True` the GIL stays held (1 tick vs 93) | **Corrected:** `concurrent=True` is required; the timeout is budget-derived, not a fixed 100 ms (§4.2) |
| 20 | Re-slice F1a | Three independent rollout risks in one PR | **Accepted** (§4.1) |

**Challenged and kept:**
- **Envelope encryption over fetch-on-use.** Fetch-on-use was re-argued as simpler. It still puts control on every cold path and needs its own invalidation channel, so the signed feed stays the delivery path.
- **Ignoring unknown stamp kinds.** This was challenged as a downgrade vector. The stamp is HMAC-verified over the raw map, so only a holder of the state key can add a kind, and every applied kind is still required, so nothing can be silently skipped.

---
_Generated by [Claude Code](https://claude.ai/code/session_01SDCVQVj8LRKW3CZ99zfdW1)_
