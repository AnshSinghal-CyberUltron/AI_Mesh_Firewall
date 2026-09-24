CLAIM 2 / lane a -- how many log records reach RedisLogPublisher.emit() per v1 chat request
==========================================================================================
Date 2026-09-23. Code under test: BASELINE 2a657fad at $SP/baseline-2a657fad (byte-identical to
`git show 2a657fad:<file>` for main.py, llm_router.py, redis_log_handler.py; read-only git, see baseline_defect/).
$SP = /tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad
$E  = $SP/evidence/micro-claims/claim2/a      $B = $SP/baseline-2a657fad
$PY = /home/contact_cyberultron_com/AI_Mesh_Firewall/gateway/.venv/bin/python (Python 3.14.4)
Functional counting only. No timing was measured. The repo, its venv and the docker stack were not touched.

CLAIM UNDER TEST
  "redis_log_handler.py does a synchronous Redis publish inside logging.Handler.emit, and main.py:6716-6730
   sets the gateway logger to DEBUG, so every log line blocks the event loop." An older plan said
   "~17 blocking round-trips per request".
  Code facts (read at baseline): redis_log_handler.py:87-105 emit() -> format -> json.dumps ->
  client.publish(...) at line 100 on a sync redis.Redis (socket_timeout=1.0, line 74). main.py:6716 imports
  the publisher, 6718-6722 build it with Formatter("%(message)s"), 6728-6730 attach it to logger "gateway"
  and call setLevel(logging.DEBUG). 6733-6737 attach a second publisher, with no formatter, to logger "bedrock"
  (propagate=False; level from BEDROCK_LOG_LEVEL, default DEBUG, bedrock_logger.py:36/155).
  Root logging: _configure_logging() runs at import (main.py:17439-17442) and uses GATEWAY_LOG_LEVEL
  (default INFO). config_sync.py:558-571 changes only the ROOT level at runtime; the comment there says the
  gateway/middleware loggers are "pinned at DEBUG".

ONE COMMAND (reproduces every number below, about 65 s)
  cd $E && $PY claim2a_emit_count.py all        # then: python3 claim2a_postprocess.py
                                                #       python3 claim2a_integrity.py > integrity_checks.txt
                                                #       python3 claim2a_report_tables.py
  Each cell (profile x level) runs in a fresh subprocess with
  PYTHONPATH=$B/gateway:$B/shared:$E/pydeps and PYTHONDONTWRITEBYTECODE=1.
  Scenarios: 5 warm-up + 20 measured requests each, through the stock openai 2.38.0 SDK ->
  httpx.ASGITransport -> the full ASGI app, middleware included. Every prompt and upstream reply is unique
  per request.

RESULT (results_table.txt has all columns; every cell was deterministic, min = median = max)
  emits/request = calls to RedisLogPublisher.emit(), both publishers, attributed to the request by a
  harness contextvar. DEBUG = gateway logger at DEBUG (main.py:6730 as shipped). INFO = the same setLevel
  call mapped to INFO (the proposed fix); the bedrock logger stays at its default DEBUG.

  profile scenario             DEBUG  INFO  HTTP/action  records (DEBUG cell, per request)
  P1      s1 benign non-stream   1     1    200 allow    gateway INFO "Token usage" (main.py:11594)
  P1      s2 benign stream       0     0    200 allow    -
  P1      s3 PII non-stream      2     2    200 redact   + gateway INFO "PII/secret detected, redacting" (main.py:9549)
  P1      s4 injection           1     1    200 allow    (NOT blocked under the verbatim fixture, see F2)
  P2      s1                     4     3    200 allow    rate_limiter INFO(:111) + routing INFO(main:10105) + token INFO(main:11594) + rate_limiter DEBUG(:274)
  P2      s2                     2     2    200 allow    rate_limiter INFO + routing INFO
  P2      s3                     5     4    200 redact   s1 set + PII INFO(main:9549)
  P2      s4                     4     3    200 allow    same as s1 (empty policy bundle: not blocked)
  P2b     s1/s2/s3               4/2/5 3/2/4 as P2
  P2b     s4                     2     2    400 blocked  rate_limiter INFO + [SECURITY_BLOCK] WARNING (main.py:1046)
  P2t2    s1                    13    12    200 allow    P2 set + 7 bedrock (6 INFO + 1 DEBUG) + 2 gateway.bedrock_client INFO
  P2t2    s2                     2     2    200 allow    streaming path: no tier-2 output scan ran
  P2t2    s3                    14    13    200 redact
  P2t2    s4                    13    12    200 allow
  P1-as-is (no shim) s1..s4      1     1    500 error    gateway ERROR "Unhandled error in proxy_chat" + traceback (1715 B)

  Root StreamHandler records/request (DEBUG/INFO): P1 equals the publisher count. P2 s1 4/3, s2 2/2,
  s3 5/4, s4 4/3. P2b s4 2/2. P2t2 s1 6/5, s3 7/6. The bedrock records do not propagate to root.
  gateway.* DEBUG records DO reach the root StreamHandler even though the root level is INFO
  (P2_DEBUG: 60 DEBUG records at root over 60 measured non-stream requests; integrity_checks.txt).
  Thread: 100% of per-request emits ran on the event-loop thread (MainThread) in every cell; 0 ran on
  worker threads (thread_breakdown.json).
  PUBLISH payload per request (median, DEBUG/INFO): P2 s1 990/797 B, s3 1196/1005 B. P2t2 s1 3518.5/3326 B.
  One-time (not per request): import-time emits = 0 (publishers are attached in startup, not at import;
  0 records created at import). P2* startup = 12 emits (10 gateway INFO, 1 WARNING
  "AIGUARDX_BACKEND_URL not set", 1 policy_sync INFO). Shutdown = 5. The first request of the process
  emitted exactly its steady-state count in every cell (no extra first-request records).
  Background (not per request): gateway.telemetry DEBUG "Telemetry flushed N events" (telemetry.py:132),
  1 per flush while events are pending (flush interval default 2.0 s). 2 per DEBUG P2* run, 0 at INFO.

FINDINGS
  F1 Under the proposed fix (gateway logger INFO), per-request publishes drop by 0 (P1) or by exactly 1
     (P2/P2b/P2t2 non-stream: rate_limiter.py:274 "RateLimit record_usage" DEBUG). Every other per-request
     record is INFO/WARNING/ERROR, so it is still published synchronously on the event-loop thread.
     DEBUG-cell non-DEBUG count (+ kept bedrock DEBUG) == INFO-cell count in all 16 profile/scenario pairs.
  F2 "~17 per request" was NOT reproduced. Max measured is 14 (P2t2 s3), and that needs the tier-2 OUTPUT
     scan: ENABLE_TIER2 defaults to true (scanner.py:1137; docker-compose.prod.yml:191), and
     output_tier2_enabled defaults on (output_guard.py:1010). With org tier2_enabled unset the gate falls
     back to the scanner flag (scanner.py:2574), so one tier-2 scan runs per non-stream response and adds
     9 emits. With tier-2 off (P2) the count is 2-5. Input tier-2 (org opt-in) was not measured.
  F3 BASELINE DEFECT: at 2a657fad, main.py:4745/4747 imports llm_router.catalog_row_is_display_alias, but
     2a657fad llm_router.py does not define it (it arrives in 2ed687a6). Every chat request with a
     credentialed routing model therefore returns HTTP 500. Measured only in P1-as-is: exactly 1 ERROR
     record per request (main.py:12039, 1715 B with traceback).
     The baseline's own tests/test_openai_sdk_compat.py chat cells: as-is 4/4 FAIL (500).
     With the shim: 2 pass; the 2 "blocked" tests still FAIL with "DID NOT RAISE", because the injection
     prompt is not blocked under the fixture (POLICY_SYNC=None). validation/*.txt.
  F4 Harness hazard found: litellm/__init__.py:19-20 calls dotenv.load_dotenv() on import when LITELLM_MODE
     is unset. From this workspace that loads a developer .env from the repo tree into os.environ
     (AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY/AWS_REGION appeared; names only, values never read or
     printed). All final runs set PYTHON_DOTENV_DISABLED=1, and each run records
     env_names_present_not_set_by_harness == [].

PROFILES -- what is real and what is stubbed (per-run truth: run_<cell>.json "singletons"/"logger_state")
  P1   tests/test_openai_sdk_compat.py::_make_sdk_app statement for statement, via pytest's MonkeyPatch,
       plus the conftest autouse resets. Real: app, middleware stack, AuthMiddleware logic, InputScanner,
       proxy_chat.
       Stubbed: CONFIG=TEST_CONFIG, CONFIG_SYNC and LLM_ROUTER are MagicMocks (so the "Token usage" text
       contains a MagicMock repr; count unaffected). POLICY_SYNC, RATE_LIMITER, CIRCUIT_BREAKER,
       REDIS_CLIENT, TELEMETRY and OUTPUT_GUARD are None. _emit_telemetry and _audit_fire_and_forget are
       no-ops. Auth uses its own fakeredis. The upstream reply is the test's stub shape with benign text
       and a 50-token stream. Publishers are attached with the statements of main.py:6716-6737 plus 6755,
       because ASGITransport never runs startup. Their redis_url is the load_config default shape, since
       TEST_CONFIG has no redis_url.
  P2   The REAL ASGI lifespan startup (main.py:6350-6878) on load_config() env defaults. Real: ConfigSync,
       LLMRouter, RateLimiter (EVAL via lupa), InputScanner, REDIS_CLIENT, TelemetryProducer, OutputGuard
       (+GroundingGuard, lexical mode), CircuitBreaker, PolicySync (signing ENFORCED as by default, bundle
       signed with the control plane's own sign_bundle and an ephemeral per-run random POLICY_SIGNING_KEY
       that is never written anywhere), both RedisLogPublishers, RedisLogSubscriber, MCP/vector wiring,
       kill-switch/model-state reads.
       Every redis-py ConnectionPool.from_url (sync + asyncio) points at one in-process fakeredis FakeServer.
       Seeded org 'acme': auth key (test payload, org_slug=acme, rate_limit_tpm raised to 10,000,000 so 100
       sequential requests never 429), llm:model_configs:acme routing = the test's model rows, an EMPTY
       compiled bundle.
       Disabled/stubbed: ENABLE_TIER2=false (tier-2 Bedrock OFF), backend/control plane (backend_url empty
       -> AGENT_ID None, no registration), RAG (default off), the upstream network seam
       LLMRouter._execute_completion (the litellm.acompletion call; returns a ModelResponse-like object or
       a 50-chunk async stream), and bedrock_client.default_bedrock_client -> a no-network dummy (used
       only by the startup preflight and the idle grounding embedder).
  P2b  P2 + a 2-policy signed bundle in the conftest compiled shape: "Block Injection" (conftest verbatim)
       and "PII Detection & Redaction" (email + phone regex, action redact). Loaded "version=1, policies=2".
  P2t2 P2, but ENABLE_TIER2 left at its production default (true). REAL BedrockClient singletons (both
       module identities) and the real BedrockScanner, breaker, cache and bedrock_logger. Only
       aiobotocore's converse() is faked, returning a clean verdict
       {"findings":[],"risk_score":0,"recommended_action":"allow"}. 75 fake calls = 75 non-stream requests.
       Unique replies prevent verdict-cache hits.

CAVEATS / DEVIATIONS (all deliberate, all recorded per run)
  - Shim (F3): all non-"noshim" cells exec the verbatim 2ed687a6 source of the 3 pure, log-free helpers
    (baseline_defect/shim_source_2ed687a6_llm_router_L29-68.py) into both llm_router module identities
    at runtime. No baseline file was modified.
  - lupa 2.8 was installed ONLY into $E/pydeps (uv pip install --target) so fakeredis supports EVAL.
    Without it, the middleware last_used Lua and the RateLimiter scripts fail and would log extra lines
    (observed in an early smoke run: DEBUG "Failed to update last_used_at: unknown command 'eval'").
    Repo venv site-packages entries: 288 before and after.
  - Socket guard: every AF_INET/AF_INET6 connect and every DNS lookup is blocked and recorded. The only
    attempts, in all cells, were redis-py 8 maint-notification getaddrinfo() calls on fakeredis's
    synthetic hostnames. redis-py catches these (connection.get_resolved_ip -> None), so they are
    equivalent to NXDOMAIN and are not gateway behaviour. 0 unexpected attempts. The live Redis on
    :6379 was never reachable: GATEWAY_REDIS_URL = redis://claim2a-fakeredis.invalid:6379/0.
  - Counts reflect this config and these inputs. Real orgs differ in policies, org firewall config
    (firewall:config:<org> not seeded, env defaults used), input tier-2 opt-in, and tier-2 findings
    (a verdict with findings adds rule-hit lines). BEDROCK_LOG_LEVEL was kept at its default DEBUG in
    INFO cells; the bedrock DEBUG "BEDROCK OUTPUT" line (1/req in P2t2) is the only bedrock DEBUG record.
  - fakeredis publishes in-memory, so this lane establishes the NUMBER and bytes of synchronous PUBLISH
    calls on the loop thread, not their latency against a real Redis.
  - Early development smoke runs, before PYTHONDONTWRITEBYTECODE=1, may have added standard .pyc caches
    under the baseline's existing __pycache__ dirs. No source changed.

COMMANDS RUN (outputs saved alongside)
  1. Baseline fidelity/defect (read-only git): for f in llm_router.py main.py redis_log_handler.py:
     `git -C <repo> show 2a657fad:<f> | diff -q - $B/<f>` -> IDENTICAL for all three.
     `git -C <repo> log --oneline -S catalog_row_is_display_alias -- gateway/ai_mesh_gateway/{main,llm_router}.py`
     -> 2ed687a6 (adds def), 2a657fad (adds import) => baseline_defect/catalog_row_is_display_alias_missing.txt
  2. `~/.local/bin/uv pip install --python $PY --target $E/pydeps lupa` -> "Installed 1 package ... + lupa==2.8"
  3. Validation of P1 against the real test (validation/pytest_sdk_compat_{with_shim,as_is}.txt; the
     exact, unabbreviated command is the "## cmd:" line at the top of each file):
     cd $E/scratch && CLAIM2A_NO_SHIM={0|1} PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$B/gateway:$B/shared:$E:$E/pydeps:$E/scratch \
       $PY -m pytest $B/gateway/ai_mesh_gateway/tests/test_openai_sdk_compat.py -p envprobe_plugin -p claim2a_pytest_shim \
       -p no:cacheprovider --rootdir=$B/gateway -c $B/gateway/pyproject.toml \
       -k '<non_streaming | streaming_chunks | blocked_request | blocked_streaming>' -q -rA
     -> with shim: "2 failed, 2 passed" (both failures = DID NOT RAISE). As-is: "4 failed" (500).
     Blocked network attempts 0; AWS* env names at finish = only the 4 set by the plugin.
  4. `cd $E && $PY claim2a_emit_count.py all` -> all_console.txt: 10 cells rc=0, exit=0,
     2026-09-23T06:15:47Z..06:16:50Z. Per cell: run_<cell>.json, stdout_/stderr_<cell>.log.
  5. `python3 claim2a_postprocess.py` -> postprocess_console.txt, thread_breakdown.json
  6. `python3 claim2a_integrity.py > integrity_checks.txt` (provenance start/end: 0 live-repo modules,
     26/43-99 baseline modules; unpublished=0; foreign env=[]; setLevel wiring; DEBUG->root; DEBUG/INFO cross-check OK x16)
  7. `python3 claim2a_report_tables.py` -> results_table.txt, record_legend.txt, report_tables_console.txt

FILE INDEX
  claim2a_emit_count.py   entry point (run/all), profile setup, request driver, per-run summary
  claim2a_lib.py          fixture data, scenarios, instrumentation (emit wrapper, _get_client->fakeredis,
                          root-handler wrapper, socket guard, redis fakes, shim loader, fake Bedrock transport)
  claim2a_pytest_shim.py  pytest plugin used for step 3; scratch/ and validation/envprobe_plugin.py = env-name probe
  records_pub_<cell>.jsonl     EVERY emit() call (phase, attribution, thread, logger, level, src, msg_len,
                               payload_len/bytes, publish ok, full JSON payload)
  replay_payloads_<cell>.jsonl measured per-request PUBLISH payloads only ({channel, payload, scenario, req_idx,
                               logger, levelname, payload_bytes}), for replay against a real Redis
  records_root_<cell>.jsonl    every record reaching the root StreamHandler; root_stream_<cell>.log its text output
  run_<cell>.json, summary.json, thread_breakdown.json, results_table.txt, record_legend.txt,
  integrity_checks.txt, all_console.txt, postprocess_console.txt, validation/, baseline_defect/, pydeps/ (lupa),
  bedrock_logdir_<cell>/ (bedrock_logger's file handler, redirected here via BEDROCK_LOG_DIR)
