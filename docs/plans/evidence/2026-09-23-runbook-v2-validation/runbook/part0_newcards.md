## 0.4 New cards required by the corrections

### GW14b — Console and control-plane contract v2 (C3)

| Field | Value |
|---|---|
| Phase | Observability / Surface |
| Depends on | GW05, GW13, GW14 |
| Required by | UI05, UI06, UI07, UI12, T22, GW21, GW24 |
| Primary owner | Backend + control-plane + frontend engineers |
| Objective | Keep every console and control-plane feature working across the v1 → v2 cutover by freezing and versioning the second customer-visible contract. |

**Why this card exists.** The console and control plane consume gateway data through six channels that §10.2.1 unfreezes. UI06 (versioned adapter) and UI12 (reconcile against v2) cover the browser end; no card owns the control-plane middle tier, and UI12 would only surface its breakage on GW23's critical path. The channels: telemetry → control-plane drain → EnforcementEvent (12 console endpoints plus notifications, incidents and gateway stats), pipeline_trace (137 keys, 87 read by the UI, v1's nine-stage order hard-coded), stage timings, the `zeroshield` body object and 10 X-ZeroShield-* headers (invisible to the OpenAI SDK and therefore to C1 and the WIRE bucket), control↔gateway admin HTTP (breaker, RAG collections, db/bedrock tests, MCP discover/call/oauth mirror, record-event callback, instance heartbeat), and 65 console-written config fields across 16 Redis names. The frontend reads 135 distinct gateway-produced fields and has zero references to v2's decision vocabulary.

**Implementation work.**
- Publish a machine-readable inventory of every consumed field and endpoint (evidence: `frontend-verifier/fields`, `cp-consumers`, `config-keys`).
- Define `DecisionRecord` v1 as a versioned JSON Schema (`schema_version` field) that is the v2 audit record plus the projection the console renders; producers and consumers are validated against it in CI.
- Build the control-plane ingestion adapter (v2 audit sink → EnforcementEvent or its successor). Remove the silent defaults in the current translation (event_type "request", module_id "1.1", risk 0).
- Decide, field by field, whether the `zeroshield` object and X-ZeroShield headers are kept (then frozen and added to the parity differ as a CONSOLE bucket) or versioned (then the console migrates first).
- Provide v2 equivalents of the admin endpoints the console uses, or retire the feature with a signed product decision.
- Own the writer side of GW05: a field-by-field mapping of the 65 FirewallConfig fields to Rule/Mode/Action or an explicit "unsupported" rejected at save; an API exposing the applied plan version per replica (UI07 "applied gateway policy version").
- During GW21 shadow, v2 records go to a separate stream (no double counting) that the console can display side by side.

**Live acceptance tests — required to exit.**

| Test | Live procedure | PASS | FAIL / stop |
|---|---|---|---|
| L14b-1 | Replay the C2 corpus through v1 and v2; query the 12 EventStore endpoints plus notifications, incidents, gateway stats. | Identical values within declared tolerance, or each difference is a pre-registered ledger entry. | Any endpoint silently changes meaning or returns defaults. |
| L14b-2 | Open Scan Detail for ALLOW/REDACT/BLOCK/FLAG/UNAVAILABLE fixtures. | Stages executed/skipped/unavailable, deciding rules, plan version and the C4 timing split render from the record. | Any field default-filled or recomputed in the browser. |
| L14b-3 | Playwright on the production bundle: simulators, Stage Timeline, MCP connector, RAG collections, breaker controls against v2. | Every feature works or is retired with sign-off. | Silent breakage. |
| L14b-4 | Emit a field not in the schema; read a field that is absent. | CI fails on both. | Drift passes. |
| L14b-5 | Save an unsupported combination in the console. | Rejected at save with the compiler's field-level message shown in the UI. | Generic "HTTP 400" or silent acceptance. |
| L14b-6 | Publish a plan at 70% load. | The applied-version API reflects every replica within the GW05 freshness bound. | UI shows success while a replica serves the old version. |

### GW16b — Production edge (C16, C20; absorbs T16)

| Field | Value |
|---|---|
| Phase | Hardening |
| Depends on | GW12, GW19 |
| Required by | GW20, GW22 |
| Objective | Make the edge transparent to streaming and never the capacity limit. |

**Why.** Measured: without an `upstream` block the repository's nginx opens 0.9986 new TCP connections per request and fails with HTTP 502 above ~420 req/s per edge→gateway pair (ephemeral-port exhaustion inside the container network namespace; host sysctls do not reach it). With upstream keepalive the same edge sustained ≥ 30,000 req/s at p99 < 0.4 ms. SSE is protected by `X-Accel-Buffering: no`, not by `proxy_buffering`.

**Tests.** L16b-1 upstream connections per request ≤ 0.01 at the qualified plateau · L16b-2 edge sustains ≥ 2× the fleet's qualified RPS adding < 1 ms p99 · L16b-3 fixed-interval SSE: every token arrives in its own read, max hold ≤ 1 ms, 30 ms injected hold detected · L16b-4 healthy stream > 350 s · L16b-5 drain one gateway during active streams per contract · L16b-6 unknown Host rejected, security headers present · L16b-7 regression gate: removing keepalive fails CI's edge test.

## 0.5 Corrected task index (replaces the §10.8 "Depends on" column)

Verified acyclic; every exit artifact is produced by a card in the depending card's closure. T24 moves before GW23 (C9). New cards: GW14b, GW16b. (The control-plane and worker external-AI work stays with GW24, which T08/T25 already route there; C13 itemizes it.)

| Card | Depends on |
|---|---|
| GW00 | — |
| GW01 | GW00, T02 |
| GW02 | GW00, GW01, T02, T04 |
| GW03 | GW00 |
| GW04 | GW00 (with the C1 type placement) |
| GW05 | GW04 |
| GW06 | GW03, GW04, GW05 |
| GW07 | GW04, GW05 |
| GW08 | GW03, GW04, GW07 |
| GW09 | GW02, GW08 |
| GW10 | GW02, GW08, T04 |
| GW11 | GW02, GW03, GW07, GW08 |
| GW12 | GW01, GW03, GW08, GW11 |
| GW13 | GW07, GW09, GW10, GW12 |
| GW14 | GW02, GW04, GW06, GW08, GW12, GW13 |
| GW14b | GW05, GW13, GW14 |
| GW15 | GW01, GW02, GW06, GW09, GW10, GW11, GW13, GW14 |
| GW16, GW17, GW18 | GW15 |
| GW16b | GW12, GW19 |
| GW19 | GW06, GW08, GW12, GW15 |
| GW20 | GW14, GW15, GW16, GW16b, GW17, GW18, GW19 |
| GW21 | GW02, GW14b, GW20 |
| GW22 | GW21, T21 |
| T20 | GW20 |
| T21 | GW19, GW20, T20 |
| T22 | GW01, GW05, GW07, GW13, GW14b, T20, UI12 |
| T23 | T04, GW01, T20, T21, T22 |
| T24 | GW22, T23 |
| GW23 | GW22, T20, T21, T22, T23, T24 |
| GW24 | GW18, GW23 |
| UI05, UI06 | UI03, GW14, GW14b |
| UI07 | UI03, GW05, GW07, GW13, GW14b |
| UI12 | UI11, GW14b, GW20, T20, T21 |
| UI13 | UI12, T22, GW23 |

Relocated tests (the need is satisfied only by a later card): LGW03-5 → GW19 · LGW04-2, LGW04-4 → GW15 · LGW05-3 → GW20 · LGW05-7 (console part) → GW14b/UI07 · LGW07-1, LGW07-4 → GW15 · LGW08-1 → GW15 · LGW08-6 → GW19 · LGW09-2 → GW15 · LGW11-3 (real redactor) → GW15 · LGW12-7 → GW15 · LGW12-8 → GW13 · LGW13-5 → GW20 · LGW19-1 → GW20.
