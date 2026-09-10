# Requirements Document

## Introduction

P0.0 is an **honesty baseline** of the live chat pipeline on git SHA `a67337fb`, measured through real `POST /v1/chat/completions` on an isolated Docker stack built from this worktree. It publishes per-stage latency, firewall tax, and CPU for a small closed matrix, and scores a **closed list** of latency/RPS claims. It does **not** change production code, does **not** ship Prompt Guard, does **not** fix streaming instrumentation (G1.1), does **not** score the detection corpus (G0.2), and does **not** certify a 20 ms SLO.

The user goal remains: firewall tax of the complete nine-stage path under 20 ms, and a labelled maximum CPU/RPS figure. This spec only **measures** that sentence. A tax above 20 ms is a scorecard FAIL, not a licence to edit the gateway.

This spec is the post-review lock of `brainstorm.md` (`PLAN_REVIEW.md`, `REQUIREMENTS_REVIEW.md`). Canonical runtime stage names on this SHA are:

`auth → kill_switch → rate_limit → policy → input_scan → model_routing → model_input → model_output → output_guardrail`

Plan documents dated 2026-08-27 and later are **not in this worktree**. Claims that exist only in those files are N/A-off-SHA.

## Glossary

- **Worktree:** `/home/contact_cyberultron_com/aimesh-p0-task0` on branch `feat/p0-task0-honesty-baseline` at SHA `a67337fb`.
- **Gateway:** The FastAPI process that serves `POST /v1/chat/completions`.
- **Nine_Stages:** The nine names in `PIPELINE_STAGE_NAMES`.
- **Firewall_Tax / Addon:** For a **non-streaming** completion only: `pipeline_trace.total_latency_ms − model_output_ms`. Equivalence to `t_addon_pre_ms + t_addon_post_ms` holds only on that non-stream path. On this SHA, applying the same subtraction to a **stream** trace equals `ttft_ms` (provider TTFT), which is **not** Firewall_Tax.
- **Wall:** Non-stream `pipeline_trace.total_latency_ms` (includes `model_output`). Not interchangeable with Firewall_Tax. Not an SLO vs 20 ms.
- **Cell_A:** Primary measurement: unique-prompt, `stream=false`, in-gateway loadtest stub with duration > 0 **and** token count ≥ Token_Floor, Tier-2 off, scans on, output grounding off, seeded **org-scoped** policy bundle, allow-path HTTP 200 with `final_action=allow`, all Nine_Stages `action != skip`, `WEB_CONCURRENCY=1`, compose `cpus` pinned.
- **Counted_Sample:** A Cell_A request that passed the allow-path filter in Requirement 6.
- **full_nine_stages:** Boolean from `compute_full_nine_stages` (chat + `input_scan` p50 > 0 + `output_guardrail` p50 > 0). **Not sufficient** to compare tax to 20 ms.
- **capacity_eligible:** Boolean from `build_honesty_report`. False on stub, non-unique prompts, or `full_nine_stages` false.
- **Loadtest_Stub:** `GATEWAY_LOADTEST_STUB_LLM` truthy and/or completion `id` `chatcmpl-loadtest-stub`.
- **Token_Floor:** Minimum completion tokens for stub cells (32). Prevents a 1-token `"ok"` output-guard.
- **Measurement_Org:** The org slug of the API key used by the harness.
- **Preflight:** Fail-closed checks before any latency sample is kept.
- **Evidence_Pack:** JSON + markdown under `docs/perf/evidence/2026-09-10-p0-task0-honesty/` (redact secrets).
- **aimeshperf / Dirty_Ansh:** Forbidden measurement targets.

## Requirements

### Requirement 1: Frozen SHA and no production-code change

**User Story:** As a reviewer, I want P0.0 to measure the committed tree, so that numbers are not mixed with dirty `ansh` or the optimised `dev/perf-9stage` gateway.

#### Acceptance Criteria

1. THE P0.0 work SHALL execute only inside Worktree `/home/contact_cyberultron_com/aimesh-p0-task0` at git SHA `a67337fb` (or a descendant that contains only spec/harness/evidence files from this spec).
2. THE P0.0 work SHALL NOT modify files under `gateway/`, `control/`, `frontend/`, `shared/`, or `services/` except by adding new harness/overlay files that those trees do not import at runtime.
3. THE P0.0 work SHALL NOT attach Docker compose project `aimeshperf` or `ai_mesh_firewall` as the measurement stack.
4. THE P0.0 work SHALL NOT edit `/home/contact_cyberultron_com/AI_Mesh_Firewall` (Dirty_Ansh) or `/home/contact_cyberultron_com/aimesh-dev`.
5. IF a command would rebuild or restart `aimeshperf-gateway-1` or the live `ai_mesh_firewall` gateway, THEN THE P0.0 work SHALL refuse that command and record the refusal.

### Requirement 2: Isolated compose overlay

**User Story:** As an operator, I want a dedicated Docker project whose published ports cannot be confused with the live gateway.

#### Acceptance Criteria

1. THE overlay SHALL set `name: aimf_p0` (base `docker-compose.yml` is `name: ai_mesh_firewall`) and SHALL compose `docker-compose.yml` plus `scripts/perf/e2e/compose.p0.yml` from this worktree. THE harness MAY also export `COMPOSE_PROJECT_NAME=aimf_p0`; overlay `name:` SHALL NOT be omitted.
2. THE overlay SHALL unpublish host ports `5432`, `6379`, `8300`, `8100`, and `6432` (compose `ports: !override []` or a non-colliding remap). IF a remap is used, THEN the harness SHALL use **only** that remapped URL.
3. THE harness SHALL bind `GATEWAY_URL` to a container whose compose label `com.docker.compose.project` equals `aimf_p0` (compose-network DNS or inspect of the TCP peer). Curl to host `:8300` is forbidden unless that port is the `aimf_p0` remap and the peer label matches.
4. THE overlay SHALL use `env_file: !override` and SHALL NOT load the host worktree `.env` for gateway/control pins.
5. THE harness SHALL fail Preflight unless volumes were created fresh for this run (`down -v` or a unique volume prefix enforced by the harness, not operator memory). Volume names SHALL match `aimf_p0_*` and SHALL have CreatedAt after the harness `down -v`. Redis SHALL be empty of `firewall:config*`, `policies:compiled*`, `llm:model_configs*`, and `kill_switch:*` **before** seed.
6. THE overlay SHALL NOT start `mcp-broker`, chroma, telemetry mongo, or transport-stub profiles.
7. THE harness SHALL build gateway and control from this worktree with `docker compose build --no-cache` or `docker compose up -d --build` (there is no `compose build --build` flag) and compose `build.labels.git.sha` equal to `git rev-parse HEAD` (no Dockerfile edit). THE harness SHALL `docker inspect` that label and SHALL fail if it disagrees with HEAD. Image digest SHALL be recorded; digest-string-equals-SHA SHALL NOT be the identity gate.
8. THE overlay MAY leave frontend, demo, rabbitmq, and mcp-stub unstarted. Chat tax SHALL be measured by HTTP to the `aimf_p0` gateway, not via Vite.

### Requirement 3: Literal overlay environment pins

**User Story:** As a measurement engineer, I want pins that cannot be overwritten by host `.env` interpolation.

#### Acceptance Criteria

1. THE overlay `environment:` values for the pins in this requirement SHALL be **literals** (no `${VAR:-…}` that can pick up host `ENABLE_TIER2`, `POLICY_SIGNING_KEY`, or stub duration).
2. Control **and** gateway SHALL set the same non-empty literal `POLICY_SIGNING_KEY`. Preflight SHALL fingerprint in-container `printenv POLICY_SIGNING_KEY` on both (length/hash in the pack, not the secret).
3. For Cell_A the gateway SHALL set `GATEWAY_LOADTEST_STUB_LLM=1`, `GATEWAY_LOADTEST_STUB_DURATION_S` to a literal greater than 0, and `GATEWAY_LOADTEST_STUB_TOK_PER_S` to a literal in the stub’s legal range. THE client SHALL send one pinned `max_tokens` integer ≥ Token_Floor (omit forbidden). Token_Floor is `usage.completion_tokens` on the response. Duration-0 stub reports `completion_tokens: 1` regardless of `max_tokens`.
4. For Cell_A the gateway SHALL set `ENABLE_TIER2=false`, `GATEWAY_OUTPUT_GUARD_ENABLED=true`, `GATEWAY_INPUT_SCAN_ENABLED=true`, `GATEWAY_OUTPUT_GROUNDING_ENABLED=false`. THE Evidence_Pack SHALL label this posture (grounding-off is a pin, not “SHA default”).
5. WHEN a Tier-2-on cell is run, THE overlay SHALL set `GATEWAY_TIER2_CACHE_TTL_SECONDS=0` and `GATEWAY_TIER2_SAMPLE_RATE=1` as literals; Preflight SHALL `docker exec` those values.
6. For Cell_A the gateway SHALL set `WEB_CONCURRENCY=1` and compose `deploy.resources.limits.cpus` to an explicit value (record both). An unpinned-worker **cell** is N/A.
7. THE overlay SHALL NOT interpolate `${ENABLE_TIER2:-false}` or `${GATEWAY_LOADTEST_STUB_DURATION_S:-2}`.

### Requirement 4: Preflight fail-closed

**User Story:** As a reviewer, I want the run to abort before warmup if the HTTP peer, org config, or stub workload is the cheaper pipeline.

#### Acceptance Criteria

1. WHEN Preflight runs, THE harness SHALL GET `/health` on the **aimf_p0** `GATEWAY_URL` and SHALL require HTTP 200.
2. THE harness SHALL require Measurement_Org’s compiled policies to be non-empty in the **gateway in-process org cache** (`GET /v1/observability` `policy.policy_count` / `version` for that API key’s org, which reads `_org_caches[org]`). Redis `policies:compiled:{org}` SHALL be snapshotted but SHALL NOT be sufficient (HMAC last-good / missed pub/sub). `/health` `policy_count` is a cross-org sum and SHALL NOT be the oracle.
3. THE harness SHALL GET Redis `firewall:config:{Measurement_Org}` (not the retired global `firewall:config`) and SHALL require `firewall_enabled`, `input_scan_enabled`, `output_scan_enabled`, `enforcement_mode`, and `tier2_enabled` to match the cell. `/health` global `CONFIG` SHALL NOT be the sole oracle for those fields.
4. THE harness SHALL `docker inspect` compose project label `aimf_p0` and `git.sha` as in Requirement 2.
5. THE harness SHALL confirm stub duration > 0 **and** a probe completion with token count ≥ Token_Floor **and** `output_guardrail` latency > 0. Duration-only OR guard-p50-only SHALL NOT pass Preflight.
6. THE harness SHALL document kill-switch **not armed** for Measurement_Org by GET `kill_switch:{Measurement_Org}:global` (absent or not armed). AOF leftovers are per-request Redis, not ConfigSync.
7. AFTER seed, THE harness SHALL restart the `aimf_p0` gateway **or** poll until org-scoped RAM matches the seed (`GET /v1/observability` for the measurement API key). Cold-load is the reliable HMAC path; boot-before-seed plus missed `policy_updates` SHALL fail Preflight.
8. AFTER the last Counted_Sample, THE harness SHALL re-GET Redis `firewall:config:{Measurement_Org}` and the compiled-bundle identity **and** re-GET `/v1/observability` policy count/version for Measurement_Org; IF either Redis or RAM differs from Preflight, THEN the run is invalid.
9. IF any criterion in this requirement fails, THEN THE harness SHALL publish no Cell_A p50/p99 and SHALL mark the Evidence_Pack `preflight=fail`.

### Requirement 5: Reuse the existing honesty bench

**User Story:** As an engineer, I want P0.0 to wrap the shipped honesty functions, not invent a fourth client.

#### Acceptance Criteria

1. THE P0.0 driver SHALL reuse `scripts/perf/gateway_pipeline_bench.py` (`build_honesty_report`, `compute_full_nine_stages`, `compute_capacity_fail_reasons`) for honesty fields.
2. THE wrapper SHALL set `UNIQUE_PROMPT=1` and SHALL capture at least two measured request bodies that differ. Honesty `addon_definition` applies to **non-stream** traces only.
3. THE P0.0 work SHALL NOT use `pipeline_9stage_verify.py` as the latency or CPU driver.
4. THE P0.0 work MAY add a thin wrapper (URL, Preflight, allow-path filter, cell labels) but SHALL NOT reimplement the capacity predicate and SHALL NOT call `gateway_pipeline_bench.run()`.
5. The shipped unit file `test_gateway_pipeline_bench_honesty.py` MAY be noted as already-green on this SHA; re-running it is **not** a P0.0 live gate.

### Requirement 6: Unique prompts and Cell_A sample filter

**User Story:** As a measurement engineer, I want Counted_Samples to be allow-path unique prompts, so that 400 content-filter traces cannot pull p50 under 20 ms.

#### Acceptance Criteria

1. THE harness SHALL send a unique prompt body on every measured request (nonce or UUID in the user content) and SHALL fail the 20 ms comparison if two captured bodies are identical or `UNIQUE_PROMPT` is not 1.
2. THE harness SHALL exclude warmup from percentiles.
3. THE harness SHALL keep at least N=16 Counted_Samples for Cell_A after warmup.
4. A Counted_Sample SHALL be HTTP 200 (not merely 2xx), `stream=false`, `final_action=allow` (not redact, flag, or rewrite), completion `id` `chatcmpl-loadtest-stub`, `usage.completion_tokens` ≥ Token_Floor, no `zeroshield.scan_only`, and every Nine_Stages name present with `action != skip`. THE harness SHALL drop HTTP 400 (including `content_filter` / `GATEWAY_BLOCK_STATUS`), 401, 403, 429, 503, status 0, redact-200, kill-switch 503, traces with model stages `skip`, and samples with `ttft_ms` or SSE. Token_Floor is the **response** completion count, not the request `max_tokens` field alone.
5. THE harness SHALL record excluded counts by class.
6. THE wrapper SHALL NOT use the stock bench’s unfiltered `addons` list as Cell_A tax and SHALL NOT call `gateway_pipeline_bench.run()`. THE wrapper SHALL own the POST loop, filter records, then compute percentiles.
7. For Cell_A the driver SHALL set `WORKERS=1` and `CONN=1` (or equivalent serial inflight). Gateway `WEB_CONCURRENCY=1` SHALL NOT be treated as homogeneous if the client queues.
8. For Cell_A every request SHALL send the same pinned `max_tokens` integer ≥ Token_Floor (omit is forbidden) and `enable_routing: false`. Cell_A user content SHALL be a short unique prompt (UUID); leftover size-cell `PROMPT` SHALL NOT be reused.

### Requirement 7: Cell_A is the only 20 ms tax comparator

**User Story:** As an operator, I want one labelled number compared to 20 ms.

#### Acceptance Criteria

1. THE Evidence_Pack SHALL compare **only** Cell_A non-stream Firewall_Tax p50 and p99 (from Counted_Samples) to 20 ms.
2. THE Evidence_Pack SHALL report Cell_A Wall p50/p99 labelled **N/A-not-tax**. It SHALL NOT PASS/FAIL Wall against 20 ms.
3. THE Evidence_Pack SHALL report per-stage p50/p99 for stages **present** on Counted_Samples. Missing stages SHALL drop the sample (Requirement 6.4). THE harness SHALL NOT synthesize 0 ms for absent stages.
4. `full_nine_stages` true is **not** sufficient for a 20 ms comparison. THE comparison also requires Preflight pass, unique bodies, Token_Floor, and Requirement 6.4 on every Counted_Sample.
5. THE Evidence_Pack SHALL NOT compare to 20 ms: UI Duration, `ttft_ms`, streaming `total_latency_ms`, `processing_time_ms`, stream `t_addon_pre_ms` / `t_addon_post_ms`, Prometheus `stream_ttft_seconds` / `stream_duration_seconds` / `chat_request_duration_seconds`, docker CPU percent, RPS, or Little’s-law `1000/RPS`.
6. IF Cell_A Firewall_Tax p99 ≤ 20 ms AND Requirement 7.4 holds, THEN the scorecard MAY record `20ms_tax: PASS` with stub and `capacity_eligible=false` on the **same** row.
7. IF Cell_A Firewall_Tax p99 > 20 ms, THEN the scorecard SHALL record `20ms_tax: FAIL` and P0.0 SHALL still be complete if the pack is published.

### Requirement 8: Labelled cells never averaged into Cell_A

**User Story:** As a reviewer, I want block, redact, and scans-off as separate rows.

#### Acceptance Criteria

1. THE harness SHALL run an **input-block** cell (content-filter HTTP 400 or 403, model stages skip) and SHALL record Wall and `full_nine_stages=false`. It SHALL NOT be merged into Cell_A.
2. THE harness SHALL run a **redact-and-forward** cell (HTTP 200, `final_action=redact`) and SHALL record tax separately. It SHALL NOT be merged into Cell_A.
3. THE harness SHALL run one **negative** cell (scans-off **or** empty policies for Measurement_Org) with `capacity_eligible=false`. It SHALL NOT be quoted as the 9-stage baseline.
4. THE harness SHALL run one **size** cell: 4096 unique characters, Cell_A posture otherwise, under `MAX_PROMPT_LENGTH`. Tax SHALL NOT be compared to 20 ms. A 10000-char cell is forbidden (would 400 `dos`).
5. Kill-switch, output-guard block, stream abort, forced 401/429 smokes, and an unpinned-worker cell are **explicit N/A** (not 20 ms rows). Cell_A still excludes 503 and 401/429 if they appear.

### Requirement 9: Streaming vs 20 ms is N/A-instrument (no streaming run)

**User Story:** As an operator, I want the pack to state why streaming tax is invalid, without running a streaming cell.

#### Acceptance Criteria

1. THE Evidence_Pack SHALL state that on this SHA, stream `total_latency_ms` is `now − provider_start_ts` (pre-model stages excluded) and stream `total_latency_ms − model_output_ms` **equals `ttft_ms`**.
2. THE Evidence_Pack SHALL mark streaming Firewall_Tax vs 20 ms as **N/A-instrument** until G1.1.
3. THE P0.0 work SHALL NOT implement the honest-stream-latency-metric spec and SHALL NOT require a streaming measurement cell.

### Requirement 10: Optional Tier-2-on and live-provider cells

**User Story:** As a reviewer, I want Haiku-on-path measured when possible, and N/A when not.

#### Acceptance Criteria

1. THE harness MAY run one non-stream stub cell with `ENABLE_TIER2=true`, TTL 0, sample rate 1, unique prompts. IF Bedrock is unreachable, THEN the cell SHALL be `N/A-measured` with the error, not omitted.
2. THE harness MAY run one non-stream live-model cell if a provider key exists (`id` ≠ `chatcmpl-loadtest-stub`); else `N/A-no-key`.
3. Live-model Wall and T2-on tax SHALL NOT be compared to 20 ms as Cell_A Firewall_Tax.

### Requirement 11: CPU and RPS reporting

**User Story:** As a capacity reader, I want CPU sampled mid-window on a pinned cgroup.

#### Acceptance Criteria

1. DURING Cell_A, THE harness SHALL sample gateway container CPU from `docker stats` and/or cgroup `cpu.stat` **mid-window**.
2. THE Evidence_Pack SHALL report offered RPS, achieved RPS, gateway CPU%, `WEB_CONCURRENCY=1`, and pinned `cpus`.
3. THE Evidence_Pack SHALL label stub RPS as **invalid for capacity**.
4. CPU% and RPS SHALL NOT be compared to 20 ms. Cgroup CPU% is not MASTER “CPU-only tax.”
5. THE Evidence_Pack SHALL NOT treat `/health` RPS or classifier-only GPU RPS as 9-stage capacity.

### Requirement 12: Evidence pack

**User Story:** As a later engineer, I want enough metadata to reproduce or reject the number.

#### Acceptance Criteria

1. THE Evidence_Pack SHALL include: git SHA, compose `git.sha` label, image digest(s), compose project, volume names, `GATEWAY_URL` peer container id, overlay pin dump (no secrets), org Redis config snapshot, stub flags, duration, Token_Floor, T2 TTL/sample, org policy identity (not just summed count), `WEB_CONCURRENCY`, cpus, N, two differing prompt fingerprints, honesty report, per-cell percentiles, excluded-error counts, post-run config re-GET.
2. THE Evidence_Pack SHALL redact API keys, signing keys, and provider tokens.
3. THE Evidence_Pack SHALL state that this SHA uses the shipped scanner plus optional Bedrock Tier-2, not in-process Prompt Guard, and that P0.0 does not implement the Sep 7 HLD.
4. THE Evidence_Pack path SHALL be `docs/perf/evidence/2026-09-10-p0-task0-honesty/` in this worktree.

### Requirement 13: Closed claim scorecard

**User Story:** As a reader of `docs/plans/`, I want only the claims this SHA can adjudicate.

#### Acceptance Criteria

1. THE scorecard SHALL contain: (a) user 20 ms **tax** on Cell_A Counted_Samples; (b) Cell_A Wall as **N/A-not-tax**; (c) MASTER ≤12 ms CPU-only tax (latency, not CPU%); (d) stub is not capacity; (e) T2-on if measured else N/A-measured; (f) PG2 4.1/5.9 ms **N/A**; (g) 100k RPS **N/A**.
2. Row (d) SHALL PASS only if `capacity_eligible` is false on stub cells.
3. THE scorecard SHALL NOT include PIPELINE-0018 UI Duration, unique-prompt as a separate row from Cell_A, or off-SHA 1,064 RPS as measured.

### Requirement 14: Forbidden reporting lies

**User Story:** As a CISO, I want explicit bans on mixes that made prior benches incomparable.

#### Acceptance Criteria

1. THE Evidence_Pack SHALL NOT average block, redact, scans-off, 401, 429, 400, or 503 samples into Cell_A tax.
2. THE Evidence_Pack SHALL NOT quote `full_nine_stages=true` as proof that all Nine_Stages ran.
3. THE Evidence_Pack SHALL NOT quote streaming totals, `ttft_ms`, `processing_time_ms`, or stream `t_addon_*` as Firewall_Tax.
4. THE Evidence_Pack SHALL NOT quote aimesh-dev 13.6 RPS / p99 17.60 ms as this SHA’s result.
5. IF Redis `firewall:config:{Measurement_Org}` or the compiled bundle identity changes mid-run, THEN THE harness SHALL invalidate the run.
