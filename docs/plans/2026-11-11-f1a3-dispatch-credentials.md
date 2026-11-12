# F1a-3: OpenAI-compatible dispatch and model credentials (design)

Status: 12 Nov 2026, **design revision 2**, for review before any code.
- Revision 1 (`c10c16e4`) was reviewed by Plan Reviewer A (review 5869433944, REQUEST CHANGES) and Plan Reviewer B (review 5869723316, REQUEST CHANGES). Every finding is answered in the **Rev 2 change log** below.
- Parent: `docs/plans/2026-11-11-f1-production-serving.md` (PR #103, branch `feat/f1a-production-serving`). §7 of the parent is the binding requirement list for this slice. **Approval of this revision is conditional on re-tracing §9 and §12 against parent revision 6 and F1a-2 revision 2** (§9.1), which are not yet written.
- Sibling: F1a-2 (MODEL state, PR #118, revision 2 pending). This plan depends only on the interface in §9.
- Base: `develop` at `562619c8`. Under `gateway_v2/`, `gateway/`, `shared/` and `control/` it differs from the reviewed `943a5f21` and from revision 1's base `25c33c86` only in `gateway_v2/gateway_v2/edge/scan_calibration.py` and tests, so every file:line below was re-checked at `562619c8`.
- Evidence: `docs/plans/evidence/2026-11-11-f1a3-dispatch/` (revision 1: prototypes, `RESULTS.md`) and its `rev2/` folder (revision 2: the paced benchmark, the full-path measurement, the transport probe, the advisory query, and the reviewers' harnesses and probes, credited; `rev2/RESULTS.md`).

Owner decisions this slice serves (parent §2): **D1** all v1 providers; F1a-3 ships the OpenAI-compatible family. **D2** envelope-encrypted keys in signed state, no control-plane call on the request path. **D3** live propagation within seconds. **D4** env-var keys refused in production. **D5** local first, hardware-independent; a KEK provider interface with a local KEK now and cloud KMS later.

**Owner decisions of 12 Nov that bind revision 2:**
- **v1 freeze.** v1 production (`gateway/`) is frozen until v2 is complete. Nothing may change how v1 derives or decrypts model keys: control's model key stays byte-identical to what v1 derives today; refusals live in control-only code, never in `shared/` modules v1 imports; control-key rotation is deferred until v1 is retired; rollout step 1 carries a v1 compatibility check (§2.5, §2.8, §8.4).
- **§11 defaults accepted**, except: **Q7** render the legacy `text_completion` shape on `/v1/completions` in F1a-3 (§3.3); **Q9** the per-org share of a shared provider origin applies **only under contention**, and an idle worker serves one org fully (§3.6.3); plus a per-org total in-flight rule (§3.6.3). **Q5** (aiohttp) is accepted on condition that it still wins under paced load (it does: §6.3), at a patched version (§3.13) with the B3/B4 mitigations (§3.13, §4).
- **What 100k RPS means** (master runbook §1.3): "100k RPS remains a future scale-out architecture test only. The code must not contain a design ceiling below it; the present $5k fleet is not expected to deliver 100k completed chats." The primary metrics are qualified RPS, CPU ms per request, qualified RPS per vCPU, RPS per $1,000-month and p99 firewall overhead (< 20 ms). A qualified request is defined in runbook §1.1. §6 is re-based on these definitions.
- **Heavy benchmarks run on GitHub runners** (Enterprise minutes). §6.8 specifies the CI job.

## Rev 2 change log

Every finding of both plan reviews, with its disposition. "Fixed" means the plan text changed in the section named; "argued" means the plan keeps its position, with the evidence named; "deferred" names the issue. Issues marked *new* must be opened by the coordinator (subagents cannot open issues; the hand-back lists them).

| Id | Sev | Finding (short) | Disposition | Where |
|---|---|---|---|---|
| A-B1 | BLOCKER | Rollout breaks v1's Fernet use (MultiFernet, `_FILE` key, rotation, refusal inside `shared/`) | **Fixed.** Control's model key stays byte-identical to v1's derivation; a control-only loader refuses to start if the `_FILE` key differs from what v1 derives; no MultiFernet; rotation deferred until v1 retires; the production refusal lives in control-only code; T-V1 plus a live v1 check in rollout step 1; rollback note for the projector's copy | §2.5, §2.8, §8.1 (T-V1, T-K1), §8.4 |
| A-B2 | BLOCKER | `upstream_slots()` = admission concurrency ≈ 15 per worker, held for the whole stream | **Fixed.** A separate per-worker stream bound `stream_slots()` derived from CPU per event, memory per stream and FDs; the admission request slot is handed off to a stream slot at dispatch; both bounds stated with numbers | §3.6.1, §3.6.2, §6.5, §10 A6 |
| A-B3 | BLOCKER | Timeout redesign violates `gap < first_event`; edge cuts at 60.18 s; idle 3.0 s unmentioned | **Fixed.** One timeout table: every bound with value, invariant, enforcing module and test; standard gap 20 s < first 30 s; per-request edge pre-content budget summed over the ranked candidates' classes; downstream keep-alive 1.0 s written through `StreamTimeouts.send` (below idle 3.0 s); boot check of the orderings | §3.7, §8.1 (T-P1 a–k) |
| A-S1 | SHOULD-FIX | No disconnect watcher on non-streaming requests | **Fixed.** Both modes run under `run_until_disconnect`; T-D6 non-stream case | §3.5, T-D6 |
| A-S2 | SHOULD-FIX | Usage and charging on aborted and failed streams | **Fixed.** Refund rule (pre-send, or any non-2xx status before content); estimate stands on abort; usage reconciles; per-attempt record under fallback | §3.5.2, T-D12 |
| A-S3 | SHOULD-FIX | DNS failure handling: single-flight, resolve timeout, failure at expiry | **Fixed.** Single-flight per origin, 2 s resolve bound, refresh-ahead, bounded serve-stale on resolver error (never on a refusal) | §4.2, T-D15 |
| A-S4 | SHOULD-FIX | Keep-alive reuse race with 5 s server idle | **Fixed.** Expiry per origin class (custom 1.5 s; default origins from the T-D4 capture, ≤ 15 s); the race outcome is defined (`upstream_unavailable`, no HTTP retry, counted) and asserted | §3.6.4, §3.8, T-D16 |
| A-S5 | SHOULD-FIX | Decoder: open delta-key vocabulary, non-string content, lone surrogates, BOM, reasoning keys | **Fixed.** Closed key set per profile, typed-content mapping, surrogate join-or-malformed, BOM strip, every vendor reasoning key normalised onto `output_reasoning` | §3.4, T-D3, T-D5 |
| A-S6 | SHOULD-FIX | Always streaming upstream fails models that refuse streaming | **Fixed.** `stream_upstream` settable per model (F1a-2 capability field) and a capture test for the 400 | §3.2, §9 I12, T-D4 |
| A-S7 | SHOULD-FIX | Reasoning budgets and parameters | **Fixed.** Reasoning-class default budget rule, `stop` drop verified by capture, `reasoning_effort` vocabulary as profile data | §3.2, T-D1, T-D4 |
| A-S8 | SHOULD-FIX | Acceptance items without a test or artifact | **Fixed.** A test per item (T-K1…T-K3, T-E1, T-H1, T-O1…T-O4) and the L36-1 artifact list; step 3 asserts a zero delta on upstream attempts | §8.1, §8.3 |
| A-S9 | SHOULD-FIX | T-D4 vendor captures undefined | **Fixed** in policy (who, accounts, scrubbing, refresh, merge vs GA gate). The vendor accounts are an owner item | §8.2, §11.2 |
| A-S10 | SHOULD-FIX | Dependencies on #118 and #103 revisions | **Fixed.** Approval is conditional on re-tracing §9 and §12 against both revisions | header, §9.1 |
| A-S11 | SHOULD-FIX | Quota code not fallback-eligible; exhausted 503 drops `retry-after` | **Fixed.** `upstream_quota_exceeded` is fallback-eligible; `routing_attempts_exhausted` carries the last cause's retry headers | §3.8, §3.10 |
| A-S12 | SHOULD-FIX | Wrong file:line citations | **Fixed.** `url_guard.py:63-88,129,136-147`; `routing_trace.py:56-68`; `chat_upstream.py:78-84`; `errors.py:434-445,494` | §1 C5, C14 |
| A-N1 | NIT | C6 is latent; cite the live non-ASCII key case | **Fixed** | §1 C6 |
| A-N2 | NIT | R17 latent; file it | **Fixed.** Linked to #128 | §2.7 |
| A-N3 | NIT | Benchmark coalescing: report reads per request, add a paced row | **Fixed.** §6 is re-based on paced streams, with reads per request | §6.3 |
| A-N4 | NIT | SSE error-frame rows for new codes | **Fixed** | §3.10 |
| A-N5 | NIT | Case-sensitive `content-type` prefix | **Fixed.** Media type parsed case-insensitively | §3.9 |
| A-N6 | NIT | Gateway-requested usage chunk must not reach a client that did not ask | **Fixed.** T-D3 asserts both cases | §3.2, T-D3 |
| A-N7 | NIT | `n` clamp and `logprobs` refusal unstated | **Fixed** | §3.2 |
| A-N8 | NIT | 299.94 s total and no per-org CA bundle unstated | **Fixed.** Stated as limits | §3.7, §4.3 |
| A-Q1 | note | KEK destroy loses nothing; KEK rotation is a rolling restart | **Fixed** | §2.8 |
| A-Q4, B-S9 | SHOULD-FIX | Q4 grant via `is_superuser`; add audit and expiry | **Fixed.** Dedicated permission, audit row, optional expiry; T-E1 | §2.5, §4.3 |
| A-Q5 | note | Q5 conditions: hashed pins, C parser, resolver prototype, DNS-cache bypass proof, happy eyeballs only over checked addresses | **Fixed.** Prototyped on aiohttp 3.14.5 (`rev2/probes/aiohttp_transport_guard.txt`) | §3.13, §4.2 |
| A-Q6, B-N7 | note | Reasoning class: add the capability field now; custom models named `o1-x` get reasoning timeouts | **Fixed.** Name pattern for default-origin profiles only; custom needs the explicit field | §3.2, §9 I12 |
| A-Q7 | note | Q7 not sound as phrased | **Fixed** by owner decision: render `text_completion` | §3.3 |
| A-Q9, B-S8 | SHOULD-FIX | Q9 share ½ refuses when idle; no per-org total cap | **Fixed** by owner decision: contention-gated max-min share; worker-level stream pool is the per-org total rule | §3.6.3 |
| A-Q11 | note | Quota message must say quota | **Fixed** | §3.10 |
| B-B1 | BLOCKER | Scale claims rest on a burst workload | **Fixed.** §6 re-based on paced streams with an event-count distribution, reads per request, a linear per-request/per-event model, the reconciliation of A's and B's methods, the $5k fleet, the 100k RPS ceiling table and a CI job. The §8 benchmark criterion is on the realistic workload. Also measured: the whole qualified path on develop, which is dominated by a quadratic output rescan outside this slice (new issue 4) | §6, §8.5 |
| B-B2 | BLOCKER | Same as A-B2 | **Fixed** | §3.6.1, §6.5 |
| B-B3 | BLOCKER | Compressed upstream bodies: cross-tenant memory DoS; aiohttp CVEs | **Fixed.** `Accept-Encoding: identity`, `auto_decompress=False`, any other `Content-Encoding` refused before the body is read, header and line bounds; aiohttp pinned **≥ 3.14.4** (3.13.3 still carries 56 OSV entries, including two HIGH in the client response parser) | §3.9, §3.13, §2.11, T-S6 |
| B-B4 | BLOCKER | aiohttp skips the resolver for IP literals; redirects on by default | **Fixed.** A `socket_factory` dial guard on every dial (literals included), a literal check at gateway apply, a single transport call site that hard-codes `allow_redirects=False`, and an AST gate; probed | §3.13, §4.2, §5, T-S2, T-S3, T-S7 |
| B-B5 | BLOCKER | v1 constraint violated by §2.5, §2.8 and rollout step 1 | **Fixed.** Same as A-B1; `GatewayAPIKey` split stated v1-safe with evidence | §2.5, §2.8, §8.4 |
| B-S1 | SHOULD-FIX | Forged keyless entries redirect prompts | **Fixed.** A2 enforced at gateway apply and at connect (keyless ⇒ private address, `egress` entry, CIDR fence); §2.11 row corrected | §4.3, §5, §2.11 |
| B-S2 | SHOULD-FIX | KEK rotation gate is circular | **Fixed.** Required kids published in signed state; readiness fails on a missing kid; the gate checks a registered gateway set | §2.8, T-C5 |
| B-S3 | SHOULD-FIX | Unbounded connection lifetime; DNS single-flight and stale policy | **Fixed.** Max connection age 60 s plus a pool generation bump on any change to an origin's checked set; DNS as A-S3 | §3.6.4, §4.2 |
| B-S4 | SHOULD-FIX | Per-worker view memory unstated; AAD precompute costs 100 MiB per worker | **Fixed.** AAD computed per request (1.65 µs); view RSS stated in the memory budget | §2.6, §6.5 |
| B-S5 | SHOULD-FIX | Egress sockets, ports, NAT, h2 | **Fixed.** Per-host and per-NAT-IP sizing; h2 foreclosed by aiohttp, recorded with its HOL-blocking condition | §6.7, §3.13 |
| B-S6 | SHOULD-FIX | Console shows omitted models as active | **Fixed.** Save-time validation and a served status per row; L36-1 step 8 | §2.5, §3.12, §8.3 |
| B-S7 | SHOULD-FIX | Counting semantics undefined | **Fixed.** Per-attempt series, `routing.attempts[]`, provider request id | §3.12 |
| B-S8 | SHOULD-FIX | Q9 share too generous; no per-org total | See A-Q9 | §3.6.3 |
| B-S10 | SHOULD-FIX | Unrestricted custom ports: port-scan oracle | **Fixed.** Custom public ports default to {443}; others by staff grant | §4.1, §4.3 |
| B-S11 | SHOULD-FIX | Revocation bound depends on F1a-2's open cold-fetch bound | **Fixed.** Stated as conditional; L36-1 step 9 revokes on a cold worker | §2.9, §8.3 |
| B-N1 | NIT | Keep-alive expiry below 5 s for custom vLLM | **Fixed** (A-S4) | §3.6.4 |
| B-N2 | NIT | Gateway ACL can write any heartbeat | **Fixed.** Per-host Valkey user for its own heartbeat key | §2.8, §3.14 |
| B-N3 | NIT | R17 not linked | **Fixed.** #128 | §2.7 |
| B-N4 | NIT | Teredo comment says 127.0.0.1 | **Fixed** in `ssrf_probe.py` (127.0.1.1) | evidence |
| B-N5 | NIT | KEK file mode under compose bind mounts | **Fixed.** Host-side mode and owner, boot refusal with a remedy line | §2.13 |
| B-N6 | NIT | Audit row should carry gateway id and kid/dek_id | **Fixed** | §3.12 |
| B-Q2 | note | Also refuse when env and file KEK are both set | **Fixed** | §2.13 |
| B-Q8 | note | KEK per cell should be a pre-GA gate | **Open owner decision** (default proposed) | §11.2 |
| B-Q12 | note | Hosts unrestricted, ports restricted | **Fixed** (B-S10) | §4.1 |

New issues needed (coordinator to open): (1) control model-key rotation to a random key after v1 retirement (MultiFernet, re-seal backlog); (2) `GatewayAPIKey` key-split contract phase (drop the old key after both releases); (3) Q8 KEK-per-cell decision before GA, if the owner makes it a gate; (4) **perf(egress): the streaming output detector re-scans the whole accumulated output on every delta** (`egress/stream.py:547-548`, `edge/chat_stream.py:450`): measured ≈ 0.66·E + 0.0126·E² ms per streamed request on develop, 90–96% of a 200–600-event qualified request (§6.4, `rev2/devpath/`). #125 (projector CPU budget) and #126 (validation cost) also bound the sealing round (§2.5).

## 0. Summary

1. **Credentials.** A three-level envelope: a fleet **KEK** (from a KEK provider: local `_FILE` now, cloud KMS later) wraps a random per-org **DEK**. The DEK seals each model credential with AES-256-GCM, a fresh 96-bit random nonce, and an AAD that binds the credential to its namespace, org, row, alias, provider, base URL, upstream model, DEK and credential version.
   - The sealed DEK and the sealed credentials travel inside the org's signed MODEL record (F1a-2's kind). A gateway opens a credential per request (0.55 µs with a cached cipher, 2.1–2.4 µs cold, plus 1.65 µs of AAD encoding) and never caches plaintext.
   - Control's Fernet layer is **unchanged in key bytes** while v1 lives (owner freeze): v1 keeps decrypting every token.
2. **Dispatch.**
   - One aiohttp connection pool per origin `(scheme, host, port)` behind a single transport call site: `trust_env=False`, no redirects, no default auth header, `Accept-Encoding: identity` with encoded bodies refused, resolve-then-pin through a custom resolver, and a dial guard that checks **every** dialled address, IP literals included.
   - Per-provider request shaping from a closed profile table; an upstream SSE decoder with a closed key vocabulary; a codes table where every code has a status, a type, a failure class, a console stage and an SSE error frame.
   - A per-worker **stream bound** separate from pipeline admission, with contention-gated per-org fairness.
   - One timeout table with checked orderings.
3. **Scaling** (§6), on paced streams. On aiohttp the hardened transport plus decoder costs 28–314 µs per request plus 32–48 µs per event (7.3–11.2 ms of CPU per streamed request at the planning mean of 227 events); httpx costs 2.4–19 ms per request plus 85–115 µs per event (28.5 ms at the mean with 64 in flight; at 256 in flight it saturates a core). aiohttp wins by 2.5–3.5× at 64 in flight and 5.7× or more at 256 in flight depending on events per request and per-worker event rate (the honest range; the 9–35× of revision 1 was a burst artefact). With F1a-3's transport, dispatch never binds the $5k fleet (the guard tier does, at 400 RPS), and nothing in this slice caps below 100k RPS; at 100k RPS the dispatch scope alone needs 970–1,487 vCPU ($32k–$49k a month), outside the $5k envelope, as runbook §1.3 expects. **The full qualified path measured on develop is different:** ≈ 1.31 s of CPU per streamed request at the planning distribution, 70–96% of it the egress loop re-running the output detector over the whole accumulated output on every delta (quadratic in events, `egress/stream.py:547-548`). That is outside this slice; it would hold the $5k fleet to about 18 qualified streamed RPS; a new issue is requested and the CI job gates it (§6.4–§6.6).
4. **Defects at develop that this design closes** (§1): C3 (proxy env honoured), C4 (one connection per worker), C5 (`100.64.0.0/10` passes; DNS per request), C6 (refusal rendered as internal error; latent), C7, C8, C13 (0.8 s / 1.0 s mid-stream bounds), and C19–C21 found by the reviews (admission bound held for the whole stream; idle and pre-content bounds; compressed bodies).

## 1. Current behaviour (develop `562619c8`)

| # | Finding | Evidence |
|---|---|---|
| C1 | Production serves no completions. `provider` is an empty `StubProviderClient`, and `apply_production` never replaces it | `edge/serve.py:175-176,233,247`; `edge/production_profile.py:55-56,518-540` |
| C2 | `OpenAIUpstreamProvider` is one client per instance; its `api_key` becomes a **default** `Authorization` header and its `base_url` is fixed, so per-org keys and origins cannot be expressed | `dispatch/openai_upstream.py:96-110,146-159` |
| C3 | The shipped client factory leaves `trust_env=True`. With `HTTP(S)_PROXY` set, a provider request, bearer included, goes to the proxy; for an `http://` base (private ollama) the bearer crosses the proxy hop **in clear** (Reviewer A reproduced: `POST http://10.255.255.1:11434/v1/chat/completions` with `authorization: Bearer <key>`). No SSRF check on the chat path | `dispatch/openai_upstream.py:103-108`; `dispatch/pinned_transport.py:1-8`; `rev2/probes/from_reviewer_a/test_repro_develop.py` (`test_c3_*`) |
| C4 | **One upstream connection per worker** (`pool_size(PROVIDER)` = `workers()` = 1 here): 4 streams held 1 s each finish at 1.35 / 2.36 / 3.37 / 4.38 s (A: 1.12 / 2.13 / 3.13 / 4.14 s) | `runtime/resources.py:349-356`; `edge/dev_profile.py:302-311`; `probe_pool.txt` |
| C5 | **SSRF gaps.** `PinnedTransport` resolves on **every** request; `resolve_checked` does not apply `is_global`, so `100.64.0.1` is allowed, and so is a name resolving to `100.100.100.200` (Alibaba ECS metadata; A). An allowlisted host takes the `block_private=False` branch, where 6to4 and Teredo embeddings pass | `dispatch/pinned_transport.py:39-52`; `dispatch/url_guard.py:63-88` (`_blocked`), `:129` (allowlist branch), `:136-147` (literal and resolved checks); `ssrf_probe_py312.json` |
| C6 | `PinnedRefused` is neither `httpx.HTTPError` nor `OSError`, so it would end as `routing_internal_error`. **Latent:** the chat path has no `PinnedTransport` at develop. The **live** instance of the class: a non-ASCII provider key raises `UnicodeEncodeError`, which escapes the provider and ends as `routing_internal_error` (A, `n1_nonascii_key.py`). The charset check (§2.5) and the in-dispatcher catch (§3.9) close both | `dispatch/pinned_transport.py:26`; `dispatch/openai_upstream.py:211-214`; `dispatch/routed_provider.py:295-298`; `rev2/probes/from_reviewer_a/n1_nonascii_key.py` |
| C7 | A routing refusal loses its candidate (`cand=None`); the console projects the last decision and shows `-` | `dispatch/routed_provider.py:302-313`; `audit/console_projection.py:49-50` |
| C8 | **Decoder gaps.** CRLF framing yields 0 frames; `: ping`, `: OPENROUTER PROCESSING` and a bare `:` set malformed; every string-valued `delta` key becomes a channel (5,000 distinct keys accepted, A); non-string `content` is dropped silently; a lone `\ud83d` passes the decoder and raises `UnicodeEncodeError` at `asgi_sse.py:276`; a leading BOM is malformed | `edge/wire/sse_decode.py:18,132-148,189-211,251-306`; `edge/asgi_sse.py:276`; A `test_c8_*`, `test_lens5_*` |
| C9 | `/v1/completions` `prompt` is folded and scanned as `completion_prompt`, but the route reuses the chat handler and returns a chat-shaped body, which SDK `completions.create()` cannot read (`choices[0].text`) | `edge/wire/openai_fold.py:147-149`; `edge/routes.py:499-500` |
| C10 | The upstream is always asked to stream; `stream_options` is never forwarded, so no upstream `usage` reaches charging | `edge/upstream_body.py:149`; `edge/wire/openai_params.py:61-79` |
| C11 | One forwarded set for every provider (`seed`, `logit_bias`, `reasoning_effort`, the `developer` role); `reasoning_effort` ∈ {minimal, low, medium, high} only | `edge/wire/openai_params.py:55,61` |
| C12 | Embeddings end `upstream_request_unbuildable` (500) after `mark_dispatched` | `dispatch/openai_upstream.py:192-196`; `edge/openai_misc.py:627-631` |
| C13 | **Mid-stream bounds:** first event 30 s, gap 0.8 s, total 299.94 s; the edge cuts after 1.0 s with no upstream event | `runtime/resources_routing.py:37-54`; `edge/stream_timeouts.py:175-191` |
| C14 | **Observability gaps.** The `routing_decision` log line has no request id, although the decision's `request_key` **is** the minted request id on the chat path (`holds.request_id`); `pipeline_trace` allows only `final_action`, `plan_version`, `stages`; no upstream series; only incident codes get retry headers | `edge/routing_trace.py:56-68`; `edge/chat_upstream.py:78-84`; `audit/console_schema.py:51`; `edge/errors.py:434-445,494` |
| C15 | **Credentials today.** Control Fernet-encrypts model keys with `ZEROSHIELD_MODEL_KEY_ENCRYPTION_KEY`, else a key derived as `base64url(sha256(SECRET_KEY))`; a malformed configured key is hashed into a valid one; recoverable `GatewayAPIKey` secrets use the same cipher; an undecryptable key becomes `""` plus a warning. v1 pushes the tokens into unsigned Redis (`llm:model_configs:{org}`) and decrypts them at reload through the **shared** builder with `DJANGO_SECRET_KEY` as the fallback, keeping plaintext inside LiteLLM. Production runs on the derived key (`ZEROSHIELD_MODEL_KEY_ENCRYPTION_KEY` unset; `.env.ec2.sample` sets only `DJANGO_SECRET_KEY`; compose passes the same `DJANGO_SECRET_KEY` to `control` and `gateway`). v1 retries twice, drops unsupported params, times out at 120 s, applies no SSRF check; control does not validate `api_base` | `control/ai_mesh_control/core/models.py:486-498,1565-1568,1709-1743`; `shared/ai_mesh_shared/llm_model_crypto.py:26-36,55-72`; `gateway/ai_mesh_gateway/llm_router.py:23,911-929`; `docs/plans/2026-08-18-deterministic-model-routing.md:821`; `docker-compose.yml:136,249` |
| C16 | Signed state is HMAC-SHA256 under `AMF_STATE_HMAC_KEY` from the environment, held by the projector, the re-hydrators and every gateway | `edge/state_runtime.py:128-151`; `runtime/state_sig.py:65-91` |
| C17 | No production gateway service in compose; shared `redis` publishes 6379 with no auth; `AMF_FD_LIMIT` never set | `docker-compose.yml:491-541,577`; `runtime/cgroup.py:242-248` |
| C18 | `cryptography` is not a gateway_v2 dependency | `gateway_v2/pyproject.toml:10-13` |
| C19 | **Admission bounds stream concurrency (reviews A-B2, B-B2).** `AdmissionBounds.concurrency` = `queue_depth(q_safe)` = `min(ceil(q_safe × p99 / util), floor(memory × util / per_worker_rss))`: sized for a 20 ms service time and for the number of **workers** that fit in memory. The request slot is held until `holds.release` at the end of `handle`, i.e. for the whole stream. Measured: 14–15 for every `q_safe` ≥ 500 on this host; a live controller admits 14 and sheds the 15th (`QUEUE_FULL`) | `admit/quota.py:119-137`; `runtime/resources.py:332-342`; `edge/routes.py:309-326`; `rev2/probes/from_reviewer_b/probe_admission_concurrency.txt` |
| C20 | **Stream bounds the revision-1 timeouts ignored (A-B3).** `StallBounds` requires `0 < gap < first_event < total`. The edge's routed first-chunk wait is `routing_pre_content_budget_s(2)` = 2 × (30 + 0.06) + 0.06 = **60.18 s**. `idle_timeout_s()` = **3.0 s** since the last completed downstream write; in the default (non-liveness) mode a 5 s upstream pause is cut with `stream_idle_timeout` at the next write | `dispatch/stall_guard.py:37-48`; `edge/chat_stream.py:246-256`; `runtime/resources_routing.py:56-61`; `runtime/resources_stream.py:35-65`; `edge/stream_timeouts.py:220-244` |
| C21 | **Compressed upstream bodies (B-B3).** httpx and aiohttp send `Accept-Encoding: gzip, deflate` by default and inflate before the caller sees a byte; a 200 KiB gzip body inflated to 201 MiB (largest single chunk handed up: 64.3 MiB httpx, 125.5 MiB aiohttp) | `rev2/probes/from_reviewer_b/decompress_amplification.txt` |

What is already right and is kept: the per-request iterator close (`openai_upstream.py:23-27,174-176`; `routed_provider.py:205-207`); the deferred SSE head, so a pre-content refusal is a real HTTP error (`edge/stream_response.py:1-10`); the GW28 single-open-site routing loop with pre-content fallback (`routed_provider.py:243-300`); verified-delta rendering (`edge/upstream_body.py:54-111`).

## 2. Credentials (§7.1, §7.2, D2, D4, D5)

### 2.1 Key hierarchy

```
KEK provider (local _FILE now; cloud KMS later)          held by: projector, gateways
  └─ KEK  kid=k2026q4   AES-256, fleet-wide, rotated on a fixed cadence
       └─ per-org DEK   dek_id=d20261111-9f3a5c1e  AES-256, random, one live per org
            ├─ K_cred = HKDF-SHA256(DEK, info="amf.cred.key.v1")
            │    └─ credential  {v, n, ct, s}  AES-256-GCM(K_cred, n, key, AAD_cred)
            └─ K_memo = HKDF-SHA256(DEK, info="amf.memo.v1")  (projector memo digest only)
```

Why three levels rather than the single `kid` level of §7.1:
- **KEK rotation re-wraps one DEK per org**, not every credential: 100k wraps instead of 500k encryptions, with no AAD change to the credentials.
- **A cloud KMS fits without a request-path or per-org call.** The KMS unwraps the KEK once per process.
- **Cryptographic tenant separation in addition to AAD.**
- **Later options:** a customer-managed key per org, or KEK-per-cell sharding (Q8, §11.2).
- **The nonce budget is trivial** (NIST SP 800-38D §8.3).

§7.1's rules map onto the two levels in §2.3 (§10, amendment A1).

### 2.2 Algorithms

- **AEAD.** AES-256-GCM (`cryptography` `AESGCM`), 128-bit tag, 96-bit nonce from `os.urandom(12)` for every encryption, at both levels.
- **Dependency.** `cryptography` becomes a gateway_v2 base dependency, pinned exactly with hashes in the image install. **Floor ≥ 50.0.2**: `46.0.3`, which revision 1 benchmarked, carries 13 OSV entries (7 GHSA, including vulnerable bundled OpenSSL) and `50.0.2` none, on 12 Nov 2026 (`rev2/probes/osv_2026-11-12.jsonl`). The AEAD figures of §2.12 were measured on 46.0.3; a short unlocked re-run on 50.0.2 (Python 3.12.3, 5k × 3) gives the same order: `dec_hot` 659–664 ns, `cold` 2.4–4.3 µs, AAD 1.87 µs, Fernet decrypt 8.9–10.0 µs (`rev2/probes/aead_cryptography_50.0.2.txt`). The CI benchmark (§6.8) re-measures them on the pin.
- **Library check at boot.** One AES-GCM known-answer test (a NIST vector). A failure refuses to start.

### 2.3 AAD and the two envelopes

AAD encoding: `enc(domain, f1, …, fn)` = `u32be(len(domain)) ‖ domain ‖ Σ u32be(len(fi)) ‖ utf8(fi)`. Every field is a `str` taken **byte-for-byte from the signed record body**; length-prefixing makes the encoding injective.

| Level | AAD | Rules from §7.1 that apply here |
|---|---|---|
| DEK wrap (KEK) | `enc("amf.dek.v1", namespace, org, dek_id, kid)` | Fresh nonce; explicit encrypt `kid` from signed state (§2.8), no "newest" heuristic; switching it is gated (§2.8); a kid is retired only when nothing uses it and every registered gateway has applied past the re-wrap; fixed cadence |
| Credential (DEK) | `enc("amf.cred.v1", namespace, org, row_id, alias, provider, base_url, upstream_model, dek_id, str(v))` | Fresh nonce; the sealed tuple `{v, n, ct, s}` is reused **only** when the plaintext and the AAD are byte-identical (§2.5) |

Why each field is in the AAD: `org`, `row_id`, `alias` (no move across tenants or rows); `provider`, `base_url` (a holder of the HMAC key but not of a KEK cannot redirect a **sealed** credential; keyless entries are covered separately in §4.3); `upstream_model`; `dek_id` (binds the kid transitively); `v` (no older ciphertext under a newer version). The AAD cannot stop a whole self-consistent old record from being replayed: signed versions and manifest anti-regress do (`runtime/state_sig.py:6-13`; F1a-2 §6.2), and §2.9 states the cold-fetch residual.

### 2.4 What the signed MODEL record carries

```json
{
  "dek": {"kid": "k2026q4", "id": "d20261111-9f3a5c1e", "n": "<12 B>", "w": "<48 B>"},
  "models": [
    {"row": "4127", "alias": "prod-chat", "provider": "openai",
     "base_url": "https://api.openai.com/v1", "model": "gpt-4o-2024-08-06",
     "caps": {"reasoning": false, "stream_upstream": true},
     "cred": {"v": 3, "n": "<12 B>", "ct": "<len(key)+16 B>", "s": "<16 B>"}},
    {"row": "4130", "alias": "local-llama", "provider": "ollama",
     "base_url": "http://10.20.0.7:11434/v1", "model": "llama3.1:8b", "cred": null},
    {"alias": "legacy", "omitted": "credential_env_var"}
  ],
  "egress": [{"kind": "private_target", "scheme": "http", "host": "10.20.0.7", "port": 11434}]
}
```

- `s` is the projector's memo digest `HMAC-SHA256(K_memo, fernet_token)[:16]`; it lets a restarted projector skip unchanged rows for 1.9 µs instead of 12–15 µs for a Fernet decrypt.
- `caps` is F1a-2's optional capability field (§9 I12): `reasoning` (timeout and budget class) and `stream_upstream`.
- `egress` lists the org's staff-granted exceptions (§4.3): private targets and non-443 public ports.
- Nothing else about a credential is in state: no last-4, no length, no env-var name.
- The KEK kids every gateway must hold are **not** in the org record: they are in the projector's signed fleet record (§2.8).

### 2.5 What control stores, and the projector's sealing round (v1-safe)

**Control, model key (owner freeze).** `LLMModelConfig.encrypted_api_key` stays the Fernet token and **its key bytes do not change while v1 serves**:
- A new **control-only** module, `control/ai_mesh_control/core/model_key.py`, builds control's model cipher. `shared/ai_mesh_shared/llm_model_crypto.py`, which v1 imports (`gateway/ai_mesh_gateway/llm_router.py:23`), is **not modified**.
- In `AMF_PROFILE=production`, `model_key.py`:
  - reads the key from `ZEROSHIELD_MODEL_KEY_ENCRYPTION_KEY_FILE` (a 44-byte url-safe base64 Fernet key, file mode 0400);
  - refuses to start if the file is missing or malformed (no hashing of a malformed key, §7.1's last bullet), or if the plain env form is the only source;
  - computes the key **v1 derives today** with the unchanged shared builder and v1's inputs (`build_llm_model_key_cipher(configured_key=<env ZEROSHIELD_MODEL_KEY_ENCRYPTION_KEY>, fallback_secret=<DJANGO_SECRET_KEY>)`, `llm_model_crypto.py:26-36`; compose passes the same `DJANGO_SECRET_KEY` to `control` and `gateway`, `docker-compose.yml:136,249`) and **refuses to start unless the file key is byte-identical**. The refusal names the remedy and never prints key material, only an 8-hex fingerprint of `sha256(key)`.
  - builds a single `Fernet`, not a `MultiFernet`: there is no second key while v1 lives.
- So the runtime **hash derivation from `SECRET_KEY` is refused** in production (the key must be supplied explicitly), while the **value** stays the bytes v1 derives. Moving to a random key is deferred until v1 retires (new issue 1; §10 A8).
- Outside production, `model_key.py` keeps today's behaviour (env, then derivation) so dev and tests are unchanged.

**Control, `GatewayAPIKey` key split (§7.1).** v1 never reads `encrypted_secret` (it authenticates on hashes in `auth:apikey:{hash}`; `grep -rn encrypted_secret gateway/ shared/` returns nothing), so the split is v1-safe. It is an expand/contract migration so that **every** release can be rolled back:
1. **Expand** (rollout step 1): `ZEROSHIELD_GATEWAY_KEY_ENCRYPTION_KEY_FILE` is loaded (control-only); reads try the new key, then the model key; **writes still use the model key**. Rolling back to the previous image is safe because no row was written with the new key.
2. **Switch** (a later control release): writes use the new key; a bounded backlog re-encrypts existing rows. Rolling back to step 1's image is safe because it reads both keys.
3. **Contract** (new issue 2): drop the model-key fallback for `GatewayAPIKey` once the backlog reports zero rows on the old key.

**Control, egress grants.** New table `OrgEgressGrant(org, kind ∈ {private_target, public_port}, scheme, host, port, reason, granted_by, granted_at, expires_at NULL)`:
- editable only by holders of a dedicated permission `core.grant_org_egress`, **not** `is_superuser` (given #120); an org admin can never grant;
- every create, change and delete writes an audit row (who, when, why, previous value);
- an expired grant is not projected.

**Control, save-time validation (B-S6).** Saving an `LLMModelConfig` runs the same checks the projector applies (base-URL grammar of §4.1, key charset, provider supported by the v2 build, alias collision, custom port grants) and stores the result in a control-only `v2_serving_status` column (`served` or `not_served:<reason>`). The projector overwrites it with the reason it actually used each round, so the console shows what the gateway serves (§3.12). The grammar has one corpus (T-S4) run against both the control and the gateway implementations.

**Who holds which secret:**

| Secret | control | projector | re-hydrator | gateway | v1 gateway |
|---|---|---|---|---|---|
| model key (Fernet, byte-identical to v1's derivation) | yes (`_FILE`) | yes (`_FILE`, decrypt only) | no | no | derives it from `DJANGO_SECRET_KEY` (unchanged) |
| `GatewayAPIKey` key (`_FILE`) | yes | no | no | no | no |
| KEK(s) (`AMF_CRED_KEKS_FILE`) | no | yes | no | yes | no |
| state HMAC (`AMF_STATE_HMAC_KEY_FILE`) | no | yes | yes | yes | no |
| Valkey user | — | `projector` (state write) | `rehydrator` (state write) | `gateway` (state read-only) + `gw-<host>` (own heartbeat key only) | — |

**The sealing round** runs inside F1a-2's projector round (`state_control/cred_seal.py`). For each active row:
1. **Omission rules** (reason codes in §3.10): `api_key_env_var` set → `credential_env_var` (D4); inactive → absent; keyless → `credential_missing`, except a keyless entry covered by a `private_target` grant (A2); a key failing the charset check (printable ASCII `0x21–0x7E`, 8–4096 bytes) → `credential_invalid_format`; a base URL failing §4 → `base_url_refused`; a non-443 public port without a grant → `base_url_refused`; a provider outside this build → `provider_unsupported`.
2. **DEK.** Reuse the org's live DEK, unwrapped from its last published record; on the first credential generate `os.urandom(32)` with a fresh `dek_id`. Rotate on the DEK cadence (Q1) as a bounded backlog after the heartbeat.
3. **Reuse rule (§7.1).** Reuse `{v, n, ct, s}` only if `s` matches the current Fernet token **and** every AAD field is byte-identical. Otherwise Fernet-decrypt (≈14 µs), check the charset, set `v ← v+1`, encrypt with a fresh nonce and recompute `s`. The plaintext is a local of one function and never logged.
4. **Order (parent §6.6).** Removals and real per-row key changes are written **before** the heartbeat; re-wraps and DEK rotations are a bounded backlog after it. The per-org seal work counts against the projector round's per-tenant CPU budget (#125), so one org with many rows cannot stall the round.
5. **Hardening.** Every secret from `_FILE`; `RLIMIT_CORE=0`, `PR_SET_DUMPABLE=0` (parent §5.6); non-root; read-only root filesystem; env forms of `_FILE` secrets refused in production (T-H1).

### 2.6 Gateway: where plaintext lives

Apply path (F1a-2's applier calls `CredentialOpener.check_org(record)`): unwrap the DEK under its kid; decrypt each sealed credential once and discard it; keep `{n, ct}` decoded from base64. **The AAD is not precomputed** (B-S4): precomputing saves 1.65 µs per request (≈0.17 cores fleet-wide at 100k RPS) but costs ≈100 MiB per worker at 100k orgs × 5 models (341 MiB with it against 240 MiB without, `rev2/probes/from_reviewer_b/view_memory.txt`), and every worker holds its own view.

A cold apply of 100k orgs × 5 models costs ≈100k × 2.1 µs + 500k × 0.6 µs ≈ 0.5 s per worker. On failure: unknown kid or DEK tag failure → the org-level `unavailable(version)` marker; one entry's tag failure → that entry `omitted: credential_unreadable`; both counted in `amf_cred_open_failures_total{reason}`. An unknown kid also fails readiness (§2.8).

Request path, `open(view, entry) -> bytes`:
1. Get `AESGCM(K_cred)` from a **bounded LRU keyed `(org, dek_id)`** (2.4 KB per entry; `cred_cipher_cache_entries()` ≈ 4096, ≈10 MB per worker). A miss adds one HKDF.
2. Encode the AAD (1.65 µs) and decrypt into the `Authorization` header bytes.

The plaintext exists in one `bytes` value for the duration of `open` and in the request's header list until the transport has written the request; **never** in a cache, a log, an exception or a trace. Process hardening as parent §5.6, no swap (`memswap_limit` = `mem_limit`), non-root, read-only root, `cap_drop: ALL`, `no-new-privileges`. A per-org plaintext cache is rejected: it would save ≈0.5 µs per request and keep every served org's key resident.

### 2.7 Leak controls (§7.2)

- **No dataclass protocol.** `SealedCredential` and `SealedDek` are plain slotted classes with redacted `__repr__`, no `__eq__` over secret fields, and `__reduce__`, `__getstate__` and `__copy__` raising `TypeError` (deleting the dataclass hooks is not enough on 3.11+, because `object.__getstate__` pickles slots). The same gap in GW30's `OrgCredential` (R17) is **latent** (gateway_v2 has no pickle, multiprocessing or copyreg use) and is tracked in **#128**; this slice does not fix it.
- **AST gates** (`lint/check_credential_types.py`): ban `asdict`, `astuple`, `vars`, `__dict__`, `copy.copy`, `pickle` and `json.dumps` on those types and on the opener's return value; `lint/check_credential_flow.py` covers every module whose name contains `cred` plus `dispatch/upstream_*.py`.
- **Errors carry names only:** exception type names, never `str(exc)`, URLs with query, headers or response bodies. Provider error bodies are read bounded (§3.9) to classify only.
- **Planted-key grep (T-L1)** over gateway, projector and re-hydrator logs, `/metrics`, SSE and HTTP bodies, `amf.trace` frames, audit and console rows, boot logs and every §3.10 error envelope: the key appears **only** in the recorder's received `Authorization` header.

### 2.8 Rotation without downtime

| What | Steps | Why no downtime |
|---|---|---|
| **KEK** (cadence Q1: 90 days) | <ol><li>Add `k_new` to every gateway's KEK file and roll the fleet (the kid set is fixed per process, so a KEK change is always a rolling restart). Each gateway reports `{kids, model_feed_seq}` in its heartbeat `{ns}:fleet:kids:<gateway_id>`, written with its **own** per-host Valkey user, whose ACL allows that one key (B-N2).</li><li>**Gate (B-S2).** The projector switches the encrypt kid only when **every registered gateway** reports `k_new`. The registered set is `{ns}:fleet:registry`, written by deploy tooling (add on provision, remove only on decommission), not inferred from live heartbeats: a partitioned or booting gateway that is registered but silent **blocks** the switch.</li><li>The switch is published in the projector's **signed fleet record** `{encrypt_kid, required_kids}`, not in deployment config.</li><li>Re-wrap every org's DEK under `k_new` as a bounded backlog; credentials are untouched.</li><li>Retire `k_old` when no published record names it **and** every registered gateway reports an applied `model_feed_seq` at or past the last re-wrap. Remove it from the gateways, then the projector, then destroy it.</li></ol> | A gateway compares its held kids with the signed `required_kids`: if any is missing it **fails readiness** (and counts `amf_cred_open_failures_total{reason="unknown_kid"}`), so the load balancer drains it instead of it serving 503s for re-wrapped orgs. Destroying a retired KEK loses no customer key: state is derived from control and the projector re-seals. |
| **DEK** (cadence Q1: 30 days, staggered by `hash(org)`) | New DEK; re-encrypt that org's ≤ N credentials; one new record version | Atomic per org |
| **Control model key** | **Deferred until v1 is retired** (owner, 12 Nov; new issue 1). While v1 serves, the key bytes are v1's derivation and there is no `MultiFernet` | v1 keeps decrypting every token |
| **Customer rotates their provider key** | Console edit → new Fernet token → `v+1` → published | Bounded by §2.9 |

### 2.9 Revocation latency

A console edit that removes, disables or replaces a key, or that revokes an egress grant, reaches a **warm** gateway within projector `lag_ms` + `fresh_ms` = **21 s** with the defaults (parent §6.10); normally one round plus a nudge, 1–2 s.
- **The bound is conditional (B-S11)** on F1a-2 closing or bounding the cold worker's single-record fetch (F1a-2 §6.2, still under review in #118). Until #118 revision 2 states it, this plan claims 21 s for warm workers only. L36-1 step 9 measures a revocation on a freshly started worker.
- Applying the record is the revocation: nothing caches plaintext. A revoked egress grant also bumps the origin's pool generation (§3.6.4), so no new request reuses a connection to the revoked address.
- **In-flight streams opened with the old key run to completion.** Use the GW29 model kill or isolation to cut them.

### 2.10 D4 (env-var keys refused in production), three layers

1. **Projector:** a row with `api_key_env_var` is published as `omitted: credential_env_var`.
2. **Gateway apply:** the record schema has no field that names an environment variable; an entry carrying one is an unknown field, omitted and counted.
3. **Gateway boot:** in `AMF_PROFILE=production`, any `AMF_PROVIDER_*`, `AMF_DEV_PROVIDER_*` or closed-list provider key variable refuses to start; an AST gate keeps production code free of provider-key env reads.

A request for an omitted alias gets `model_unavailable` (503, `x-should-retry: false`) with console rule `omitted:<reason>`, never `model_not_found`.

### 2.11 Threat model

| Actor or event | Gets | Does not get, and why | Residual |
|---|---|---|---|
| Stolen state store dump | Org ids, aliases, base URLs, sealed DEKs and credentials, plans, KEY hashes | Plaintext keys (need a KEK, never in state, control or PG) | Metadata; destroying a retired KEK makes old dumps unreadable |
| Stolen gateway host (root, live) | Resident KEKs, the HMAC key, the cipher cache, in-flight plaintext: **every key that gateway could open** | State-store writes (read-only user); the control DB; the Fernet key | Inherent to D2. Mitigations: no core dumps, not dumpable, no swap, non-root, read-only FS, KEK file 0400; project-scoped provider keys; KEK-per-cell (Q8) |
| Stolen gateway disk or image | Nothing secret (KEKs are a tmpfs-mounted `_FILE` secret) | — | KMS-wrapped later |
| Compromised re-hydrator (HMAC key, writer user) | It can forge or regress records. **It can point an org's alias at a keyless entry on a host of its choice** (no AAD on keyless entries) | Redirect a **sealed** credential (AAD binds `base_url`); a keyless entry to a public host or to a private address outside an `egress` grant and the operator CIDR fence: refused at gateway apply **and** at connect (§4.3, B-S1). Regression refused by anti-regress | Denial of service; redirect of keyless traffic **within** the org's own granted private targets. Ed25519 signatures (§7.8 follow-up) remove forging |
| Compromised projector | Control's Fernet key and the KEKs: every key | — | Hardened like a gateway; control tier only |
| Malicious org admin | Their own key and base URL | Other orgs' keys; internal network reach (`is_global`, block list, private only by staff grant); a non-443 public port without a grant (B-S10); header injection (charset); **memory exhaustion of a shared worker through a compressed body** (identity only; encoded bodies refused before reading; header and line bounds; §3.9, B-B3); **a dial to an IP literal outside policy** (dial guard on every dial, §4.2, B-B4); starving other orgs (contention-gated stream slots, §3.6.3) | Hostile content from their own upstream (the output scan applies) |
| Replay or rollback of state | — | Warm workers refuse old records (cursor, manifest `not_before`, `feed_floor`, stamp); `v` in the AAD stops splicing | A cold worker's single-record fetch: §2.9 |
| Log, trace or metrics leak | — | §2.7 gates and T-L1 | — |
| Control or DB compromise | Fernet tokens; with the control host, the model key (= v1's derivation of `DJANGO_SECRET_KEY` while v1 lives) | KEKs and DEKs | While v1 lives, `DJANGO_SECRET_KEY` disclosure equals model-key disclosure, as today; removed by new issue 1 |

### 2.12 Cost per request (measured)

`aead_results.json` (Python 3.12.3, `cryptography` 46.0.3, OpenSSL 3.0.13; thread CPU, minimum over 7 repeats of the mean of 20k operations; A re-measured `dec_hot` 603–613 ns and `cold` 2.3–3.1 µs):

| Operation | ns per op | Where |
|---|---|---|
| Credential decrypt, cached `AESGCM(DEK)` (39 / 164 / 256-byte keys) | 586 / 545 / 818 (locked: 520 / 580 / 583) | request path |
| Cold: KEK unwrap + build + decrypt | 2,106–2,445 | request path, cache miss |
| AAD encode (143 bytes) | 1,651 | **request path** (revision 2) |
| `b"Bearer " + key` | 56–61 | request path |
| Encrypt (projector) | 963–1,067 | projector, changed rows |
| Fernet decrypt (projector) | 11,786–14,640 | projector, changed rows |
| Memo digest `s` (projector) | 1,702–2,066 | projector, every row per round |

Per request: ≈2.3 µs on a cipher-cache hit, ≈4.1 µs on a miss. Against the 7.3–11.2 ms of dispatch and decode per streamed request at the planning mean (§6.4), credentials are under 0.1%.

### 2.13 KEK provider interface (D5)

```python
class KekProvider(Protocol):
    def kids(self) -> frozenset[str]: ...                                  # exported as amf_gateway_cred_kids
    def unwrap(self, kid: str, nonce: bytes, wrapped: bytes, aad: bytes) -> bytes: ...  # UnknownKid / InvalidTag
    def wrap(self, kid: str, dek: bytes, aad: bytes) -> tuple[bytes, bytes]: ...         # projector only
```

- **`FileKekProvider` (now).** Reads `AMF_CRED_KEKS_FILE` as `{"keys": [{"kid", "key": "<b64 32 B>"}]}`. Refuses: a file readable or writable by group or other, or not owned by the process uid; a duplicate kid; a kid outside `[a-z0-9-]{1,32}`; a key that is not 32 bytes. **Compose (B-N5):** a compose `secrets:` entry with `file:` is a bind mount that keeps the host file's mode and owner, so the deploy step is `install -m 0400 -o 10001 -g 10001 <src> <secrets dir>/amf_cred_keks.json`; the boot refusal prints that remedy line, with the observed mode and uid, never the content.
- **`EnvKekProvider` (dev and test only).** `AMF_CRED_KEKS` holds the same JSON. Refused in production, and also refused in every profile when both `AMF_CRED_KEKS` and `AMF_CRED_KEKS_FILE` are set (B-Q2).
- **`KmsWrappedKekProvider` (later).** One KMS Decrypt per process per kid at boot (W × kids per host per rollout), encryption context `{amf: kek, kid, namespace}`; a KMS outage at boot refuses to start.
- **`KmsDirectKekProvider` (HSM-only policies, small fleets).** One KMS call per org per worker on the apply path; not viable at 100k orgs (Q1).

## 3. Dispatch

### 3.1 Components and flow

```
edge (verified, masked body; parent pipeline unchanged)
  └─ RoutedProvider.open_routed (GW28: one open site, pre-content fallback)      dispatch/routed_provider.py
       └─ per candidate: UpstreamDispatcher.open(request, entry)                  NEW dispatch/upstream_dispatch.py
            ├─ ProviderProfile.shape(openai_body, entry) -> wire body             NEW dispatch/upstream_profiles.py
            ├─ StreamSlots.acquire(org) -> slot (contention-gated, §3.6)          NEW dispatch/stream_slots.py
            ├─ CredentialOpener.open(view, entry) -> header bytes                  NEW dispatch/cred_open.py
            ├─ UpstreamTransport.post_stream / post_json (the ONE call site)      NEW dispatch/upstream_transport.py
            │     ├─ OriginPools: one aiohttp TCPConnector per origin             (pool, generation, max age)
            │     ├─ PinnedResolver (AbstractResolver; resolve-then-pin)         dispatch/url_guard.py (extended)
            │     └─ dial guard (socket_factory; every dial, literals included)
            └─ decode: SSEDecoder (closed vocabulary, CRLF, comments, usage) | JSON  edge/wire/sse_decode.py
```

- `RoutingTable` is built from the **pinned MODEL view** (F1a-2), per view version. A candidate's `provider_key` becomes `(provider, origin)`. `RoutedProvider` keeps its single open call site; `rebind()` stays the only mutation of the verified body (`routed_provider.py:96-109`). Shaping happens after `rebind` and only **removes or renames** fields.

### 3.2 Provider profiles (OpenAI-compatible family)

Defaults checked on 11 Nov 2026 against vendor documentation (`RESULTS.md` §5). Each row is pinned by a replayed capture (T-D4, §8.2). Cells marked "(capture)" default to *drop* until captured.

| Profile | Default base | Auth | Budget field | Drops | Renames and maps | Stream usage | Delta keys (closed) |
|---|---|---|---|---|---|---|---|
| `openai` | `https://api.openai.com/v1` | Bearer | `max_tokens`; reasoning class: `max_completion_tokens`, drops `temperature`, `top_p`, penalties, `logit_bias`, and `stop` (capture) | — | `reasoning_effort` ∈ {none, minimal, low, medium, high} (capture per model) | `include_usage` | role, content, refusal, tool_calls |
| `google` | `…/v1beta/openai` | Bearer | `max_tokens` | `logit_bias`, `seed` (capture), penalties (capture) | `developer`→`system` (capture); effort ∈ {low, medium, high}, `minimal`→`low` | `include_usage` (capture) | role, content, tool_calls |
| `mistral` | `https://api.mistral.ai/v1` | Bearer | `max_tokens` | `logit_bias`, `stream_options` | `seed`→`random_seed`; `developer`→`system`; effort per `RESULTS.md` §5 | implicit | role, content (string or typed parts), tool_calls |
| `deepseek` | `https://api.deepseek.com/v1` | Bearer | `max_tokens` | `logit_bias` (capture) | `developer`→`system` | `include_usage` | role, content, reasoning_content, tool_calls |
| `huggingface` | `https://router.huggingface.co/v1` | Bearer | `max_tokens` | `logit_bias`, `reasoning_effort` (capture) | `developer`→`system` | `include_usage` (capture) | role, content, reasoning_content, reasoning, tool_calls |
| `meta` | `https://api.llama.com/compat/v1` | Bearer | `max_tokens` (capture) | `logit_bias`, `seed` (capture) | `developer`→`system` | (capture) | role, content, tool_calls |
| `cohere` | `…/compatibility/v1` | Bearer | `max_tokens` | `logit_bias` | effort kept only for `high` | (capture) | role, content, tool_calls |
| `ollama` | none; staff-granted private base only (§4.3) | none, or Bearer if a key is set | `max_tokens` | `logit_bias` | `developer`→`system` | `include_usage` | role, content, reasoning, tool_calls |
| `custom` | none; required | Bearer | `max_tokens` | `logit_bias`, `reasoning_effort` | `developer`→`system` | not requested | role, content, refusal, reasoning_content, reasoning, tool_calls |

Profile mechanics:
- **Profiles are data**: allowed fields, renames, role map, budget rule, usage mode, delta-key set, timeout class rule, embeddings flag, keep-alive expiry. Adding a provider is a row plus its capture.
- **Drops** are counted in `amf_upstream_params_dropped_total{provider}`, never logged with a value. Fields outside every profile are refused at parse (`openai_params.py:100-134`).
- **`reasoning_effort` vocabulary is profile data (A-S7).** Parse accepts the union {none, minimal, low, medium, high, xhigh} (widening `openai_params.py:55`); each profile maps or drops per vendor, counted.
- **`n` and `logprobs` (A-N7).** `n > 1` is clamped to 1 at parse (`_MAX_COMPLETIONS_PER_REQUEST = 1`, `runtime/resources.py:194`), so a vendor's lack of `n` is moot; `logprobs` and `top_logprobs` are refused at parse with `unsupported_parameter`. SDK users see one choice; the conformance notes say so.
- **Reasoning class (Q6, A-Q6, B-N7).** For the **default-origin** profiles (openai, google, mistral, deepseek) the class comes from the profile's model-name pattern (e.g. `^o\d`, `^gpt-5`, `-reasoner$`), overridable by the explicit `caps.reasoning`. For `custom`, `huggingface` and `ollama` the class is **only** the explicit `caps.reasoning` (F1a-2 I12), so a custom model named `o1-x` gets the standard class unless the operator says otherwise. The class sets the timeouts (§3.7) and the budget rule below.
- **Reasoning budget (A-S7).** With no client budget the gateway sends the org ceiling, which defaults to 4,096 (`runtime/resources.py:178`); reasoning tokens count against it, so empty `content` with `finish_reason: length` is common. Rule: for the reasoning class with no client budget, the budget is `org max_response_tokens` if the org declared one, else `reasoning_default_completion_tokens()` = 16,384, clamped by the 65,536 hard cap; the budget hold uses that value. A reasoning response that ends `length` with no content gets console rule `reasoning_budget_exhausted`. T-D1 and the openai capture pin it.
- **`stream_upstream` (A-S6).** The upstream is asked to stream by default, so abort, first-event timing and incremental output scanning behave the same in both client modes. `caps.stream_upstream=false` (per model, set in control) selects the JSON path of §3.11 for that model; a streaming client then receives the scanned response as SSE chunks after the output decision. The capture set includes OpenAI's 400 for a model whose streaming needs organisation verification; it maps to `upstream_invalid_request` with rule `stream_not_permitted`, and the console tells the operator to set the flag.
- **`include_usage` (A-N6).** Requested where the profile allows. The decoder lifts `usage` into `UpstreamEvent.usage` for charging. The usage-only chunk (`choices: []`) is forwarded downstream **only** if the client asked for `include_usage`; otherwise it is consumed. T-D3 asserts both.

### 3.3 `/v1/completions` (S1 F5; Q7 decided: render the legacy shape)

- `prompt` (one string, or a one-element list of strings; token ids refused, `openai_params.py:176-184`) is folded as `completion_prompt` and scanned (C9), and sent upstream as one `user` message to the chat endpoint (most of the family has no legacy endpoint). `echo`, `suffix`, `best_of`, `logprobs` and tool fields are refused at parse with `unsupported_parameter`.
- **The response is rendered as `text_completion`** by a completions renderer selected by route (`deps.renderer` gains `completions_body` and `completions_chunk`):
  - non-stream: `{"id": "cmpl-…", "object": "text_completion", "created", "model", "choices": [{"text", "index": 0, "logprobs": null, "finish_reason"}], "usage"}`;
  - stream: each chunk `{"id": "cmpl-…", "object": "text_completion", "created", "model", "choices": [{"text": <delta>, "index": 0, "logprobs": null, "finish_reason": null}]}`, a final chunk with `finish_reason`, the optional usage chunk under the same rule as §3.2, then `data: [DONE]`.
  - Only the `content` channel becomes `text`; reasoning and refusal channels are scanned like chat and not rendered (the legacy shape has no field for them); errors use the same SSE error frames.
- L36-1 asserts: a `prompt` carrying a blocked value makes **zero** recorder calls; a redacted one arrives masked; `client.completions.create(...).choices[0].text` works in the SDK matrix (T-D13).

### 3.4 Upstream SSE fidelity (S1 F6, §7.6)

Decoder changes in `edge/wire/sse_decode.py`:
- **Framing.** An event ends at `\n\n`, `\r\n\r\n` or `\r\r`; lines split on `\r\n`, `\n` or `\r` (WHATWG). One leading UTF-8 BOM is stripped at stream start (A-S5 d). A comment-only event (`: ping`, `: OPENROUTER PROCESSING`, a bare `:`) yields nothing and is not malformed. An event with fields but no `data` stays malformed. T-D3 feeds every split point for each framing.
- **Closed delta-key vocabulary (A-S5 a).** Only the profile's delta keys (§3.2) become channels. Any other key is dropped, never forwarded and never given egress state, and counted in `amf_upstream_unknown_delta_keys_total{provider}`. Per-stream state is therefore bounded by the profile, not by the upstream.
- **Typed content (A-S5 b).** `content` that is a list of parts is mapped: `{"type": "text", "text"}` parts to the content channel; `{"type": "thinking", …}` parts (Mistral reasoning models) to the reasoning channel; any other part type, or any other non-string `content`, is `stream_malformed_upstream` (never silently dropped).
- **Surrogates (A-S5 c).** A delta string ending in a lone high surrogate is held (at most one code unit per channel) and joined with the next delta on the same channel; a lone surrogate that is not completed by the next delta, or a lone low surrogate, is `stream_malformed_upstream`. Nothing reaches `asgi_sse.py:276` that cannot encode as UTF-8.
- **Reasoning keys (A-S5 e).** `reasoning_content` (DeepSeek, vLLM), `reasoning` (OpenRouter, newer vLLM, ollama) and Mistral `thinking` parts are all normalised onto the domain channel `output_reasoning` (declared scanned, `domain/channels.py:45`) **before** egress. T-D5 plants a value in each form and asserts redaction before forwarding.
- **`finish_reason`** is mapped per profile onto `stop | length | tool_calls | content_filter`; anything else is malformed and counted.
- **`usage`**: read from any chunk, last one wins, non-negative integers only.
- **Vendor `{"error": …}` mid-stream** keeps the GW12 R11 scan-then-forward path.
- **Bounds.** An event over `max_upstream_event_bytes()`, or a buffer that grows past it without a terminator, is `stream_malformed_upstream`. The transport bounds header and line sizes (§3.13), and encoded bodies never reach the decoder (§3.9).

### 3.5 Abort, disconnect and charging

#### 3.5.1 Abort and disconnect (S1 F3/F4; A-S1)

- Each request's upstream read is owned by its own async generator. A client disconnect, a kill-switch latch, a timeout or a fallback closes it, which closes the connection: a half-read body never returns to the pool. `abort()` stays a no-op.
- **Non-streaming requests also run under `run_until_disconnect`** (today only streaming does, `routes.py:314` against `:316`). An abandoned non-stream request therefore releases its admission and stream slots and its upstream connection within `cancellation_bound_s()`, instead of holding them up to the 299.94 s total while the customer's provider keeps generating.
- T-D6: two concurrent streams on one origin pool, cancel one, the other completes byte-identical; the cancelled socket is closed (pool count drops; the recorder sees FIN within `cancellation_bound_s()`); the same with the GW29 kill latch, after a GW28 fallback, and **for a non-streaming request whose client disconnects mid-generation**.

#### 3.5.2 Usage and charging (A-S2)

| Case | Upstream state | Budget outcome | Record |
|---|---|---|---|
| Pre-send failure (destination refused, DNS, stream slot wait expired, credential) | nothing sent | **refund** (`UpstreamEvent.sent=False`) | attempt `{code, sent: false}` |
| Non-2xx status before any content (400, 401, 403, 404, 413, 422, 429, 5xx) | sent; the provider generated nothing | **refund** (`UpstreamEvent.billable=False`) | attempt `{code, sent: true, billable: false}` |
| Pre-content timeout or reset after send | sent; the provider may have started | estimate stands | attempt `{code, sent: true, billable: true}` |
| Completed with `usage` | — | estimate reconciled to `usage` (refund the difference; an excess is charged) | usage in audit |
| Client abort, gateway cut (`output_blocked`, timeouts, kill), or mid-stream reset **after content** | provider billed for generated tokens | estimate stands (no `usage` arrives; the estimate already reserved `max_tokens`) | `usage_missing` in audit |

- **GW28 fallback.** The hold is per request. An attempt that ends refundable does not consume it; the next attempt runs under the same hold. A billable failed attempt is recorded in `routing.attempts[]` and counted in `amf_upstream_billable_failures_total{provider}`; the gateway budget meters the delivering attempt (reconciled), and the docs state that the customer's provider account may also bill the failed attempt (today's GW28 behaviour).
- T-D12 drives each row and asserts the hold, the refund and the audit fields.

### 3.6 Capacity: stream slots, fairness, origin pools

#### 3.6.1 Two bounds, not one (A-B2, B-B2)

The pipeline admission bound (C19) is sized for work that completes in about one p99: auth, plan, input scan, routing preflight. A streamed response lasts seconds, so holding that slot for the stream capped a worker at 14–15 streams. Revision 2 separates them:
- **Admission request slot** (`AdmissionBounds.request_depth`, unchanged sizing): held from admit until the stream slot is granted, i.e. through the pipeline and the bounded slot wait.
- **Stream slot** (`stream_slots()`, new, per worker): acquired **before** dispatch (so a refusal is pre-send and refunded), held until the response ends. On grant, `RequestHolds.handoff_to_stream()` completes the admission request slot.

This changes F1a-1-owned admission semantics (where the request slot is released), so it is amendment A6 (§10) and a hard dependency on parent revision 6 (§9.1).

#### 3.6.2 `stream_slots()` derivation

```
S_cpu = floor(util / (r_event × c_event))
S_mem = floor((per_worker_rss − rss_base − view_rss − cred_lru_bytes) / per_stream_bytes)
S_fd  = floor((connection_budget() / workers() − keepalive_idle_cap) / 2)
stream_slots() = min(S_cpu, S_mem, S_fd)      refuse to start if < 1
```

- `c_event` is the measured CPU per upstream event of the whole streamed path (dispatch, decode, output controls, downstream write). It is a **declared, measured input** like `q_safe`: `AMF_STREAM_EVENT_CPU_US`, produced by the CI benchmark job (§6.8), with no production default (refuse to start), as `q_safe` has none (`admit/quota.py:119`).
- `r_event` is the declared per-stream event rate (`AMF_STREAM_EVENT_RATE_HZ`; planning value 50 Hz, a 20 ms inter-token interval).
- `per_stream_bytes` = upstream connection + downstream connection + request state + `stream_buffer_bytes()`; measured by the CI job. `view_rss` is F1a-2's per-worker view (240 MiB at 100k orgs × 5 models without precomputed AAD).
- `per_worker_rss` must be raised to cover the view: the contract default of 400 MiB leaves 160 MiB for everything else at 100k orgs. F1a-2 and F1a-3 agree the figure (§9 I13).
- The numbers on the measured costs are in §6.5.
- `stream_slots()` is exported as `amf_stream_slots{state="limit|used|waiting"}` and is the autoscaling signal (in-flight, as #99 4.4 asks), not CPU.

#### 3.6.3 Contention-gated per-org fairness (Q9 decided; B-S8)

`StreamSlots` (one per worker) grants slots by one rule:
- **Free slot and no waiter → grant at once**, whatever the org already holds. An idle worker serves one org fully (owner, Q9).
- **No free slot → wait** in the org's FIFO, bounded by `upstream_slot_wait_s()` (§3.7) and by the admission request depth (waiters hold admission slots). Expiry → `upstream_inflight_limited` (429, `retry-after: 1`), pre-send, refunded.
- **On release, the freed slot goes to the waiting org that holds the fewest slots** (ties: oldest waiter). Under contention, holdings therefore converge to the max-min fair share as streams end, without preemption. With one org holding all `S` slots, a newcomer's expected wait is about `T_stream / S` (the first release).
- This is **the per-org total in-flight rule** across all origins: an org with several custom origins competes for the same worker pool. A hard per-org ceiling would idle capacity on an otherwise idle worker, against the Q9 decision; under contention the max-min rule bounds every org to its share.
- **Per-origin limit = `stream_slots()`** for every origin. Origins need no share of their own: contention for an origin is contention for the worker's stream slots, so Q9's "share of a shared provider origin under contention" is exactly this rule.
- State: one counter per org with a held or waiting stream, so the table is bounded by `stream_slots()` + admission depth.
- Fleet-level fairness stays GW27's per-org rate limits (`admit/org_limits.py`); this rule is per worker.
- T-D14: an idle worker grants all `S` slots to one org; with two orgs waiting, freed slots alternate toward the lower holder; a waiter is never refused while a slot is free; the wait bound; table bounds.

#### 3.6.4 Origin pools and connection lifetime (§7.4; A-S4, B-S3, B-N1)

- **Key.** `Origin = (scheme, host, port)`, canonical. One aiohttp `TCPConnector` per hostname origin, never shared across hostnames (TLS session and SNI stay per hostname; the custom resolver keeps the hostname as the pool key and `server_hostname`).
- **Default-provider origins** (≤ 7) are created lazily and never evicted. **Custom origins** sit in a refcounted LRU of `upstream_custom_origins()` (contract, from the FD budget): eviction only at zero in flight; if every custom origin is busy, a new one is refused (`upstream_unavailable`, fallback-eligible, `amf_upstream_origin_refused_total{reason="table_full"}`). Distinct custom origins per org are capped at the projector (`base_url_cap`) and re-checked at apply.
- **Keep-alive idle expiry, per class:** default origins take the profile value, at most 15 s and at most half the idle close observed for that origin by the T-D4 capture run (which records it); **custom origins 1.5 s**, below gunicorn's 2 s and uvicorn's 5 s defaults (vLLM `VLLM_HTTP_TIMEOUT_KEEP_ALIVE` default 5 s). Idle keep-alive per origin ≤ `ceil(stream_slots() / 2)`.
- **Maximum connection age** `upstream_conn_max_age_s()` = 60 s: an older connection is closed after its current response instead of returning to the pool, so a hot origin re-dials (and re-checks, §4.2) at least once a minute.
- **Pool generation.** Any change to an origin's checked address set (a new DNS answer, an egress grant revoked or expired at view apply) bumps the origin's generation: idle connections are closed at once, in-flight ones finish, and no connection from an older generation is reused. Revocation therefore converges for new requests at view apply (§2.9), not at connection age.
- **Pool acquire** never waits: a request reaches the transport only holding a stream slot, and the per-origin limit equals `stream_slots()`.
- **Why not one pool per `(org, base_url)`:** with 100k orgs on a handful of origins it would multiply sockets and handshakes by the org count. Credentials never ride a pool (per-request header), and fairness lives in the stream slots.

### 3.7 Timeouts: one table (A-B3)

Values at the contract's `target_p99_ms` = 20 ms. Every value is a contract accessor (literals only in `runtime/resources.py`). "Boot check" means `derive_bounds` refuses to start if the ordering fails.

| Bound | Value | Invariant | Enforced by | Test |
|---|---|---|---|---|
| DNS resolve | `upstream_dns_timeout_s()` = 2 s | < connect | `PinnedResolver` (single-flight) | T-D15 |
| Connect incl. TLS | `upstream_connect_timeout_s()` = 5 s | < standard first event | transport (`ClientTimeout.sock_connect`) | T-P1 a |
| Stream-slot wait | `upstream_slot_wait_s()` = min(2 s, remaining pre-content budget) | ≤ pre-content budget | `StreamSlots` | T-D14, T-P1 b |
| First event, standard | 30 s (`upstream_first_event_timeout_s()`, unchanged) | gap < first < total (`StallBounds`, `stall_guard.py:46-48`) | `StallGuard` per attempt | T-P1 c |
| First event, reasoning | 120 s (`upstream_reasoning_first_event_timeout_s()`, v1's ceiling, §7.6) | same | `StallGuard` | T-P1 d, e |
| Gap, standard | **20 s** | 0 < 20 < 30 | `StallGuard` | T-P1 f |
| Gap, reasoning | 60 s | 0 < 60 < 120 | `StallGuard` | T-P1 g |
| Total per attempt | 299.94 s (`upstream_total_timeout_s()`) | first < total < max stream; boot check | `StallGuard` | T-P1 h |
| Edge pre-content (first upstream event) | **per request**: Σ over the first `k` ranked candidates of (that candidate's first event + cancel) + cancel, `k` = min(`routing_max_attempts()`, candidates). Standard × 2 = 60.18 s; reasoning + standard = 150.18 s; reasoning × 2 = 240.18 s | ≥ every attempt's first event + cancel, so the router fires first (GW28 D-13); ≤ max stream − cancel: boot check `max_attempts × (120 + 0.06) + 0.06 < 300` | `StreamTimeouts.next_event`, `first_event_s` taken from the routing decision instead of `routing_pre_content_budget_s(2)` (`chat_stream.py:250-256`) | T-P1 d, e |
| Edge inter-event, routed | the selected candidate's class gap + `cancellation_bound_s()` (20.06 s / 60.06 s) | > router gap (router fires first) | `StreamTimeouts.next_event` | T-P1 f, g |
| Edge inter-event, unrouted (dev echo only) | 1.0 s (unchanged) | — | `StreamTimeouts.next_event` | existing |
| Idle (client back-pressure) | 3.0 s (`idle_timeout_s()`, unchanged) | keep-alive < idle | `StreamTimeouts.send` | T-P1 i |
| Downstream keep-alive | `downstream_keepalive_s()` = idle / 3 = **1.0 s**: after the SSE head is committed, a `: keep-alive\n\n` comment is written **through `StreamTimeouts.send`** whenever no write happened for that long | < idle; only after the head | `ChatStream` serving loop | T-P1 i, j |
| Write | 0.5 s (`write_timeout_s()`, unchanged) | — | `StreamTimeouts.send` | existing |
| Max stream duration | 300 s | > total per attempt | edge | existing |
| Edge load balancer read | nginx `/v1` `proxy_read_timeout 360s` (`deploy/nginx.conf:133`); GKE backend ≥ 350 s (#99 4.2) | > max stream duration (non-stream responses send nothing until complete; pre-content sends nothing for up to 240.18 s) | deployment | T-O3 |

Notes:
- With the 1.0 s keep-alive, a 5 s, 20 s or 59 s upstream pause after the first event never trips the 3.0 s idle bound while the client reads; a client that stops reading still trips the write bound once its socket buffer fills.
- **Pre-content there is no keep-alive**: the SSE head is deferred so that a pre-content refusal stays a real HTTP error (kept from develop). The client sees no byte for up to the pre-content budget, within the LB timeout and the OpenAI SDK's 600 s default.
- **Limits (A-N8).** A single attempt is cut at 299.94 s, which cuts long high-effort reasoning runs; this is the parent's max stream duration, unchanged here.
- T-P1 cases: (a) connect timeout → `upstream_timeout` rule `connect`; (b) slot wait → `upstream_inflight_limited`; (c) standard 31 s first event → `upstream_timeout` rule `first_event`, then fallback; (d) reasoning 90 s first event completes; (e) reasoning 125 s → `upstream_timeout` and, with a second reasoning candidate, the fallback still fits the edge budget; (f) standard 5 s and 19 s pauses complete, 21 s → `upstream_timeout` rule `gap`; (g) reasoning 59 s pause completes; (h) total; (i) 5 s pause with a slow-reading but live client: no `stream_idle_timeout`, keep-alives seen; (j) keep-alive bytes are SSE comments that the OpenAI and Anthropic SDKs ignore (SDK matrix); (k) boot refuses an ordering violation (unit, per invariant).

### 3.8 Retries and idempotency

- A chat or embeddings POST is **not idempotent**, so the HTTP layer **never retries**: no transparent retry on a reused connection, connector retries 0.
- **Keep-alive race (A-S4).** When a server closes an idle connection while a request is being written on it, the outcome is defined: `upstream_unavailable` (pre-content, `sent=True`, billable unknown → treated as non-billable only if a status line was received; here none was, so the estimate stands), GW28 fallback-eligible, counted in `amf_upstream_reused_conn_failures_total`. The per-class expiry of §3.6.4 makes it rare. T-D16 reproduces it with a recorder that closes idle connections at 1.0 s and a client whose expiry is set to 1.4 s for the test, and asserts the code, the counter and the single recorder call.
- The only re-attempt is GW28's candidate fallback, **pre-content only** (`routed_provider.py:69,284-286`), on the closed set: `upstream_timeout`, `upstream_unavailable`, `upstream_rate_limited`, `upstream_error`, **`upstream_quota_exceeded`** (A-S11: pre-content and unbilled; exactly what an org with a second provider wants), `provider_unreachable`, `upstream_destination_refused`, `credential_unavailable`. `upstream_inflight_limited` is **not** eligible: it is a local capacity signal, and another candidate would need the same worker's stream slot.
- **`routing_attempts_exhausted` carries the last cause's retry headers** (A-S11): if the last attempt was `upstream_rate_limited` with `retry-after: 7`, the 503 carries `retry-after: 7`; if it was a no-retry code, `x-should-retry: false`.
- v1's `num_retries=2` is deliberately not reproduced (R6 retry-storm class, `llm_router.py:1819`).

### 3.9 Error mapping

- **Encoding first (B-B3).** The request carries `Accept-Encoding: identity`. A response whose `Content-Encoding` is anything but absent or `identity` is `stream_malformed_upstream` **from the response head, before any body byte is read** (aiohttp `auto_decompress=False`), and the connection is closed, not pooled. Probed on 3.14.5: a 64 MiB gzip body is refused with 0 KiB RSS growth (`rev2/probes/aiohttp_transport_guard.txt`, check 5).
- **Media type (A-N5).** `content-type` is parsed as a media type (lower-cased, parameters stripped): `text/event-stream` for chat, `application/json` for embeddings; anything else on a 2xx is `stream_malformed_upstream`.
- Status, then a bounded body classification (at most `max_provider_error_bytes()` = 8 KiB, never echoed or logged): 401 and 403 → `upstream_auth_error`; Google's 400 `API_KEY_INVALID` → `upstream_auth_error`; 402, and 429 with `insufficient_quota` → `upstream_quota_exceeded`; 429 → `upstream_rate_limited` with `retry-after` clamped to `[1, max_retry_after_s()]` (new `UpstreamEvent.retry_after_s`); 400, 413, 422 → `upstream_invalid_request` (rule `stream_not_permitted` for the §3.2 case); 404 → `upstream_not_found`; 408, 504 → `upstream_timeout`; 500 → `upstream_error`; 502, 503 → `upstream_unavailable`; 3xx → `upstream_error`, never followed.
- **Dial refusals** (dial guard, §4.2) arrive as `ClientConnectorError` whose `os_error` is the guard's `DialRefused` (verified on 3.14.5) and become `upstream_destination_refused`. Any other exception inside the dispatcher becomes a declared code, never `routing_internal_error` (closes C6's class, including the non-ASCII key case).

### 3.10 Codes table (§7.5)

Every code gets an `_ENVELOPE` row (`edge/errors.py:159-347`), a `failure_class` entry (`domain/failure_class.py:46-71`), a `stages_to_console` row and **an SSE error-frame row** (`edge/wire/sse.py:338` table; A-N4), so a mid-stream failure renders its own message, not the generic one.

| Code | HTTP / type | Retry headers | Class | Console (stage → action, rule) | SSE frame message | New? |
|---|---|---|---|---|---|---|
| `upstream_auth_error` | 502 `gateway_error` | `x-should-retry: false` | operational | `model_output` → error | "The model provider rejected the configured credential." | reuse; header added |
| `upstream_quota_exceeded` | 429 `rate_limit_error` | `x-should-retry: false` | operational | `model_output` → error | "The model provider account has exhausted its quota or billing." (says quota, not rate, A-Q11) | **new** |
| `upstream_rate_limited` | 429 `rate_limit_error` | `retry-after` (clamped) | operational | `model_output` → error | existing | reuse |
| `upstream_invalid_request` | 400 `invalid_request_error` | — | operational | `model_output` → error (rule incl. `stream_not_permitted`) | existing | reuse |
| `upstream_not_found` | 404 `not_found_error` | — | operational | `model_output` → error | existing | reuse |
| `upstream_timeout` | 504 `timeout_error` | — | operational | `model_output` → error (rule `connect` / `first_event` / `gap` / `total`) | existing | reuse |
| `upstream_unavailable` | 502 `gateway_error` | — | operational | `model_output` → error | existing | reuse |
| `upstream_error` | 502 `gateway_error` | — | operational | `model_output` → error | existing | reuse |
| `upstream_request_unbuildable` | 500 `gateway_error` | — | operational | `model_output` → error | existing | reuse |
| `stream_malformed_upstream` | 502 `gateway_error` | — | operational | `model_output` → error (rule `encoding` / `media_type` / `framing` / `oversize` / `vocabulary`) | existing | reuse |
| `upstream_destination_refused` | 502 `gateway_error` | `x-should-retry: false` | operational | `model_output` → error | "The model endpoint's address is not permitted." | **new** |
| `provider_unreachable` | 502 `gateway_error` | — | operational | `model_output` → error, rule `dns` | "The model endpoint's name could not be resolved." | reuse (A3) |
| `upstream_inflight_limited` | 429 `rate_limit_error` | `retry-after: 1` | operational | `model_output` → error | "The gateway is at its stream capacity; retry shortly." | **new** |
| `credential_unavailable` | 503 `service_unavailable` | — | operational | `model_output` → error, rule `unknown_kid` / `dek_tag` / `cred_tag` | existing | reuse |
| `model_unavailable` | 503 `service_unavailable` | `x-should-retry: false` | operational | `model_routing` → error, rule `omitted:<reason>` | — (preflight only) | **new** |
| `routing_attempts_exhausted` | 503 `service_unavailable` | **the last cause's** | operational | `model_routing` → error | existing | headers added |
| `model_not_found` / `routing_unknown_model` | 404 `not_found_error` | — | **policy** | `model_routing` → block | — | classify |
| `routing_internal_error` | 503 `service_unavailable` | — | operational | `model_routing` → error | existing | classify |
| `unsupported_operation` | 422 `invalid_request_error` | — | policy | `model_routing` → block | — | reuse |

- Console stage convention unchanged (`console_stages.py:163-193`): preflight refusals on `model_routing`; everything after it, including the dispatcher's pre-send failures, on `model_output`.
- **Omission reasons** (closed, shared with F1a-2 §6.5): `credential_env_var`, `credential_missing`, `credential_invalid_format`, `credential_unreadable`, `base_url_refused`, `base_url_cap`, `provider_unsupported`, `alias_collision`.
- **The refusal keeps its candidate** (fixes C7).
- **`retry_headers` generalisation.** `NO_RETRY_CODES` = {`upstream_auth_error`, `upstream_quota_exceeded`, `upstream_destination_refused`, `model_unavailable`} emit `x-should-retry: false`; rate-limit codes emit `retry-after`; incident codes unchanged.

### 3.11 Embeddings (§7.6)

`UpstreamTransport.post_json` POSTs `<base>/embeddings` (`encoding_format: "float"` forced, `Accept-Encoding: identity`), reads a body bounded by `max_embedding_response_bytes()`, validates `data[i].embedding` (finite floats, one per input, index-ordered, equal dimension) and yields today's per-input events. Profile flag `embeddings`: true for openai, google, mistral, cohere, ollama, custom (capture); false for deepseek, huggingface, meta → `unsupported_operation` before dispatch. Pre-send failures refund (they move above `mark_dispatched`); `usage` feeds charging.

### 3.12 Observability (§7.7; B-S6, B-S7, B-N6)

- **Trace.** `pipeline_trace.routing = {requested_model, selected_model, route_destination, attempts}`; `route_destination` is the provider id (closed vocabulary), never a URL. `attempts[]` is bounded by `routing_max_attempts()`; each item is `{candidate, provider, code, sent, billable}`. `console_schema.py:51` gains `routing` with its own key set and grammars; control's ingest stores it; the console shows the final code plus the attempt count (UI: Q10, console track).
- **Provider request id.** The response's `x-request-id` (or the profile's equivalent header), if it matches `[A-Za-z0-9_.:-]{1,64}`, is recorded in the attempt and the audit record, for support and billing correlation; otherwise dropped and counted.
- **Audit row** also carries the gateway id, the `kid` and the `dek_id` used (B-N6): non-secret identifiers that scope a host-compromise window.
- **Series** (pre-seeded, closed label vocabularies; added to `Publisher.SERIES_COUNT` with their own producer). **Counting semantics (B-S7): every upstream series counts attempts, not client requests**; pre-send failures are attempts with `result="presend"`.

| Series | Labels (cardinality) |
|---|---|
| `amf_upstream_attempts_total` | provider × result{ok, error, presend} (36) |
| `amf_upstream_failures_total` (§7.7) | provider × code: the 15 dispatch codes of §3.10 (180) |
| `amf_upstream_first_event_seconds` (histogram) | provider (12 × 3) |
| `amf_upstream_params_dropped_total` | provider (12) |
| `amf_upstream_unknown_delta_keys_total` | provider (12) |
| `amf_upstream_billable_failures_total` | provider (12) |
| `amf_upstream_reused_conn_failures_total` | — (1) |
| `amf_upstream_pool_connections`, `amf_upstream_pool_origins` | class{default, custom} (2 + 2) |
| `amf_upstream_origin_refused_total` | reason{table_full, ssrf, dial_guard, org_cap} (4) |
| `amf_stream_slots` | state{limit, used, waiting} (3) |
| `amf_stream_slot_wait_seconds` (histogram) | — (3) |
| `amf_upstream_inflight_limited_total` | — (1) |
| `amf_model_omitted_requests_total` | reason (8) |
| `amf_cred_open_failures_total` | reason{unknown_kid, dek_tag, cred_tag, format} (4) |
| `amf_cred_cipher_cache_total` | event{hit, miss, evict} (3) |
| `amf_gateway_cred_kids` (§7.1) | kid: one per held KEK, fixed per process, ≤ `cred_max_kids()` = 4 |

- **Logs.** `routing_decision` gains `request_id=` (`routing_trace.py:56-68`; the decision's `request_key` is `holds.request_id`, `chat_upstream.py:78-84`). Dispatch logs carry the request id, provider, origin class (never the host), code and exception type name.
- **Console served status (B-S6).** The model list shows `v2_serving_status` (§2.5): "served", or "not served by the v2 gateway: <reason>" for every omission, including `provider_unsupported` (every Azure, Anthropic and Bedrock row until F1b/F1c). L36-1 step 8.
- **L36-1 metric deltas** are read from a single-worker gateway (§7.7).

### 3.13 HTTP transport (Q5)

**Choice.** aiohttp, behind the `UpstreamTransport` seam (`post_stream`, `post_json`, `aclose`), because it still wins under paced load by 2.5–3.5× at 64 in flight and 5.7× or more at 256 in flight (§6.3). httpx stays for GW32 OAuth (`pinned_http`) and tests only.

**Version floor (B-B3).** Reviewer B named 3.13.3 (CVE-2025-69223, the `auto_decompress` zip bomb). The advisory database on 12 Nov 2026 shows that is not enough (`rev2/probes/osv_2026-11-12.jsonl`):

| aiohttp | OSV entries | Client-relevant examples still open |
|---|---|---|
| 3.12.15 (revision 1's bench) | 72 | all below |
| 3.13.3 | 56 | GHSA-cq5v-8q36-5273 (HIGH, out-of-bounds read in the C response parser on malformed chunked bodies, fixed 3.14.3); GHSA-2g87-pp6c-29x5 (HIGH, unbounded memory/CPU, fixed 3.14.4); GHSA-63hw-fmq6-xxg2 (C parser bypasses `max_line_size`, fixed 3.14.1); GHSA-4m7w-qmgq-4wj5 (`server_hostname` override ignored on reused HTTPS connections, fixed 3.14.1, directly relevant to pinning); GHSA-9x8q-7h8h-wcw9 (resources not closed after mid-body disconnect, fixed 3.14.1) |
| 3.13.5 | 36 | the 3.14.x fixes above |
| **3.14.4, 3.14.5** | **0** | — |

So the floor is **`aiohttp>=3.14.4`, pinned `==3.14.5` with hashes**; the repo's dependency scan (OSV, `reusable-dependency-scan`) gates new advisories. The benchmark of §6 ran on 3.14.5.

**The single call site** (`dispatch/upstream_transport.py`) hard-codes, and a boot assertion re-checks per pool:
- `ClientSession(trust_env=False, auto_decompress=False, max_line_size=8190, max_field_size=8190, skip_auto_headers=("Accept-Encoding", "User-Agent"))`, `Accept-Encoding: identity` on every request, `allow_redirects=False` on every request (B-B4: the session cannot hold it, so only this call site may call `post`);
- `TCPConnector(resolver=PinnedResolver, use_dns_cache=False, socket_factory=dial_guard, limit=stream_slots(), keepalive_timeout=<class expiry>, ssl=<system CA context, TLS ≥ 1.2, OP_NO_COMPRESSION>)`;
- no default headers; `Authorization` set per request (§2.6);
- the C parser is asserted at boot (`aiohttp.http_parser.HttpResponseParser` is the C class); the pure-Python parser refuses to start.
- **AST gate** `lint/check_upstream_transport.py`: `aiohttp.ClientSession`, `aiohttp.request`, `TCPConnector` and httpx clients may be constructed only in `upstream_transport.py` and `pinned_http.py`.
- `UpstreamTransport` raises only declared failures (`DialRefused`, `ResolveFailed`, `ConnectFailed`, `ReadFailed`, `EncodingRefused`, `MediaTypeRefused`, timeouts), which §3.9 maps.

**What A's conditions required, and the proof** (`rev2/probes/aiohttp_transport_guard.py` on 3.14.5, all checks pass):
- control: without a dial guard an IP-literal URL to a listening `127.0.0.2` connects and the resolver is never called (B's finding reproduced on 3.14.5; `connector.py` `_resolve_host` returns literals directly);
- with the guard, the literal is refused with **0 TCP connections** at the server; a hostname resolves through `PinnedResolver` and connects; a resolver answer outside the checked set (rebinding double) is refused with 0 connections; a 302 to `169.254.169.254` is returned, not followed; a gzip body is refused before reading; the wire carries `Accept-Encoding: identity`; with `HTTP(S)_PROXY` set the request still goes direct.
- `use_dns_cache=False` makes aiohttp call `PinnedResolver.resolve` for every new connection (4 calls for 4 connections in the probe), so aiohttp's own DNS cache cannot hold an unchecked answer; happy eyeballs only ever sees the checked addresses the resolver returns, and the dial guard re-checks each one.

**HTTP/2 is foreclosed by this choice (B-S5).** aiohttp has no h2 client. h2 multiplexing would cut sockets and handshakes; §6.7 shows sockets are not the binding limit at HTTP/1.1. If h2 is adopted later (another client), the connection-level flow-control window must be raised and every stream drained into its own bounded buffer, or one slow downstream consumer stalls other tenants' streams on the same connection.

### 3.14 Trust boundary (§7.8) and compose `gateway-v2-prod` (§7.9)

**Valkey ACL**, on a dedicated `valkey-state` service for the production profile (the shared dev `redis`, C17, is unchanged):
- `user default off`;
- `user gateway`: read-only (`%R~`) on `{ns}:meta:*`, `{ns}:idx:*`, `{ns}:on:*`, `{ns}:key`, `{ns}:ks`, `{ns}:budget`, `{ns}:plan:*`, `{ns}:model:*`, `{ns}:fleet:signed`, `{ns}:fleet:registry`, `{ns}:stamp`; `SUBSCRIBE` on `{ns}:updates`; read-write only on the request-path families `{ns}:budget:*`, `{ns}:audit:*`, `{ns}:auditctl:*`, `{ns}:responses:*`;
- **`user gw-<host>`** (one per gateway host, created by deploy tooling): write on `{ns}:fleet:kids:<host>` only (B-N2);
- `user projector`, `user rehydrator`: write on the state families; deploy tooling writes `{ns}:fleet:registry`;
- passwords from `_FILE` secrets; key families `runtime/store_keys.py:44-177`.
- T-S1: `gateway` gets `NOPERM` on `SET {ns}:model:x` and on `SET {ns}:fleet:kids:<other host>`.

**`gateway-v2-prod`** (compose profile `gateway-v2-prod`, not started by default): `python -m gateway_v2.edge.serve` with `AMF_PROFILE=production`; secrets under `/run/secrets` (`AMF_STATE_HMAC_KEY_FILE`, `AMF_CRED_KEKS_FILE`, `AMF_STATE_VALKEY_PASSWORD_FILE`, the `gw-<host>` password), env forms refused; `ulimits: {core: 0, nofile: 65536}`, `AMF_FD_LIMIT: "65536"`, `mem_limit` = `memswap_limit`; `user: "10001:10001"`, `read_only: true`, `tmpfs: /tmp`, `cap_drop: [ALL]`, `no-new-privileges`; port `127.0.0.1:8302:8302`; `WEB_CONCURRENCY: ${GATEWAY_V2_PROD_WORKERS:-1}`; `depends_on` healthy `valkey-state`, `state-rehydrator-a`, `state-projector-a`; healthcheck `/readyz`; `AMF_PRIVATE_EGRESS_CIDRS` empty by default; declared `AMF_STREAM_EVENT_CPU_US` and `AMF_STREAM_EVENT_RATE_HZ` (§3.6.2).
**Test overlay** `docker-compose.f1a3-test.yml` is the **only** place the recorder is reachable (§7.3): it adds the T02 recorder on a private network, sets `AMF_PRIVATE_EGRESS_CIDRS` to it and seeds an `OrgEgressGrant` for the test org. T-O3 and T-O4 check these properties statically.

## 4. SSRF (§7.3)

### 4.1 Base-URL grammar

Checked at the projector (omit `base_url_refused`), re-checked at gateway apply, and the final URL asserted at dispatch.
- **Scheme:** `https`; `http` only under a `private_target` grant (§4.3).
- **Host:** lower-case LDH labels (each ≤ 63, total ≤ 253, no trailing dot), or a dotted-quad IPv4 or bracketed IPv6 literal. Refused: userinfo, percent-encoding in the host, zone ids, IDN (punycode accepted as LDH), numeric and hex shortcuts (`2130706433`, `0x7f.1`, `127.1`).
- **Port (B-S10).** Default origins: the profile's. Custom **public** hosts: **443 only** by default; any other port needs an `OrgEgressGrant(kind=public_port)` from staff. Private targets: the port named in their grant. This removes the port-scan oracle that distinct codes would otherwise give.
- **Path:** only `[A-Za-z0-9._~!$&'()*+,;=:@/-]` segments; refused: `.`/`..` segments, `%2e`, `%2f`, `%5c`, backslashes, empty segments; no query, no fragment.
- **Join:** structural, `base_path + "/" + endpoint`, `endpoint ∈ {chat/completions, embeddings}`; the dispatcher asserts the final path before dialling.
- **F1b/F1c:** the same grammar governs `api_version`, Bedrock `region` and every path segment.
- **Host allowlist (Q12, accepted):** custom public hosts are unrestricted by default; `AMF_CUSTOM_HOST_ALLOW` (exact hosts or `*.suffix`) is the regulated-deployment switch, enforced by the projector and at connect.
- **IP literals (B-B4):** a literal host is checked against the address policy **at gateway apply** (omit `base_url_refused`), not only by name at the projector.

### 4.2 Address policy, resolve-then-pin and the dial guard

An address is **refused** when it is not `is_global` **or** `_blocked(...)` refuses it (`url_guard.py:63-88`: metadata, link-local, loopback, reserved, multicast, unspecified). Embedded IPv4 is decoded and re-checked **unconditionally, even for an allowlisted host**: `64:ff9b::/96`, `64:ff9b:1::/48`, `2002::/16`, Teredo `2001::/32`, `::/96`, `::ffff:0:0/96`. Evidence `ssrf_probe_py312.json` (whose Teredo comment now correctly says 127.0.1.1, B-N4).

**`PinnedResolver`** (an aiohttp `AbstractResolver`, one entry per origin):
- **Resolve once per window** (`upstream_dns_ttl_s()` = 30 s) through the loop's non-blocking resolver, **single-flight per origin** (concurrent requests await one resolution), bounded by `upstream_dns_timeout_s()` = 2 s.
- **Check every returned address**; any refusal refuses the whole answer (a mixed answer is refused, `url_guard.py:144-147`).
- **Refresh ahead:** the first lookup after 80% of the window starts a background re-resolution and is served from the current set, so expiry never makes every in-flight request resolve at once.
- **Resolver error** (timeout, SERVFAIL, NXDOMAIN): serve the last checked set for at most `upstream_dns_stale_s()` = 300 s, re-checking it against the **current** policy (grants, fence, allowlist) on every use; counted. A **refusal is never served stale**: a new answer containing a refused address refuses new connections at once and bumps the pool generation (§3.6.4).
- No usable set and no stale set → `provider_unreachable` (pre-send, refunded, fallback-eligible).
- Cost: per-request resolution (develop, C5) would add ≈68 µs plus an executor hop to every request; the cache makes it a dict lookup.

**Dial guard** (`socket_factory`, B-B4): aiohttp calls it for **every** dial, including IP-literal hosts, for which it never calls the resolver. It refuses (raising `DialRefused`, before any SYN) any address not in the origin's current checked set, or, for a literal host, any address that fails the policy and the grant/fence rules of §4.3. Probed on 3.14.5 (§3.13).

**DNS rebinding defence** = check-on-resolve + dial-only-checked-addresses (guard) + no second resolution + bounded window + generation bump. T-S3 uses a resolver double that answers public, then `127.0.0.1`, then `169.254.169.254`, and runs **against the aiohttp transport** with literal rows (T-S2, T-S7).

### 4.3 Private targets and keyless entries (A2; B-S1)

A private, non-global target is dialled only when **all** hold:
1. **A staff-granted `OrgEgressGrant(kind=private_target)`** for that org, scheme, host and port, projected into the org's MODEL record `egress` (§2.5). Per org: org B cannot use org A's grant. Enforced at the projector.
2. **The address is inside the operator fence** `AMF_PRIVATE_EGRESS_CIDRS` on the gateway (default empty: no private egress at all).
3. **Re-checked at gateway apply and at connect** (dial guard), against the resolved address.

**Keyless entries (B-S1).** A keyless entry has no AAD, so a holder of the HMAC key could point an alias at a keyless entry on a host of its choice. Therefore, at **gateway apply and at connect**, a keyless entry is accepted only if its base URL is covered by a `private_target` grant in the same record **and** its address is inside the CIDR fence; a keyless entry for a public host, or outside the fence, is omitted (`credential_missing`) and never dialled. The residual (§2.11): a forger can redirect keyless traffic only among the org's own granted private targets inside the operator's fence.

Further rules: `http` only under such a grant; metadata, link-local, loopback and multicast are **never** allowed (a docker-host ollama is reached through the host-gateway bridge address); **redirects are off everywhere** (§3.13). **Limit (A-N8):** there is no per-org CA bundle, so a private HTTPS endpoint must present a publicly trusted certificate, or use `http` inside the grant and the fence.

## 5. What the projector and the gateway each refuse, and when

| Check | Projector (omit) | Gateway apply | Gateway connect (dial guard) |
|---|---|---|---|
| D4 env-var key | `credential_env_var` | unknown field → omit | — |
| Key charset | `credential_invalid_format` | — (sealed) | header validity |
| Base-URL grammar and port | `base_url_refused` | re-check → omit | final-path assertion |
| Address policy | name-level; literals checked | **literal hosts checked** (B-B4) | **every dial**, literals included |
| Private target | `private_target` grant must exist | grant in view **and** CIDR fence | grant **and** fence, on the resolved address |
| Keyless | only under a `private_target` grant | grant **and** fence, else omit (B-S1) | grant **and** fence |
| Custom origins per org | `base_url_cap` | count re-check | table capacity (§3.6.4) |

## 6. Scaling (re-based on paced streams; B-B1, A-N3)

### 6.1 What is measured, and what "100k RPS" means here

- **Definitions (owner, 12 Nov; runbook §1.1 and §1.3).** A *qualified* request is a real `/v1` request with authentication, org configuration, policy work, input controls, provider dispatch, output controls, finalisation and audit enqueue; blocks are reported separately. "100k RPS remains a future scale-out architecture test only. The code must not contain a design ceiling below it; the present $5k fleet is not expected to deliver 100k completed chats." The primary metrics are qualified RPS, CPU ms per request, qualified RPS per vCPU, RPS per $1,000-month and p99 firewall overhead (< 20 ms).
- **What F1a-3 owns** of a qualified streamed request: dispatch (transport and connection), the SSE decode, credentials and shaping. §6.3 measures that scope on paced streams. §6.4 measures the **whole qualified-shaped path as it exists on develop** (dev profile) to show what share dispatch is; the F1a-3 path itself is measured by the CI job (§6.8) once it exists.
- **Cost basis:** runbook §0.6 (Cloud Billing Catalog, asia-south1, on-demand): a c4-highcpu-16 gateway with its boot disk is $527.13 a month for 16 vCPU, **$32.95 per vCPU-month**; utilisation cap 0.75 (`ResourceContract.utilization_cap`). Reviewer B priced AWS c7g at $26.46; the ratios do not change.

### 6.2 Method

- **Upstream** (Reviewer B's `sse_upstream.py`, separate process): 2025-shape OpenAI chunks of about 325 B, chunked transfer, **one HTTP chunk per event**, paced at 20 ms ± 30% jitter, ending with the finish chunk, the usage chunk and `[DONE]`.
- **Client** (Reviewer B's `client_cost.py`, plus one variant): main-thread CPU (`time.thread_time`; equal to `process_time`, single-threaded) per request, median of 2 runs after a warm-up; utilisation and reads (raw variants) or decoded events per request recorded for every row. Variants: `httpx_raw`, `httpx_decode`, `current` (the shipped `OpenAIUpstreamProvider`), `aiohttp_raw`, `aiohttp_decode`, and **`aiohttp_design`**: the §3.13 transport (hostname resolved by a pinned `AbstractResolver`, `socket_factory` dial guard, identity encoding with the encoded-body check, header bounds, no redirects) plus the shipped `SSEDecoder`.
- **Scenarios.** Events per request E ∈ {24, 200, 600} at 64 in flight (3,200 events/s on the worker), and E ∈ {24, 200} at 256 in flight (12,800 events/s, closer to a busy worker, §6.5).
- **Run.** One heavy-lock acquisition, 07:09–07:24Z, after 36 minutes in the queue; `nice 10`; host load 0.3–2.6 from other agents' unlocked work (each `*.env.txt` has the snapshot). Python 3.12.3, aiohttp 3.14.5 (C parser), httpx 0.28.1, uvloop 0.23.0. 28,416 authorised upstream requests, **0 errors and 0 bad-auth** in every row.
- Reviewer A's lock-step harness was also re-run unchanged on the same versions, for the reconciliation.
- Files: `rev2/bench/out/`; tables printed by `python -I rev2/summarize.py`.

### 6.3 Results, and the reconciliation of A's and B's numbers

Main-thread CPU µs per request, [utilisation], (reads or events per request):

| Scenario | E | in flight | httpx_raw | httpx_decode | current | aiohttp_raw | aiohttp_decode | **aiohttp_design** |
|---|---|---|---|---|---|---|---|---|
| P1 | 24 | 64 | 4,463 [0.47] (25) | 4,710 [0.53] | — | 925 [0.11] (25) | 1,462 [0.18] | **1,338** [0.17] |
| P2 | 200 | 64 | 20,932 [0.32] (201) | 26,021 [0.40] | 27,792 [0.42] | 5,213 [0.08] (201) | 10,220 [0.16] | **10,032** [0.16] |
| P3 | 600 | 64 | 54,473 [0.28] (601) | 71,121 [0.37] | — | 16,078 [0.08] (601) | 27,449 [0.15] | **28,890** [0.15] |
| P5 | 24 | 256 | 20,690 [**0.93**] (25) | 21,357 [**0.92**] | — | 466 [0.23] (25) | 734 [0.36] | **793** [0.38] |
| P4 | 200 | 256 | 34,450 [**0.87**] (201) | 36,264 [**0.94**] | — | 3,491 [0.24] (201) | 5,776 [0.36] | **6,404** [0.39] |

Linear model `c(E) = a + b·E` (least squares over the scenarios at one event rate):

| Per-worker event rate | Client | a: µs per request | b: µs per event |
|---|---|---|---|
| 3,200 /s (64 in flight) | httpx_decode | 2,402 | 114.9 |
| 3,200 /s | aiohttp_decode | 748 | 44.8 |
| 3,200 /s | **aiohttp_design** | **314** | **47.7** |
| 12,800 /s (256 in flight) | httpx_decode | 19,325 | 84.7 (core saturated, so understated) |
| 12,800 /s | aiohttp_decode | 46 | 28.7 |
| 12,800 /s | **aiohttp_design** | **28** | **31.9** |

What the numbers say:
- **Reads match events on paced streams** (25, 201 and 601 reads for 24, 200 and 600 events), so nothing is coalesced and the comparison is per event, as B asked.
- **Cost is linear in events per request, and per-event cost dominates.** aiohttp with the design transport costs 32–48 µs per event; httpx 85–115 µs. The shipped decoder is 11.5–18.3 µs of aiohttp's per-event cost (`decode − raw`), about 40%: it is the next lever, and the §3.4 rewrite gets a per-event budget in CI (§6.8).
- **httpx's per-request cost grows with the number in flight** (2.4 ms at 64, ≈19 ms at 256), and at 256 in flight httpx **saturates the core** (utilisation 0.87–0.94) where aiohttp uses 0.24–0.39. aiohttp's per-event cost **falls** as the event rate rises (48 → 32 µs), because each loop wake-up serves more ready sockets.
- **The design transport costs what plain aiohttp costs** (−124 µs to +1,441 µs, −12% to +11% against `aiohttp_decode`): the resolver and the dial guard run per new connection, not per event.
- **Ratios with the decoder, httpx ÷ aiohttp_design:** 3.5× (E 24), 2.6× (E 200) and 2.5× (E 600) at 64 in flight; 26.9× (E 24) and 5.7× (E 200) at 256 in flight. `current` ÷ `aiohttp_design` = 2.8× at P2.

**Reconciliation.** The reviewers' numbers differ because their workloads differ along three axes, and all three move the ratio:

| Method | Reads/request | Pacing | Decoder | Ratio httpx ÷ aiohttp, this host, 3.14.5 |
|---|---|---|---|---|
| Revision 1 / A gap 0 (burst: whole response in one write) | 23 vs **1** | none | no | 23–25× (A on 3.12.15: 22–29×) — an artefact of coalescing |
| A gap 5 ms, 32 in flight, **lock-step** (every stream's event lands in the same loop iteration) | 23 vs 23 | lock-step | no | 6.7–7.4× (httpx 3,501–3,861 µs, aiohttp 522–535 µs; A on 3.12.15: 11–13×) |
| B / this run, 20 ms **± 30% jitter** (independent streams), 64 in flight | 25–601 both | jittered | yes | **2.5–3.5×** (B on 3.12.15: 2.7–4.2×) |
| This run, 256 in flight | 25–201 both | jittered | yes | **5.7–26.9×** (httpx saturates the core) |

So: burst numbers are not usable; lock-step pacing amortises each loop wake-up over many streams and flatters the cheaper per-wake-up client; jittered pacing with the decoder is the realistic shape. **The honest range is 2.5–3.5× at moderate load, rising to 5.7× and more where a worker is busy, because httpx cannot hold 256 paced streams on one core.** aiohttp never loses, so the owner's Q5 condition holds.

### 6.4 CPU per streamed request: planning distribution, and the full path on develop

- **Planning distribution** (owner item 3 until F5 telemetry replaces it): 30% short answers of 24 events, 50% of 200, 20% long of 600; **mean 227.2 events**. Because the dispatch cost is linear in E, its mean cost is the cost at the mean; at OpenAI-style streaming an event is about one output token.
- **Dispatch scope at the mean:**

| Per-worker event rate | Client | CPU ms per streamed request | RPS per vCPU (0.75 cap) | RPS per $1,000-month | vCPU at 100k RPS | $ per month at 100k RPS |
|---|---|---|---|---|---|---|
| 3,200 /s | httpx_decode | 28.5 | 26 | 799 | 3,800 | $125,187 |
| 3,200 /s | **aiohttp_design** | **11.2** | **67** | **2,041** | **1,487** | **$49,001** |
| 12,800 /s | httpx_decode | ≥ 38.6 (saturated) | ≤ 19 | ≤ 590 | ≥ 5,142 | ≥ $169,417 |
| 12,800 /s | **aiohttp_design** | **7.3** | **103** | **3,131** | **970** | **$31,943** |

- **The whole qualified-shaped path on develop** (`rev2/devpath/`): develop's gateway_v2 in the dev profile (auth, the example plan, admission, input scan, routed dispatch on the shipped httpx provider, incremental output scan, finalisation, audit), one worker, against the same paced upstream; gateway **process** CPU per qualified request (HTTP 200, content, `[DONE]`, no error frame) from `/proc`, less an idle baseline. C4 serialises the dev provider to one upstream connection, so streams run one at a time (a pessimistic, low-batching condition), and the same condition was measured for dispatch alone:

| Run | Events per request | Qualified / sent | Gateway CPU per qualified request | Dispatch alone, same single-stream condition: `current` (httpx) / `aiohttp_design` | Pipeline (full − `current`) |
|---|---|---|---|---|---|
| D1 | 24 | 96 / 96 | **27.2 ms** | 8.0 / 4.0 ms | 19.2 ms (70%) |
| D2b | 200 | 24 / 24 | **640 ms** | 63.3 / 30.0 ms | 576 ms (90%) |
| D3 | 600 | 6 / 8 (two `504`: C4 queued them past the first-event bound) | **≈ 4.9 s** | ≈ 189 ms (linear estimate) / — | ≈ 4.7 s (96%) |

(D1, D2 and the single-stream dispatch rows ran in one heavy-lock acquisition, D2b and D3 in a second. The first D2's idle window overlapped the gateway's boot, so D2b repeats it with a clean baseline; D2 agrees once its idle rate is corrected (647 ms against 640 ms). Both are kept in `rev2/devpath/out/`.)

What this shows:
- **On develop, the qualified streamed request is dominated by the output controls, and their cost grows faster than linearly with events**: about 4.1 + 0.66·E + 0.0126·E² ms (exact through the three points; exponent ≈ 1.6). At the planning distribution the mean is **≈ 1.31 s of CPU per qualified streamed request**, of which dispatch is 4–30%.
- **Root cause** (`rev2/devpath/profile_e200.txt`, cProfile of four 200-event streams): the shipped egress loop runs the whole output detector over the **entire accumulated output on every delta**, `text.hits = list(self._detector(text.whole))` (`egress/stream.py:547-548`, wired for chat at `edge/chat_stream.py:450`). A stream of E deltas therefore canonicalises O(E²) characters (2,853 `canonicalize` calls over 1.16 M characters for the four streams, `detect/deterministic/canonicalize.py:417`). Parent revision 5 makes the **customer-regex** scan incremental (§5.2.4); this built-in detector path is not covered there.
- **It is outside F1a-3's scope** (egress, GW12/F1a-1), so this plan does not fix it; it requests an issue (hand-back) and makes the CI `qualified-path` job (§6.8) report it on every run. It is, however, **the binding CPU cost of a qualified streamed request today**: it caps a worker at about 1.2 RPS of 200-event streams (0.75 / 0.64 s), and at 50 events/s per stream about 5 concurrent streams per worker (§6.5).
- **What F1a-3 changes on this path:** aiohttp saves 4 ms (E 24) and 33 ms (E 200) per request in this single-stream condition, and far more where httpx saturates a core (§6.3): 5–15% of today's full path. The production qualified cost is measured by the CI job once F1a-1's scan work and F1a-3 land.
- Not representative of production in these ways: dev identity and audit sinks; the example plan's detectors; no guard model (GPU tier); one stream at a time (C4), so no wake-up batching.

### 6.5 Per-worker bounds with the measured costs

- **Today (C19):** the admission request slot, 14–15 per worker, is the stream bound: 3–5 RPS per worker at 3–5 s streams.
- **With `stream_slots()`** (§3.6.2) at `r_event` = 50 Hz:
  - **S_cpu, F1a-3's dispatch scope alone** (`c_event` = 31.9 µs, the busy-worker figure): 0.75 / (50 × 31.9 µs) = **470 streams**. This is what the transport itself allows a worker.
  - **S_cpu on develop's full path** (`c_event` ≈ 3.2 ms at E = 200, §6.4): 0.75 / (50 × 3.2 ms) ≈ **5 streams**. The output scan, not dispatch and not admission, binds a worker today; the declared `AMF_STREAM_EVENT_CPU_US` (from the CI job) makes `stream_slots()` follow whatever the pipeline really costs, so a worker sheds at the door (`upstream_inflight_limited`, 429) instead of slowing every stream on it.
  - **S_mem:** per-stream bytes = upstream 14.8 KB (aiohttp, plain, measured; ≈ 29 KB with TLS, using revision 1's httpx TLS delta) + downstream connection and request state (to be measured by the CI job; planning 64 KB) ≈ 100 KB. With `per_worker_rss` = 1 GiB: (1,024 − 150 base − 240 view − 10 cipher LRU) MiB / 100 KB ≈ **6,400**. Not binding.
  - **S_fd:** with `nofile` 65,536 and 12 workers per host, `connection_budget()` / workers ≈ 4,095; less the idle keep-alive cap (≤ 7 default origins × ⌈S/2⌉ ≈ 1,645) and halved: ≈ **1,200**. Not binding.
  - So `stream_slots()` = S_cpu: **470** on the F1a-3 transport alone, and whatever the pipeline's measured per-event cost allows once it is added.
- **Per-worker throughput** = `stream_slots()` / T: 470 / 5 s ≈ 94 RPS for the dispatch scope, consistent with the 103 RPS per vCPU of §6.4.

### 6.6 Qualified RPS within $5,000 a month

- **The fleet:** runbook §0.6 fleet A, 2 × c4-highcpu-16 gateways + 4 guards, **$4,475.30 a month**, **400 Poisson RPS**, bound by the guard tier (measured unit). Gateway CPU at the 0.75 cap: 32 vCPU × 0.75 = **24 busy vCPU**.
- **Gateway CPU capacity at the planning distribution:**

| Cost model | CPU per qualified streamed request | Gateway-tier RPS (24 busy vCPU) | Binding within $5k |
|---|---|---|---|
| F1a-3 dispatch scope, aiohttp (§6.4) | 7.3–11.2 ms | 2,140–3,290 | guard tier (400) |
| F1a-3 dispatch scope, httpx | 28.5–≥ 38.6 ms | ≤ 842 | guard tier (400) |
| **develop's full qualified path** (§6.4) | **≈ 1.31 s** | **≈ 18** | **gateway CPU** |

- **Qualified RPS within $5k = min(400, gateway-tier RPS).** With F1a-3's transport, dispatch is never the binding term (it leaves 85–90% of the gateway budget for the pipeline at 400 RPS: 24 vCPU / 400 RPS = 60 ms per request available). On develop's output pipeline, the gateway tier would serve about **18 qualified streamed RPS**, not 400: the egress rescan (§6.4) must become linear for the $5k fleet to reach its guard-bound capacity. That is the new issue requested in the hand-back.
- **RPS per $1,000-month:** fleet level 89 (400 / $4,475.30, guard-bound); gateway tier, dispatch scope, 2,041–3,131 (§6.4).

### 6.7 No architectural ceiling below 100k RPS (B-S5)

At the planning mean (227 events at 20 ms plus time to first token) a stream lasts about **T = 5 s**, so 100k RPS is about **500k concurrent upstream streams** (HTTP/1.1, one socket each) and as many downstream.

| Resource | Bound and how it scales | At 100k RPS | Caps below 100k RPS? |
|---|---|---|---|
| Gateway CPU | per request; linear in events for the F1a-3 scope, **quadratic** on develop's egress path (§6.4) | 970–1,487 vCPU for the dispatch scope; ≈ 175,000 vCPU (≈ $5.8M a month) on develop's full path | Not a cap (workers × hosts), but develop's egress rescan is a super-linear cost: new issue 4 |
| Stream concurrency | `stream_slots()` per worker, contract-derived from CPU, memory and FDs (§3.6.2), no literal | 500k streams / 470 per worker ≈ 1,064 workers for the dispatch scope (≈ 89 c4-highcpu-16 hosts at 12 workers each); with today's pipeline cost the CPU term binds first | **No** (develop's admission bound of 14–15 per worker would have needed ≈33k workers at 5 s; removed by A6) |
| Upstream sockets | one per stream; per host ≈ workers × `stream_slots()` | 500k | No: per host, a few thousand |
| Ephemeral ports per (source IP, provider IP:443) | 28,232 on this host (32768–60999); 64,511 with `net.ipv4.ip_local_port_range = 1024 65535` (deploy setting) | a host holds a few thousand streams in total | No: ≥ 5× headroom per host even if every stream goes to one provider IP |
| NAT | GCP Cloud NAT: ⌈P / 64,512⌉ NAT IPs per destination 3-tuple ([Google: NAT IP calculation](https://docs.cloud.google.com/apigee/docs/api-platform/security/nat-calculation)); AWS NAT gateway: 55,000 per unique destination per NAT IP, up to 8 IPs | 500k to one provider IP would need 8 Cloud NAT IPs or 2 AWS NAT gateways; per-host public egress IPs need none | No: a stated deploy sizing rule (P = concurrent streams per provider IP) |
| FDs | per worker `2 × stream_slots() + idle keep-alive ≤ connection_budget() / workers()` (boot check); `nofile` 65,536 | ≈ 1,000 of ≈ 4,000 per worker at 12 workers per host | No |
| Memory per worker | base + view (240 MiB at 100k orgs × 5 models) + cipher LRU (10 MiB) + `stream_slots()` × per-stream bytes (upstream 14.8 KB measured, plain; ≈ 29 KB with TLS) | ≈ 450–500 MiB | No: the view scales with orgs, not RPS (a shared-memory view is F1a-2's lever at 1M orgs) |
| Origin pools, DNS | ≤ 7 default origins + a bounded custom LRU per worker; 1 resolution per origin per worker per 30 s | — | No |
| KMS | W × kids per host per rollout | — | No |
| Projector | per changed row, not per request | — | No |
| Valkey on the request path | unchanged by this slice; F1d shards it | — | Out of scope (F1d) |
| Edge load balancer | 500k downstream connections; backend timeout ≥ 350 s (#99) | — | Deploy sizing (#99) |
| Provider quota | the customer's BYOK quota | — | Not gateway capacity (runbook §1.1) |

### 6.8 CI benchmark job (GitHub runners)

The owner wants heavy benchmarks on the Enterprise runners. The implementation PR adds `.github/workflows/perf-dispatch.yml` (a rule-6 change, Reviewer B signs off):
- **Triggers:** `workflow_dispatch`; nightly on `develop`; `pull_request` touching `gateway_v2/gateway_v2/dispatch/**`, `edge/wire/sse_decode.py`, `edge/chat_stream.py`, `edge/stream_timeouts.py`, `gateway_v2/pyproject.toml` or the benchmark folder.
- **Job `paced-dispatch`** (standard hosted runner, the baseline): scenarios P1–P3 (64 in flight), 3 repeats, upstream and client in separate processes; records runner type, CPU model, `nproc`, kernel and package versions. **Gate:** `aiohttp_design`'s per-event cost `b` and per-request cost `a` within 1.2× of the committed baseline for that runner class, and the decoder's per-event cost (`decode − raw`) within its budget; otherwise the job fails with the table.
- **Job `paced-dispatch-256`** (larger runner, optional): P4–P5 with the client and the upstream pinned to different cores (`taskset`), so the measured core can saturate without the upstream competing.
- **Job `qualified-path`** (larger runner): the `gateway-v2-prod` image (one worker), the recorder in paced mode, a load client drawing E from the planning distribution; reports gateway process CPU per qualified request and per event, stream-slot use, and the client-side p99 firewall overhead against a direct path (runbook §1.2 method). Its p90 per-event CPU and per-stream bytes are published as the declared `AMF_STREAM_EVENT_CPU_US` and the `per_stream_bytes` input (§3.6.2). **Gate: linearity in events** — the CPU per qualified request at 600 events must be at most 1.2 × 3 × the cost at 200 events (develop measures 7.7×, §6.4), so a per-delta whole-output rescan cannot return unnoticed.
- Artifacts: raw JSON plus `summarize.py` output; no secrets (random bearer per run). About 15 minutes standard and 30 minutes larger per run.

### 6.9 Not claimed here

The fleet curve and the Poisson pass point at fleet scale (runbook G-09, F1d/GW20b); the p99 overhead of the F1a-3 path (CI job and F1d); client-side TLS CPU at scale (revision 1 measured +550 µs per handshake upstream; pooled keep-alive makes handshakes per connection, and the 60 s max age bounds them at one per connection-minute).

## 7. Interface between edge and dispatch

- `UpstreamRequest.body` keeps `openai_body` and `openai_endpoint` (`upstream_body.py:105-111`).
- `UpstreamEvent` gains optional fields, so existing producers are unchanged: `usage: Usage | None`; `retry_after_s: float | None`; `sent: bool = True`; `billable: bool = True`; `provider_request_id: str | None`.
- `RequestHolds` gains `handoff_to_stream(slot)` (§3.6.1).
- The edge's pre-content budget and routed inter-event bound come from the routing decision (§3.7), so `StreamTimeouts` takes them per request.
- `RoutingContext` gains nothing; the candidate's entry comes from the pinned MODEL view (`routing_boot.py:109-149` style injection), the table built per view version.

## 8. Test plan, live exit, rollout

### 8.1 Offline tests (house idiom: seeded `random.Random`, ≥ 10,000 iterations for properties)

| Id | Test |
|---|---|
| T-C1 | AAD encoding is injective (property); a known-answer vector is pinned |
| T-C2 | Round trip opens; any AAD field changed by one byte → `InvalidTag`, for every field |
| T-C3 | A credential sealed for org A placed in org B's record → `credential_unreadable` for B, A untouched |
| T-C4 | Reuse rule; 10k random edits never repeat a `(DEK, nonce)` pair |
| T-C5 | KEK rotation: gate refuses until **every registered** gateway lists the kid (a registered-but-silent gateway blocks); the signed `required_kids` makes a gateway lacking a kid fail readiness; backlog bounded; retire refused while a record or a cursor lags; a forged heartbeat for another host is `NOPERM` |
| T-C6 | DEK rotation is atomic per record |
| T-C7 | Unknown kid, DEK tag and credential tag failures → the parent §6.2 markers; other orgs unaffected |
| T-C8 | D4: projector omission; unknown env field omitted at apply; production boot with a provider env key refuses |
| T-C9 | `FileKekProvider` refusals (mode, owner, duplicate, length, grammar) with the remedy line; `EnvKekProvider` refused in production and when both forms are set; boot KAT |
| **T-V1** | **v1 compatibility (owner, A-B1, B-B5).** A token written by control's production loader (`model_key.py`) decrypts with `ai_mesh_shared.llm_model_crypto.decrypt_api_key(token, fallback_secret=<DJANGO_SECRET_KEY>)` with `ZEROSHIELD_MODEL_KEY_ENCRYPTION_KEY` unset, exactly the call `gateway/ai_mesh_gateway/llm_router.py:917-924` makes; a token written before the release still decrypts in control; `shared/ai_mesh_shared/llm_model_crypto.py` is byte-identical to `develop` (hash pinned in the test) |
| T-K1 | `model_key.py` in production refuses: missing file, malformed key, env-only source, and a file key that differs from v1's derivation (the error names the remedy and prints only fingerprints); dev profile unchanged |
| T-K2 | `GatewayAPIKey` expand phase: reads new-then-old, writes old; switch phase: writes new, backlog re-encrypts, step-1 image still reads every row (migration test with both code versions) |
| T-K3 | Save-time validation stores `v2_serving_status`; the projector overwrites it with the reason it used |
| T-E1 | `OrgEgressGrant`: only `core.grant_org_egress` can create or change one; an org admin and a superuser without the permission cannot; every change writes an audit row; an expired grant is not projected |
| T-H1 | Projector and gateway hardening: `RLIMIT_CORE` = 0, `PR_SET_DUMPABLE` = 0 read back from `/proc/self/status`; env forms of `_FILE` secrets refused in production |
| T-L1 | Planted-key grep across every sink of §2.7 |
| T-L2 | AST gates: credential types; credential-flow scope; no `str(exc)` in dispatch logs; **transport call site** (§3.13) |
| T-D1 | Profile shaping table, including reasoning-class rename, budget rule and `reasoning_effort` mapping |
| T-D2 | Shaping never changes or adds a text value (property) |
| T-D3 | Decoder: LF, CRLF, CR framing; comments; BOM; every split point; closed delta-key vocabulary (5,000 unknown keys → dropped, counted, state bounded); typed content; surrogate join and lone-surrogate malformed; `finish_reason` map; `usage`; oversized event; usage chunk forwarded only when the client asked |
| T-D4 | Replayed vendor captures (§8.2) |
| T-D5 | A planted value in `reasoning_content`, `reasoning` and a Mistral `thinking` part is redacted before forwarding |
| T-D6 | Abort isolation (§3.5.1), kill latch, post-fallback, **non-stream client disconnect** |
| T-D7 | Codes: every §3.10 code renders its envelope, retry headers, console stage and SSE error frame |
| T-D8 | Classification: Google `API_KEY_INVALID`, `insufficient_quota`, 402, 429 with `retry-after`, 3xx not followed, `stream_not_permitted` |
| T-D9 | Pools: one pool per hostname when two names pin to one IP (pool table and SNI); default origins never evicted; custom LRU evicts only idle; generation bump closes idle and blocks reuse; max age |
| T-D10 | No HTTP-level retry (one recorder call per attempt); fallback only on the closed set, pre-content; `upstream_quota_exceeded` falls back; `routing_attempts_exhausted` carries the last cause's headers |
| T-D11 | Embeddings JSON path |
| T-D12 | Charging table of §3.5.2, row by row, including fallback |
| T-D13 | `/v1/completions` renders `text_completion` (non-stream and stream); SDK `completions.create` reads `choices[0].text`; refused legacy parameters |
| T-D14 | Stream slots: handoff releases the admission slot; contention-gated max-min rule; wait bound; table bounds; `stream_slots()` derivation refuses below 1 |
| T-D15 | DNS: single-flight (N concurrent lookups → 1 resolution), resolve timeout, refresh-ahead, stale-on-error re-checked against current policy, never stale on refusal |
| T-D16 | Keep-alive race outcome (§3.8) |
| T-S1 | Valkey ACL, including the per-host heartbeat user |
| T-S2 | SSRF address table (`ssrf_probe.py` rows plus all IANA special-purpose blocks), default and allowlisted, every embedded-IPv4 form, **through the aiohttp transport**, with IP-literal rows |
| T-S3 | DNS rebinding against the aiohttp transport: no socket ever opened to a refused address |
| T-S4 | Base-URL grammar corpus (≥ 200 hostile URLs) run against **both** the control and the gateway implementations with identical verdicts; ports; final-path assertion; redirects off |
| T-S5 | Private target needs grant **and** fence; org B cannot use org A's grant; keyless entries refused for public hosts and outside the fence at apply and at connect |
| T-S6 | Encoding: `Accept-Encoding: identity` on the wire; gzip, deflate, br and unknown encodings refused before any body read; RSS growth bounded; header and line size bounds |
| T-S7 | Dial guard: IP-literal URLs (v4, v6, v4-mapped) outside policy open 0 connections; a resolver answer outside the checked set opens 0 connections |
| T-P1 | Timeouts, cases a–k of §3.7 |
| T-O1 | `routing_decision` log line carries `request_id=` equal to the response's id |
| T-O2 | `pipeline_trace.routing` schema (keys, grammars, bounded `attempts`) and control ingest round trip |
| T-O3 | Compose properties of `gateway-v2-prod` (secrets, ulimits, read-only, user, ports, `depends_on`, healthcheck, `memswap_limit`) and the LB timeout, checked statically from the compose and nginx files |
| T-O4 | The recorder service and `AMF_PRIVATE_EGRESS_CIDRS` appear only in the test overlay (static check) |

Fault injection (recorder modes, extending `staging/t02/recorder.py`): statuses 401, 403, 402, 429 with `retry-after`, 500, 503, Google-400, OpenAI streaming-verification 400; malformed event, CRLF framing, comment keep-alives, BOM, unknown delta keys, typed content, split surrogates; mid-stream pause, first-event delay; disconnect mid-stream, no `[DONE]`, oversized event, 302 to `169.254.169.254`; slow headers, a huge error body, an unknown `finish_reason`; **a reset on a reused keep-alive connection (expected: §3.8)**; **a gzip bomb and a `br` body (expected: `stream_malformed_upstream`, no inflation)**; per-profile strict mode (Mistral-style 422 on an unknown field).

### 8.2 Vendor captures (T-D4; A-S9)

- **Who and with what:** the F1a-3 implementer records captures with platform-owned test accounts, one per vendor, whose keys exist only in CI secrets and the recorder's environment (never in a file, commit or log). The accounts are an owner item (§11.2).
- **What:** streaming and non-streaming chat, a tool call, `include_usage`, an error event, a 401, a 429, the reasoning-model budget and `stop` behaviour, the streaming-verification 400 (OpenAI), and the origin's idle-close time (for §3.6.4).
- **Scrubbing:** a scrubber replaces ids, keys, org names, fingerprints and request ids with fixed tokens; the repo's secret scan runs on `tests/fixtures/upstream_captures/`; a test asserts no fixture contains the CI secret values' fingerprints.
- **Refresh:** quarterly, and when a vendor changelog touches the OpenAI-compatible surface.
- **Gate:** merge requires the `openai` capture and the recorder's strict mode for every profile; a profile without its own capture ships with drop-defaults, marked `profile_unverified` in the console and in `amf_upstream_params_dropped_total`; **GA requires every capture**.

### 8.3 Live exit L36-1 (parent §1)

On the compose `gateway-v2-prod` with one worker and the test overlay. **Evidence directory:** `docs/plans/evidence/<date>-l36-1-f1a3/`, holding the recorder log, the before and after `/metrics` dumps, the console export, the `amf.trace` frames, the planted-key grep output, the revocation timings, and a `RUN.md` with commands, image digests and the per-run nonce.

| Step | Expect |
|---|---|
| 1 | One org per shipped profile (`api_base` → recorder `/p/<profile>`, strict per-profile mode). Valid console key, streaming and non-streaming, chat and `/v1/completions` → 200 in the right shape. The recorder shows the shaped body, the bearer and the nonce exactly once |
| 2 | Invalid console key → 401 `invalid_api_key`; the unauth counter +1 |
| 3 | Blocked prompt (chat and `/v1/completions` `prompt`) → declared refusal; recorder calls **0**; **`amf_upstream_attempts_total` delta 0** (A-S8) |
| 4 | Invalid provider key (recorder 401) → 502 `upstream_auth_error`, `x-should-retry: false`; console row `model_output` error |
| 5 | Trace, metrics (`amf_upstream_attempts_total{provider,ok}` +1 per request; `amf_upstream_failures_total` +1 for step 4) and the console row agree, joined by request id; the request id joins the recorder by the nonce |
| 6 | Planted-key grep (T-L1) over everything the run produced |
| 7 | Revocation on a **warm** worker: delete the key in the console; time to `model_unavailable` ≤ 21 s (record the value) |
| 8 | Console served status (B-S6): a `provider_unsupported` row and a `base_url_refused` row show "not served by the v2 gateway: <reason>", and a request for each gets `model_unavailable` with the same reason |
| 9 | Revocation on a **cold** worker (B-S11): restart the worker, revoke within its first state cycle, record the time; compare with F1a-2's stated bound |
| 10 | A 30 s mid-stream pause in a reasoning-class stream completes with keep-alives observed by the client (T-P1 live) |

### 8.4 Rollout and rollback (v1-safe)

0. **Baseline (no change).** Record v1's effective model-key fingerprint from the running v1 container with v1's own code (`docker compose exec gateway python -c` calling the shared builder exactly as `llm_router.py:917-924` does, printing only `sha256(key)[:8]`), and v1's reload line (`N valid models … 0 invalid dropped`).
1. **Control release (v1-safe).**
   - `model_key.py` (control-only), reading the `_FILE` key that must equal v1's derivation; no `MultiFernet`; no rotation task.
   - `GatewayAPIKey` **expand** phase (reads new-then-old, writes old).
   - `OrgEgressGrant` with its permission and audit; save-time validation and `v2_serving_status`.
   - **v1 compatibility check, a gate for this step:** (a) control's boot check passed (the file key equals v1's derivation); (b) control writes a canary token over a random 32-byte plaintext (not a provider key) with the new loader, and `docker compose exec gateway python -c '<LLMRouter._prepare_reload_entry({"litellm_params": {"api_key_encrypted": <canary>}})>'` with v1's own code and environment returns the canary plaintext; (c) v1's next reload line shows the same valid-model count and `0 invalid dropped`; (d) one real BYOK chat through v1 succeeds. Any failure → roll back.
   - **Rollback:** no stored token changed (same key bytes; `GatewayAPIKey` still written with the old key), so the previous image reads everything. Nothing in `shared/` changed, so v1 is untouched either way.
1b. **Control release: `GatewayAPIKey` switch** (writes new, re-encrypt backlog). Rollback to step 1's image is safe (it reads both keys).
2. **Gateway release.** KEK file mounted; `amf_gateway_cred_kids` exported; registry entry added by deploy; dispatch code present; MODEL applied only behind F1a-2's flags and rollout floor. Without MODEL the provider stays the stub, which fails closed. Rollback to any image at or above F1a-2's step-1 floor is safe.
3. **Projector.** Model-key `_FILE` mounted **read-only, decrypt only**; signed fleet record `{encrypt_kid, required_kids}` published; the kid gate passes; MODEL emitted (F1a-2 flag). **Rollback:** stop the projection and remove the projector's model-key mount. The projector's copy is a second holder of the same key bytes; it never re-encrypts control's tokens, so neither control nor v1 is affected. F1a-2's no-rollback rule for re-hydrators after its step 2 applies unchanged.
4. **L36-1 live run**, then promotion.
- **Deferred until v1 retires** (owner): control model-key rotation to a random key (`MultiFernet`, re-seal backlog; new issue 1); `GatewayAPIKey` contract phase (new issue 2).
- **KEK incident.** Stop the projector, rotate the KEK and every DEK, ask customers to rotate provider keys; the runbook says cryptography cannot un-leak a key exposed while a KEK was compromised.

### 8.5 Acceptance (all required)

- T-* green; L36-1 steps 1–10 green with the evidence directory complete;
- the full gateway_v2 suite and the control suites (ratchet) green on GitHub runners (CI), plus the locally recorded affected suites per the current owner rule;
- the CI benchmark job (§6.8) on the implementation: `aiohttp_design` within 20% of this plan's P1–P3 `aiohttp_design` rows (a = 314 µs, b = 47.7 µs at 64 in flight) when run on this host class, and within 20% of the baseline the CI job records for its runner class on the implementation's base commit row on the **paced** workload, `c_event` and `per_stream_bytes` recorded as the declared contract inputs, or the regression explained;
- two agent reviews.

## 9. Interface needed from F1a-2

| # | Needed from F1a-2 | Parent § |
|---|---|---|
| I1 | MODEL kind, key-stored `{ns}:model:<org>`, one record per active org and a tombstone on offboard | §6.5, §6.11 |
| I2 | Record body carries `dek`, `models[].cred`, `models[].row/alias/provider/base_url/model`, `models[].caps` and `egress` **as raw strings** (§2.4) | §6.7 |
| I3 | Applier calls `CredentialOpener.check_org(record)` and maps the result onto `unavailable(version)` or per-entry omission | §6.2 |
| I4 | Pinned per-request `ModelView` with `org_id`, `version`, `lookup(alias)` (O(1), casefolded), `sealed_dek`, `unavailable`, `egress`, `entries_for_routing()` | §6.3, §6.9 |
| I5 | `ModelEntry` exposes the opaque `SealedCredential`, never plaintext; **no precomputed AAD** (B-S4) | §6.4 |
| I6 | `OmittedEntry.reason` from the closed vocabulary of §3.10 | §6.5 |
| I7 | Projector round hooks `seal_rows(org, rows, previous_record)`, respecting the pre/post-heartbeat split, the backlog limit and the per-tenant budget (#125) | §6.6 |
| I8 | A projector-private memo may use `s` | §6.6 |
| I9 | The worker's applied MODEL `feed_seq`, readable by the dispatch layer for the heartbeat | §6.10 |
| I10 | Breaker counts the omission reasons | §6.6 |
| I11 | Candidates per alias bound `routing_max_candidates()` | §6.7 |
| I12 | **Optional capability field** `caps = {reasoning: bool \| null, stream_upstream: bool}` per model, sourced from control (A-Q6, A-S6), added in F1a-2 revision 2 to avoid a second migration | §6.7 |
| I13 | The per-worker view RSS at 100k orgs × 5 models, so the contract's `per_worker_rss` and `stream_slots()` memory term use one agreed number (B-S4) | §6.4 |
| I14 | The cold worker's single-record fetch bound (B-S11), which §2.9's revocation bound inherits | §6.2 |

### 9.1 Conditions on approval (A-S10)

This revision can be approved only as a design whose §9 and §12 are re-traced after (a) F1a-2 revision 2 (#118: 8 blocker findings, including the projector source read, the MODEL heartbeat and the LRU; I1–I14 may move) and (b) parent revision 6 (#103), whose §7 this plan traces and which must accept amendment A6 (stream slots) and the admission handoff. A delta review of §9 and §12 after both is a merge condition.

## 10. Requirements I think need amending (with evidence)

| # | Requirement | Issue | Proposal |
|---|---|---|---|
| A1 | §7.1: the AAD tuple ends in `kid`; the reused triple is `{kid, nonce, ct}` | With a DEK, the KEK `kid` in the credential AAD would force re-encrypting every credential on KEK rotation | `kid` rules apply to the DEK wrap; the credential AAD binds `dek_id`, `upstream_model` and `v`; reused tuple `{v, n, ct, s}` |
| A2 | §7.6: "keyless rows are omitted" | Ollama is keyless by default (`core/models.py:1553`) | Keyless only under a staff `private_target` grant, enforced at projector, gateway apply and connect (§4.3) |
| A3 | §7.5: reuse `provider_unreachable` | The chat path already uses `upstream_unavailable` for connect and read failures | `provider_unreachable` = DNS failure (pre-send); `upstream_unavailable` = connect or read failure |
| A4 | Missing from §7: mid-stream bounds | Gap 0.8 s and edge 1.0 s cut real streams (C13); idle 3.0 s and pre-content 60.18 s (C20) | The §3.7 table, its invariants and T-P1 |
| A5 | Missing from §7: proxy environment | C3 | `trust_env=False`, boot assertion, test with `HTTPS_PROXY` set |
| A6 | Missing from §7 and parent §5 (admission): stream concurrency | The admission request slot, sized for a 20 ms service time, is held for the whole stream (C19), capping a worker at 14–15 streams | Separate `stream_slots()` with the handoff (§3.6.1); parent revision 6 must accept it |
| A7 | §7.4: "per-org in-flight cap per origin" | A fixed share refuses at once on an idle worker (A-Q9) | Contention-gated max-min share at the worker's stream slots (owner, Q9) |
| A8 | §7.1: "Fernet hash-derivation refused in production" and the separate model key | v1 decrypts the same tokens with the derived key (C15); owner freeze | Runtime derivation refused in control; key **value** stays v1's derivation until v1 retires; rotation then (new issue 1) |

## 11. Owner decisions and open items

### 11.1 Recorded (12 Nov)

| # | Question | Decision |
|---|---|---|
| Q1 | KEK custody | Accepted: KMS-wrapped keyring; break-glass split between two owners; KEK 90 days, DEK 30 days; destroy a KEK one cadence after retirement (loses nothing: state is re-sealed from control); KEK rotation is a fleet rolling restart |
| Q2 | Refuse an env KEK in production | Accepted; also refused when both forms are set (§2.13) |
| Q3 | Keyless local ollama (A2) | Accepted, enforced at apply and connect (§4.3) |
| Q4 | Who grants private egress | Accepted: platform staff, via a dedicated permission with audit and optional expiry (§2.5) |
| Q5 | HTTP client | Accepted conditional on the paced re-measurement: aiohttp still wins (§6.3); floor 3.14.4 (§3.13) |
| Q6 | Reasoning class | Accepted: name pattern for default-origin profiles; explicit capability field otherwise (§3.2, I12) |
| Q7 | Legacy completions shape | **Render `text_completion` in F1a-3** (§3.3) |
| Q8 | KEK per cell | Accepted as a later slice; whether it is a pre-GA gate is open (§11.2) |
| Q9 | Per-org share of a shared origin | **Only under contention; an idle worker serves one org fully** (§3.6.3) |
| Q10 | Routing trace UI | Accepted: schema and ingest now, UI in the console track |
| Q11 | Quota code | Accepted, with the quota message and fallback eligibility (§3.10, §3.8) |
| Q12 | Custom-host allowlist | Accepted: hosts unrestricted by default; **ports restricted to 443 by default** (§4.1) |
| — | What 100k RPS means | Runbook §1.3 (scale-out architecture test; no design ceiling below it; not within $5k) |

### 11.2 Still needing an owner decision

1. **Q8 as a pre-GA gate** (B-Q8). Default proposed: decide cell and kid naming and LB affinity before GA; implement after F1d.
2. **Vendor test accounts for T-D4** (§8.2): one platform-owned account per shipped profile, keys delivered as CI secrets.
3. **The planning event-count distribution** used for the headline (§6.4): mean 227.2 events per streamed response. F5 should replace it with production telemetry.
4. **`AMF_STREAM_EVENT_CPU_US` and `AMF_STREAM_EVENT_RATE_HZ` as declared inputs with no production default**, like `q_safe` (§3.6.2).
5. **The CI benchmark job** (§6.8) on a larger runner (optional) for the 256-in-flight rows.
6. **Priority of the egress rescan** (§6.4, new issue 4): it binds the $5k fleet at about 18 qualified streamed RPS on develop's pipeline. Proposed: fix it in F1a-1 (parent revision 6) or as a GW12 follow-up before F1a-3's L36-1 run, since the CI `qualified-path` gate will otherwise fail on long streams.

## 12. Traceability: §7 → this plan

(Re-traced against parent revision 5's §7; to be re-traced against revision 6, §9.1.)

| §7 item | Section |
|---|---|
| 7.1 AES-256-GCM, fresh `os.urandom(12)` nonce | §2.2, §2.3 |
| 7.1 AAD canonical length-prefixed | §2.3 (A1) |
| 7.1 reuse only when plaintext and AAD are byte-identical | §2.3, §2.5 step 3, T-C4 |
| 7.1 explicit encrypt kid; switch gated on `amf_gateway_cred_kids{kid}` | §2.8, §3.12, T-C5 |
| 7.1 retire a kid only when unused and the cursor has passed | §2.8, I9 |
| 7.1 fixed rotation cadence | §2.8, Q1 |
| 7.1 projector hardened, `_FILE` secrets | §2.5 step 5, §3.14, T-H1 |
| 7.1 separate control key for `GatewayAPIKey` | §2.5, T-K2 |
| 7.1 Fernet hash-derivation refused in production | §2.5 (A8), T-K1, T-V1 |
| 7.2 no dataclass protocol / AST gate | §2.7, T-L2 |
| 7.2 key charset; omit `credential_invalid_format` | §2.5 step 1 |
| 7.2 exception type names only | §2.7, §3.12 |
| 7.2 planted-key grep | §2.7, T-L1, L36-1 step 6 |
| 7.3 `is_global` **or** `_blocked` | §4.2, T-S2 |
| 7.3 embedded IPv4 decoded and re-checked | §4.2, T-S2 |
| 7.3 private targets only via per-org entries, projector and connect | §4.3, §5, T-S5 |
| 7.3 userinfo, query, fragment, backslash, dot segments rejected | §4.1, T-S4 |
| 7.3 structural join; final-path assertion | §4.1 |
| 7.3 same grammar for `api_version`, region, segments | §4.1 |
| 7.3 recorder only through a test overlay | §3.14, T-O4 |
| 7.3 `http` only for org-scoped private entries | §4.1, §4.3 |
| 7.4 pools keyed by origin; default origins never evicted | §3.6.4, T-D9 |
| 7.4 custom origins per org capped | §3.6.4, §5 |
| 7.4 pool-acquire bounded by the request deadline | §3.6.3, §3.7 (slot wait) |
| 7.4 `trust_env=False`, pinned dialling, no redirects, no default auth header | §3.13 (A5), T-S7 |
| 7.4 per-org in-flight cap per origin | §3.6.3 (A7), T-D14 |
| 7.4 refcounted LRU pool table | §3.6.4, T-D9 |
| 7.5 a row per code | §3.10, T-D7 |
| 7.5 `upstream_auth_error` 502 no-retry; `provider_unreachable` | §3.10 (A3) |
| 7.5 `PinnedRefused` declared code | §3.9, §3.10 |
| 7.5 refusal keeps its candidate | §3.10 |
| 7.5 `routing_internal_error`, `model_not_found`, D4 classified | §3.10 |
| 7.6 shaping | §3.2, T-D1 |
| 7.6 SSE comments and `\r\n\r\n` | §3.4, T-D3 |
| 7.6 Google bad-key 400 → auth error | §3.9, T-D8 |
| 7.6 reasoning first-event timeout up to 120 s | §3.7, T-P1 |
| 7.6 embeddings non-SSE path | §3.11, T-D11 |
| 7.6 keyless Bedrock refused like D4 | §2.5 step 1 |
| 7.6 inactive and keyless rows omitted | §2.5 step 1 (A2) |
| 7.6 vendor quirks replayed from captures | T-D4, §8.2 |
| 7.7 `pipeline_trace.routing`, no `base_url` | §3.12, T-O2 |
| 7.7 `amf_upstream_failures_total{provider,code}` | §3.12 |
| 7.7 L36-1 deltas from a single worker | §3.12, §8.3 |
| 7.7 `routing_decision` logs carry `request_id` | §3.12, T-O1 |
| 7.8 gateway Valkey user read-only; ACLs | §3.14, T-S1 |
| 7.8 Ed25519 follow-up | §3.14, §2.11 |
| 7.9 `_FILE` secrets, `ulimits.core: 0`, `127.0.0.1` port, `depends_on` healthy, `AMF_FD_LIMIT` | §3.14, T-O3 |

---
_Generated by [Claude Code](https://claude.ai/code/session_01SDCVQWYGFYf4ZGcAYxkEAy)_
