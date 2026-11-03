# GW36 — Production boot wiring and local guard model (plan)

Status date: 2 November 2026. Branch `suraj-revamp`. Card text: runbook v3.1 §00.6 (GW36). This plan
is the Phase 2 output required by `CLAUDE.md`; nothing below is implemented until the Phase 3/4
reviews have challenged it.

## 1. Card

| Field | Value |
|---|---|
| Depends on | GW05c, GW06, GW08, GW10, GW11 (all DONE local half) |
| Required by | GW20, GW21, GW23 (and, in practice, the live tests of GW31, GW32, GW33, GW34) |
| Objective | `edge/serve.py` serves real traffic with no dev flags, and local guard inference is real |
| L36-1 | Production boot with no dev flags: a valid key gets a completion; an invalid key gets a declared 401 |
| L36-2 | Labelled injection corpus through the guard: recall and FPR at or above the T01-signed floors |

## 2. Owner-decision defaults adopted for this card (2 Nov 2026)

The owner asked to proceed on defaults. They are recorded here and in the progress file; none of
them is a signature.

| # | Decision | Default used |
|---|---|---|
| 0.7-1 | In-flight kill switch | Cut at the next chunk with the terminal error frame (`InFlightKill.CUT_NEXT_CHUNK`) |
| 0.7-2 | Freshness vs ride-through | `RV_STATE_FRESH_MS` 5000, `RV_STATE_PG_GRACE_MS` 16000 |
| 0.7-4 | Holdback trade-off | Accept the shipped trade-off table (uuid/sha256/url/base64 may hold 6) |
| 00.7-7 | Egress | Excluded from the $5k envelope, reported separately |
| 00.7-8 | Tail trim | ≤ 0.1% of samples, run fails above |
| 00.7-9 | Holdback in SLO | p99 excludes holdback; held-tokens histogram published alongside |
| 00.7-10 | Surface scoping | Confirmed (GW26) |
| 00.7-11 | MCP URL | Keep `/gateway/{org}/mcp/{srv}` (GW32) |
| 00.7-12 | Scan Detail | Six stages, auth and rate_limit real (GW35) |
| GW36 | Guard model | Llama Prompt Guard 2 22M, ONNX, CPU; semantic action FLAG until a recall/FPR floor is signed (C6) |

## 3. Discovered architecture (evidence)

Production boot today (`edge/serve.py:212-257`, AMF_DEV_PROFILE unset) wires every dependency to a
fail-closed default: `fail_closed_resolver`, `unresolved_snapshot_source`, `fail_closed_admission`,
`fail_closed_budget`, an empty `StubProviderClient`, `empty_catalog_source`, `no_findings_detector`,
`MinimalOutputResolver`, a static metrics registry. Every request refuses.

The dev profile (`edge/dev_profile.py`, `edge/dev_components.py`, `edge/dev_*.py`) wires the real
pipeline against env-declared inputs: one static bearer, a plan file plus an unsigned Redis poll,
env-declared `q_safe`/budget, the echo provider or one env URL, a static model list.

Production-shaped pieces that already exist but are only constructed by tests:

| Piece | Location | Notes |
|---|---|---|
| `CachingIdentityResolver(cache, fetch, key_namer)` | `edge/identity_resolver.py:170` | default `key_namer=bearer_key` passes the RAW token |
| `IdentityCache`, `principal_of`, `identity_applier` | `admit/identity.py` | negative cache, single-flight, stamp-gated |
| `ValkeyStateStore`, `require_bounded_client` | `runtime/store_valkey.py` | HMGET for KEY/KS/BUDGET, MGET for PLAN |
| `FeedReader`, `StateSynchroniser`, `NudgeListener` | `runtime/state_feed.py`, `state_task.py`, `state_nudge.py` | nothing schedules `drain_all` |
| `StampView` | `runtime/state_stamp.py` | `build_app(state_view=)` gates `/readyz` |
| `plan_applier`, `ReplicaSnapshot(stamp=)` | `plan/delta.py:130`, `plan/snapshot.py` | signed PLAN record → compiled plan |
| `killswitch_applier` | `admit/killswitch.py:261` | KS snapshot (request-path use is GW29) |
| `StateWriter`, `ValkeyPublisher`, `Rehydrator` | `state_control/` | writer API used by tests only |
| `Publisher.from_contract` | `edge/publisher.py:228` | production-grade |
| `HttpProviderClient` | `dispatch/http_provider.py` | posts `{"model","text_deltas","stream"}` |
| `SemanticDetector` | `detect/semantic/detector.py` | async `classify_input`; not imported by `edge` |
| PG2-22M ONNX backend + tokenizer windowing | `docs/plans/evidence/2026-09-23-runbook-v2-validation/rvproto/` | prototype, reusable |
| Labelled corpus | `tests/detection_corpus/{malicious,benign}.jsonl` | 323 + 425 rows, train/eval split |

## 4. Gaps found (each verified in code)

| ID | Gap | Evidence | Confidence |
|---|---|---|---|
| G1 | Nothing outside tests writes signed KEY records; control writes v1 `auth:apikey:{sha256}` only | `control/.../signals.py:77-121`; `StateWriter.key_add` has no non-test caller | High |
| G2 | Control writes an UNSIGNED plan document to `{rv2}:plan:{org}`, the same key the signed PLAN feed owns | `control/.../firewall_v2_publish.py:56-58,281`; `runtime/store_keys.py` `record_key(PLAN, org)` | High |
| G3 | `bearer_key` returns the raw token; control keys by `sha256(raw).hexdigest()` | `edge/identity_resolver.py:135-162`; `GatewayAPIKey.hash_raw_key` | High |
| G4 | Unknown key → 503 `shared_state_unavailable`, never 401 (L36-1 needs 401) | `edge/openai_chat.py:157-158,341,548` | High |
| G5 | No process schedules the state synchroniser or nudge listener; `build_app` lifespan is a no-op | `edge/app.py` lifespan; dev uses `DevServedApp` | High |
| G6 | Freshness stamp is written only by a re-hydrator covering all four kinds; without it every request 503s | `state_control/rehydrate.py:63,254` | High |
| G7 | No BUDGET applier, no per-org quota registry; `Principal.rate_per_s/burst` never used for GCRA in prod | `admit/quota.py`, dev hardwires 0.0 | High |
| G8 | No production provider config; `HttpProviderClient` body is not OpenAI-shaped; roles dropped at the fold (V-23) | `dispatch/http_provider.py:339-351`, `edge/wire/openai.py:442-500`, `edge/dev_upstream.py:44` | High |
| G9 | Guard backend scores `sha256(window)`; no onnxruntime/tokenizers; semantic detector unwired; `classify_input` emits nothing when `context.rules=()` | `detect/guard/local_onnx.py:115-129`, `detect/semantic/detector.py:205-223` | High |
| G10 | PG2-22M on CPU is ~88 ms/window (8 threads) vs `guard_wait_budget_s()` 0.100 s; input detector is a sync callable invoked per delta | `lanes/guard-bench/summary/cpu_latency.csv`, `edge/chat_deps.py:56`, `edge/input_enforcement.py:139-209` | High |
| G11 | Unknown OpenAI paths return 501 (V-15) | `edge/app.py:536-544` | High |
| G12 | `test_console_inventory` fails on clean HEAD (ledgers stale) | `docs/plans/evidence/2026-11-02-gw30-offline-gate/02b_*` | High |
| G13 | `PostgresControlDB.init_schema()` is only called by tests; nothing creates `amf_state_*` in a deployment | `state_control/pg.py:379` | High |
| G14 | `GuardRouter` routes remote guard pools only; with an in-process CPU backend it has nothing to route | `dispatch/guard_routing.py:94-152` | Medium |

## 5. Design

### 5.1 State path (identity, plan, kill switch, budget) — one authority

Control stays the system of record; signing stays in ONE implementation (`runtime/state_sig.py`,
used by `state_control`). Django must not import `gateway_v2`.

- **Projector (new, `state_control/projector.py`, run by the existing re-hydrator service or as its
  own `python -m state_control.projector`).** Reads control's Postgres (read-only SQL over the
  shared database: active `GatewayAPIKey` rows, org settings, published plan documents) and writes
  through `StateWriter`: `key_add` / `key_revoke` (sha256 key hash, org id, `rate_per_s`/`burst`
  derived from the key's TPM / RPM), `plan_set` (the document body), OFF kill-switch records,
  BUDGET records. It is idempotent per row version and runs on a period plus a Postgres
  `LISTEN` nudge if available. This closes G1, G13 (`init_schema` at projector start), and gives
  the re-hydrator all four kinds so the stamp is written (G6).
- **Plan document key (G2).** Control stops writing `{rv2}:plan:{org}`. It writes the unsigned
  document to a Postgres table (`core_v2_plan_document`, one row per org, monotonic `revision`) and,
  for the dev profile only, keeps writing `{rv2}:dev:firewall-plan` and a renamed per-org dev key
  `{rv2}:devplan:{org}`. The projector turns the row into a signed PLAN record. Dev profile reads the
  renamed key. No reader ever sees two formats under one key.
- **v2 org id.** The control org slug (already used by `AMF_V2_AUDIT_ORG_MAP`) becomes the v2
  `org_id` for every kind; the map stays only for the dev org alias.

### 5.2 Gateway state runtime (G5)

New `edge/state_runtime.py`: per worker, built from `AMF_STATE_VALKEY_URL` + `AMF_STATE_HMAC_KEY`
(same names the re-hydrator uses) — `ValkeyStateStore` (bounded client) → `FeedReader` →
`StateSynchroniser` with appliers {KEY: `identity_applier`, PLAN: `plan_applier`, KS:
`killswitch_applier`, BUDGET: new `budget_applier`} + `StampView`; a lifespan task drives
`drain_all()` on `next_delay_s` and runs `NudgeListener`. A generic production lifespan wrapper
replaces the dev-only `DevServedApp` responsibilities (lease acquire/return, audit writer, state
task). `build_app(state_view=stamp)` gates `/readyz`.

### 5.3 Identity (G3, G4)

- `hashed_bearer_key(scope)` = `sha256(bearer).hexdigest()`; never logs or stores the raw token.
- 401 vs 503: `CachingIdentityResolver` gains an outcome that distinguishes **absent credential**
  and **store answered: no such key / revoked** (→ new posture `invalid_api_key`, HTTP 401
  `authentication_error`, `code=invalid_api_key`, matching v1/OpenAI) from **state unverified /
  store unreachable** (→ 503 `shared_state_unavailable`, unchanged). The protocol keeps
  `resolve(scope) -> TenantIdentity | None` for back-compat; the refusal reason is carried by a
  new optional `resolve_outcome` the lifecycle prefers when present. The negative cache already
  refuses to answer while unverified, so a 401 is only ever issued against verified state.

### 5.4 Admission and quota (G7)

- Admission: the real GW19 `AdmissionController`; `q_safe` from `AMF_Q_SAFE` (DECLARED until
  GW20 measures it; refuse-to-start if unset — no default, no literal). Audit sizing from the
  shipped `AuditSink.calibrate` once wired, else declared env as in dev.
- Quota: per-principal `LocalGCRA` from `Principal.rate_per_s/burst` (cached per key, rebuilt on
  key delta) + per-org `BudgetLease` registry keyed by org, created on first use from the org's
  BUDGET record (pool tokens, generation). An org with no BUDGET record refuses with the narrow
  `budget_unavailable` (fail closed), and the projector always writes one.

### 5.5 Provider and catalogue (G8)

- Roles (V-23): carry the OpenAI message role through the fold (`CanonicalRequest.input_roles`,
  aligned with `input_message_index`), `GatewayRequest`, verify and the authorized entry, so the
  upstream body is rebuilt as `messages[]` with their original roles (masked text substituted).
- Production provider: `OpenAIUpstreamProvider` (promoted from `DevUpstreamProvider`, production
  names `AMF_PROVIDER_URL`, `AMF_PROVIDER_API_KEY`, `AMF_PROVIDER_MODELS`), non-2xx fails closed,
  OpenAI-shaped body, real SSE decode. One deployment-level upstream for every org; the catalogue
  is that model list. Per-org BYOK catalogue + routing is GW28 (named in the boot log, not faked).

### 5.6 Detection (G9, G10, G14)

- Deterministic GW09 detectors + per-org customer conditions read from the request's pinned plan
  (no dev ContextVar coupling).
- Real guard: `detect/guard/local_onnx.py` loads a model FILE (`AMF_GUARD_MODEL_PATH`,
  `AMF_GUARD_TOKENIZER_PATH`), verifies sha256 of model + tokenizer bytes against
  `AMF_GUARD_MANIFEST_HASH` (C35; mismatch = UNAVAILABLE), runs onnxruntime CPU with
  `tokenizers` (no truncation, `[CLS]+≤510+[SEP]`), score = softmax(logits)[1]. New optional
  extra `guard` (`onnxruntime`, `tokenizers`, `numpy`); the hash stand-in is removed from the
  production path and kept only as an explicit test double.
- Request path: the input scan gains ONE async semantic classification per request, off-loop on
  the `ScanExecutor`, over role-scoped segments, with `DetectorContext.rules` = the plan's
  PROMPT_INJECTION / JAILBREAK rules; findings merge with deterministic findings before the single
  resolver decision. Default semantic mode FLAG (C6). Over budget / not ready ⇒ UNAVAILABLE finding
  routed through the rule's `on_unavailable` posture, never clean.
- GuardRouter: wired only when `AMF_GUARD_BACKEND` names a remote pool; with `local_onnx` the boot
  log names it N/A (GW25 owns the GPU pool). TensorRT on L4 stays GW25.

### 5.7 Observability, audit, stores, MCP

Publisher with live state producers (synchroniser + stamp), audit sink + response store over the
state Valkey, MCP deps from production env names. Every dependency is named in one boot-log line.

### 5.8 Selection

`AMF_PROFILE=production` (explicit) selects this wiring; unset keeps today's honest fail-closed
table so a misconfigured host still refuses; `AMF_DEV_PROFILE=1` unchanged. Production refuses to
start on any missing required input (never falls back to a stub).

## 6. Slices (each ends with offline gates green + a live check)

| Slice | Scope | Exit evidence | Agent-days |
|---|---|---|---|
| S0 | V-15 404 `endpoint_not_found`; console-inventory ledgers regenerated | unknown path 404 live; lint test green | 0.5 |
| S1 | V-23 roles through fold → transform → verify → provider body | recording provider sees system/user/assistant roles, masked | 1–1.5 |
| S2 | Control: plan document to Postgres + dev key rename; projector (keys, plans, KS OFF, BUDGET) + `init_schema` | live: console key create → signed KEY in Valkey; save → signed PLAN; stamp written | 2–3 |
| S3 | Gateway state runtime + hashed identity + 401/503 split + per-org snapshot | L36-1 auth half: valid key resolves, unknown key 401, Valkey down 503 | 2 |
| S4 | Admission (`AMF_Q_SAFE`) + per-principal GCRA + per-org lease registry + BUDGET applier | burst → declared 429; org budget exhausted → `budget_unavailable` | 1.5–2 |
| S5 | Production provider + catalogue + `AMF_PROFILE=production` boot + compose service | **L36-1** live | 1–1.5 |
| S6 | Real ONNX guard + async off-loop semantic stage + corpus scorer | **L36-2** recall/FPR on eval split, latency per window | 2–3 |
| S7 | Promote dev parity tests to the production boot; boot-log/test table updates; evidence + docs | parity suite green on both boots | 1 |

Revised total: **11–15 agent-days** (up from 7–11: G1/G2/G13 put a control-to-state projector in
scope, which the card text did not mention).

## 7. Risks

- R1 Plan key migration: rolling control before the projector exists breaks the dev profile's
  plan feed. Mitigation: S2 ships the rename and the projector together; dev reads both keys for
  one release.
- R2 HMAC key distribution: gateway and re-hydrator must share `AMF_STATE_HMAC_KEY`; a mismatch
  fails the whole fleet closed (correct, but loud). Boot verifies one signed record or the stamp.
- R3 CPU guard latency: W ≥ 2 windows exceeds the 100 ms guard budget on CPU; long prompts become
  UNAVAILABLE → `on_unavailable` posture. Mitigation: FLAG default, published per-window latency,
  GPU path is GW25. This is a measured limitation, not a defect to hide.
- R4 PG2 quality: in-scope recall 35–40%, benign SQL 0.99 (C6). L36-2 has no signed floor (T01 was
  not signed by the owners); S6 publishes the measured numbers and does not claim a pass.
- R5 Org id change: moving from the dev alias to the slug can strand audit rows; the audit map
  stays for the dev org.
- R6 Bulk key updates skip Django signals; the projector reads table state, not signals, so this
  is covered.

## 8. Not in GW36

Per-org BYOK provider catalogue and routing (GW28); kill-switch enforcement on the request path
(GW29 — GW36 only feeds the KS snapshot); TensorRT/L4 and the guard pool (GW25); MCP production
URL (GW32); RAG (GW31).

## 9. Phase 3/4 review outcome (supersedes §5–§6 where they disagree)

Five contradictory reviewers attacked §3–§6. Every finding below was re-checked in code before it
was accepted (devil's-advocate pass); none of the gaps G1–G14 was a pure false positive.

### 9.1 Corrections to the gap table

- G6 is not a separate gap: the shipped re-hydrator already covers all four kinds and repairs an
  empty kind to a count-0 manifest (`state_control/pg.py:136-138`, `rehydrate.py:287-288`); it
  stamps once the tables exist. G6 reduces to G13, and `init_schema` belongs in
  `RehydratorService.build` (precedent `audit_control/service.py:200`), not in the projector.
- G4 is wider: the cache already separates "verified absent" (`None`) from "unverified"
  (`StoreDataUnavailable`, `admit/identity.py:166-179`); the resolver discards it
  (`edge/identity_resolver.py:225-235`), and six call sites test `resolve() is None` (chat,
  `openai_misc.py:415,753`, `openai_misc_models.py:109,136`, `openai_responses.py:223`,
  `mcp.py:205`).
- G12 also includes `tests/gates/test_check_tenant_scale.py::test_the_shipped_data_plane_is_clean`.
- G2 has a third reader: `dispatch/vector_credentials.py:131` names `{rv2}:plan:{org}`.

### 9.2 New findings accepted (verified)

| ID | Finding | Evidence | Fix (slice) |
|---|---|---|---|
| N1 | **`/metrics` crashes after the first non-503 pre-decision refusal.** `RejectionCounters` seeds only status 503; any other status adds a series, `exposition()` asserts `rendered == SERIES_COUNT` | `edge/counters.py:138-166`, `edge/series_count.py:84`, `edge/publisher.py:350-353`, `edge/outcome_metrics.py:52-55` | Seed a fixed status set and count it in `series_count` (S0) |
| N2 | Projector is outside the freshness proof: stamps stay fresh while the projector is dead, so a Django revocation can be served from RAM indefinitely and a new key can be 401'd against "verified" state | `state_control/rehydrate.py:284-316,542` | Projector heartbeat (source high-water) in `amf_state`; `_claimable` withholds the stamp past a lag bound; declare the new-key lag (S2) |
| N3 | `StateWriter.put` always bumps version/feed_seq; absence never means revoked | `state_control/writer.py:238-261,168` | Compare-and-skip inside the counters lock; diff projected inventory vs KEY records; revoke at `expires_at`; single leader via advisory lock; separate process (S2) |
| N4 | Quota protocol has no owner; GCRA is per worker; TPM is not requests/s; live pool counter has no production seeder | `edge/chat_deps.py:85-87`, `admit/gcra.py:1-4,76`, `runtime/store_keys.py:81-135`, `edge/dev_admit.py:250-254` | `evaluate/refund` gain `owner`; per-org lease registry; per-worker rate = rate / workers; rate 0 = no per-key limit (declared); pool seeded by the projector with CAS on generation = BUDGET `feed_seq` (S2, S4) |
| N5 | Customer conditions reach detectors only through the dev ContextVar; offloaded scans would lose it and fall back to the primary org | `edge/dev_components.py:304`, `edge/dev_plan_feed.py:55,93`, `edge/executor.py:115` | A detector factory seam `(plan, phase, surface) -> detector`, cached by `plan.version` (S5) |
| N6 | Provider body drops `max_tokens`/`n`/sampling/tools; `tool_call_id`/tool_calls/images lost at the fold; one abort handle per instance (cross-request abort); client per request; upstream `tool_calls` deltas dropped | `edge/dev_upstream.py:89-148`, `dispatch/http_provider.py:163-231`, `edge/wire/sse.py:726-728` | Message skeleton + path-substituted masked text; allowlisted fields incl. clamped `max_tokens`/`n`; per-request abort; pooled client; `tools` → declared 400 until GW34 implements tool-call deltas (S1) |
| N7 | Raw redis errors escape `drain_all` (kills the state task) and the cold identity fetch (→ 500, not 503) | `runtime/state_task.py:142-152`, `runtime/store_valkey.py:106-161`, `edge/routes.py:335-360` | Convert at the store adapter; supervise and restart the state task (S3) |
| N8 | Off-loop KEY applier (>32 records) mutates the unlocked `IdentityCache` | `runtime/state_task.py:145` | KEY applier always inline (S3) |
| N9 | A worker can serve stale kinds under a fresh global stamp; a just-booted worker answers `PlanUnknownTenant` (403) for orgs not yet applied | `runtime/state_task.py:146-152`, `plan/snapshot.py:79-80` | Per-kind verified gate (cursor ≥ floor, last good round < FRESH_MS) → 503; `/readyz` on the same (S3) |
| N10 | `/readyz` == `/health` and stays 503 until the first stamp; compose healthcheck uses it | `edge/app.py:530`, `docker-compose.yml:533-538` | Add `/livez` for Docker; `/readyz` for LBs; never refuse to start on a missing stamp (S3) |
| N11 | Identity refusal on `stream=true` is SSE 200 + error frame, so the SDK never raises `AuthenticationError`; 401 has no audit/trace/request id | `edge/routes.py:392-398`, `edge/audit_emit.py:74` | Render identity refusals as plain HTTP regardless of `stream`; mint `x-request-id`; `invalid_api_key` in the envelope, NOT in `POSTURE_CODES` (S3). Security-event audit of failed credentials → GW35 |
| N12 | Guard: wedges permanently after 5 s idle; manifest hash self-certifies; one classify per rule; budget pre-check assumes 100 windows/s; JAILBREAK can never fire (backend emits only PROMPT_INJECTION); `und`/CJK → UNAVAILABLE | `detect/guard/local_onnx.py:111-113,127,193-195`, `detect/semantic/detector.py:221-222,286,318`, `language.py:223-225` | Background probe instead of reply-only staleness; manifest required; one classify per request shared by the plan's semantic rules; measured windows/s under concurrency; one shared `InferenceSession`, small intra-op threads; semantic rules default `on_unavailable=flag`; semantic spans never feed REDACT (S6) |
| N13 | Trace/console cannot tell a detector UNAVAILABLE from a real FLAG/BLOCK | `audit/record.py:139`, `control/.../v2_audit_adapter.py:118-133` | Per-stage `unavailable` in the trace + adapter passthrough; fixed-name guard-unavailable counter (S6) |
| N14 | Gateway Publisher would publish frozen state metrics and duplicate the re-hydrator family | `edge/dev_metrics.py:62-63`, `state_control/service.py:210` | Live state adapter re-read per scrape; drop the re-hydrator family from the gateway (S5) |
| N15 | Control KS has a real `is_active`; per-org OFF writes are pointless (absent = off) | `control/.../models.py:859`, `admit/killswitch.py:227-249` | Projector mirrors engaged KS only (S2) |
| N16 | Plan body `org_id` must equal the record key; control skips unmapped orgs; an org with no config compiles an empty plan | `plan/delta.py:58`, `firewall_v2_publish.py:96-98,270-271` | Projector stamps the slug; platform floor default (§9.3) (S2) |
| N17 | Unknown path vs known path with wrong method both hit the 501 fallback | `edge/app.py:536-544` | 404 `endpoint_not_found` for unknown path, 405 for a known path with the wrong method, placeholders stay 501 (S0) |

### 9.3 Additional defaults adopted (owner said "consider the defaults")

| Topic | Default |
|---|---|
| Org with no saved firewall config | Publish the platform default plan (PII redact, secret/credential block, injection/jailbreak flag), same rules as `dev/plan.example.json` |
| Inactive / expired / unknown key | 401 `invalid_api_key` (runbook L36-1, OpenAI); v1 answered 403 for inactive/expired — registered difference |
| `tools` / `functions` in a chat request | Declared 400 `unsupported_parameter` until GW34 streams tool-call deltas back; never silently dropped |
| Per-key request rate | Not configured in control today (TPM only) → no per-key request GCRA in GW36 (declared in the boot log); org token budget enforced via the lease; per-key request limits are GW27 |
| Mid-stream key revocation / kill switch | `_NoControlSource` stays until GW29 (named in the boot log) |
| Model allow-list per key | GW28 |

### 9.3a Answers taken from the runbook (3 Nov 2026)

| Question | Runbook answer | Source |
|---|---|---|
| Which upstream for the S1/S5 live tests? | The **synthetic OpenAI-compatible provider recorder**, not a real provider. It records the bytes the provider received (the zero-calls-on-BLOCK proofs use it). It exists: `staging/t02/recorder.py` (stdlib; `POST /v1/chat/completions`, admin `/calls`, `/reset`, `/fault`). | §3 L1 staging ("synthetic OpenAI provider recorder"), T02, LGW11-2, L06-1 |
| When does a real provider come in? | Only at the T24 canary ("authorized real provider", official SDK through authorized BYOK). Customer BYOK is excluded from firewall cost/latency accounting. | T24, S37, cover "Customer model" |
| Which guard model? | Llama Prompt Guard 2 **22M**. 86M cannot meet the 20 ms budget for the ≤ 1,024-token band. | §0-A.2 (PG2-86M 5.94/12.37/19.00 ms) |
| Which runtime for GW36? | `local_onnx` on CPU is **development/CI only** (best 74 ms per window). Production is one owner process per L4 with an exact-shape 1×512 fp16 TensorRT engine (GW25). | GW08 C7/C35 correction, §10.5.4 |
| Which export? | fp32 ONNX exported from the pristine weights with parity checked against reference logits. **Not an int8/quantised export:** int8 flips 2–6% of decisions against fp32. Pinned bytes from the prototype: safetensors `5120e30b…`, ONNX `2bf20e0f…` (opset 17, onnxruntime 1.30, max logit diff 4.3e-6), tokenizer `6eb98335…`. Exporter: `rvproto/tools/export_onnx.py`. | GW08 correction, T09 step 47, `lanes/proto-builder/onnx-parity-22M-nopad.json` |
| Model identity | Hash the model and tokenizer bytes actually loaded and compare with a signed manifest; mismatch = UNAVAILABLE. | C35 |
| Tokenizer | Disable the shipped tokenizer.json truncation and padding at 512. | C6, GW10 correction |
| Semantic action | FLAG until a signed per-request FPR and recall floor exists; thresholds calibrated per window count. T01 floors are not signed, so L36-2 publishes measured numbers and does not claim a pass. | C6, C16 |
| Card order | Start with GW36 ("nothing else can be measured until production serves traffic"). | v3.1 review, §00.6 |

Not in the runbook: where to obtain the 22M weights. They are not in the repo or on this machine (the prototype's `models/` lived on the controller VM). Meta's Hugging Face repo is licence-gated. Whatever source is used, the bytes must match the pinned safetensors hash above, so the source is verifiable.

### 9.4 Revised slices

| Slice | Scope | Exit evidence | Agent-days |
|---|---|---|---|
| S0 | N1 metrics status seeding; N17 404/405; console-inventory + tenant-scale gate on clean HEAD | `/metrics` stays 200 after a 400/401 refusal; unknown path 404 live; lint/gate tests green | 0.5–1 |
| S1 | N6 provider body + production `OpenAIUpstreamProvider` | recording upstream sees roles, tool_call_id, clamped max_tokens; `tools` → 400; abort is per request | 1.5–2 |
| S2 | G2/G13/N2/N3/N4-seed/N15/N16: control plan table + dev key rename; projector process; `init_schema` in re-hydrator; heartbeat in freshness proof; fix the `store_ahead` xfail loop | live: console key create → signed KEY; save → signed PLAN; revoke → OFF; projector killed → stamp withheld | 3–4 |
| S3 | G3/G4/G5/N7–N11: state runtime, hashed identity, 401/503 at all six sites, plain-HTTP identity refusal, `/livez` | valid key resolves; unknown key 401 (stream and non-stream); Valkey down 503; `/metrics` consistent | 2–3 |
| S4 | N4: quota owner, per-org lease registry, admission from `AMF_Q_SAFE` | org budget exhausted → `budget_unavailable`; other org unaffected | 2 |
| S5 | N5/N14 + `AMF_PROFILE=production` boot + compose service | **L36-1** live | 1–1.5 |
| S6 | G9/G10/N12/N13: real ONNX guard + async off-loop semantic stage | **L36-2** published numbers (no signed floor exists) | 3 |
| S7 | Parity tests on the production boot; docs; evidence | parity suite green on both boots | 1 |

Revised total: **14–18 agent-days.**
