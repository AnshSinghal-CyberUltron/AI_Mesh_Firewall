# Measurement harness spec (shared instrument for v1 baseline, v2 prototype, micro-claims)

SP = /tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad
Code lives in SP/harness/ (Go module + Python analyzer + deploy scripts). Build static Go binaries on the controller
(install Go into ~/.local/go if missing) and scp them to VMs.

## 1. synthprov — deterministic OpenAI-compatible provider + recorder (Go)
- POST /v1/chat/completions (stream true/false), GET /v1/models. Reads the FULL body, parses JSON.
- Content: token j of request r = word chosen by hash(request_id, j) from a fixed benign English word list
  (~4 chars/token incl. leading space). 1 token per SSE chunk. Deterministic so the client can hash-check content.
- Output length: min(max_tokens, x-synth-tokens header, default flag). Pacing: TTFT (x-synth-ttft-ms / flag, default 150)
  then one chunk every ITL (x-synth-itl-ms / flag, default 20). Use a single timer schedule per stream (no drift accumulation).
- Exact OpenAI SSE framing: `data: {chat.completion.chunk json}\n\n`, first chunk has role, last chunk has finish_reason,
  optional usage chunk when stream_options.include_usage, then `data: [DONE]\n\n`. Flush after every chunk.
  Non-stream: one chat.completion JSON with usage.
- Tool calls: if request has `tools` and header x-synth-tool: 1 → stream a tool_call whose arguments JSON is split across
  >=5 chunks (fragments), finish_reason tool_calls.
- Test injections (header x-synth-inject): `email`, `aws` (AKIA... synthetic key), `split-aws` (secret split across 3 chunks
  mid-token), `none` (default).
- Faults (header x-synth-fault): `prebyte-500`, `disconnect-after:N`, `malformed-after:N`, `stall-after:N:MS`.
- Recorder: JSONL line per request: request_id (x-request-id), recv_to_first_ns, recv_to_last_ns (monotonic, measured from
  "request body fully read" to first/last content byte written), stream, status, body_len, body_sha256, canary_hits
  (which of the configured canary strings appear verbatim in the RAW request body — used to prove REDACT/BLOCK at provider
  bytes), tokens_out. For a deterministic 10% sample (hash(request_id)%10==0): per-chunk emission offsets (ns from recv).
  Counters endpoint GET /_rv/stats; records flushed to a file given by flag. Must not become the bottleneck.

## 2. olg — open-loop load generator (Go)
- Arrival schedule independent of completions: constant-rate (default) or Poisson (flag), for a duration; each request in its
  own goroutine at its scheduled time. HTTP/1.1 keepalive, large idle pool; record connection opens.
- Lateness L_i = actual send start − scheduled time. Schedule DROP := L_i > 5 ms. A run with drops > 0 is INVALID for capacity
  claims (T01: loadgen_drops = 0). Report lateness p50/p99/max.
- Requests from a prepared corpus JSONL (field: messages, stream, max_tokens, tokens_in, class) cycled with a unique nonce
  injected per request (no two identical prompts in a run). Mix flags: SSE fraction (default 0.7).
- Headers: Authorization, x-request-id (uuid), x-synth-* passthrough flags.
- Per request (monotonic, client host): sched, lateness, t_headers, t_first_content (first SSE delta with non-empty content, or
  body complete for JSON), t_end ([DONE] seen or body complete), status, error class, chunks, bytes, content_sha256 of the
  assembled assistant text, done_seen, response headers x-rv-disposition / x-rv-plan-version / x-rv-stages. For the same 10%
  sample: per-chunk arrival offsets + cumulative char counts.
- Targets: list of base URLs (client-side round robin) — or a single edge URL.
- Output: JSONL per request + manifest.json (rate, duration, seed, mix, versions, host info, git sha of harness).

## 3. Metrics (analyzer, Python, recompute-from-raw)
Join client and provider records on request_id. All are DURATIONS measured on one host's monotonic clock — no cross-host
absolute timestamps (runbook T03 rule).
- T_addon_total_i = client_total_i − provider_total_i            (client_total = t_end − send_start;
                                                                   provider_total = recv_to_last)
- T_addon_first_i = client_first_content_i − provider_recv_to_first_i   (SSE: added time to first token)
- T_release_lag_max_i (10% sample) = max over client arrivals k of
      [(arrival_k − send_start) − (provider emission offset of the last token fully covered by cumulative chars at k)]
  i.e. the worst added delay suffered by any piece of content in the stream (covers mid-stream holds that totals hide).
- T_fw_addon_i := T_addon_total_i for JSON; := max(T_addon_total_i, T_addon_first_i, T_release_lag_max_i where sampled) for SSE.
  NOTE: this is GROSS — it includes the client↔gateway and gateway↔provider network hops. Also run DIRECT (loadgen→provider)
  at the same rate and report its T_addon_* distribution as the network/HTTP floor. Never subtract floors per request.
- Gateway-internal decomposition (from the SUT's own metrics) is reported alongside for attribution, never instead of the above.
- QUALIFIED request (runbook §1.1): HTTP 200 + complete (done_seen for SSE / valid JSON), content hash equals the provider's
  deterministic content (or the disposition header says REDACT and canary checks pass), disposition ALLOW or REDACT,
  x-rv-stages shows every stage of the declared profile EXECUTED (none SKIPPED/UNAVAILABLE), unique prompt, not 429/503/cancelled.
- A rate step PASSES iff over its qualified cohort: p99 T_fw_addon < 20 ms, app/infra error rate <= 0.1% of offered,
  0 schedule drops, 0 safety failures (any canary reaching provider/client when policy required REDACT/BLOCK).
- Capacity = highest offered rate whose 3 repeats all PASS; the next ladder step that fails is reported with it.

## 4. Workload profiles (T01 contract band: <=1,024 in / <=400 out tokens, 70/30 SSE/JSON, English)
- Token counts measured with the PG2 tokenizer of the guard model in use (document which).
- HEADLINE: input tokens uniform in [100, 1024]; output tokens uniform in [50, 400]; 70% SSE / 30% JSON; benign English
  prompts (built from tests/detection_corpus/benign.jsonl + filler), unique nonce each.
- WORST-BAND: 1,024 in / 400 out, 70/30.
- Provider pacing: TTFT 150 ms; ITL swept over {10, 20, 30} ms (the output-holdback lag depends on ITL — this sweep is required).
- CORRECTNESS MIX (moderate rate, not a capacity run): 80% benign / 10% PII (REDACT) / 5% secret (BLOCK) / 5% injection (BLOCK),
  plus provider-side injections email / split-aws for output checks.

## 5. Harness validation (must pass before any SUT number is used)
- DIRECT runs loadgen→synthprov at >= 2× the highest SUT rate you intend to test: 0 drops, provider p99 recv_to_last within
  1 ms of the configured schedule, loadgen VM CPU < 70%.
- Instrument honesty: put a trivial pass-through proxy (Go or nginx) in the path with an artificial 5 ms delay before dispatch and
  a 30 ms hold after chunk 50 → T_addon_total rises by ~5 ms, T_release_lag_max shows ~30 ms; with no delays both collapse to the
  network floor. Save these runs as evidence.
