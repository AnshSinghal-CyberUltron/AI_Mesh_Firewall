# F1: production gateway serves real completions (GW36 S5 + per-org providers)

Status: 11 Nov 2026. Branch `feat/f1a-production-serving`, stacked on PR #102 and #101. This is the Phase 2 plan required by `CLAUDE.md`; nothing here is implemented until the Phase 3/4 reviews have challenged it.

## 1. Goal and exit

`AMF_PROFILE=production` with **no dev flags** serves chat completions for every organization, to that organization's own model connections, with the full firewall pipeline on. "Full pipeline" covers:
- input scan, plan resolver, output scan, GW33 output detectors, customer rules;
- audit to the console;
- the kill switch and routing.

**Exit (L36-1, extended):**
- A valid console key gets a completion, streaming and non-streaming, from a recording upstream for each provider family shipped in the slice.
- An invalid key gets 401 `invalid_api_key`.
- A blocked prompt makes **zero** upstream calls.
- The trace and the console row agree.

The run uses the production compose service.

## 2. Owner decisions (11 Nov 2026)

| # | Decision | Source |
|---|---|---|
| D1 | Feature 1 ships **all v1 providers**: openai, anthropic, azure, google, aws_bedrock, mistral, cohere, deepseek, huggingface, meta, ollama, custom | owner |
| D2 | Model API keys: "the industry standard that scales to 100k RPS while keeping security". Resolved in §4.3 as envelope-encrypted credentials distributed with signed state, decrypted in gateway memory once per record version; no control-plane call on the request path | owner delegated; plan |
| D3 | Model changes in the console reach gateways **live, within seconds** (signed-state feed) | owner |
| D4 | Models whose key comes from a server env var (`api_key_env_var`) are **refused in production** | owner |
| D5 | Build locally first, scale to this machine's limit, and keep the design hardware-independent (throughput scales with cores/workers); cloud tests later | owner |

## 3. Current behaviour (evidence from the three Phase 1 explorations)

Production profile today, as built by `edge/production_profile.py` and `edge/serve.py`:

| Dependency | Production today | Consequence |
|---|---|---|
| identity, plans, kill switch, admission, quota, org limits, incident gate | real, from signed state (S2–S4, GW27, GW29) | — |
| `provider` | empty `StubProviderClient` (`serve.py:175,237`) | no completion can be served |
| `routing` | unset; `build_routed_provider` imported, never called | — |
| `catalog` | `empty_catalog_source` | `/v1/models` is `[]` |
| `input_detector` | `no_findings_detector` | **fail-open hazard**: admission is now real, so the old "identity refuses first" argument (`chat_deps.py:194-196`) no longer holds |
| `phase_resolvers` | unset → `MinimalOutputResolver` for both phases (`phase_resolvers.py:100-102`) | plan rules not enforced |
| `output_detector` / `error_scanner` | unset / `no_error_findings` | no output scan base |
| audit | `IncidentAuditTap(downstream=None)` drops every record (`incident_wiring.py:84-86`) | console sees nothing |
| Publisher | `_NoAudit`; still publishes the 44-series re-hydrator family (N14) | false `amf_store_memory_reachable 0`, duplicate series |
| response store | per-worker memory store | `/v1/responses` breaks across workers |
| unauth audit (GW35) | unwired | failed-credential records dropped |
| compose | no production gateway service; `gateway-v2` is dev-profile only | L36-1 cannot run from compose |

Customer conditions (GW26) reach detectors only through a dev `ContextVar` that `run_in_executor` does not copy (`executor.py:115`). This is N5, confirmed: on four off-loop paths a peer org's request is scanned with the primary org's rules. `feed.conditions()` also returns the latest program, not the request's pinned plan.

Signed state has kinds PLAN, KEY, KS and BUDGET only (`domain/state.py:43-49`). No model or provider record exists. The control source of truth is `core_llmmodelconfig` (Fernet-encrypted key, `api_base`, LiteLLM `model_id`, provider enum, sensitivity, tags, is_active).

## 4. Design

### 4.1 Slices (one feature at a time; each is its own PR into `develop` with full validation)

| Slice | Scope | Exit |
|---|---|---|
| **F1a** | Pipeline wiring in production (detector factory N5, phase resolvers, base detectors, audit sink, N14, unauth audit, response store), the **MODEL signed kind** end to end, live routing table and catalogue, OpenAI-compatible provider family with per-request credentials and SSRF-pinned base URLs, compose `gateway-v2-prod` | L36-1 live for openai, deepseek, mistral, ollama, huggingface, meta, cohere (compat), google (Gemini OpenAI-compat), custom |
| **F1b** | Native adapters: Azure OpenAI (deployment path, `api-version`, `api-key` header), Anthropic Messages (request and SSE translation) | L36-1 for azure and anthropic through protocol-faithful fake upstreams |
| **F1c** | AWS Bedrock Converse / ConverseStream (SigV4, AWS event-stream framing) | L36-1 for aws_bedrock through a SigV4-verifying fake |
| F1d | Load and scaling on this machine, hardware-scaling curve | published curves; no regression in p99 overhead |

### 4.2 MODEL signed kind (F1a)

- **Domain.**
  - Add `StateKind.MODEL`. Its record is keyed by org and holds that org's model catalogue as one document (BUDGET pattern, hash-stored).
  - Each entry carries:
    - `alias` (the client-facing `model_name`);
    - `provider`, `upstream_model` (LiteLLM prefix stripped) and `base_url`;
    - `api_version` (Azure) and `region` (Bedrock);
    - `sensitivity`, `tags`, `weight`, `priority`, `is_active`;
    - `cred` (§4.3).
- **Projector.**
  - Reads `core_llmmodelconfig` with its org slug in the same read-only snapshot as the other kinds.
  - Rows that cannot be served are omitted **per row** and named in a projector note and a counter:
    - `api_key_env_var` set (D4);
    - unknown provider;
    - invalid identifiers;
    - a `custom` `base_url` that fails the SSRF policy.
  - One bad row never fails the org or the fleet, unlike `RoutingConfigError` at boot.
  - A model deactivation is safety-relevant, so MODEL is projected **before** the safety heartbeat, alongside plans and key revocations.
- **Rollout.**
  - The stamp's all-kinds check (`state_sig.py:418-437`) means every gateway and re-hydrator must ship together.
  - An older gateway would refuse the new stamp and fail closed fleet-wide.
  - This is acceptable before cutover: no production v2 traffic exists. It is recorded in the PR as a deploy-order note.
- **Gateway.**
  - A snapshot plus applier modelled on `admit/budget_state.py`, gated in `READY_KINDS`.
  - It feeds a **live** routing table source and the `CatalogSource` from the same snapshot, so the listed models are exactly the routable ones (v1 lesson, `llm_router.py:2017-2037`).
  - Lookup is a dict by `(org, alias)`, O(1); the current `RoutingTable.candidates_for` linear scan is replaced.

### 4.3 Credentials (D2)

**Requirement:** 100k RPS with no secret-store call on the request path, keys never in logs or responses, rotation and revocation within the signed-state freshness bound, and least privilege.

**Chosen: envelope encryption in signed state.**

- **Projector, which already reads control's DB:**
  1. Decrypts the Fernet ciphertext with control's key.
  2. Re-encrypts it with AES-256-GCM under a **gateway credential key** (`AMF_PROVIDER_CRED_KEYS`: `kid:base64key` pairs, the newest kid used for encryption).
  3. Publishes `{kid, nonce, ct}` in the MODEL record. The record AAD binds `(org, alias, record version)`, so a ciphertext cannot be replayed into another org's entry.
- **Gateway:**
  1. Decrypts once per record version in the applier into an `UpstreamCredential`-style secret type: constant `repr`, no pickling or copying.
  2. Holds it in the immutable snapshot.
  3. Attaches the `Authorization` (or `api-key`) header **per request** to a shared pooled httpx client per base URL.
- **Why not "fetch from control on use":**
  - It puts control on the cold path of every worker × model.
  - It fails uncached models when control is down.
  - It needs a separate invalidation channel.
  - The signed feed already gives versioned, ordered, freshness-bounded delivery.
- **Why not copy the Fernet ciphertext as-is:** that would put control's DB key on every gateway (the v1 trust model). The re-encryption keeps control's key in control.
- **Rotation:**
  - Add a new kid to both sides, then remove the old kid after one projector round.
  - Removing a key in the console publishes a MODEL version without that entry, so it stops serving within the freshness bound.
- **Production refusals:** no `SECRET_KEY`-derived Fernet fallback; empty or malformed `AMF_PROVIDER_CRED_KEYS` means refuse to start.

### 4.4 Providers in F1a (OpenAI-compatible family)

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

- **Pooling:**
  - One pooled `httpx.AsyncClient` per worker **per base URL** (not per key). The per-request header replaces the per-client default header, which removes the "more keys than workers refuses to boot" constraint (`routing_boot.py:121-133`).
  - Pool size per base URL derives from `ResourceContract` (no literals).
- **SSRF:** tenant `api_base` goes through `dispatch/url_guard.py` and `pinned_transport.py` (the GW17/GW32 precedent): https only except loopback in dev; resolved IP pinned; private ranges refused in production.
- **Embeddings:** routed the same way when the alias serves embeddings (`/v1/embeddings` uses the same snapshot).

### 4.5 Pipeline wiring (F1a)

- **Detector factory (N5).**
  - Shape: `DetectorFactory(plan, phase, surface, stage) -> Callable[[str], Sequence[Finding]]`.
  - It compiles the customer `ConditionProgram` once per `(org, plan.version)`, cached one entry per org under a lock.
  - The closure captures the immutable program by value, with no `ContextVar`.
  - Both profiles use it, and `request_org` is deleted.
  - The 12 `bind_surface` call sites take the factory; moderations passes `plan=None` (built-ins only).
- **Customer regex must be pre-emptible (blocker for F1a).** This was Reviewer A's finding on #100, and it is confirmed. Today a customer regex runs on Python `re` in a thread with `join(budget_s)` (`detect/conditions.py:190-208`). `re` holds the GIL, so the join cannot interrupt a catastrophic match. `^(\w+\s?)+$` on 25 characters took 1.31 s against a 100 ms budget, returned no timeout code, and froze every thread in the worker. The shape filter (`:115-121`) misses it.
  - Fix: compile customer conditions with a linear-time engine. Use `google-re2` where the pattern is RE2-compatible; otherwise use the `regex` package with `timeout=`, which releases the GIL and raises. A pattern neither engine accepts is refused at publish time with `policy_regex_timeout`.
  - Tests: both reviewer patterns, plus a throughput test proving that one tenant's pattern cannot stall another tenant's request.
- **Resolvers:** `phase_resolvers=phase_resolver_factory()` in production. Base input and output detectors are the deterministic GW09 set. These helpers move out of `dev_components.py`.
- **Audit:**
  1. Build admission before the incident wiring, so `bounds.audit_depth` exists.
  2. Build `AuditSink(ValkeyAuditStore(store_client, store_keys), StreamBudget(...), queue_depth=bounds.audit_depth)`.
  3. Pass `audit=sink.emit` into `build_incident`.
  4. Add a lifespan hook: start the writer at startup; drain then cancel at shutdown, before the runtime closes the client.
  5. Publish a live audit metrics adapter instead of `_NoAudit`.
- **N14:** `Publisher.rehydration` becomes optional and the gateway passes `None`.
- **GW35 unauth audit:** `build_unauth_gate` and its counters.
- **Response store:** `ValkeyResponseStore` on the runtime store.
- **Boot log:** each line states what is real.

### 4.6 Compose and deploy

`gateway-v2-prod`:
- profile `gateway-v2-prod`, `AMF_PROFILE=production`;
- `AMF_STATE_VALKEY_URL`, `AMF_STATE_HMAC_KEY`, `AMF_PROVIDER_CRED_KEYS` and `AMF_Q_SAFE` required;
- `AMF_FD_LIMIT` set (the S3 boot showed 786,657 metric series under Docker's default nofile);
- port bound to 127.0.0.1;
- `depends_on`: healthy re-hydrator and projector.

The projector gets `AMF_PROVIDER_CRED_KEYS` and control's model key.

## 5. Test and acceptance plan (F1a)

- **Unit and property tests:**
  - envelope round-trip, AAD binding (a swapped org fails) and kid rotation;
  - per-row projector omission;
  - O(1) catalogue lookup;
  - detector factory: an org-B request on every off-loop path uses org B's program (fails at HEAD, which is the N5 regression test);
  - audit sink lifecycle;
  - Publisher series count without the re-hydrator family.
- **Live stack:** postgres, pgbouncer, redis, control, projector, re-hydrator, `gateway-v2-prod`, and a recording OpenAI-compatible upstream with per-org keys.
  1. Console key → completion (stream and non-stream); invalid key → 401.
  2. A BLOCK prompt makes 0 upstream calls (recorder `/calls`).
  3. A PII prompt is redacted before the upstream; the recorder sees the mask.
  4. Two orgs with different models and keys: each upstream sees only its own org's key.
  5. A console model disable stops serving within the freshness bound; re-enable restores it.
  6. Key rotation in the console reaches the upstream header within the bound.
  7. An `api_key_env_var` model is listed nowhere and refused with a declared code.
  8. An SSRF `custom` base URL (169.254.169.254, 10.x) is omitted by the projector with a note.
  9. The audit row reaches the console (EnforcementEvent), and the trace matches.
  10. `/metrics` is consistent; no re-hydrator family on the gateway.
  11. Restart and Valkey-blip scenarios.
- **Regression:** full gateway_v2 suite, control suites (ratchet), and the CI pipelines on the PR.
- **Load (smoke here, full in F1d):** fixed-rate open-loop run against the recorder at increasing RPS on this machine; p50/p99 overhead, error rate, CPU per request.

## 6. Risks

| Risk | Mitigation |
|---|---|
| Lock-step rollout of a new kind | Pre-cutover only; documented deploy order (re-hydrators, projector and gateways together) |
| Ciphertext persisted in the append-only state log | AES-GCM under a gateway-only key; rotation by kid; the log never holds plaintext |
| Detector factory refactor touches 12 call sites | Off-loop regression tests per site; full suite |
| Provider coverage depends on vendor OpenAI-compat endpoints (cohere, google, meta) | F1b/F1c add native adapters where compat is partial; L36-1 per family with a protocol-faithful fake |
| Catastrophic customer regex stalls a worker for every tenant (GIL-bound `re`) | Linear-time engine (RE2 / `regex` with timeout) before production detectors go live (§4.5) |
| CPU guard model not in F1 | Semantic stage stays FLAG/UNAVAILABLE-declared until F2 (GW36 S6) |
