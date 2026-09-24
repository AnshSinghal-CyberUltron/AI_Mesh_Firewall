# Harness analysis (sut, policy={'blocks_total': 0, 'expected_blocks': 0, 'false_positive_blocks': 0, 'other_blocks': 0, 'benign_offered': 300, 'false_positive_rate': 0.0, 'policy_misses': 0, 'latency_client_total_ms': {}, 'block_rule': {'statuses': [400, 403, 422, 451], 'envelope_re': '(?i)content_filter|content_polic|polic(y|ies)_|blocked|block_|guard|violation|unsafe|threat'}}) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | FAIL |
| infra_error_rate_le_budget | PASS |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 300 over 300 s = 1.0 req/s (configured 1.0)
- qualified: 300 (1.0 req/s; dispositions ['ALLOW', 'FLAG', 'REDACT'], source v1)
- infra errors (the error-budget gate): 0 (rate 0.0); reasons: {}
- policy stratum (not qualified, not infra errors): expected blocks 0, FALSE-POSITIVE blocks 0 of 300 benign (FP rate 0.0), other blocks 0, policy misses 0
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=300 p50=0.1434 p90=0.219 p99=0.2812 p99.9=0.3529 max=0.3529 mean=0.1547
- join: {'joined': 300, 'by_nonce_fallback': 0, 'provider_records': 375, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=300 p50=87.014 p90=115.4456 p99=292.7572 p99.9=301.0259 max=301.0259 mean=91.7041 |
| T_addon_first | n=300 p50=700.4137 p90=837.0886 p99=1004.846 p99.9=1093.8226 max=1093.8226 mean=531.5736 |
| T_release_lag_max | n=32 p50=1026.3423 p90=1141.39 p99=1172.1488 p99.9=1172.1488 max=1172.1488 mean=661.036 |
| T_release_lag_max_arrival | n=32 p50=1026.3423 p90=1141.39 p99=1172.1488 p99.9=1172.1488 max=1172.1488 mean=661.036 |
| T_fw_addon | n=300 p50=709.3873 p90=951.339 p99=1141.39 p99.9=1172.1488 max=1172.1488 mean=553.4099 |
| T_fw_addon_sse | n=210 p50=740.6939 p90=1001.1766 p99=1143.8644 p99.9=1172.1488 max=1172.1488 mean=756.7403 |
| T_fw_addon_json | n=90 p50=78.9408 p90=101.7776 p99=118.7781 p99.9=118.7781 max=118.7781 mean=78.9725 |
| client_ttft_sse | n=210 p50=881.6851 p90=1047.5976 p99=1184.1348 p99.9=1243.8736 max=1243.8736 mean=875.5939 |
| provider_sched_err_last | n=300 p50=0.048 p90=0.0924 p99=0.1139 p99.9=0.2251 max=0.2251 mean=0.052 |
| provider_sched_err_max_per_stream | n=300 p50=0.0623 p90=0.1056 p99=0.2302 p99.9=0.2552 max=0.2552 mean=0.0683 |
| provider_write_max | n=300 p50=0.0222 p90=0.0332 p99=0.0449 p99.9=0.0481 max=0.0481 mean=0.0229 |

- release lag: 32 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/v1-bench/runs/L1-r1/lg-v1ready', 'samples': 300, 'busy_max': 15.5608962616832, 'busy_mean': 3.8, 'machine': 'c4-standard-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 401, 'busy_max': 17.69830482132606, 'busy_p95': 9.52616671972082}
- completeness: [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/v1-bench/runs/L1-r1/lg-v1ready', 'scheduled': 375, 'recorded': 375, 'interrupted': False}]
- health olg L1-r1: gc_cycles=0 sched_latency_max_ms=0.114688 tcp=None
- health synthprov rv-v1-prov-1: gc_cycles=0 sched_latency_max_ms=0.16384 tcp={'TcpRetransSegs': 27, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 27, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 62019, 'TcpInSegs': 61797}
