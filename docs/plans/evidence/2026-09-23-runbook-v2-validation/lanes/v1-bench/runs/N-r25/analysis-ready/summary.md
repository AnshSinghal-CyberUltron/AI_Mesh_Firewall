# Harness analysis (sut, policy={'blocks_total': 0, 'expected_blocks': 0, 'false_positive_blocks': 0, 'other_blocks': 0, 'benign_offered': 7500, 'false_positive_rate': 0.0, 'policy_misses': 0, 'latency_client_total_ms': {}, 'block_rule': {'statuses': [400, 403, 422, 451], 'envelope_re': '(?i)content_filter|content_polic|polic(y|ies)_|blocked|block_|guard|violation|unsafe|threat'}}) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | FAIL |
| infra_error_rate_le_budget | PASS |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 7500 over 300 s = 25.0 req/s (configured 25.0)
- qualified: 7500 (25.0 req/s; dispositions ['ALLOW', 'FLAG', 'REDACT'], source v1)
- infra errors (the error-budget gate): 0 (rate 0.0); reasons: {}
- policy stratum (not qualified, not infra errors): expected blocks 0, FALSE-POSITIVE blocks 0 of 7500 benign (FP rate 0.0), other blocks 0, policy misses 0
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=7500 p50=0.0853 p90=0.0954 p99=0.1076 p99.9=0.1321 max=0.1612 mean=0.0865
- join: {'joined': 7500, 'by_nonce_fallback': 0, 'provider_records': 9375, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=7500 p50=345.0636 p90=3075.3248 p99=6927.6045 p99.9=9610.5716 max=11060.6089 mean=1067.0684 |
| T_addon_first | n=7500 p50=781.1168 p90=1045.6527 p99=1307.2794 p99.9=1581.4073 max=1918.8948 mean=662.0176 |
| T_release_lag_max | n=784 p50=1171.1544 p90=3279.4556 p99=6624.4911 p99.9=7758.2918 max=7758.2918 mean=1432.133 |
| T_release_lag_max_arrival | n=784 p50=1171.1544 p90=3279.4556 p99=6624.4911 p99.9=7758.2918 max=7758.2918 mean=1432.133 |
| T_fw_addon | n=7500 p50=832.028 p90=3101.9705 p99=6931.6606 p99.9=9610.5716 max=11060.6089 mean=1254.7478 |
| T_fw_addon_sse | n=5250 p50=1061.9059 p90=3783.1879 p99=7535.8108 p99.9=9835.1215 max=11060.6089 mean=1705.349 |
| T_fw_addon_json | n=2250 p50=192.7205 p90=289.082 p99=495.2334 p99.9=679.8539 max=710.5733 mean=203.345 |
| client_ttft_sse | n=5250 p50=998.6229 p90=1251.2476 p99=1495.2922 p99.9=1810.3788 max=2068.9013 mean=1008.6439 |
| provider_sched_err_last | n=7500 p50=0.0541 p90=0.0882 p99=0.1013 p99.9=0.1093 max=0.1375 mean=0.0525 |
| provider_sched_err_max_per_stream | n=7500 p50=0.0654 p90=0.101 p99=0.1248 p99.9=0.1745 max=0.1969 mean=0.0645 |
| provider_write_max | n=7500 p50=0.0115 p90=0.02 p99=0.0318 p99.9=0.0497 max=0.1033 mean=0.0131 |

- release lag: 784 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/v1-bench/runs/N-r25/lg-v1ready', 'samples': 300, 'busy_max': 7.625753051269946, 'busy_mean': 3.83, 'machine': 'c4-standard-8', 'zone': 'asia-south1-c'}]
- provider CPU: {'samples': 412, 'busy_max': 19.051932211496457, 'busy_p95': 4.068037958314963}
- completeness: [{'dir': '/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/v1-bench/runs/N-r25/lg-v1ready', 'scheduled': 9375, 'recorded': 9375, 'interrupted': False}]
- health olg N-r25: gc_cycles=0 sched_latency_max_ms=0.08192 tcp=None
- health synthprov rv-v1-prov-2: gc_cycles=0 sched_latency_max_ms=0.098304 tcp={'TcpRetransSegs': 3912, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 3912, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 1506052, 'TcpInSegs': 1492114}
