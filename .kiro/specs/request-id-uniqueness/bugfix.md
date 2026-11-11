# Bugfix Requirements Document

## Introduction

The v2 gateway mints each request's `request_id` as a pure hash of the tenant and the request content. Two requests with byte-identical content therefore get the same id. Integration finding F2 (4 Nov 2026) observed this live: five identical chat calls all returned `zs-dev-org-fb2340bd19bc5b758a6b2018`. Stream and non-stream repeats collided, MCP `tools/list` reused one id across separate calls, and embeddings and RAG showed the same collision.

The impact is that audit rows and Scan Detail rows for repeated prompts collide. This is the same conflation class PIPELINE-0023 fixed in the control-plane `proxy_chat` path, but from a new cause: deterministic content hashing with no per-request uniqueness. Admission slots, admit refcounts and budget refunds are keyed on `request_id`, so a collision can release, refund or attribute the wrong call's resources.

Owner decision: ids must be unique per request, even for byte-identical content. They must stay stable across every phase of one request (identity, incident gate, admission, budget, input scan, output scan, audit, Scan Detail, response header). A clock-minted id that differs between phases remains rejected. Per-phase random suffixes are not acceptable.

## Bug Analysis

### Current Behavior (Defect)

1.1 WHEN two chat requests from the same tenant have byte-identical content (model and every input channel), whether sequential or concurrent THEN the system assigns both the same `request_id`.

1.2 WHEN a streaming chat request and a non-streaming chat request have identical content THEN the system assigns them the same `request_id`.

1.3 WHEN the same MCP method (e.g. `tools/list`) is called more than once with an identical message body THEN the system reuses one `request_id` for every call.

1.4 WHEN identical embeddings requests or identical RAG and vector requests (same tenant, path and body) are repeated THEN the system assigns the same `request_id` to each.

1.5 WHEN the MCP incident gate runs with no request id yet noted THEN the system derives its fallback id from only the org and a constant, so every such call for an org shares one id.

1.6 WHEN two requests with colliding ids are in flight or complete in the same period THEN the system writes audit and Scan Detail rows for both under one `request_id`, so the rows cannot be told apart and the repeated prompts are conflated.

1.7 WHEN two requests with colliding ids hold admission slots, in-flight records or budget spends at the same time THEN the system keys those resources on the shared `request_id`, so a completion or refund for one request can release, count or credit state belonging to the other.

### Expected Behavior (Correct)

2.1 WHEN two chat requests have byte-identical content, whether sequential or concurrent, THEN the system SHALL assign each a distinct `request_id`.

2.2 WHEN a streaming and a non-streaming chat request have identical content THEN the system SHALL assign each a distinct `request_id`.

2.3 WHEN the same MCP method is called repeatedly with an identical body THEN the system SHALL assign each call a distinct `request_id`.

2.4 WHEN identical embeddings, RAG or vector requests are repeated THEN the system SHALL assign each a distinct `request_id`.

2.5 WHEN the MCP incident gate needs an id and none has been noted for the request THEN the system SHALL use a per-request-unique id and SHALL NOT use a per-org constant.

2.6 WHEN one request passes through any of its phases (identity, incident gate, admission, budget, input scan, output scan, audit, Scan Detail row, `x-request-id` header and error envelope) THEN the system SHALL carry the SAME `request_id` end to end. That id SHALL come from a per-request uniqueness source minted once at request entry and carried through all phases. It SHALL NOT be re-minted or suffixed per phase.

2.7 WHEN several requests are in flight at once, including requests with identical content, THEN the system SHALL key admission slots, in-flight records, admit refcounts and budget refunds on distinct ids, so completing or refunding one request affects only that request's resources.

2.8 WHEN the per-request uniqueness source cannot produce an id THEN the system SHALL fail closed by refusing the request and SHALL NOT proceed with a shared, empty or content-only id.

### Unchanged Behavior (Regression Prevention)

3.1 WHEN a request is processed THEN the system SHALL CONTINUE TO produce ids with the shipped `zs-` prefix and the tenant-scoped `zs-{org_id}-…` form, so one tenant's id never contains another tenant's org id and two tenants never share an id.

3.2 WHEN a client supplies an `X-Request-ID` header THEN the system SHALL CONTINUE TO ignore it as the canonical id, so concurrent pinned client headers cannot collide (PIPELINE-0023).

3.3 WHEN a request is refused before an id exists (identity refusals, and plan-unavailable refusals that already mint a random id) THEN the system SHALL CONTINUE TO render those refusals with a random content-free id, with no change to wire shape.

3.4 WHEN moderations run THEN the system SHALL CONTINUE TO use its existing per-request random id.

3.5 WHEN one request is shed, refused or completed THEN the system SHALL CONTINUE TO echo the same id in the admission verdict, the budget verdict, the incident verdict, the HTTP error envelope body and the `x-request-id` header (including the SSE start header). The `Admitted` verdict SHALL CONTINUE TO carry the id it was given.

3.6 WHEN a request spends budget and then makes zero provider calls THEN the system SHALL CONTINUE TO refund exactly the spent cost to the same owner's lease and to no other org.

3.7 WHEN a request is admitted and then completes or exits THEN the system SHALL CONTINUE TO release exactly one admission slot, with `complete` idempotent and a no-op for an id with no outstanding admit.

3.8 WHEN audit records are written and exported THEN the system SHALL CONTINUE TO require a non-empty `request_id`. The durable sink's `(org_id, stream_id)` idempotence and the `request_id` lookup index SHALL be unchanged.

3.9 WHEN the control-plane `proxy_chat` path binds its own `zs-*` id (`_bind_gateway_request_id`, `client_correlation_id`, `_stamp_pipeline_trace_request_id`) THEN the system SHALL CONTINUE TO behave exactly as fixed by PIPELINE-0023, and this fix SHALL NOT change that path.

3.10 WHEN error bodies and responses are rendered THEN the system SHALL CONTINUE TO produce the frozen GW35 golden wire bytes, apart from the id value itself, which golden comparisons normalize.

3.11 WHEN any lifecycle phase fails or raises THEN the system SHALL CONTINUE TO fail closed, never fail open, with no module-level mutable state introduced for id minting.
