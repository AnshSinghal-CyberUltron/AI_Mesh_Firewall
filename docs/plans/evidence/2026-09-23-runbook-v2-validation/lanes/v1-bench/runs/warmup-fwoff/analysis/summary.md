# Harness analysis (sut, policy=none) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | FAIL |
| error_rate_le_budget | FAIL |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 1200 over 60 s = 20.0 req/s (configured 20.0)
- qualified: 839 (13.98 req/s); expected blocks: 0; errors: 361 (rate 0.30083333333333334)
- error reasons: {'disposition_missing': 361, 'stage_auth_missing': 361, 'stage_kill_switch_missing': 361, 'stage_rate_limit_missing': 361, 'stage_policy_missing': 361, 'stage_input_scan_missing': 361, 'stage_model_routing_missing': 361, 'stage_model_input_missing': 361, 'stage_model_output_missing': 361, 'stage_output_guardrail_missing': 361}
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=1200 p50=0.088 p90=0.1003 p99=0.1335 p99.9=0.1795 max=0.3406 mean=0.0901
- join: {'joined': 1200, 'by_nonce_fallback': 0, 'provider_records': 1300, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=839 p50=170.9081 p90=873.9051 p99=2004.0356 p99.9=2508.3084 max=2508.3084 mean=352.4599 |
| T_addon_first | n=839 p50=82.7535 p90=171.3384 p99=372.0701 p99.9=566.8335 max=566.8335 mean=100.497 |
| T_release_lag_max | n=89 p50=284.8354 p90=592.9423 p99=1918.8243 p99.9=1918.8243 max=1918.8243 mean=333.4522 |
| T_release_lag_max_arrival | n=89 p50=284.8354 p90=592.9423 p99=1918.8243 p99.9=1918.8243 max=1918.8243 mean=333.4522 |
| T_fw_addon | n=839 p50=203.9695 p90=873.9051 p99=2004.0356 p99.9=2508.3084 max=2508.3084 mean=362.9073 |
| T_fw_addon_sse | n=839 p50=203.9695 p90=873.9051 p99=2004.0356 p99.9=2508.3084 max=2508.3084 mean=362.9073 |
| T_fw_addon_json | n=0 |
| client_ttft_sse | n=839 p50=232.819 p90=321.3675 p99=522.0737 p99.9=716.8407 max=716.8407 mean=250.5499 |
| provider_sched_err_last | n=1200 p50=0.0536 p90=0.0884 p99=0.1005 p99.9=0.1064 max=0.1092 mean=0.0522 |
| provider_sched_err_max_per_stream | n=1200 p50=0.0626 p90=0.0999 p99=0.1193 p99.9=0.1466 max=0.1523 mean=0.0622 |
| provider_write_max | n=1200 p50=0.0123 p90=0.0218 p99=0.0438 p99.9=0.0765 max=0.0781 mean=0.0145 |

- release lag: 125 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/v1-bench/runs/warmup-fwoff/lg-v1aug', 'samples': 60, 'busy_max': 7.060577832123494, 'busy_mean': 3.43, 'machine': 'c4-standard-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 95, 'busy_max': 16.09312843494345, 'busy_p95': 8.844375337267884}
- completeness: [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/v1-bench/runs/warmup-fwoff/lg-v1aug', 'scheduled': 1300, 'recorded': 1300, 'interrupted': False}]
- health olg warmup-fwoff: gc_cycles=0 sched_latency_max_ms=0.114688 tcp=None
- health synthprov rv-v1-prov-2: gc_cycles=0 sched_latency_max_ms=0.098304 tcp={'TcpRetransSegs': 619, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 619, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 214845, 'TcpInSegs': 210405}
