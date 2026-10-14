# Requirements Document

## Introduction

Card B5 of the `gateway_v2/` backend-rewrite effort implements **GW16b — Production edge:
upstream keepalive + SSE transparency + firewall-added-delta SLO**. The source of truth is the
runbook `docs/AI_MESH_MASTER_RUNBOOK_v3_BACKEND_REWRITE.md` §601 (the GW16b card) together with
its §1.2 v3 AMENDMENT (R2-12, R2-16) and the R2-12 register row. The card's objective, verbatim:
**"Make the edge transparent to streaming and never the capacity limit."** It depends on GW12 and
GW19 and is required by GW20 and GW22.

GW16b sits **in front of** the B3 serving entrypoint (`gateway_v2/gateway_v2/edge/serve.py`), which
already hosts the shipped ASGI application under uvicorn, binds a **plain TCP socket** on
`RV_HOST`/`RV_PORT` (default `127.0.0.1:8300`), and applies `RV_CONN_MAX_AGE_S` (default 30 s) as
the uvicorn `timeout_keep_alive`. B3 explicitly **deferred** "real TLS termination at the edge + NLB
bake-off / internet-vantage" to B5/GW16b (B3 `serve.py` R7.3). GW16b builds that edge.

**Why (the measured defect).** Per §601: without an nginx `upstream` keepalive block the
repository's nginx opens **0.9986 new TCP connections per request** and fails with **HTTP 502 above
~420 req/s** per edge→gateway pair (ephemeral-port exhaustion inside the container network
namespace; host sysctls do not reach it). WITH upstream keepalive the same edge sustained
**≥ 30,000 req/s at p99 < 0.4 ms**. SSE is protected by **`X-Accel-Buffering: no`, NOT by
`proxy_buffering`**. The repository's v1 edge `deploy/nginx.conf` exhibits exactly this defect: its
`/v1/` location sets `proxy_buffering off` but has **no `upstream` keepalive block** and **no
`X-Accel-Buffering: no`**. `deploy/nginx.conf` is treated as **reference** for the v2 edge, not the
thing to edit — GW16b builds a new v2 edge for the `gateway_v2` serving path.

**The SLO contract.** Per §1.2 (R2-06/R2-11/R2-12, runbook line 686) and the R2-12 correction: the
SLO is certified **from the client side, as the firewall-added delta against a direct path over the
same network** (C4 from a black-box synthetic client, C39). The gateway's own histogram under-reads
C4 by **3.2–5.8 ms** and **cannot certify it** (GW14d). Rare **≈ 40 ms lost-segment tails** appear
on **every external path**, including a direct external IP with **no firewall** in it; the delta
method keeps them **out of the firewall's account**. GW16b owns the **definition** of this delta
(how C4 is computed and asserted client-side), not the fleet measurement itself.

B5 is a **category-2** card: the **LOCAL half** is built and verified locally; the fleet/cloud
halves stay explicit named seams (see Requirement 8). Scope is the **local half only**.

### Scope split — LOCAL half vs DEFERRED cloud gates (settled by the runbook, baked in)

**LOCAL-BUILDABLE (this card builds + verifies locally):**

1. The **v2 edge nginx config** for the `gateway_v2` serving path, placed under `gateway_v2/`
   (not editing the v1 `deploy/nginx.conf`): an `upstream` block **with keepalive** to the B3
   uvicorn gateway (the 0.9986-conns/request / 502-above-420-RPS fix); `X-Accel-Buffering: no` for
   SSE (not relying on `proxy_buffering`); `proxy_http_version 1.1` + `Connection ""` on the
   upstream (keepalive correctness); connection recycling aligned to `RV_CONN_MAX_AGE_S` (30 s);
   unknown-Host rejection (default_server, CWE-1021) + security headers; an SSE read timeout
   covering a > 350 s stream; chunked / streaming passthrough.
2. The **firewall-added-delta SLO definition**: a small, testable `gateway_v2` definition of how C4
   is computed **client-side** as the delta of the through-firewall path vs a direct path over the
   same network (§1.2 / C39), **excluding** the ≈ 40 ms lost-segment tails present on both paths,
   and **not** trusting the gateway's own under-reading histogram. A measurement **definition**, not
   the fleet measurement.
3. The locally-runnable subset of the L16b tests: **L16b-1** (upstream connections per request
   ≤ 0.01 against a local nginx→uvicorn), **L16b-3** (fixed-interval SSE: each token in its own
   read, max hold ≤ 1 ms, a 30 ms injected hold detected), **L16b-6** (unknown Host rejected +
   security headers present), **L16b-7** (removing keepalive fails the edge test = CI regression
   gate). These run against a **local nginx in front of the B3 gateway**.

**DEFERRED (named cloud/fleet seams, NOT scaffolded here — see Requirement 8):**

- **L16b-2** (edge sustains ≥ 2× the fleet's qualified RPS adding < 1 ms p99) — needs the fleet
  (GW20).
- **L16b-4** (healthy stream > 350 s) — a soak/long-run; the config MUST support it
  (read timeout), a short local proxy-passthrough proof is in scope, the full > 350 s soak is
  deferred.
- **L16b-5** (drain one gateway during active streams per contract) — the NLB 300 s pre-drain +
  MIG scale-in is cloud; B3 already owns the in-gateway SIGTERM drain, so B5 **documents** the
  edge/NLB drain contract and defers the fleet drill.
- **G-10** (TLS termination cost at 600 RPS), **G-11** (internet-vantage latency), **G-13** (store
  outage behind a health-checked NLB), and the **NLB bake-off vs ALB** — all cloud. TLS termination
  in the gateway tier is a uvicorn `ssl_*` / cert operational concern; B5 may **define** the config
  knob but the cost measurement is deferred.

### Grounded decisions (resolved from the runbook — not open questions)

- **Edge topology = regional external passthrough NLB → gateways (Option A)**, with **TLS
  terminated in the gateway tier**. The regional **ALB added 8–10 ms p99 and is REJECTED for token
  streams**. (§1.2 AMENDMENT, R2-12 row.) The NLB/ALB bake-off itself is a cloud gate; B5 records
  the decision as settled, not a thing to re-measure locally.
- **Connection recycling is mandatory** because the NLB balances per connection: without it a
  scaled-out gateway received 0–0.2 req/s while the old one kept 40. The recycling bound is
  `RV_CONN_MAX_AGE_S` (30 s). (§1.2 AMENDMENT, §0.2 NLB-balancing row.)
- **SSE transparency comes from `X-Accel-Buffering: no`, not `proxy_buffering`.** (§601 Why.)
- **The upstream keepalive block is the fix** for 0.9986 conns/request and the 502-above-420-RPS
  wall. (§601 Why; C20.)
- **The SLO is the client-side firewall-added delta vs a direct path over the same network,
  excluding the ≈ 40 ms lost-segment tails, and the gateway's own histogram cannot certify it.**
  (§1.2 line 686; R2-11/R2-12; GW14d.)
- **On MIG scale-in the NLB drains 300 s before VM deletion + SIGTERM**, so streams up to ≈ 375 s
  survive. B3 already owns the in-gateway drain (`RV_DRAIN_ACCEPT_S` 15 s + `RV_DRAIN_S` 60 s). B5
  documents the **edge** side of that contract. (§1.2 AMENDMENT, §0.2 NLB-balancing row.)

### New ambiguities (not fully settled by the runbook — flagged, non-blocking)

1. **Where the v2 edge config lives and how it is tested locally.** The runbook does not pin a
   path inside `gateway_v2/` (cloud is NLB-fronted). Lean: a new `gateway_v2/deploy/edge/` nginx
   conf plus a scripted local nginx→uvicorn harness (or a docker-compose) that stands the edge in
   front of the running B3 server. This is a **design** choice, captured in Requirement 1 and
   Requirement 6; it does not block writing requirements. Flagged for user review.
2. **Whether the firewall-added-delta SLO definition is a `gateway_v2` module + tests or only
   documentation.** Lean: a small, testable `gateway_v2` module so the delta definition is
   **CI-checkable** (L16b/delta assertable), rather than prose only. Captured in Requirement 5.
   Flagged for user review; the lean is baked into the acceptance criteria.
3. **TLS termination at the edge in the local half.** The runbook terminates TLS in the gateway
   tier and leaves the **cost** a deferred gate (G-10). B5 may **define** the uvicorn
   `ssl_keyfile`/`ssl_certfile` config knob and the plaintext-vs-TLS listen decision without
   measuring cost. Captured in Requirement 8 (knob defined, cost deferred). Flagged for user
   review.

## Glossary

- **V2_Edge**: The reverse proxy (nginx) that fronts the B3 `gateway_v2` serving entrypoint for the
  `gateway_v2` serving path. The subject of this spec. Distinct from the v1 product edge
  (`deploy/nginx.conf`), which is reference only.
- **B3_Gateway**: The running B3 serving entrypoint (`gateway_v2/gateway_v2/edge/serve.py`) hosting
  the shipped ASGI application under uvicorn on `RV_HOST`/`RV_PORT` (default `127.0.0.1:8300`),
  honoring `RV_CONN_MAX_AGE_S` as `timeout_keep_alive`. The V2_Edge's upstream.
- **Upstream_Keepalive_Block**: The nginx `upstream { ... keepalive N; }` block, used with
  `proxy_http_version 1.1` and `proxy_set_header Connection ""`, that pools and reuses TCP
  connections to the B3_Gateway instead of opening a new connection per request.
- **SSE_Stream**: A Server-Sent-Events token stream (OpenAI-style `text/event-stream`) proxied from
  the B3_Gateway through the V2_Edge to the client.
- **Firewall_Added_Delta**: The client-side C4 latency contribution attributable to the AI Mesh
  firewall, computed as the difference between the through-firewall path and a direct path measured
  over the same network, with the lost-segment tails excluded. The SLO is certified against this
  delta, not against the gateway's own histogram.
- **Direct_Path**: A reference path to the same backend over the same network **without** the
  firewall edge in it, used as the baseline for the Firewall_Added_Delta.
- **Lost_Segment_Tail**: A rare ≈ 40 ms latency tail (the lost-segment TCP signature) that appears
  on **every** external path, including a direct external IP with no firewall; excluded from the
  firewall's account by the delta method.
- **Connection_Recycling**: Bounding the age/request-count of a pooled upstream keepalive
  connection, aligned to `RV_CONN_MAX_AGE_S` (30 s), so the per-connection-balancing NLB can
  redistribute connections to scaled-out gateways.
- **Unknown_Host_Rejection**: The nginx `default_server` catch-all that rejects requests whose Host
  does not match a configured server name (CWE-1021 Host isolation), returning a rejection rather
  than serving a named vhost's content.
- **Security_Headers**: The browser security headers applied edge-wide (the
  `deploy/security-headers.inc` pattern: `X-Content-Type-Options`, `X-Frame-Options`,
  `Referrer-Policy`, `Content-Security-Policy frame-ancestors`, `Strict-Transport-Security`).
- **Local_Edge_Harness**: The locally-runnable setup that stands the V2_Edge (nginx) in front of a
  running B3_Gateway (uvicorn) so the L16b local subset can be executed without the fleet.
- **Edge_Regression_Gate**: The CI check that fails when the Upstream_Keepalive_Block is removed
  from the V2_Edge config (L16b-7).
- **Edge_Drain_Contract**: The documented edge/NLB side of scale-in: the NLB drains 300 s before VM
  deletion and SIGTERM, so streams up to ≈ 375 s survive; the in-gateway drain is owned by B3.
- **Deferred_Seam**: A real documented boundary whose cloud/fleet half is a separate card and is
  NOT scaffolded here (listed in Requirement 8).

## Requirements

### Requirement 1: V2 edge with upstream keepalive to the B3 gateway

**User Story:** As a gateway operator, I want the v2 edge to reuse pooled TCP connections to the B3
gateway through an upstream keepalive block, so that the edge does not open a new connection per
request and does not fail with 502 under load (the measured 0.9986-conns/request /
502-above-420-RPS defect).

#### Acceptance Criteria

1. THE V2_Edge SHALL define an Upstream_Keepalive_Block that targets the B3_Gateway and SHALL proxy
   `gateway_v2` serving-path requests through that block.
2. THE V2_Edge SHALL set `proxy_http_version 1.1` and SHALL clear the upstream `Connection` header
   (`proxy_set_header Connection ""`) on requests proxied to the B3_Gateway.
3. WHEN the V2_Edge proxies a sequence of requests to the B3_Gateway over the Local_Edge_Harness,
   THE V2_Edge SHALL reuse pooled upstream connections such that new upstream TCP connections per
   request measured at the qualified local plateau is at most 0.01 (L16b-1).
4. THE V2_Edge SHALL be a configuration under `gateway_v2/` for the `gateway_v2` serving path and
   SHALL NOT modify the v1 product edge configuration `deploy/nginx.conf`.
5. WHERE the V2_Edge proxies to the B3_Gateway, THE V2_Edge SHALL pass streaming and chunked
   responses through to the client without buffering the full response body.

### Requirement 2: SSE transparency

**User Story:** As an API client streaming tokens, I want each token to reach me as the gateway
emits it, so that the edge is transparent to streaming and never holds or buffers tokens.

#### Acceptance Criteria

1. WHEN the V2_Edge proxies an SSE_Stream, THE V2_Edge SHALL set `X-Accel-Buffering: no` for that
   response and SHALL NOT rely on `proxy_buffering` alone for SSE transparency.
2. WHEN the B3_Gateway emits a token on a fixed-interval SSE_Stream, THE V2_Edge SHALL deliver that
   token to the client in its own read (L16b-3).
3. WHILE proxying an SSE_Stream at a fixed token interval, THE V2_Edge SHALL hold each token no
   longer than 1 millisecond before delivering it to the client (L16b-3).
4. WHEN a 30-millisecond hold is injected into a fixed-interval SSE_Stream, THE Local_Edge_Harness
   SSE test SHALL detect that hold (L16b-3).
5. THE V2_Edge SHALL set an SSE upstream read timeout that covers a stream lasting longer than 350
   seconds (L16b-4 configuration support).

### Requirement 3: Connection recycling aligned to RV_CONN_MAX_AGE_S

**User Story:** As a gateway operator, I want pooled upstream connections recycled on a bounded age
aligned to `RV_CONN_MAX_AGE_S`, so that the per-connection-balancing NLB can redistribute traffic
to scaled-out gateways (without recycling a scaled-out gateway received 0–0.2 req/s).

#### Acceptance Criteria

1. THE V2_Edge SHALL bound the lifetime of a pooled upstream keepalive connection with a
   Connection_Recycling bound aligned to `RV_CONN_MAX_AGE_S` (30 seconds).
2. WHEN a pooled upstream connection reaches its Connection_Recycling bound, THE V2_Edge SHALL close
   that connection and establish a fresh connection for subsequent requests.
3. THE V2_Edge Connection_Recycling bound SHALL be consistent with the B3_Gateway
   `timeout_keep_alive` derived from `RV_CONN_MAX_AGE_S`, so the edge and gateway recycle on an
   aligned age.

### Requirement 4: Unknown-Host rejection and security headers

**User Story:** As a security reviewer, I want the v2 edge to reject requests with an unrecognized
Host and to serve browser security headers, so that Host-isolation (CWE-1021) holds and
defense-in-depth headers are present.

#### Acceptance Criteria

1. WHEN the V2_Edge receives a request whose Host does not match a configured server name, THE
   V2_Edge SHALL reject that request with a rejection status rather than serving named-vhost content
   (Unknown_Host_Rejection, CWE-1021) (L16b-6).
2. THE V2_Edge SHALL respond with the Security_Headers on served responses (L16b-6).
3. WHEN the Local_Edge_Harness security test issues a request with an unknown Host, THE test SHALL
   assert the request is rejected and that the Security_Headers are present on a served response
   (L16b-6).

### Requirement 5: Firewall-added-delta SLO definition

**User Story:** As an SLO owner, I want a testable definition of the firewall-added delta computed
client-side against a direct path, so that the latency SLO is certified from the client vantage,
excludes the lost-segment tails, and does not trust the gateway's own under-reading histogram.

#### Acceptance Criteria

1. THE Firewall_Added_Delta SHALL be defined as the client-side C4 latency of the through-firewall
   path minus the client-side C4 latency of the Direct_Path measured over the same network.
2. WHEN the Firewall_Added_Delta is computed, THE definition SHALL exclude the Lost_Segment_Tail
   samples that appear on both the through-firewall path and the Direct_Path.
3. THE Firewall_Added_Delta definition SHALL be computed from client-side measurements and SHALL NOT
   be sourced from the gateway's own request-latency histogram.
4. THE Firewall_Added_Delta SHALL be implemented as a testable `gateway_v2` definition that computes
   and asserts the delta from paired client-side path measurements.
5. WHERE the Firewall_Added_Delta definition is implemented in `gateway_v2`, THE implementation
   SHALL NOT introduce any capacity literal outside
   `gateway_v2/gateway_v2/runtime/resources.py`.
6. IF the Direct_Path baseline samples are absent when a Firewall_Added_Delta is requested, THEN THE
   definition SHALL signal an error rather than attributing absolute latency to the firewall.

### Requirement 6: Local edge harness and the L16b local subset

**User Story:** As a developer, I want the v2 edge runnable locally in front of the B3 gateway with
the L16b local subset executable, so that the edge's connections-per-request, SSE transparency, and
Host/header behaviour are verified without the fleet.

#### Acceptance Criteria

1. THE Local_Edge_Harness SHALL stand the V2_Edge in front of a running B3_Gateway on a local
   socket.
2. WHEN the L16b-1 local test runs against the Local_Edge_Harness, THE test SHALL assert new
   upstream TCP connections per request is at most 0.01 at the qualified local plateau.
3. WHEN the L16b-3 local test runs against the Local_Edge_Harness, THE test SHALL assert each
   fixed-interval SSE token arrives in its own read, each token is held at most 1 millisecond, and a
   30-millisecond injected hold is detected.
4. WHEN the L16b-6 local test runs against the Local_Edge_Harness, THE test SHALL assert an unknown
   Host is rejected and the Security_Headers are present.
5. WHEN the L16b-4 local proxy-passthrough proof runs against the Local_Edge_Harness, THE test SHALL
   assert a stream longer than a short bounded duration passes through without the edge closing it,
   demonstrating the SSE read-timeout configuration supports a > 350-second stream.

### Requirement 7: CI edge regression gate

**User Story:** As a reviewer, I want CI to fail if the upstream keepalive block is removed from the
v2 edge, so that the measured conns-per-request fix cannot silently regress.

#### Acceptance Criteria

1. THE Edge_Regression_Gate SHALL assert the V2_Edge config contains an Upstream_Keepalive_Block.
2. IF the Upstream_Keepalive_Block is removed from the V2_Edge config, THEN THE Edge_Regression_Gate
   SHALL fail (L16b-7).
3. THE Edge_Regression_Gate SHALL run as part of the `gateway_v2` edge test suite in CI.

### Requirement 8: Edge drain contract and deferred cloud gates recorded as named seams

**User Story:** As a reviewer, I want the edge/NLB drain contract documented and the cloud/fleet
gates recorded as explicit named seams, so that the boundary between the local build and the
deferred work stays visible and nothing is silently scaffolded.

#### Acceptance Criteria

1. THE V2_Edge SHALL document the Edge_Drain_Contract: on MIG scale-in the NLB drains 300 seconds
   before VM deletion and SIGTERM, so streams up to approximately 375 seconds survive, with the
   in-gateway drain owned by B3.
2. THE V2_Edge SHALL record the regional external passthrough NLB → gateways (Option A) topology and
   the rejection of the regional ALB for token streams as settled decisions, and SHALL record the
   NLB bake-off as a Deferred_Seam not re-measured locally.
3. THE V2_Edge SHALL record TLS termination in the gateway tier as a defined configuration knob and
   SHALL record the TLS cost measurement (gate G-10) as a Deferred_Seam not measured locally.
4. THE V2_Edge SHALL record internet-vantage latency (gate G-11) as a Deferred_Seam and SHALL NOT
   scaffold an internet-vantage measurement.
5. THE V2_Edge SHALL record the store outage behind a health-checked NLB (gate G-13) as a
   Deferred_Seam and SHALL NOT scaffold a store-outage drill.
6. THE V2_Edge SHALL record the edge sustaining ≥ 2× the fleet's qualified RPS (L16b-2, GW20), the
   full > 350-second healthy-stream soak (L16b-4), and the drain-one-gateway-during-active-streams
   drill (L16b-5) as Deferred_Seams and SHALL NOT scaffold fleet-scale behaviour.
7. WHERE a Deferred_Seam is referenced in the V2_Edge config or the `gateway_v2` delta definition,
   THE reference SHALL name the owning card or gate.
