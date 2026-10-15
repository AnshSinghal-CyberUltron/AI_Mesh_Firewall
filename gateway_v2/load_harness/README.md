# load_harness — B6 / R2-21 (load harness v2.3: open-loop Poisson load + measurement)

This package is the **local half** of **GW20b** (R2-21): "harness v2.3", the open-loop Poisson
load-and-measurement instrument for the `gateway_v2` serving path. (The release version — `v2.3`
above — is **not an independent literal**: it is the value of the single `HARNESS_VERSION` stamp in
`load_harness/version.py`, the one authority. This README derives from that stamp and is not a
second source of truth; a release-version change happens only in `version.py` — B7 / R2-20, Req 1.4.) It generates open-loop arrivals
under an injected clock + injected `random.Random`, mixes in the five GW20b strata, collects
client-side C4, climbs a ≤ 10%-step rate ladder with 3 repeats, and emits the GW20b per-run report —
then drives a local **dry-run** against one gateway booted by B1's `build_app` and hosted by B3's
`serve.py`, backed by B4's fake guard, over the **non-TLS** path.

It is a **sibling top-level package**, like `state_control/` and `audit_control/` — deliberately
**outside** the `gateway_v2` import-linter serving layers (`[tool.importlinter].root_packages =
["gateway_v2"]` is unchanged). The harness is an offered-load client and an offline generator, not a
serving-path layer. Its one allowed import edge is **downward** into `gateway_v2` only —
`gateway_v2.runtime.latency_slo` (C4 / `Firewall_Added_Delta` reconciliation) and
`gateway_v2.runtime.resources.ResourceContract` (the pass bound) — exactly as `state_control`
imports `gateway_v2.domain.state_knobs`; `gateway_v2` never imports `load_harness`.

GW20b / R2-21 is a **category-2** card: the local half is built and verified here; the fleet/cloud
halves stay explicit, named `Deferred_Cloud_Gate`s (see below). Nothing in those seams is scaffolded.

## Running `python -m load_harness`

The entrypoint (`__main__.py`) parses a `RunSpec` from the environment (warn-and-default — a missing
or invalid value falls back to a documented default and logs one line, never raising), constructs
the seeded `random.Random(seed)` + the injected clock, and builds the **offline, deterministic** half
of a run (the arrival schedule + the planned requests), printing a one-line-per-section summary.

```sh
# Offline generation only (no gateway, no extra required):
LH_OFFERED_RATE_PER_S=100 LH_ARRIVAL_MODE=poisson LH_SEED=0 \
  LH_STRATUM_LONG_PROMPT=1 LH_LONG_PROMPT_SHARE=0.05 \
  python -m load_harness
```

When the dry-run orchestration (`dryrun.py`) is present, the entrypoint hands off to it via a lazy
seam; the dry-run attaches to the booted local gateway (B1 + B3 + B4 fake guard) over the **non-TLS**
path. The in-process attach uses `httpx` (and the real-uvicorn loopback variant uses `uvicorn`),
both of which are the **`conformance`** extra — install it for the dry-run:

```sh
pip install 'gateway-v2[conformance]'
```

A tree without that extra still imports `load_harness.dryrun` cleanly; only an actual attach raises
`DryRunDependencyMissing` with the install hint.

### Environment knobs (`LH_*`)

| Env var | Purpose | Default |
|---|---|---|
| `LH_OFFERED_RATE_PER_S` | configured offered rate (req/s) | `100.0` |
| `LH_ARRIVAL_MODE` | `poisson` or `constant` | `poisson` |
| `LH_SEED` | `random.Random` seed (int) | `0` |
| `LH_STRATUM_MULTI_KEY` | enable the multi-key stratum (bool) | `false` |
| `LH_STRATUM_LONG_PROMPT` | enable the long-prompt stratum (bool) | `false` |
| `LH_LONG_PROMPT_SHARE` | long-prompt share, snapped to `0.01` / `0.05` / `0.25` | `0.0` |
| `LH_STRATUM_RETRY` | enable the SDK-retry stratum (bool) | `false` |
| `LH_RETRY_SHARE` | base retry share `[0,1]` | `0.0` |
| `LH_AGGRESSIVE_RETRY_SHARE` | aggressive-retry share `[0,1]` | `0.0` |
| `LH_STRATUM_SECRET_OUTPUT` | enable the secret-shaped-output stratum (bool) | `false` |
| `LH_SECRET_OUTPUT_SHARE` | secret-shaped share `[0,1]` | `0.0` |
| `LH_STRATUM_TENANT_SCALE` | enable the tenant-scale stratum (bool) | `false` |
| `LH_NUM_REQUESTS` | number of open-loop arrivals to generate | `1000` |
| `LH_NUM_KEYS` | multi-key population size (floor `2000`) | `2000` |
| `LH_NUM_ORGS` | multi-key org count (floor `200`) | `200` |
| `LH_NUM_TENANTS` | tenant-scale population size (floor `10000`) | `10000` |

`LH_OFFERED_RATE_PER_S`, `LH_ARRIVAL_MODE`, `LH_SEED`, and the `LH_STRATUM_*` / `*_SHARE` knobs are
read by `config.load_run_spec` onto the `RunSpec`; `LH_NUM_REQUESTS` and the population sizes
(`LH_NUM_KEYS` / `LH_NUM_ORGS` / `LH_NUM_TENANTS`) are operational generation sizes read by
`__main__.py`, not `RunSpec` fields. None is a capacity-position literal — the ladder, not any knob,
discovers the server's ceiling.

## Determinism contract

The whole generation path threads two injected dependencies and nothing wall-clock or global: an
injected `Clock = Callable[[], float]` and one seeded `random.Random` built from `RunSpec.seed`.
The `ArrivalSchedule`, every stratum assignment, and `PlannedRequest` composition are produced from
`(rng, clock)` alone, with **no** network or server dependency. **Same seed + same clock ⇒
byte-identical schedule and strata assignments** — reproducible and verifiable **offline** without a
running server. (`RunSpec` stores the seed as an `int` so the value type stays frozen + slots; the
live `Random` is constructed at the generation boundary, never module-global `random.*`.)

## GW20b per-run report fields

Each offered run produces a frozen `report.RunReport` carrying the GW20b diagnostic fields a knee is
interpreted and signed from. The report is a pure value **carrier**: it computes and fabricates
nothing.

- `c4_p99_ms` + `slo_bound_ms` + `c4_passes` — the C4 p99 and pass/fail against the SLO bound. The
  bound is read from `ResourceContract.target_p99_ms` (the harness holds no SLO/capacity literal);
  C4 p99 / tail exclusion / the firewall-added delta are delegated to `runtime.latency_slo`, never
  recomputed.
- `owner_queue_wait_s`, `event_loop_lag_s`, `reason_mix` — read from the gateway's exposed telemetry
  at the client boundary; the harness carries them unmodified and classifies nothing itself. The
  `ReasonMix` is the distribution of outcome reasons (`admitted` + each shed/error reason) over the
  offered requests, stored as a sorted `(reason, count)` tuple (not a mutable dict).
- `gpu_busy` — a windowed GPU-busy field modeled as an explicit sum type: `GpuBusy.available(value)`
  from a configured source, or the single `GpuBusy.UNAVAILABLE` sentinel. With no source configured,
  the type makes fabrication **unrepresentable** — a local run reports `UNAVAILABLE`, never an
  invented number.
- `schedule_drop_count` — any non-zero count flags the run as not a clean server measurement.
- `loadgen_headroom` + `generator_bound` — the margin between the rate the generator can issue and
  the configured offered rate (gate LGW20-5), and whether the run was excluded from knee
  determination because that margin fell below the threshold.
- `realized_strata_shares` + `distinct_keys` / `distinct_orgs` / `distinct_tenants` — the realized
  long-prompt / retry / secret-shaped shares and the identity cardinalities actually exercised.
- `arrival_mode_constant` — `True` under constant arrivals so the published limits are labeled
  **upper bounds** (constant arrivals understate the tail pressure the Poisson process reveals).

## Open-loop honesty

The schedule is computed **up front** from the arrival process; the driver dispatches each arrival at
its `scheduled_s` and **never** waits on a prior response, so the offered rate is never reduced by
server latency (open-loop, not closed-loop).

- **Schedule drops fail the run.** A dispatch whose actual start slips past `scheduled_s` by more
  than the drop tolerance is a `Schedule_Drop` — evidence the *generator*, not the server, fell
  behind. Any non-zero count fails that run's pass test; it is surfaced, never absorbed.
- **Generator-bound runs are excluded.** A run whose `Loadgen_Headroom` falls below the minimum
  margin is marked generator-bound and excluded from knee determination (never counted as a server
  pass or fail), so a measured knee reflects the server and not the loadgen.

## The secret-shaped stratum — honest limit

The secret-shaped-output stratum drives the gateway's **GW12b bounded-holdback MECHANISM** by
scripting the stub provider's **output bytes** to match the holdback shape classes (AWS-, email-,
UUID-shaped, etc. — obviously-synthetic placeholders, never real secrets). Those bytes flow through
the booted gateway's **real** egress + bounded-holdback path, so the client observes real holdback
timing (which the C4 collector **excludes** per Req 8.3) and the realized secret-shaped share.

The honest limit: this drives the holdback **mechanism** (keyed on output-byte shape); it does
**not** prove the gateway's GW07/GW08 **detector** classifies those bytes as secrets.
Detector-driven classification (GW07/GW08) and a real-upstream secret-generation socket are a
deferred seam (see below).

## Deferred cloud gates

Each is named with its owning card/gate. Nothing below is scaffolded in this card.

| Deferred_Cloud_Gate | Owner | Status |
|---|---|---|
| Fleet-scale knee **numbers** (the Poisson-forced capacity sizing) | **R2-01** | out of scope; local half de-risks the one-shot (Req 15.1) |
| Autoscaling step tests | **G-01** | named; not delivered here (Req 15.1) |
| Retry amplification at fleet scale | **G-06** | named; the local retry stratum models `x-should-retry: false` + amplification, fleet-scale measurement deferred (Req 15.1) |
| Integrated re-measure | **G-09** | named; not delivered here (Req 15.1) |
| Real GPU-busy data source | **GW20b / fleet** | field **defined + positioned** (`GpuBusy`); reported `UNAVAILABLE` locally, never fabricated (Req 15.2) |
| TLS client / edge-TLS gate | **G-10** | **not built**; the dry-run drives the gateway over the non-TLS plain-TCP path (Req 15.3) |
| GW07/GW08 secret **detector** (detector-driven classification) | **GW07 / GW08** | later seam; the secret-shaped stratum drives the holdback mechanism only (Req 6 honest limit) |
| Socket-level fake-generation upstream (design option (b)) | **later harness extension** | named seam; the dry-run boots with a scripted socket-free `StubProviderClient` (option (a)) |

## Source of truth

- The **GW20 / GW20b** card in `docs/AI_MESH_MASTER_RUNBOOK_v3_BACKEND_REWRITE.md` is the binding
  authority.
- The spec (requirements / design / tasks) lives at `.kiro/specs/load-harness-r2-21/`.
