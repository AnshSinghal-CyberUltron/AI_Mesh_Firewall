# v2 critical-path prototype ("rvproto") — spec

Purpose: produce MEASURED end-to-end numbers for the architecture the runbook §10 prescribes, under the FULL profile, so the
plan's target (p99 T_fw_addon < 20 ms, max qualified RPS, horizontal scaling with no code change) is tested, not derived.
It is throwaway evaluation code (lives in SP/rvproto, later copied into the evidence bundle), NOT a gateway_v2 implementation
and never merged into gateway_v2/. It must nevertheless do ALL the work the real thing would do per request — a prototype
that skips work produces a fake number. Read rb.md §10.3–§10.6 (lines 2147-2264) before building.

SP = /tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad

## Stack
Python 3.12 (uv), uvicorn[standard] (uvloop + httptools), pure ASGI (NO BaseHTTPMiddleware, no FastAPI dependency injection on
the hot path), orjson, aiohttp OR httpx for upstream (pick by measurement; pooled keepalive), redis.asyncio + hiredis,
tokenizers (HF Rust), onnxruntime (CPU) / onnxruntime-gpu + tensorrt (GPU), tritonclient[grpc] (Triton backend),
optional `hyperscan` for one-pass multi-pattern matching. Bounds (workers, pools, queues, stream buffers) come from the REAL
GW03 ResourceContract: import it from /home/contact_cyberultron_com/AI_Mesh_Firewall/gateway_v2 (gateway_v2.runtime.resources).

## Per-request lifecycle (runbook §10.4) — every step mandatory under the FULL profile
1. edge: t0 = perf_counter_ns() at ASGI entry, BEFORE auth. Bounded body read. Parse OpenAI chat request into frozen types.
   OpenAI-shaped error envelopes for every error (status + {"error":{message,type,param,code}}).
2. admit: Bearer key → sha256 → Principal from a per-process RAM cache; miss → Redis HGET (shared store).
   Kill-switch: RAM snapshot refreshed in background from Redis; past a staleness ceiling → UNAVAILABLE → fail-closed 503.
   Quota: local GCRA per org + org token-budget LEASE from Redis via Lua (DECRBY chunk when local lease is exhausted; chunk size
   from ResourceContract). Count shared-state round trips per request (metric). Reject 401/429/503 per contract.
3. plan: pinned immutable ExecutionPlan per org from a RAM snapshot (loaded from Redis at startup, pub/sub push + periodic
   reconcile). Two orgs with opposite plans (org-A ENFORCE: PII→REDACT, secrets→BLOCK, injection(PG2)→BLOCK, output secrets/PII
   →REDACT; org-B: PII→MONITOR, injection→FLAG, output→MONITOR). Plan version pinned per request, carried to audit + header.
4. detect (input): canonicalize once (NFKC, strip zero-width/bidi controls, one bounded percent-decode) with span map;
   deterministic detectors in ONE multi-pattern pass (PII: email, phone, SSN, card+Luhn, IPv4; secrets: AWS AKIA/ASIA,
   GitHub ghp_/gho_/github_pat_, Slack xox*, Google AIza, Stripe sk_live_, JWT, PEM private-key header, generic
   api_key=<high-entropy>) + injection heuristics as SIGNAL findings; semantic: PG2 over ALL windows of the input
   (tokenize with the model's tokenizer; 512-token windows with a declared overlap, e.g. 64; no truncation — over-length is an
   explicit 413-style reject) through a GuardBackend with three implementations selected by config:
     - local_cpu   : onnxruntime CPU (dev/CI)
     - local_gpu   : onnxruntime TensorRT EP (fp16, engine cache, warmed before ready) falling back to CUDA EP only if configured,
                     in-process, cross-request MICRO-BATCHER (dedicated inference thread per GPU, batch up to B windows or
                     T µs, both from config/contract) — worker process i uses GPU i % n_gpus
     - triton_grpc : Triton Inference Server (same node or off-box) with dynamic batching
   Submit semantic work first (non-blocking), run deterministic detectors meanwhile, then await the guard. Every detector
   returns Finding(status EXECUTED|SKIPPED|UNAVAILABLE); guard timeout/unready → UNAVAILABLE (never clean).
5. resolve: PURE function (findings, plan, phase) → Decision per §10.5.3 (priority, no severity max, MONITOR non-enforcing,
   UNAVAILABLE routed through the rule's on_unavailable). Mints DispatchAuthorization only for non-blocking dispositions.
6. dispatch: apply REDACT transformations, RE-VERIFY the transformed payload (re-scan: no redacted span's pattern remains),
   then ONE provider call through a pooled keepalive client requiring DispatchAuthorization. Forward x-request-id.
   BLOCK → zero provider calls, OpenAI error envelope (403 + code), before any SSE byte.
7. egress: JSON → output scan of the full content → resolve(OUTPUT) → emit. SSE → incremental parse of upstream frames,
   streaming output scan with PATTERN-AWARE MINIMAL HOLDBACK: release immediately everything that cannot be part of a
   still-incomplete match; hold only the minimal suffix that could still become a match (e.g. a partial word that could become an
   email, a partial "AKIA…" run); redact spans found; re-serialize OpenAI chunks preserving id/model/role/tool_calls fragments/
   finish_reason/usage; [DONE]. Backpressure = await send. Client disconnect cancels the upstream request and pending guard work.
   Record per chunk: processing lag (compute) and holdback wait (time waiting for more upstream bytes) SEPARATELY.
8. audit: exactly one DecisionRecord per phase → bounded asyncio.Queue (size from contract) → background batched writer
   (Redis XADD pipeline); overflow is DROPPED AND COUNTED (audit_completeness_ratio exported). Never awaited on the request path.
- Response headers: x-rv-disposition, x-rv-plan-version, x-rv-stages (e.g. canon:E,det:E,sem:E,resolve:E,dispatch:E,out:E,audit:E
  where E/S/U = executed/skipped/unavailable), x-request-id.
- /metrics (per worker): HDR/log-bucket histograms for T_input, guard queue wait, guard exec, tokenization, det scan, resolve,
  dispatch connect/wait, release processing lag, holdback wait, T_finalize; counters for round trips, audit drops.
  /healthz and /readyz (ready only after guard warm-up and plan load).

## E2E acceptance (must ALL pass locally with synthprov + local_cpu BEFORE any GCP benchmark; save outputs)
- Official OpenAI Python SDK (openai==2.38.0, same pin as the repo) sync + AsyncOpenAI: non-stream chat, stream chat,
  tool-call stream with fragmented arguments reconstructed exactly, BLOCK → typed APIStatusError (403) with OpenAI envelope and
  zero SSE bytes, bad key → AuthenticationError, quota → RateLimitError.
- Official OpenAI Node SDK (pin the version recorded in docs/plans/evidence/2026-09-21-gw01): stream + tools + typed errors.
- Unmodified-app base_url swap: a small script written purely against the OpenAI SDK works by changing base_url + key only.
- Provider-byte proofs from synthprov records: ALLOW → 1 call with original bytes; REDACT → 1 call, raw canary absent;
  BLOCK → 0 calls.
- Output: provider-injected email and split-aws (secret split across chunks mid-token) never reach the client raw, SSE stays
  valid and parseable by both SDKs.
- Two tenants concurrently (org-A/org-B, identical fixtures, 200 concurrent requests): opposite dispositions, correct plan
  versions, zero cross-tenant leakage (config, findings, request ids, audit).
- Guard backend killed/unready mid-load: semantic findings UNAVAILABLE; FAIL_CLOSED rule → BLOCK; FAIL_OPEN rule → declared
  action + recorded; never a fabricated clean result.
- Instrument honesty: injected 5 ms pre-dispatch delay and a 30 ms mid-stream hold are visible in the harness metrics.
- Also try the repo's frozen conformance suite (gateway_v2/tests/openai_conformance, live-uvicorn / TCP variants) against the
  prototype's base URL; report pass/fail per test (the prototype implements chat/models only — list which tests are out of scope).
