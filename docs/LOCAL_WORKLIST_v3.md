# Local Worklist — v3 Backend Rewrite (`gateway_v2/`)

**Purpose.** A single pick-one-by-one worklist of everything from
`AI_MESH_MASTER_RUNBOOK_v3_BACKEND_REWRITE.md` that can be **built, fixed, and verified LOCALLY**
(no cloud, no GPU, no fleet) in the `gateway_v2/` tree. The runbook is the source of truth for
*what* and *why*; this file is the source of truth for *what is doable locally now, in what order,
and what is already done*.

**Standard (the "clean" rule).** Every item here is either:
- **category 1** — code is cloud-complete, only a live gate is deferred; or
- **category 2** — a real documented seam, the cloud piece is a separate card.

We do **NOT** add **category 3** work (can't write honest code without the cloud surface) to this
list. Category-3 items are parked in the "Deferred — cloud/GPU/fleet only" section at the bottom so
the boundary stays visible.

**How to use.** Pick the top unstarted item under "Buildable locally — not yet done", run the
spec workflow (requirements → design → tasks → Run-All-Tasks), commit per-layer on `suraj-revamp`,
then mark it DONE here and in the runbook. Verification for every item is the offline `pytest`
suite + `mypy --strict` + `ruff` + `import-linter` + the AST gates — the same gate every shipped
card used.

Legend: ✅ DONE (local half closed) · 🔜 BUILDABLE NOW · ⛔ BLOCKED (needs a prerequisite card) ·
☁️ DEFERRED (cloud/GPU/fleet only).

---

## A. Already DONE — local half closed (shipped + verified on `suraj-revamp`)

These are here for reference and to show what each later item can build on. Evidence: the matching
`docs/plans/` plan docs + `docs/plans/evidence/` + the AGENTS.md changelog sections.

| Register / card | What shipped | Evidence |
|---|---|---|
| ✅ R2-02 (GW05c) | Incremental O(changes) state propagation; per-record publish; per-key cache invalidation | AGENTS.md R2-04/GW05 + state_control |
| ✅ R2-03 (GW05b) | Signed freshness stamp; trust-only-newer-stamp; fail-closed per kind | docs/plans 2026-10-08-r2-04 |
| ✅ R2-04 (GW05b) | PG session bounds (SET LOCAL + verify_bounds); ≥2 re-hydrators; idle-stop exemption | docs/plans 2026-10-08-r2-04 |
| ✅ R2-05 (GW14c) | Audit memory budget (byte-accounted XTRIM); durable Postgres sink; completeness ratio | docs/plans 2026-10-08-r2-05 |
| ✅ R2-06 (GW12b) | Bounded holdback (≤ cap upstream tokens); held-tokens histogram; trade-off table | docs/plans 2026-10-08-r2-06 |
| ✅ R2-07 / R2-08 / R2-18 (GW19) | Per-owner CoDel admission; 503 + Retry-After ≥ 1 s + x-should-retry; drain; post-heal recovery | docs/plans 2026-10-08-r2-07-r2-08-gw19 |
| ✅ R2-09 / R2-14 / R2-19 (GW06) | Org budget lease spent locally; async refill off the request path; retry-once; store boundary | docs/plans 2026-10-08-r2-09 |
| ✅ R2-17 (GW05) | C36 design confirmed (no code change) | runbook §0.3 |
| ✅ GW12 (keystone, not an R2 item) | SSE egress pipeline: bounded buffers, backpressure, real cancellation, timeouts | docs/plans 2026-10-09-gw12 |
| ✅ R2-10 ★ / R2-11 (GW14d) | Unified typed exposition; bounded tenant directory; windowed C4 SLO histogram; reset-safe rates; rejected{status,reason}; posture-vs-BLOCK; /readyz on metrics dir | docs/plans 2026-10-10-gw14d |
| ✅ R2-23 (push-coalescing) | Per-owner publish-nudge coalescer; durability never coalesced; microbenchmark; verification obligation | docs/plans 2026-10-11-r2-23-push-coalescing |
| ✅ GW15 (B1, chat surface) | Real `/v1/chat/completions` + `/v1/completions`: OpenAI parse/render, identity → plan → CoDel admission → budget lease → input scan → holdback/output guard → SSE; stock-SDK conformance subset | `.kiro/specs/chat-surface-gw15` |
| ✅ GW16 (B2, misc surfaces) | `/v1/embeddings` (full billable lifecycle), `/v1/models` + `/v1/models/{id}`, `/v1/moderations`; misc wire codec/renderer + moderation mapping; catalog injection seam; in-process stock-SDK conformance subset | `.kiro/specs/chat-surface-gw16-misc` |
| ✅ B3 (serving entrypoint) | uvicorn multiprocess host for `build_app` (`edge/serve.py`): honest fail-closed boot, `interface=asgi3`, `workers=contract.workers()`, SIGTERM→GW19 drain, `WorkerSupervisor` respawn seam, `serve` extra; real-socket conformance (stock SDK + curl + `/readyz` + `/metrics` + worker crash + SIGTERM drain) | `.kiro/specs/serving-entrypoint-gw-b3` |
| ✅ B4 (GW17/GW18, provider egress + routing) | Real streaming HTTP/SSE `HttpProviderClient` (generation egress, drops in behind the shipped `ProviderClient` seam; httpx under the `conformance` extra) + C43 load-aware `GuardRouter` (min-queue-wait over an injected load snapshot) + two `resources.py` capacity accessors; local verification against a `Fake_Upstream`/`Fake_Guard` incl. an end-to-end stock-SDK completion through the real client via `build_app` | `.kiro/specs/provider-egress-gw-b4` |
| ✅ B5 (GW16b, edge + delta SLO) | nginx edge: upstream keepalive (64/30s/1000) fixes 0.9986 conns/req + 502 above ~420 RPS; SSE transparency (`X-Accel-Buffering no`, `proxy_read_timeout 400s`); unknown-Host `default_server` 403; 5 security headers; `server_tokens off`. Firewall-added-delta SLO as a testable `runtime/latency_slo.py` (client-side delta vs direct path, ~40ms lost-segment tail-trim, NOT the gateway histogram) bound from `contract.target_p99_ms`. Docker edge harness (`nginx:1.30-alpine`, envsubst `B3_UPSTREAM`); static keepalive grep gate + docker-gated live L16b-1/-3/-6/-4 (L16b-1 = 0.0067 conns/req) | `.kiro/specs/edge-gw16b` |

---

## B. Buildable locally — NOT yet done (the actual worklist, in recommended order)

Each row is a self-contained card. "Local verification" is what proves it without cloud. "Live
gate (deferred)" is the cloud half that is explicitly NOT in scope for the local build.

### B1. ✅ GW15 — the real `/v1/chat/completions` + `/v1/completions` handler  **(DONE — keystone)**
- **DONE (2026).** Shipped + full gate green on `suraj-revamp`; see section A. Spec: `.kiro/specs/chat-surface-gw15`.
- **Why.** `edge/openai_chat.py` is empty ("empty until GW15/GW16"); `edge/routes.py` is a THIN
  wiring proof that explicitly defers request parsing, auth, plan resolution, admission, and the
  end-to-end input-scan → model-call → output-guard decision. This card builds the real request
  path and ties together every data-plane mechanism already shipped (A, above). Nothing else in
  the serving half can be driven until this exists.
- **Scope.** OpenAI request parsing + the shared request/response value types; API-key → identity
  via the shipped state cache; plan resolution; wire the shipped **GW19 CoDel admission** in front;
  wire the shipped **GW06 budget lease**; run the shipped **GW12b holdback + output guard** pipeline;
  emit the SSE response through the shipped `edge/wire` encoder; honest `WriteOutcome`/error mapping.
- **Local verification.** The existing `tests/openai_conformance/` SDK compat harness +
  `StubProviderClient`; property/unit tests per the house idiom; the full gate.
- **Live gate (deferred).** None of its own — it is driven by the fleet run later. Category 1.
- **Depends on.** The shipped A-items (admission, lease, holdback, observability, state) — all done.

### B2. ✅ GW16 — misc OpenAI surfaces + request/response typing  **(DONE)**
- **DONE (2026).** Shipped + full gate green on `suraj-revamp` (1495 passed, 10-scenario conformance subset, mypy/ruff/import-linter/6 AST gates, `unscanned_channels()==()`); 6 per-layer commits. Spec: `.kiro/specs/chat-surface-gw16-misc`. The OpenAI typing was NOT folded into B1 — misc typing lives in `edge/wire/openai_misc.py` (sibling to `openai.py`) to stay under the size gate.
- **Why.** `edge/openai_misc.py` is empty ("empty until GW16"). `/v1/embeddings`, `/v1/models`,
  `/v1/moderations`, and the shared OpenAI typing `edge/wire` says "land later (GW01/GW15+)".
- **Scope.** The three misc routes + the OpenAI request/response types GW15 also needs (coordinate
  with B1; may fold the typing into B1 and keep only the routes here).
- **Local verification.** SDK compat tests (the harness already exercises moderations/models/audio).
- **Depends on.** B1 (shares the typing). Category 1.

### B3. ✅ Serving entrypoint — real ASGI host + worker model  **(DONE)**
- **DONE (2026).** Shipped + full gate green on `suraj-revamp` (1530 passed / 95 skipped / 1 xfailed; mypy/ruff/import-linter/6 AST gates; `unscanned_channels()==()`); 6 per-layer commits. Spec: `.kiro/specs/serving-entrypoint-gw-b3`. Host = **uvicorn** multiprocess supervisor (`uvicorn.run(..., workers=N, factory=True, interface=asgi3)`), so `config.workers` is genuinely honored (the real-socket tests caught + fixed a bound-method ASGI3 mis-detection AND `Server.run()` ignoring `workers`). Manually verified: a booted 2-worker server serves `/readyz`+`/metrics`, fail-closes data-plane requests with 503 JSON envelopes (identity/provider deferred), survives a worker kill, and drains cleanly on SIGTERM.
- **Why.** `edge/app.py` `build_app()` exists but nothing hosts it (`app = None` default). GW19's
  notes name a "serving-entrypoint card": remove the inert `--worker-connections`, migrate off the
  deprecated uvicorn worker class, add REAL OS process supervision behind the shipped
  `WorkerSupervisor` seam.
- **Scope.** An actual ASGI server (uvicorn/hypercorn or a raw asyncio server) hosting `build_app`,
  with the worker model GW19 assumes; wire the `WorkerSupervisor` to real process supervision.
- **Local verification.** A real localhost HTTP client hitting the running server; the SDK compat
  harness over a live local socket; `/readyz` + `/metrics` reachable.
- **Live gate (deferred).** Fleet-scale worker behaviour under overload (that's the GW20 run).
  Category 2 (the supervision seam is real; the OS-level supervision is the deployable piece).
- **Depends on.** B1 (something to serve).

### B4. ✅ GW17 / GW18 — provider egress client + dispatch routing  **(DONE)**
- **DONE (2026).** Shipped + full gate green on `suraj-revamp` (1559 passed / 95 skipped / 1 xfailed; mypy/ruff/import-linter/6 AST gates; `unscanned_channels()==()`); 6 per-layer commits. Spec: `.kiro/specs/provider-egress-gw-b4`. Two upstreams per the runbook: the generation provider is organization-selected / BYOK (OpenAI-compatible, excluded from firewall cost/latency accounting) reached by the real `HttpProviderClient`; the enforcement guard is the self-hosted pool reached by the C43 `GuardRouter` over the split-topology TCP. B4 built generation egress + C43 guard ROUTING + the guard-egress seam; the real detector model CALL stays GW07/GW08. The real BYOK socket + real GPU guard fleet (GW25) + fleet-scale C43 capacity (GW20/GW20b) remain deferred live gates. Verified end-to-end: a stock-SDK chat request streamed a real completion through `HttpProviderClient` to a local fake upstream via `build_app`.
- **Why.** `dispatch/provider.py` is a `StubProviderClient` with "the real-socket path is a DEFERRED
  live gate." The real provider client (HTTP/socket to the upstream LLM or the guard model service)
  and the dispatch routing to the guard tier do not exist.
- **Scope.** The real `ProviderClient` implementation + its injection seam; the `DispatchRouter`
  selection to the real upstream. The CLIENT CODE and seam are local-buildable; the live socket is
  the deferred gate.
- **Local verification.** The client driven against a LOCAL fake upstream (an aiohttp/asyncio stub
  server that speaks the provider's SSE shape); byte-verified transform round-trip (already a GW12
  property); abort/cancel paths.
- **Live gate (deferred).** The real socket to the real provider / real guard — a live gate.
  Category 2 (seam real, live socket deferred).
- **Depends on.** B1.
- **OPEN QUESTION for the owner:** is the upstream a real external LLM provider, or your own
  guard/model service? This decides what the fake upstream mimics.

### B5. ✅ GW16b — production edge + firewall-added-delta SLO definition  **(DONE)**
- **DONE (2026).** Shipped + full gate green on `suraj-revamp` (1582 passed / 95 skipped / 1 xfailed, full suite incl. the docker-gated live edge subset; mypy --strict 166 files, ruff, import-linter 2/2, 6 AST gates, `unscanned_channels()==()`, static edge keepalive gate OK); 4 per-layer commits. Spec: `.kiro/specs/edge-gw16b`. Built per runbook §601 (NOT the worklist one-liner): the edge is an **nginx** tier (host nginx absent; runs via Docker), NOT TLS-termination code in the gateway — TLS cost is the deferred G-10 gate.
- **What shipped.** `deploy/edge/gateway-edge.conf` (upstream keepalive 64/30s/1000-req fixing the 0.9986 conns/req + 502-above-~420-RPS problem; SSE transparency via `proxy_http_version 1.1` + `Connection ""` + `X-Accel-Buffering no` + `proxy_read_timeout 400s`; unknown-Host `default_server` → 403; `server_tokens off`) + `security-headers.inc` (5 headers, byte-identical to v1 deploy). `runtime/latency_slo.py` defines C4 as the **client-side firewall-added delta** vs a direct path (symmetric ~40ms lost-segment tail-trim, nearest-rank p99, fail-closed on missing direct baseline), bound from `contract.target_p99_ms` — explicitly NOT the gateway histogram (under-reads 3.2–5.8ms). Docker harness: `Dockerfile.edge` (`nginx:1.30-alpine`, `NGINX_ENVSUBST_FILTER=B3_UPSTREAM` so nginx `$vars` survive) + `docker-compose.edge.yml` + `README.md` (Edge_Drain_Contract: `RV_CONN_MAX_AGE_S` 30s, 300s NLB pre-drain → 375s streams).
- **Local verification.** `test_latency_slo.py` (16 tests, 6 seeded properties + AST no-histogram proof); `test_edge_config_gate.py` + `check_edge_keepalive.sh` static grep gate; docker-gated `test_edge_live_l16b.py` driving the REAL nginx edge over a purpose-built framework-free ASGI upstream — L16b-1 keepalive (0.0067 conns/req), L16b-3 SSE passthrough + injected hold, L16b-6 unknown-Host 403 + headers, L16b-4 request passthrough; skips cleanly with `AMF_EDGE_DOCKER=0`.
- **Live gate (deferred).** G-10 (TLS cost at 600 RPS), G-11 (internet-vantage), G-13 (store gap behind a health-checked NLB). Category 2.
- **Depends on.** B3 (a running server for the edge to proxy to).

### B6. 🔜 R2-21 — load harness v2.3 (the measurement instrument)  **(NEXT)**
- **Why.** No load/capacity harness exists in `gateway_v2`. R2-01 and gates G-01/05/06/09 all need
  it. The round-2 harness is an external tool; this builds the local-runnable equivalent.
- **Scope.** A client program generating the GW20b strata: **Poisson arrivals** (the core of R2-01),
  **multi-key** (≥ 2,000 keys over ≥ 200 orgs), **long-prompt corpora** (1 / 5 / 25% of 4–7k-token
  prompts), **SDK-retry clients** (+ aggressive), **secret-shaped outputs**, **tenant scale ≥ 10k**;
  plus the C4 measurement (worst chunk per stream, client-side, holdback excluded) and the ladder /
  report format (≤ 10% steps, 3/3 repeats, per-run GPU-busy windowed / owner queue wait / loop lag /
  reason mix).
- **Local verification.** Run the FULL Poisson ladder against a single local gateway (B1+B3) + a
  LOCAL fake guard (B4's fake upstream): prove the harness produces correct arrival distributions,
  strata, and C4 numbers — the dry-run that de-risks the one-shot cloud run. This is the biggest
  cost-saver: when the GPU fleet lights up you are measuring, not debugging.
- **Live gate (deferred).** The actual fleet-scale knee numbers (R2-01), autoscaling (G-01),
  retry amplification at fleet scale (G-06), the integrated re-measure (G-09). Category 1 (the
  harness is complete; only the fleet it drives is cloud).
- **Depends on.** B1, B3, B4 (needs a real local endpoint + a fake guard to drive).

### B7. 🔜 R2-20 (partial) — immutable harness release hygiene
- **Why.** R2-20: "the harness is released immutably (as bin/v2.N) and runs from frozen copies"
  (editing a running harness in place killed a measurement). The `/dev/shm` RemoveIPC half is HOST
  config, not code — out of scope.
- **Scope (local half).** A release/versioning mechanism for the B6 harness (frozen, immutable
  `bin/v2.N` copies; a build step that stamps a version; a guard against running a mutated copy).
- **Local verification.** A test that a frozen harness build is byte-stable and that an in-place
  edit is rejected / detected.
- **Depends on.** B6 (the harness it versions). Category 1 (small).

### B8. 🔜 R2-13 (partial) — the flush chaos drill + declared failure behaviour doc
- **Why.** R2-13 asks to publish the declared store-failure behaviour and run chaos drills (flush /
  partition / forced-failover). The **flush** drill is locally runnable against the shipped
  `MemoryStore` / fakeredis twins; partition + forced-failover are cloud.
- **Scope (local half).** A local flush-drill test (store flush → re-hydrator restore within the
  bound, 0 violations) + a short "declared failure behaviour" doc section.
- **Local verification.** The flush-drill test against the in-memory / fakeredis twins (we already
  have `MemoryControlDB` + the re-hydrator).
- **Live gate (deferred).** Partition + Redis forced-failover drills (G-13 / real stores), the
  Cloud SQL + store overlap (G-14). Category 2.
- **Depends on.** nothing new (the re-hydrator + twins are shipped). NARROW — low value; optional.

### B9. 🔜 R2-24 (partial) — validation-environment watchdog utilities
- **Why.** R2-24: idle watchdog, burn-rate monitor, store-memory alarm, audit guard, owner-visible
  teardown rule. The store-memory alarm partly exists (GW14c). The rest are local-codeable
  monitoring utilities, though they are ops tooling that only earns its keep against a running env.
- **Scope (local half).** Standalone monitor utilities (store-memory alarm wired to the GW14c
  budget; an audit-backlog guard) with injected clocks so they're unit-testable.
- **Local verification.** Unit tests driving the alarms past their thresholds on injected inputs.
- **Depends on.** nothing new. LOW priority — ops tooling; thin local value. Optional.

---

## C. Recommended order (why this sequence)

```
B1 GW15 (chat handler)         ← keystone; unblocks everything
   └─► B2 GW16 (misc + typing)
   └─► B3 serving entrypoint    ← something to host B1
          └─► B4 GW17/18 (provider egress + fake upstream)
                 └─► B5 GW16b (TLS + delta def)
                 └─► B6 R2-21 harness v2.3
                        └─► B7 R2-20 harness release hygiene
                        └─► local Poisson dry-run (1 gateway + fake guard)   ← the cloud-cost saver
B8 R2-13 flush drill   ← independent, optional, narrow
B9 R2-24 watchdogs     ← independent, optional, ops
```

**The gate that unlocks the cloud one-shot:** after B1–B6 are built and the local Poisson dry-run
passes (one gateway + a fake guard driven through the full ladder), the cloud run becomes a pure
measurement — provision 3× c4-highcpu-16 + 4× g2-standard-4 + passthrough NLB + Cloud SQL 16 HA +
Memorystore Valkey 8 HA + a load-gen, and capture R2-01 (the Poisson knee) + G-01/05/06/09 in one
sitting. See the GPU/instance config in the chat history (GCP primary; AWS g6=L4 fallback).

---

## D. Deferred — cloud / GPU / fleet only (NOT local work; parked for visibility)

These are category-3 (can't write honest local code) or pure measurement/ops/hardware. Do NOT add
them to the local worklist.

| Register / gate | Why it is cloud-only |
|---|---|
| ☁️ R2-01 (GW20/GW20b) | The Poisson knee is a MEASUREMENT at fleet scale (≥3 gateways + ≥4 guards). Needs B1–B6 built first, then a real fleet. The local half is "build the serving path + harness" (B1–B6); the number itself is cloud. |
| ☁️ R2-12 live halves (GW16b) | NLB bake-off, TLS edge cost (G-10), internet-vantage latency (G-11), store-gap-behind-NLB (G-13) — real cloud networking + a live edge. |
| ☁️ R2-15 (GW25) | GPU capacity ops: reserved L4 baseline, capture procedure, T4 fallback, failover region — GCP reservations/quotas/hardware. |
| ☁️ R2-16 (GW20) | MIG vs GKE platform step tests, DCGM, HPA, drain timing — "the step tests themselves were not run"; real platforms. |
| ☁️ R2-22 ★ (GW23 / UI) | Console regressions — live in the UI / old v1 product, a DIFFERENT codebase, not `gateway_v2`. |
| ☁️ Gates G-01, G-02, G-03, G-10..G-15 | Autoscaling, drain-with-long-streams, guard rollout, TLS, internet, zone loss, store-behind-NLB, PG+store overlap, post-heal recovery — all require the fleet/stores/GPUs. The CODE each exercises is in B1–B6; the gate run is cloud. |

---

## E. Open questions for the owner (resolve before/while building B-items)

1. **Upstream identity (blocks B4 scope).** Is the provider a real external LLM API, or your own
   guard/model service? Determines the real `ProviderClient` and the local fake.
2. **Serving entrypoint choice (B3).** uvicorn (migrate off the deprecated worker class) vs a raw
   asyncio server? The runbook flags the deprecated uvicorn worker class as something to leave.
3. **Scope granularity.** Keep B1/B2/B4 as separate cards (recommended — each independently
   verifiable), or fold into one "serving path" card?

---

_Last updated: 2026-10-14. Companion to `docs/AI_MESH_MASTER_RUNBOOK_v3_BACKEND_REWRITE.md`
(the source of truth for what/why). Completion is mirrored in the runbook's §0.3 corrections
register and §0.5 open-gates table._
