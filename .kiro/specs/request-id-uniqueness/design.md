# Request ID Uniqueness Bugfix Design

## Overview

The v2 gateway derives each request's `request_id` as a pure hash of tenant plus request content. Chat (`ChatSurface._request_id`), embeddings (`MiscSurface._request_id`), RAG/vector (`rag_common._request_id`) and MCP (`mcp._request_id`) each carry their own copy of that hash. Byte-identical requests therefore share one id (finding F2). Chat compounds it by recomputing the hash in three places: lifecycle phase 3 (`holds.note_request`), `_admit` and `_budget`. The MCP incident gate falls back to `_request_id(org, "incident", {})`, a per-org constant.

The fix replaces content hashing with a per-request random id, `zs-{org}-{random hex}`, minted once by a single helper and carried on `RequestHolds`. Every phase reads the id from `holds`. No phase recomputes it. If the id cannot be minted, the request is refused (fail closed). Admission refcount behaviour stays as is.

Owner decisions, settled here:

| # | Decision |
|---|----------|
| D1 | Shape is `zs-{org_id}-{hex}` with `hex` from `secrets.token_hex`. No content-hash component: it adds no uniqueness and would leak content equality across requests. |
| D2 | Minted ONCE per request, at the first point the tenant is known (immediately after the plan/pin phases, exactly where `_request_id` is called today). Carried via `RequestHolds` (`note_request` / `request_id`). Chat `_admit` and `_budget` stop recomputing and take the id from `holds`. |
| D3 | Entropy or mint failure refuses the request (req 2.8). It never falls back to a shared, empty or content-only id. |
| D4 | Applied uniformly: chat (stream and non-stream), embeddings, RAG, vector, MCP (including the incident-gate fallback). |
| D5 | The admission refcount (req 3.7) is kept even though unique ids make it redundant. |
| D6 | `tests/edge/test_lgw15_isolation.py` currently calls `ChatSurface._request_id` statically. It is updated to the new helper and keeps the tenant-scoped `zs-{org}-` assertions. |
| D7 | PIPELINE-0023's control-plane `proxy_chat` path (`_bind_gateway_request_id`, `client_correlation_id`, `_stamp_pipeline_trace_request_id`) is untouched. |
| D8 | **Decision (open item a, resolved):** a mint failure uses a NEW code `request_id_unavailable` (`posture.REQUEST_ID_UNAVAILABLE`), mapped to HTTP 503 with no id and no `x-request-id`. `shared_state_unavailable` is NOT reused, because it would misreport the cause. |
| D9 | **Decision (open item b, resolved):** NO set-once guard is added to `RequestHolds.note_request`. Some refusal paths legitimately note a pre-id and then a request id. Single-id-per-request is enforced by `begin_request` being idempotent and by property test B. |

## Glossary

- **Bug_Condition (C)**: Two or more requests whose id is derived only from tenant + content (or a per-org constant) receive the same `request_id`. The trigger is a repeat of identical content; the defect is the id collision.
- **Property (P)**: Each request gets a distinct id, and that one id is carried unchanged through every phase of that request.
- **Preservation**: Behaviour for inputs that do not trigger C: `zs-` and `zs-{org}-` form, header handling, pre-id refusals, moderations, verdict echo, refund, release, audit validation, golden wire bytes.
- **RequestHolds** (`gateway_v2/edge/request_holds.py`): per-request `__slots__` ledger. Holds `_request_id` (via `note_request` / `request_id`), admission flag, spend, dispatch flag. Constructed once per request, never shared.
- **mint_request_id()** (`edge/identity_resolver.py`): existing content-free `zs-` + 24 hex id used only for pre-identity refusals. Unchanged.
- **request id helper** (new, `edge/request_ids.py`): `mint_tenant_request_id(org_id)`, which returns `zs-{org_id}-{hex}` and raises `RequestIdUnavailable` on failure.
- **Phase**: identity, plan, incident gate, admission, budget, input scan, dispatch, output scan, audit, console projection, response header and error envelope.

## Bug Details

### Bug Condition

The bug manifests whenever the same tenant sends two requests whose hashed fields are equal. The hashed fields are `model` + every input channel for chat, `model` + items for embeddings, `path` + raw body for RAG/vector, and `method` + raw message for MCP. It also manifests for every MCP call that reaches the incident gate before an id is noted, because that fallback hashes only `(org, "incident", {})`.

**Formal Specification:**
```
FUNCTION isBugCondition(X)
  INPUT: X of type RequestPair (two requests r1 != r2, sequential or concurrent,
         stream or non-stream, any surface)
  OUTPUT: boolean

  RETURN X.r1.org == X.r2.org
         AND X.r1.surface == X.r2.surface
         AND hashedFields(X.r1) == hashedFields(X.r2)      // byte-identical content
         AND currentId(X.r1) == currentId(X.r2)            // F produces a collision
END FUNCTION

FUNCTION isBugConditionIncidentGate(X)
  INPUT: X of type McpCall reaching incident gate with holds.request_id == ""
  RETURN idUsedByGate(X) == hash(X.org, "incident", {})    // per-org constant (1.5)
END FUNCTION
```

Streaming and non-streaming calls collide because `stream` is not among the hashed fields (1.2).

### Examples

- Five identical chat calls from `dev-org` all return `zs-dev-org-fb2340bd19bc5b758a6b2018`. Expected: five distinct ids, each `zs-dev-org-<24 hex>`.
- A stream and a non-stream call with the same prompt share an id. Expected: two distinct ids.
- MCP `tools/list` twice with the same body shares an id. Expected: two ids.
- MCP incident gate reached with no id noted uses `zs-{org}-<hash of "incident">` for every call. Expected: the per-request id, which the gate verdict, `holds`, the audit row and the header all carry.
- Control's `_upsert` (`core/v2_audit_ingest.py`) looks up the existing `EnforcementEvent` by `(org, source=gateway_v2, request_id)` and merges via `merge_row`. Two colliding requests are folded into ONE row. This is the user-visible symptom of 1.6.
- Edge case: a chat request refused in the plan phase already uses the random `zs-<24 hex>` id and does not collide. It is unchanged (3.3).

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- Ids keep the `zs-` prefix and the `zs-{org_id}-…` tenant-scoped form (3.1).
- A client `X-Request-ID` is never the canonical id (3.2).
- Identity refusals and chat plan-unavailable refusals keep their random content-free id from `identity_resolver.mint_request_id()` / `refusal_request_id` (3.3).
- Moderations keep their own `uuid4` id (3.4).
- The same id appears in the admission verdict, budget verdict, incident verdict, error body, `x-request-id` header and SSE start header; `Admitted` carries the id it is given (3.5).
- Budget refund returns exactly the spent cost to the same owner when no provider call was made (3.6).
- Release performs exactly one admission `complete`; `complete` is idempotent and a no-op for an id with no outstanding admit (3.7).
- Audit records still require a non-empty `request_id`. The durable sink's `(org_id, stream_id)` idempotence and `request_id` index are unchanged (3.8).
- The control-plane `proxy_chat` path is unchanged (3.9).
- GW35 golden wire bytes are unchanged apart from the id value (3.10).
- Every phase still fails closed; no module-level mutable state is introduced (3.11).

**Scope:**
Everything that does not derive an id from content or a per-org constant is unaffected. This includes provider dispatch, scanning, plan resolution, output guard and routing decisions.

## Hypothesized Root Cause

Confirmed by reading the code (not a hypothesis for the requirements phase, but still to be re-confirmed by the exploration test before the fix):

1. **Content-addressed id by design.** `ChatSurface._request_id` explicitly documents "pure function of the request identity" and rejects a clock-minted id because it would differ between phases. The only way to get cross-phase stability was to recompute the same pure function in each phase. This is the origin of the collision.
2. **No carried id.** Chat phase 3 notes the id on `holds`, but `_admit` (`openai_chat.py` ~L561) and `_budget` (~L603) call `self._request_id(identity, req)` again instead of reading `holds.request_id`. Fixing uniqueness without fixing this would give each phase a different random id.
3. **Four copies of the same hash.** `openai_chat.py`, `openai_misc.py` (embeddings), `rag_common.py` (RAG/vector) and `mcp.py` each hand-roll `sha256(...)[:24]`. `mcp._forward` also re-notes a hashed id after `_tenant()` may already have used the fallback, so the gate and the rest of the request can see different ids.
4. **Per-org constant fallback.** `mcp._incident_org_gate` uses `holds.request_id or _request_id(org_id, "incident", {})` and only notes the id when the gate refuses.

## Correctness Properties

Property 1: Bug Condition - Distinct requests get distinct ids and one id per request

_For any_ two distinct requests, including byte-identical ones, sequential or concurrent, stream or non-stream, on any of chat, embeddings, RAG, vector or MCP (and the MCP incident-gate path), the fixed gateway SHALL assign them different `request_id` values of the form `zs-{org_id}-{hex}`; and _for any_ single request, every phase (incident gate, admission verdict, budget verdict, audit row, console projection, error envelope, `x-request-id` header, SSE start) SHALL carry the same id that was minted once for that request. If the id cannot be minted, the request SHALL be refused with no provider call and no shared, empty or content-only id.

**Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8**

Property 2: Preservation - Everything outside id derivation is unchanged

_For any_ input where the bug condition does NOT hold (pre-identity refusals, moderations, client `X-Request-ID` headers, shed/refuse/complete paths, refunds, releases, audit and wire rendering), the fixed gateway SHALL produce the same result as the original, preserving the `zs-` / `zs-{org}-` forms, the id echo across verdicts, exactly-once release and refund, the non-empty-id audit rule, the golden wire bytes (id value normalised), and the untouched control-plane `proxy_chat` binding.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10, 3.11**

## Fix Implementation

### Changes Required

Layering: all changes are inside `gateway_v2.edge` (the top layer). Nothing new is imported from a lower layer except stdlib `secrets`. `admit`, `audit`, `egress` and `runtime` do not change, so the import-linter contract needs no edit.

**File**: `gateway_v2/gateway_v2/edge/request_ids.py` (new)

1. **Single mint helper.** `mint_tenant_request_id(org_id: str, *, token_hex: Callable[[int], str] = secrets.token_hex) -> str` returns `f"zs-{org_id}-{token_hex(n)}"`. The `n` is a width bytes constant (`_ID_BYTES = 12`, giving 24 hex, matching the shipped width). It is a formatting width, not a capacity bound, and lives here with the existing prefix convention, outside any capacity position. No module-level mutable state; the entropy source is an injected default parameter, never a module global.
2. **Fail closed.** Wrap the call. Any exception, or a result that is not exactly `2n` lowercase hex chars, raises `RequestIdUnavailable` (a small exception class, no payload). It never returns a partial or empty id and never falls back to hashing.
3. **No content parameters.** The signature accepts only `org_id`. This makes a content-derived id impossible by construction and lets an AST test assert that no edge module imports `hashlib` for id derivation.

**File**: `gateway_v2/gateway_v2/edge/request_holds.py`

4. **`begin_request(org_id, mint=mint_tenant_request_id) -> str`.** Idempotent: if `_request_id` is already set it returns it unchanged; otherwise it mints, calls `note_request`, and returns the id. This is the one way a surface obtains its id. `note_request` stays as the primitive for refusal paths that note a pre-id (3.3). `RequestHolds` remains `__slots__`; no new slot is needed.

**File**: `gateway_v2/gateway_v2/edge/openai_chat.py`

5. **Mint once.** Replace `request_id = self._request_id(identity, req)` + `holds.note_request(request_id)` with `request_id = holds.begin_request(identity.org_id)`, wrapped so `RequestIdUnavailable` returns a refusal outcome (see item 12).
6. **Stop recomputing.** `_admit(identity, req, request_id)` and `_budget(identity, req, cost, key_hash, request_id)` take the id as an argument. Delete the static `_request_id` and the `hashlib`, `_REQUEST_ID_PREFIX`, `_REQUEST_ID_HEX_WIDTH` and `_REQUEST_ID_SEP` constants that only it used. The docstrings that describe a "pure function of the request identity" are rewritten.
7. The plan-unavailable branch (`holds.note_request(mint_request_id())`) is unchanged (3.3). The minted-once id is created after the plan and pin phases, so the pre-id refusal path is not re-routed.

**File**: `gateway_v2/gateway_v2/edge/openai_misc.py`, `incident_misc.py`

8. **Embeddings.** `request_id = holds.begin_request(identity.org_id)` replaces `self._request_id(...)`. `embeddings_gate` keeps its `request_id` argument (the refusal and echo contract) and its `note_request` call becomes a no-op re-note of the same value. Delete `_request_id`. `moderations_gate` is unchanged (3.4).

**File**: `gateway_v2/gateway_v2/edge/rag_common.py`

9. **RAG / vector.** In `serve`, replace `ctx.holds.note_request(_request_id(...))` with `ctx.holds.begin_request(session.org_id)`. `_admit_and_charge` already reads `ctx.holds.request_id`, so it needs no change. Delete `_request_id` and the `hashlib` import if unused.

**File**: `gateway_v2/gateway_v2/edge/mcp.py`

10. **MCP.** `_tenant()` is the first point the org is known and it runs before the incident gate. Call `ctx.holds.begin_request(identity.org_id)` there (before `_incident_org_gate`), so `_incident_org_gate` simply reads `holds.request_id` and the per-org-constant fallback is removed (2.5). `_forward` and `_refuse_reply_message` drop their own `note_request(_request_id(...))` and use the already-noted id. Delete `_request_id`.

**File**: `gateway_v2/gateway_v2/domain/posture.py`, `edge/errors.py`

11. **New refusal code** `REQUEST_ID_UNAVAILABLE = "request_id_unavailable"` (codes only, domain layer), mapped in the single Error_Envelope table to HTTP 503 with a generic message and no `x-request-id` (no id exists). Existing codes are untouched, so golden bytes for every other code are unchanged. Decision D8: reusing `shared_state_unavailable` would avoid a new wire code but would misreport the cause, so the dedicated code is used.
12. **Refusal plumbing.** Each surface catches `RequestIdUnavailable` at the `begin_request` call and refuses with that code before admission, budget and dispatch, so there are zero provider calls and nothing to refund or complete. On the chat lifecycle this returns `_refused(REQUEST_ID_UNAVAILABLE)` with `request_id=None`.

**Files**: `gateway_v2/tests/edge/test_lgw15_isolation.py`

13. **Test update (D6).** Replace the two `ChatSurface._request_id(...)` calls (~L527, ~L536) with `mint_tenant_request_id(block_org)` / `mint_tenant_request_id(allow_org)`. Keep the assertions `block_org in block_rid and allow_org not in block_rid`, the mirror for the allow tenant, and `block_rid != allow_rid`. Because ids are now random, the two ids are distinct with overwhelming probability, and the asserts remain meaningful.

### Not changed

- `admit/admission.py`, `admit/lease.py`, `admit/quota.py` (admit refcount, refund and lease semantics, 3.6, 3.7), `audit/*` (3.8), `egress/*`.
- `identity_resolver.mint_request_id` / `refusal_request_id` (3.3).
- Control plane `gateway/ai_mesh_gateway` `proxy_chat` binding (D7, 3.9).
- Stream id `chatcmpl-{holds.request_id}` in `edge/routes.py` is derived from the id and becomes per-stream unique with no change.

### Investigation: does anything rely on equal ids for equal content?

Searched `gateway_v2/`, `control/` and `frontend/`. Result: nothing relies on it. Details:

| Area | Finding | Impact |
|------|---------|--------|
| Audit sink (`audit/record.py`, `console_schema.py`, `store_audit.py`, `pg` sink) | `request_id` is required and non-empty. The sink's idempotence key is `(org_id, stream_id)`; `request_id` is a lookup index only. No code assumes equal content implies equal id. | Safe. Unique ids make rows separable (fixes 1.6). |
| Control audit ingest (`core/v2_audit_ingest.py::_upsert`) | Finds an existing `EnforcementEvent` by `(org, source=gateway_v2, request_id)` and merges phases with `merge_row`. It relies on one id being shared across the phases of ONE request, not across requests. Under the bug, repeated prompts collapse into one row. | Safe, and the fix removes the collapse. This is why the id must be stable across phases (item 2.6). |
| Control Scan Detail / threat feed (`policy/security_views.py`) | The feed collapses events by `metadata.request_id` (ids shorter than 8 chars are standalone), and Scan Detail merges siblings sharing a request_id. Both assume id equality means the same request. `zs-{org}-<24 hex>` is always longer than 8. | Safe and improved: distinct requests are no longer merged. |
| `_pipeline_io_fingerprint` / `_merge_related_scan_metadata` (PIPELINE-0023) | Compares the prompt fingerprint between siblings that share an id and refuses to merge on mismatch. It is a guard against client-pinned id collisions and does not rely on equal content producing equal ids. | Unchanged and still useful as defence in depth. |
| Control ingest dedupe in `core/tasks.py` | Dedupe key is `(org, event_type, action, request_id)` for ids of 8+ chars. It treats an equal id as a retry. Under the bug, the second identical prompt would be dropped as a retry. | Safe, and the fix removes that false dedupe. |
| `gateway/ai_mesh_gateway` (control-plane proxy_chat) | Uses its own `zs-*` binding (`_bind_gateway_request_id`). Independent of `gateway_v2` ids. | Unchanged (D7). |
| Frontend (`frontend/src/utils/liveGateway.js`, `LogDetailPage.jsx`, etc.) | Reads `zs.request_id` / `data.request_id` for display and for opening Scan Detail by id. No logic compares ids across requests or assumes content equality. | Safe. |
| gateway_v2 internals | `Admitted`, `ShedVerdict`, `BudgetVerdict` echo the id given. Admission `complete(request_id)` and `quota.refund(..., request_id=...)` use it as a key. Equal ids were a hazard here (1.7), not a dependency. | Safe. |
| Existing tests | Only `tests/edge/test_lgw15_isolation.py` calls `ChatSurface._request_id` directly. Other tests in `tests/` pass literal ids such as `"zs-1"`. Golden comparisons normalise the id. | One test updated (item 13). |

Caveat: this is a static search (grep and reading the call sites). It has not been confirmed by running the test suites; the final verification step does that.

## Testing Strategy

### Validation Approach

Two phases: surface counterexamples on the unfixed code first, then verify the fix and preservation. Property tests follow the house idiom: seeded `random.Random`, at least 100 iterations (the repo's convention is 10,000 for fast pure properties), `asyncio.run`, no hypothesis. Each property test carries an in-source tag.

### Exploratory Bug Condition Checking

**Goal**: Show the collision on unfixed code and confirm the root cause (content hash, plus phases recomputing the id).

**Test Plan**: Drive each surface with N byte-identical requests and read the id from the audit record, `x-request-id` and the verdicts. Run on UNFIXED code.

**Test Cases**:
1. **Chat sequential identical**: five identical calls; expect one distinct id (will fail on unfixed code).
2. **Chat concurrent identical**: five at once via `asyncio.gather`; expect one id and cross-released admission slots (will fail).
3. **Stream vs non-stream**: same prompt, `stream` true and false; expect equal ids (will fail).
4. **MCP `tools/list` twice** and **MCP incident gate with no noted id**: expect equal ids (will fail).
5. **Embeddings / RAG / vector repeated**: expect equal ids (will fail).
6. **Phase agreement probe**: after a naive "random in `_request_id`" patch, admission and budget would disagree. Used to confirm root cause 2 (may fail on a half-fix).

**Expected Counterexamples**:
- N calls yield one id. Cause: content hash with no per-request entropy.
- Admission verdict id differs from the noted id under a half-fix. Cause: `_admit`/`_budget` recompute.

### Fix Checking

**Goal**: For all inputs where the bug condition holds, the fixed code gives distinct ids and one id per request.

**Pseudocode:**
```
FOR ALL X WHERE isBugCondition(X) DO
  ids := run_fixed(X).ids
  ASSERT distinct(ids)
  ASSERT FORALL r IN X: oneIdAcrossPhases(r)
  ASSERT FORALL id IN ids: matches("zs-{org}-[0-9a-f]{24}")
END FOR

FUNCTION expectedBehavior(result)
  RETURN result.id matches "zs-{org}-{hex}"
         AND result.id == holds.request_id at every phase
         AND (mintFailed IMPLIES refused AND providerCalls == 0 AND id absent)
END FUNCTION
```

### Preservation Checking

**Goal**: For all inputs where the bug condition does not hold, original and fixed behaviour are identical apart from the id value.

**Pseudocode:**
```
FOR ALL X WHERE NOT isBugCondition(X) DO
  ASSERT normalizeId(original(X)) = normalizeId(fixed(X))
END FOR
```

**Testing Approach**: Property-based plus golden comparison, because they cover the wide input space (prompts, orgs, outcomes) and the frozen wire bytes.

**Test Plan**: Capture behaviour on the unfixed code (existing goldens and suites), then re-run after the fix.

**Test Cases**:
1. **Wire goldens (3.10)**: all GW35 golden error and console bytes match with the id normalised.
2. **Pre-id refusals (3.3)**: identity and plan-unavailable refusals still render a random `zs-<24 hex>` id.
3. **Moderations (3.4)**: still use their own id, unchanged shape.
4. **Client `X-Request-ID` (3.2)**: concurrent calls with a pinned header get distinct gateway ids and the header is ignored.
5. **Verdict echo (3.5)**: shed, budget and incident verdicts, the error body, `x-request-id` and the SSE start header all show one id.
6. **Refund and release (3.6, 3.7)**: zero-provider refusal refunds exactly the spent cost to the owner; `complete` is idempotent and a no-op for an unknown id; the refcount still holds.
7. **Audit (3.8)**: an empty `request_id` is still refused by `AuditRecord`; sink idempotence tests pass unchanged.
8. **Control plane (3.9)**: `gateway/ai_mesh_gateway/tests/test_pipeline_request_id_conflation.py` passes unchanged, and `git diff` shows no change under `gateway/`.
9. **Structure (3.11)**: the AST gates (no module-level mutable, frozen/slotted dataclasses, capacity literals) and import-linter pass.

### Unit Tests

- `mint_tenant_request_id`: shape, tenant scope (the `org` appears, no other org does), hex width, failure paths (raising entropy, short or non-hex output) raise `RequestIdUnavailable`.
- `RequestHolds.begin_request`: first call mints and notes, second call returns the same id, injected failing mint propagates.
- Each surface refuses with `request_id_unavailable`, with zero provider calls, no admit, no spend, when the mint fails (2.8).
- MCP: the incident gate sees the same id the rest of the request carries; no per-org constant remains (grep-style test that `"incident"` is not hashed).
- A static test that no edge module outside `identity_resolver` and `request_ids` derives an id with `hashlib`.

### Property-Based Tests

Both are tagged in-source and run at least 100 iterations (default 1,000), seeded, with the failing seed and iteration printed on failure.

- **Property test A (tag: `# Feature: request-id-uniqueness, Property 1a: distinct requests never share an id`)**
  Generate random orgs and bodies, and for each build a set of K (2 to 8) byte-identical requests per surface (chat stream, chat non-stream, embeddings, RAG, vector, MCP). Run them sequentially in one case and concurrently (`asyncio.gather`) in another. Assert all ids are pairwise distinct, match `zs-{org}-[0-9a-f]{24}`, and that completing or refunding one request leaves the others' slots and spends untouched (2.7). Include cases with identical content across two different orgs to confirm tenant scoping.
- **Property test B (tag: `# Feature: request-id-uniqueness, Property 1b: all phases of one request carry one id`)**
  For each generated request, capture the id seen by the incident gate, `admission.admit`, `quota.evaluate`, `holds.request_id`, the audit record, the console projection, the error envelope or response header, and the SSE start. Assert the set has exactly one element. Mix outcomes (allow, shed, budget refuse, input block, incident refuse) and inject a mint failure in a fraction of cases to assert refusal with no id and no provider call.

### Integration Tests

- Full chat, embeddings, RAG, vector and MCP flows with N identical calls: audit rows are N and distinct; control `_upsert` creates N `EnforcementEvent` rows (extends `tests` for `v2_audit_ingest`).
- Streaming flow: `chatcmpl-{request_id}` stream ids are unique per stream and the SSE start header carries the same id as the final audit row.
- Admission under saturation with identical content: per-owner fairness is unchanged and each shed verdict echoes its own request's id.
- Scan Detail and threat feed: repeated prompts show as separate rows (collapse-by-request_id no longer merges them).
- Full gate: `pytest` for `gateway_v2`, `mypy --strict`, ruff, import-linter, the five AST gates, and the control and frontend suites that read `request_id`.

## Open Items

None. Both former open items are resolved as decisions D8 and D9 above:

- The mint-failure code is `request_id_unavailable`, HTTP 503, no id (item 11 applies this; the "open point" wording there is superseded by D8).
- No set-once guard on `RequestHolds.note_request` (D9).
