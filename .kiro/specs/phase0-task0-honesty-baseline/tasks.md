use 2cla

# P0.0 Honesty Baseline — Implementation Tasks

Worktree: `/home/contact_cyberultron_com/aimesh-p0-task0` @ `a67337fb`
Spec: `requirements.md` + `design.md` (DA-locked 2026-09-10)
**No product edits** under `gateway/`, `control/`, `frontend/`, `shared/`, `services/`.
**Do not** attach, restart, or rebuild compose project `aimeshperf` or `ai_mesh_firewall`.

Harness files live in `scripts/perf/e2e/`. Evidence: `docs/perf/evidence/2026-09-10-p0-task0-honesty/`.

Constants:

| Name                                | Value                                                                |
| ----------------------------------- | -------------------------------------------------------------------- |
| Compose project                     | `aimf_p0` (overlay `name:` — base file is `ai_mesh_firewall`) |
| Org slug                            | `aimfp0`                                                           |
| Signing key (literal, not a secret) | `p0-harness-signing-key-not-a-secret`                              |
| Gateway remap                       | `18300:8300` (never host `:8300`)                                |
| `GATEWAY_URL`                     | `http://127.0.0.1:18300`                                           |
| Stub                                | duration`2`, tok/s `50`, `max_tokens` `32`                   |
| Driver inflight                     | WORKERS=1 CONN=1                                                     |
| Token_Floor                         | response`usage.completion_tokens >= 32`                            |

---

## Task 1: Counted_Sample classifier (TDD)

**Files:**

- Create: `scripts/perf/e2e/p0_classify.py`
- Test: `scripts/perf/e2e/test_p0_classify.py`

**Step 1:** Write failing tests for `classify_sample(status, body, stream=False)`:

- HTTP 200 + `final_action=allow` + stub id + 32 completion tokens + nine `action != skip` → `counted`
- HTTP 400 with `pipeline_trace` (content_filter, model skip, tax would be wall−0) → `http_400` **not** counted
- HTTP 200 `final_action=redact` → `redact`
- HTTP 200 `zeroshield.scan_only` / non-stub id → `scan_only`
- HTTP 200 `final_action=flag` or `rewrite` → `flag_or_rewrite`
- `ttft_ms` present or `stream=True` → `stream`
- `usage.completion_tokens=16` with request max_tokens 32 → `short_tokens`
- Missing stage or `action=skip` → `skip_model`
- HTTP 401/403/429/503/0 → matching class

Also test `firewall_tax_ms(trace)` = `total_latency_ms - model_output_ms` on non-stream allow fixture; `None` on stream fixture. Test `build_chat_body(uuid)` includes UUID, `max_tokens=32`, `stream=False`, `enable_routing=False`, short prompt.

**Step 2:** Run

```
cd /home/contact_cyberultron_com/aimesh-p0-task0
python3 -m pytest scripts/perf/e2e/test_p0_classify.py -q
```

Expected: FAIL (module missing).

**Step 3:** Implement `p0_classify.py` only. Nine names copied from `PIPELINE_STAGE_NAMES`. Never call `gateway_pipeline_bench.run()`.

**Step 4:** Re-run pytest. Expected: PASS.

---

## Task 2: Overlay + non-secret env

**Files:**

- Create: `scripts/perf/e2e/compose.p0.yml`
- Create: `scripts/perf/e2e/p0.env`

Overlay **must**:

1. `name: aimf_p0`
2. `ports: !override []` on postgres, redis, pgbouncer, control
3. gateway `ports: !override ["18300:8300"]`
4. `env_file: !override ["scripts/perf/e2e/p0.env"]` on control + gateway
5. Literal `environment:` pins (no `${ENABLE_TIER2:-}`, no `${PERF_*}`):
   - both: `POLICY_SIGNING_KEY: "p0-harness-signing-key-not-a-secret"`
   - gateway: `GATEWAY_LOADTEST_STUB_LLM=1`, `GATEWAY_LOADTEST_STUB_DURATION_S=2`, `GATEWAY_LOADTEST_STUB_TOK_PER_S=50`, `ENABLE_TIER2=false`, `GATEWAY_OUTPUT_GUARD_ENABLED=true`, `GATEWAY_INPUT_SCAN_ENABLED=true`, `GATEWAY_OUTPUT_GROUNDING_ENABLED=false`, `WEB_CONCURRENCY=1`, `GATEWAY_TIER2_CACHE_TTL_SECONDS=0`
6. gateway `cpus: "1.0"`, `mem_limit: 4g`
7. `build.labels.git.sha: "${P0_GIT_SHA}"` on gateway + control (export SHA before compose)
8. Override external MCP networks so `up` does not require live `mcp_sandbox_bridge`:

```yaml
networks:
  mcp_sandbox_bridge:
    name: aimf_p0_mcp_sandbox_bridge
    external: false
  mcp_sandbox_net_zeroshield:
    name: aimf_p0_mcp_sandbox_net_zeroshield
    external: false
```

Do **not** copy `compose.perf.yml` (no SYS_PTRACE, GIL/GC, token-stub, WC=4).

**Verify:**

```
export P0_GIT_SHA=$(git -C /home/contact_cyberultron_com/aimesh-p0-task0 rev-parse HEAD)
docker compose -f docker-compose.yml -f scripts/perf/e2e/compose.p0.yml config
```

Assert project name `aimf_p0`, gateway ports only `18300`, `ENABLE_TIER2: "false"` literal, no `8300:8300`.

---

## Task 3: Preflight (fail-closed)

**Files:**

- Create: `scripts/perf/e2e/p0_preflight.py`

Single `GATEWAY_URL` (default `http://127.0.0.1:18300`). Refuse URL host:port `127.0.0.1:8300`. TCP-owner inspect: `com.docker.compose.project=aimf_p0`.

Order: git SHA → inspect gateway labels (`aimf_p0`, `git.sha==HEAD`) → volumes `aimf_p0_*` CreatedAt → empty Redis glob **before seed** (`firewall:config*`, `policies:compiled*`, `llm:model_configs*`, `kill_switch:*`) → caller seeds → restart gateway → GET `/health` on GATEWAY_URL → Redis org snapshot + `kill_switch:{org}:global` → GET `/v1/observability` (`organization==aimfp0`, `policy.policy_count>0`) → docker exec pin dump → probe POST via `build_chat_body` + `classify_sample==counted`.

Write `docs/perf/evidence/2026-09-10-p0-task0-honesty/preflight.json`. On fail: `preflight=fail`, no Cell_A percentiles.

**Verify (unit):** mock-friendly helpers for empty-glob, peer-label, observability RAM check. pytest in `scripts/perf/e2e/test_p0_preflight.py` for refuse-8300 and Redis-full-RAM-empty.

---

## Task 4: Driver (owns POST loop)

**Files:**

- Create: `scripts/perf/e2e/p0_drive.py`

Import honesty **functions** from `gateway_pipeline_bench` only after setting `GATEWAY`, `MAX_TOKENS=32`, `UNIQUE_PROMPT=1`, `ENABLE_ROUTING=0`, `WORKERS=1`, `CONN=1`. **Never** call `run()`.

Serial POSTs, N≥16 Counted_Samples after warmup=2. Unique UUID per body. Two fingerprints must differ. Tax only on counted records. `docker stats` on the **same** container as the TCP peer. Write `cells/{id}.json` (never concatenate).

Cell_A first. Then block / redact / negative / size-4096 as separate files. Negative: HMAC-valid scans-off or empty policies **confirmed in RAM**; restore + re-preflight. Size: 4096 unique chars, same Counted_Sample filter.

---

## Task 5: Orchestrator + seed

**Files:**

- Create: `scripts/perf/e2e/p0_run.sh`

```
export COMPOSE_PROJECT_NAME=aimf_p0 P0_GIT_SHA=$(git rev-parse HEAD)
# refuse if command would touch aimeshperf / ai_mesh_firewall
docker compose -f docker-compose.yml -f scripts/perf/e2e/compose.p0.yml down -v
docker compose ... build --no-cache
docker compose ... up -d postgres redis pgbouncer control gateway
# wait healthy
# empty-redis check
docker compose ... exec -T control python manage.py ensure_zeroshield_admin --org-slug aimfp0
docker compose ... exec -T control python manage.py seed_pii_policy_package --org-slug aimfp0
# mint GatewayAPIKey for aimfp0, print raw once to a 0600 file under evidence (gitignored)
docker compose ... restart gateway
python3 scripts/perf/e2e/p0_preflight.py
python3 scripts/perf/e2e/p0_drive.py --cell A
# other cells
# post-run Redis+observability re-GET
# scorecard.md + REPORT.md
```

Seed via **aimf_p0 control exec**, never host `:8100`.

---

## Task 6: Evidence pack

**Files:**

- Create: `docs/perf/evidence/2026-09-10-p0-task0-honesty/` (gitkeep)
- `scorecard.md` rows: 20ms tax Cell_A; Wall N/A-not-tax; MASTER ≤12 ms **latency**; stub ≠ capacity; T2 N/A; PG2 N/A; 100k N/A
- `REPORT.md`: stream `now−provider_start_ts`; subtraction aliases TTFT; `full_nine_stages` is two timers; grounding-off pin; this SHA ≠ aimesh-dev 13.6 RPS

---

## Task 7: Live `aimf_p0` run

Bring up **only** `aimf_p0`. Confirm `ss -ltnp | grep 8300` still belongs to `aimeshperf` (untouched). Confirm `18300` owner project=`aimf_p0`. Publish Cell_A pack even if tax p99 > 20 ms (`20ms_tax: FAIL` is a complete P0.0).

---

## Out of this plan

G1.1, G0.2, PG2, product gateway edits, streaming cell, 401/429 smokes, unpinned-worker cell, copying `aimesh-dev`.
