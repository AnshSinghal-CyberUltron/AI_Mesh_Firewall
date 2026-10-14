# Requirements Document

## Introduction

Card B3 of the `gateway_v2/` backend-rewrite effort (`docs/LOCAL_WORKLIST_v3.md` §B3) stands up a
**runnable serving entrypoint** that hosts the already-shipped ASGI application. Today
`gateway_v2/gateway_v2/edge/app.py::build_app(...)` returns a working raw-ASGI 3.0 callable (router,
`lifespan`, `/readyz`, `/metrics`, chat + misc surfaces), but the module ends with
`app: ASGIApplication | None = None` — a stub that nothing hosts. B3 **hosts** `build_app`; it does
not rewrite it.

B3 is a **category-2** card (`LOCAL_WORKLIST_v3.md` standard): the OS-level worker supervision, the
graceful-drain wiring, the config surface, and real-socket conformance are built and verified
LOCALLY; the fleet-scale behaviour and the real provider/TLS/host pieces stay explicit named seams
(see Requirement 7). Scope is the **local half only**.

The entrypoint runs the shipped application under **uvicorn** with a modern worker + loop
configuration, exposes a configurable host / port / worker-count surface through the existing
`runtime` config idiom, drives the shipped **GW19 drain state machine** on `SIGTERM`, wires the
shipped **`WorkerSupervisor`** seam to real OS process supervision, and constructs the injected
production dependencies at boot with an **explicit fail-closed fallback** for every dependency not
yet wired (the real provider socket in particular stays `StubProviderClient` — a named deferred
seam, not scaffolded here).

### Grounded decisions (resolved from the runbook — not open questions)

These are settled and baked into the requirements below; sources in parentheses.

- **ASGI host = uvicorn**, modern/recommended worker + loop config (asyncio or uvloop), explicitly
  **not** the deprecated uvicorn worker class; the inert `--worker-connections` is removed.
  (USER decision + runbook GW19 notes / R2-07.)
- **Worker model = multi-worker, per-worker event loop** ("1 gateway × 12 workers"; C38 per-worker
  isolation). Worker count is **configurable via env**, never a capacity literal. The shipped
  `WorkerSupervisor` maps onto OS worker **processes**: isolate a crashed worker + respawn, peers
  keep admitting. (runbook §10.5.4 / R2-07; `gateway_v2/gateway_v2/admit/supervisor.py`.)
- **Graceful shutdown = SIGTERM drives the shipped GW19 `AdmissionController` drain state machine**
  through ASGI `lifespan.shutdown`: `ACCEPTING → DRAINING → TERMINATING → FLUSHED → EXITED`.
  Declared-termination streams are **not** truncated. Drain bounds come from the runbook:
  `RV_DRAIN_ACCEPT_S` (stop accepting; runbook value 15 s) then `RV_DRAIN_S` (drain in-flight;
  runbook value 60 s); a declared maximum stream duration must be **covered by** the drain window
  (gate G-02). Connection recycling `RV_CONN_MAX_AGE_S` (30 s). The `ScanExecutor` pool is released
  on shutdown (already wired in `build_app`'s `lifespan.shutdown`). (runbook §0.2 NLB-balancing row,
  R2-12; `gateway_v2/gateway_v2/admit/admission.py::AdmissionController.drain`.)
- **Deployment run model = a system user or a container** (runbook R2-20 / §3). Local verification
  is therefore a **real localhost socket**: the stock OpenAI Python SDK (base_url + key swap only)
  AND `curl` against the running uvicorn server, exercising chat (GW15) + embeddings / models /
  moderations (GW16); `/readyz` + `/metrics` reachable over the socket.
- **Local listen port = configurable via env `RV_PORT`, defaulting to 8300** (the port the existing
  `tests/openai_conformance/test_live_tcp_8300.py` already targets), so that deferred live-TCP test
  can be turned on against the `gateway_v2` app. The runbook does not pin a port (cloud is
  NLB-fronted); this is an in-spec decision, not an open question.
- **Honest boot**: construct `build_app`'s injected deps at boot; where a real dep is not wired yet
  it MUST fall back to its shipped fail-closed default, and that fallback MUST be explicit. The
  `provider` stays `StubProviderClient` (the real provider socket is B4 / GW17-18 — a named deferred
  seam); identity / plan / state come from the shipped GW05 seams; admission / quota from the shipped
  GW19 / GW06. B3 must not fake a real upstream or a real catalog backend.

### New ambiguities found (not settled by the runbook)

1. `tests/openai_conformance/test_openai_sdk_compat_live_uvicorn.py` targets the **old v1 gateway**
   (`ai_mesh_gateway.main`), not `gateway_v2`. It is therefore **not** the `gateway_v2` live-uvicorn
   test B3 turns on. B3's real-socket conformance (Requirement 5) is written against the
   `gateway_v2` `build_app` application and the `gateway_v2` conformance harness
   (`AMF_CONFORMANCE_BASE_URL` / `tcp_base_url()`), reusing the existing in-process conformance
   subset over a live socket. Flagged for user review; no blocking decision required to write
   requirements.
2. Whether the serving subcommand hangs off the existing `runtime/lifecycle.py::run_cli` `--serve`
   idiom or is a new entrypoint is left to the design phase (lean toward extending the existing CLI
   idiom). Captured as a design choice in Requirement 6, not an open requirements question.

## Glossary

- **Serving_Entrypoint**: The runnable process that constructs the injected production dependencies,
  calls `build_app(...)`, and hosts the returned ASGI application under uvicorn. The subject of this
  spec.
- **Served_App**: The raw-ASGI 3.0 application returned by
  `gateway_v2.edge.app.build_app(...)` — router + `lifespan` + `/readyz` + `/metrics` + chat/misc
  surfaces. Hosted, not rewritten, by the Serving_Entrypoint.
- **ASGI_Host**: uvicorn, configured with a modern worker + loop setting and explicitly not the
  deprecated uvicorn worker class.
- **Worker**: One OS process running a per-worker event loop that hosts the Served_App. The
  Serving_Entrypoint runs a configurable number of Workers.
- **Worker_Supervisor**: The shipped `gateway_v2.admit.supervisor.WorkerSupervisor` seam that
  isolates a crashed Worker (drops only its slots from the shared accounting) and respawns a
  replacement via an injected respawn callable, while peers keep admitting.
- **Admission_Controller**: The shipped `gateway_v2.admit.admission.AdmissionController`, whose
  `drain()` runs the drain state machine `ACCEPTING → DRAINING → TERMINATING → FLUSHED → EXITED`.
- **Drain_Window**: The in-flight-stream drain budget `RV_DRAIN_S` (runbook value 60 s), entered
  after the `RV_DRAIN_ACCEPT_S` stop-accepting period (runbook value 15 s).
- **Declared_Termination**: An explicit terminal frame emitted for an in-flight stream still
  unfinished past the Drain_Window — never a silently truncated stream.
- **Serving_Config**: The frozen configuration (host, port, worker-count, drain bounds, connection
  max-age) loaded once at boot from the environment through the existing `runtime` env→dataclass
  idiom (`load_contract` / `load_holdback_config` style).
- **Fail_Closed_Fallback**: The shipped fail-closed default a `build_app` dependency uses when its
  real implementation is not wired at boot (e.g. `fail_closed_resolver`, `fail_closed_admission`,
  `fail_closed_budget`, `StubProviderClient`, `empty_catalog_source`).
- **Deferred_Seam**: A real documented boundary whose cloud/host half is a separate card and is NOT
  scaffolded here (listed in Requirement 7).
- **Conformance_Harness**: The `gateway_v2` OpenAI conformance harness
  (`gateway_v2.contracts.openai_conformance.harness`), selecting TCP mode via
  `AMF_CONFORMANCE_BASE_URL` / `tcp_base_url()`.

## Requirements

### Requirement 1: Runnable serving entrypoint hosting build_app under uvicorn

**User Story:** As a gateway operator, I want a runnable entrypoint that hosts the shipped ASGI
application under uvicorn, so that the `gateway_v2` serving path can accept real HTTP traffic on a
real socket.

#### Acceptance Criteria

1. WHEN the Serving_Entrypoint is started, THE Serving_Entrypoint SHALL construct the injected
   production dependencies and call `gateway_v2.edge.app.build_app(...)` to obtain the Served_App.
2. WHEN the Serving_Entrypoint has obtained the Served_App, THE Serving_Entrypoint SHALL host the
   Served_App under uvicorn as the ASGI_Host.
3. WHILE the ASGI_Host is running, THE ASGI_Host SHALL serve the Served_App over a real TCP socket
   on the configured host and port.
4. WHERE a `build_app` dependency has no real implementation wired at boot, THE Serving_Entrypoint
   SHALL inject that dependency's shipped Fail_Closed_Fallback.
5. WHERE the real provider socket is not wired, THE Serving_Entrypoint SHALL inject
   `StubProviderClient` as the `provider` dependency and SHALL NOT construct a real upstream client.
6. WHERE the org/plan-derived model catalog backend is not wired, THE Serving_Entrypoint SHALL
   inject `empty_catalog_source` as the `catalog` dependency.
7. WHEN the Serving_Entrypoint injects a Fail_Closed_Fallback for an unwired dependency, THE
   Serving_Entrypoint SHALL record a boot log line naming that dependency and its fallback.
8. IF `build_app` raises during startup, THEN THE Serving_Entrypoint SHALL exit with a non-zero
   status code and SHALL NOT begin serving traffic.

### Requirement 2: Multi-worker per-worker-loop model with real OS worker supervision

**User Story:** As a gateway operator, I want a multi-worker, per-worker-loop model with real OS
process supervision, so that a single crashed worker is isolated and replaced while its peers keep
serving.

#### Acceptance Criteria

1. WHEN the Serving_Entrypoint starts the ASGI_Host, THE ASGI_Host SHALL run the configured number
   of Workers, each with its own event loop.
2. THE ASGI_Host SHALL use a modern uvicorn worker and loop configuration and SHALL NOT use the
   deprecated uvicorn worker class.
3. THE Serving_Entrypoint SHALL NOT pass the inert `--worker-connections` option to the ASGI_Host.
4. WHEN a Worker process exits abnormally, THE Worker_Supervisor SHALL isolate the crashed Worker by
   removing only its slots from the shared concurrency accounting.
5. WHEN a Worker process exits abnormally, THE Worker_Supervisor SHALL respawn a replacement Worker
   through its injected OS respawn callable.
6. WHILE a crashed Worker is being isolated and respawned, THE surviving Workers SHALL continue
   admitting requests.
7. WHEN the Worker_Supervisor respawns a replacement Worker, THE replacement Worker SHALL re-enter
   the shared concurrency accounting with the same slot contribution as the crashed Worker.

### Requirement 3: Graceful SIGTERM drain driving the shipped GW19 drain state machine

**User Story:** As a gateway operator, I want SIGTERM to drive the shipped admission drain state
machine through the ASGI lifespan, so that in-flight streams finish or terminate cleanly within a
bounded window during a scale-in or deploy.

#### Acceptance Criteria

1. WHEN the Serving_Entrypoint receives `SIGTERM`, THE Serving_Entrypoint SHALL initiate the
   ASGI_Host `lifespan.shutdown` sequence.
2. WHEN the `lifespan.shutdown` sequence runs, THE Serving_Entrypoint SHALL invoke the shipped
   `Admission_Controller.drain()` to drive the drain state machine through
   `ACCEPTING → DRAINING → TERMINATING → FLUSHED → EXITED`.
3. WHEN the drain enters `DRAINING`, THE Admission_Controller SHALL stop accepting new requests.
4. WHILE the drain is within the Drain_Window, THE Admission_Controller SHALL allow in-flight
   streams to finish.
5. IF an in-flight stream remains unfinished past the Drain_Window, THEN THE Admission_Controller
   SHALL emit a Declared_Termination for that stream and SHALL NOT silently truncate it.
6. THE Serving_Entrypoint SHALL read the stop-accepting period from `RV_DRAIN_ACCEPT_S`, defaulting
   to 15 seconds when the key is unset.
7. THE Serving_Entrypoint SHALL read the Drain_Window from `RV_DRAIN_S`, defaulting to 60 seconds
   when the key is unset.
8. THE Serving_Entrypoint SHALL set the Admission_Controller's declared maximum stream duration to a
   value covered by the Drain_Window.
9. WHEN the `lifespan.shutdown` sequence completes, THE Served_App SHALL release the `ScanExecutor`
   worker pool.
10. THE Serving_Entrypoint SHALL read the connection maximum age from `RV_CONN_MAX_AGE_S`,
    defaulting to 30 seconds when the key is unset, and SHALL apply it as the ASGI_Host connection
    recycling bound.

### Requirement 4: /readyz and /metrics reachable over the real socket

**User Story:** As a gateway operator, I want `/readyz` and `/metrics` reachable over the real
socket, so that a load balancer and a metrics scraper can observe the running server.

#### Acceptance Criteria

1. WHEN a client requests `GET /readyz` over the real socket AND every required state dependency is
   available, THE Served_App SHALL respond with status 200.
2. IF a client requests `GET /readyz` over the real socket WHILE a required state dependency is
   unavailable, THEN THE Served_App SHALL respond with status 503 and a body naming the unavailable
   dependency.
3. WHEN the Served_App evaluates `/readyz`, THE Served_App SHALL gate the state dependency before
   the metrics directory, consistent with the shipped `build_app` composition.
4. WHEN a client requests `GET /metrics` over the real socket, THE Served_App SHALL respond with
   status 200 and the Prometheus text exposition.
5. WHEN the Served_App serves `GET /metrics`, THE Served_App SHALL compute the exposition off the
   serving loop, consistent with the shipped `build_app` composition.

### Requirement 5: Real-socket OpenAI conformance over the running server

**User Story:** As a gateway operator, I want the stock OpenAI SDK and curl to exercise the OpenAI
surfaces against the running server, so that real wire behaviour is verified over a real socket
rather than only in-process.

#### Acceptance Criteria

1. WHEN the stock OpenAI Python SDK is pointed at the running Serving_Entrypoint with only a
   base_url and API-key swap, THE Serving_Entrypoint SHALL serve the `/v1/chat/completions` surface
   over the real socket.
2. WHEN the stock OpenAI Python SDK exercises the embeddings, models, and moderations surfaces
   against the running Serving_Entrypoint, THE Serving_Entrypoint SHALL serve `/v1/embeddings`,
   `/v1/models`, `/v1/models/{id}`, and `/v1/moderations` over the real socket.
3. WHEN a `curl` request is issued against the running Serving_Entrypoint for a served surface, THE
   Serving_Entrypoint SHALL return a response over the real socket with a non-HTML content type.
4. WHEN the `gateway_v2` real-socket conformance test suite runs with `AMF_CONFORMANCE_BASE_URL`
   set to the running server, THE conformance subset SHALL execute against the live socket using the
   Conformance_Harness.
5. WHEN the deferred live-TCP conformance test (`test_live_tcp_8300.py`) runs against the running
   Serving_Entrypoint on the configured port, THE test SHALL assert SDK-visible authentication
   errors and JSON (not HTML) error envelopes over the real socket.
6. WHILE real backend dependencies remain unwired, THE real-socket conformance SHALL assert
   SDK-visible protocol behaviour and SHALL NOT assert stubbed-upstream response bytes.

### Requirement 6: Config/env surface through the existing runtime config idiom

**User Story:** As a gateway operator, I want host, port, worker-count, and drain bounds configured
through the existing runtime env idiom, so that the serving configuration is loaded, validated, and
logged the same way as every other `gateway_v2` config.

#### Acceptance Criteria

1. WHEN the Serving_Entrypoint boots, THE Serving_Entrypoint SHALL load the Serving_Config from the
   environment once, using the existing `runtime` env→dataclass-plus-logs idiom
   (`load_contract` / `load_holdback_config` style).
2. THE Serving_Entrypoint SHALL read the listen port from `RV_PORT`, defaulting to 8300 when the key
   is unset.
3. THE Serving_Entrypoint SHALL read the listen host from the environment, defaulting to a
   loopback-safe host when the key is unset.
4. THE Serving_Entrypoint SHALL read the Worker count from the environment, consistent with the
   shipped worker-count resolution in `runtime/resources.py`.
5. IF a Serving_Config environment value is present but invalid, THEN THE Serving_Entrypoint SHALL
   emit a warning log line naming the rejected key and value AND SHALL use the documented default
   for that key.
6. THE Serving_Entrypoint SHALL NOT introduce any capacity literal outside
   `gateway_v2/gateway_v2/runtime/resources.py`.
7. THE Serving_Config SHALL be a frozen value with no module-level mutable state.

### Requirement 7: Deferred gates recorded as explicit named seams

**User Story:** As a reviewer, I want the cloud/host halves recorded as explicit named seams, so
that the boundary between the local build and the deferred work stays visible and nothing is
silently scaffolded.

#### Acceptance Criteria

1. THE Serving_Entrypoint SHALL record the real provider socket (B4 / GW17-18) as a Deferred_Seam
   and SHALL NOT scaffold a real upstream client.
2. THE Serving_Entrypoint SHALL record fleet-scale overload, autoscaling, and the Poisson knee
   (GW20 / GW20b, gates G-01 / G-02 at fleet scale) as Deferred_Seams and SHALL NOT scaffold
   fleet-scale behaviour.
3. THE Serving_Entrypoint SHALL record real TLS termination at the edge and the NLB bake-off /
   internet-vantage path (B5 / GW16b, R2-12; gates G-10 / G-11 / G-13) as Deferred_Seams and SHALL
   NOT scaffold TLS termination.
4. THE Serving_Entrypoint SHALL record the `/dev/shm` RemoveIPC host configuration (R2-20 host half)
   as a Deferred_Seam and SHALL NOT scaffold host-level IPC configuration.
5. WHERE a Deferred_Seam is referenced in the serving code, THE reference SHALL name the owning card
   and SHALL leave the shipped Fail_Closed_Fallback or stub in place.
