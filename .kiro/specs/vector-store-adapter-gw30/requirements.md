# Requirements Document

## Introduction

GW30 ("One adapter interface for every vector store", AI Mesh Firewall Runbook v3.1 §00.6) builds the single adapter interface and the set of per-provider connectors that let the gateway talk to any supported vector store through one uniform, injected, fail-closed seam. It is a v1-parity, pre-cutover card: it is required by the GW21 shadow run and the GW23 cutover, it depends on GW11 (dispatch authorization, complete), and it is the blocker for GW31 (RAG parity) and GW18.

GW30 exists because v1's `_resolve_vector_client` silently falls back to "any available" provider when the requested store cannot be reached. That fallback sends one tenant's query to a store the tenant never configured, and an unreachable store can surface an empty result set as if it were a clean, complete search. GW30 replaces that behaviour with one adapter interface where a connector that cannot perform an operation returns a declared `unsupported_operation`, an unreachable provider returns a declared error (never an empty result), and there is no silent fallback to a different provider.

The GW30 objective, verbatim (runbook §00.6): *"One adapter interface for every vector store, with connectors for Pinecone, Chroma, Milvus/Zilliz, Qdrant, Weaviate, pgvector, Elasticsearch/OpenSearch, Redis, MongoDB Atlas, AWS (OpenSearch Serverless, Bedrock Knowledge Bases), GCP (Vertex AI Vector Search) and Azure (AI Search)."*

The GW30 exit criteria (runbook): a single adapter interface with the declared operations and capability flags; connectors for every named store; per-org credentials from the control plane that never appear in responses or logs; a declared `unsupported_operation` for any operation a connector cannot do; no silent fallback to another provider; org-to-org credential isolation. No card exits on unit tests alone — exit evidence is live Docker/staging behaviour, real wire bytes, and controlled-load measurement, captured by the three live acceptance tests L30-1, L30-2 and L30-3.

### Scope boundary (what GW30 is NOT)

GW30 provides the adapter interface, the connectors, credential handling, the `unsupported_operation` contract, and the no-silent-fallback guarantee. The four-stage RAG pipeline, the `/v1/rag/*` and `/v1/vector/*` routes themselves, async ingest, and project-scoped namespaces are GW31 (a separate downstream card) and are explicitly out of scope here. GW30 ships the adapter that GW31 will consume; this document introduces no RAG route and no RAG pipeline behaviour.

## Glossary

- **GW30**: The runbook pre-cutover card this feature implements (§00.6, "One adapter interface for every vector store").
- **GW11**: The completed dispatch-authorization card GW30 depends on; GW30 follows its injected-`ProviderClient`, fail-closed, byte-honest idiom.
- **GW31**: The downstream RAG-parity card that consumes the GW30 adapter; out of scope here.
- **Vector_Adapter**: The single `@runtime_checkable` interface protocol that every vector-store connector implements, mirroring the shipped `dispatch/provider.py::ProviderClient` protocol (injected from `edge`/`dispatch`, never imported downward).
- **Vector_Connector**: A concrete implementation of the Vector_Adapter for one named vector store (e.g. the Pinecone connector, the Qdrant connector).
- **Adapter_Operation**: One of the declared interface operations: `query`, `upsert`, `delete`, `list_collections`, `create_collection`, and `health`.
- **Capability_Flags**: The per-connector declaration of which optional features the connector supports: metadata `filters`, `namespaces`, and `hybrid_search`.
- **Unsupported_Operation**: The declared, uniform error a Vector_Connector returns when asked to perform an Adapter_Operation (or an optional capability) it cannot perform. It is a value-code, never an empty success result.
- **Provider_Unreachable**: The declared, uniform error a Vector_Connector returns when the backing vector store cannot be reached (connection refused, timeout, transport error, or an upstream 5xx).
- **Org_Credential**: The per-org vector-store credential (API key, endpoint, region, secret) resolved from the control plane under the per-org plan-key idiom `{rv2}:plan:{org}`, scoped to exactly one tenant.
- **Credential_Source**: The injected seam that resolves an Org_Credential for a given `org_id`, mirroring the plan-snapshot source idiom; injected from `edge`/`dispatch`, never imported downward.
- **No_Silent_Fallback**: The GW30 guarantee that a request for a specific vector store NEVER transparently reaches a different store; the requested-but-unreachable store returns a declared error instead.
- **Vector_Posture_Code**: A value-code in the shared `domain/posture.py` vocabulary that GW30 uses for its declared errors (`unsupported_operation`, `provider_unreachable`, `credential_unavailable`), codes only, with `edge` owning any HTTP mapping.
- **Stub_Vector_Connector**: The injectable, in-process, socket-free Vector_Connector driven by a seeded `random.Random` and a scripted result/error sequence, mirroring `dispatch/provider.py::StubProviderClient`, used by local tests and property tests.
- **Resource_Contract**: `ResourceContract` (`gateway_v2/runtime/resources.py`), the only module permitted to hold capacity-position literals (e.g. connector timeouts, result-size bounds).
- **L30-1 / L30-2 / L30-3**: The three GW30 live acceptance tests (runbook GW30 card "Live acceptance tests — required to exit"), captured here as verification obligations.

## Requirements

### Requirement 1: One uniform adapter interface for every vector store

**User Story:** As a backend engineer, I want one adapter interface that every vector store connector implements, so that calling code works against any store through a single injected seam without knowing which store is behind it.

#### Acceptance Criteria

1. THE Vector_Adapter SHALL define a `@runtime_checkable` protocol declaring exactly the six Adapter_Operations `query`, `upsert`, `delete`, `list_collections`, `create_collection`, and `health`, and THE Vector_Adapter SHALL treat a connector that is missing any one of these six operations as a non-conforming connector.
2. IF a connector is checked against the Vector_Adapter protocol and does not implement all six Adapter_Operations, THEN THE Vector_Adapter SHALL reject the connector with a declared Vector_Posture_Code and SHALL NOT expose it to calling code.
3. THE Vector_Adapter SHALL declare a `capabilities` accessor returning exactly the three Capability_Flags `filters`, `namespaces`, and `hybrid_search`, each as a boolean value, for the connector.
4. THE Vector_Adapter SHALL be defined in a layer that permits injection from `edge`/`dispatch` and SHALL NOT be imported by any layer below `dispatch` in the import-linter layer contract.
5. WHERE a Vector_Connector is provided to calling code, THE calling code SHALL receive the connector as an injected Vector_Adapter instance rather than importing a connector module downward.
6. WHEN an Adapter_Operation completes successfully, THE Vector_Adapter SHALL return the result as a frozen value type carrying a success payload.
7. IF an Adapter_Operation does not complete successfully, THEN THE Vector_Adapter SHALL return a frozen value type carrying a declared Vector_Posture_Code identifying the failure, and SHALL NOT raise the failure to calling code as an unhandled exception.

### Requirement 2: Declared adapter operations with uniform results

**User Story:** As a developer consuming the adapter, I want each operation to return a uniform, declared result shape, so that success and every failure mode are explicit and testable.

#### Acceptance Criteria

1. WHEN `query` is called with a collection identifier, a query vector, and an optional filter, THE Vector_Connector SHALL return a declared result value containing between 0 and the requested maximum number of matching records within 5000 milliseconds.
2. IF `query` is called and the backing store is unreachable, the collection identifier does not exist, the query vector dimension does not match the collection dimension, or the operation exceeds 5000 milliseconds, THEN THE Vector_Connector SHALL return a declared Vector_Posture_Code identifying the specific failure condition without returning partial records.
3. WHEN `upsert` is called with a collection identifier and a set of between 1 and 1000 records, THE Vector_Connector SHALL apply all records to the backing store and return a declared success value reporting the count of records applied within 5000 milliseconds.
4. IF `upsert` is called and the backing store is unreachable, the collection identifier does not exist, the record set is empty or exceeds 1000 records, or any record vector dimension does not match the collection dimension, THEN THE Vector_Connector SHALL return a declared Vector_Posture_Code identifying the specific failure condition and SHALL NOT apply any records from the set to the backing store.
5. WHEN `delete` is called with a collection identifier and a set of between 1 and 1000 record identifiers, THE Vector_Connector SHALL remove every matching identified record from the backing store and return a declared success value reporting the count of records removed within 5000 milliseconds.
6. IF `delete` is called and the backing store is unreachable, the collection identifier does not exist, or the identifier set is empty or exceeds 1000 identifiers, THEN THE Vector_Connector SHALL return a declared Vector_Posture_Code identifying the specific failure condition and SHALL NOT remove any records from the backing store.
7. WHEN `list_collections` is called, THE Vector_Connector SHALL return a declared result value containing between 0 and the tenant's maximum number of collections available to the tenant within 5000 milliseconds.
8. WHEN `create_collection` is called with a collection specification, THE Vector_Connector SHALL create the collection in the backing store and return a declared success value within 5000 milliseconds.
9. IF `create_collection` is called and the backing store is unreachable, a collection with the specified identifier already exists, or the collection specification is missing a required field, THEN THE Vector_Connector SHALL return a declared Vector_Posture_Code identifying the specific failure condition and SHALL NOT create a collection in the backing store.
10. WHEN `health` is called, THE Vector_Connector SHALL return a declared status of either reachable or degraded for the backing store within 5000 milliseconds, OR a declared Vector_Posture_Code identifying the specific failure condition if the status cannot be determined within 5000 milliseconds.

### Requirement 3: Capability flags declared per connector

**User Story:** As a developer, I want each connector to declare which optional features it supports, so that calling code can select behaviour by declared capability instead of per-store special cases.

#### Acceptance Criteria

1. THE Vector_Adapter SHALL expose exactly three Capability_Flags, named `filters`, `namespaces`, and `hybrid_search`, as a single frozen (immutable) value in which each flag resolves to one of exactly two states, supported or unsupported, with no flag left undefined.
2. WHERE a Vector_Connector supports metadata filters on `query`, THE Vector_Connector SHALL report its `filters` Capability_Flag as supported, and WHERE a Vector_Connector does not support metadata filters on `query`, THE Vector_Connector SHALL report its `filters` Capability_Flag as unsupported.
3. WHERE a Vector_Connector supports namespaces, THE Vector_Connector SHALL report its `namespaces` Capability_Flag as supported, and WHERE a Vector_Connector does not support namespaces, THE Vector_Connector SHALL report its `namespaces` Capability_Flag as unsupported.
4. WHERE a Vector_Connector supports hybrid (dense combined with sparse or keyword) search, THE Vector_Connector SHALL report its `hybrid_search` Capability_Flag as supported, and WHERE a Vector_Connector does not support hybrid search, THE Vector_Connector SHALL report its `hybrid_search` Capability_Flag as unsupported.
5. IF `query` is called with a filter while the connector reports its `filters` Capability_Flag as unsupported, THEN THE Vector_Connector SHALL return the `unsupported_operation` Vector_Posture_Code to the caller, SHALL NOT silently ignore the filter, and SHALL NOT return any query results for that call.
6. WHEN `query` is called with a filter while the connector reports its `filters` Capability_Flag as supported, THE Vector_Connector SHALL apply the filter to the query and return only results matching the filter.

### Requirement 4: A connector for every named vector store

**User Story:** As a platform operator, I want a connector for every supported vector store, so that any tenant's configured store is reachable through the same adapter interface.

#### Acceptance Criteria

1. THE Vector_Adapter SHALL provide a Vector_Connector implementation for each of the following named vector stores: Pinecone, Chroma, Milvus/Zilliz, Qdrant, Weaviate, pgvector, Elasticsearch/OpenSearch, Redis, MongoDB Atlas, AWS OpenSearch Serverless, AWS Bedrock Knowledge Bases, GCP Vertex AI Vector Search, and Azure AI Search, for a total of exactly 13 named Vector_Connector implementations.
2. WHEN the Vector_Adapter is queried for the set of named Vector_Connector implementations, THE Vector_Adapter SHALL return every one of the 13 named stores listed in criterion 1 with no name omitted and no name duplicated.
3. FOR EACH of the 13 named Vector_Connector implementations, THE Vector_Connector SHALL satisfy the Vector_Adapter protocol such that an `isinstance` check against the protocol returns true.
4. THE Vector_Adapter SHALL provide a Stub_Vector_Connector that satisfies the Vector_Adapter protocol such that an `isinstance` check against the protocol returns true.
5. WHILE the Stub_Vector_Connector is in use, THE Stub_Vector_Connector SHALL derive every non-scripted value solely from a `random.Random` instance initialized with a caller-supplied integer seed, such that two instances constructed with the same seed and the same scripted result/error sequence produce byte-identical outputs across all calls.
6. WHILE the Stub_Vector_Connector is in use, THE Stub_Vector_Connector SHALL return results and raise errors strictly in the order given by its scripted result/error sequence, and SHALL open no network socket, file-system handle, or other external I/O resource.
7. IF a caller requests a Vector_Connector for a store name that is not one of the 13 named stores in criterion 1, THEN THE Vector_Adapter SHALL reject the request with an error indicating that the requested store name is unsupported, and SHALL not return a connector instance.

### Requirement 5: Unimplemented operations surface as unsupported_operation, never an empty result

**User Story:** As a developer, I want a connector that cannot perform an operation to say so explicitly, so that an unsupported operation is never mistaken for a successful empty search.

#### Acceptance Criteria

1. THE Vector_Posture_Code vocabulary SHALL define `unsupported_operation` as a distinct code in the shared `domain/posture.py` vocabulary that is not equal to any success code or any other error code, with `edge` owning any HTTP mapping.
2. IF a Vector_Connector is asked to perform an Adapter_Operation it does not implement for its backing store, THEN THE Vector_Connector SHALL return a Vector_Adapter result carrying the `unsupported_operation` Vector_Posture_Code and SHALL identify the requested Adapter_Operation that is unsupported.
3. IF a Vector_Connector is asked to perform an optional capability it reports as unsupported, THEN THE Vector_Connector SHALL return a Vector_Adapter result carrying the `unsupported_operation` Vector_Posture_Code and SHALL identify the requested optional capability that is unsupported.
4. WHEN a Vector_Connector returns a Vector_Adapter result carrying the `unsupported_operation` Vector_Posture_Code, THE Vector_Connector SHALL set the result's record collection to absent (not an empty collection) and SHALL NOT set any success indicator on the same result.
5. WHEN a caller inspects a Vector_Adapter result, THE Vector_Adapter result SHALL expose the Vector_Posture_Code such that an `unsupported_operation` outcome is distinguishable, by that code alone, from a successful result whose record collection contains exactly zero records.
6. IF a Vector_Connector is asked to perform an Adapter_Operation it does implement, THEN THE Vector_Connector SHALL NOT return the `unsupported_operation` Vector_Posture_Code, returning a success code with its record collection (which MAY contain zero records) or another defined error code instead.

### Requirement 6: Provider-unreachable returns a declared error, never an empty result

**User Story:** As a tenant, I want a query against an unreachable store to fail with a declared error, so that a transport failure is never presented to me as a clean, complete search that found nothing.

#### Acceptance Criteria

1. THE Vector_Posture_Code vocabulary SHALL define `provider_unreachable` as a code in the shared `domain/posture.py` vocabulary.
2. IF the backing vector store cannot be reached for an Adapter_Operation due to a connection refusal, a connection or read timeout, a transport-layer error, or an upstream response with a 5xx-class status, THEN THE Vector_Connector SHALL return the `provider_unreachable` Vector_Posture_Code and SHALL NOT raise an unhandled exception to the caller.
3. WHEN a Vector_Connector returns `provider_unreachable` for a call, THE Vector_Connector SHALL NOT return an empty success result for that same call.
4. WHEN an Adapter_Operation has not completed within the connector timeout derived from the Resource_Contract, THE Vector_Connector SHALL abort the operation and return `provider_unreachable` rather than waiting beyond that timeout.
5. IF the Resource_Contract does not supply a connector timeout value, THEN THE Vector_Connector SHALL refuse to start the Adapter_Operation and SHALL return `provider_unreachable` rather than issuing a request with an unbounded wait.
6. THE Vector_Adapter result value SHALL carry an explicit outcome indicator that distinguishes a `provider_unreachable` outcome from a successful result whose record count is zero, such that a caller can determine which of the two occurred without inspecting the record count.

### Requirement 7: No silent fallback to another provider

**User Story:** As a tenant, I want a request for a specific vector store to reach only that store, so that my data and my query never travel to a store I did not configure.

#### Acceptance Criteria

1. WHEN calling code requests an Adapter_Operation against a specific requested store identifier, THE Vector_Adapter SHALL route the call only to the Vector_Connector registered for that exact store identifier.
2. IF the requested store identifier does not match any registered Vector_Connector, THEN THE Vector_Adapter SHALL return a declared Vector_Posture_Code indicating the store is not configured, SHALL NOT route the call to any connector, and SHALL NOT modify tenant data or query state.
3. IF the requested store's Vector_Connector returns `provider_unreachable` or `unsupported_operation`, THEN THE Vector_Adapter SHALL return that same declared Vector_Posture_Code to the calling code and SHALL NOT invoke, retry, or forward the operation against any other Vector_Connector.
4. THE Vector_Adapter SHALL select a Vector_Connector using only an exact match against the explicitly requested store identifier, and SHALL NOT select a connector by "any available" availability, by load, by round-robin, or by any criterion other than that exact store identifier match.
5. THE Vector_Adapter SHALL preserve the No_Silent_Fallback guarantee for every Adapter_Operation such that, for any single Adapter_Operation, the count of distinct Vector_Connectors invoked is at most one and equals the connector registered for the requested store identifier.

### Requirement 8: Per-org credentials from the control plane

**User Story:** As a platform operator, I want each connector to use the requesting org's own credentials resolved from the control plane, so that connectors are provisioned per tenant and never share a static credential.

#### Acceptance Criteria

1. WHEN the Credential_Source receives a resolution request for a given `org_id`, THE Credential_Source SHALL resolve exactly one Org_Credential scoped to that `org_id` from the control plane under the per-org plan-key idiom `{rv2}:plan:{org}` within 5000 milliseconds.
2. WHERE a Vector_Connector performs an Adapter_Operation, THE Vector_Connector SHALL use the Org_Credential resolved for the requesting org's `org_id` through the injected Credential_Source and SHALL NOT use an Org_Credential resolved for any other `org_id`.
3. IF the Credential_Source receives a resolution request with a missing or empty `org_id`, THEN THE Vector_Connector SHALL return the declared `credential_unavailable` Vector_Posture_Code and SHALL NOT contact the backing store, and the Adapter_Operation state SHALL remain unchanged.
4. IF the Credential_Source cannot resolve an Org_Credential because the control plane is unreachable within the 5000 millisecond resolution bound, THEN THE Vector_Connector SHALL return the declared `credential_unavailable` Vector_Posture_Code and SHALL NOT contact the backing store, and the Adapter_Operation state SHALL remain unchanged.
5. IF the Credential_Source resolves an Org_Credential whose verification status is not verified, THEN THE Vector_Connector SHALL return the declared `credential_unavailable` Vector_Posture_Code and SHALL NOT contact the backing store, and the Adapter_Operation state SHALL remain unchanged.
6. THE Credential_Source SHALL be injected from the `edge` or `dispatch` layer and SHALL NOT be imported by any layer below `dispatch`.
7. THE Vector_Connector SHALL NOT embed a static credential or a process-wide default credential.

### Requirement 9: Credentials never leak into responses, logs, or audit

**User Story:** As a security engineer, I want Org_Credential values to never appear in any response, log line, or audit record, so that a connector call cannot put a tenant's secret at rest or on the wire.

#### Acceptance Criteria

1. WHEN an Adapter_Operation produces a result value, THE Vector_Connector SHALL exclude every Org_Credential value, in both plaintext and any encoded or partially-masked form that reproduces one or more consecutive characters of the credential, from that result value.
2. WHEN the Vector_Connector emits a log line at any severity level, THE Vector_Connector SHALL exclude every Org_Credential value, in both plaintext and any encoded or partially-masked form that reproduces one or more consecutive characters of the credential, from that log line.
3. WHEN the Vector_Connector contributes a field to an audit record, THE Vector_Connector SHALL exclude every Org_Credential value, in both plaintext and any encoded or partially-masked form that reproduces one or more consecutive characters of the credential, from every field of that audit record.
4. IF an Adapter_Operation returns a declared Vector_Posture_Code carrying diagnostic detail, THEN THE Vector_Connector SHALL exclude every Org_Credential value from that diagnostic detail, and SHALL restrict the diagnostic detail to a reference identifier naming the affected Org_Credential rather than its value.
5. THE Vector_Connector SHALL exclude every Org_Credential value from every exported or retained record, such that no serialized field in any exported or retained record contains an Org_Credential value.
6. IF a result value, log line, audit record, or diagnostic detail is detected to contain one or more consecutive characters of an Org_Credential value immediately prior to emission or persistence, THEN THE Vector_Connector SHALL withhold that entire result value, log line, audit record, or diagnostic detail from emission or persistence and SHALL emit a Vector_Posture_Code indicating a credential-leak prevention outcome.

### Requirement 10: Org-to-org credential isolation

**User Story:** As a tenant, I want my credential usable only for my own requests, so that another tenant can never use my credential and I can never reach a store with another tenant's credential.

#### Acceptance Criteria

1. WHEN a Vector_Connector performs an Adapter_Operation for a request bearing `org_id` A, THE Vector_Connector SHALL use only the Org_Credential resolved for `org_id` A and SHALL NOT use, cache, or fall back to any Org_Credential resolved for a different `org_id`.
2. IF the `org_id` associated with the resolved Org_Credential does not exactly match the `org_id` borne by the request, THEN THE Vector_Adapter SHALL refuse the Adapter_Operation before issuing any call to the vector store, SHALL return a declared Vector_Posture_Code indicating a credential-scope mismatch, and SHALL leave the vector store unmodified.
3. IF a request bears no resolvable `org_id` or the `org_id` is empty, THEN THE Vector_Adapter SHALL refuse the Adapter_Operation before issuing any call to the vector store and SHALL return a declared Vector_Posture_Code indicating a missing or invalid org identity.
4. THE Credential_Source SHALL resolve an Org_Credential scoped to exactly one `org_id` and SHALL NOT return a credential associated with any other `org_id`.
5. IF the Credential_Source cannot resolve an Org_Credential scoped to the request's `org_id`, THEN THE Vector_Adapter SHALL refuse the Adapter_Operation before issuing any call to the vector store and SHALL return a declared Vector_Posture_Code indicating credential resolution failure.
6. THE Vector_Connector SHALL carry no Org_Credential state that is shared, reused, or observable across requests bearing different `org_id` values.

### Requirement 11: Connector bounds and capacity literals confined to the Resource_Contract

**User Story:** As a backend engineer, I want every connector bound to derive from the Resource_Contract, so that capacity literals stay in one place and connectors fail closed under load.

#### Acceptance Criteria

1. THE Vector_Connector SHALL derive its per-operation timeout from the Resource_Contract and SHALL NOT embed a capacity-position timeout literal in the connector module.
2. THE Vector_Connector SHALL derive any result-size bound, expressed as a maximum result count between 1 and 10,000 items and a maximum result payload size between 1 and 10,485,760 bytes, from the Resource_Contract.
3. IF an Adapter_Operation would exceed a Resource_Contract-derived bound, THEN THE Vector_Connector SHALL abort the operation before returning any partial result, SHALL discard any accumulated result data, and SHALL return a declared Vector_Posture_Code identifying the exceeded bound rather than returning an unbounded result.
4. THE Vector_Adapter modules SHALL hold no module-level mutable table and no capacity-position literal outside the Resource_Contract.
5. WHEN an Adapter_Operation does not complete within the Resource_Contract-derived per-operation timeout, THE Vector_Connector SHALL cancel the operation, SHALL release any resources held by the operation, and SHALL return a declared Vector_Posture_Code indicating a timeout.
6. IF the Resource_Contract does not supply a required bound at connector initialization, THEN THE Vector_Connector SHALL refuse to start and SHALL return a declared Vector_Posture_Code indicating an unset capacity bound rather than applying a default capacity literal.

### Requirement 12: L30-1 — uniform behaviour across every connector (verification obligation)

**User Story:** As a release engineer, I want the same operation suite to behave identically across every connector in live staging, so that the adapter interface is proven uniform on real wire bytes before cutover.

#### Acceptance Criteria

1. THE L30-1 verification SHALL run the same `query`, `upsert`, and `delete` operation suite against every named Vector_Connector in live Docker/staging, executing each operation against all connectors using byte-identical equivalent input payloads.
2. WHEN the L30-1 suite runs against every connector with byte-identical equivalent inputs, THE L30-1 verification SHALL confirm that the returned result set for each operation is identical across all connectors, where identical means matching returned record identifiers, matching returned field values, and matching result count.
3. IF any connector returns a result set that differs from another connector for the same equivalent input, THEN THE L30-1 verification SHALL fail and record the differing connector, the operation, and the observed difference as exit evidence.
4. WHEN a declared error condition is triggered for an operation, THE L30-1 verification SHALL confirm that every connector surfaces the same declared error category for that equivalent input, and SHALL fail if any connector surfaces a different error category or no error.
5. THE L30-1 verification SHALL record the real wire bytes exchanged with each connector and the observed per-operation outcome for every connector as persisted exit evidence, and SHALL NOT substitute unit-test results for live wire evidence.

### Requirement 13: L30-2 — unreachable provider yields a declared error (verification obligation)

**User Story:** As a release engineer, I want a live unreachable-provider test, so that an unreachable store is proven to return a declared error and never an empty result presented as a clean search.

#### Acceptance Criteria

1. WHEN a search request is issued against a Vector_Connector whose backing store has been made unreachable in live staging, THE L30-2 verification SHALL observe the `provider_unreachable` Vector_Posture_Code returned within 30 seconds of the request.
2. IF the response to a request against the unreachable store does not carry the declared `provider_unreachable` error, or is labelled as a successful or complete search, THEN THE L30-2 verification SHALL fail and record the observed response as exit evidence.
3. THE L30-2 verification SHALL record the observed `provider_unreachable` Vector_Posture_Code, the request identifier, and the observation timestamp as persisted exit evidence.
4. IF the `provider_unreachable` Vector_Posture_Code is not observed within 30 seconds of the request against the unreachable store, THEN THE L30-2 verification SHALL record a failed outcome as exit evidence.

### Requirement 14: L30-3 — org-to-org credential isolation refused live (verification obligation)

**User Story:** As a release engineer, I want a live cross-org credential test, so that using Org A's credential for Org B's request is proven to be refused before cutover.

#### Acceptance Criteria

1. WHEN Org A's credential is presented to serve Org B's request in the live staging environment, THE L30-3 verification SHALL observe the request refused and SHALL confirm the refusal occurs before any backing-store connection is attempted.
2. THE L30-3 verification SHALL confirm that the refused request reaches no backing store with the mismatched credential, evidenced by zero backing-store operations recorded for that request against Org B's store.
3. WHEN a cross-org credential request is refused, THE L30-3 verification SHALL observe a refusal response that identifies the cause as a credential-to-org mismatch and SHALL observe that no response payload derived from Org B's store is returned.
4. THE L30-3 verification SHALL record each refusal outcome as exit evidence, including the request identifier, the presented credential's org, the target request's org, and the observed refusal response, retained for the duration of the cutover review.
5. IF a cross-org credential request is NOT refused, or any backing-store operation against Org B's store is observed for that request, THEN THE L30-3 verification SHALL record a failed outcome as exit evidence and SHALL mark the cutover gate as blocked.

### Requirement 15: Correctness properties captured for property-based testing

**User Story:** As a test engineer, I want the GW30 invariants expressed as seeded property tests in the house idiom, so that no-silent-fallback, no-credential-leak, unsupported-operation-not-empty, and org-isolation are checked across many generated inputs.

#### Acceptance Criteria

1. THE GW30 property tests SHALL use a seeded `random.Random` source, SHALL NOT use the `hypothesis` library, and SHALL execute a minimum of 10,000 generated iterations per property.
2. FOR ALL generated requests against a specific store across at least 10,000 iterations, THE No_Silent_Fallback property SHALL confirm the call reaches only the requested store's connector and reaches zero other connectors.
3. FOR ALL generated Adapter_Operations and declared errors across at least 10,000 iterations, THE no-credential-leak property SHALL confirm that zero Org_Credential values appear in any result value, log line, or audit record.
4. IF a generated operation terminates with a declared error, THEN THE no-credential-leak property SHALL confirm that zero Org_Credential values appear in the error value or its associated error indication.
5. FOR ALL generated unsupported operations across at least 10,000 iterations, THE unsupported-operation-not-empty property SHALL confirm the result equals the `unsupported_operation` Vector_Posture_Code and that this result is not equal to a successful result containing zero records.
6. FOR ALL generated `org_id` pairs across at least 10,000 iterations, where the two `org_id` values in a pair are distinct, THE org-credential-isolation property SHALL confirm an operation for one `org_id` uses only that `org_id`'s Org_Credential and uses zero credentials belonging to any other `org_id`.
7. IF any property check fails for a generated input, THEN THE GW30 property tests SHALL report a failure that includes the seed value and the failing generated input, and SHALL preserve the seed so the failing case is reproducible.
