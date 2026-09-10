# Design Document

## Overview

P0.0 is a **measurement harness** around the already-shipped chat pipeline on SHA `a67337fb`. It does not change gateway/control/frontend product code. It brings up an isolated Compose project `aimf_p0`, fail-closed preflights that the HTTP peer **and in-process org cache** match the intended cell, drives `POST /v1/chat/completions` through a wrapper that **owns the POST loop**, filters Counted_Samples, and writes `docs/perf/evidence/2026-09-10-p0-task0-honesty/`.

The only number compared to 20 ms is Cell_A non-stream Firewall_Tax (`pipeline_trace.total_latency_ms − model_output_ms`) from those Counted_Samples. Stub RPS is labelled `capacity_eligible=false`. Streaming is not run; the pack states that on this SHA stream `total − model_output` **aliases** `ttft_ms` (overwrite is `now − provider_start_ts`; rounding/`setdefault`; `stage_sum` may exceed total).

Locked by `DESIGN_REVIEW.md` (five lenses + Devil’s Advocate, 2026-09-10).

### Scope

**In:** overlay `compose.p0.yml`, non-secret `p0.env` only if `env_file: !override` cannot collide with host `.env`, preflight + wrapper scripts, evidence pack, labelled cells A / input-block / redact / negative / size-4096, optional T2-on and live-model.

**Out:** product code; Dockerfile git LABEL; G1.1; G0.2; PG2; attaching `aimeshperf`; Playwright UI Duration; nine-port frontend remap; `pipeline_9stage_verify.py`; copying `aimesh-dev` `compose.perf.yml`; `gateway_pipeline_bench.run()`; stock unfiltered `addons`.

### Key decisions

- Isolation **pattern** from `compose.perf.yml` is re-implemented here (unpublish ports, literal pins, worker pin). Do **not** copy that file, SYS_PTRACE, GIL/GC/trace-mode keys, `perf-token-stub`, `${PERF_*}` interpolation, `WEB_CONCURRENCY=4`, or tok/s 100.
- Completeness is **per-sample nine `action != skip`**, not `honesty.full_nine_stages` (that boolean is two timers: `input_scan` p50>0 and `output_guardrail` p50>0). Caption it as such in the pack.
- Content blocks are HTTP **400** by default (`GATEWAY_BLOCK_STATUS`). Counted_Sample is HTTP **200**, not 2xx.
- `MAX_PROMPT_LENGTH` is 10000; size cell is **4096** unique chars.
- Grounding is pinned **off** and labelled.
- Chat uses `CONFIG_SYNC.get_config(org)` / `POLICY_SYNC.get_policies(org)` (HMAC last-good RAM). Redis GET is not that cache.
- Overlay **`name: aimf_p0`**. Base compose is `name: ai_mesh_firewall`.

## Architecture

```
Worktree aimesh-p0-task0 (SHA a67337fb)
    docker compose -f docker-compose.yml -f scripts/perf/e2e/compose.p0.yml
        project aimf_p0  (overlay name: aimf_p0; unpublished 5432/6379/8300/8100/6432)
        gateway+control built with label git.sha=HEAD
    seed via aimf_p0 control (compose exec / remap — never host :8100)
    restart aimf_p0 gateway (or poll RAM) after seed
    scripts/perf/e2e/p0_preflight.py  → fail-closed (Redis + RAM + TCP peer)
    scripts/perf/e2e/p0_drive.py      → owns POST loop; imports honesty fns only
        one GATEWAY_URL; TCP-owner inspect com.docker.compose.project=aimf_p0
    docs/perf/evidence/2026-09-10-p0-task0-honesty/
        preflight.json  cells/*.json  scorecard.md  REPORT.md
```

**Single `GATEWAY_URL`** for preflight GET, probe POST, and every measured POST. Resolve to a **listening** socket; `docker inspect` the TCP peer; require `com.docker.compose.project=aimf_p0`. Reject host `127.0.0.1:8300` on this machine (it is `aimeshperf`). Prefer a client container on the compose network, **or** a documented high remap whose peer label matches. No permanent extra client compose service; `docker compose run --rm` or that remap is enough.

Workers, mcp-broker, frontend, chroma, mongo are not started. Postgres/Redis exist only as `aimf_p0_*` volumes created after harness `down -v`.

## Overlay (`scripts/perf/e2e/compose.p0.yml`)

Overlay on `docker-compose.yml`. Must not duplicate the whole stack. First line after comments: `name: aimf_p0`.

| Service | Overlay |
|---|---|
| top-level | `name: aimf_p0` |
| postgres, redis | `ports: !override []` |
| control, gateway | `ports: !override []` (or a high remap recorded in evidence); `env_file: !override ["scripts/perf/e2e/p0.env"]` **plus** literal `environment:` pins that beat base interpolation |
| pgbouncer | `ports: !override []` |
| gateway+control `build.labels` | `git.sha: "${P0_GIT_SHA}"` where the wrapper exports `P0_GIT_SHA=$(git rev-parse HEAD)` **before** compose so the label is a literal at build time. Digest is recorded; digest≠SHA string compare is **not** the gate. |
| gateway deploy.resources.limits | `cpus: "1"`, memory recorded (e.g. `4g`) |
| gateway environment literals | `POLICY_SIGNING_KEY` (same string as control; no `${POLICY_SIGNING_KEY:-}`), `GATEWAY_LOADTEST_STUB_LLM=1`, `GATEWAY_LOADTEST_STUB_DURATION_S=2`, `GATEWAY_LOADTEST_STUB_TOK_PER_S=50`, `ENABLE_TIER2=false`, `GATEWAY_OUTPUT_GUARD_ENABLED=true`, `GATEWAY_INPUT_SCAN_ENABLED=true`, `GATEWAY_OUTPUT_GROUNDING_ENABLED=false`, `WEB_CONCURRENCY=1`, `GATEWAY_TIER2_CACHE_TTL_SECONDS=0` |
| unused profiles | not started |

`p0.env` holds only non-secrets that are **also** repeated as literals in `environment:` for any key base compose already sets in `environment:`.

Bring-up (harness-owned):

```
cd /home/contact_cyberultron_com/aimesh-p0-task0
export COMPOSE_PROJECT_NAME=aimf_p0 P0_GIT_SHA=$(git rev-parse HEAD)
docker compose -f docker-compose.yml -f scripts/perf/e2e/compose.p0.yml down -v
docker compose -f docker-compose.yml -f scripts/perf/e2e/compose.p0.yml build --no-cache
docker compose -f docker-compose.yml -f scripts/perf/e2e/compose.p0.yml up -d postgres redis pgbouncer control gateway
```

There is no `compose build --build` flag. Refuse if the command list includes `aimeshperf` or the live `ai_mesh_firewall` project.

## Seed and Measurement_Org

After control is healthy: migrate if needed, create/ensure one org + API key used only by this harness, seed a **non-empty** policy bundle signed with the overlay key, push compiled policies to Redis **via aimf_p0 control** (compose exec or control remap — never host `:8100`). Pin org `max_response_tokens` ≥ 32 (default 4096 is fine; a seed of 16 would clamp Token_Floor).

**Then restart the aimf_p0 gateway** (preferred) or poll until RAM matches. Bring-up starts gateway before seed; `_load_initial_bundle` can set `_sync_completed=True` with empty `_org_caches`. Seed then writes Redis and publishes `policy_updates`. If pub/sub is missed, Redis looks seeded and RAM stays `[]` → `evaluate([])` is allow.

Intended Cell_A org config: firewall on, input/output scans on, `tier2_enabled` false/absent, enforcement block, kill-switch not armed.

Negative cell: new gateway process **or** HMAC-valid scans-off/empty-policy payload + confirmed RAM. DEL Redis keeps last-good. Restore and re-preflight before any later Cell_A. Prefer **Cell_A first**.

## Preflight (`scripts/perf/e2e/p0_preflight.py`)

One `GATEWAY_URL` for every HTTP call. Order, fail-closed:

1. `git rev-parse HEAD` in the worktree; refuse dirty product trees if gateway/control files differ from `a67337fb` except harness paths.
2. `docker inspect` gateway: labels `com.docker.compose.project=aimf_p0` and `git.sha` == HEAD; record digest. Volume names `aimf_p0_*`; CreatedAt after this run’s `down -v`.
3. Resolve `GATEWAY_URL`. Inspect the TCP peer of that URL. Reject project `aimeshperf` / `ai_mesh_firewall`. Reject host `:8300` unless that remap’s peer is `aimf_p0`.
4. **Before seed:** Redis empty of `firewall:config*`, `policies:compiled*`, `llm:model_configs*`, `kill_switch:*`. Fail if any existed (legacy `policies:compiled` without a suffix included).
5. Seed via aimf_p0 control. Restart gateway (or poll RAM).
6. GET `/health` 200 on **that** URL (liveness only — not the policy oracle).
7. GET Redis `firewall:config:{org}`: scans/firewall/enforcement/T2 match Cell_A. Snapshot `_sig` / `compiled_at` / policy ids. Also GET `kill_switch:{org}:global` (absent/not armed).
8. GET `/v1/observability` with the measurement API key:
   - `organization` == Measurement_Org (must equal the Redis snapshot slug).
   - `policy.policy_count` > 0 and `policy.version` matches seed.
   - Fail if Redis is full and `policy.policy_count` is 0 (missed pub/sub / HMAC refuse / last-good empty).
   - Do **not** treat observability `firewall_enabled` / `enforcement_mode` as `get_own_config`: those fields use `get_config` (fallback to `default`/global). The endpoint has **no** `input_scan_enabled`.
9. Config RAM truth for Cell_A: probe POST (same request factory as Cell_A) must show `input_scan` and `output_guardrail` with `action != skip` (scans actually ran for Measurement_Org, not inherited global-off).
10. `docker exec` gateway: `POLICY_SIGNING_KEY` fingerprint matches control; stub duration > 0; `ENABLE_TIER2=false`; `WEB_CONCURRENCY=1`; grounding false.
11. Probe POST: unique short prompt, pinned `max_tokens=32`, `stream=false`, `enable_routing=false`: HTTP 200, `final_action=allow`, completion id `chatcmpl-loadtest-stub`, `usage.completion_tokens >= 32`, nine stages `action != skip`, no `scan_only` / `ttft_ms`.
12. Write `preflight.json`. On any fail: `preflight=fail`, no Cell_A percentiles.

Post-run: re-GET Redis **and** `/v1/observability`; mismatch invalidates the pack.

`docker stats` / cgroup CPU during Cell_A MUST target the **same** container as the POST peer.

## Driver (`scripts/perf/e2e/p0_drive.py`)

Owns the POST loop. Imports honesty helpers only (`build_honesty_report`, `compute_full_nine_stages`, `compute_capacity_fail_reasons`, optionally `addon_from_trace` / `STAGE_NAMES` / `extract_completion_id`).

**Do not** call `gateway_pipeline_bench.run()` or `_child`. **Do not** use unfiltered `addons`. Stock module binds `GATEWAY=http://127.0.0.1:8300`, `MAX_TOKENS=16`, `UNIQUE_PROMPT=0`, `ENABLE_ROUTING=1`, `WORKERS=8`, `CONN=8` at **import time**. If those constants are imported at all, set the env vars **before** import; the wrapper still must not send through those defaults.

One request factory for probe and Cell_A:

- `stream: false`
- `max_tokens: 32` (one integer; omit forbidden)
- `enable_routing: false`
- user content = short phrase + UUID only (byte-capped; never leftover size-cell `PROMPT`)
- Bearer = Measurement_Org key

Cell_A inflight: `WORKERS=1`, `CONN=1` (serial). Overlay `WEB_CONCURRENCY=1` does not make 64-way queueing homogeneous.

Per response, classify **the record** then percentile:

**Counted_Sample (all must hold):**

- HTTP status **200** (not 2xx)
- `stream=false`; no `ttft_ms`; not SSE
- `pipeline_trace.final_action == "allow"` (drop redact / flag / rewrite)
- completion `id == "chatcmpl-loadtest-stub"`
- no `zeroshield.scan_only`
- `usage.completion_tokens >= 32`
- all nine `PIPELINE_STAGE_NAMES` present, each `action != skip`

**Drop (count by class):** 400 (incl. `content_filter`), 401, 403, 429, 503, status 0, redact-200, flag/rewrite, scan-only 200, skip-model, short completion tokens, stream fields.

Tax = `pipeline_trace.total_latency_ms - model_output_ms` only on Counted_Samples. Do not synthesize 0 ms for absent stages. Wall = `pipeline_trace.total_latency_ms` labelled **N/A-not-tax** (not httpx `wall_ms`).

Warmup discarded. N≥16 Counted_Samples for Cell_A.

Capture two prompt fingerprints (hash of **wire** bodies) and assert they differ.

Honesty `full_nine_stages` is recorded and captioned as two timers; it is **not** the 20 ms completeness gate.

## Cells

Separate processes / separate output files, never concatenated. Cell_A first.

| ID | How | vs 20 ms |
|---|---|---|
| A | Counted_Sample filter, T2 off, short unique prompt, driver 1×1 | **yes** (only this row) |
| block | Prompt that trips input terminal block (expect 400/403, skip model) | N/A-not-20ms |
| redact | Prompt with maskable PII under a redact rule (200, `final_action=redact`) | N/A-not-20ms |
| negative | HMAC-valid scans-off **or** empty org policies, confirmed in RAM; restore + re-preflight before any later A | N/A-not-20ms |
| size | 4096 unique chars, Cell_A posture, same Counted_Sample filter (dos-400 dropped, not a fake size tax) | N/A-not-20ms |
| t2 / live | optional MAY; restore Cell_A pins after | N/A-not-20ms |

N/A (documented, not run as SLO cells): kill-switch, OG-block, stream abort, 401/429 smokes, unpinned-worker **cell**, streaming tax.

T2-on overlay: `ENABLE_TIER2=true`, `GATEWAY_TIER2_CACHE_TTL_SECONDS=0`, `GATEWAY_TIER2_SAMPLE_RATE=1` as literals; `docker exec` those values. Overlay `ENABLE_TIER2=false` means `_bedrock_scanner is None` on Cell_A even if Redis leftover `tier2_enabled: true`.

## Evidence pack

```
docs/perf/evidence/2026-09-10-p0-task0-honesty/
  preflight.json
  cells/A.json          # Counted_Sample percentiles, honesty, CPU, exclusions
  cells/block.json
  cells/redact.json
  cells/negative.json
  cells/size4096.json
  scorecard.md
  REPORT.md
```

`REPORT.md` must state: pack overwrite; stream total is `now − provider_start_ts`; subtraction **aliases** TTFT; `stage_sum` may exceed total; neither is Firewall_Tax; `honesty.full_nine_stages` is two timers; this SHA is not aimesh-dev 13.6 RPS; grounding-off is a pin.

`scorecard.md` rows: (a) 20 ms tax on Cell_A Counted_Samples; (b) Wall N/A-not-tax; (c) MASTER ≤12 ms **latency** (pin SLO F p50 vs `T_addon_pre`), never CPU%; (d) stub ≠ capacity (`capacity_eligible=false`); (e) T2 N/A or measured; (f) PG2 N/A; (g) 100k N/A.

## Error and identity handling

| Failure | Effect |
|---|---|
| Signing mismatch / HMAC refuse | RAM keeps last-good or stays empty. Preflight fails if observability `policy.policy_count` is 0 while Redis is full, or identity ≠ seed. |
| Host `.env` `ENABLE_TIER2=true` | Ignored: overlay literals + `env_file: !override`. |
| Reused Redis AOF / wrong compose project | `down -v` + overlay `name: aimf_p0` + empty glob + volume CreatedAt. Postgres leftover would republish old `FirewallConfig` — volumes must be this run’s. |
| Wrong `:8300` | Preflight inspects TCP peer labels; host `:8300` rejected. |
| `get_config` default fallback | Probe stages prove Measurement_Org scans ran; observability `organization` matches slug. |
| Negative cell DEL Redis | Forbidden as the only step. |

## Testing the harness (not the product)

- Unit: classify-sample helper (400 vs 200-allow vs redact-200 vs skip-model vs scan-only vs flag) with fixture traces; unique-body check; tax formula on a non-stream fixture; refuse stream fixture as Counted_Sample; Token_Floor on `usage.completion_tokens` not request `max_tokens`.
- No product pytest gate required beyond noting shipped honesty tests exist.

## Out of scope (design)

Parallel policy+scan, PG2, G1.1 code, frontend, MCP, attaching `aimeshperf`, quoting stub RPS as capacity, quoting aimesh-dev 13.6 RPS as this SHA.
