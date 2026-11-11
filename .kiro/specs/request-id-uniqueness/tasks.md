# Implementation Plan

Paths are relative to the worktree root `/work/suraj/AI_Mesh_Firewall/.worktrees/bugfix-f2-request-id`. Source is under `gateway_v2/gateway_v2/`, tests under `gateway_v2/tests/`. Work on branch `bugfix-f2-request-id`. Design decisions D8 (new code `request_id_unavailable`, HTTP 503, no id) and D9 (no set-once guard on `RequestHolds.note_request`) are recorded in design.md.

- [x] 1. Write bug condition exploration test
  - **Property 1: Bug Condition** - Identical requests collide on one request_id
  - **CRITICAL**: This test MUST FAIL on unfixed code - failure confirms the bug exists
  - **DO NOT attempt to fix the test or the code when it fails**
  - **NOTE**: This test encodes the expected behavior - it will validate the fix when it passes after implementation
  - **GOAL**: Surface counterexamples that demonstrate the id collision
  - **Scoped PBT Approach**: The bug is deterministic, so scope the property to concrete failing cases: byte-identical content, same tenant, per surface
  - Create `gateway_v2/tests/edge/test_request_id_uniqueness_explore.py` using the house idiom (seeded `random.Random`, `asyncio.run`, no hypothesis) and existing harnesses (`tests/edge/_chat_lifecycle_fixtures.py`, `_mcp_fixtures.py`)
  - Cases from Bug Condition `isBugCondition` / `isBugConditionIncidentGate`:
    - five identical chat calls, sequential, then via `asyncio.gather` (1.1, 1.7)
    - stream vs non-stream with identical content (1.2)
    - MCP `tools/list` twice with identical body (1.3)
    - MCP incident gate reached with no noted id: the fallback id is the same for every call of an org (1.5)
    - repeated embeddings, RAG and vector calls (1.4)
    - audit rows for repeated prompts share one `request_id` (1.6)
  - Assert expectedBehavior: ids pairwise distinct and matching `zs-{org}-[0-9a-f]{24}` (2.1-2.5)
  - Run on UNFIXED code
  - **EXPECTED OUTCOME**: Test FAILS (this is correct - it proves the bug exists)
  - Document counterexamples (e.g. "5 identical chat calls all return one id") in the task notes
  - Mark task complete when the test is written, run, and the failure is documented
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7_

- [x] 2. Write preservation property tests (BEFORE implementing fix)
  - **Property 2: Preservation** - Behavior outside id derivation is unchanged
  - **IMPORTANT**: Follow observation-first methodology
  - Observe on UNFIXED code, for inputs where the bug condition does not hold, then encode as tests in `gateway_v2/tests/edge/test_request_id_preservation.py` (seeded `random.Random`, at least 100 iterations, id values normalised):
    - ids keep the `zs-` prefix and `zs-{org}-` form; one tenant's id never contains another org id (3.1)
    - client `X-Request-ID` is ignored as the canonical id (3.2)
    - identity and plan-unavailable refusals render a random content-free `zs-<24 hex>` id with unchanged wire shape (3.3)
    - moderations keep their own per-request id (3.4)
    - one request echoes one id in admission, budget and incident verdicts, error body, `x-request-id` and SSE start header (3.5)
    - zero-provider-call refusal refunds exactly the spent cost to the same owner only (3.6)
    - release performs one `complete`; `complete` is idempotent and a no-op for an unknown id (3.7)
    - `AuditRecord` still rejects an empty `request_id`; sink idempotence unchanged (3.8)
    - GW35 golden wire bytes match with the id normalised (3.10)
    - failures in any phase fail closed (3.11)
  - Record the control-plane baseline: `gateway/ai_mesh_gateway/tests/test_pipeline_request_id_conflation.py` passes on unfixed code (3.9)
  - Run tests on UNFIXED code
  - **EXPECTED OUTCOME**: Tests PASS (this confirms baseline behavior to preserve)
  - Mark task complete when tests are written, run, and passing on unfixed code
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10, 3.11_

- [x] 3. Fix for request_id collision on byte-identical requests

  - [x] 3.1 Add the `request_id_unavailable` posture code (domain layer)
    - In `gateway_v2/domain/posture.py` add `REQUEST_ID_UNAVAILABLE = "request_id_unavailable"` (codes only, no logic)
    - Do not reuse `shared_state_unavailable` (decision D8)
    - Keep existing codes untouched so other golden bytes do not change
    - _Bug_Condition: isBugCondition(input) where mint cannot produce an id (2.8)_
    - _Expected_Behavior: mintFailed IMPLIES refused AND providerCalls == 0 AND id absent_
    - _Preservation: existing posture codes and their wire bytes (3.10)_
    - _Requirements: 2.8, 3.10_

  - [x] 3.2 Add the mint helper `edge/request_ids.py`
    - `RequestIdUnavailable` exception (no payload)
    - `mint_tenant_request_id(org_id, *, token_hex=secrets.token_hex)` returns `zs-{org_id}-{24 hex}`; `_ID_BYTES = 12` is a format width
    - Accept only `org_id`; no content parameters, no `hashlib`
    - Fail closed: any exception or a result that is not exactly 24 lowercase hex chars raises `RequestIdUnavailable`; never return a partial, empty or hashed id
    - No module-level mutable state; entropy source is an injected default parameter
    - Unit tests `gateway_v2/tests/edge/test_request_ids.py`: shape, tenant scope, hex width, failing entropy, short and non-hex output
    - _Bug_Condition: isBugCondition(input) from design (content-derived id)_
    - _Expected_Behavior: expectedBehavior(result) - id matches `zs-{org}-{hex}`, minted without content_
    - _Preservation: `zs-{org_id}-` form (3.1), no module-level mutable state (3.11)_
    - _Requirements: 2.1, 2.8, 3.1, 3.11_

  - [x] 3.3 Add `RequestHolds.begin_request` in `edge/request_holds.py`
    - `begin_request(org_id, mint=mint_tenant_request_id) -> str`: if an id is already set return it unchanged; else mint, call `note_request`, return the id
    - Keep `__slots__`; add no slot
    - Do NOT add a set-once guard to `note_request` (decision D9); refusal paths may still note a pre-id
    - Unit tests: first call mints and notes, second call returns the same id, a failing mint propagates `RequestIdUnavailable`
    - _Bug_Condition: isBugCondition(input) where phases recompute or re-mint the id_
    - _Expected_Behavior: one id minted once per request and carried on holds (2.6)_
    - _Preservation: `Admitted`/verdict echo (3.5), pre-id refusal path via `note_request` (3.3)_
    - _Requirements: 2.6, 3.3, 3.5_

  - [x] 3.4 Map `REQUEST_ID_UNAVAILABLE` in `edge/errors.py`
    - Add to the single Error_Envelope table: HTTP 503, generic message, no `x-request-id` header, no id in the body
    - Add a unit test for the mapping and a golden-neutrality check (other codes' bytes unchanged)
    - _Expected_Behavior: refusal with no id when the mint fails (2.8)_
    - _Preservation: frozen GW35 golden wire bytes for all other codes (3.10)_
    - _Requirements: 2.8, 3.10_

  - [x] 3.5 Chat: mint once and stop recomputing (stream and non-stream)
    - `edge/openai_chat.py`: replace `self._request_id(identity, req)` plus `holds.note_request(...)` with `holds.begin_request(identity.org_id)`; catch `RequestIdUnavailable` and return `_refused(REQUEST_ID_UNAVAILABLE)` with `request_id=None` before admission, budget and dispatch
    - `_admit(identity, req, request_id)` and `_budget(identity, req, cost, key_hash, request_id)` take the id as an argument; remove their recomputation
    - Delete the static `_request_id` and its `hashlib` and `_REQUEST_ID_*` constants; rewrite docstrings that describe a pure-function id
    - Leave the plan-unavailable `holds.note_request(mint_request_id())` branch unchanged
    - Verify `edge/chat_lifecycle.py`, `chat_stream.py`, `openai_nonstream.py`, `openai_responses.py` and `routes.py` read the id from `holds` only (the stream id `chatcmpl-{request_id}` stays derived)
    - _Bug_Condition: isBugCondition(input) for chat, stream and non-stream (1.1, 1.2, 1.7)_
    - _Expected_Behavior: distinct id per request, same id in every phase (2.1, 2.2, 2.6, 2.7)_
    - _Preservation: verdict and header echo (3.5), refund (3.6), release (3.7), pre-id refusals (3.3)_
    - _Requirements: 2.1, 2.2, 2.6, 2.7, 2.8, 3.3, 3.5, 3.6, 3.7_

  - [x] 3.6 Embeddings: mint once
    - `edge/openai_misc.py` (and `incident_misc.py` where it re-notes): `request_id = holds.begin_request(identity.org_id)`; delete `_request_id`
    - Catch `RequestIdUnavailable` and refuse with `REQUEST_ID_UNAVAILABLE`
    - `embeddings_gate` keeps its `request_id` argument; `moderations_gate` unchanged
    - _Bug_Condition: isBugCondition(input) for embeddings (1.4)_
    - _Expected_Behavior: distinct per-request id (2.4, 2.6)_
    - _Preservation: moderations keep their own id (3.4)_
    - _Requirements: 2.4, 2.6, 2.8, 3.4_

  - [x] 3.7 RAG and vector: mint once
    - `edge/rag_common.py` `serve`: replace `ctx.holds.note_request(_request_id(...))` with `ctx.holds.begin_request(session.org_id)`; delete `_request_id` and the unused `hashlib` import
    - Catch `RequestIdUnavailable` and refuse with `REQUEST_ID_UNAVAILABLE`
    - Check `rag.py`, `rag_vector.py`, `rag_ingest.py`, `rag_admin.py` and `rag_entries.py` for any other id derivation
    - `_admit_and_charge` already reads `ctx.holds.request_id`; no change expected
    - _Bug_Condition: isBugCondition(input) for RAG and vector (1.4)_
    - _Expected_Behavior: distinct per-request id (2.4, 2.6)_
    - _Preservation: admission and refund keyed on the carried id (3.5, 3.6, 3.7)_
    - _Requirements: 2.4, 2.6, 2.7, 2.8, 3.5, 3.6, 3.7_

  - [x] 3.8 MCP including the incident-gate fallback
    - `edge/mcp.py`: call `ctx.holds.begin_request(identity.org_id)` in `_tenant()` before `_incident_org_gate`
    - `_incident_org_gate` reads `holds.request_id`; remove the `_request_id(org_id, "incident", {})` per-org-constant fallback
    - `_forward` and `_refuse_reply_message` stop re-noting a hashed id and use the noted one
    - Delete `mcp._request_id`; check `mcp_*.py` (identity, reply, policy gate, cred gate, session gate, console) for other derivations
    - Catch `RequestIdUnavailable` and refuse with `REQUEST_ID_UNAVAILABLE`
    - _Bug_Condition: isBugCondition(input) for MCP, and isBugConditionIncidentGate (1.3, 1.5)_
    - _Expected_Behavior: distinct per-request id; incident gate uses the per-request id (2.3, 2.5, 2.6)_
    - _Preservation: incident verdict echo (3.5), identity refusals (3.3)_
    - _Requirements: 2.3, 2.5, 2.6, 2.8, 3.3, 3.5_

  - [x] 3.9 Update `tests/edge/test_lgw15_isolation.py`
    - Replace the two `ChatSurface._request_id(...)` calls (~L527, ~L536) with `mint_tenant_request_id(block_org)` and `mint_tenant_request_id(allow_org)`
    - Keep the tenant-scope assertions (`block_org in block_rid and allow_org not in block_rid`, the mirror, and `block_rid != allow_rid`)
    - Fix any other test that referenced a removed `_request_id` or relied on equal ids for equal content
    - _Preservation: tenant-scoped `zs-{org}-` form (3.1)_
    - _Requirements: 3.1_

  - [x] 3.10 Write property tests (tagged in-source, at least 100 iterations)
    - Create `gateway_v2/tests/edge/test_request_id_uniqueness_property.py`; seeded `random.Random`, `asyncio.run`, default 1,000 iterations (never below 100), print the failing seed and iteration
    - **Property test A**, tagged `# Feature: request-id-uniqueness, Property 1a: distinct requests never share an id`:
      - K (2 to 8) byte-identical requests per surface (chat stream, chat non-stream, embeddings, RAG, vector, MCP), sequential and via `asyncio.gather`
      - assert ids pairwise distinct and matching `zs-{org}-[0-9a-f]{24}`
      - include identical content across two orgs (tenant scoping)
      - completing or refunding one request leaves the others' slots and spends untouched
    - **Property test B**, tagged `# Feature: request-id-uniqueness, Property 1b: all phases of one request carry one id`:
      - capture the id seen by the incident gate, `admission.admit`, `quota.evaluate`, `holds.request_id`, the audit record, the console projection, the error envelope or response header, and the SSE start
      - assert exactly one distinct value per request
      - mix outcomes (allow, shed, budget refuse, input block, incident refuse)
      - inject a mint failure in a fraction of cases: refusal, no id, zero provider calls, no admit, no spend
    - Add a static test that no edge module outside `identity_resolver` and `request_ids` derives an id with `hashlib`
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8_

  - [x] 3.11 Verify bug condition exploration test now passes
    - **Property 1: Expected Behavior** - Distinct requests get distinct ids, one id per request
    - **IMPORTANT**: Re-run the SAME test from task 1 - do NOT write a new test
    - The test from task 1 encodes the expected behavior
    - **EXPECTED OUTCOME**: Test PASSES (confirms bug is fixed)
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8_

  - [x] 3.12 Verify preservation tests still pass
    - **Property 2: Preservation** - Behavior outside id derivation is unchanged
    - **IMPORTANT**: Re-run the SAME tests from task 2 - do NOT write new tests
    - Also re-run `gateway/ai_mesh_gateway/tests/test_pipeline_request_id_conflation.py` and confirm `git diff` shows no change under `gateway/` (3.9)
    - **EXPECTED OUTCOME**: Tests PASS (confirms no regressions)
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10, 3.11_

- [x] 4. Offline gate
  - Run from `gateway_v2/`: full `pytest`, `mypy --strict`, `ruff`, import-linter, and the five AST gates (`capacity_literals`, `no_module_mutable`, `frozen_dataclasses`, `http_outside_edge_resolve`, `tenant_scale`)
  - The only known failure is the Pinecone tenant-scale finding at `pinecone_transport.py:140`; anything else must be fixed
  - Confirm no import-linter contract change was needed (all edits are in `edge` plus a domain code)
  - _Requirements: 3.8, 3.10, 3.11_

- [x] 5. Live check against the real stack
  - Bring up the stack (compose files in the repo root) and run against it, not stubs
  - Five identical calls each for chat non-stream, chat stream, MCP `tools/list`, embeddings and RAG return five distinct ids matching `zs-{org}-<24 hex>` (2.1-2.4)
  - For one request, the audit row and the Scan Detail row share one `request_id`, equal to the `x-request-id` header (2.6, 3.5)
  - Repeated prompts appear as separate audit and Scan Detail rows (1.6)
  - A zero-provider-call refusal refunds exactly the spent cost (3.6); admission slots return to baseline (3.7)
  - The control-plane `proxy_chat` path is unchanged: PIPELINE-0023 behavior and the diff under `gateway/` is empty (3.9)
  - Save evidence under `docs/plans/evidence/` (dated directory) with commands and outputs
  - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.6, 3.5, 3.6, 3.7, 3.9_

- [-] 6. Changelog pointers (same commit as the fix)
  - Add the pointer entry to all four memories in the same commit:
    - `docs/pipeline/CHANGELOG.md` (canonical entry)
    - `AGENTS.md` (one-line pointer, newest last)
    - `.cursor/rules/pipeline-changelog.mdc`
    - Ruflo: `memory store --namespace pipeline/changes` with the same text
  - Entry covers: content-hash id replaced by a per-request id minted once and carried on `RequestHolds`, new `request_id_unavailable` 503 code, MCP incident-gate fallback removed, decisions D8 and D9, verification results, and the known Pinecone tenant-scale finding
  - _Requirements: 3.9_

- [ ] 7. Commit on branch `bugfix-f2-request-id` (do not push)
  - Confirm the current branch is `bugfix-f2-request-id`
  - Stage specific files by name (new and changed source, tests, spec files, changelog files, evidence); never `git add -A` or `git add .`
  - Check that no secrets or `.env` files are staged
  - Commit once with the fix and the four changelog updates together; leave git config and hooks alone; do not push
  - _Requirements: 3.9_

- [ ] 8. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.
