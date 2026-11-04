# Runbook v3.1 progress

Status date: 2 November 2026. Source of the card text: `docs/AI_MESH_MASTER_RUNBOOK_v3.1_CLEAN.docx`, Part 00. This file does not change that document.

GW26 and GW30 have new code on this tree and have been exercised on the local stack. Neither exits. The live lines below say exactly what was observed and what was not. GW36 was started on 2 November; slices S0–S3 are done (S3 on 3 November: the production profile reads real signed state, and a bad key is a 401), S4–S7 remain, so it does not exit. GW17b, GW17c, GW27, GW28, GW29, GW31, GW32, GW33, GW34 and GW35 have no new card work from this pass. The runbook says each of those cards is required before GW21 and GW23, and that a card does not exit on unit tests alone.

## GW26 — Customer policy rules and surface scoping

Depends on GW05 and GW07. Required by GW18, GW27, GW31, GW21, and GW23.

Objective in the runbook: restore Policy Management so an organization can write keyword, regex, pattern, and semantic conditions, scope them to global, chat, RAG, vector, MCP, embeddings, or a named pipeline, and have that scope enforced on every matching surface.

### Implementation work

| Runbook item | State | What is true in the tree |
|---|---|---|
| Extend `Rule` with a condition (type, field, direction, pattern or keyword list, detector class), compile it once into the matcher, and fail closed on a regex timeout | In the gateway | `Rule.condition` is optional and defaults to none, so older plans still hash. Keyword match is a case-insensitive substring. A nested quantifier, a quantified wildcard, a quantified alternation, or a pattern that does not compile is refused at scan time with `policy_regex_timeout` (HTTP 400 `invalid_request_error`). The pattern is not executed. The plan still loads. |
| Enforce surfaces in the resolver, and add a console control to set them | Partial | There is no `GLOBAL` surface. A rule with no surface list uses the domain default: pipeline → chat and embeddings, rag → rag and vector, mcp → mcp. An explicit list replaces that default. The Policy Management rule form has a surface checklist. The resolver skips a finding when the route surface is not in the rule's list. `scan_conditions` now takes the same surface and skips a refused pattern that does not name it. Chat input, embeddings, MCP input and output, the non-streaming chat output scan, and the streaming chat detector pass their surface. Omitting the surface still selects by phase. A rule with `pipeline_stage` set to `query`, `retriever`, `ranker`, or `generator` matches only that stage. On 2 November a temporary block keyword `Gw26StageQuery` with stage `query` was published (169 rules) and a chat call returned HTTP 200 `allow` with the keyword present. The rule was deleted and the org republished at 168. No live route passes a stage name, so this card did not observe the rule firing. That positive case waits on GW31. A completed customer match is held until the release contains the whole span, so a keyword whose tail looks like a card is not emitted in pieces. The echo provider's diagnostic re-scan catches `PolicyRegexRefusal` and does not withhold a request the route already allowed. |
| Publish Policy Management rules into the v2 plan | Wired for pipeline, rag, and mcp | Saving a policy or a rule republishes every enabled policy on the org, plus the firewall drafts. `rewrite` and `model_downgrade` are omitted from the document and named in `unsupported`, so a bad action cannot replace the previous plan. A compile failure leaves the previous plan serving. Unknown categories map to `exfil`. |
| Write each organization's plan to its own key | Wired, and checked with two orgs | The org key is `{rv2}:plan:{org}`. The primary org still falls back to `{rv2}:dev:firewall-plan` when its org key is empty. A peer org never reads that dev key. The dev profile accepts an optional second bearer (`AMF_DEV_PEER_API_KEY`, `AMF_DEV_PEER_ORG_ID`; compose defaults `dev-peer-key` and `org-b`). The customer matcher uses the request org's program. On 2 November the dev key was still length 1367 and sha256 `8017db3b907c550592ce910a70c9ceaf571685c7a28be794a8c7e4ecccf92aa4`. |

The 27 October decision was to ignore surfaces. The wired routes no longer do that for findings that reach the resolver. Runbook decision 10 still says "Confirm." This file records the reversal. The runbook text was not edited.

`semantic` conditions are not a separate matcher. A detector-class rule joins builtin findings only when the rule has no customer condition.

### Live data created on the zeroshield org

These rows are in Postgres and in `{rv2}:plan:dev-org`. They are not source edits.

- Policy `GW26_COMPETITOR_CHAT` (id 47), domain pipeline.
- Rule "Block AcmeRivalGW26 on chat": keyword `AcmeRivalGW26`, action block, field prompt, surface chat only.
- Rule "Redact ZsCardToken99 globally": keyword `ZsCardToken99`, action redact, field response, surface global, replacement `[REDACTED]`. This is a test token, not a card number.
- A third rule, pattern `(a+)+`, field prompt, surface list embeddings, was created for L26-4 on 2 November (rule id 169), observed, then deleted. The republish returned `rules: 168`. The pattern is not in `{rv2}:plan:dev-org`.
- `{rv2}:plan:org-b` holds one chat keyword, `OnlyOrgBgw26`. It is not a console org. The dev key was not the body of that write.

The save responses named one omitted rule: `PKG2_PIPE_UNAUTHORIZED_ADVICE/Rewrite definitive medical advice (rewrite)`.

### Live tests

L26-4 and L26-5 were observed on the live gateway. L26-3 was replayed on chat for the 16 pipeline policies. The rag and mcp packages stay registered differences. L26-1 and L26-2 are still unmet on MCP and RAG.

| Test | Runbook pass line | State |
|---|---|---|
| L26-1 | A competitor-name rule scoped to chat only. Blocked on chat; allowed on MCP and RAG | Chat only. In the Attack Simulator at `http://127.0.0.1:8180/?tab=firewall-1-1`, signed in as `admin@zeroshield.io`, prompt `Tell me about AcmeRivalGW26 pricing` was BLOCKED, HTTP 400, 19 ms, request `zs-dev-org-b6525df0a2da8f4434e67d80`. Stages: Auth ALLOW, Rate Limit ALLOW, Input Scan BLOCK, Model Routing SKIP, Model Output SKIP, Output Guardrail SKIP. The same page, prompt `what is the refund window`, was ALLOWED, HTTP 200, 25 ms, request `zs-dev-org-3317cafc4a2684a2a974b9bd`, model text `echo: what is the refund window`. Curl with model `echo-1` matched those two status codes. MCP: `MCPServerRegistration` count for this org is 0. `POST /v1/mcp` `tools/call` returned HTTP 404 JSON-RPC `-32000` "No active MCP server with that name exists for this organization." The scan did not run. RAG: `POST /v1/rag/query` returned HTTP 501 `not_implemented:rag`. A chat-only rule does not select on `Surface.RAG` in the resolver unit test. That is not a live RAG allow. |
| L26-2 | An output redact rule on all surfaces masks the value in chat, responses, MCP results, and RAG chunks | Chat, `/v1/responses`, and streaming chat. The simulator prompt `quote ZsCardToken99 please` returned HTTP 200, Output Guardrail REDACT, model text `echo: quote [REDACTED] please`. Curl on `/v1/chat/completions`, `/v1/responses`, and `stream: true` was HTTP 200 with the mask present and the raw token absent. With SSE stream mode on, the simulator showed the same mask after the stream hold fix. MCP and RAG were not claimed: no MCP server is registered, and RAG is 501. |
| L26-3 | Replay the 48 production `PKG2_*` policies. Each enforces as in v1, or is a registered expected difference | Pipeline package replayed on chat on 2 November. Rag, mcp, the missing four, the rewrite rule, monitor-on-the-wire, and the catalog replacement tokens are registered differences. Detail is under the table. |
| L26-4 | A pathological regex finishes in bounded time and fails closed with a declared code | Observed on 2 November, then removed. Rule id 169, pattern `(a+)+`, surface embeddings, field prompt. `POST /v1/embeddings` returned HTTP 400 `policy_regex_timeout` in 24.0 ms. `POST /v1/chat/completions` with `what is the refund window` stayed HTTP 200, model text `echo: what is the refund window`. The rule was deleted and the org republished (`rules: 168`). After the feed poll, the refund prompt was HTTP 200 and embeddings returned 403 `scan_failure` because echo-1 does not return a JSON vector. That 403 is not a pass. |
| L26-5 | Two orgs with conflicting rules. Each enforces only its own plan | Observed on the live gateway on 2 November. Primary bearer: `AcmeRivalGW26` HTTP 400 `input_blocked`; `OnlyOrgBgw26` HTTP 200 `echo: quote OnlyOrgBgw26 please`. Peer bearer `dev-peer-key` for org `org-b`: `AcmeRivalGW26` HTTP 200; `OnlyOrgBgw26` HTTP 400 `input_blocked`. The dev key bytes did not change. The browser is still one org. Production boot is still GW36. |

L26-3 chat replay, 2 November, model `echo-1`. The pipeline catalog is 16 policies and 55 rules. Each rule was sent once as its own chat prompt. Request times were 2.9–28.4 ms (399.4 ms summed). No probe rule was left behind, and `{rv2}:dev:firewall-plan` stayed length 1367 and sha256 `8017db3b907c550592ce910a70c9ceaf571685c7a28be794a8c7e4ecccf92aa4`.

- 23 block rules: HTTP 400 `input_blocked`, action `block`. Twenty-one were decided by a `pol-PKG2_*` rule. `Block instruction-override phrases` was decided by `fw-prompt_injection-input`. `Block known jailbreak personas` was decided by `fw-jailbreak-input`. The action matched the catalog in both of those.
- 13 redact rules: HTTP 200, action `redact`, the matched bytes absent, `[REDACTED]` present. Email, US phone, and US SSN were decided by `fw-pii-input`. The other ten were decided by a `pol-PKG2_*` rule. The catalog replacement tokens, including `[REDACTED_EMAIL]`, were not written. A direct email check had `[REDACTED]` and not `[REDACTED_EMAIL]`.
- 18 monitor rules: HTTP 200, action `allow`, phrase present, no deciding rule. They are published as mode `monitor`. The resolver records that class of finding and does not change the disposition, so the wire stays `allow`. That is a registered difference from a visible monitor tag.
- 1 rewrite rule, `Rewrite definitive medical advice`: HTTP 200, action `allow`, phrase present. It stays omitted from the plan and named in `unsupported`.
- Three fixtures match more than one catalog rule of the same action. Card PAN matches `PIPE_PII` and `PIPE_PCI`, both redact, decided by `pol-PKG2_PIPE_PII-8`. The two profanity redact rules share a fixture, decided by `pol-PKG2_PIPE_PROFANITY-39`. The shell exfil block shares a fixture inside `PIPE_CODE_EXFIL`, decided by `pol-PKG2_PIPE_CODE_EXFIL-50`.

Not replayed on a live route, and registered as that difference: 14 rag policies (49 rules) apply to rag and vector, and `POST /v1/rag/query` is HTTP 501. 14 mcp policies (56 rules) apply to mcp, and `POST /v1/mcp` is HTTP 404 with no server. The 13 `VEC_*` specs are not `PKG2_*`. The catalog has 44 policy specs and the docx says 48. The docx names no missing four, and none were invented.

Embeddings on this dev profile: every `/v1/embeddings` call, including a clean prompt and the competitor name, returns HTTP 403 `scan_failure` because echo-1 returns text and the embeddings drain requires a JSON vector. That status is not `input_blocked`. The in-process ASGI test uses a provider that yields a vector and shows a chat-only keyword is not an input block on embeddings.

Auth and Rate Limit in the simulator stayed ALLOW on the blocked request. Those two stages are still hard-coded (GW35). The page does not invent kill-switch, policy, or model-input stages.

### Unit tests that exist for this card

On 2 November the GW26 detect, plan, edge, and dev-profile files were run again: 72 passed. ruff, mypy --strict on the touched modules, and the size check passed. Django tests were not run; the repo venv has no Django.

- `gateway_v2/tests/detect/test_gw26_conditions.py`
- `gateway_v2/tests/edge/test_gw26_customer_rules.py`
- `gateway_v2/tests/plan/test_gw26_org_plan.py`
- `gateway_v2/tests/resolve/test_gw26_surface_scope.py` (resolver surface filter; it does not close L26-1)

Control Django tests were not run here. The repo virtualenv and `gateway_v2/.venv` do not include Django. The control container is the process that published the live plan.

## GW30 — Vector store adapter interface and connectors

Depends on GW11. Required by GW21 and GW23.

Objective in the runbook: one adapter interface for every vector store, with connectors for Pinecone, Chroma, Milvus/Zilliz, Qdrant, Weaviate, pgvector, Elasticsearch/OpenSearch, Redis, MongoDB Atlas, AWS (OpenSearch Serverless, Bedrock Knowledge Bases), GCP (Vertex AI Vector Search) and Azure (AI Search); interface query, upsert, delete, list and create collections, health, and capability flags (filters, namespaces, hybrid search); credentials per org from control, never in responses or logs; a connector that cannot do an operation answers a declared `unsupported_operation`, never an empty result; no silent fallback to another provider (the v1 `_resolve_vector_client` "any available" defect).

This card was built from the `.kiro/specs/vector-store-adapter-gw30` spec, bottom-up on the import-linter layer order, following the shipped `dispatch/provider.py` idiom (injected `ProviderClient` / `StubProviderClient`). It does not add a route or any pipeline behaviour; the runbook puts `/v1/rag/*`, `/v1/vector/*` and the four-stage pipeline in GW31, and `edge/rag.py` is still the 501 stub. So GW30 ships the adapter layer GW31 will wire, and nothing in the browser exercises it yet.

### Implementation work

| Runbook item | State | What is true in the tree |
|---|---|---|
| One adapter interface: query, upsert, delete, list/create collections, health, capability flags | In `dispatch` | `dispatch/vector_adapter.py` defines the frozen `@runtime_checkable Vector_Adapter` protocol (the six async ops + `capabilities()`), the frozen slotted value types (`AdapterResult`, `VectorRecord`, the six request types, `Capabilities`), and the posture codes live in `domain/posture.py`. Outcome is distinguishable by `code` alone. |
| A connector that cannot do an operation answers `unsupported_operation`, never an empty result | In `dispatch` | `dispatch/vector_operations.py` enforces the capability flags on the op path: a filter/namespace/hybrid request against a connector that declares that flag false returns `AdapterResult(code=unsupported_operation)` with `records is None` (absent), never an empty `()` success. |
| No silent fallback to another provider | In `dispatch` | `dispatch/vector_registry.py` selects by exact `store_id` match only; an unregistered id returns `store_not_configured` on a frozen result, never "any available". |
| Credentials per org from control, never in responses or logs | In `dispatch` | `dispatch/vector_credentials.py` holds `OrgCredential` (redacted repr, no serialization method), the injected `Credential_Source` seam, and the scope guard (resolved org must equal request org before any store call, else `credential_scope_mismatch`; missing org -> `org_identity_invalid`). `dispatch/vector_leak_guard.py` is the pre-emission leak guard: any reproduction of one or more consecutive characters of a genuine credential (plaintext or base64/base64url/hex/percent) withholds the whole emission and returns `credential_leak_withheld` carrying only a reference id. The literal "one or more consecutive characters" rule (R9, runbook GW09 byte-verified redaction, LGW17-6 split-secret floor) is honoured with no minimum-run threshold; false positives are handled by an entropy anchor on whether the secret is genuine credential material, not by relaxing the match. |
| Per-operation bounds, fail closed | In `dispatch` + `runtime` | `dispatch/vector_bounded_op.py` runs each op under `ResourceContract.vector_operation_timeout_s()` and the result-size ceilings (`runtime/resources.py` + `resources_vector.py`); a timeout/transport failure -> `provider_unreachable`, a mid-op bound breach -> `vector_capacity_exceeded` (partial discarded), an unset bound -> `vector_capacity_unset`. No capacity literal outside `runtime/resources.py`. |
| The 13 named connectors over an injected transport | Stub in the tree; real transports for the live gate | `dispatch/vector_connectors.py` has the `Vector_Transport` seam, `Stub_Vector_Connector` (socket-free, offline tests), the 13-connector capability table as a factory (no module-level mutable), and `Named_Vector_Connector`. The real per-store SDK/HTTP clients are the deferred live gate; the `dispatch/vector_transports/` package (one module per store, not on the production import path) implements real transports for the stores that have a local backend. It replaced the single 1309-line `vector_live_transports.py` on 2 November (see "GW30 gate fix" below). |

Offline gates on 2 November: the `lgw30` suite is 78 passed; `mypy --strict`, `ruff`, `lint-imports` (3 contracts kept, 0 broken), and the AST gates (`check_capacity_literals`, `check_no_module_mutable`, `check_frozen_dataclasses`, `check_http_outside_edge_resolve`) all clean. The full offline suite showed 0 new regressions (two pre-existing unrelated failures in `edge/dev_components.py` tenant-scale and the frontend console inventory, both outside GW30's change surface).

### Live tests

The runbook's exit evidence is "live Docker/staging, real wire bytes"; L30-1 is "same results and declared errors on every connector", with no cloud mandate and no explicit provider count. The three gates were run through the shipped adapter seam (`VectorRegistry` / `build_connector` / `run_bounded_operation` / `resolve_scoped_credential`) against real store containers in `docker-compose.gw30-stores.yml`, injecting the real transports. The 13 connector ids are the spec's expansion of the runbook's grouped provider list (Milvus/Zilliz and Elasticsearch/OpenSearch are grouped there; AWS lists two sub-items).

| Test | Runbook pass line | State |
|---|---|---|
| L30-1 | Same query/upsert/delete suite against every connector; same results and declared errors on all | LIVE-PASS across 7 real stores on 2 November (re-run after the gate fix the same day): pgvector (SQL + native `<=>`), Redis (RediSearch server-side KNN on redis-stack; the first run used brute-force hashes on plain Redis), Elasticsearch (dense_vector kNN), Qdrant, Weaviate (near_vector over gRPC), Chroma, and MongoDB Atlas Local. Byte-identical on byte-identical input: `create_collection`/`upsert`/`query`/`delete` all `vector_ok`, `query` ids `(r1, r3, r2)` (cosine order) and count 3 on all seven, upsert count 3 and delete count 3 on all seven. The six cloud/heavy stores are recorded infra-blocked, not fabricated (see below). |
| L30-2 | Provider unreachable; declared error, never empty results as a clean search | LIVE-PASS. Each real transport pointed at a 127.0.0.1 port with nothing listening, driven through `run_bounded_operation`, returned `provider_unreachable` with `records is None` within 30 s (sub-100 ms for the refused connections; ~5 s for the Mongo connect timeout). None was a `vector_ok` empty result. Code, request id and timestamp persisted. |
| L30-3 | Org A's credential with org B's request; refused | LIVE-PASS. Org A's verified credential presented for an Org B request returned `credential_scope_mismatch`; an empty request org returned `org_identity_invalid`. Both refused before any backing-store call, with zero backing-store operations proven by a call-counting wrapper around a real live transport, no Org B payload, and the credential secret absent from the refusal's emitted form. |

Infra-blocked on this host, named so they are not read as a pass: `pinecone`, `aws_bedrock_kb`, `gcp_vertex_vector`, `azure_ai_search` are cloud-only SaaS with no local image and no credentials here. `milvus` needs etcd and minio sidecars and was not brought up. To complete the full set, run the four cloud connectors against staging credentials and Milvus with its sidecars.

`aws_opensearch_serverless` (OpenSearch 2.17.1, same `knn_vector` API) is LIVE-PASS since 3 November on all four gates, next to the main stack and the other seven stores, with about 3.6 GiB available throughout. The earlier note that OpenSearch was "SIGTERM/OOM'd" by host RAM was wrong: the 2 November container shows `OOMKilled=false`, exit 143 after a graceful `stopping ... stopped`, and no out-of-memory line in that boot's kernel log. It was stopped, not killed. Evidence: `docs/plans/evidence/2026-11-03-gw30-opensearch/SUMMARY.md`.

### GW30 gate fix — 2 November

The full offline suite failed two gates on `dispatch/vector_live_transports.py`: module size (1309 lines) and `check_tenant_scale` (`smembers`, `scan_iter`, `keys`). Root cause and fix, with one contradictory review: `docs/plans/2026-11-02-gw30-gate-fix.md`.

- `keys` was a gate false positive (a Python dict). `smembers` and `scan_iter` were real: the Redis transport read every member and issued one `HGET` per record on every query, and scanned the keyspace to list collections, because the live gate ran plain Redis with no vector index. The Redis transport is now RediSearch: `FT.CREATE`, one `FT.SEARCH` KNN per query, `FT._LIST`, dimension checked before an atomic batch write. A Redis without the search module fails closed.
- Found while tracing: six transports (Qdrant, Weaviate, Elasticsearch, OpenSearch, Chroma, Mongo) deleted an existing collection inside `create_collection`. Requirement 2.9 says that case is a declared failure. New code `collection_exists`; every transport now uses the store's atomic create and maps its "already exists" error to it.
- The module is now the `dispatch/vector_transports/` package, one module per store. The live harness uses dedicated `gw30-redis-stack` (6979) and `gw30-pgvector` (6932) containers; before, it wrote into the main stack's Redis and Postgres.
- Full offline suite after the fix: 2794 passed, 100 skipped, 1 xfailed, 0 failed.
- Live: L30-1, L30-2 and L30-3 LIVE-PASS on 7 stores, and a new L30-1b (create, upsert, create again: `collection_exists`, records intact) LIVE-PASS on all 7. Evidence: `docs/plans/evidence/2026-11-02-gw30-gate-fix/`.
- Still open for GW30 (recorded in the fix plan, not fixed): Mongo query is a whole-collection read; `filters` is declared but ignored by every transport; partial upserts on pgvector and Elasticsearch; made-up delete counts on Qdrant, Chroma and Weaviate; Elasticsearch/OpenSearch delete swallows connection errors; Weaviate batch failures are not surfaced; name sanitisation can alias two collections; Mongo client is never closed; sync SDKs ignore the async timeout.

Why GW30 does not exit: the real transports are injected by the live-gate harness, not by the production boot (`edge/serve.py`). Wiring the connectors into a served route is GW31 (routes + pipeline) and GW36 (production boot). Per the runbook a card is not done for cutover until it is wired and its live tests pass; GW30 has the live tests passing on 8 real stores (OpenSearch added 3 November) but is not yet wired.

### Unit and property tests that exist for this card

On 2 November the `lgw30` suite was 78 passed (offline, stub-backed, no socket): posture-code distinctness, the `ResourceContract` vector derivations, the adapter value types and absent-vs-empty invariant, credential redaction/non-serialization, protocol conformance, and the six seeded `random.Random` >= 10,000-iteration property tests (one per design property: no silent fallback, no credential leak, unsupported-operation-not-empty, org-credential isolation, provider-unreachable-not-empty, stub determinism), plus the connector-table unit tests.

- `gateway_v2/tests/domain/test_lgw30_posture.py`
- `gateway_v2/tests/runtime/test_lgw30_resources.py`
- `gateway_v2/tests/dispatch/test_lgw30_values.py`
- `gateway_v2/tests/dispatch/test_lgw30_credential_value.py`
- `gateway_v2/tests/dispatch/test_lgw30_protocol.py`
- `gateway_v2/tests/dispatch/test_lgw30_stub_determinism.py`
- `gateway_v2/tests/dispatch/test_lgw30_registry.py`
- `gateway_v2/tests/dispatch/test_lgw30_capabilities.py`
- `gateway_v2/tests/dispatch/test_lgw30_credential_isolation.py`
- `gateway_v2/tests/dispatch/test_lgw30_leak_guard.py`
- `gateway_v2/tests/dispatch/test_lgw30_bounded_op.py`
- `gateway_v2/tests/dispatch/test_lgw30_connectors.py`

Live-gate harness and evidence: `docs/plans/evidence/2026-11-02-gw30-live-gate/` (`run_live_gates.py` for the first 2-store run, `run_live_gates_9store.py` for the 7-store run, `SUMMARY_9STORE.md`, and the `L30-1/-2/-3` JSON). The store stack is `docker-compose.gw30-stores.yml`. This is a GW card, not an R2 item, so no four-memory Ruflo/Cursor mirror was written.

## GW36 — Production boot wiring and local guard model

Depends on GW05c, GW06, GW08, GW10 and GW11. Required by GW20, GW21 and GW23.

Plan: `docs/plans/2026-11-02-gw36-production-boot.md`. It went through the `CLAUDE.md` order: exploration, a written plan, five contradictory reviews, and a devil's-advocate pass that re-checked each finding in code. The reviews added findings N1–N17 (plan §9.2) and raised the estimate from 7–11 to 14–18 agent-days. The main reason is that nothing writes signed state from control today: API keys go only to the v1 `auth:apikey:*` format, and control writes an unsigned plan document to `{rv2}:plan:{org}`, the key the signed PLAN feed owns. GW36 therefore needs a control-to-state projector (slice S2).

### Slice S0 — done on 2 November

| Item | What changed | Evidence |
|---|---|---|
| N1 `/metrics` crash | Found during the review and reproduced live. On the running dev gateway, one 400 refusal (`max_tokens=2000000`) made that worker's `/metrics` return 500 on every later scrape: `RejectionCounters` seeded status 503 only, a 400 added a series, and the Publisher's fixed `SERIES_COUNT` assertion failed (`rendered 3273 != 3272`). The `status` label is now a closed set (`errors.RENDERABLE_STATUSES`, every envelope status plus the generic 500), seeded with real zeros, and counted in `series_count`. An out-of-set status counts on `amf_publisher_vocab_error_total` and adds no series. | Live after rebuild: 20 of 20 scrapes 200 after a 400 and a 503 refusal; `amf_rejected{status="400",reason="rejected"} 1` |
| N17 / V-15 routing miss | Unknown path → 404 `endpoint_not_found`; known path with another method → 405 `method_not_allowed` with `allow`; the body never echoes the path. `/v1/rag/query` and `/v1/vector/query` stay 501. | Live: `GET /v1/files` 404; `GET /v1/chat/completions` 405 `allow: POST`; `POST /v1/rag/query` 501. Stock `openai` 3.29 `files.list()` raises `NotFoundError` after one HTTP request (no retry). |
| Pre-existing gate failures | `tests/lint/test_console_inventory.py`: ledgers regenerated with `--write`. The drift was committed frontend reads plus line shifts, and one `threat_category` read that commit `a3291e6f` removed. `check_tenant_scale` on `edge/dev_components.py`: the text-scan method was named `scan`, which the gate reads as Redis SCAN; renamed `findings_on`. | Both tests pass |

Gates on 2 November: full offline suite 2778 passed, 100 skipped, 1 xfailed, 2 failed; `mypy --strict` 202 files; ruff; import-linter 3/3; capacity-literal, module-mutable, frozen-dataclass and http-outside-edge gates exit 0. The two failures are `tests/gates/test_check_tenant_scale.py` and `tests/gates/test_empty_tree_passes.py`, and both name only `gateway_v2/dispatch/vector_live_transports.py` (1309 lines; `scan_iter`, `smembers`, `keys`), which the GW30 commits landed on this branch during this session. Both were fixed later the same day; see "GW30 gate fix" above.

Evidence: `docs/plans/evidence/2026-11-02-gw36/` (`S0_live_before.txt`, `S0_live_after.txt`, `S0_full_offline_suite.txt`).

### Slice S1 — done on 3 November (production upstream provider)

Plan with three contradictory reviews: `docs/plans/2026-11-03-gw36-s1-upstream-provider.md`. Evidence: `docs/plans/evidence/2026-11-03-gw36-s1/`.

- The provider now receives the request rebuilt from the verified, masked deltas with its structure: roles in order, text parts joined exactly as scanned, the v1 output-budget rule (unset → 4096, set → clamped, `max_completion_tokens` alone → clamped with no `max_tokens`), and validated sampling fields. One loop builds both the scanned text pairs and the upstream template, so the template holds no request text.
- Found and fixed: `/v1/completions` never scanned `prompt` (live, an SSN prompt got `input_scan` allow and the model received nothing). `prompt` and `stop` are now scanned input channels.
- Not forwarded, refused with a declared 400 naming the field: tools and function calling, `response_format`, logprobs, tool-role messages, non-text content parts, unknown fields. A message `name` is accepted and not forwarded.
- `OpenAIUpstreamProvider` replaces the dev adapter (deleted): one pooled client per worker, no cross-request abort. A non-2xx status, a non-SSE body, a stream without `[DONE]` or a transport failure ends with a typed `gateway_error`. Before, a provider error reached the client as a 200 whose content was the error code; now a provider 429 is a 429 `upstream_rate_limited`, and the provider's own 401/403 is a 502 `upstream_auth_error`.
- Found while verifying and fixed: a stream cut by an upstream failure reported `final_action: allow` in its trace.
- Live against the T02 recorder through a second dev gateway (:8302): 15/15 checks. Offline: 2845 passed in a container (the one container failure needs `git`; it passes on the host).
- Recorded, not fixed: a stalled upstream holds a non-streaming request up to the 300 s read timeout (S5 / GW28); an upstream failure shows `output_guardrail: block` until the console schema has an `error` action (GW35); a streamed refusal before the first byte is still an SSE error frame after a 200 (S5 / GW34); the request id hashes raw input text (S3); embeddings to a real upstream (S1b).

### Slice S2 — done on 3 November (control-to-state projector)

Plan with five contradictory reviews: `docs/plans/2026-11-03-gw36-s2-control-projector.md`. Evidence: `docs/plans/evidence/2026-11-03-gw36-s2/`.

- Found live before any change: the deployed re-hydrators failed every round with `relation "amf_state_counter" does not exist` (G13). No stamp had ever been written, and two unsigned documents sat at `{rv2}:plan:dev-org` and `{rv2}:plan:org-b`.
- Control now stores each org's plan document in `core_v2_plan_document` (migration `core/0041`), keyed by the org slug and rebuilt under a row lock on the org. Two racing saves previously could store the older one. The dev copy moved to `{rv2}:devplan:{alias}`, so control never writes `{rv2}:plan:*`. An org with no saved config now publishes the model defaults (v1 parity), instead of "firewall off".
- Found and fixed while reading control: a failed policy load published a plan without the org's policies, and a failed config import published "firewall off". Both now refuse to store.
- New `state-projector-a/-b` (`python -m state_control.projector_service`) reads control's tables in one read-only snapshot and writes signed KEY, PLAN and KILL SWITCH records.
  - One writer at a time, under a Postgres lease; every write is fenced and renews it.
  - Each round runs in a fixed order: switches on, revokes, plans, a safety heartbeat, switches off, then new keys (capped per round).
  - The re-hydrators withhold the stamp when the heartbeat is older than 16 s.
  - `init_schema` now runs in both services.
- `store_ahead` repair: after an epoch bump, gateways refused the kind for good, not only the re-hydrator, because the manifest sat above every record and below a reader's applied position. The repair now re-signs every record above the store's epoch and position, keeping revoke, op and engaged state. The R2-04 strict xfail passes and its marker is removed.
- Live on the stack, every result read back through the gateway's own `FeedReader`, appliers and `StampView`:

  | Check | Result |
  |---|---|
  | Console key create | signed KEY in 655–806 ms |
  | Config save | PLAN 1.3 → 1.4; an identical save makes no new version |
  | Deactivate | REVOKE in 866–979 ms |
  | Hard delete | REVOKE in 993 ms |
  | Org-wide kill switch | on and off in about 1.6 s |
  | Clean stop of the writer | takeover in 0.8 s, 0 withheld stamps |
  | Crash of the writer | takeover in 5.3 s (lease TTL) |
  | Both projectors down | first withheld stamp at 16.9 s |
  | Restart | stamp resumes in 1.8–2.3 s |

- Round cost at 10,000 keys and 200 orgs on real Postgres: 249 ms steady state, 286 ms for one revoke, onboarding 2,000 keys per round.
- Gates: full offline suite 2916 passed, 100 skipped, 0 failed. `state_control` with a live Postgres 16: 296 passed. `mypy --strict` 269 files, ruff, import-linter 3/3 and the AST gates are clean, apart from the documented R2-05 `valkey.py:100`.
- Recorded, not fixed:
  - The console refuses org-wide kill switches and creates only per-model ones. v2 has no org-plus-model scope, so those are counted and logged as not enforced until GW29.
  - BUDGET records and pool seeding move to S4, because control has no budget field.
  - Identity-cache fill race and `_served` not cleared on offboard (S3).
  - Rate 0 is not a GCRA (S4).
  - The diff is O(records) (measured above).
  - Pre-existing `makemigrations` drift on `FirewallConfig.routing_enabled`.

### Slice S3 — done on 3 November (gateway state runtime, hashed identity, 401/503)

Plan with five contradictory reviews: `docs/plans/2026-11-03-gw36-s3-state-runtime-identity.md`. Evidence: `docs/plans/evidence/2026-11-03-gw36-s3/`. Developed on the shared dev VM.

- `AMF_PROFILE=production` builds a signed-state runtime per worker (`edge/state_runtime.py`). It runs in the ASGI lifespan; one task owns every drain, and nudges only wake it.
  - **Cold start:** the kill switch adopts its engaged set and the KEY cursor jumps to the head, so start-up does not grow with the number of keys or records.
  - **Lifespan:** each worker waits, bounded by the 5 s freshness window, to catch up before uvicorn routes to it.
  - **Everything else:** provider, admission, quota, detectors and catalog stay on their fail-closed defaults, named in the boot log (S4/S5).
- **Identity is the sha256 of the bearer over signed KEY records.**
  - A missing, unknown or revoked key is 401 `invalid_api_key`, OpenAI's shape, on every surface. It is plain HTTP even for `stream=true`, so the SDK raises `AuthenticationError`, and it carries `WWW-Authenticate` and a random request id.
  - Unverified state is 503 `shared_state_unavailable`, never 401.
  - Only the console key shape is looked up, so junk tokens cost no store read.
- **Found and fixed:**
  - A fill in flight during a revoke delta was cached forever (N18); the fix covers revokes and adds alike.
  - A fill from a regressed store could resurrect a revoked key; the fetch now checks the KEY manifest against the applied cursor.
  - The KEY applier could run in a thread over an unlocked cache (N8).
  - Raw Redis errors escaped the synchroniser and stopped every kind (N7); only transport faults convert, and server errors stay loud.
  - There was no per-kind freshness (N9).
  - The plan snapshot kept an offboarded org's plan (N19).
  - The production `/readyz` would have silently ignored the stamp.
- `/livez` was added (static 200), and the compose healthcheck uses it.
- Live on the VM stack, 17/17 checks, with a production-profile gateway (2 workers) reading the stack's own signed state:

  | Check | Result |
  |---|---|
  | E1 console key resolves | about 1 s; chat refused only at admission (S4) |
  | E2 unknown / junk / missing key | SDK `AuthenticationError` 401, streaming or not |
  | E3 deactivation | 401 on every worker in 0.9 s |
  | E4 Valkey down | 503, `/readyz` 503 past the 5 s bound, `/livez` 200 |
  | E5 Valkey back | recovers in 0.7 s |
  | E6 re-hydrators stopped | 503 at 4.7 s |
  | E7 `/metrics` | counts the 401s |

- Gates: offline suite 2956 passed, 0 failed (VM, `-n 24`, 58 s); `mypy --strict` 272 files; ruff; import-linter 3/3; every AST gate clean. The negative controls for N18 and N19 fail without the fixes.
- Changed on purpose: a wrong dev-profile key is now 401, where it was 503.
- Recorded, not fixed:
  - MCP `initialize`/`ping` answer before identity (GW32).
  - A malformed unauthenticated body is 400 before 401.
  - Plan pins grow per request (S4).
  - Streams admitted before a revoke keep running (GW29).
  - Failed-credential audit records (GW35).
  - The duplicate re-hydrator metric family on the gateway (N14, S5).

### Not done

S4–S7 in plan §9.4. L36-1 and L36-2 are not run.

### Working defaults for the open owner decisions

The owner asked on 2 November to proceed on defaults. They are working defaults for GW36, not signatures: in-flight kill switch cuts at the next chunk; `RV_STATE_FRESH_MS` 5000 and `RV_STATE_PG_GRACE_MS` 16000; the shipped holdback trade-off table; egress excluded from the envelope and reported separately; tail trim ≤ 0.1%; p99 excludes holdback with the held-tokens histogram published; surface scoping confirmed; keep `/gateway/{org}/mcp/{srv}`; six Scan Detail stages with real auth and rate_limit; guard model PG2-22M ONNX on CPU with semantic action FLAG. Plan §9.3 adds the GW36-specific ones (platform default plan for an org with no saved config, 401 for unknown/inactive/expired keys, `tools` refused with a declared 400 until GW34).

## Cards not started

| Card | Runbook objective, short | State |
|---|---|---|
| GW17b | Authenticated MCP upstreams | Not started. Upstream auth still refuses with `mcp_upstream_auth_unsupported`. |
| GW17c | Per-key `mcp_allowed_tools` and `mcp_max_tool_calls` | Not started |
| GW27 | The 45 unsupported FirewallConfig fields, built or retired by name | Not started. `firewall_v2_map.py` still rejects those fields on save. |
| GW28 | Multi-model routing | Not started |
| GW29 | Kill switch, isolation, reroute, auto-isolation | Not started. `kill_switch_engaged` is still hard-coded false. |
| GW30 | Vector adapter and the connector list, `unsupported_operation` instead of an empty success | Adapter layer built and live-tested on 2 November; see the GW30 section above. Not wired into the production boot (that is GW31/GW36), so it does not exit for cutover. |
| GW31 | Four-stage RAG, the retired routes, async ingest, project on Principal, six RAG settings | Not started. `/v1/rag/query` is HTTP 501 `not_implemented:rag`. |
| GW32 | v1 MCP URL or a signed redirect, gateway as OAuth 2.1 server, real HTTP+SSE and WebSocket | Not started |
| GW33 | Output parity: IP leak, exfil channels, grounding, human review, REWRITE, canary | Not started. The GW26 publisher omits `rewrite` and `model_downgrade` and names them unsupported. It does not implement REWRITE. |
| GW34 | SDK compatibility; unknown paths 404 `endpoint_not_found` | Not started as a card. The unknown-path item landed with GW36 slice S0 on 2 November: an unknown path is 404 `endpoint_not_found`, a known path with another method is 405 with `allow`, and the registered placeholders stay 501. The client matrix (L34-1) is not run. |
| GW35 | Console pages and Scan Detail truth | Local half built on 4 November: Auth and Rate Limit show the real outcome, `error` is a console action, failed credentials are audited. Live stack run, browser walk and owner signatures not done, so it does not exit. See `docs/plans/evidence/2026-11-04-gw35/SUMMARY.md`. |
| GW36 | Production boot and a real local model | S0–S3 done (2–3 November); see the GW36 section above. `AMF_PROFILE=production` resolves real console keys and plans from signed state, but provider, admission, quota and detectors are still fail-closed (S4/S5), so it does not serve completions yet. |

## Owner decisions still written as open in v3.1

§00.7 items 7 through 12 are unchanged: egress against the $5,000 envelope, the latency-tail trim share, whether holdback counts in the 20 ms SLO, confirming the surface-scoping reversal, the MCP client URL, and whether six-stage Scan Detail is signed. Item 10 is the one this slice touches. Findings that reach the resolver are filtered by surface, and a refused regex is skipped when the route surface is not in the rule's list. The streaming chat detector receives the route surface. The document still asks for confirmation, and this file does not pretend that sentence was signed.

## GW35 (4 November 2026): local half built, not exited
Auth and Rate Limit now show the real outcome, an upstream failure shows `error` and no longer `output_guardrail: block`, and a rejected key leaves an audit row under `_unauthenticated`. The live stack run, the Playwright sweep, the browser walk and the owner signatures were not done, so the card does not exit. See `docs/plans/evidence/2026-11-04-gw35/SUMMARY.md`.
