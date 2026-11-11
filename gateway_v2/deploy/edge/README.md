# V2 edge — GW16b / card B5 (production edge: upstream keepalive + SSE transparency)

This directory is the **local half** of the production edge for the `gateway_v2` serving path. It
fronts the B3 serving entrypoint (`gateway_v2/gateway_v2/edge/serve.py`, a plain-TCP uvicorn host on
`RV_HOST`/`RV_PORT`, default `127.0.0.1:8300`) and fixes the measured v1 defect: no upstream
keepalive block (0.9986 new TCP connections per request → HTTP 502 above ~420 req/s per
edge→gateway pair) and no `X-Accel-Buffering: no` for SSE. With the upstream keepalive block the
same edge sustained ≥ 30,000 req/s at p99 < 0.4 ms.

GW16b is a **category-2** card: the local half is built and verified here; the fleet/cloud halves
stay explicit, named `Deferred_Seam`s (see below). Nothing in those seams is scaffolded.

## Edge_Drain_Contract (R8.1)

On MIG scale-in, the regional external passthrough NLB **drains 300 s before VM deletion and
SIGTERM**, so in-flight streams of up to ≈ **375 s** survive a scale-in event.

The **in-gateway** drain is owned by **B3**, not by this edge:
`gateway_v2/gateway_v2/edge/serve.py` `install_drain_hook` drives the GW19 drain state machine
(`ACCEPTING → DRAINING → TERMINATING → FLUSHED → EXITED`) using `RV_DRAIN_ACCEPT_S` (15 s) +
`RV_DRAIN_S` (60 s).

**B5 documents only the edge/NLB side of this contract.** It builds no drain code and simulates no
fleet drain drill. The drain-one-gateway-during-active-streams drill is **L16b-5** (deferred — see
below).

## Settled topology (R8.2)

- Edge topology = **regional external passthrough NLB → gateways (Option A)**, with **TLS terminated
  in the gateway tier**.
- The regional **ALB is REJECTED for token streams**: it added **8–10 ms p99**. The passthrough NLB
  measured the same as a plain external IP (Δ C4 p99 −0.12 … +0.27 ms).
- The NLB **balances per connection**, which is why connection recycling aligned to
  `RV_CONN_MAX_AGE_S` (30 s) is mandatory — without it a scaled-out gateway received 0–0.2 req/s
  while the old one kept 40.
- The **NLB bake-off vs ALB is a `Deferred_Seam`** (cloud / **GW20**): recorded as settled, **not
  re-measured locally**.

The local half stands the V2_Edge (nginx) directly in front of one B3_Gateway over loopback — there
is no local NLB. The NLB-specific behaviour (per-connection balancing, 300 s pre-drain) is
documented here and in `gateway-edge.conf`, not simulated.

## Deferred cloud gates — each named with its owning card/gate (R8.3–R8.6)

Nothing below is scaffolded in this card.

| Deferred_Seam | Owner | Status |
|---|---|---|
| NLB bake-off vs ALB (Option A settled; ALB rejected for streams) | cloud / **GW20** | settled decision, not re-measured locally (R8.2) |
| TLS termination in the gateway tier — **knob defined**, cost deferred | **G-10** | knob defined (below); cost at 600 RPS not measured, no cert handling scaffolded (R8.3) |
| Internet-vantage latency | **G-11** | named; no measurement scaffolded (R8.4) |
| Store outage behind a health-checked NLB | **G-13** | named; no drill scaffolded (R8.5) |
| Edge sustains ≥ 2× the fleet's qualified RPS adding < 1 ms p99 | **L16b-2 / GW20** | named; no fleet-scale behaviour scaffolded (R8.6) |
| Full > 350 s healthy-stream soak | **L16b-4** | config supports it (`proxy_read_timeout 400s`); the full soak is deferred (R8.6) |
| Drain one gateway during active streams | **L16b-5** | documented via the Edge_Drain_Contract above; the fleet drill is deferred (R8.6) |
| Fleet C4 (C39 synthetic-client) measurement of the Firewall_Added_Delta | **GW20** | named next to `gateway_v2/gateway_v2/runtime/latency_slo.py` (`assert_within_slo`) (R8.7) |

### TLS knob (R8.3) — defined, cost deferred

TLS terminates in the gateway tier. The config knob is **defined**, the cost is deferred to **G-10**
(no cert handling scaffolded):

- **Option A — uvicorn TLS on the B3 host:** pass `ssl_keyfile` / `ssl_certfile` to the uvicorn
  server in `gateway_v2/gateway_v2/edge/serve.py` (which binds a plain TCP socket today, per
  `serve.py` R7.3).
- **Option B — edge TLS listen:** add a `listen 443 ssl;` server with `ssl_certificate` /
  `ssl_certificate_key` paths in `gateway-edge.conf`.

B5 records this listen decision as a knob only. The **cost at 600 RPS is deferred to G-10**; no cert
material, no cert rotation, and no TLS listener is scaffolded here.

## What B5 built locally vs what is deferred

**Built + verified locally (this card):**

- `gateway-edge.conf` — the v2 edge server + the `upstream b3_gateway { … keepalive 64; }` block,
  `proxy_http_version 1.1` + `Connection ""`, `X-Accel-Buffering: no` SSE transparency,
  `proxy_read_timeout 400s`, connection recycling aligned to `RV_CONN_MAX_AGE_S` (30 s), the
  `default_server` unknown-Host 403 catch-all (CWE-1021).
- `security-headers.inc` — the five browser security headers + the reused v1 hardening posture.
- `Dockerfile.edge` + `docker-compose.edge.yml` — the Local_Edge_Harness (nginx container → host B3).
- `check_edge_keepalive.sh` — the static Edge_Regression_Gate (L16b-7): fails if the keepalive block
  is removed. No Docker.
- `../../tests/edge/test_edge_live_l16b.py` — the docker-gated live L16b subset (L16b-1 / -3 / -6 /
  -4-passthrough); skips cleanly when Docker is absent.
- `../../gateway_v2/runtime/latency_slo.py` — the pure, client-side `Firewall_Added_Delta` SLO
  definition (not sourced from the gateway histogram).

**Deferred (named cloud/fleet seams, not scaffolded):** NLB bake-off (GW20), TLS cost (G-10),
internet-vantage latency (G-11), store outage behind a health-checked NLB (G-13), edge ≥ 2× fleet
qualified RPS (L16b-2 / GW20), the full > 350 s soak (L16b-4), the drain-one-gateway drill (L16b-5),
and the fleet C4 (C39) measurement of the Firewall_Added_Delta (GW20).
