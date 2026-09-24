# rv harness — usage

Shared measurement instrument for the runbook-validation lanes (spec: `SP/HARNESS_SPEC.md`).
`SP` = `/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad`.
Everything lives in `SP/harness`. Do not use any number before `SP/harness/READY` exists; READY records the
validated capacity of the instrument and the binary checksums it was validated with.

| piece | what it is |
|---|---|
| `bin/synthprov` | deterministic OpenAI-compatible provider + per-request recorder (static linux/amd64) |
| `bin/olg` | open-loop load generator + per-request recorder (static) |
| `bin/rvproxy` | pass-through proxy with injectable pre-dispatch delay / mid-stream hold (instrument-honesty tests only) |
| `analyze.py` | recomputes every metric + pass/fail from the raw files (Python >= 3.14 for `.zst`; uses orjson if present) |
| `make_corpus.py` | builds HEADLINE / WORST-BAND / CORRECTNESS corpora with exact PG2 token counts |
| `corpora/*.jsonl` | ready-made corpora (+ `.manifest.json` each) |
| `deploy/*.sh` | GCP.md-compliant VM create / push / start / stop / run / collect / delete helpers |
| `shared/words.json`, `shared/canaries.json` | output-token list, nonce alphabet, canary strings (embedded in the binaries too) |
| `bin/SHA256SUMS` | checksums of the binaries (the harness git SHA is embedded: `olg` logs it, manifests record it) |

Python for the analyzer / corpus tools: `/home/contact_cyberultron_com/AI_Mesh_Firewall/gateway/.venv/bin/python` (3.14, has orjson + tokenizers).

---------------------------------------------------------------------------------------------------------
## 1. Topology and one-command steps

```
DIRECT (floor):  olg VMs ──────────────────────────────▶ synthprov VMs
SUT:             olg VMs ──▶ gateway / prototype (SUT) ──▶ synthprov VMs
```

The load generator never runs on the SUT; synthprov never shares cores with the SUT (GCP.md).

**Validated capacity (see READY; DIRECT, WORST-BAND 1024 in / 400 out, TTFT 150 ms, ITL 20 ms, 70% SSE,
c4-highcpu-8, asia-south1-c):** 6,000 RPS with 5 loadgens (1,200 RPS each) + 4 synthprov (1,500 RPS each),
3 x 300 s repeats, 0 schedule drops, 0 errors, loadgen busy max <= 63%, provider schedule error p99 <= 0.23 ms.
At 8,000 RPS (1,600 per loadgen) there were still 0 drops but loadgen busy reached 75% (> 70%: first failing step).
**Sizing rule: one c4-highcpu-8 loadgen per 1,200 RPS and one synthprov per 1,500 RPS of WORST-BAND traffic.**

**Standard shapes for all lanes (controller decision): loadgen = c4-standard-8, synthprov = c4-standard-8,
asia-south1-c** (`deploy/create_vms.sh c4-standard-8 ...`). Per 1,000 RPS of HEADLINE at ITL 20 ms, provision
**1 loadgen + 1 synthprov** c4-standard-8, i.e. the validated WORST-BAND rule rounded up. HEADLINE requests are
strictly lighter than WORST-BAND: mean 225 vs 400 output tokens (=SSE chunks) and ~562 vs 1,024 input tokens.
This per-1,000 figure is DERIVED, not measured: c4-standard-8 has the same C4 8-vCPU platform as the validated
c4-highcpu-8 with 2x the memory, and the controller stopped further validation. At ITL 10 or 30 ms the chunk
count per request, and so per-second chunk throughput at a given RPS, is unchanged; only stream duration and
concurrency change. Poisson arrivals: +50% loadgen CPU (measured), still inside this rule for HEADLINE by the same
estimate. The rate is split evenly across loadgens (`step.sh` does it).

```bash
H=$SP/harness; export LANE=<your-lane>            # VM names must be rv-<lane>-<role>-<n>
export EVID=$SP/evidence/<your-agent-dir>          # vm-ledger.jsonl + runs/ go here
# 1) VMs (labels, ephemeral ssh key, zone fallback c->a->b, host tuning, binaries pushed + verified)
$H/deploy/create_vms.sh c4-highcpu-8 rv-$LANE-lg-1 rv-$LANE-lg-2 rv-$LANE-prov-1 rv-$LANE-prov-2
$H/deploy/push.sh $H/corpora/headline-22M.jsonl rv-$LANE-lg-1 rv-$LANE-lg-2      # corpus -> ~/rv/corpora/
# 2a) DIRECT floor step (fresh providers, synchronized loadgens, collect, analyze --mode direct)
$H/deploy/step.sh direct-1000 1000 "rv-$LANE-lg-1 rv-$LANE-lg-2" "rv-$LANE-prov-1 rv-$LANE-prov-2" \
   "-corpus /home/rv/rv/corpora/headline-22M.jsonl -ramp 30 -warmup 60 -duration 300"
# 2b) SUT step: configure the SUT's upstream to http://<prov-ip>:8080 (any of 8080-8083 on every provider VM),
#     then point the loadgens at the SUT:
TARGETS="http://<sut-ip>:<port>" ANALYZE_FLAGS="--profile-stages canon,det,sem,resolve,dispatch,out,audit" \
$H/deploy/step.sh sut-1000 1000 "rv-$LANE-lg-1 rv-$LANE-lg-2" "rv-$LANE-prov-1 rv-$LANE-prov-2" \
   "-corpus /home/rv/rv/corpora/headline-22M.jsonl -ramp 30 -warmup 60 -duration 300 -auth-file /home/rv/rv/auth"
# 3) results: $EVID/runs/<run>/analysis/summary.{md,json}; raw files in $EVID/runs/<run>/<vm>/...
# 4) cleanup (closes ledger entries)
$H/deploy/delete_vms.sh rv-$LANE-lg-1 rv-$LANE-lg-2 rv-$LANE-prov-1 rv-$LANE-prov-2
```

Other helpers: `deploy/sh.sh VM 'cmd'` (ssh), `deploy/push_bin.sh VM...` (redeploy binaries),
`deploy/prov_start.sh|prov_stop.sh VM RUN [flags]`, `deploy/proxy_start.sh|proxy_stop.sh`,
`deploy/olg_run.sh RUN TOTAL_RATE TARGETS "FLAGS" LG...`, `deploy/collect.sh RUN DEST VM...`.
Env for step.sh: `PROV_ENV` (env vars for synthprov, e.g. `GODEBUG=gctrace=1`), `EXTRA_VMS` (extra VMs whose
`~/rv/runs/RUN` should be collected, e.g. your SUT VM if you write SUT metrics there), `RUNS`.
Auth: put the Authorization header value (e.g. `Bearer sk-...`) in a file on each loadgen and pass
`-auth-file`; it is never logged or written to manifests (`auth_set: true` only).

Every step: >= 60 s warm-up, 300 s measurement (GCP.md), repeats at the knee. Rate steps above the SUT's knee
are expected to FAIL; report the highest repeatable PASS and the first FAIL.

---------------------------------------------------------------------------------------------------------
## 2. synthprov (provider)

`synthprov -listen :8080,:8081,:8082,:8083 -ttft 150ms -itl 20ms -record records.jsonl -stats-out stats.json -cpu-log cpu.jsonl`

* `POST /v1/chat/completions` (and `/chat/completions`), `GET /v1/models`, `GET /_rv/stats`, `GET /healthz`.
  Reads and parses the full body (bodies up to `-max-body`). OpenAI-exact SSE framing: role chunk, one
  content token per chunk (`data: {...}\n\n`, flushed per chunk), final chunk with `finish_reason`, usage
  chunk when `stream_options.include_usage`, `data: [DONE]`. JSON: one `chat.completion` with usage.
* Content is deterministic: token j = `words.out_tokens[splitmix64(fnv1a64(rid)+j) % N]` (~4.6 chars/token,
  ~1 PG2 token per provider token). Same rid -> same content for SSE and JSON.
* Output length n = min(max_tokens | max_completion_tokens, `x-synth-tokens`) if either is present, else `-tokens`.
* Pacing: token j (0-based) at `recv + TTFT + j*ITL` from one absolute schedule (no drift); JSON bodies are
  sent at `recv + TTFT + (n-1)*ITL`. `recv` = request body fully read.
* Per-request controls, header first, else an in-prompt directive `rvsynth{k=v;k=v}` (for gateways that do not
  forward `x-synth-*`; `make_corpus.py --synth-in-text` writes them):
  `x-synth-tokens`, `x-synth-ttft-ms`, `x-synth-itl-ms`, `x-synth-tool: 1` (needs `tools` in the request ->
  streams a tool_call whose arguments JSON is split over >= 5 fragments, finish_reason `tool_calls`),
  `x-synth-inject: email | aws | split-aws` (output canary inserted mid-response; split-aws = AWS key split
  across 3 chunks mid-token), `x-synth-fault: prebyte-500 | disconnect-after:N | malformed-after:N | stall-after:N:MS`.
* Request id: `x-request-id` header; if a gateway drops it, synthprov re-derives the olg request id from
  the nonce in the prompt (`rid_src` = header | nonce | generated), so joins still work. Content is keyed on
  that same id, so the client/provider content hashes still match. v1 (52a584e9) forwards no client headers but
  forwards the messages unchanged on ALLOW, so the nonce path joins v1 traffic (v1-bench: 20/20 joined, content
  sha equal). A `body.user` id path is NOT implemented (it would need a synthprov rebuild, which is frozen). It
  is only needed for a gateway that rewrites the prompt text around the nonce.
* Canary check: every request's RAW body and decoded message text are scanned for every canary value and
  fragment (`shared/canaries.json`) -> `canary_hits` (proves REDACT/BLOCK at provider bytes).
* GC: default `-gc -1 -memlimit auto` (GC off, soft limit 60% RAM). Measured: GC mark phases delayed the token
  schedule of ~25% of live streams by ~12 ms (runs/cal-1000-gctrace); with GC off the per-stream max schedule
  error dropped from 13.2 ms to 0.39 ms (runs/cal-1000c). GC cycles are reported in stats.json `runtime`.

### synthprov record (records.jsonl, one line per request)
`rid, rid_src, recv_ns (ns since process start; same-host only), nonce, stream, status, body_len, body_sha256,
body_read_ns, canary_hits[], max_tokens_req, tokens_out, ttft_ms, itl_ms, recv_to_first_ns, recv_to_last_ns
(pre-write instants of first/last content token), recv_to_last_written_ns, sched_last_ns, sched_err_last_ns,
sched_err_max_ns (worst token lateness vs schedule in this stream), write_max_ns (longest write+flush: backpressure
evidence), content_sha256, content_len, tool_args_sha256, inject, inject_applied, fault, tool, param_src, client_gone,
err, sampled, emit_ns[] + emit_cum[] (sampled requests: per-chunk ready offset from recv and cumulative payload bytes)`.
`stats.json`: counters, final GC/scheduler-latency histograms (`runtime`), CPU summary, host info.

---------------------------------------------------------------------------------------------------------
## 3. olg (open-loop load generator)

`olg -targets URL[,URL..] -corpus FILE -rate R -duration S -warmup W -ramp P [-arrival constant|poisson] -out DIR`

* Arrival schedule is precomputed and independent of completions (constant or Poisson; optional linear ramp
  then warm-up at full rate; phases recorded per request: 0 ramp, 1 warm, 2 measure). Each request runs in
  its own goroutine at its scheduled instant. HTTP/1.1 keep-alive, unlimited pool; connection opens counted.
* Lateness `late_ns = send_start - scheduled`; `> -drop-ms` (default 5 ms) is a schedule DROP -> run invalid for
  capacity claims. The dispatcher sleeps with `clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME)` (kernel hrtimer),
  not a Go timer: an execution trace (evidence/harness-builder/tracestall, runs/diag-stall) showed a Go sleep timer
  left in the heap of an idle P firing ~2 ms late, giving rare 2-6 ms dispatcher stalls (one DROP in 1.2M requests
  at 4000 RPS, runs/val-4000). With the kernel sleep the max lateness at the same load fell from 3.57 ms to
  1.13 ms (runs/diag-stall2). Everything else still uses Go timers with a 100 us timerfd heartbeat (Go's netpoller
  otherwise sleeps in whole ms: p99 1.06 ms -> 12-100 us, evidence/harness-builder/timer-exp/results.txt).
* `-flight-dir DIR` enables an execution-trace flight recorder that dumps the last ~3 s of runtime trace when a
  request is later than `-flight-ms` (diagnostics; `evidence/harness-builder/tracestall` has the analyzer).
* Every request gets a unique nonce `(ref-w1-..-w6)` substituted for `{{RVNONCE}}` in the corpus prompt (words:
  run tag, loadgen index, 4 bytes of seq). Every nonce is exactly 15 PG2 tokens (both 22M and 86M tokenizers,
  verified on 200k random nonces), so corpus token counts hold per request. `x-request-id` = UUID from
  sha256(nonce). The run tag varies per run by default (no prompt repeats across runs; `-run-tag` pins it).
* SSE share: `-sse-frac 0.7` = exactly 70% (Bresenham-interleaved), independent of the corpus pick.
* Target choice per request = seeded hash of (loadgen, seq) over `-targets` (uniform; plain round robin created
  synchronized bursts at each target when several loadgens run: runs/val-6000-r4).
* Dispatch: 3 redundant dispatchers (`-dispatchers`, `-dispatch-stagger-us 250`) share a precomputed schedule; each
  sleeps in the kernel and the first awake claims due arrivals (CAS). `manifest.counts.claims_by_dispatcher` shows how
  often a backup covered for the primary.
* Several loadgens: give each `-lg i -lg-count K` and `-start-at <unix ms>` (deploy/olg_run.sh does this).
* `-H 'Name: value'` extra headers; `-synth-headers` (default on) sends corpus `synth{}` as `x-synth-*`.
* GC: `-gc -1 -memlimit auto` by default (same reason as synthprov).

### olg record (requests.jsonl, one line per scheduled request, including failures)
`rid, lg, seq, ph, cls, cid, stream, tokens_in, max_tokens, target, sched_ns (offset from run epoch), late_ns,
conn_ns, reused, wrote_ns, fb_ns, hdr_ns, first_ns, last_content_ns, end_ns (all relative to send_start),
status, err (connect|timeout|transport|eof_mid_stream|no_done|malformed_sse|error_event|json_invalid|http_NNN|
drain_cancel|inflight_cap), err_detail, chunks, events, bytes, content_sha256, content_len, tool_args_sha256,
done_seen, finish, usage_seen, malformed, error_event, post_done_events, post_done_hang, disp / plan_ver / stages
(x-rv-disposition / x-rv-plan-version / x-rv-stages response headers), resp_rid, ctype, canary_hits (canaries seen
in the response), nonce, inject, fault, sampled, arr_ns[] + arr_cum[] (sampled: per content event arrival offset
and cumulative payload bytes)`.
Arrival time of an event = completion time of the socket read that delivered its last byte (a net.Conn wrapper
stamps every Read). Also written: `manifest.json` (config, counts, lateness, CPU, runtime, host), `cpu.jsonl`,
`timeseries.jsonl` (per second: scheduled, sent, inflight, ok, errors, drops, late max, conn opens).

---------------------------------------------------------------------------------------------------------
## 4. analyze.py (metrics; re-runnable from raw files alone)

```bash
PY=/home/contact_cyberultron_com/AI_Mesh_Firewall/gateway/.venv/bin/python
$PY $H/analyze.py --client RUN/lg-vm-1/lg RUN/lg-vm-2/lg --provider RUN/prov-vm-1/prov RUN/prov-vm-2/prov \
   --mode direct|sut [--profile-stages canon,det,...] [--policy none|enforce|monitor] [--corpus corpus.jsonl] \
   [--slo-ms 20] [--err-budget 0.001] [--drop-ms 5] [--phases 2] [--per-second] --out RUN/analysis
```
Durations only, each on one host's monotonic clock (never cross-host absolute timestamps):
* `T_addon_total = client end_ns - provider recv_to_last_ns`; `T_addon_first = client first_ns - provider recv_to_first_ns`.
* `T_release_lag_max` (sampled SSE requests, default 10% = fnv1a64(rid)%10==0 on both sides; `-sample-mod` must
  match on olg and synthprov) = max over provider content pieces j of (arrival of the FIRST client event whose
  cumulative payload bytes cover piece j) - (emission offset of piece j): the worst added delay suffered by any
  piece of content. The spec's literal per-arrival form (arrival k minus emission of the last piece fully covered
  at k) is reported as `T_release_lag_max_arrival`; it under-reports when a gateway releases several held tokens
  together (by one ITL per coalesced token) and over-reports by ~ITL when a gateway splits tokens across events
  (pattern-aware holdback does this); tests/test_analyze.py demonstrates both. Use `T_release_lag_max`.
  Output-redacted streams (content length changed) are "unmappable" and counted, not guessed.
* `T_fw_addon` = `T_addon_total` (JSON); `max(T_addon_total, T_addon_first, T_release_lag_max if sampled)` (SSE).
  GROSS: includes both network hops. Report the DIRECT run at the same rate as the network/HTTP floor; never
  subtract floors per request.
* Negative addons are never clamped: any negative value marks the run INVALID. Every scheduled request must be
  recorded (manifest scheduled == recorded) or the run is INVALID.
* Outcome strata (runbook T01 app_infra_error_rate; §1.1 policy blocks are correctness traffic reported separately):
  - QUALIFIED: HTTP 200, complete ([DONE] / valid JSON), no stream/transport error, joined to exactly ONE provider
    call (retries/duplicates are errors), provider 200, content sha equal to the provider's (output REDACT: provider
    injected, no output canary reached the client, client length = provider length - injected piece + 0..64 bytes),
    unique nonce; `--mode sut` also requires disposition in `--qualified-dispositions` (default ALLOW,REDACT,FLAG;
    FLAG = transport-allow) and every `--profile-stages` stage EXECUTED.
  - POLICY: status in `--block-statuses` (default 400,403,422,451) AND block evidence (disposition BLOCK, or an
    error envelope matching `--block-envelope-re`). v1 returns CONTENT blocks as HTTP 400 `code=content_filter`
    (gateway main.py `_resolve_content_block_status`, GATEWAY_BLOCK_STATUS default 400) and keeps 403 for auth/actor
    blocks, so "403 only" would misfile v1 blocks. The stratum splits into expected (prompt labelled attack/secret),
    FALSE-POSITIVE (prompt labelled benign, no output injection) and other; FP rate = FP blocks / benign offered;
    the cohort's client latency is reported separately. Not qualified and NOT counted as infra errors.
  - POLICY MISS (`--policy enforce`): a BLOCK-expected prompt served 200 (also a safety failure).
  - INFRA ERROR: everything else not qualified (5xx, non-policy 4xx, 429/503 shed, timeout, incomplete stream,
    content mismatch, skipped stage, ...). Only these count toward `--err-budget` (0.1%).
  Latency percentiles are computed over the qualified cohort only.
* Disposition/stage source is pluggable (`--disposition-source auto|xrv|v1`): `xrv` = `x-rv-disposition` /
  `x-rv-stages` recorded by olg; `v1` = `pipeline_trace.final_action` + `pipeline_trace.stages` (EXECUTED iff
  action != skip), or `final_action` / `v1_stages` ("auth:allow,...") / `resp_headers["x-zeroshield-action"]`
  (redacted|flag) fields. olg does not capture v1 bodies or X-ZeroShield headers, so v1 fields must be added to an
  augmented copy of requests.jsonl (v1-bench joins v1's audit events by client_correlation_id = x-request-id).
  `auto` uses x-rv fields when present, else the v1 fields.
* Safety failures (`--policy enforce`): a planted canary reached the provider for a REDACT/BLOCK entry, a BLOCK entry
  reached the provider at all, or an output canary reached the client.
* Step PASS (sut): qualified-cohort p99 T_fw_addon < --slo-ms, infra errors <= --err-budget of offered, 0 drops,
  0 safety failures, run VALID. Harness PASS (direct): 0 drops, 0 infra errors and 0 policy outcomes, provider p99
  sched_err_last within --sched-tol-ms (1 ms), loadgen CPU max < --cpu-max (70%) in the measurement phase, run VALID.
* Percentiles are nearest-rank over the merged raw samples of all loadgens (never averaged per worker).
* CPU "busy" = 1 - cpuidle residency / wall (tasks + IRQ + softirq). /proc/stat is tick-sampled on these kernels
  and was observed to read ~0% on a loadgen whose own process used 16.5% of all CPUs; it is kept as `busy_tick`.
* Outputs: `summary.json` (everything), `summary.md` (human table). Error reasons and up to 20 examples each for
  errors and safety failures are included, plus `instrument_health` per process: GC cycles, max goroutine
  scheduling latency, and TCP counter deltas (retransmits, loss probes, RTO timeouts) from the nstat snapshots.

---------------------------------------------------------------------------------------------------------
## 5. Corpora (make_corpus.py)

Ready-made (seed-fixed; manifests list sha256, token stats, recount verification):
`corpora/headline-{22M,86M}.jsonl` (5000 entries, tokens_in ~U[100,1024], max_tokens ~U[50,400], benign),
`corpora/worst-{22M,86M}.jsonl` (2000 entries, 1024 in / 400 out), `corpora/correctness-{22M,86M}.jsonl` (2000:
80% benign of which 1/8 inject=email and 1/8 inject=split-aws, 5% of benign with tools+tool=1, 10% PII, 5% secret,
5% injection; tokens_in ~U[100,512], max_tokens ~U[50,200]).
`tokens_in` = Llama-Prompt-Guard-2 tokens (the tokenizer named in the entry; no special tokens, padding and
truncation disabled) of `system + "\n" + user` with a real nonce. The PG2 normalizer deletes every space, so token
counts are not additive over words; entries are fitted to the exact target by search and re-verified.
Build others: `$PY make_corpus.py --profile headline|worst|correctness --n N --seed S --tokenizer 22M|86M
--models $SP/models --benign <repo>/tests/detection_corpus/benign.jsonl --out FILE [--synth-in-text]
[--min-in/--max-in/--min-out/--max-out]`. Corpus entries carry `expect{input,output}` used by `--policy enforce`.
Injection fixtures are verbatim from tests/detection_corpus/malicious.jsonl (ids in canaries.json).

---------------------------------------------------------------------------------------------------------
## 6. Known limitations / things that will bite you

* The GCP network drops ~1 in 10^5-10^6 provider->client segments in-zone (no drops in qdisc/NIC counters on our
  VMs; `nstat` deltas are saved per run as `nstat.before/after`). On a sparse SSE flow a lost chunk is only
  recovered when later chunks arrive, so the chunk (and the next one) arrive ~1-2 ITL late (20/40 ms at ITL 20).
  This is real network behaviour, shows in the DIRECT floor's release-lag tail (p99.9/max), and affects SUT runs
  equally. Compare SUT tails to the DIRECT floor at the same rate.
* The first request on a new connection occasionally took ~16 ms longer (ramp phase only; steady-state requests
  reuse pooled connections). Keep >= 60 s warm-up.
* Release lag is sampled (10%); a hold applied to < ~1% of streams can escape p99 of T_fw_addon. Use
  `-sample-mod 1` (both olg and synthprov) for honesty/correctness runs at moderate rates.
* Response headers are sent before content, so a disposition header cannot describe OUTPUT redaction on SSE;
  output-REDACT qualification uses the canary + length rule above.
* A shared synthprov record file across runs makes rids repeat if run tags collide (1/256): the analyzer then
  reports `provider_calls_N`. step.sh always starts providers with fresh files.
* Ports: synthprov listens on 8080-8083 by default (spreads ephemeral ports); rvproxy on 9000.
* Harness VMs need `deploy/prep_host.sh` (create_vms.sh runs it): besides fd limits it lowers the EEVDF base slice
  to 300 us and disables RUN_TO_PARITY. Without it a woken thread can wait ~one 2.8 ms slice for a busy CPU; traces
  showed 2-10 ms dispatcher stalls (runs/diag-6000, diag-6000b). If you bring your own VMs, run prep_host.sh on them.
* Poisson arrivals cost ~1.5x loadgen CPU at the same mean rate (runs/val-poisson-4800 vs val-const-4800, same VMs);
  provision >= 1.5x the loadgens for Poisson runs.
* The very first arrival of loadgen 0 is scheduled at t = 0 and can be ~10 ms late while the dispatchers start;
  it is in the ramp phase, which the analyzer excludes (phase 2 only by default).
* Data volume: a 6,000 RPS x 300 s WORST-BAND step is ~1.4 GB zstd-compressed (10% per-chunk sample). /tmp is RAM-backed
  on the controller; keep large runs on disk (this lane's are under /home/contact_cyberultron_com/rv-harness-evidence).
* rvproxy is a test instrument (one upstream, simple SSE re-framing); it is not a model of a gateway.

---------------------------------------------------------------------------------------------------------
## 7. Validation record (details in READY)

* Unit tests: `go test ./...` (SSE framing incl. CRLF/bare-CR/long lines/truncation; chunk parser 200k-case differential
  vs encoding/json + native fuzzing (2.67M execs); request extractor 150k-case differential, zero allocations; nonce
  / rid / sampling / canary tests; provider framing, determinism and allocation benchmarks) and
  `python -m unittest discover -s tests` (22 tests: percentile definition, release-lag math incl. the required
  mid-stream-hold-visible-in-lag-not-in-total case, coalesced/split token cases, join by nonce, INVALID on negative
  addon or incomplete records, drops, safety failures, output-redaction length rule, stage checks).
* DIRECT capacity: 6,000 RPS WORST-BAND, 3 x 300 s repeats PASS (0 drops, 0 errors, loadgen busy <= 63%, provider
  schedule error p99 <= 0.231 ms); 8,000 RPS fails only the 70% loadgen-CPU rule (0 drops).
* Instrument honesty (runs/hon-report/honesty.md): injected 5.06 ms pre-dispatch delay -> T_addon_total +5.107 ms;
  injected 30.01 ms hold at content event 50 -> T_release_lag_max +29.68 ms while T_addon_total moved 0.011 ms and
  the sub-20 ms claim FAILS; provider TTFT 50/500/2000 ms leaves T_addon_total p50 within 0.006 ms of the floor.
