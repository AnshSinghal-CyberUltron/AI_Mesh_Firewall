# P0.0 design review — accumulating notes

Patched into `design.md` only after **all five** design lenses return.

## Hidden failures — [P0.0 design hidden failures](7490dc58-1c87-4dbd-9b80-9676f23bd558)

**Verdict:** Preflight plus URL binding do not close the `aimeshperf` hole. Host `:8300` is `aimeshperf-gateway-1` today. Stock `gateway_pipeline_bench.py` defaults `GATEWAY=http://127.0.0.1:8300` at **import time**, `UNIQUE_PROMPT=0`, `MAX_TOKENS=16`. Unpublishing `:8300` makes that default *more* likely to hit the forbidden stack.

**Accept when patching:**

1. **Single `GATEWAY_URL`** for preflight GET, probe POST, and every Cell_A POST. Resolve to a **listening** socket; TCP-owner inspect `com.docker.compose.project=aimf_p0`. Reject host `127.0.0.1:8300` on this machine (it is `aimeshperf`).
2. **Do not call `gateway_pipeline_bench.run()`.** Wrapper owns the POST loop. Honesty helpers only. Set `GATEWAY`/`UNIQUE_PROMPT`/`MAX_TOKENS` before any import if those module constants are used at all.
3. Overlay **`name: aimf_p0`** (do not rely on a shell `export`). Pins are **literals** — do not copy `compose.perf.yml` `${PERF_*}` interpolations.
4. Policy oracle = **gateway in-memory org cache** (observability that reads `_org_caches[org]`, or a rule that must fire), not Redis GET and not `/health.policy_count`.
5. **One request factory** for probe and Cell_A: `max_tokens>=32`, UUID on the wire, `stream=false`, stub id, remap/sidecar URL **not** 8300/8100.
6. `docker stats` target MUST be the same container as the POST peer (no CPU from `aimf_p0` + tax from `:8300`).
7. Seed via `aimf_p0` control, not host `:8100` (`aimeshperf-control-1`).
8. `compose build --build` is not a Compose flag — use `docker compose build --no-cache` or `up --build` as documented.

## False positives — [P0.0 design false positives](8ddad604-615e-43e4-97e6-19ed1a525b5c)

**Verdict:** Design is already shrunk. No dropped SLO cells came back. Over-scope is copy-paste risk (`compose.perf.yml` / aimesh-dev gateway), not extra matrix cells.

**Accept when patching:**

1. Re-implement **pattern only** (unpublish ports, literal pins, worker pin). Do **not** copy `compose.perf.yml`, SYS_PTRACE, GIL/GC/trace-mode keys (not on this SHA), `perf-token-stub`, `${PERF_*}` interpolation, `WEB_CONCURRENCY=4`, tok/s 100.
2. Import honesty helpers; do **not** rewrite them or use stock `run()` / unfiltered `addons` as Cell_A tax.
3. No G1.1 product edit; streaming remains pack **text** only.
4. Runnable cells stay A / block / redact / negative / size-4096. T2/live remain MAY. No 401/429 smokes, no 10k-char, no unpinned-worker cell.
5. No permanent extra client compose service; `docker compose run --rm` or a remap is enough.

**Keep:** overlay isolation, Counted_Sample filter, 400 content-filter, `WEB_CONCURRENCY=1`, scorecard.

## Observability — [P0.0 design observability](5db65e8b-cecb-4063-beb1-d1175a11e208)

**Verdict:** Five locks hold (non-stream tax, streaming text-only N/A, Wall/CPU% not vs 20 ms, `full_nine_stages` not nine stages). Leftover risk is **how the pack prints** stream math, MASTER 12 ms, and the honesty boolean.

**Accept when patching:**

1. REPORT.md TTFT text: overwrite is `now − provider_start_ts`; subtraction **aliases** TTFT (rounding / `setdefault`); `stage_sum` may exceed total; neither root subtraction nor stage-sum is Firewall_Tax.
2. MASTER row (c) is a **latency** vs 12 ms (pin SLO F p50 vs `T_addon_pre`); never CPU%.
3. Caption `honesty.full_nine_stages` as two timers; nine-`action != skip` remains the 20 ms completeness gate.
4. Wall = `pipeline_trace.total_latency_ms`, not httpx `wall_ms`.
5. Label block / redact / negative / T2 / live tax **N/A-not-20ms**.

## Stale state — [P0.0 design stale state](fc1c8a47-d38b-4106-9ecc-83c97b24d1c8)

**Verdict:** Labels, `down -v`, Redis snapshots, literal signing key, `WEB_CONCURRENCY=1`, and post-run re-GET prove **Redis bytes**, not the in-process last-good caches `proxy_chat` reads.

**Accept when patching:**

1. After seed: **restart gateway** or poll until org-scoped RAM matches seed (`GET /v1/observability` policy version/count for the API key’s org). Fail if Redis is full and RAM is empty.
2. Empty-before-seed: `firewall:config*`, `policies:compiled*`, `llm:model_configs*`, `kill_switch:*`, plus volume CreatedAt / `aimf_p0_*` names. Overlay **`name: aimf_p0`**.
3. HMAC last-good = **Redis ≠ RAM**; post-run re-GET both.
4. Negative cell: new process **or** HMAC-valid payload + confirmed RAM, then restore and re-confirm before Cell_A.
5. Assert `get_config` used Measurement_Org’s own entry (observability `input_scan_enabled`), not `default`/global.
6. API key org slug must equal the snapshot slug.

## Edge cases — [P0.0 design edge cases](12b4f073-3a9c-4feb-aa8e-ba0cc555936b)

**Verdict:** Cell_A still mixes unlike traces if the wrapper keeps stock `addons` or a weaker filter than Req 6.4. HTTP 400 content-filter traces carry `pipeline_trace` with `model_output=0`. `WEB_CONCURRENCY=1` does not pin driver inflight.

**Accept when patching:**

1. Counted_Sample = filter **records**, then percentile. Never `_merge_pct` stock `addons`. Do not call `run()`.
2. Positive gate: HTTP **200** (not 2xx), `final_action==allow`, stub id `chatcmpl-loadtest-stub`, `usage.completion_tokens >= 32`, nine names present, each `action != skip`. Drop 400/403/401/429/503/0, redact/flag/rewrite, `zeroshield.scan_only`, skip-model, SSE/`ttft_ms`.
3. Pin wrapper `MAX_TOKENS` to **one** integer ≥ 32 and ≤ org ceiling; pin org `max_response_tokens`; refuse omitted `max_tokens`. Token_Floor is **`usage.completion_tokens`**, not the request field alone.
4. Pin Cell_A driver `WORKERS=1` and `CONN=1` (serial). Overlay `WEB_CONCURRENCY=1` does not make 64-way queueing homogeneous.
5. Pin `ENABLE_ROUTING=0` (stock default is on).
6. Cap Cell_A prompt bytes (short + UUID only). Run Cell_A first, or restore org config and re-preflight after negative/T2. Size 4096 is its own file; same Counted_Sample filter so dos-400 unique-char prompts are not a fake size tax.

## Devil's Advocate lock (2026-09-10) — all five design lenses in

| Challenge | Decision |
|---|---|
| Redis GET is identity | **Reject.** Chat reads HMAC last-good RAM. After seed: restart gateway or poll `/v1/observability` until `policy.policy_count`/`version` match the seed. Fail if Redis is full and RAM is empty. |
| Observability `input_scan_enabled` proves org config | **Reject as written.** `GET /v1/observability` has **no** `input_scan_enabled`. Config fields use `get_config` (falls back to `default`/global). Policy RAM **does** read `_org_caches[org]`. Config truth = restart-after-seed + probe stages `input_scan`/`output_guardrail` `action != skip` + `organization` == Measurement_Org. |
| `docker compose build --build` | **Reject.** Not a Compose flag. Use `build --no-cache` or `up -d --build`. |
| Export `COMPOSE_PROJECT_NAME` without overlay `name` | **Reject.** Base `docker-compose.yml` is `name: ai_mesh_firewall`. Overlay **must** set `name: aimf_p0`. |
| Wrap `run()` and post-filter `addons` | **Reject.** Own POST loop. Filter records, then percentile. |
| `max_tokens ≥ 32` without pinning | **Reject.** One literal (32). Token_Floor = response `usage.completion_tokens`. Duration-0 stub reports 1 token. |
| `WEB_CONCURRENCY=1` is homogeneous Cell_A | **Reject.** Stock driver is WORKERS=8 × CONN=8. Pin driver 1×1. |
| Negative cell = DEL Redis | **Reject.** Last-good keeps Cell_A rules. New process or HMAC-valid payload + confirmed RAM, then restore + re-preflight before Cell_A. Prefer Cell_A first. |
| Copy `compose.perf.yml` | **Reject.** Pattern only. No SYS_PTRACE, GIL/GC, perf-token-stub, `${PERF_*}`, WC=4. |
| MASTER 12 ms is CPU% | **Reject.** Latency vs 12 ms (SLO F p50 vs `T_addon_pre`). |

`design.md` patched. Next: `tasks.md`.
