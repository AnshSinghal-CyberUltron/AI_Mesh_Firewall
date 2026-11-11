# Requirements Document

## Introduction

This feature implements card **GW31** ("RAG pipeline parity", AI Mesh Firewall Runbook v3.1 Part 00) of the `gateway_v2/` backend rewrite. It restores the four-stage RAG pipeline and its routes on v2, with every stage on by default. Today `gateway_v2/gateway_v2/edge/rag.py` is a stub (`__all__ = ()`) and `edge/routes.py` `build_routes` registers `POST /v1/rag/query` and `POST /v1/vector/query` as `_placeholder(...)` handlers that render HTTP 501 `not_implemented`. GW31 replaces those placeholders with real served routes that drive the already-shipped GW30 vector adapter.

GW31 depends on GW30 (vector adapter, shipped), GW26 (surface and `pipeline_stage` scoping, shipped in the plan, compiler and resolver), GW09 and GW10 (deterministic and semantic detectors). It is required by GW21 and GW23, and it unblocks the GW18 and GW26 RAG claims. GW26 has `pipeline_stage` in `plan/compiler.py` (`_PIPELINE_STAGES = {"", "query", "retriever", "ranker", "generator"}`) and the `Surface.RAG` / `Surface.VECTOR` values in `domain/plan.py`, but no live route passes a stage name, so L26-1 and L26-2 are unmet on RAG. GW31 makes that scoping fire on live routes.

The card objective (runbook): *"Restore the four-stage RAG pipeline and its routes on v2, with every stage on by default."* Implementation work: query, retriever, ranker (distance anomaly, trust re-rank, near-duplicate cap), and generator (context verification, redaction, canary, context binding); grounding on a local model through `GuardBackend`; the route set below; asynchronous ingest for every connector; project-scoped namespaces; and the six RAG console settings compiled into the plan.

### Scope boundaries

- **GW30 is shipped. GW31 WIRES it; it does not rebuild it.** The `Vector_Adapter` protocol, `AdapterResult`, the connectors under `dispatch/vector_transports/`, `VectorRegistry`, credentials handling, and the `unsupported_operation` / `provider_unreachable` contract are consumed as they are.
- **GW26 is shipped.** The `Rule.surfaces` filter and `pipeline_stage` matching in `resolve/conflict.py` are consumed, not changed, except for passing the surface and stage from the new routes.
- **No external platform AI.** Grounding may use a local model through `GuardBackend` only. v1 `llm_judge` and the Titan embedder stay removed. The v1 ranker and generator use no model; GW31 keeps them model-free.
- **Not in scope:** the production boot wiring of connectors (GW36), MCP (GW32), hallucination and grounding scoring beyond the generator's context checks (GW33), the cutover itself (GW21, GW23), and any frontend work.
- **Deferred live gates.** The operator has no cloud resources. Live tests L31-1..L31-4 are captured as verification obligations; the local half runs against the Stub vector connector and a local Chroma, and cloud-store runs are deferred.

### Assumptions (conservative, fail-closed)

1. The runbook does not define the exact HTTP shape of every v1 route. Where it does not, GW31 keeps the v1 path and method and returns the existing house `Error_Envelope` for failures.
2. The runbook lists "the admin collection routes the console calls" without paths. GW31 serves `GET/POST/DELETE /v1/admin/rag/collections` (the path named in the runbook's 00.5 analysis) and nothing else under `/v1/admin/`.
3. The runbook gives no numeric defaults for `rag_max_documents` or `rag_relevance_threshold`. Absent or invalid values fail closed to the most restrictive documented bound (see Requirement 12 and Requirement 13), never to "unlimited" or "0 threshold".
4. "Every stage on by default" is read as: when the plan carries no explicit setting for a stage, the stage runs. A stage can be switched off only by an explicit, valid plan setting. No stage is ever skipped on error.
5. A request that arrives with no resolvable project uses the project `default`, giving namespace `org{id}-default__{collection}`. This matches v1 behaviour and the GW26 notes. It never reaches another project's namespace.

## Glossary

- **GW31**: The runbook card this feature implements.
- **RAG_Surface**: The set of served routes under `/v1/rag/*` and the admin collection routes. Plan surface `Surface.RAG`.
- **Vector_Surface**: The served routes under `/v1/vector/*`. Plan surface `Surface.VECTOR`.
- **Vector_Adapter**: The GW30 `@runtime_checkable` protocol in `dispatch/vector_adapter.py` with `query`, `upsert`, `delete`, `list_collections`, `create_collection`, `health`.
- **AdapterResult**: The GW30 frozen result value. Outcome is determined by `code` alone; `records is None` on any non-OK code and `records == ()` on a successful empty query.
- **VectorRegistry**: The GW30 per-org resolver of a connector (`dispatch/vector_registry.py`).
- **Pipeline**: The four ordered stages Query, Retriever, Ranker, Generator, run for `/v1/rag/query`.
- **Query_Stage**: Stage `query`. Canonicalises and scans the user query before any embedding or retrieval.
- **Retriever_Stage**: Stage `retriever`. Calls the Vector_Adapter `query`, scans each retrieved chunk for indirect injection and sensitive data, and applies `rag_max_documents`.
- **Ranker_Stage**: Stage `ranker`. Distance anomaly detection, trust re-rank, and the near-duplicate cap.
- **Generator_Stage**: Stage `generator`. Context verification, redaction, canary, and context binding before and after the model call.
- **Near_Duplicate_Cap**: The ranker limit on near-identical chunks. v1 value 0.5, read as the maximum fraction of the final context window that near-duplicates of one source may occupy.
- **Trust_Level**: A per-chunk trust label (source and ingest provenance) used by trust re-rank. A low-trust chunk ranks below an otherwise equal high-trust chunk.
- **Canary**: A per-request random token inserted into the generator's context and checked on output. Its appearance in output means the context leaked.
- **Context_Binding**: Binding retrieved context to the request's org, project and request id so a chunk from another tenant cannot enter the prompt.
- **Principal**: The frozen `domain/identity.py` dataclass (`key_id, org_id, rate_per_s, burst, feed_seq`). GW31 adds `project`.
- **Project**: The key's project identifier carried on Principal.
- **Namespace**: The string `org{id}-{project}__{collection}`.
- **RAG_Settings**: The six console settings `rag_enabled`, `vector_db_isolation`, `rag_max_documents`, `rag_relevance_threshold`, `rag_redaction_enabled`, `rag_tier2_enabled`.
- **Async_Ingest**: Ingest that returns HTTP 202 after durable acceptance and completes the store write in the background for every connector.
- **Ingest_Job**: The bounded background unit created by `POST /v1/rag/ingest`.
- **Retired_Routes**: The v1 routes the ledger marks as retired and that GW31 re-registers: collections, documents delete, vector config, vector upsert/delete, admin collections.
- **Stage_Scope**: The `pipeline_stage` value passed to the resolver so GW26 rules with that stage match only that stage.
- **GuardBackend**: The local-model seam through which grounding runs. No remote platform AI.
- **Edge_Layer**: `gateway_v2.edge`, the only layer allowed to hold HTTP objects.
- **FAIL_OPEN**: Forwarding unscanned or undecided content, or admitting a request, when a guard, count, auth or decision cannot be computed. Forbidden; pinned at 0.
- **ResourceContract**: `runtime/resources.py`, the only module permitted to hold capacity-position literals.
- **Offline_Gate_Suite**: Full offline pytest (container-run style), `mypy --strict`, `ruff`, `import-linter`, and the AST gates (capacity literals, no module-level mutable state, frozen dataclasses, HTTP outside edge/resolve, tenant scale).

## Requirements

### Requirement 1: Replace the 501 stubs with served RAG and vector routes

**User Story:** As an API client, I want the RAG and vector routes to be served, so that retrieval-augmented requests work instead of returning HTTP 501.

#### Acceptance Criteria

1. THE Edge_Layer SHALL serve `POST /v1/rag/query`, `POST /v1/rag/ingest`, `GET /v1/rag/collections`, `POST /v1/rag/collections`, `DELETE /v1/rag/collections`, `DELETE /v1/rag/documents`, `POST /v1/vector/query`, `POST /v1/vector/upsert`, `POST /v1/vector/delete`, and `/v1/vector/config` through real handlers.
2. THE Edge_Layer SHALL serve `GET`, `POST` and `DELETE` on `/v1/admin/rag/collections`.
3. WHEN `build_routes` runs, THE Edge_Layer SHALL NOT register `_placeholder("rag")` or `_placeholder("vector")` for any path listed in criteria 1 and 2.
4. WHEN a request uses a known path with a method not listed for it, THE Edge_Layer SHALL return 405 `method_not_allowed` with an `allow` header, and WHEN the path is unknown, SHALL return 404 `endpoint_not_found`, preserving the N17 routing behaviour.
5. THE route handlers SHALL live in `edge/rag.py` (and sibling `edge/` modules), and THE route table SHALL be built inside `build_routes` with no module-level mutable table.
6. WHEN no vector adapter or pipeline dependency is injected, THE Edge_Layer SHALL return a declared error through the `Error_Envelope` and SHALL NOT fall back to a 501 success-shaped body or an empty result.
7. THE Edge_Layer SHALL authenticate, admit and budget every request on these routes through the same Principal, `AdmissionController` and `QuotaComponent` path the chat surface uses, before any adapter call.

### Requirement 2: Query stage

**User Story:** As a security engineer, I want the user query scanned before it reaches any store, so that a hostile query is stopped at the first stage.

#### Acceptance Criteria

1. WHEN `POST /v1/rag/query` is received, THE Query_Stage SHALL run first and SHALL complete before the Retriever_Stage begins.
2. THE Query_Stage SHALL canonicalise the query text and scan it with Surface `rag` and Stage_Scope `query`.
3. IF the Query_Stage resolves a BLOCK, THEN THE Pipeline SHALL refuse the request and SHALL make zero Vector_Adapter calls and zero model calls.
4. IF the Query_Stage resolves REDACT, THEN THE Pipeline SHALL pass only the redacted query to later stages and SHALL NOT pass the original text to the adapter or the model.
5. IF the query is empty, not a string, or longer than the ResourceContract query bound, THEN THE Query_Stage SHALL reject it with a declared value-code before any scan or adapter call.
6. IF the scan cannot be computed, THEN THE Query_Stage SHALL fail closed with a declared value-code and SHALL NOT continue.
7. THE Query_Stage SHALL be on unless the plan explicitly disables it, and SHALL stay on when the setting is absent or invalid.

### Requirement 3: Retriever stage

**User Story:** As a security engineer, I want retrieved chunks scanned and bounded, so that a poisoned document cannot reach the model or the client.

#### Acceptance Criteria

1. THE Retriever_Stage SHALL call `Vector_Adapter.query` through the org's connector with the project-scoped Namespace and SHALL NOT call any other connector.
2. WHEN the adapter returns a non-OK code, THE Retriever_Stage SHALL propagate the declared code (`unsupported_operation`, `provider_unreachable`, `credential_unavailable`, capacity codes) and SHALL NOT substitute an empty result or another provider.
3. WHEN the adapter returns `VECTOR_OK` with `records == ()`, THE Retriever_Stage SHALL treat it as a successful empty retrieval and SHALL pass an empty context onward.
4. THE Retriever_Stage SHALL scan every retrieved chunk for injection and sensitive data with Surface `rag` and Stage_Scope `retriever`, and SHALL exclude a chunk whose scan resolves BLOCK.
5. IF a chunk scan cannot be computed, THEN THE Retriever_Stage SHALL exclude that chunk and SHALL record the failure; it SHALL NOT pass the chunk unscanned.
6. THE Retriever_Stage SHALL apply `rag_max_documents` as an upper bound on chunks leaving the stage (Requirement 12).
7. A poisoned document retrieved through any RAG or vector path SHALL be detected (L31-1, LGW18-1), and the published result SHALL state measured recall on an indirect-injection fixture family, not a single fixture.

### Requirement 4: Ranker stage

**User Story:** As a security engineer, I want retrieved chunks re-ranked for anomalies, trust and duplication, so that flooding or poisoned low-trust content cannot dominate the context.

#### Acceptance Criteria

1. THE Ranker_Stage SHALL run after the Retriever_Stage and before the Generator_Stage and SHALL scan with Stage_Scope `ranker`.
2. THE Ranker_Stage SHALL apply distance anomaly detection and SHALL drop a chunk whose distance is anomalous relative to the retrieved set, using a deterministic statistic with no model call.
3. THE Ranker_Stage SHALL apply trust re-rank, ordering by Trust_Level before distance for chunks within the relevance threshold, so that a low-trust chunk never outranks an otherwise equal higher-trust chunk.
4. THE Ranker_Stage SHALL apply the Near_Duplicate_Cap, limiting near-duplicates of one chunk to the 0.5 fraction of the final context (L31-2), and SHALL drop the excess lowest-trust duplicates first.
5. THE Ranker_Stage SHALL drop every chunk below `rag_relevance_threshold` (Requirement 13).
6. THE Ranker_Stage SHALL be deterministic: identical input and settings SHALL produce an identical ordered output.
7. IF a ranker step cannot be computed, THEN THE Ranker_Stage SHALL fail closed by dropping the affected chunks and recording the failure, and SHALL NOT pass them on unranked.
8. THE Ranker_Stage SHALL be on unless the plan explicitly disables it.

### Requirement 5: Generator stage

**User Story:** As a security engineer, I want the final context verified, redacted and bound to the request, so that nothing unsafe or foreign reaches the model or the client.

#### Acceptance Criteria

1. THE Generator_Stage SHALL run last and SHALL scan with Stage_Scope `generator`.
2. THE Generator_Stage SHALL perform context verification: every chunk in the prompt SHALL carry the request's org and project, and a chunk that does not SHALL be removed (Context_Binding).
3. WHERE `rag_redaction_enabled` is true, THE Generator_Stage SHALL redact matched sensitive spans in the context and in the model output, and SHALL verify by bytes that the raw span is absent after redaction.
4. THE Generator_Stage SHALL insert a per-request Canary into the context and SHALL withhold the output and return a declared value-code if the Canary appears in it.
5. WHERE `rag_tier2_enabled` is true, THE Generator_Stage SHALL run grounding on a local model through `GuardBackend`, and SHALL NOT call any remote platform AI.
6. IF `GuardBackend` is unavailable or errors while `rag_tier2_enabled` is true, THEN THE Generator_Stage SHALL fail closed by withholding the answer with a declared value-code and SHALL NOT treat the answer as grounded.
7. IF redaction cannot be verified by bytes, THEN THE Generator_Stage SHALL withhold the output.
8. THE Generator_Stage SHALL be on unless the plan explicitly disables it.

### Requirement 6: Stage-scoped policy fires on live routes (GW26 unblock)

**User Story:** As a policy administrator, I want a rule scoped to `query`, `retriever`, `ranker` or `generator` to fire on the matching stage of a live RAG request, so that GW26's stage scoping is proven end to end.

#### Acceptance Criteria

1. WHEN a stage runs, THE Pipeline SHALL pass Surface `rag` (or `vector` on vector routes) and that stage's name as the Stage_Scope to the resolver.
2. WHEN a rule has `pipeline_stage` set to one of the four names, THE resolver SHALL apply it only in that stage on a live request and SHALL NOT apply it in the other three.
3. WHEN a rule has an empty `pipeline_stage`, THE Pipeline SHALL apply it in every stage.
4. A rule scoped to retrieval and a rule scoped to chat only SHALL behave per LGW18-6 and L26-1: the chat-only rule SHALL NOT fire on RAG, and the retrieval-scoped rule SHALL NOT fire on chat.
5. THE live test SHALL publish a block keyword rule with `pipeline_stage = query`, observe the block on `/v1/rag/query`, delete the rule, and observe the allow, and repeat for each of the other three stages.
6. THE Pipeline SHALL emit the matched stage in the audit record and the pipeline trace for a stage-scoped decision.

### Requirement 7: Retired routes re-registered

**User Story:** As a console user, I want the collections, documents and vector config routes back, so that nothing the console calls returns 404 or 501.

#### Acceptance Criteria

1. WHEN `GET /v1/rag/collections` is received, THE RAG_Surface SHALL list only the calling org and project's collections via `Vector_Adapter.list_collections` and SHALL strip the Namespace prefix from the names returned.
2. WHEN `POST /v1/rag/collections` is received, THE RAG_Surface SHALL create the collection via `Vector_Adapter.create_collection` in the project Namespace.
3. WHEN `DELETE /v1/rag/collections` or `DELETE /v1/rag/documents` is received, THE RAG_Surface SHALL delete only within the project Namespace via `Vector_Adapter.delete`.
4. WHEN `/v1/vector/config` is received, THE Vector_Surface SHALL return the org's connector name and its Capability_Flags and SHALL NOT return any credential, endpoint secret or `detail_ref` secret material.
5. WHEN `POST /v1/vector/query`, `/v1/vector/upsert`, or `/v1/vector/delete` is received, THE Vector_Surface SHALL run the same Retriever scan and Ranker steps on query results and the same input scan on upserted content as the RAG routes.
6. IF an operation is unsupported by the connector, THEN THE route SHALL return `unsupported_operation` and SHALL NOT return an empty success.
7. Every route in this requirement SHALL require authentication and SHALL scope to the Principal's org and project.

### Requirement 8: Asynchronous ingest for every connector

**User Story:** As a data owner, I want ingest to store my documents in whichever store I configured, so that a Chroma ingest is stored and retrievable (v1 stored Pinecone only and dropped the rest after answering 202).

#### Acceptance Criteria

1. WHEN `POST /v1/rag/ingest` is received and accepted, THE RAG_Surface SHALL return HTTP 202 with an ingest job id only after the job is durably queued.
2. THE Ingest_Job SHALL write through `Vector_Adapter.upsert` for whichever connector the org has configured, and SHALL NOT be limited to Pinecone.
3. THE RAG_Surface SHALL scan ingested content with Surface `rag` before it is queued, and SHALL refuse a BLOCK before returning 202.
4. THE Ingest_Job SHALL record its outcome (accepted, stored, failed with a declared code) and `GET` of the job id SHALL return it; a failed job SHALL NOT be reported as stored.
5. WHEN the adapter returns a non-OK code, THE Ingest_Job SHALL record the declared code and SHALL NOT report success.
6. THE number of concurrent Ingest_Jobs, the queue depth, and the per-document size bound SHALL derive from ResourceContract; IF the queue is full, THEN THE RAG_Surface SHALL return a declared overload value-code with `Retry-After` and SHALL NOT drop the job silently.
7. A document ingested to Chroma SHALL be retrievable through `/v1/rag/query` (L31-3).
8. An Ingest_Job SHALL write only to the Principal's project Namespace.

### Requirement 9: Project on Principal and project-scoped namespaces

**User Story:** As a tenant, I want two projects in one org to be isolated, so that each reads only its own collections.

#### Acceptance Criteria

1. THE Principal SHALL gain a `project: str` field, SHALL stay a frozen slotted dataclass, and SHALL be populated from the signed key record.
2. IF the signed key record has no project, THEN THE identity resolver SHALL set `project` to `default`.
3. IF the project string is empty after trimming, longer than the ResourceContract bound, or contains a character outside `[A-Za-z0-9_-]`, THEN THE identity resolver SHALL reject the key with a declared value-code and SHALL NOT silently rewrite it.
4. THE Namespace SHALL be built as `org{id}-{project}__{collection}`, and the collection name SHALL be validated against the same character set so it cannot contain the `__` separator or a prefix that crosses projects.
5. THE namespace builder SHALL be a single pure function used by every RAG and vector route, and no route SHALL build a namespace string by other means.
6. A request for a collection outside the Principal's Namespace SHALL be refused with a declared value-code and SHALL make zero adapter calls.
7. Two projects in one org SHALL each read only their own collections (L31-4), and org A SHALL NOT reach org B's namespace.
8. Existing consumers of `Principal` SHALL continue to work: the field SHALL have a default or every construction site SHALL be updated, and the Offline_Gate_Suite SHALL stay green.

### Requirement 10: Setting `rag_enabled`

**User Story:** As an org admin, I want to switch RAG off, so that no RAG request is served for my org.

#### Acceptance Criteria

1. THE plan compiler SHALL compile `rag_enabled` into the plan as a boolean.
2. WHEN `rag_enabled` is false, THE RAG_Surface and Vector_Surface SHALL refuse every request with a declared `rag_disabled` value-code and SHALL make zero adapter calls.
3. WHEN `rag_enabled` is absent, THE Pipeline SHALL treat it as true, matching "on by default".
4. IF `rag_enabled` is present but not a boolean, THEN THE plan compiler SHALL raise `CompileError`, and THE previous plan SHALL keep serving.

### Requirement 11: Setting `vector_db_isolation`

**User Story:** As an org admin, I want vector DB isolation controlled by the plan, so that each project's data stays in its own namespace.

#### Acceptance Criteria

1. THE plan compiler SHALL compile `vector_db_isolation` into the plan from a closed set of values.
2. THE closed set SHALL be `project` (default; Namespace per project) and `org` (one Namespace per org, project segment fixed to `default`).
3. WHEN `vector_db_isolation` is absent, THE Pipeline SHALL use `project`.
4. IF the value is outside the closed set, THEN THE plan compiler SHALL raise `CompileError`, and THE Pipeline SHALL NOT widen isolation.
5. WHEN `vector_db_isolation` is `org`, THE Pipeline SHALL still prefix every Namespace with `org{id}` so org A cannot reach org B.

### Requirement 12: Setting `rag_max_documents`

**User Story:** As an org admin, I want to cap the documents passed to the model, so that context size and cost stay bounded.

#### Acceptance Criteria

1. THE plan compiler SHALL compile `rag_max_documents` as an integer.
2. WHEN `rag_max_documents` is set, THE Retriever_Stage and Ranker_Stage SHALL emit at most that many chunks.
3. WHEN `rag_max_documents` is absent, THE Pipeline SHALL use the ResourceContract maximum, not an unbounded value.
4. IF the value is not an integer, less than 1, or greater than the ResourceContract maximum, THEN THE plan compiler SHALL raise `CompileError`.
5. THE `top_k` sent to the adapter SHALL NOT exceed the lesser of `rag_max_documents` and the ResourceContract adapter result bound.

### Requirement 13: Setting `rag_relevance_threshold`

**User Story:** As an org admin, I want low-relevance chunks dropped, so that weak matches never reach the model.

#### Acceptance Criteria

1. THE plan compiler SHALL compile `rag_relevance_threshold` as a number in the closed interval [0.0, 1.0].
2. THE Ranker_Stage SHALL drop every chunk whose normalised relevance is below the threshold.
3. WHEN absent, THE Pipeline SHALL use the ResourceContract default threshold, declared as a named constant there.
4. IF the value is not a number, NaN, infinite, or outside [0.0, 1.0], THEN THE plan compiler SHALL raise `CompileError`.
5. WHEN every chunk is below the threshold, THE Pipeline SHALL proceed with an empty context and SHALL NOT lower the threshold to find a match.

### Requirement 14: Setting `rag_redaction_enabled`

**User Story:** As an org admin, I want redaction in the RAG path controlled by the plan, so that sensitive spans do not reach the model or the client.

#### Acceptance Criteria

1. THE plan compiler SHALL compile `rag_redaction_enabled` as a boolean.
2. WHEN true, THE Generator_Stage SHALL apply Requirement 5 criterion 3.
3. WHEN absent, THE Pipeline SHALL treat it as true.
4. WHEN false, THE Pipeline SHALL still BLOCK any finding whose disposition is BLOCK, and SHALL NOT turn a BLOCK into an allow.
5. IF the value is not a boolean, THEN THE plan compiler SHALL raise `CompileError`.

### Requirement 15: Setting `rag_tier2_enabled`

**User Story:** As an org admin, I want the semantic (Tier-2) checks controlled by the plan, so that local-model grounding can be switched on or off per org.

#### Acceptance Criteria

1. THE plan compiler SHALL compile `rag_tier2_enabled` as a boolean.
2. WHEN true, THE Pipeline SHALL run the GW10 semantic detectors on query and chunks and SHALL run grounding through `GuardBackend` (Requirement 5 criteria 5 and 6).
3. WHEN absent, THE Pipeline SHALL treat it as true.
4. WHEN false, THE Pipeline SHALL skip only the semantic and grounding checks; the deterministic GW09 checks SHALL still run on every stage.
5. THE Pipeline SHALL NOT call `llm_judge`, the Titan embedder, or any remote platform AI under either value.
6. IF the value is not a boolean, THEN THE plan compiler SHALL raise `CompileError`.

### Requirement 16: Compile the six settings into the plan atomically

**User Story:** As an org admin, I want a bad RAG setting to leave the previous plan serving, so that a typo cannot disable protection.

#### Acceptance Criteria

1. THE plan compiler and plan document SHALL carry the six RAG_Settings as a frozen value in the plan, and THE document round trip SHALL be lossless.
2. WHEN any setting fails validation, THE compile SHALL fail as a whole and THE previous plan SHALL keep serving; no partial plan SHALL be published.
3. THE resolved settings for a request SHALL come from the plan snapshot in effect for that request and SHALL NOT be read from a mutable global.
4. A plan with no RAG settings block SHALL resolve to the defaults in Requirements 10 to 15, with every stage on.
5. THE resolver of the six settings SHALL be pure and SHALL live below the Edge_Layer.

### Requirement 17: Fail-closed behaviour and error mapping

**User Story:** As a security engineer, I want every failure on these routes to refuse rather than leak, so that FAIL_OPEN stays at zero.

#### Acceptance Criteria

1. IF any stage raises, times out, or cannot decide, THEN THE Pipeline SHALL refuse the request with a declared value-code, SHALL log at the level of the house event log, and SHALL NOT forward unscanned content to the model or the client.
2. THE domain posture vocabulary SHALL gain the RAG value-codes this card needs (for example `rag_disabled`, `rag_query_blocked`, `rag_output_withheld`, `rag_ingest_overload`, `rag_namespace_refused`) as codes only, and THE Edge_Layer `Error_Envelope` SHALL own the HTTP mapping.
3. THE Pipeline SHALL NOT include chunk text, a credential, or a Canary value in any error body, log line, audit record or metric label.
4. THE Pipeline SHALL emit per-request exports through the shipped `PerRequestExports` producer with `FAIL_OPEN` pinned at 0, and SHALL NOT add a tenant-derived metric label.
5. THE Pipeline SHALL emit an audit record for every request on these routes, including refusals and sheds, with a reason code.

### Requirement 18: Live acceptance tests L31-1 to L31-4 (verification obligations)

**User Story:** As a release owner, I want the four live tests defined as pass/fail gates, so that the card exits on observed behaviour and not on unit tests alone.

#### Acceptance Criteria

1. L31-1: a poisoned document SHALL be retrieved on every RAG and vector path and the injection SHALL be detected on all of them.
2. L31-2: a near-duplicate flood of a low-trust document SHALL be capped by the Ranker_Stage.
3. L31-3: a Chroma ingest SHALL store the document and the document SHALL be retrievable.
4. L31-4: two projects in one org SHALL each read only their own collections.
5. WHERE the live stack or a cloud store is unavailable, THE evidence SHALL state which half ran locally (Stub vector connector or local Chroma) and which half is deferred, and SHALL NOT claim a pass that was not observed.

## Non-Functional Requirements

### Requirement 19: Architecture constraints

**User Story:** As a maintainer, I want GW31 to obey the house layering and purity rules, so that the gates stay green and the design stays enforceable.

#### Acceptance Criteria

1. THE implementation SHALL follow the import-linter layer order `edge > admit > plan > detect > resolve > dispatch > egress > audit > runtime > contracts > domain`, built bottom-up, with no upward import.
2. THE `resolve` layer SHALL import only `domain`.
3. THE `egress` layer SHALL NOT import `detect`; any scanner SHALL be injected from `edge` or `dispatch`.
4. HTTP objects (request, response, status, headers, ASGI scope) SHALL exist only in the Edge_Layer; other layers SHALL return frozen values and codes.
5. THE implementation SHALL NOT import `starlette` or `fastapi` outside `edge`.
6. NO capacity-position literal (timeouts, queue depths, size bounds, top-k ceilings, thresholds, job counts) SHALL appear outside `runtime/resources.py`; every bound SHALL derive from ResourceContract or an injected config.
7. NO module SHALL hold module-level mutable state; tables SHALL be built inside functions or exposed as `MappingProxyType` / tuples / frozensets.
8. EVERY value type SHALL be `@dataclass(frozen=True, slots=True)`.
9. Clock, random source, store clients, vector adapter, `GuardBackend`, and ingest queue SHALL be injected so tests run with no socket.
10. THE implementation SHALL be fail-closed everywhere with FAIL_OPEN pinned at 0.

### Requirement 20: Offline gate suite and property tests

**User Story:** As a test engineer, I want every invariant expressed as a tagged property test and all gates green, so that regressions are caught offline.

#### Acceptance Criteria

1. THE full offline pytest suite SHALL pass with 0 failures in a container-run style invocation, with no previously passing test removed or weakened.
2. `mypy --strict`, `ruff`, and `import-linter` SHALL exit 0 on the changed tree.
3. THE five AST gates (capacity literals, no module-level mutable state, frozen dataclasses, HTTP outside edge/resolve, tenant scale) SHALL exit 0 on the `gateway_v2` tree.
4. THE correctness properties SHALL be seeded `random.Random` tests with no `hypothesis`, SHALL run at least 100 iterations each, and SHALL carry a feature and property tag comment in the house form `Feature: gw31-rag-pipeline, Property N: <name>`.
5. IF a property fails, THEN THE test SHALL report the seed and the failing generated input.
6. THE properties SHALL cover at least: (P1) a BLOCK at the query stage makes zero adapter and model calls; (P2) no chunk reaches the generator unscanned; (P3) namespaces never cross org or project; (P4) the Near_Duplicate_Cap bound holds and ranking is deterministic; (P5) a lower-trust chunk never outranks an equal higher-trust chunk; (P6) `rag_max_documents` and `rag_relevance_threshold` bounds always hold; (P7) invalid settings never produce a plan and never widen behaviour; (P8) a failed ingest is never reported stored; (P9) no secret, chunk text or Canary appears in an error body or log; (P10) a stage-scoped rule fires only in its stage.
7. EACH of the four stages and each of the six settings SHALL have at least one unit test that exercises it in isolation using the Stub vector connector.
8. THE implementation SHALL add no new third-party dependency.

## Out of Scope

- Rebuilding or changing the GW30 adapter, connectors, registry or credentials code.
- Changing GW26 resolver semantics.
- Production boot wiring of real connector transports (GW36).
- MCP, OAuth, chat, responses, embeddings routes.
- `llm_judge`, the Titan embedder, any remote platform AI.
- Frontend and console changes.
- Cloud-store live runs and fleet capacity certification (deferred live gates).
- Hallucination scoring beyond the generator's context checks (GW33).

## Clarifications from design review (appended, GW31 design pass 2)

- **C-1 (amends Req 1.1, 7.3).** `DELETE /v1/rag/collections` and `DELETE /v1/admin/rag/collections` delete the listed `ids` in the named collection via `Vector_Adapter.delete`. A request with no `ids` (whole-collection drop) returns `unsupported_operation` with zero adapter calls, because the GW30 protocol has no drop operation. Follow-up: a GW30 `drop_collection` operation.
- **C-2 (clarifies Req 2.7, 4.8, 5.8).** The six RAG settings contain no per-stage toggle, so all four stages always run; `rag_enabled=false` is the only whole-pipeline switch.
- **C-3 (clarifies Req 9.3).** An invalid `project` on a key record is refused with the declared value-code `project_invalid` (503) on every route.
- **C-4 (clarifies Req 4).** The ranker-stage scan is per chunk; a ranker BLOCK drops that chunk.
- **C-5 (amends Req 9.4, 9.5 and the Glossary "Namespace"; design pass 4).** `org{id}-{project}__{collection}` is the LOGICAL triple `(org_id, project, collection)` only. The string handed to `Vector_Adapter` as `collection` is the ENCODED wire name built by the single builder `wire_collection(namespace)`: lowercase-only segments `[a-z0-9_-]` (uppercase is refused, never folded), each segment escape-encoded, joined with an unambiguous separator, total length bounded by `wire_max_length` (58 = strictest connector name limit 63 minus the 5-character `gw30_` transport prefix). The encoding is injective over triples and case-stable, so no pair of distinct triples can share a store collection on any connector. Req 9.5 is amended to name `wire_collection` as the only builder. Req 9.4's collection-name charset rule applies to all three segments.
- **C-6 (clarifies Req 3, 5, 8; design pass 4).** The shipped GW30 transports neither persist nor return `VectorRecord.metadata`. GW31 therefore owns a bounded `ChunkStore` (text, vector, source, trust, keyed by wire collection and record id). Ingest writes the vector to the adapter and the chunk entry to the ChunkStore; retrieval resolves returned record ids through the ChunkStore, and a miss drops the chunk (fail closed). Org and project binding comes from the store key. GW30 is not amended; a durable ChunkStore is GW36.
- **C-7 (clarifies Req 8.1 and the Async_Ingest glossary; final design pass).** In GW31 "durably queued" means recorded in the injected `IngestJobStore`. The shipped in-memory store is NOT durable across process exit; a store-backed implementation is GW36. A store whose `put` fails or raises yields no 202 and a declared refusal (`rag_unavailable`). Evidence must state which store ran.
- **C-8 (clarifies Req 1.6, 3.2; final design pass).** When the adapter returns at least one record id and none resolves in the ChunkStore, the request is refused with the declared code `rag_chunk_store_unavailable` (503), never an empty 200. A partial miss drops and counts the missing chunks. The in-memory ChunkStore is single-process.
- **C-9 (amends Req 12.4 and Req 9.3; design round 4).** (a) Req 12.4: a `rag_max_documents` above the `ResourceContract` ceiling is accepted at compile and clamped down by `effective_settings` with `settings_clamped` in the trace; `CompileError` remains for non-integer, bool and `< 1`. (b) Req 9.3: the project charset is `[a-z0-9_-]`; uppercase is refused with `project_invalid` and never folded (consistent with C-5).
