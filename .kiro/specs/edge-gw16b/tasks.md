# Implementation Plan: Production edge — upstream keepalive + SSE transparency + firewall-added-delta SLO (B5 / GW16b)

## Overview

Convert the B5 / GW16b design into a series of code-generation steps that land the **two
deliverables** the design names, **bottom-up** so each piece is gate-checkable before the next
builds on it, the same shape as the shipped B3 / B4 cards (source before its tests;
property/unit/integration/live tests; a checkpoint; a final gate):

1. **The firewall-added-delta SLO module** (`gateway_v2/gateway_v2/runtime/latency_slo.py`, NEW) —
   the **pure Python half**, built FIRST because it is gate-checkable in isolation. It computes
   `Firewall_Added_Delta = C4(through-firewall) − C4(direct)` from paired client-side path samples
   with the symmetric ≈ 40 ms lost-segment tail-trim, errors on an absent direct baseline, is never
   sourced from the gateway histogram, and reads the SLO bound from `contract.target_p99_ms`. This
   is the **only** gated `gateway_v2` Python in B5.
2. **The v2 edge nginx config** (`gateway_v2/deploy/edge/`, NEW infra) — the `upstream { … keepalive }`
   block + `proxy_http_version 1.1` + `Connection ""`, `X-Accel-Buffering: no` + streaming
   passthrough + `proxy_read_timeout 400s`, `keepalive_timeout`/`keepalive_requests` aligned to
   `RV_CONN_MAX_AGE_S` (30 s), `default_server` 403 unknown-Host + the reused security-headers
   include, `server_tokens off`, the `${B3_UPSTREAM}` envsubst param, plus the security-headers
   include and the Dockerfile/compose the design names. The v1 `deploy/nginx.conf` is **reference
   only — not edited**.
3. **The CI edge regression gate** (`check_edge_keepalive.sh` + `tests/edge/test_edge_config_gate.py`)
   — a static grep gate that FAILS if the keepalive block is removed, surfaced via pytest, static
   (no Docker) so it always runs in CI. Mirrors `scripts/test_nginx_security.sh`.
4. **The Local_Edge_Harness + the docker-gated live L16b subset** — a docker-compose (nginx
   container → the B3 serving entrypoint) + a test module gated behind Docker availability (skip
   cleanly when Docker is absent, like `test_nginx_security.sh`'s `NGINX_DOCKER`): L16b-1, L16b-3,
   L16b-6, L16b-4.
5. **Edge drain contract + deferred-seam documentation** — the NLB 300 s pre-drain / ≈ 375 s
   stream-survival contract documented in the edge config/README; G-10/G-11/G-13 + L16b-2/4/5 + the
   NLB bake-off + the TLS cost named as deferred seams (TLS knob defined, cost deferred); nothing
   scaffolded.

Implementation language: **Python** for the delta module (grounded in concrete
`gateway_v2/gateway_v2/runtime/*.py` modules + the shipped house idiom); the nginx config,
the gate script, and the docker harness are **infra / shell / test** artifacts, NOT gated
`gateway_v2` Python.

### The Python-half / infra-half boundary (critical to this card)

- **Python-half gate** (`.venv/bin/python -m pytest`, `mypy --strict`, `ruff check gateway_v2
  state_control lint tests`, `lint-imports`, the 6 AST gates, `unscanned_channels()==()`, ≤ 800 /
  ≤ 120) applies **only** to `runtime/latency_slo.py` + its tests. The module is a pure `runtime/`
  computation: no HTTP object, no I/O, no upward import, SLO bound read from
  `ResourceContract.target_p99_ms`, so it adds **no** new import edge and breaks **no** gate.
- **Infra half** (the nginx conf, `security-headers.inc`, `Dockerfile.edge`,
  `docker-compose.edge.yml`, `check_edge_keepalive.sh`) is **not** Python and is **not** under the
  Python gates. It is gated by a static grep-shaped checker and the docker-gated live subset,
  mirroring `scripts/test_nginx_security.sh`. Do **not** force pytest/mypy/ruff/lint-imports/the 6
  AST gates onto the nginx/shell artifacts.

### Conventions enforced in EVERY coding task

- **Property tests** (delta module only): a seeded `random.Random` driven for **≥ 100 iterations**,
  **NO hypothesis** (the gateway_v2 house idiom; see the shipped `tests/` property suites). Each
  property test is tagged `# Feature: edge-gw16b, Property N: <text>` and
  `# Validates: Requirements X.Y`, with exactly one property ↔ one property-based test. Six
  properties exist (Properties 1–6 over the pure delta function); the design's PBT prework
  classifies ALL nginx edge behaviours (keepalive reuse, SSE transparency, Host rejection, headers)
  as **INTEGRATION** (docker-gated, 1–3 examples), so **no** property sub-task is written for the
  config.
- **Value types** (delta module) are frozen slotted dataclasses — `PathSamples`,
  `PairedPathSamples`, `FirewallDelta` are all `@dataclass(frozen=True, slots=True)`. No
  module-level mutable (`check_no_module_mutable`, `check_frozen_dataclasses`).
- **No capacity literal outside `runtime/resources.py`** — the SLO bound is read via
  `ResourceContract.target_p99_ms`; the module holds **no** capacity literal. The ≈ 40 ms
  `Lost_Segment_Tail` threshold is an **operational / statistical** constant (a measured network
  artefact per R2-12), a named module constant with a runbook-citing docstring, handled the same
  way `serving_config.py`'s operational defaults are allowed outside `resources.py`
  (`check_capacity_literals`).
- **The delta module stays ≤ 800 lines / each function ≤ 120 lines** (`check_sizes`), has no HTTP
  type (`check_http_outside_edge_resolve`), and does not touch `check_tenant_scale` /
  `check_secret_headers`. `lint-imports` stays **2 contracts kept** (no new edge).
- **The nginx config / gate script / harness** are kept clean, commented, and gated by the static
  grep checker + the docker-gated live subset ONLY; the v1 `deploy/nginx.conf` is never modified.
- Each task references its requirement sub-clauses and the design component/section/property it
  satisfies (traceability), matching the B3 / B4 `tasks.md` style; every code/config task is
  followed by its test/gate note.

## Tasks

- [x] 1. Firewall-added-delta SLO module — `runtime/latency_slo.py` (NEW, the pure Python half, built first)
  - [x] 1.1 Implement the frozen value types + `exclude_lost_segment_tails`
    - Create `gateway_v2/gateway_v2/runtime/latency_slo.py`: frozen+slotted
      `PathSamples(samples_ms: tuple[float, ...])` (immutable client-side C4 samples, milliseconds),
      `PairedPathSamples(through_firewall: PathSamples, direct: PathSamples)` (the two paths over the
      SAME network; `direct` empty ⇒ error), and `FirewallDelta(delta_p99_ms, through_p99_ms,
      direct_p99_ms, excluded_through, excluded_direct)` — all `@dataclass(frozen=True, slots=True)`
      per `check_frozen_dataclasses` (R5.4)
    - Add the `Lost_Segment_Tail` threshold as a NAMED module constant (≈ 40 ms) with a docstring
      citing R2-12 ("≈ 40 ms lost-segment tails on every external path"), classified operational /
      statistical — NOT a capacity literal — parsed with the same warn-and-default env idiom as
      `serving_config.py` if an override is ever wired, default baked, no bare literal at the call
      site (R5.5)
    - `exclude_lost_segment_tails(samples: PathSamples) -> tuple[PathSamples, int]` drops every
      sample AT OR ABOVE the threshold and returns the trimmed `PathSamples` + the count excluded;
      every retained sample is strictly below the threshold; symmetric per-path trim (the honest
      definition for unpaired client fleets, design decision #4) (R5.2)
    - No module-level mutable, no HTTP type, no upward import; module ≤ 800 lines / functions ≤ 120
    - _Design: Components §2 (Firewall_Added_Delta), Data Models (value types + constant classification)_
    - _Requirements: 5.2, 5.4, 5.5_

  - [x] 1.2 Implement `firewall_added_delta` + `DirectBaselineMissing` + `assert_within_slo`
    - In `gateway_v2/gateway_v2/runtime/latency_slo.py`: `firewall_added_delta(paired:
      PairedPathSamples) -> FirewallDelta` computes `delta_p99_ms = p99(through_firewall, tails
      excluded) − p99(direct, tails excluded)`, both sides CLIENT-SIDE from the `PathSamples` inputs
      ONLY — the function never reads the gateway histogram and has no access to one; a comment at
      the type cites GW14d (histogram under-reads C4 3.2–5.8 ms, cannot certify) (R5.1, R5.3)
    - Define `class DirectBaselineMissing(ValueError)`; if `paired.direct.samples_ms` is empty,
      `firewall_added_delta` RAISES `DirectBaselineMissing` naming that the Direct_Path baseline is
      required, rather than returning an absolute latency — the firewall is never charged an absolute
      number (R5.6); an empty through-firewall path with a present baseline is also a measurement
      error and raises rather than returning a meaningless delta
    - `assert_within_slo(delta: FirewallDelta, contract: ResourceContract) -> None` reads the bound
      from `contract.target_p99_ms` (gate G-11's "firewall-added delta < 20 ms p99", runbook §0.5)
      and raises a clear error naming the measured `delta_p99_ms` and the `target_p99_ms` bound it
      exceeded iff `delta.delta_p99_ms > contract.target_p99_ms`; the bound comes from the one
      capacity authority, no literal in the module (R5.4, R5.5)
    - Add a comment naming the fleet C4 measurement (C39 synthetic client) as the deferred seam
      (GW20); honest typing, no `Any` leak
    - _Design: Components §2 (definition / tail exclusion / not-the-histogram / baseline-absent / SLO assert), Error Handling_
    - _Requirements: 5.1, 5.3, 5.4, 5.5, 5.6_

  - [x]* 1.3 Write the Property 1–3 tail-exclusion property tests (PBT)
    - `gateway_v2/tests/edge/test_latency_slo.py` — three property tests, seeded `random.Random`
      ≥ 100 iters, no hypothesis, over generated `PathSamples` (including tail samples, boundary
      values at the threshold, and empty tuples):
    - **Property 1: Tail exclusion never raises a path's p99** — **Validates: Requirements 5.2**: the
      p99 of `exclude_lost_segment_tails(samples)` is ≤ the p99 of `samples`
    - **Property 2: Tail exclusion is idempotent** — **Validates: Requirements 5.2**: applying
      `exclude_lost_segment_tails` twice yields the same trimmed samples as once (`f(f(x)) == f(x)`)
    - **Property 3: Excluded samples are exactly the at-or-above-threshold samples** — **Validates:
      Requirements 5.2**: the excluded count equals the number of input samples at or above the
      `Lost_Segment_Tail` threshold, and every retained sample is strictly below it
    - _Design: Correctness Properties → Properties 1, 2, 3_

  - [x]* 1.4 Write the Property 4–6 delta/baseline/SLO property tests (PBT)
    - `gateway_v2/tests/edge/test_latency_slo.py` (append) — three property tests, seeded
      `random.Random` ≥ 100 iters, no hypothesis:
    - **Property 4: Delta is the difference of tail-free path p99s** — **Validates: Requirements 5.1,
      5.2**: for any `PairedPathSamples` with a non-empty direct baseline, `firewall_added_delta`
      returns `delta_p99_ms == through_p99_ms − direct_p99_ms`, each `*_p99_ms` the p99 after
      symmetric tail exclusion
    - **Property 5: Absent direct baseline is an error, never an absolute latency** — **Validates:
      Requirements 5.6**: for any `PairedPathSamples` whose `direct` path has no samples,
      `firewall_added_delta` raises `DirectBaselineMissing` and returns no `FirewallDelta`
    - **Property 6: assert_within_slo honours the contract bound** — **Validates: Requirements 5.4,
      5.5**: for any `FirewallDelta` and any `ResourceContract`, `assert_within_slo` raises iff
      `delta_p99_ms` is strictly greater than `contract.target_p99_ms`
    - _Design: Correctness Properties → Properties 4, 5, 6_

  - [x]* 1.5 Write delta-module example/unit tests
    - `gateway_v2/tests/edge/test_latency_slo.py` (append): a sample set with no tail (exclusion is a
      no-op), a set that is all tail (p99 of the empty remainder handled), the exact ≈ 40 ms boundary
      sample (at-or-above dropped, strictly-below retained); the `DirectBaselineMissing` error
      message names the required baseline; the SLO-exceeded error message names the measured delta +
      the bound; a delta computed from `PathSamples` is NOT sourced from any gateway histogram
      (there is no code path reading metrics exposition); `assert_within_slo` passes a within-bound
      delta and raises an over-budget one
    - _Design: Testing Strategy (Firewall_Added_Delta tests — unit/EXAMPLE), Error Handling_
    - _Requirements: 5.1, 5.3, 5.4, 5.6_
  - Run the **Python-half gate** over `runtime/latency_slo.py` + `tests/edge/test_latency_slo.py`
    (`.venv/bin/python -m pytest`, `mypy --strict`, `ruff`, `lint-imports` 2 kept, the 6 AST gates
    incl. `check_capacity_literals`/`check_frozen_dataclasses`/`check_sizes`,
    `unscanned_channels()==()`, ≤ 800 / ≤ 120) before building on it.

- [x] 2. V2 edge nginx config — `gateway_v2/deploy/edge/` (NEW infra; v1 `deploy/nginx.conf` NOT edited)
  - [x] 2.1 Write the security-headers include — `security-headers.inc`
    - Create `gateway_v2/deploy/edge/security-headers.inc`: its OWN copy of the v1 security-header
      set (NOT a cross-tree include of `deploy/`) — `X-Content-Type-Options nosniff`,
      `X-Frame-Options DENY`, `Referrer-Policy strict-origin-when-cross-origin`,
      `Content-Security-Policy frame-ancestors 'none'`, `Strict-Transport-Security
      max-age=31536000`; reuses the `deploy/security-headers.inc` pattern (R4.2)
    - _Design: Components §1 (files table — security-headers.inc), Contrast with the v1 defect_
    - _Requirements: 4.2_

  - [x] 2.2 Write the upstream keepalive block + proxy location — `gateway-edge.conf`
    - Create `gateway_v2/deploy/edge/gateway-edge.conf` with the named `upstream b3_gateway { … }`
      block: `server ${B3_UPSTREAM};` (envsubst placeholder resolved to the B3 host:port at
      container start — default `host.docker.internal:8300`), `keepalive 64;` (the per-pair fix for
      0.9986 conns/req), `keepalive_timeout 30s;` and `keepalive_requests 1000;` aligned to
      `RV_CONN_MAX_AGE_S` with a comment pointing at `RV_CONN_MAX_AGE_S` (R1.1, R3.1, R3.2, R3.3)
    - The named `server { listen 80; server_name gateway.local; }` proxies `/` through the upstream
      with `proxy_pass http://b3_gateway;`, `proxy_http_version 1.1;`, and `proxy_set_header
      Connection "";` (clearing the hop-by-hop Connection header lets nginx reuse the pool), plus the
      forwarded-header set (`Host`, `X-Real-IP`, `X-Forwarded-For`, `X-Forwarded-Proto`) (R1.1, R1.2)
    - SSE transparency: `proxy_set_header X-Accel-Buffering no;` + `add_header X-Accel-Buffering no
      always;` as the MECHANISM, with `proxy_buffering off;` kept only as defence in depth (NOT the
      mechanism relied on), `proxy_cache off;`, `chunked_transfer_encoding on;` for streaming /
      chunked passthrough, and `proxy_read_timeout 400s;` + `proxy_send_timeout 400s;` covering a
      > 350 s stream (R1.5, R2.1, R2.5)
    - `include /etc/nginx/edge/security-headers.inc;` on the named server; `server_tokens off;`
    - _Design: Components §1 (upstream keepalive block / SSE transparency / connection recycling), Architecture_
    - _Requirements: 1.1, 1.2, 1.5, 2.1, 2.5, 3.1, 3.2, 3.3_

  - [x] 2.3 Add the default_server unknown-Host 403 catch-all — `gateway-edge.conf` (append)
    - Append to `gateway_v2/deploy/edge/gateway-edge.conf` a `listen 80 default_server; server_name
      _;` catch-all that returns 403 for an unrecognised Host (CWE-1021 Host isolation) and does NOT
      proxy or serve named content — the v1 pattern; it never falls through to the named vhost or a
      SPA (R4.1)
    - _Design: Components §1 (unknown-Host rejection + security headers), Error Handling (unknown Host)_
    - _Requirements: 4.1_

  - [x] 2.4 Write the edge container image + harness compose — `Dockerfile.edge` + `docker-compose.edge.yml`
    - Create `gateway_v2/deploy/edge/Dockerfile.edge`: builds the nginx image from the stock `nginx`
      base, COPYs `gateway-edge.conf` (as the templated conf under `/etc/nginx/templates/` so the
      stock image's `/docker-entrypoint.d/` envsubst resolves `${B3_UPSTREAM}` on start) + the
      `security-headers.inc` under `/etc/nginx/edge/`; the build runs `nginx -t` so a malformed conf
      never ships (Error Handling — edge config load failure)
    - Create `gateway_v2/deploy/edge/docker-compose.edge.yml`: an `nginx` service built from
      `Dockerfile.edge` publishing a host port, with `${B3_UPSTREAM}` pointing at the host B3 via
      `host.docker.internal` (`extra_hosts: ["host.docker.internal:host-gateway"]`); B3 is started by
      the test harness as a local uvicorn process on `127.0.0.1:8300` (the shipped `serve.py`) — the
      harness declares the nginx → B3 edge once (design decision #3, R6.1)
    - _Design: Components §1 (files — Dockerfile.edge / docker-compose.edge.yml), §3 (Local_Edge_Harness)_
    - _Requirements: 1.4, 6.1_

- [x] 3. CI edge regression gate (L16b-7) — `check_edge_keepalive.sh` + the pytest wrapper (static, no Docker)
  - [x] 3.1 Write the static keepalive grep gate — `check_edge_keepalive.sh`
    - Create `gateway_v2/deploy/edge/check_edge_keepalive.sh` (grep-shaped, mirrors
      `scripts/test_nginx_security.sh`'s always-run static section): assert `gateway-edge.conf`
      contains the `upstream` block AND a `keepalive` directive inside it, `proxy_http_version 1.1`,
      `proxy_set_header Connection ""`, and `X-Accel-Buffering no`; FAIL (non-zero exit) if the
      keepalive directive is removed (R7.1, R7.2)
    - Also statically assert the config carries `X-Accel-Buffering: no`, the `default_server` 403
      catch-all that does not serve named content, the five security headers, and `server_tokens
      off` — the same assertions as `test_nginx_security.sh`, so the Host/header posture (R4) is
      checked statically with no Docker; runs on the conf template (literal directives survive
      envsubst), no container needed (R7.3)
    - _Design: Components §4 (Edge_Regression_Gate), Testing Strategy (static conf gate)_
    - _Requirements: 4.1, 4.2, 7.1, 7.2, 7.3_

  - [x]* 3.2 Surface the gate via a pytest wrapper — `tests/edge/test_edge_config_gate.py`
    - Create `gateway_v2/tests/edge/test_edge_config_gate.py` that shells `check_edge_keepalive.sh`
      and asserts exit 0, so the static gate is collected by the standard `pytest` run and by CI as
      part of the `gateway_v2` edge test suite (R7.3); a negative-control assertion confirms the
      script exits non-zero when fed a conf fixture with the keepalive directive removed (R7.2); runs
      with NO Docker
    - _Design: Components §4 (pytest wrapper surfacing the gate), Testing Strategy (surfaced to CI)_
    - _Requirements: 7.1, 7.2, 7.3_

- [x] 4. Local_Edge_Harness + docker-gated live L16b subset — `tests/edge/test_edge_live_l16b.py` (gated behind Docker)
  - [x] 4.1 Build the harness driver + Docker-availability gate
    - Create `gateway_v2/tests/edge/test_edge_live_l16b.py` with the harness driver: bring the
      `docker-compose.edge.yml` nginx container up in front of a host B3 uvicorn process
      (`127.0.0.1:8300`, the shipped `serve.py`), tear down on exit; gate the whole live module
      behind Docker availability (`command -v docker` / an `AMF_EDGE_DOCKER` toggle, the same shape
      as `test_nginx_security.sh`'s `NGINX_DOCKER` / `SKIP_NGINX_DOCKER`) so it SKIPS cleanly when
      Docker is absent (R6.1)
    - _Design: Components §3 (Local_Edge_Harness, docker-gated), Testing Strategy (docker-gated live subset)_
    - _Requirements: 6.1_

  - [x]* 4.2 L16b-1 — conns/request ≤ 0.01 (integration)
    - In `gateway_v2/tests/edge/test_edge_live_l16b.py`: read the new-upstream-connection counter
      before/after a fixed request count at the qualified local plateau (nginx `stub_status` active/
      handled counters, or the `/proc/net` SYN-count fallback decided at harness-build time — the
      honest metric is "new upstream TCP connections per request"), assert `new_upstream_connections
      / requests ≤ 0.01`; 1–3 representative plateau runs (R1.3, R6.2)
    - _Design: Components §3 (L16b-1), Testing Strategy (L16b-1)_
    - _Requirements: 1.3, 6.2_

  - [x]* 4.3 L16b-3 — fixed-interval SSE per-token read + hold ≤ 1 ms + 30 ms injected hold detected (integration)
    - In `gateway_v2/tests/edge/test_edge_live_l16b.py`: a fixed-interval SSE source (the B3 stub
      provider emits one token per fixed interval) + a client timestamping each read; assert each
      token arrives in its OWN read, max hold ≤ 1 ms, and that a 30 ms injected hold is DETECTED (the
      test injects a 30 ms pause and confirms the client sees it — proving the edge is transparent,
      not smoothing); 1–3 examples (R2.2, R2.3, R2.4, R6.3)
    - _Design: Components §3 (L16b-3), Testing Strategy (L16b-3)_
    - _Requirements: 2.2, 2.3, 2.4, 6.3_

  - [x]* 4.4 L16b-6 — unknown Host 403 + security headers over the live edge (integration)
    - In `gateway_v2/tests/edge/test_edge_live_l16b.py`: `curl` with `Host: evil.example` → 403;
      `curl` a served response → assert the five security headers present (direct reuse of the
      `test_nginx_security.sh` header assertions); 1–3 examples (R4.1, R4.2, R4.3, R6.4)
    - _Design: Components §3 (L16b-6), Testing Strategy (L16b-6)_
    - _Requirements: 4.1, 4.2, 4.3, 6.4_

  - [x]* 4.5 L16b-4 — short proxy-passthrough proof (integration)
    - In `gateway_v2/tests/edge/test_edge_live_l16b.py`: a bounded stream longer than a short bound
      (e.g. a few seconds) passes through the edge without the edge closing it, demonstrating the
      `proxy_read_timeout 400s` config supports a > 350 s stream; the full > 350 s soak is a
      Deferred_Seam (R8.6) and is NOT run here; 1–3 examples (R2.5, R6.5)
    - _Design: Components §3 (L16b-4, short passthrough proof; full soak deferred), Testing Strategy_
    - _Requirements: 2.5, 6.5_

- [x] 5. Checkpoint — delta module + edge config + static gate + harness green
  - Ensure all tests pass, ask the user if questions arise. Run the **Python-half gate** over
    `runtime/latency_slo.py` + `tests/edge/test_latency_slo.py` (`.venv/bin/python -m pytest`,
    `mypy --strict`, `ruff check gateway_v2 state_control lint tests`, `lint-imports` 2 kept, the 6
    AST gates — `check_capacity_literals`, `check_no_module_mutable`, `check_frozen_dataclasses`,
    `check_http_outside_edge_resolve`, `check_sizes`, `check_tenant_scale`; `unscanned_channels()`
    still `()`, module ≤ 800 / functions ≤ 120); run the **static edge-config gate**
    (`test_edge_config_gate.py`, no Docker); and, where Docker is available, the live L16b subset
    (`test_edge_live_l16b.py`), confirming it SKIPS cleanly when Docker is absent. Do NOT apply the
    Python gates to the nginx conf / shell / compose artifacts.

- [x] 6. Edge drain contract + deferred seams recorded as named references (doc/verification task)
  - [x] 6.1 Document the Edge_Drain_Contract + name every Deferred_Seam next to the config
    - Add to `gateway_v2/deploy/edge/gateway-edge.conf` (comment block) + a
      `gateway_v2/deploy/edge/README.md`: the Edge_Drain_Contract — on MIG scale-in the NLB drains
      300 s before VM deletion + SIGTERM so streams up to ≈ 375 s survive, the in-gateway drain owned
      by B3 (`serve.py install_drain_hook`, `RV_DRAIN_ACCEPT_S`/`RV_DRAIN_S`); B5 documents only the
      edge/NLB side, builds no drain code, simulates no fleet drill (R8.1)
    - Record the settled topology (regional external passthrough NLB → gateways Option A; regional
      ALB rejected for token streams) and name each Deferred_Seam with its owning card/gate, nothing
      scaffolded: NLB bake-off → cloud/GW20 (R8.2); TLS termination in the gateway tier — a defined
      uvicorn `ssl_keyfile`/`ssl_certfile` (or edge `listen 443 ssl`) KNOB, cost deferred to G-10, no
      cert handling scaffolded (R8.3); internet-vantage latency → G-11, no measurement scaffolded
      (R8.4); store outage behind a health-checked NLB → G-13, no drill scaffolded (R8.5); edge ≥ 2×
      fleet qualified RPS → L16b-2/GW20, full > 350 s soak → L16b-4 (config supports it, soak
      deferred), drain-one-gateway drill → L16b-5 (documented via Edge_Drain_Contract, fleet drill
      deferred) (R8.6). Also name the fleet C4 (C39) seam next to `runtime/latency_slo.py` (GW20)
    - This is a doc/verification task — a `grep` confirming each Deferred_Seam reference in the
      config or the delta module names its owning card or gate (R8.7); NO scaffolding is added
    - _Design: Edge drain contract and deferred seams (R8.1 table), Design decisions #6 (TLS knob)_
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7_

- [x] 7. Final verification gate
  - [x] 7.1 Run the full gate and map evidence to the §10.9 card format
    - **Python half** — from cwd `gateway_v2` with venv `gateway_v2/.venv`, run over
      `runtime/latency_slo.py` + `tests/edge/test_latency_slo.py`: `.venv/bin/python -m pytest` (full
      offline suite + the delta property/unit subset), `mypy --strict`, `ruff check gateway_v2
      state_control lint tests`, `lint-imports` (2 contracts kept, no new edge), the 6 AST gates
      (`check_capacity_literals`, `check_no_module_mutable`, `check_frozen_dataclasses`,
      `check_http_outside_edge_resolve`, `check_sizes`, `check_tenant_scale`); confirm
      `unscanned_channels()` is still `()` and the module stays ≤ 800 lines / functions ≤ 120. Fix
      any gate failure before closing
    - **Static edge-config gate** — run `test_edge_config_gate.py` (shells `check_edge_keepalive.sh`,
      no Docker) and confirm it passes AND fails the negative control (keepalive removed). This gate
      is an ALWAYS-RUN exit evidence line
    - **Live L16b subset** — where Docker is available, run `test_edge_live_l16b.py` (L16b-1/-3/-6/-4)
      against the Local_Edge_Harness. Note EXPLICITLY in the evidence that these docker live tests
      SKIP cleanly when Docker is unavailable, and that the static gate + the Python-half gate are the
      ALWAYS-RUN exit evidence
    - Map the evidence to §10.9 (why / implementation / live-acceptance / exit / evidence /
      rollback): why = make the edge transparent to streaming and never the capacity limit (fix the
      0.9986-conns/req / 502-above-420-RPS defect) + certify the firewall-added delta client-side;
      implementation = `runtime/latency_slo.py` + the `gateway_v2/deploy/edge/` config (conf +
      include + Dockerfile + compose) + `check_edge_keepalive.sh` + the two edge test modules;
      live-acceptance = the static keepalive/header gate + (Docker-gated) L16b-1/-3/-6/-4 transcripts
      + the six delta property tests; exit = Python gate green + static gate green (+ live subset
      where Docker present); evidence = the gate transcripts; rollback = B5 is additive
      (`runtime/latency_slo.py`, the new `gateway_v2/deploy/edge/` tree, the two edge test modules),
      reverting removes the v2 edge + the delta module with no change to the v1 `deploy/nginx.conf`
      or any shipped `gateway_v2` module. Record the deferred seams (NLB bake-off, TLS cost G-10,
      G-11, G-13, L16b-2/4-soak/5) as such — NOT closed here
    - _Design: Testing Strategy (the Python-half gate / static conf gate / docker-gated live subset), Mapping to §10.9_
    - _Requirements: 7.3, 8.1, 8.6_

- [x] 8. Final checkpoint
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional test sub-tasks (property, unit, integration, live) and can be
  skipped for a faster MVP; core implementation sub-tasks are never optional. The agent MUST
  implement un-starred sub-tasks and MUST NOT implement starred ones.
- Each task references specific requirement sub-clauses and the design component/section/property it
  satisfies for traceability; collectively the tasks cover all 8 requirements per the design.
- Checkpoints (tasks 5, 8) ensure the Python-half gate (`mypy --strict`, `ruff`, `lint-imports` 2
  contracts, the 6 AST gates incl. `check_sizes`, `unscanned_channels()` empty) is green over
  `runtime/latency_slo.py` + its tests, and that the static edge-config gate + the docker-gated live
  subset run; each code/config task is also followed by a note to run the appropriate gate.
- **The Python gate applies to the Python half ONLY** — `runtime/latency_slo.py` +
  `tests/edge/test_latency_slo.py`. The nginx conf, `security-headers.inc`, `Dockerfile.edge`,
  `docker-compose.edge.yml`, and `check_edge_keepalive.sh` are infra / shell / test artifacts gated
  by the static grep checker + the docker-gated live subset, NOT by pytest/mypy/ruff/lint-imports/
  the 6 AST gates. The v1 `deploy/nginx.conf` is reference only and is never edited.
- Property tests use a seeded `random.Random` for ≥ 100 iterations, no hypothesis; one property ↔
  one test; tagged `# Feature: edge-gw16b, Property N` and `# Validates: Requirements X.Y`. Six
  properties exist (Properties 1–3 tail-exclusion invariants, Property 4 delta definition, Property
  5 baseline-absent error, Property 6 SLO-bound honouring) — the design's PBT prework classifies ALL
  nginx edge behaviours as INTEGRATION (docker-gated, 1–3 examples), so no property sub-task is
  written for the config.
- Capacity literals live only in `runtime/resources.py`: the SLO bound is read via
  `ResourceContract.target_p99_ms`; the ≈ 40 ms `Lost_Segment_Tail` threshold is an operational /
  statistical constant (a measured network artefact per R2-12), a named module constant classified
  like `serving_config.py`'s operational defaults. The delta value types are frozen+slotted with no
  module-level mutable; the module has no HTTP type and adds no import edge.
- The docker-gated live L16b tests (`test_edge_live_l16b.py`) SKIP cleanly when Docker is
  unavailable (the `test_nginx_security.sh` `NGINX_DOCKER` shape); the static edge-config gate +
  the Python-half gate are the ALWAYS-RUN exit evidence.
- Deferred seams (task 6) — NLB bake-off (cloud/GW20), TLS cost (G-10, knob defined), internet
  vantage (G-11), store outage behind NLB (G-13), edge ≥ 2× fleet RPS (L16b-2/GW20), full > 350 s
  soak (L16b-4), drain-one-gateway drill (L16b-5), fleet C4 measurement (C39/GW20) — have NO
  implementation tasks; they stay named references in the edge config / README / delta module,
  nothing scaffolded.
- Shared-file coordination: §1.3 + §1.4 + §1.5 all append to `tests/edge/test_latency_slo.py`, so
  they are serialized into distinct waves (append-only). The nginx conf is written by §2.2 then
  appended by §2.3, so those are serialized into distinct waves to avoid write conflicts. §1.1
  before §1.2 (the value types + exclusion before the delta/assert functions that use them). §4.1
  (the harness driver) before the live L16b sub-tasks §4.2–§4.5 that drive it; §3.1 (the script)
  before §3.2 (the pytest wrapper that shells it).

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "2.1", "2.2", "3.1"] },
    { "id": 1, "tasks": ["1.2", "2.3", "3.2", "4.1"] },
    { "id": 2, "tasks": ["1.3", "2.4", "4.2", "4.3", "4.4", "4.5"] },
    { "id": 3, "tasks": ["1.4"] },
    { "id": 4, "tasks": ["1.5"] },
    { "id": 5, "tasks": ["6.1"] },
    { "id": 6, "tasks": ["7.1"] }
  ]
}
```
