# Requirements Document

## Introduction

GW11 ("Build provider dispatch with verified transformation and no firewall-initiated generation", runbook §10.9) builds the one place where a provider is called, reachable only with authorization, sending only verified bytes. A v3 consolidation audit found GW11 was wrongly marked done: the `dispatch/` modules that exist (`provider.py`, `http_provider.py`, `routing.py`, `guard_routing.py`, `transform.py`) are the GW12 / B4 egress transport, while GW11's own dispatch-authorization core — the sealed `DispatchAuthorization` minted only by `resolve/`, the single authorized `ProviderClient` entry point, transformation verification against finding spans, exact provider-error mapping, plan-driven timeouts, response-size bounds, the snapshot catalogue, and the bounded instrumented pool — was never built.

This feature implements that core. The runbook `docs/AI_MESH_MASTER_RUNBOOK_v3_BACKEND_REWRITE.md` is the SOLE source of truth; this document implements EXACTLY the GW11 card plus its v2.1 corrections C11 (register row §0-A.3, enforcement mechanism) and C27 (register row §0-A.3, error mapping + timeouts + size), and nothing outside it. Every requirement is traceable to the GW11 card, C11, C27, or one of the live acceptance tests LGW11-1 through LGW11-6.

The GW11 objective, verbatim: *"One place where a provider is called, reachable only with authorization, sending only verified bytes."*

The GW11 exit criteria: single authorized entry point; verified transformations; no firewall-initiated generation; snapshot catalogue; bounded, instrumented pool.

### Why GW11 exists (runbook)

v1 has multiple dispatch paths with independently assembled arguments, and the output rewrite helper calls `LLM_ROUTER.acompletion` on the guard's behalf — a model call the customer did not request, made by the firewall. Separately, `reload_models_now` runs on the request path and mutates the shared router's model list under a per-org lock, which the function's own docstring records as having previously caused `422 no_provider_configured` under concurrency.

## Glossary

- **GW11**: The runbook dispatch-phase card this feature implements (§10.9, "Build provider dispatch with verified transformation and no firewall-initiated generation").
- **C11**: The runbook v2.1 correction (register row §0-A.3; references §10.4, GW07, GW11, §10.2.3). States that "BLOCK cannot reach a provider, by type" is not enforceable in Python by a sealed constructor alone (`object.__new__`+setattr and relabel-then-mint still reach a recording provider; the HTTP gate misses positional/named status codes, 5xx and raised exceptions and is evaded by import aliases). Remedy: sealed constructor + provenance AST gate (caught 17/17 forgery forms) + runtime assertion + recorder zero-call proof + an import-linter forbidden contract for starlette/fastapi outside `edge/`.
- **C27**: The runbook v2.1 correction (register row §0-A.3; references GW11, GW12, GW15). Remedy: plan-declared timeouts per provider/model; exact error-class mapping with headers; response-size bound; scan provider error payloads; incremental history scanning and an SDK-compatible over-band error; audit every started request.
- **Dispatch_Authorization**: A sealed, frozen value type that is proof a request is authorized to reach a provider. It is MINTED only inside `resolve/`; the authorized provider entry point requires it as a parameter. Its runbook contract: "not constructible outside `resolve/`" — no public constructor for convenience, direct construction / `dataclasses.replace` / `object.__new__`+setattr / relabel-then-mint all barred.
- **Dispatch_Core**: The GW11 authorization + verification + error-mapping + pool-bound layer this feature adds around the existing provider entry point. The term for the new work, distinct from the shipped GW12 egress transport.
- **Authorized_Provider_Entry**: The single `ProviderClient` entry point that requires a `Dispatch_Authorization` to be called. Wraps / fronts the existing `@runtime_checkable` `ProviderClient` protocol (`gateway_v2/dispatch/provider.py`) without rewriting the GW12 transport.
- **Decision**: The frozen resolver output (`gateway_v2/domain/decision.py`): `disposition`, `per_finding`, `transformations`, `findings`, `plan_version`, `deciding_rules`, `unavailable_detectors`.
- **Disposition**: `ALLOW` / `FLAG` / `REDACT` / `BLOCK` (`gateway_v2/domain/decision.py`).
- **Transformation**: A frozen `(kind, span, replacement)` triple on a `Decision` (`gateway_v2/domain/decision.py`).
- **Finding**: A detector result (`gateway_v2/domain/finding.py`) carrying `spans: tuple[Span, ...]`.
- **Span**: A frozen `(start, end)` byte range (`gateway_v2/domain/finding.py`).
- **Verified_Bytes**: The request body after `Decision.transformations` are applied AND the result has been verified against the finding spans; the only bytes the authorized entry point may send.
- **Dispatch_Router**: The deterministic, pure, plan-identity → destination selector (`gateway_v2/dispatch/routing.py`, `DispatchRouter`). GW11 reuses it: routing on the request path reads only the pinned plan, with no adjudicator call and no shared mutable router state.
- **Guard_Backend**: The §10.5.4 local-model backend protocol (`gateway_v2/detect/guard/backend.py`, `GuardBackend`). GW11 routes REWRITE through this local-model path, never a provider generation call.
- **Provider_Catalogue**: The resolved set of providers/models available to a tenant, resolved as a snapshot like the plan.
- **Empty_Catalogue_Error**: The explicit provisioning error raised when a tenant genuinely has no provider configured (a `no_provider_configured`-class condition caused by actual absence).
- **Control_Plane_Outage_Error**: The distinct error raised when the catalogue cannot be resolved because of a store/control-plane blip. It must never surface to a tenant as "connect your provider".
- **Resource_Contract**: `ResourceContract` (`gateway_v2/runtime/resources.py`), the only module permitted to hold capacity-position literals. The connection-pool bound derives from it (`pool_size(PoolKind.PROVIDER)`).
- **Pool_Wait_Time**: The time a request waits for a free connection from the bounded provider pool; GW11 exports it.
- **Provenance_Gate**: The new AST gate (mirroring `gateway_v2/lint/check_http_outside_edge_resolve.py`) that flags any minting or forgery of a `Dispatch_Authorization` outside `resolve/`.
- **Runtime_Assertion**: The runtime check at the provider entry point that re-verifies the authorization's provenance, backing the type-level guarantee that the gate cannot see at run time.
- **Recorder**: A test double that records every provider call attributable to the firewall; used to prove zero calls on BLOCK and zero firewall-attributable calls on REWRITE.
- **LGW11-1 … LGW11-6**: The six GW11 live acceptance tests (runbook GW11 card "Live acceptance tests — required to exit").

## Requirements

### Requirement 1: Sealed Dispatch_Authorization minted only by resolve/ (LGW11-1)

**User Story:** As a backend engineer, I want a `Dispatch_Authorization` value that cannot be constructed anywhere outside `resolve/`, so that a request can reach a provider only when the single security authority authorized it.

#### Acceptance Criteria

1. THE Dispatch_Core SHALL define `Dispatch_Authorization` as a frozen, slotted value type with no public constructor usable outside `resolve/`.
2. WHERE a caller outside `resolve/` attempts direct construction of a `Dispatch_Authorization`, THE Dispatch_Core SHALL make that construction fail (sealed constructor).
3. WHERE a caller attempts `dataclasses.replace` on an existing `Dispatch_Authorization`, THE Dispatch_Core SHALL make that attempt fail.
4. THE Dispatch_Core SHALL place `Dispatch_Authorization` in the `gateway_v2.domain` layer so that `resolve/` can import it under the "Resolve imports only domain" import-linter contract while `dispatch/` imports it from the same layer, and SHALL restrict MINTING to `resolve/` through the Provenance_Gate rather than through import visibility.
5. THE resolve layer SHALL be the only place that mints a `Dispatch_Authorization`, and SHALL mint one as part of producing a non-terminal `Decision`.
6. IF a code path attempts to obtain a `Dispatch_Authorization` by `object.__new__` plus attribute setting, THEN THE Dispatch_Core SHALL cause that path to be rejected by the combination of the Provenance_Gate and the Runtime_Assertion (Requirement 10).
7. IF a code path attempts to relabel or mutate a `Decision` and then mint an authorization outside `resolve/`, THEN THE Dispatch_Core SHALL cause that path to be rejected by the Provenance_Gate (Requirement 10).

### Requirement 2: Single authorized provider entry point requiring the authorization (LGW11-1)

**User Story:** As a backend engineer, I want exactly one provider entry point that will not type-check without a `Dispatch_Authorization`, so that no code path can call a provider without authorization.

#### Acceptance Criteria

1. THE Dispatch_Core SHALL expose exactly one Authorized_Provider_Entry through which a provider request is issued.
2. THE Authorized_Provider_Entry SHALL require a `Dispatch_Authorization` as a parameter so that a call omitting it is a static type error at build time.
3. WHEN a dispatch is attempted without an authorization value, THE Dispatch_Core SHALL produce a type error at build time and SHALL provide no runtime path that reaches a provider without authorization (LGW11-1).
4. THE Authorized_Provider_Entry SHALL front the existing `@runtime_checkable` `ProviderClient` protocol and the existing `UpstreamRequest` / `UpstreamEvent` value types (`gateway_v2/dispatch/provider.py`) WITHOUT rewriting the GW12 egress transport.
5. WHEN the Authorized_Provider_Entry is invoked, THE Runtime_Assertion (Requirement 10) SHALL verify the authorization's provenance before any provider byte is sent.

### Requirement 3: Transformation application and verification against finding spans (LGW11-3 non-redactor part)

**User Story:** As a backend engineer, I want the applied `Decision.transformations` verified against the finding spans before the call, so that only verified bytes are sent and an unverified dispatch is impossible.

#### Acceptance Criteria

1. WHEN a non-terminal `Decision` carries transformations, THE Dispatch_Core SHALL apply `Decision.transformations` to the request bytes before the provider call.
2. WHEN transformations have been applied, THE Dispatch_Core SHALL verify the resulting bytes against the finding spans recorded on `Decision.findings` before the provider call.
3. IF verification of the transformed bytes against the finding spans fails, THEN THE Dispatch_Core SHALL treat the failure as terminal and SHALL NOT send any bytes to the provider.
4. WHEN a REDACT `Decision` is dispatched, THE Dispatch_Core SHALL send only the sanitized bytes and SHALL record a verification log entry (LGW11-3).
5. THE Dispatch_Core SHALL perform verification using the shipped `Transformation` values and finding spans, and SHALL NOT introduce a new redactor (the real redactor is deferred to GW15 per the GW11 card; see Requirement 12).

### Requirement 4: BLOCK reaches no provider, recorder-proven (LGW11-2)

**User Story:** As a security owner, I want a BLOCK decision to reach no provider at all, proven by a recorder showing zero calls, so that blocked content never leaves the firewall.

#### Acceptance Criteria

1. WHEN a `Decision` has disposition `BLOCK`, THE Dispatch_Core SHALL make no provider call.
2. WHILE a request is resolved to `BLOCK`, THE resolve layer SHALL mint no `Dispatch_Authorization` for that request.
3. WHEN a BLOCK decision is processed against a Recorder, THE Dispatch_Core SHALL cause the Recorder to show zero provider calls (LGW11-2).

### Requirement 5: No firewall-initiated generation; REWRITE via the local Guard_Backend (LGW11-4)

**User Story:** As a compliance owner, I want the firewall to never make a provider generation call the customer did not request, so that REWRITE runs on a local model and the firewall initiates zero provider generations.

#### Acceptance Criteria

1. THE Dispatch_Core SHALL make the `dispatch/` provider path callable only from the request path, and SHALL NOT initiate a provider generation call on the firewall's own behalf.
2. WHERE a `Decision` requires REWRITE and REWRITE is supported, THE Dispatch_Core SHALL run the rewrite through the `Guard_Backend` against a local model (`gateway_v2/detect/guard/backend.py`).
3. WHEN a REWRITE is performed with the local model against a Recorder, THE Dispatch_Core SHALL cause zero provider calls attributable to the firewall (LGW11-4).

### Requirement 6: Deterministic plan-driven routing with no request-path shared mutable state

**User Story:** As a backend engineer, I want routing to be a deterministic function of the pinned plan with no adjudicator call and no shared mutable router state on the request path, so that concurrency cannot flip a tenant's destination or corrupt shared state.

#### Acceptance Criteria

1. WHEN the Authorized_Provider_Entry selects a destination, THE Dispatch_Core SHALL derive it deterministically from the pinned plan using `Dispatch_Router` (`gateway_v2/dispatch/routing.py`).
2. THE Dispatch_Core SHALL make no adjudicator call on the request path when selecting a destination.
3. THE Dispatch_Core SHALL read no shared mutable router state and SHALL mutate no shared router state on the request path.
4. WHEN the same pinned plan is routed more than once, THE Dispatch_Router SHALL return the same destination each time (determinism).

### Requirement 7: Snapshot catalogue with distinct empty-catalogue and outage errors (LGW11-5)

**User Story:** As a tenant-facing engineer, I want catalogue resolution to be a snapshot, with an empty catalogue reported as a distinct provisioning error from a control-plane outage, so that a store blip never tells a tenant to "connect your provider".

#### Acceptance Criteria

1. THE Dispatch_Core SHALL resolve the Provider_Catalogue as a snapshot, in the same manner as the pinned plan.
2. IF a tenant's resolved catalogue is genuinely empty, THEN THE Dispatch_Core SHALL raise the Empty_Catalogue_Error as an explicit provisioning error.
3. IF the catalogue cannot be resolved because of a control-plane or store outage, THEN THE Dispatch_Core SHALL raise the Control_Plane_Outage_Error.
4. THE Dispatch_Core SHALL make the Empty_Catalogue_Error and the Control_Plane_Outage_Error two distinct errors (LGW11-5).
5. WHEN a control-plane or store outage occurs, THE Dispatch_Core SHALL NOT surface a message that tells the tenant to "connect your provider" (LGW11-5).

### Requirement 8: Exact provider error mapping, plan-driven timeouts, size bound, error-payload scanning, over-band error (C27)

**User Story:** As a backend engineer, I want provider errors mapped exactly, timeouts driven by the plan per provider/model, non-stream bodies bounded, provider error payloads scanned, and over-band conversations returned as an SDK-compatible error, so that the firewall does not amplify retries, cut legitimate slow generations, OOM a worker, leak secrets, or mis-report over-band requests.

#### Acceptance Criteria

1. WHEN a provider returns a `400`, THE Dispatch_Core SHALL map it to a `400` carrying the provider's error code.
2. WHEN a provider returns a `429`, THE Dispatch_Core SHALL map it to a `429` carrying the `Retry-After` header and the provider's rate-limit headers.
3. THE Dispatch_Core SHALL NOT map provider `4xx` responses to a blanket `502` (the prototype's blanket `502` caused the SDK to retry three times).
4. THE Dispatch_Core SHALL take the provider request timeout from the pinned plan per provider/model, so that a legitimately slow first token (for example 125 s) is not cut at a fixed 120 s.
5. WHEN a provider returns a non-streaming response, THE Dispatch_Core SHALL bound the response size so that one BYOK tenant cannot OOM a shared worker (the prototype grew RSS to three times the body).
6. WHEN a provider returns an error payload, THE Dispatch_Core SHALL scan that payload before forwarding it, so that a provider error frame echoing a secret is not forwarded unscanned.
7. WHEN a multi-turn conversation exceeds the model's context band, THE Dispatch_Core SHALL return an SDK-compatible `context_length_exceeded` error rather than a `413`, using incremental history scanning.
8. WHEN a request is started, THE Dispatch_Core SHALL audit it, including requests that are later cancelled.

### Requirement 9: Bounded, instrumented provider connection pool (ResourceContract)

**User Story:** As an operator, I want the provider connection pool bounded by the Resource_Contract and its wait time exported, so that pool exhaustion causes bounded backpressure rather than an exception storm and I can observe contention.

#### Acceptance Criteria

1. THE Dispatch_Core SHALL bound the provider connection pool using the Resource_Contract (`ResourceContract.pool_size(PoolKind.PROVIDER)`, `gateway_v2/runtime/resources.py`) and SHALL hold no capacity literal of its own.
2. WHEN the connection pool is exhausted, THE Dispatch_Core SHALL apply bounded queueing with backpressure rather than raising an exception storm.
3. THE Dispatch_Core SHALL export the Pool_Wait_Time.
4. IF a provider returns a malformed body, THEN THE Dispatch_Core SHALL report a protocol error rather than reporting success.
5. WHILE a provider is slow, THE Dispatch_Core SHALL apply the bounded timeout from Requirement 8.4 rather than waiting unbounded.

### Requirement 10: C11 enforcement stack — provenance gate, forbidden import contract, runtime assertion

**User Story:** As a platform engineer, I want the "not constructible outside resolve/" guarantee enforced by a provenance AST gate, a forbidden-import contract, and a runtime assertion, so that the type-level guarantee holds against the Python holes a sealed dataclass alone cannot close.

#### Acceptance Criteria

1. THE Provenance_Gate SHALL be an AST gate, mirroring `gateway_v2/lint/check_http_outside_edge_resolve.py`, that scans the tree and exits non-zero on any hit.
2. WHEN the Provenance_Gate scans a tree, THE Provenance_Gate SHALL flag minting or forgery of a `Dispatch_Authorization` outside `resolve/`, including direct construction, `dataclasses.replace`, `object.__new__`-plus-attribute-set forgery, and import-alias evasion, covering the forgery-form class the prototype gate caught (17/17 forms).
3. THE Provenance_Gate SHALL be runnable as `python -m lint.<gate_name> gateway_v2` and SHALL be added to the structural-gates job in `.github/workflows/gateway-v2.yml`.
4. THE Dispatch_Core SHALL add a fourth import-linter contract to `gateway_v2/pyproject.toml`, a forbidden contract asserting that `starlette` and `fastapi` are not importable outside `gateway_v2.edge`.
5. WHERE the `edge` layer serves HTTP with no HTTP framework (the raw-ASGI app imports neither `starlette` nor `fastapi`, which are not runtime dependencies in `pyproject.toml`), THE forbidden import contract SHALL still assert the general "no HTTP framework outside `edge`" intent grounded in the actual dependencies, naming `starlette` and `fastapi` per C11.
6. WHEN the Authorized_Provider_Entry is invoked, THE Runtime_Assertion SHALL assert the authorization's provenance at the provider entry point before any provider byte is sent, backing the recorder zero-call proof (Requirement 4.3).

### Requirement 11: Concurrency safety under load (LGW11-6)

**User Story:** As an operator, I want 500 concurrent requests across 20 orgs to produce no spurious provisioning errors and no shared-state corruption, so that the v1 `reload_models_now` concurrency defect cannot recur.

#### Acceptance Criteria

1. WHILE 500 concurrent requests are processed across 20 orgs, THE Dispatch_Core SHALL produce zero spurious provisioning errors (no `no_provider_configured` not caused by actual absence) (LGW11-6).
2. WHILE 500 concurrent requests are processed across 20 orgs, THE Dispatch_Core SHALL exhibit no shared-state corruption (LGW11-6).
3. WHEN a provisioning error is produced under concurrency, THE Dispatch_Core SHALL produce it only when a provider is genuinely absent for that tenant.

### Requirement 12: Non-goals and deferred boundaries

**User Story:** As a reviewer, I want the GW11 scope boundaries stated explicitly, so that work the runbook defers to other cards is not built here.

#### Acceptance Criteria

1. THE Dispatch_Core SHALL NOT implement the real redactor; the real-redactor part of LGW11-3 is deferred to GW15 per the GW11 card, and GW11 implements only the verification-against-spans and sanitized-dispatch mechanism using the shipped `Transformation` values (Requirement 3).
2. THE Dispatch_Core SHALL NOT rebuild the real provider socket / live BYOK generation transport; the GW12 `http_provider.py` transport already exists, and GW11 adds only the authorization, verification, error-mapping, and pool-bound layer around the entry point. The real live-socket conformance is a deferred live gate.
3. THE Dispatch_Core SHALL run REWRITE execution through the shipped `Guard_Backend` (GW08) local path and SHALL NOT run it as a provider generation call (Requirement 5).
4. THE Dispatch_Core SHALL preserve the house conventions: `from __future__ import annotations`; frozen `@dataclass(frozen=True, slots=True)` value types; tuple `__all__`; no module-level mutable; no capacity literal outside `runtime/resources.py`; the layer order `edge > admit > plan > detect > resolve > dispatch > egress > audit > runtime > contracts > domain`; and SHALL keep `mypy --strict`, `ruff`, `lint-imports`, and the AST gates green against the existing full-suite baseline.
