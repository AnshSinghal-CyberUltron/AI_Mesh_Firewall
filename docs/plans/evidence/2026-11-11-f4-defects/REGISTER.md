# F4 defect register (11 Nov 2026)

Owner: Gateway reliability engineer (feature F4, "fix open defects").
Verified at `origin/develop` **25c33c86**. `develop` then moved to **50cb47d6** (#110); that delta
changes only `edge/scan_calibration.py` and four GW28/GW34 test files, none of the files cited below.

Every row was re-checked on the tree, not taken from older notes. The repro scripts and their
captured output are in this directory (`repro_*.py`, `repro_*.out.txt`). Each script runs from
`gateway_v2/` with `/home/claude/work/venv-ci/bin/python`, except `repro_f5.py`, which runs from
`control/` with the control venv.

## Sources of the labels

| Label in the old list | Exact source |
|---|---|
| "built-ins chat-only" | `docs/plans/evidence/2026-11-04-integration/FINDINGS.md` **F3**; SUMMARY row 13 (L31-1 FAIL). `docs/AI_MESH_RUNBOOK_v3.1_PROGRESS.md` L26-1 is the *customer-rule* scope test, which passes. `docs/MODULE1_OPENAI_SDK_TEST_REPORT.md` around line 689 is **I-07**, a v1 (`gateway/ai_mesh_gateway`) finding: `blocked_keywords` were enforced on chat but not on embeddings |
| "null request_id" | FINDINGS.md **F4**; SUMMARY row 32 |
| "MCP needs_reauth" | FINDINGS.md **F5** |
| F6, F7, F8 | FINDINGS.md F6 (routing trace after an upstream 500), F7 (RAG unknown collection 502), F8 (GW33 grounding streaming-only) |
| GW30 transport defects | `HANDOFF.md` items 3 and "Pre-existing"; `docs/plans/2026-11-02-gw30-gate-fix.md` "recorded as open GW30 findings" 1–9; FOUND, NOT FIXED in `2026-11-03-gw30-{milvus,pinecone}-live-gate.md` |
| SDK-matrix evidence overwrite | `gateway_v2/tests/openai_conformance/sdk_matrix/conftest.py` |
| `request_pre_check_exceeded` 500 | `HANDOFF.md` line 97 |
| V9 Publisher series | `HANDOFF.md` V9 and item 4 |

`docs/plans/2026-11-04-gw32-mcp-transport.md` and `2026-11-04-gw17b-security-design.md` list no open defect. They record residual risks only (sandbox holes, access logs). Those are not reproducible defects, so they are not registered here.

## Summary

Severity classes: **security**, **correctness**, **hygiene**. Status: CONFIRMED (reproduced at develop), NOT REPRODUCIBLE (with evidence), LATENT (real in code, no serving path reaches it today).

| # | Defect | Status | Severity | Root cause (file:line at 25c33c86) | Overlap / blocker | Plan |
|---|---|---|---|---|---|---|
| R1 | Built-in rules cover chat only (F3) | CONFIRMED | security | `control/.../core/firewall_v2_map.py:513` (`_draft`), `firewall_v2_controls.py:164` (`threat_drafts`) publish `surfaces: ["chat"]`; the resolver selects by surface (`resolve/conflict.py:66`) | `firewall_v2_publish.py` is also in F1a-1 §5.2.3 (publish-time regex limits), in a different function; the logic lives in `firewall_v2_map.py` | **PR 1** |
| R2 | No built-in rule for `Category.SECRET` (new, found reproducing F3) | CONFIRMED | security | The detectors emit `secret` for AWS and API keys (`detect/deterministic/patterns.py:153,301,309`). `_INPUTS`/`_OUTPUTS` (`firewall_v2_map.py:275-282`) have no `secret` row, and the join is by category equality (`resolve/conflict.py:78-90`) | needs an owner decision: which console fields govern it, and whether it rolls out behind the D-8 gate | PR 2 after the decision |
| R3 | MCP server marked `needs_reauth` on a control→gateway 401 (F5) | CONFIRMED | correctness (availability) | `mcp_connector/views.py:823` sends the gateway hop's non-200 to the upstream classifier. `_classify_sync_error` (`:603`, the `"auth"`/`" 401"` keys) labels it MCP_AUTH_FAILED. The heuristic at `:922` sets `needs_reauth`, and v2 then refuses every call with 403 `mcp_upstream_needs_reauth` | none | PR 3 |
| R4 | `request_pre_check_exceeded` (and `malformed_request_body`) render 500 `gateway_error` | CONFIRMED | correctness | No `_ENVELOPE` entry (`edge/errors.py:159-348`), so the generic 500 is used. A client error is reported as a server fault, and stock SDKs retry 5xx twice | snapshot in `tests/edge/test_v2_leaks_enforcement_preservation.py:284-289` changes with it | PR 4 |
| R5 | Non-stream upstream error envelopes: `error.request_id: null`, no `x-request-id` (F4) | CONFIRMED | correctness (observability) | `edge/openai_nonstream.py:368` calls `render_http(code, retry_after_s=...)` without `request_id` | **blocked**: these bytes are frozen by `tests/edge/test_gw35_golden_capture.py` + `golden/gw35_pre_change.json`. The owner reverted the fix (4d75e401 → 7caa3a2e). GW35 is in F1a-1 scope | needs an owner decision to re-capture the golden |
| R6 | Trace shows `model_routing: skip / refused_internal_error` after a real upstream failure (F6) | CONFIRMED | correctness (observability) | `dispatch/routed_provider.py:287` records a non-fallback upstream failure as a refusal, and `:310` defaults its reason to `refused_internal_error`. `edge/console_projection.py:64` treats any `refusal_code` as a routing refusal | F1a-3 §7.7 (routing trace schema; requirements only, no code) | PR 5 |
| R7 | RAG query for a collection the org lacks → 502 `provider_unreachable` (F7) | CONFIRMED (dev store) | correctness | `dispatch/rag_dev_store.py:37-39`. No posture code exists for a missing collection; the live transports map store not-found exceptions to `provider_unreachable` too (`vector_bounded_op.py`) | GW36 owns production store wiring | PR 6 (dev store + declared code) or defer |
| R8 | GW33 grounding is streaming-only (F8) | CONFIRMED, **no enforcement effect** | hygiene | Chat passes `context_chunks=()` on both paths (`chat_stream.py:437`, `openai_nonstream.py:302`), so grounding is SKIPPED by design (GW33 D-15). The non-stream path only omits the SKIPPED record | GW31/GW33 own the chat context source | no fix now |
| R9 | `check_tenant_scale` at `pinecone_transport.py:140` | **NOT REPRODUCIBLE** | — | Fixed on develop by 54aa9926, a gate false positive (`.keys()` on one in-memory response). `python -m lint.check_tenant_scale` exits 0; `tests/gates/test_check_tenant_scale.py` 13 passed | — | none |
| R10 | GW30 open findings 1–6, 8, 9 (filters unread, partial upserts, delete counts, swallowed delete errors, Weaviate batch, Mongo client leak, sync SDK timeouts) | LATENT | correctness (filters: security once wired) | No transport reads `req.filter` (0 hits in `dispatch/vector_transports/`), yet `vector_connectors.py:268` declares `filters=True`. No serving path imports `dispatch/vector_transports` (no `edge` import) | GW36 wires production stores | defer; list kept here |
| R11 | GW30 finding 7: `safe_name` maps `a-b` and `a_b` to one collection | LATENT, mitigated | — | `_common.py:50-52`. The GW31 path passes injectively encoded names (`rag_namespace.py:67`, `_u`/`_d` escapes), so no two namespaces collide | — | none |
| R12 | SDK-matrix conftest rewrites committed evidence on every run | CONFIRMED | hygiene | `tests/openai_conformance/sdk_matrix/conftest.py:82-100` writes `docs/plans/evidence/2026-11-04-gw34/sdk-matrix-report.json` in `pytest_sessionfinish`. Without the Node SDK, 10 cells are rewritten to `skipped` (`sdk_matrix_mutation.txt`) | none | PR 7 |
| R13 | V9: `tool_calls_released`/`withheld` are not Publisher series | CONFIRMED (missing) | hygiene (observability) | Counters reach only the audit detail (`edge/request_holds.py:191-202`) | **overlaps F1a-1 N14**: Publisher signature and `series_count` (§5.5) | deferred until F1a-1 lands |
| R14 | v1 I-07 (`blocked_keywords` not on embeddings) | NOT REPRODUCIBLE on v2 | — | The keyword row is a `pipeline` policy row, so it publishes `chat` + `embeddings` (`firewall_v2_publish.py:48-53, 227-257`) | — | none |
| R15 | RAG generator releases text unchanged on a REWRITE disposition (new, found while designing R1) | LATENT | security | `edge/rag_stages.py:393-404` handles BLOCK and REDACT only, so a REWRITE falls through to `return text`. Reachable today only through a customer rewrite policy on the `rag` domain for an org in `V2_REWRITE_ORGS` | R1 extends `rewrite` as `redact` so that R1 does not create a new path to it | separate PR on request |

Order of work by severity: R1, R2 (decision), R3, R4, R5 (decision), R6, R7, R12. R13 waits for F1a-1. R8–R11 and R14 need no change now.

## Detail

### R1: built-ins are chat-only (F3)
- **Repro:** `repro_f3.py` → `repro_f3.out.txt`. It publishes a default document with the real control code, compiles it, scans with `BASE_DETECTORS` and resolves per surface. An injection is `block` on chat and `allow` on mcp, rag, vector and embeddings. An email is `redact` on chat and `allow` elsewhere. Every published rule has `surfaces: ['chat']`.
- **Expected:** a built-in protection runs wherever its scan runs: MCP tool arguments and results, the RAG query, chunks and answer, vector retrieval, and embeddings input for PII (runbook L31-1, "poisoned doc detected on every RAG and vector path").
- **Fix (PR 1):** each built-in category row gets an extension row for the other surfaces it protects, and the chat row keeps its bytes. These are new rule drafts, so they follow the existing D-8 gate, `V2_PLAN_NEW_RULES_MODE` (default `monitor`).

### R2: `secret` category has no built-in rule
- **Repro:** `repro_f3.out.txt`, row `aws_key`. Findings are `['secret']`, and the decision is `allow` on every surface, both phases, chat included. The console's `output_credential_*` row is category `credential`, which the AKIA finding does not join.
- **Decision needed:** (a) input `secret`: block under `content_filtering_enabled`, as `toxicity` and `prompt_injection` are? (b) output `secret`: follow `output_credential_enabled`/`_action`? (c) Roll out behind the D-8 gate like R1?

### R3: `needs_reauth` from a gateway-hop 401 (F5)
- **Repro:** `repro_f5.py` → `repro_f5.out.txt`. `gateway HTTP 401: Missing or malformed Authorization header` gives code `MCP_AUTH_FAILED` and the summary "...re-authorize the connection...", so the `needs_reauth` heuristic fires on `re-authorize`.
- **Why it is wrong:** the v1 internal discover endpoint reports upstream MCP failures as HTTP 200 with a JSON-RPC error (`gateway/ai_mesh_gateway/mcp_proxy.py` `_discovery_error_response`; `_adapter_forward` always answers 200). A non-200 is therefore the control→gateway hop itself (internal key, request shape, disabled, not found), never the upstream's credentials.

### R4: pre-check refusals render 500
- **Repro:** `repro_wire.py`. A 1,048,652-byte body (limit 1,048,576) returns `status=500 ... "type": "gateway_error", "code": "request_pre_check_exceeded"`.

### R5: null `request_id` on non-stream upstream errors (F4)
- **Repro:** `repro_wire.py`. Non-stream `upstream_error` returns `502, x-request-id=None, "request_id": null`; streaming returns both. The same holds for `upstream_rate_limited`, 429.
- **Blocked:** wire-contract decision recorded in FINDINGS.md ("Reverted (owner decision, left open as F4)").

### R6: routing trace after an upstream failure (F6)
- **Repro:** `repro_f6.py`. One candidate, upstream `upstream_error`. The decisions are `primary a` then `refused_internal_error None upstream_error`, and the stages read `model_routing skip ran=False rule=refused_internal_error|-|0|-|m`, `model_output error`.

### R7: unknown RAG collection (F7)
- **Repro:** `repro_f7_f8.py`. `InMemoryVectorAdapter.query` for a collection never created gives `provider_unreachable`, rendered as HTTP 502.

### R12: SDK-matrix evidence overwrite
- **Repro:** run `pytest tests/openai_conformance/sdk_matrix` on a host without `node_modules/openai`. The run passes (45 passed, 10 skipped) and leaves `docs/plans/evidence/2026-11-04-gw34/sdk-matrix-report.json` modified: 32 insertions and 33 deletions, every node cell `ran 0, skipped "node_modules/openai is absent"` (`sdk_matrix_mutation.txt`).
