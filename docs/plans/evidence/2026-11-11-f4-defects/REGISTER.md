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
| v1 golden suite (added by the coordinator) | CI `chat-pipeline-golden` / `golden-freeze`, run 42977201040; waiver in `docs/ci/MERGE_POLICY.md:33` ("defect-fix feature") |

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
| R17 | GW30 `OrgCredential` pickles with its secret in the bytes (reported by the F1a-3 architect) | CONFIRMED | security (credential exposure), **LATENT** | `gateway_v2/dispatch/vector_credentials.py:66-101`. The class deletes the dataclass `__getstate__`/`__setstate__` and claims "NO serialization method". On Python 3.11+, `object.__getstate__` still pickles slots: `pickle.dumps` with protocols 2–5 (the default is 5) writes `_secret` into the bytes on 3.12.3 and 3.13.18. `loads` and `deepcopy` raise `FrozenInstanceError`, so it serialises out but never round-trips (`probe_vector_credential_pickle.out.txt`). `repr` is redacted | overlaps F1a-3 §7.2 (`SealedCredential`: `__reduce__`/`__getstate__`/`__copy__` raise); share one helper | queued, small PR; not urgent |
| **R16a** | **v1 gateway (LIVE in production): obfuscated, encoded and split PII/secrets on INPUT reach the model** (21 of the 153 `golden-freeze` failures) | CONFIRMED | **security, top** | `gateway/ai_mesh_gateway/scanner.py:1197-1330`. Task 3.1 (policy-driven detection) reduced `_scan_prompt_sync` to a plain-value floor (`docs/pipeline/CHANGELOG.md:267`), and PIPELINE-0032 restored only the plain values. The G33/G53/G69/G71/G87/G91 decoders no longer drive the verdict. An HTML-entity or markdown-split SSN gets verdict `allow`, and `redact_all` leaves its bytes unchanged (`probe_v1golden.out.txt`) | v1 code; no F1a or hotfix file | **first after this PR** |
| **R16b** | v1: no deterministic injection or jailbreak floor at Tier-1 (125 of the 153) | CONFIRMED; **stale expectation against a deliberate design**, residual risk open | security (owner decision) | `scanner.py:1210-1212`: "Injection / jailbreak / RAG-poisoning stay policy-only (task 3.1) … A zero-policy org will not get a built-in injection block." Prod relies on org policies plus the Tier-2 model floor (PIPELINE-0033; `ENABLE_TIER2=true`, `GATEWAY_TIER2_SAMPLE_RATE` in `docker-compose.prod.yml`). With Tier-2 off, sampled out or degraded and no org injection policy, `ignore all previous instructions` is `allow` | owner decision: restore a Tier-1 floor, or re-baseline the golden | decision |
| R16c | v1 output-side golden cases (G35, G88, G10; 5 of the 153) | **UNVERIFIED** at the OutputGuard level | security, if real | The golden calls `InputScanner(config={})._scan_output_sync`, which returns `flag` for PII (`scanner.py:1698-1705`). `sanitize_output_for_verdict` does not mask a `flag`. The production OutputGuard (`output_guard.py:1254`) applies the org's `output_pii_action`, which these tests do not exercise | — | verify with an OutputGuard-level test before any fix |
| R16d | v1 stale fixtures (2 of the 153) | stale expectation | hygiene | `plain_email`: the oracle flags the smart-mask `j***@e***.com` itself (`email_smart_masked`, PIPELINE-0012); the raw address is gone. `g101` card: the fixture `4111-1111-1111-1119` fails Luhn, and the Luhn gate (be797529) rejects it by design | — | fix the fixtures with R16a |

**R16 deployment status: v1 is the live production data plane.**
- `docker-compose.prod.yml:183-235`: service `gateway`, image `ai-mesh-gateway`, which `infra/scripts/build-push-images.sh:135-136` builds from `gateway/Dockerfile`. It serves `GATEWAY_PUBLIC_URL=https://aimeshgateway.zeroshield.ai`.
- `deploy/nginx.conf:124-125` and `deploy/nginx-ssl.conf:47-63,184-185`: `/v1` is proxied to `gateway:8300`.
- `infra/terraform/envs/prod/main.tf:211`: `container_port = 8300`.
- `gateway-v2` is profile-gated in prod, so a plain `up` never starts it (`docker-compose.prod.yml:377-381`).
- Control defaults to `AMF_GATEWAY_BACKEND=v1` (`main_app/settings.py:330`).
- gateway_v2 does not serve production completions until F1 lands.

Order of work by severity: **R16a** (v1, live), R1 (this PR), R16b and R2 (decisions), R3, R4, R5 (decision), R6, R7, R12. R16c needs verification first. R17 is latent (queued; align with F1a-3). R13 waits for F1a-1. R8–R11 and R14 need no change now.

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

### R16: v1 gateway golden suite (`golden-freeze`, 153 failures)
- **Repro:** `cd gateway && GATEWAY_LIVE=0 /home/claude/work/venv-v1/bin/python -m pytest tests/golden -q -p no:cacheprovider` gives `153 failed, 515 passed, 7 skipped`. That is the same id set as CI run 42977201040 (job 130378920740, head bd88e563) and as the `docs/ci/MERGE_POLICY.md` waiver. List: `v1_golden_failures.txt`.
- **Classification:** 21 real input misses (R16a), 125 injection cases that encode the pre-task-3.1 Tier-1 contract (R16b), 5 output cases unverified (R16c), 2 stale fixtures (R16d). Sample probe: `probe_v1golden.py` → `probe_v1golden.out.txt`:
  - plain SSN and AKIA still `redact` (the floor works for plain values);
  - HTML-entity SSN → `allow`, bytes unchanged after `redact_all`;
  - markdown-split `1**2**3-45-6789` → `allow`, bytes unchanged;
  - SSN split across two user turns → `allow`;
  - percent-encoded AKIA → verdict `allow`, although `redact_all` would mask it;
  - plain injection → `allow`.
- **Expected for R16a:** the platform PII/secret floor that PIPELINE-0032 restored for plain values also holds for the obfuscations G33/G53/G69/G71/G87/G91 once fixed, when the value is recoverable by canonicalisation or decoding: redact, or block where masking is impossible.

### R17: `OrgCredential` pickles its secret (GW30)
- **Repro:** `probe_vector_credential_pickle.py` → `.out.txt`. Python 3.13.18 and 3.12.3 give `dumps_p2..p5_secret_in_bytes: True`, `p0/p1: TypeError`, `loads: FrozenInstanceError`, `deepcopy: FrozenInstanceError`, `repr_redacted: True`. This matches the F1a-3 probe (`wt-f1a3/.../2026-11-11-f1a3-dispatch/probe_pickle.txt`).
- **Where it could be pickled.** Nothing does today. In `gateway_v2`, `state_control` and `audit_control`:
  - There is no `pickle`, `multiprocessing`, `ProcessPoolExecutor`, `copyreg`, `shelve`, logging `QueueHandler`/`SocketHandler` or error-reporting SDK. The one `copy.deepcopy` (`edge/wire/mcp.py:256`) copies MCP JSON params, and a deepcopy of `OrgCredential` raises anyway.
  - uvicorn workers build their own app through the factory (`edge/serve.py:286-325`). No credential exists in the parent's dependencies.
- **Is the path live?** No. `OrgCredential` is imported only by `dispatch/vector_operations.py` and `dispatch/vector_leak_guard.py`, and no module imports either one, so no served request constructs it. Production runs the v1 gateway (R16), so `gateway_v2` serves no production traffic.
- **When it becomes live.**
  - GW36/F1a-3 wire per-org vector credentials into serving.
  - A later change pickles dependencies (a process pool, a spawn context passing objects, or a pickling cache or error reporter).
  - The exposure would then be the plaintext secret in transit, or at rest in that pickle.
- **Fix (queued).** Make `__reduce_ex__`, `__reduce__`, `__getstate__`, `__copy__` and `__deepcopy__` raise `TypeError`, with a test over every protocol. Correct the module comment. Align with F1a-3's `SealedCredential` so both use one helper.
