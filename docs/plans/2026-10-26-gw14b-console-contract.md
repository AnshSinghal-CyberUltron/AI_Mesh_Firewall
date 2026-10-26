# GW14b — Console and control-plane contract v2

**Status:** local half in progress. Inventory, schema, chat projection, audit emit, the dev Redis writer, the Scan Detail ingest, the admin-route ledger, the FirewallConfig field map, the v2 source split, a console save that publishes a plan, and a streamed output decision are in the tree. Standing note: runbook §0-A.4, "Local status (2026-10-26)".
**Card:** GW14b (C3), runbook §0-A.4.
**Source of truth:** `docs/AI_MESH_MASTER_RUNBOOK_v3_BACKEND_REWRITE.md` §0-A.4, §10.2.1, §10.2.3.
**Depends on:** GW05, GW13, GW14 (local halves closed).
**Required by:** UI05, UI06, UI07, UI12, T22, GW21, GW24.
**Date:** 2026-10-26.

This plan follows the runbook's implementation list in the order the card writes it. The chat-body projection is item 4. It is not the first commit.

---

## 1. What the card is for

§10.2.1 freezes one external contract: an OpenAI SDK client keeps working after a base-URL and API-key change. Everything else is unfrozen, including telemetry, `pipeline_trace`, the `zeroshield` object, `X-ZeroShield-*` headers, admin HTTP, and console-written config keys.

GW14b is the card that freezes that second contract so the console does not break on the v1 → v2 cutover. UI06 and UI12 cover the browser. They do not own the control-plane middle tier, and UI12 would only show the breakage on GW23's critical path.

The card's own exit is L14b-1 through L14b-6. A simulator badge that says ALLOW is a symptom of this card being unbuilt. It is not a separate feature.

---

## 2. Discovered architecture

Two stacks answer the console today.

| Path | Process | What the console gets |
|---|---|---|
| Login, policies, threat feed, Scan Detail | control `:8100` | `EnforcementEvent` rows written by the v1 telemetry drain in `control/ai_mesh_control/core/tasks.py` |
| Attack Simulator chat | `gateway-v2` `:8301` via the Vite `/v1` proxy | OpenAI JSON only. No `zeroshield`, no `pipeline_trace`, no `X-ZeroShield-*` |

`gateway_v2` already has the decision types the card names, and nothing on the serving path uses them for the console:

- `gateway_v2/gateway_v2/domain/decision.py` — `Decision` and `DecisionRecord` (disposition, per-finding dispositions, transformations, deciding rules, plan version, unavailable detectors).
- `gateway_v2/gateway_v2/audit/record.py` — `AuditRecord` with `schema_version = 1`, phases `input` / `output` / `admission`. `from_decision()` is the only mapping from a decision to stored bytes. `Finding.evidence` is deliberately not serialized.
- `audit_control` — Postgres sink (`amf_audit_record`) with `schema_version`. Built and tested. Not called from `edge/`.

`edge/openai_chat.py` has no reference to audit, `DecisionRecord`, `pipeline_trace`, or `zeroshield`. A chat that the dev profile redacts or blocks still returns only the OpenAI body (or the 400 error envelope). The preservation snapshot in `gateway_v2/tests/edge/test_v2_leaks_enforcement_preservation.py` (`_chat_json_ok`) locks that body to `id`, `object`, `created`, `model`, `choices`, `usage`.

The control-plane drain still applies the defaults the card tells us to remove. `_build_enforcement_metadata` in `control/ai_mesh_control/core/tasks.py`:

- `event.get("event_type", "request")`
- module falls through to `"1.1"`
- `event.get("risk_score", 0)` and a non-numeric risk becomes 0

`EnforcementEvent` (`control/ai_mesh_control/policy/models.py`) stores `action` plus a JSON `metadata`. The console reads `metadata.pipeline_trace` and `metadata.extra`. It does not read `disposition`, `plan_version`, or `deciding_rules`. That absence was measured on 2026-09-23 and is still true: `frontend/src` has no reader for those v2 names.

Layer rule that binds this card: `edge` is the only layer that builds an HTTP response. `egress` must not import `detect`. The control plane must not import `gateway_v2`. The adapter consumes the serialized audit record, not the Python type.

---

## 3. Current behavior, with evidence

| Claim | Evidence | Confidence |
|---|---|---|
| The console chat proxy targets gateway-v2 | `docker-compose.yml` `VITE_GATEWAY_PROXY: http://gateway-v2:8301`. Browser run of "What is the capital of France?" rendered `echo: What is the capital of France?` and gateway-v2 logged `POST /v1/chat/completions` 200 | High |
| That 200 is labeled ALLOW with no threat | `normalizeChatPipelineResult` uses `pipeline_trace.stages` only when `length >= 6`. Otherwise `inferFinalAction` returns `zeroshield.action` or, on HTTP 200, `"allow"` (`frontend/src/utils/liveGateway.js`) | High |
| No serving path writes an audit record | `from_decision` and `AuditRecord(` appear in `audit/record.py` and tests only. `edge/` does not call them | High |
| The September inventories are the ones the card names, and they are stale as a file path | `docs/plans/evidence/2026-09-23-runbook-v2-validation/lanes/frontend-verifier/{fields,cp-consumers,config-keys}`. The field dump cites `/home/contact_cyberultron_com/AI_Mesh_Firewall/...` | High |
| FirewallConfig's console surface is 65 fields | `config-keys/01_firewallconfig_fields.out.txt`: 63 model fields, minus `updated_by`, plus 3 method fields = 65 serializer-exposed | High, re-count on this tree before coding |
| The UI reads a handful of `X-ZeroShield-*` headers; v1 emits more | `15-zeroshield-headers.txt` and `fields/gw_keys.json` (19 emitted names). Frontend reads original-model, pipeline-request-id, rag-context-id, rerouted, routed-model, routing-policy-summary, routing-reason, routing-source, stream-scan-mode, and a signature v1 does not emit | High for the 2026-09-23 tree; re-grep before freezing |
| Twelve console event endpoints sit on the v1 drain | `cp-consumers/endpoint_keys.txt`: threat-feed, threat-feed detail, soc-kpis, module-kpis, module-trends, attack-vector-trends, module-charts, owasp-stats, rag-pipeline-kpis, rag-pipeline-trace, plus incidents and the notification path the card names | High |
| `/v1/responses`, `/v1/mcp`, `/v1/rag/query`, `/v1/vector/query` are 501 | `edge/routes.py` placeholders. Runbook correction 2026-10-22, still true after the 2026-10-26 enforcement card | High |
| Dev plan is a file, not `FirewallConfig` | `gateway_v2/dev/plan.example.json`, `AMF_DEV_PROFILE=1`. A policy saved in the console does not change this process | High |

Suspected issue, not yet a coded bug: a block today is HTTP 400 `invalid_request_error`. `inferFinalAction` treats a 4xx with no `zeroshield.action` as `error`, so a card-number block will not render as BLOCK even after a naive "add action on 200" patch. The error envelope needs the same projection as the success body.

---

## 4. Decision the card requires before any projection

The card says: decide, field by field, whether `zeroshield` and the `X-ZeroShield-*` headers are kept (then frozen as a CONSOLE bucket in the parity differ) or versioned (then the console migrates first).

**Decision: keep them. Do not version the console first.**

| Fact | Why it settles the choice |
|---|---|
| `v2_field_presence.txt` shows `disposition`, `plan_version`, `deciding_rules`, `transformations` are not read anywhere in the console | A versioned vocabulary has no consumer. Shipping it does not make Scan Detail honest |
| The console already reads `zeroshield.action`, `pipeline_trace.stages`, `pipeline_trace.final_action`, and the routing headers above | Keeping those names is the migration with the smaller blast radius |
| UI06, the versioned adapter, depends on GW14b | Requiring the console to migrate first inverts the dependency the runbook drew |
| §10.2.1 allows a response field the SDK can ignore, and forbids a header the SDK must send | An additive `zeroshield` object and response headers the SDK does not read stay inside the frozen wire contract |

What "keep" means, field by field:

| Console field | v2 source | Rule |
|---|---|---|
| `zeroshield.action` | `Decision.disposition` for the phase that decided the request | Same four spellings: `allow`, `flag`, `redact`, `block`. No fifth spelling |
| `pipeline_trace.final_action` | Same value as `zeroshield.action` | One writer. The browser must not recompute it |
| `pipeline_trace.stages[]` | Work this request actually did | `name`, `action`, `ran`, deciding rule, plan version. Latency only when measured |
| `pipeline_trace.plan_version` | `DecisionRecord.plan_version` | Absent means the record is invalid, not "unknown" |
| `X-ZeroShield-Action` | Same action | Header and body cannot disagree |
| Routing headers (`Original-Model`, `Routed-Model`, `Routing-Reason`, `Routing-Source`, `Rerouted`, `Routing-Policy-Summary`) | The route this request used | Omit the header when that fact does not exist. Do not send an empty string that the UI treats as a route |
| `X-ZeroShield-Stream-Scan-Mode` | The output mode the plan selected | Only on a streamed response |
| `X-ZeroShield-Pipeline-Request-ID` | The gateway request id already minted for the audit row | Same id as `AuditRecord.request_id` |
| `X-ZeroShield-RAG-Context-ID`, `X-ZeroShield-Signature` | No producer on this gateway | Not emitted. RAG stays on GW18. The signature header is already absent from v1 (`15-zeroshield-headers.txt`); freezing it as "not part of the contract" is the signed decision |
| v1-only headers (`Clamped`, `Detection-Tier`, `Factuality-Warning`, `Matched-Patterns`, `Redacted-Types`, `Review-Required`, `Scan-Only`, `Stream-Lifecycle`, `Routing-Policy-Id`) | No v2 producer | Not invented to fill the timeline. Each is either mapped onto a stage field in the inventory or listed in the expected-diff ledger |

Stages that are real on the chat path, and only these:

1. `auth` — key accepted or rejected.
2. `rate_limit` — admission or budget outcome.
3. `input_scan` — the input `Decision`.
4. `model_routing` — selected model.
5. `model_output` — provider called, or `ran: false` / `action: skip` when the input decision blocked before dispatch.
6. `output_guardrail` — the output `Decision`, or skipped when the provider was not called.

`kill_switch`, `policy`, and `circuit_breaker` are not separate stages. v2 applies policy inside the resolver, and a synthetic `policy: allow` is the default-fill L14b-2 rejects. Six real stages is also the count at which the current UI stops synthesizing (`stages.length >= 6`). That match is convenient. It is not a reason to add a seventh empty stage.

`flag` is a disposition the dev plan already produces for injection and jailbreak. The UI's `inferFinalAction` will surface it once `zeroshield.action` is `flag`. Do not collapse flag into allow.

---

## 5. Implementation sequence

Each item is one runbook bullet. Do them in this order. A later item may not paper over a missing earlier artifact.

### 5.1 Inventory (runbook bullet 1)

Re-run the three inventories against this tree and check them in as machine-readable JSON. The September files stay as historical evidence. They are not the contract, because their paths point at a different checkout.

| Artifact | Contents |
|---|---|
| `frontend-verifier/fields` | Every gateway-produced field the frontend reads, with file and the object it is read from (`zeroshield`, `pipeline_trace`, header, EnforcementEvent metadata) |
| `cp-consumers` | The event endpoints, the metadata keys each one reads, and the drain function that writes them |
| `config-keys` | The 65 serializer fields, the Redis names they publish to, and whether v1's gateway reads the published key |

A CI check fails when a new frontend read of a gateway field is not in the inventory. That is L14b-4's consumer half, started here so later bullets cannot drift.

Confidence this step is mechanical: high. Risk: the September scripts hardcode the old absolute path and must be pointed at this repo before they are trusted.

### 5.2 `DecisionRecord` schema v1 (runbook bullet 2)

The card's schema is the v2 audit record **plus** the projection the console renders. `AuditRecord.schema_version = 1` is only the audit half. Bump it when the projection fields join the stored record. Additions that do not change meaning do not bump, per the comment in `audit/record.py`; this change does change what a reader must understand, so the version becomes 2 only if an existing field changes meaning. Prefer adding a sibling object `console` on the serialized record so version 1 readers still see `outcome`, `phase`, and `detail`.

JSON Schema, checked in CI:

- Producer: `from_decision` and `admission` output validates.
- Consumer: a fixture decoder used by the control-plane adapter validates.
- L14b-4: a test emits one field that is not in the schema and expects CI failure. A test reads one field the schema requires and that the fixture omits, and expects failure. Both directions.

The schema does not include `Finding.evidence`. The audit module already refuses to store the matched text. The projection must not put the raw prompt or the raw secret into `pipeline_trace` either. Span offsets and the replacement token are enough. L14b-2 asks Scan Detail to show deciding rules and plan version, not the secret.

`edge/openai_chat.py` is at the 800-line module cap. The projection is a new edge module. The chat handler calls it. It does not grow inline.

### 5.3 Emit, then ingest (runbook bullet 3)

The adapter has nothing to read until the serving path writes.

**Producer, in `edge`, one record per phase.** Input decision, output decision, and admission (including budget refusal and overload shed) each become one `AuditRecord` through `from_decision` or `admission`. Enqueue is the existing bounded audit path. A full queue counts a loss. It does not block the response and it does not drop the console projection for the request that is in hand: the HTTP projection is built from the decision, not from a round trip to Postgres.

**Adapter, in the control plane.** A reader of the durable sink (`amf_audit_record`) translates a versioned record into an `EnforcementEvent`. It does not call `_build_enforcement_metadata`'s defaults.

Remove these silent fills for records that claim to be v2 (`v` present):

| Current default | Replacement |
|---|---|
| missing `event_type` → `"request"` | missing phase → the event is rejected and counted, not stored as a chat request |
| missing module → `"1.1"` | module comes from the route (`chat` → the firewall module the inventory maps, and only that mapping). No fallback |
| missing `risk_score` → `0` | no score field is written. A dashboard that needs a number shows "absent", which L14b-1 treats as a registered difference rather than a fake zero |

v1 telemetry keeps its current defaults until cutover. The adapter is a separate function. Mixing the two is how a v2 redact becomes "request / 1.1 / risk 0" and the threat feed lies.

The v1 drain is not deleted in this card. L14b-1 compares both.

### 5.3b Store one row Scan Detail can open (this step)

The producer and the translator are in the tree. Scan Detail still cannot see them. `ThreatFeedView` lists `EnforcementEvent` rows for the logged-in organization. The dev gateway writes `org_id=dev-org` onto `{rv2}:audit:dev-org`. The console org slug is `zeroshield`, not `dev-org`. A row stored under the wrong organization is invisible.

What this step builds:

- One row per `request_id`, not one row per phase. The feed collapses siblings and would otherwise keep the newer output `allow` and hide an input `redact`. The row's `action` is `zeroshield.action` on the record (the final action). Phase outcomes stay in `metadata.phases`.
- The translator still does not write `event_type` or `risk_score`. `module_id` is `1.1` only when the record says `route=chat`, and that value is copied into `metadata.module_id` because the feed filters that key.
- A missing phase or an `evidence` field is not stored. The stream cursor still moves past it so one bad record cannot stall the org.
- Organization: slug match on `org_id`. `AMF_V2_AUDIT_ORG_MAP=dev-org=zeroshield` is the local bridge and is set on the control service. An unmapped org is counted and not written into another tenant.
- The control process already drains v1 telemetry on a thread (`TELEMETRY_DRAIN_MODE=thread`). That loop calls the v2 ingest after the v1 drain. A Redis lock keeps the three gunicorn workers from inserting the same chat twice. The v1 drain function is not edited.
- The raw prompt is not copied into the row. The console object already omits it.

Not in this step: the admin-route ledger, the 65-field FirewallConfig map, and the GW21 shadow run. A streamed chat now keeps the output `Decision` and stores an output phase.

### 5.4 Console projection on the HTTP response (runbook bullet 4)

This is the piece the Attack Simulator reads directly, without waiting for the drain.

Attach the kept object in three places:

- Non-stream chat JSON, beside the OpenAI fields.
- The SSE terminal frame the UI already detects: empty `choices` plus `zeroshield` or `pipeline_trace` (`normalizeStreamChatPipelineResult`). Content deltas stay plain chat chunks.
- The block and budget error bodies, so HTTP 400 and HTTP 503 carry `zeroshield.action` and the stages that ran. A block's `model_output` and `output_guardrail` are `ran: false`.

Headers from the table in §4, on the same response. Body action and `X-ZeroShield-Action` are written by one function.

The OpenAI preservation test splits. The SDK keys stay byte-identical to `_chat_json_ok`. The console object is asserted separately. Deleting the snapshot's "nothing else" rule without a replacement assertion is not allowed: that test is what stops a later edit from putting the raw prompt into the body.

Local proof, before any claim that the timeline matches:

| Prompt | Expected action | Expected stage fact |
|---|---|---|
| Clean sentence | `allow` | input and output ran; provider called |
| SSN under the dev plan | `redact` | input stage `redact`; provider receives the masked text; UI action is REDACT |
| Luhn-valid card | `block` | HTTP 400; `model_output.ran` false; zero provider calls; UI action is BLOCK |
| "you are DAN" | `flag` | HTTP 200; action `flag`; provider called |

Browser check on the Attack Simulator at `:8180`, stream mode on and off. Scan Detail for these new events is in scope only after §5.3 has written a row. Until then, the simulator is the surface under test, and the threat feed is still the v1 store.

### 5.5 Admin endpoints (runbook bullet 5)

Inventory every control-plane route the console calls that is not an event read. The card names the set: breaker, RAG collections, db/bedrock connectivity tests, MCP discover/call/oauth mirror, the record-event callback, and instance heartbeat.

For each route the inventory finds, the deliverable is one line: **build on v2** or **retire**, with the product reason. Retirement is a ledger entry, not a 501 left in place for the UI to discover.

Known before that inventory, and not to be re-decided by guesswork during coding:

| Surface | Already true | This card |
|---|---|---|
| MCP discover/call/oauth | `/v1/mcp` is 501. GW17 owns the surface | Ledger: not a GW14b feature. L14b-3 cannot pass the MCP page until GW17 or an explicit retire |
| RAG collections and vector | `/v1/rag/query` and `/v1/vector/query` are 501. GW18 owns them | Same, for the RAG page |
| Breaker, heartbeat, record-event, db/bedrock test | Not in `gateway_v2/edge/routes.py` | The inventory decides. A route the simulator or the gateway page calls on every load cannot stay as an implicit 404 |

Do not add stub JSON that returns success. L14b-3's failure mode is silent breakage.

The ledger is `docs/plans/evidence/2026-10-26-gw14b/admin-ledger/routes.json`. `python -m lint.check_console_inventory` fails when a console or v1 caller in this set is missing, when the caller list drifts, or when a 501 placeholder is signed `build`. All 26 rows are `retire`. The control proxies for the breaker, RAG collections, db-test, and bedrock-test still forward to the v1 gateway. gateway-v2 does not grow a success body for any of them. The existing 501 placeholders stay until GW15, GW17, or GW18 replace them.

### 5.6 FirewallConfig writer (runbook bullet 6)

This is the writer side of GW05, owned jointly with this card. The dev-file plan is not a substitute.

For each of the 65 serializer fields:

- Map it to a rule, a mode, or an action the resolver can represent, or
- Mark it `unsupported`.

`unsupported` is rejected at save with the compiler's field-level message. A generic HTTP 400 fails L14b-5. Silent acceptance fails L14b-5.

The applied-version API returns the plan version each replica has loaded. UI07 reads it. L14b-6 publishes a plan at 70% load and requires every replica to report the new version inside the GW05 freshness bound. That load test is a live gate. The local half is the API plus a test with two in-process replicas and an injected clock.

Console policies start driving `gateway_v2` when a save publishes a plan the replicas load. The map, the rejection, and that publish are in. The dev process still boots from `dev/plan.example.json`, then replaces it when `{rv2}:dev:firewall-plan` changes. The poll is off the request path. A body the compiler rejects leaves the previous plan in place.

What landed:

- `control/ai_mesh_control/core/firewall_v2_map.py` classifies all 65 serializer fields. Mapped fields become rules the compiler accepts (`pii`, `jailbreak`, `prompt_injection`, `toxicity`, and output `pii` / `credential` / `exfil`). `enforcement_mode=audit` and action `rewrite` are rejected by name.
- An unsupported field is rejected only when the saved value changes, and turning the feature off is allowed. The settings form posts the whole row; rejecting an unchanged `rag_enabled: true` would block every other edit. The 400 body is `{field: "...field is unsupported..."}`.
- `gateway_v2/plan/applied.py` `report_applied` is the applied-version read. Two in-process replicas on one store both report the published version. A replica whose clock is past the freshness bound, while the store is unreachable, reports no version.
- A successful config PUT writes the mapped rules to `{rv2}:dev:firewall-plan` for the gateway org in `AMF_V2_AUDIT_ORG_MAP`. Each dev worker loads that body on its lifespan poll. The chat path still reads the in-memory snapshot. Rules that exist only in the example file (secrets, command phrases) are not form fields, so the first save stops applying them. Jailbreak in the form is a block rule.

### 5.7 Shadow stream (runbook bullet 7)

GW21 depends on GW14b. This card does not run the shadow. v2 records already use a different Redis stream (`{rv2}:audit:<org>`) from the v1 list `telemetry:events`, and the ingest does not write that list.

What landed:

- Every stored v2 row keeps `metadata.source = gateway_v2`.
- SOC totals, module charts for 1.1, and the hourly fact builder exclude those rows. The SOC response adds `gateway_v2.total_threats` beside the v1 total. The firewall page shows that count as "gateway v2, not added". The threat feed still lists the rows, so Scan Detail can open them.
- Adding the two totals is the double-count this split exists to prevent.

---

## 6. Exit tests

| Test | Where it can go green | What is still a live gate |
|---|---|---|
| L14b-1 | Fixture replay of a handful of decisions through the adapter, compared with a recorded v1 event for the same decision | Full C2 corpus through v1 and v2, then all 12 endpoints plus notifications, incidents, and gateway stats |
| L14b-2 | Unit render of ALLOW / REDACT / BLOCK / FLAG / UNAVAILABLE fixtures from the record, asserting the browser fixture does not fill a missing stage | Playwright on Scan Detail against the drained row |
| L14b-3 | The chat simulator cases in §5.4 | Production-bundle Playwright for MCP, RAG, and breaker. Those fail until §5.5's ledger retires them or GW17/GW18 exist |
| L14b-4 | Schema CI in §5.2 | Same test, kept as a required check |
| L14b-5 | One unsupported FirewallConfig field rejected with the field name in the body | The console shows that message, not a toast that says "HTTP 400" |
| L14b-6 | Two in-process replicas, injected clock, freshness bound | Publish at 70% load against real replicas |

A local half of this card is L14b-2 fixtures, L14b-4, the §5.4 browser chats, and the §5.5 ledger. On 2026-10-26 the settings page passed L14b-5. Scan Detail now renders the saved row: six stages, plan version, and deciding rule, with score and timing left blank when the record has neither. No saved row is unavailable. Calling the card closed still requires L14b-1, the unavailable fixture, L14b-3, and L14b-6.

---

## 7. Risks

| Risk | Why it is real | What the sequence does about it |
|---|---|---|
| Projection built from a second decision | A body assembled in the handler can disagree with the audit row | One function reads the `Decision` that was enforced. The audit record and the HTTP object are both outputs of that function |
| Raw secret in the trace | Scan Detail wants text. The audit module already banned `Finding.evidence` because the stream is retained | Offsets and the replacement only. A test scans the serialized record and the HTTP console object for the fixture secret |
| Browser still synthesizes | A trace with fewer than 6 stages falls through to `buildSimulatorStages`, which invents auth/policy/routing detail | Emit the six real stages. Do not lower the threshold in the same change as a short trace. A follow-up may trust `ran` on a shorter trace; that is a frontend change and it is not required if the six stages are real |
| v1 drain defaults applied to v2 rows | `_build_enforcement_metadata` will happily turn an incomplete record into request/1.1/0 | Separate adapter. A test feeds a record with no module and expects rejection |
| Module cap | `openai_chat.py` cannot absorb the projection | New module. The 800-line gate stays |
| SDK snapshot | Adding `zeroshield` changes `_chat_json_ok` if the test still compares the whole body | Split the assertion. SDK keys unchanged |
| Admin stubs | A 200 from an empty RAG route would make L14b-3 look green | Ledger or a real route. No success stub |
| Inventory drift | September scripts do not run on this tree | Re-run is bullet 1, and CI owns it afterwards |

---

## 8. What this plan does not do

- It does not implement GW17 or GW18. Their console pages are a retire-or-wait line in §5.5.
- It does not run the GW21 shadow.
- It does not change v1's `pipeline_trace.py` or the v1 drain defaults for v1 events.
- It does not start coding at the chat response. The first commit is the inventory.

---

## 9. Order of work, when implementation is approved

1. Re-run and check in the three inventories. CI fails on an unlisted frontend read.
2. JSON Schema for the audit record plus the console projection. L14b-4 tests.
3. Edge emits one record per phase. Adapter writes `EnforcementEvent` without the three defaults. Secret-absent test.
4. HTTP projection and headers. Split the OpenAI snapshot. Browser: allow, redact, block, flag.
5. Admin-route ledger. No success stubs. Done: 26 rows, all `retire`, CI on caller drift.
6. Field-by-field FirewallConfig map and save-time rejection. Applied-version report with two in-process replicas. A console save publishes the mapped rules; the running dev workers load them off the request path. The 70% load gate is still L14b-6.
7. Source marker so v2 rows cannot be summed with v1. Done: SOC and 1.1 charts exclude `source=gateway_v2` and report that count separately. The feed still lists the rows.

No v1 file changes, so no `PIPELINE-xxxx` changelog entry. A control-plane adapter is not a `gateway_v2` layer violation if it reads JSON only.
